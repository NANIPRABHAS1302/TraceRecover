"""Unit tests for ToolExecutor registry, AST/schema validation, caching, and output states."""

from __future__ import annotations

from agent.react_agent import ReactAgent
from config import LLMClient
from tools.executor import ToolExecutor
from tools.failure_injector import FailureInjector
from trace.trace_logger import TraceLogger


def test_tool_executor_validates_schema_and_ast_before_execution() -> None:
    executor = ToolExecutor.with_default_tools()

    assert executor.validate_arguments("calculator", {"expression": "12 * 4"}) is None
    assert executor.validate_arguments("calculator", {"expression": "__import__('os')"}) is not None
    assert executor.validate_arguments("python_exec", {"code": "sum([1, 2, 3])"}) is None
    assert executor.validate_arguments("python_exec", {"code": "open('x')"}) is not None
    assert executor.validate_arguments("mock_api", {"ticket_id": "T-401"}) is None
    assert executor.validate_arguments("mock_api", {"ticket_id": "INVALID"}) is not None
    assert executor.validate_arguments("search", {"query": ""}) is not None

    bad_outcome = executor.execute("calculator", {"expression": "__import__('os')"})
    assert bad_outcome.output_state == "discarded"
    assert bad_outcome.validation_error is not None
    assert bad_outcome.execution.error is not None


def test_tool_executor_caches_deterministic_calls_and_bypasses_when_injector_active() -> None:
    calls = 0

    def counting_tool(args):
        nonlocal calls
        calls += 1
        return f"val:{args['query']}"

    executor = ToolExecutor(enable_cache=True)
    executor.register("search", counting_tool, required_fields={"query": str})

    first = executor.execute("search", {"query": "alpha"})
    second = executor.execute("search", {"query": "alpha"})
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.output_state == "committed"
    assert second.output_state == "committed"
    assert calls == 1
    assert executor.cache_stats() == {"hits": 1, "misses": 1, "entries": 1}

    # Active failure injector (failure_rate > 0) must bypass the cache
    active_injector = FailureInjector(failure_rate=50, seed=7)
    third = executor.execute(
        "search", {"query": "alpha"}, failure_injector=active_injector
    )
    assert third.cache_hit is False
    assert executor.cache_stats()["hits"] == 1


def test_tool_executor_logs_explicit_committed_and_discarded_output_states(tmp_path) -> None:
    executor = ToolExecutor.with_default_tools()
    logger = TraceLogger(tmp_path)

    ok_outcome, ok_step = executor.execute_and_log(
        thought="Lookup valid ticket",
        tool_name="mock_api",
        arguments={"ticket_id": "T-401"},
        logger=logger,
    )
    fail_outcome, fail_step = executor.execute_and_log(
        thought="Lookup unknown ticket",
        tool_name="mock_api",
        arguments={"ticket_id": "T-999"},
        logger=logger,
    )

    assert ok_outcome.output_state == "committed"
    assert ok_step["output_state"] == "committed"
    assert fail_outcome.output_state == "discarded"
    assert fail_step["output_state"] == "discarded"


def test_react_agent_with_tool_executor_tags_committed_state(tmp_path) -> None:
    executor = ToolExecutor.with_default_tools()
    agent = ReactAgent(
        client=LLMClient(provider="local"),
        tool_executor=executor,
        log_directory=tmp_path,
    )
    result = agent.run("What is 19 * 23 + 4?")

    assert result.final_decision == "ANSWER"
    assert result.trace[0]["output_state"] == "committed"
    assert executor.cache_stats()["entries"] == 1
