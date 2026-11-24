"""Focused tests for the Phase 1 deterministic baseline."""

import json

import pytest

from agent.react_agent import ReactAgent
from config import LLMClient
from tools.calculator import calculate
from tools.python_exec import execute
from tools.search_tool import search


def test_tools_return_deterministic_results() -> None:
    assert calculate("19 * 23 + 4") == 441
    assert "Paris" in search("What is the capital of France?")
    assert execute("sum([1, 2, 3])") == 6


def test_safe_python_executor_rejects_imports() -> None:
    with pytest.raises(ValueError, match="safe literals"):
        execute("__import__('os').system('echo unsafe')")


def test_local_agent_logs_tool_observation_and_final_answer(tmp_path) -> None:
    agent = ReactAgent(
        client=LLMClient(provider="local"),
        log_directory=tmp_path,
    )

    result = agent.run("What is the capital of France?")

    assert "Paris" in result.answer
    assert [step["tool"] for step in result.trace] == ["search", "final"]
    required_fields = {
        "step_id", "thought", "tool", "input", "output", "error", "latency", "timestamp"
    }
    assert all(set(step) == required_fields for step in result.trace)
    logged_steps = [
        json.loads(line)
        for line in result.log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert logged_steps == result.trace


def test_mock_api_question_uses_ticket_tool(tmp_path) -> None:
    result = ReactAgent(
        client=LLMClient(provider="local"),
        log_directory=tmp_path,
    ).run("What is the status and owner of ticket T-104?")

    assert result.trace[0]["tool"] == "mock_api"
    assert result.trace[0]["output"]["status"] == "closed"
    assert "Avery" in result.answer
