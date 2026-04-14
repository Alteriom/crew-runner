"""Normalize LLM outputs that arrive as function-call lists instead of strings.

GLM-5.1:cloud returns a list of ChatCompletionMessageToolCall objects when it
uses its thinking/reasoning capability. CrewAI's TaskOutput.raw expects str,
so we intercept at the LLM.call() boundary.

This is a copy of crewai-backend/src/agents/llm_output_normalizer.py for the
crew-runner sidecar (separate container, no shared imports).
"""
import json
import logging
from crewai import LLM

logger = logging.getLogger(__name__)


def normalize_llm_output(output):
    """Convert non-string LLM outputs to a plain string."""
    if isinstance(output, str):
        return output
    if output is None:
        return ""
    if not isinstance(output, list):
        return str(output)

    texts = []
    for item in output:
        # ChatCompletionMessageToolCall with .function.arguments
        if hasattr(item, "function") and hasattr(item.function, "arguments"):
            args = item.function.arguments
            try:
                parsed = json.loads(args)
                if isinstance(parsed, dict) and "content" in parsed:
                    texts.append(str(parsed["content"]))
                elif isinstance(parsed, str):
                    texts.append(parsed)
                else:
                    texts.append(json.dumps(parsed, ensure_ascii=False))
            except (json.JSONDecodeError, TypeError):
                texts.append(str(args))
        # Dict representation of function/tool call
        elif isinstance(item, dict):
            func = item.get("function", {})
            if isinstance(func, dict) and "arguments" in func:
                texts.append(str(func["arguments"]))
            elif "text" in item:
                texts.append(str(item["text"]))
            elif "content" in item:
                texts.append(str(item["content"]))
            else:
                texts.append(str(item))
        # Object with .text or .content attribute
        elif hasattr(item, "text"):
            texts.append(str(item.text))
        elif hasattr(item, "content"):
            texts.append(str(item.content))
        else:
            texts.append(str(item))

    return "\n".join(texts)


class NormalizedLLM(LLM):
    """LLM wrapper that ensures call() always returns a string.

    Intercepts non-string responses (e.g. function-call lists from GLM-5.1)
    and converts them to plain text before CrewAI's task pipeline sees them.
    """

    def call(self, *args, **kwargs):
        result = super().call(*args, **kwargs)
        if not isinstance(result, str):
            logger.info("NormalizedLLM: non-string response type=%s, normalizing", type(result).__name__)
            return normalize_llm_output(result)
        return result
