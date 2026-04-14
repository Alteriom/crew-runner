"""crew-runner — isolated HTTP service for CrewAI crew execution.

Mirrors the Claude Runner pattern: the backend delegates crew execution
to this service via HTTP, keeping the FastAPI event loop unblocked.

Endpoints:
    POST /execute         — run a crew synchronously, return JSON result
    POST /execute/stream  — run a crew and stream NDJSON events (step/task/result)
    GET  /health          — health check
"""
import asyncio
import json as json_mod
import logging
import os
import time
from typing import Any, Optional
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

import litellm

from src import worker_client

# ---------------------------------------------------------------------------
# Monkey-patch: normalize crewai TaskOutput.raw to always be a str.
# GLM-5.1 sometimes returns a list of ChatCompletionMessageToolCall objects
# as its final answer. crewai.TaskOutput.raw requires str, so we intercept
# at the Pydantic validator level before the ValidationError fires.
# ---------------------------------------------------------------------------
def _patch_task_output_raw():
    try:
        from crewai.task import TaskOutput  # noqa: PLC0415
        from llm_output_normalizer import normalize_llm_output
        import json
        from pydantic import field_validator

        # Only patch once
        if getattr(TaskOutput, "_raw_patched_by_crew_runner", False):
            return

        original_validators = {}

        @classmethod  # type: ignore[misc]
        def _raw_str_validator(cls, v):  # noqa: N805
            if isinstance(v, str):
                return v
            logger.warning("TaskOutput.raw: non-string type=%s, normalizing", type(v).__name__)
            return normalize_llm_output(v)

        # Inject as a before-validator on the 'raw' field
        try:
            # Pydantic v2 approach: re-register model with validator
            # Simpler: just wrap __init__ to coerce raw before Pydantic sees it
            orig_init = TaskOutput.__init__

            def patched_init(self, *args, **kwargs):
                if "raw" in kwargs and not isinstance(kwargs["raw"], str):
                    kwargs["raw"] = normalize_llm_output(kwargs["raw"])
                orig_init(self, *args, **kwargs)

            TaskOutput.__init__ = patched_init
            TaskOutput._raw_patched_by_crew_runner = True
            logger.info("TaskOutput.__init__ patched: raw will always be normalized to str")
        except Exception as e:
            logger.warning("Could not patch TaskOutput.__init__: %s", e)
    except Exception as exc:
        logger.warning("_patch_task_output_raw failed: %s", exc)

_patch_task_output_raw()

# ── Version management ───────────────────────────────────────────────────────
VERSION_FILE = Path(__file__).parent / "VERSION"

def get_version() -> str:
    """Read version from VERSION file."""
    try:
        return VERSION_FILE.read_text().strip()
    except Exception:
        return "unknown"

__version__ = get_version()


app = FastAPI(
    title="Crew Runner",
    description="Isolated HTTP runner for CrewAI/Ollama crews",
    version="1.0.0",
)

# Initialize worker registration
worker_client.init_worker_client()



# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    """Mirrors EngineTask from the backend."""
    prompt: str
    system_context: str = ""
    execution_id: str = ""
    tenant_id: str = ""
    inputs: dict = Field(default_factory=dict)
    timeout_seconds: int = 3600
    mcp_servers: Optional[list[dict]] = None


class ExecuteResponse(BaseModel):
    """Mirrors EngineResult from the backend."""
    success: bool
    output: str
    engine_type: str = "crew_runner"
    token_usage: Optional[dict] = None
    tasks_output: Optional[list[dict]] = None
    error: Optional[str] = None
    duration_seconds: Optional[float] = None
    execution_logs: Optional[list[dict]] = None


# ---------------------------------------------------------------------------
# Output extraction helpers (from crewai_native.py)
# ---------------------------------------------------------------------------


def _extract_raw_output(crew_output) -> str:
    from llm_output_normalizer import normalize_llm_output
    if hasattr(crew_output, "result"):
        crew_output = crew_output.result
    if hasattr(crew_output, "raw"):
        return normalize_llm_output(crew_output.raw)
    for attr in ("content", "text", "output"):
        val = getattr(crew_output, attr, None)
        if val:
            return str(val)
    return str(crew_output)


def _extract_token_usage(crew_output) -> dict | None:
    actual = crew_output.result if hasattr(crew_output, "result") else crew_output
    usage = getattr(actual, "token_usage", None)
    if not usage:
        return None
    try:
        return {
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "cached_prompt_tokens": int(getattr(usage, "cached_prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "successful_requests": int(getattr(usage, "successful_requests", 0) or 0),
        }
    except (TypeError, ValueError):
        return None


def _extract_tasks_output(crew_output) -> list[dict] | None:
    from llm_output_normalizer import normalize_llm_output
    actual = crew_output.result if hasattr(crew_output, "result") else crew_output
    tasks_output_raw = getattr(actual, "tasks_output", []) or []
    if not tasks_output_raw:
        return None

    tasks_output_list = []
    for task_obj in tasks_output_raw:
        task_dict = {
            "description": getattr(task_obj, "description", ""),
            "name": getattr(task_obj, "name", None),
            "expected_output": getattr(task_obj, "expected_output", None),
            "summary": getattr(task_obj, "summary", None),
            "raw": normalize_llm_output(getattr(task_obj, "raw", "")),
            "agent": getattr(task_obj, "agent", ""),
            "output_format": str(getattr(task_obj, "output_format", "")).replace("OutputFormat.", ""),
        }

        json_dict = getattr(task_obj, "json_dict", None)
        if json_dict is not None:
            task_dict["json_dict"] = json_dict

        pydantic_obj = getattr(task_obj, "pydantic", None)
        if pydantic_obj is not None:
            try:
                task_dict["pydantic"] = pydantic_obj.model_dump()
            except Exception:
                pass

        tasks_output_list.append(task_dict)

    return tasks_output_list


# ---------------------------------------------------------------------------
# Execution log collector — captures CrewAI callbacks for the backend
# ---------------------------------------------------------------------------


class ExecutionLogCollector:
    """Captures CrewAI step_callback and task_callback events during execution.

    The collected logs are returned in the HTTP response so the backend can
    persist them via ExecutionLogger, restoring [THINK] / [OUTPUT] visibility
    that was lost when crew execution moved to the sidecar.
    """

    def __init__(self) -> None:
        self._logs: list[dict] = []

    def step_callback(self, agent_step: Any) -> None:
        """CrewAI step_callback — captures agent reasoning steps."""
        try:
            thought = str(getattr(agent_step, "thought", "") or "")
            action = str(getattr(agent_step, "action", "") or "")
            observation = str(getattr(agent_step, "observation", "") or "")

            self._logs.append({
                "type": "step",
                "thought": thought[:65000],
                "action": action[:65000],
                "observation": observation[:65000],
            })
        except Exception as exc:
            logger.warning("ExecutionLogCollector: failed to capture step: %s", exc)

    def task_callback(self, task_output: Any) -> None:
        """CrewAI task_callback — captures task completion events."""
        try:
            description = str(getattr(task_output, "description", "Task") or "Task")
            raw = str(getattr(task_output, "raw", "") or "")
            agent_name = str(getattr(task_output, "agent", "unknown") or "unknown")

            self._logs.append({
                "type": "task_completion",
                "description": description[:2000],
                "raw": raw[:60000],
                "agent": agent_name[:500],
            })
        except Exception as exc:
            logger.warning("ExecutionLogCollector: failed to capture task: %s", exc)

    def get_logs(self) -> list[dict]:
        return self._logs


# ---------------------------------------------------------------------------
# Crew building helpers
# ---------------------------------------------------------------------------


def _reconstruct_llm(llm_value):
    """Reconstruct an LLM from a serialized dict or plain model string.

    All models use the Ollama provider. Model strings should be:
      ollama/<model-name>   e.g. ollama/kimi-k2.5:cloud
      <model-name>          bare name is automatically prefixed with ollama/

    Ollama cloud subscription covers all inference costs.
    """
    import os
    ollama_base = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com")
    ollama_key = os.environ.get("OLLAMA_API_KEY", "")

    def make_llm(model, base_url=None, api_key=None):
        from llm_output_normalizer import NormalizedLLM
        kwargs = {"model": model}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        return NormalizedLLM(**kwargs)

    def resolve_model_string(model_str: str):
        """Return (model, base_url, api_key) — always routes through Ollama."""
        if model_str.startswith("ollama/"):
            return model_str, ollama_base, ollama_key
        elif "/" in model_str:
            # Has a provider prefix — still route through Ollama base_url
            # (kimi-k2.5:cloud, minimax-m2.7:cloud etc are served via Ollama Cloud)
            return model_str, ollama_base, ollama_key
        else:
            # No prefix → prefix with ollama/
            return f"ollama/{model_str}", ollama_base, ollama_key

    if isinstance(llm_value, dict) and "_litellm_model" in llm_value:
        model = llm_value["_litellm_model"]
        # Explicit base_url/api_key in dict takes precedence
        base_url = llm_value.get("_api_base")
        api_key = llm_value.get("_api_key")
        if not base_url and not api_key:
            model, base_url, api_key = resolve_model_string(model)
        elif not base_url:
            # Has api_key but no base_url — auto-detect from model
            model, base_url, _ = resolve_model_string(model)
        return make_llm(model=model, base_url=base_url, api_key=api_key)

    if isinstance(llm_value, str):
        model, base_url, api_key = resolve_model_string(llm_value)
        return make_llm(model=model, base_url=base_url, api_key=api_key)

    return llm_value


def _instantiate_tools_from_metadata(tool_metadata: list[dict]) -> list[Any]:
    """Instantiate CrewAI BaseTool objects from tool metadata dicts.

    The backend serialises tool metadata (id, name, type) into the agent config.
    We recreate real tool instances here inside the runner so they execute
    in the same process as the CrewAI agents.
    """
    from src.tools_wrappers import create_crewai_tool  # type: ignore
    tools = []
    for meta in tool_metadata or []:
        try:
            tool = create_crewai_tool(
                tool_type=meta.get("type", ""),
                tool_name=meta.get("name", ""),
                credentials=meta.get("credentials", {}),
                description=meta.get("description"),
            )
            if tool is not None:
                tools.append(tool)
                logger.debug("crew-runner: instantiated tool %s (type=%s)", meta.get("name"), meta.get("type"))
            else:
                logger.warning("crew-runner: no wrapper for tool %s (type=%s) — skipping", meta.get("name"), meta.get("type"))
        except Exception as exc:
            logger.warning("crew-runner: failed to instantiate tool %s: %s", meta.get("name"), exc)
    return tools


def _build_crewai_agent(cfg: dict) -> Any:
    """Build a crewai.Agent from an agent config dict."""
    from crewai import Agent

    # Instantiate tools from metadata (tool objects can't be serialised over HTTP,
    # so we recreate them here inside the runner process).
    tool_metadata = cfg.get("tool_metadata", [])
    tools = _instantiate_tools_from_metadata(tool_metadata)
    if tools:
        logger.info("crew-runner: agent=%s tools=%d", cfg.get("name", cfg.get("role", "?")), len(tools))

    backstory = cfg.get("backstory", "You are a helpful AI assistant.")

    # Inject resolved skill descriptions into backstory so the agent
    # has contextual knowledge from its assigned skills.
    skills = cfg.get("skills", [])
    if skills:
        skill_lines = []
        for s in skills:
            desc = s.get("description", "")
            if desc:
                skill_lines.append(f"### {s['name']}\n{desc}")
        if skill_lines:
            backstory += "\n\n## Loaded Skills\n\n" + "\n\n".join(skill_lines)

    kwargs: dict = {
        "role": cfg.get("role", "Assistant"),
        "goal": cfg.get("goal", "Complete the assigned task"),
        "backstory": backstory,
        "verbose": cfg.get("verbose", False),
        "allow_delegation": cfg.get("allow_delegation", False),
        "cache": cfg.get("cache", True),
        "tools": tools,
    }

    # Always set LLM — fall back to DEFAULT_LLM_MODEL/PROVIDER env vars
    # so agents don't silently use OpenAI when no model is configured.
    llm_value = cfg.get("llm")
    if not llm_value:
        default_model = os.environ.get("DEFAULT_LLM_MODEL", "glm-5.1")
        default_provider = os.environ.get("DEFAULT_LLM_PROVIDER", "ollama")
        if not default_model.startswith("ollama/"):
            default_model = f"{default_provider}/{default_model}"
        llm_value = default_model
    kwargs["llm"] = _reconstruct_llm(llm_value)
    if cfg.get("max_iter"):
        kwargs["max_iter"] = cfg["max_iter"]
    if cfg.get("max_retry_limit"):
        kwargs["max_retry_limit"] = cfg["max_retry_limit"]
    if cfg.get("max_rpm"):
        kwargs["max_rpm"] = cfg["max_rpm"]
    if cfg.get("max_execution_time"):
        kwargs["max_execution_time"] = cfg["max_execution_time"]

    # Handle planning flag — if True, must also set planning_llm to Ollama
    # or CrewAI will default planning_llm to OpenAI
    if cfg.get("planning"):
        kwargs["planning"] = True
        planning_model = cfg.get("llm_reasoning") or cfg.get("llm") or llm_value
        kwargs["planning_llm"] = _reconstruct_llm(planning_model)

    return Agent(**kwargs)


def _build_crew_from_config(
    crew_config: dict,
    agent_configs: list[dict],
    step_callback: Any = None,
    task_callback: Any = None,
) -> Any:
    """Build a crewai.Crew from config dicts."""
    from crewai import Crew, Task, Process

    # Build agents
    agents = []
    agent_by_id: dict[str, Any] = {}
    for cfg in agent_configs:
        agent = _build_crewai_agent(cfg)
        agents.append(agent)
        agent_by_id[cfg.get("id", "")] = agent

    # Build tasks
    tasks = []
    task_objs_by_id: dict[str, Any] = {}
    for task_meta in crew_config.get("tasks", []):
        assigned_agent = agent_by_id.get(task_meta.get("agent_id", ""))
        if assigned_agent is None and agents:
            assigned_agent = agents[0]

        context_ids: list[str] = task_meta.get("context_dependencies", [])
        context_tasks = [task_objs_by_id[tid] for tid in context_ids if tid in task_objs_by_id]

        task_kwargs: dict = {
            "description": task_meta["description"],
            "expected_output": task_meta.get("expected_output") or "Task output",
            "async_execution": task_meta.get("async_execution", False),
            "human_input": task_meta.get("human_input", False),
        }
        if assigned_agent:
            task_kwargs["agent"] = assigned_agent
        if context_tasks:
            task_kwargs["context"] = context_tasks

        task_obj = Task(**task_kwargs)
        task_objs_by_id[task_meta.get("id", f"task_{len(tasks)}")] = task_obj
        tasks.append(task_obj)

    # Build crew
    process_map = {
        "sequential": Process.sequential,
        "hierarchical": Process.hierarchical,
    }
    process = process_map.get(crew_config.get("process_type", "sequential"), Process.sequential)

    crew_kwargs: dict = {
        "agents": agents,
        "tasks": tasks,
        "process": process,
        "verbose": crew_config.get("verbose", False),
        "cache": crew_config.get("cache", True),
        "stream": False,  # Always False — streaming causes CrewStreamingOutput with no .raw
        "planning": crew_config.get("planning", False),
    }

    if crew_config.get("memory"):
        crew_kwargs["memory"] = True
        embedder_config = crew_config.get("embedder_config")
        if embedder_config:
            crew_kwargs["embedder"] = embedder_config
    else:
        crew_kwargs["memory"] = False

    if crew_config.get("manager_llm"):
        crew_kwargs["manager_llm"] = crew_config["manager_llm"]
    if crew_config.get("max_rpm") is not None:
        crew_kwargs["max_rpm"] = crew_config["max_rpm"]
    if crew_config.get("planning_llm"):
        crew_kwargs["planning_llm"] = crew_config["planning_llm"]

    crew_kwargs["tracing"] = crew_config.get("tracing", False)

    if step_callback is not None:
        crew_kwargs["step_callback"] = step_callback
    if task_callback is not None:
        crew_kwargs["task_callback"] = task_callback

    return Crew(**crew_kwargs)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["system"])
async def health():
    """Health check endpoint - includes session capacity info."""
    return {
        "status": "ok",
        "service": "crew-runner",
        "version": __version__,
        "active_sessions": worker_client.get_active_sessions(),
        "max_sessions": worker_client.get_max_sessions(),
        "at_capacity": worker_client.get_active_sessions() >= worker_client.get_max_sessions()
    }

@app.get("/version", tags=["system"])
async def version():
    """Return the service version."""
    return {
        "version": __version__,
        "service": "crew-runner"
    }


@app.post("/execute", response_model=ExecuteResponse, tags=["execution"])
async def execute(request: ExecuteRequest):
    """Execute a CrewAI crew and return the result.

    The crew runs in a thread pool to avoid blocking the event loop.
    The backend sends crew_config and agent_configs via the inputs dict
    (under _crew_config and _agent_configs keys).
    """
    start = time.monotonic()
    execution_id = request.execution_id or "unknown"

    crew_config = request.inputs.get("_crew_config", {})
    agent_configs = request.inputs.get("_agent_configs", [])

    if not crew_config or not agent_configs:
        return ExecuteResponse(
            success=False,
            output="Missing _crew_config or _agent_configs in inputs",
            error="Missing crew configuration",
            duration_seconds=time.monotonic() - start,
        )

    # Filter out internal keys for kickoff inputs
    kickoff_inputs = {k: v for k, v in request.inputs.items() if not k.startswith("_")}

    logger.info(
        "execute: starting execution=%s tenant=%s agents=%d tasks=%d",
        execution_id, request.tenant_id, len(agent_configs),
        len(crew_config.get("tasks", [])),
    )
    worker_client.increment_sessions()


    try:
        log_collector = ExecutionLogCollector()
        crew = _build_crew_from_config(

            crew_config,
            agent_configs,
            step_callback=log_collector.step_callback,
            task_callback=log_collector.task_callback,
        )

        crew_output = await asyncio.wait_for(
            asyncio.to_thread(crew.kickoff, inputs=kickoff_inputs),
            timeout=request.timeout_seconds,
        )

        # Consume streaming output only for actual generators/streaming types
        # CrewOutput is iterable but should NOT be consumed this way
        import types
        if isinstance(crew_output, types.GeneratorType) or hasattr(crew_output, '__next__'):
            collected = None
            for chunk in crew_output:
                collected = chunk
            if collected is not None:
                crew_output = collected

        raw_output = _extract_raw_output(crew_output)
        token_usage = _extract_token_usage(crew_output)
        tasks_output = _extract_tasks_output(crew_output)
        execution_logs = log_collector.get_logs() or None
        duration = time.monotonic() - start

        logger.info(
            "execute: completed execution=%s duration=%.1fs output_len=%d logs=%d",
            execution_id, duration, len(raw_output),
            len(execution_logs) if execution_logs else 0,
        )

        worker_client.decrement_sessions()
        return ExecuteResponse(
            success=True,
            output=raw_output,
            token_usage=token_usage,
            tasks_output=tasks_output,
            execution_logs=execution_logs,
            duration_seconds=duration,
        )

    except TimeoutError as exc:
        duration = time.monotonic() - start
        logger.error("execute: timeout execution=%s after %.1fs: %s", execution_id, duration, exc)
        worker_client.decrement_sessions()
        return ExecuteResponse(
            success=False,
            output=f"Execution timed out after {duration:.0f}s",
            error=str(exc),
            duration_seconds=duration,
            execution_logs=log_collector.get_logs() or None,
        )
    except Exception as exc:
        duration = time.monotonic() - start
        logger.error("execute: error execution=%s: %s", execution_id, exc, exc_info=True)
        worker_client.decrement_sessions()
        return ExecuteResponse(
            success=False,
            output=str(exc),
            error=str(exc),
            duration_seconds=duration,
            execution_logs=log_collector.get_logs() or None,
        )


@app.post("/execute/stream", tags=["execution"])
async def execute_stream(request: ExecuteRequest):
    """Execute a CrewAI crew and stream NDJSON events during execution.

    Streams intermediate step/task events as they happen, then a final
    result event with the full output.  Each line is a JSON object followed
    by a newline (NDJSON / application/x-ndjson).

    Event types:
        {"type": "step", "thought": ..., "action": ..., "observation": ...}
        {"type": "task_completion", "description": ..., "raw": ..., "agent": ...}
        {"type": "result", "success": ..., "output": ..., "token_usage": ..., "tasks_output": ..., "duration_seconds": ...}
        {"type": "error", "error": ...}
    """
    start = time.monotonic()
    execution_id = request.execution_id or "unknown"

    crew_config = request.inputs.get("_crew_config", {})
    agent_configs = request.inputs.get("_agent_configs", [])

    if not crew_config or not agent_configs:
        async def error_gen():
            yield json_mod.dumps({
                "type": "error",
                "error": "Missing _crew_config or _agent_configs in inputs",
            }) + "\n"
        return StreamingResponse(error_gen(), media_type="application/x-ndjson")

    kickoff_inputs = {k: v for k, v in request.inputs.items() if not k.startswith("_")}

    # Track session capacity for this streaming execution
    worker_client.increment_sessions()

    async def generate():
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        def step_callback(agent_step):
            """Sync callback invoked by CrewAI on each agent step — queues an event."""
            try:
                thought = str(getattr(agent_step, "thought", "") or "")
                action = str(getattr(agent_step, "action", "") or "")
                observation = str(getattr(agent_step, "observation", "") or "")
                event_queue.put_nowait({
                    "type": "step",
                    "thought": thought[:65000],
                    "action": action[:65000],
                    "observation": observation[:65000],
                })
            except asyncio.QueueFull:
                logger.warning("execute_stream: event queue full, dropping step event")
            except Exception:
                pass

        def task_callback(task_output):
            """Sync callback invoked by CrewAI on task completion — queues an event."""
            try:
                description = str(getattr(task_output, "description", "") or "")
                raw = str(getattr(task_output, "raw", "") or "")
                agent_name = str(getattr(task_output, "agent", "") or "")
                event_queue.put_nowait({
                    "type": "task_completion",
                    "description": description[:2000],
                    "raw": raw[:60000],
                    "agent": agent_name[:500],
                })
            except asyncio.QueueFull:
                logger.warning("execute_stream: event queue full, dropping task event")
            except Exception:
                pass

        try:
            crew = _build_crew_from_config(
                crew_config, agent_configs,
                step_callback=step_callback,
                task_callback=task_callback,
            )

            # Run crew.kickoff() in a thread so we can stream events concurrently
            kickoff_future = asyncio.create_task(
                asyncio.wait_for(
                    asyncio.to_thread(crew.kickoff, inputs=kickoff_inputs),
                    timeout=request.timeout_seconds,
                )
            )

            # Stream events while crew is running
            while not kickoff_future.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                    yield json_mod.dumps(event) + "\n"
                except asyncio.TimeoutError:
                    continue

            # Drain any remaining queued events
            while not event_queue.empty():
                try:
                    event = event_queue.get_nowait()
                    yield json_mod.dumps(event) + "\n"
                except asyncio.QueueEmpty:
                    break

            # Get the crew result
            crew_output = kickoff_future.result()

            # Consume streaming output only for actual generators
            import types
            if isinstance(crew_output, types.GeneratorType) or hasattr(crew_output, "__next__"):
                collected = None
                for chunk in crew_output:
                    collected = chunk
                if collected is not None:
                    crew_output = collected

            raw_output = _extract_raw_output(crew_output)
            token_usage = _extract_token_usage(crew_output)
            tasks_output = _extract_tasks_output(crew_output)
            duration = time.monotonic() - start

            yield json_mod.dumps({
                "type": "result",
                "success": True,
                "output": raw_output,
                "token_usage": token_usage,
                "tasks_output": tasks_output,
                "duration_seconds": duration,
            }) + "\n"

        except TimeoutError as exc:
            duration = time.monotonic() - start
            yield json_mod.dumps({
                "type": "result",
                "success": False,
                "output": f"Execution timed out after {duration:.0f}s",
                "error": str(exc),
                "duration_seconds": duration,
            }) + "\n"
        except Exception as exc:
            duration = time.monotonic() - start
            logger.error("execute_stream: error execution=%s: %s", execution_id, exc, exc_info=True)
            yield json_mod.dumps({
                "type": "result",
                "success": False,
                "output": str(exc),
                "error": str(exc),
                "duration_seconds": duration,
            }) + "\n"
        finally:
            # Always decrement session counter when streaming completes
            worker_client.decrement_sessions()

    return StreamingResponse(generate(), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8081"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
