"""Tests for crew-runner llm_output_normalizer."""
import json
import sys
import os
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))

from llm_output_normalizer import normalize_llm_output, NormalizedLLM


class TestNormalizeLlmOutput:
    def test_string_passthrough(self):
        assert normalize_llm_output("hello") == "hello"

    def test_none_returns_empty(self):
        assert normalize_llm_output(None) == ""

    def test_empty_list(self):
        assert normalize_llm_output([]) == ""

    def test_int_fallback(self):
        assert normalize_llm_output(42) == "42"

    def test_function_call_objects(self):
        call1 = MagicMock()
        call1.function.arguments = "The answer is 42"
        call2 = MagicMock()
        call2.function.arguments = "More details here"
        result = normalize_llm_output([call1, call2])
        assert "The answer is 42" in result
        assert "More details here" in result

    def test_json_arguments_with_content(self):
        call = MagicMock()
        call.function.arguments = json.dumps({"content": "extracted text"})
        result = normalize_llm_output([call])
        assert result == "extracted text"

    def test_dict_function_calls(self):
        items = [{"function": {"arguments": "The answer is 42"}}]
        assert normalize_llm_output(items) == "The answer is 42"

    def test_dict_with_content(self):
        items = [{"content": "hello"}]
        assert normalize_llm_output(items) == "hello"

    def test_mixed_list(self):
        call = MagicMock()
        call.function.arguments = "first"
        items = [call, {"content": "second"}]
        result = normalize_llm_output(items)
        assert "first" in result
        assert "second" in result


class TestNormalizedLLM:
    def test_is_subclass_of_llm(self):
        from crewai import LLM
        assert issubclass(NormalizedLLM, LLM)
