"""Tests for the crew-runner FastAPI service."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from main import app, ExecutionLogCollector, _build_crewai_agent


client = TestClient(app)


class TestHealth:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "crew-runner"


class TestExecute:
    def test_missing_crew_config(self):
        resp = client.post("/execute", json={
            "prompt": "test",
            "inputs": {},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "Missing" in data["output"]

    def test_missing_agent_configs(self):
        resp = client.post("/execute", json={
            "prompt": "test",
            "inputs": {
                "_crew_config": {"name": "test", "tasks": [], "process_type": "sequential"},
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    @patch("main._build_crew_from_config")
    def test_successful_execution(self, mock_build):
        mock_crew = MagicMock()
        mock_output = MagicMock(spec=["raw", "token_usage", "tasks_output"])
        mock_output.raw = "Crew result output"
        mock_output.token_usage = MagicMock(
            total_tokens=100, prompt_tokens=80,
            cached_prompt_tokens=0, completion_tokens=20,
            successful_requests=1,
        )
        mock_output.tasks_output = []
        mock_crew.kickoff.return_value = mock_output
        mock_build.return_value = mock_crew

        resp = client.post("/execute", json={
            "prompt": "test prompt",
            "execution_id": "exec-001",
            "tenant_id": "tenant-001",
            "inputs": {
                "_crew_config": {"name": "test", "tasks": [], "process_type": "sequential"},
                "_agent_configs": [{"role": "test", "goal": "test", "backstory": "test"}],
                "topic": "AI trends",
            },
            "timeout_seconds": 60,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["output"] == "Crew result output"
        assert data["token_usage"]["total_tokens"] == 100
        assert data["duration_seconds"] is not None

    @patch("main._build_crew_from_config")
    def test_execution_exception(self, mock_build):
        mock_build.side_effect = Exception("LLM connection failed")

        resp = client.post("/execute", json={
            "prompt": "test",
            "inputs": {
                "_crew_config": {"name": "test", "tasks": [], "process_type": "sequential"},
                "_agent_configs": [{"role": "test", "goal": "test", "backstory": "test"}],
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "LLM connection failed" in data["output"]

    def test_kickoff_inputs_exclude_internal_keys(self):
        """Verify that _prefixed keys are not passed to crew.kickoff."""
        with patch("main._build_crew_from_config") as mock_build:
            mock_crew = MagicMock()
            mock_output = MagicMock()
            mock_output.raw = "done"
            mock_output.token_usage = None
            mock_output.tasks_output = []
            mock_crew.kickoff.return_value = mock_output
            mock_build.return_value = mock_crew

            client.post("/execute", json={
                "prompt": "test",
                "inputs": {
                    "_crew_config": {"name": "test", "tasks": [], "process_type": "sequential"},
                    "_agent_configs": [{"role": "a", "goal": "b", "backstory": "c"}],
                    "topic": "AI",
                    "_internal": "should be excluded",
                },
            })

            kickoff_inputs = mock_crew.kickoff.call_args[1]["inputs"]
            assert "topic" in kickoff_inputs
            assert "_crew_config" not in kickoff_inputs
            assert "_agent_configs" not in kickoff_inputs
            assert "_internal" not in kickoff_inputs


class TestExecutionLogCollector:
    def test_step_callback_captures_reasoning(self):
        collector = ExecutionLogCollector()
        step = MagicMock()
        step.thought = "I need to analyze the data"
        step.action = "Analyzing dataset"
        step.observation = "Found 3 patterns"

        collector.step_callback(step)

        logs = collector.get_logs()
        assert len(logs) == 1
        assert logs[0]["type"] == "step"
        assert logs[0]["thought"] == "I need to analyze the data"
        assert logs[0]["action"] == "Analyzing dataset"
        assert logs[0]["observation"] == "Found 3 patterns"

    def test_task_callback_captures_completion(self):
        collector = ExecutionLogCollector()
        task_output = MagicMock()
        task_output.description = "Research AI trends"
        task_output.raw = "AI is advancing rapidly in 2026..."
        task_output.agent = "Researcher"

        collector.task_callback(task_output)

        logs = collector.get_logs()
        assert len(logs) == 1
        assert logs[0]["type"] == "task_completion"
        assert logs[0]["description"] == "Research AI trends"
        assert "AI is advancing" in logs[0]["raw"]
        assert logs[0]["agent"] == "Researcher"

    def test_multiple_callbacks_accumulate(self):
        collector = ExecutionLogCollector()

        step = MagicMock(thought="thinking", action="doing", observation="saw")
        collector.step_callback(step)
        collector.step_callback(step)

        task_output = MagicMock(description="task", raw="output", agent="agent")
        collector.task_callback(task_output)

        assert len(collector.get_logs()) == 3

    def test_step_callback_handles_missing_attrs(self):
        collector = ExecutionLogCollector()
        step = MagicMock(spec=[])  # no attributes

        collector.step_callback(step)

        logs = collector.get_logs()
        assert len(logs) == 1
        assert logs[0]["thought"] == ""

    def test_step_callback_truncates_long_content(self):
        collector = ExecutionLogCollector()
        step = MagicMock()
        step.thought = "x" * 100000
        step.action = ""
        step.observation = ""

        collector.step_callback(step)

        logs = collector.get_logs()
        assert len(logs[0]["thought"]) == 65000

    @patch("main._build_crew_from_config")
    def test_execution_returns_logs(self, mock_build):
        """Verify the /execute endpoint returns execution_logs from callbacks."""
        def capture_build(crew_config, agent_configs, step_callback=None, task_callback=None):
            # Simulate callbacks being invoked during crew build
            if step_callback:
                step = MagicMock()
                step.thought = "[THINK] Analyzing the problem"
                step.action = "reasoning"
                step.observation = ""
                step_callback(step)
            if task_callback:
                task = MagicMock()
                task.description = "Analysis task"
                task.raw = "Analysis complete"
                task.agent = "Analyst"
                task_callback(task)

            mock_crew = MagicMock()
            mock_output = MagicMock()
            mock_output.raw = "Final output"
            mock_output.token_usage = None
            mock_output.tasks_output = []
            mock_crew.kickoff.return_value = mock_output
            return mock_crew

        mock_build.side_effect = capture_build

        resp = client.post("/execute", json={
            "prompt": "test",
            "execution_id": "exec-logs-test",
            "inputs": {
                "_crew_config": {"name": "test", "tasks": [], "process_type": "sequential"},
                "_agent_configs": [{"role": "a", "goal": "b", "backstory": "c"}],
            },
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["execution_logs"] is not None
        assert len(data["execution_logs"]) == 2
        assert data["execution_logs"][0]["type"] == "step"
        assert "[THINK]" in data["execution_logs"][0]["thought"]
        assert data["execution_logs"][1]["type"] == "task_completion"

    @patch("main._build_crew_from_config")
    def test_stream_endpoint_returns_ndjson(self, mock_build):
        """Verify the /execute/stream endpoint streams NDJSON events."""
        def capture_build(crew_config, agent_configs, step_callback=None, task_callback=None):
            if step_callback:
                step = MagicMock()
                step.thought = "Analyzing"
                step.action = ""
                step.observation = ""
                step_callback(step)
            if task_callback:
                task = MagicMock()
                task.description = "Research"
                task.raw = "Done"
                task.agent = "Agent"
                task_callback(task)

            mock_crew = MagicMock()
            mock_output = MagicMock(spec=["raw", "token_usage", "tasks_output"])
            mock_output.raw = "Stream output"
            mock_output.token_usage = None
            mock_output.tasks_output = []
            mock_crew.kickoff.return_value = mock_output
            return mock_crew

        mock_build.side_effect = capture_build

        import json
        resp = client.post("/execute/stream", json={
            "prompt": "test",
            "execution_id": "stream-test",
            "inputs": {
                "_crew_config": {"name": "test", "tasks": [], "process_type": "sequential"},
                "_agent_configs": [{"role": "a", "goal": "b", "backstory": "c"}],
            },
        })

        assert resp.status_code == 200
        lines = [line for line in resp.text.strip().split("\n") if line.strip()]
        events = [json.loads(line) for line in lines]

        # Should have step + task_completion + result events
        types = [e["type"] for e in events]
        assert "step" in types
        assert "task_completion" in types
        assert "result" in types

        result_event = next(e for e in events if e["type"] == "result")
        assert result_event["success"] is True
        assert result_event["output"] == "Stream output"


class TestBuildCrewaiAgentSkills:
    """Test skill injection into agent backstory."""

    def test_skills_appended_to_backstory(self):
        captured = {}

        class MockAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        cfg = {
            "role": "Debug Specialist",
            "goal": "Debug issues",
            "backstory": "You are a debug expert.",
            "skills": [
                {"name": "ssh-essentials", "description": "SSH key routing and server access patterns."},
                {"name": "docker", "description": "Docker compose stack context."},
            ],
        }
        with patch("crewai.Agent", MockAgent):
            _build_crewai_agent(cfg)
        assert "## Loaded Skills" in captured["backstory"]
        assert "### ssh-essentials" in captured["backstory"]
        assert "SSH key routing" in captured["backstory"]
        assert "### docker" in captured["backstory"]
        assert captured["backstory"].startswith("You are a debug expert.")

    def test_no_skills_backstory_unchanged(self):
        captured = {}

        class MockAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        cfg = {
            "role": "Agent",
            "goal": "Do things",
            "backstory": "Original backstory.",
            "skills": [],
        }
        with patch("crewai.Agent", MockAgent):
            _build_crewai_agent(cfg)
        assert captured["backstory"] == "Original backstory."

    def test_skills_without_description_skipped(self):
        captured = {}

        class MockAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        cfg = {
            "role": "Agent",
            "goal": "Do things",
            "backstory": "Base.",
            "skills": [
                {"name": "empty-skill", "description": ""},
                {"name": "real-skill", "description": "Useful context."},
            ],
        }
        with patch("crewai.Agent", MockAgent):
            _build_crewai_agent(cfg)
        assert "### empty-skill" not in captured["backstory"]
        assert "### real-skill" in captured["backstory"]

    def test_missing_skills_key_no_error(self):
        captured = {}

        class MockAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        cfg = {"role": "Agent", "goal": "Do things", "backstory": "Base."}
        with patch("crewai.Agent", MockAgent):
            _build_crewai_agent(cfg)
        assert captured["backstory"] == "Base."
