"""Tests for bounded, failure-aware recovery strategies."""

import json
from typing import Any

from agent.react_agent import ReactAgent
from config import LLMClient
from healing.detector import FailureDetector
from healing.recovery import RecoveryEngine, RecoveryStrategy
from tools.failure_injector import FailureInjector, FailureType, ToolExecution
from trace.trace_logger import TraceLogger


def _failure(
    category: str,
    tool: str = "search",
    arguments: dict[str, Any] | None = None,
    *,
    modified_arguments: dict[str, Any] | None = None,
    error: str | None = "TimeoutError: simulated timeout",
    step_id: int = 1,
) -> dict[str, Any]:
    failure = {
        "step_id": step_id,
        "tool_name": tool,
        "failure_category": category,
        "original_input": (
            arguments
            if arguments is not None
            else {"query": "What is the capital of France?"}
        ),
        "actual_output": None,
        "error_message": error,
        "failure_origin": "natural_or_unknown",
    }
    if modified_arguments is not None:
        failure["modified_input"] = modified_arguments
    return failure


def _logger(tmp_path, failure: dict[str, Any]) -> TraceLogger:
    logger = TraceLogger(tmp_path)
    logger.log_step(
        thought="Original failing call",
        tool=failure["tool_name"],
        input_data=failure["original_input"],
        output=failure.get("actual_output"),
        error=failure.get("error_message"),
        latency=0.001,
        failure_metadata={
            "tool_name": failure["tool_name"],
            "failure_injected": False,
            "original_input": failure["original_input"],
        },
    )
    return logger


def _success_executor(
    tool_name: str,
    tool,
    arguments: dict[str, Any],
) -> ToolExecution:
    return FailureInjector(enabled=False).execute(tool_name, tool, arguments)


def _run_recovery_attempt(execution: ToolExecution):
    engine = RecoveryEngine()
    failure = _failure("TIMEOUT")
    tool = lambda arguments: "Paris"
    return engine._run_tool_attempt(
        strategy=RecoveryStrategy.RETRY,
        attempt_number=1,
        original_step_id=1,
        original_tool="search",
        failure=failure,
        tool_name="search",
        arguments={"query": "What is the capital of France?"},
        tools={"search": tool},
        executor=lambda tool_name, selected_tool, arguments: execution,
    )


def test_normal_recovery_execution_reports_not_injected_provenance() -> None:
    execution = ToolExecution(
        output="Paris",
        error=None,
        failure_type=None,
        failure_injected=False,
        original_arguments={"query": "What is the capital of France?"},
        execution_arguments={"query": "What is the capital of France?"},
        execution_status="success",
    )

    _, detection, record = _run_recovery_attempt(execution)

    assert detection.failure_origin == "not_injected"
    assert detection.reported_failure_injected is False
    assert detection.reported_failure_type is None
    assert record["recovery_execution_result"]["failure_injected"] is False
    assert record["recovery_execution_result"]["reported_failure_type"] is None


def test_injected_recovery_execution_reports_current_attempt_provenance() -> None:
    execution = ToolExecution(
        output="Paris",
        error=None,
        failure_type=FailureType.WRONG_OUTPUT,
        failure_injected=True,
        original_arguments={"query": "What is the capital of France?"},
        execution_arguments={"query": "What is the capital of France?"},
        execution_status="injected_failure",
    )

    _, detection, record = _run_recovery_attempt(execution)

    assert detection.failure_origin == "injected"
    assert detection.reported_failure_injected is True
    assert detection.reported_failure_type == FailureType.WRONG_OUTPUT.value
    assert detection.failure_detected is False
    execution_result = record["recovery_execution_result"]
    assert execution_result["failure_injected"] is True
    assert execution_result["reported_failure_type"] == FailureType.WRONG_OUTPUT.value
    assert execution_result["execution_status"] == "injected_failure"


def test_recovery_execution_without_provenance_metadata_is_unknown() -> None:
    detection = FailureDetector().detect_step(
        {
            "step_id": 1,
            "tool": "search",
            "input": {"query": "What is the capital of France?"},
            "output": "Paris",
            "error": None,
            "execution_status": "success",
        }
    )

    assert detection.failure_origin == "unknown"
    assert detection.reported_failure_injected is None
    assert detection.reported_failure_type is None
    assert detection.failure_detected is False


def test_injection_metadata_alone_does_not_trigger_failure() -> None:
    detection = FailureDetector().detect_step(
        {
            "step_id": 1,
            "tool": "search",
            "input": {"query": "What is the capital of France?"},
            "output": "Paris",
            "error": None,
            "execution_status": "injected_failure",
            "failure_injected": True,
            "failure_type": FailureType.WRONG_OUTPUT.value,
        }
    )

    assert detection.failure_detected is False
    assert detection.failure_origin == "injected"
    assert detection.reported_failure_type == FailureType.WRONG_OUTPUT.value


def test_recovery_attempt_record_preserves_execution_metadata(tmp_path) -> None:
    failure = _failure("TIMEOUT")
    logger = _logger(tmp_path, failure)
    execution = ToolExecution(
        output="Paris",
        error=None,
        failure_type=FailureType.WRONG_OUTPUT,
        failure_injected=True,
        original_arguments={"query": "What is the capital of France?"},
        execution_arguments={"query": "What is the capital of France?"},
        execution_status="injected_failure",
    )

    result = RecoveryEngine().recover(
        failure,
        "What is the capital of France?",
        [],
        {"search": lambda arguments: "Paris"},
        lambda tool_name, tool, arguments: execution,
        lambda question, observations, excluded: {},
        logger,
    )

    logged_attempt = json.loads(logger.recovery_path.read_text(encoding="utf-8"))
    execution_result = logged_attempt["recovery_execution_result"]
    assert result.recovery_success is True
    assert execution_result["failure_injected"] is True
    assert execution_result["reported_failure_type"] == FailureType.WRONG_OUTPUT.value
    assert execution_result["execution_status"] == "injected_failure"
    assert execution_result["modified_input"] == execution.execution_arguments


def test_retry_recovers_after_a_timeout(tmp_path) -> None:
    failure = _failure("TIMEOUT")
    logger = _logger(tmp_path, failure)
    original_trace = logger.path.read_text(encoding="utf-8")
    calls = 0

    def executor(tool_name, tool, arguments):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ToolExecution(
                output=None,
                error="TimeoutError: retry also timed out",
                failure_type=None,
                failure_injected=False,
                original_arguments=dict(arguments),
                execution_arguments=dict(arguments),
                execution_status="failed",
            )
        return _success_executor(tool_name, tool, arguments)

    result = RecoveryEngine(max_retries=2).recover(
        failure,
        "What is the capital of France?",
        [],
        {"search": lambda args: "Paris"},
        executor,
        lambda question, observations, excluded: {},
        logger,
    )

    assert result.recovery_success is True
    assert result.recovered_output == "Paris"
    assert result.report["attempt_count"] == 2
    assert result.report["strategies_used"] == ["RETRY"]
    assert logger.path.read_text(encoding="utf-8") == original_trace


def test_retry_limit_is_respected(tmp_path) -> None:
    failure = _failure("TIMEOUT")
    logger = _logger(tmp_path, failure)
    calls = 0

    def always_timeout(tool_name, tool, arguments):
        nonlocal calls
        calls += 1
        return ToolExecution(
            output=None,
            error="TimeoutError: still unavailable",
            failure_type=None,
            failure_injected=False,
            original_arguments=dict(arguments),
            execution_arguments=dict(arguments),
            execution_status="failed",
        )

    result = RecoveryEngine(max_retries=2).recover(
        failure,
        "What is the capital of France?",
        [],
        {"search": lambda args: "Paris"},
        always_timeout,
        lambda question, observations, excluded: {},
        logger,
    )

    assert result.recovery_success is False
    assert calls == 2
    assert result.report["attempt_count"] == 2


def test_argument_repair_reuses_original_valid_value(tmp_path) -> None:
    failure = _failure(
        "INVALID_ARGUMENTS",
        arguments={"query": "What is the capital of France?"},
        modified_arguments={},
        error="KeyError: 'query'",
    )
    logger = _logger(tmp_path, failure)
    seen_arguments: list[dict[str, Any]] = []

    def tool(arguments):
        seen_arguments.append(dict(arguments))
        return "The capital is Paris."

    result = RecoveryEngine().recover(
        failure,
        "What is the capital of France?",
        [],
        {"search": tool},
        _success_executor,
        lambda question, observations, excluded: {},
        logger,
    )

    assert result.recovery_success is True
    assert seen_arguments == [{"query": "What is the capital of France?"}]
    assert result.report["attempts"][0]["repaired_arguments"] == seen_arguments[0]


def test_argument_repair_does_not_invent_missing_values(tmp_path) -> None:
    failure = _failure(
        "INVALID_ARGUMENTS",
        arguments={},
        modified_arguments={},
        error="KeyError: 'query'",
    )
    logger = _logger(tmp_path, failure)
    calls = 0

    def should_not_run(tool_name, tool, arguments):
        nonlocal calls
        calls += 1
        return _success_executor(tool_name, tool, arguments)

    result = RecoveryEngine().recover(
        failure,
        "What is the capital of France?",
        [],
        {"search": lambda args: "Paris"},
        should_not_run,
        lambda question, observations, excluded: {},
        logger,
    )

    assert result.recovery_success is False
    assert calls == 0
    assert "unavailable in the original input" in result.final_error


def test_tool_switch_uses_compatible_calculation_tool(tmp_path) -> None:
    failure = _failure(
        "UNEXPECTED_RESPONSE",
        tool="calculator",
        arguments={"expression": "4 * 5"},
        error=None,
    )
    logger = _logger(tmp_path, failure)
    tools = {
        "calculator": lambda args: 99,
        "python_exec": lambda args: 20,
    }

    result = RecoveryEngine().recover(
        failure,
        "What is 4 * 5?",
        [],
        tools,
        _success_executor,
        lambda question, observations, excluded: {},
        logger,
    )

    assert result.recovery_success is True
    assert result.recovered_tool == "python_exec"
    assert result.recovered_output == 20
    assert result.report["attempts"][0]["strategy"] == "TOOL_SWITCH"


def test_replan_uses_original_query_and_excludes_failed_tool(tmp_path) -> None:
    failure = _failure(
        "UNEXPECTED_RESPONSE",
        tool="search",
        error=None,
    )
    logger = _logger(tmp_path, failure)
    planner_calls: list[tuple[str, set[str]]] = []
    tools = {
        "search": lambda args: "incorrect",
        "fallback": lambda args: "verified local result",
    }
    engine = RecoveryEngine(
        strategy_map={
            "UNEXPECTED_RESPONSE": [RecoveryStrategy.REPLAN],
        }
    )

    def planner(question, observations, excluded):
        planner_calls.append((question, excluded))
        return {"action": "fallback", "arguments": {"query": question}}

    result = engine.recover(
        failure,
        "Original question stays intact",
        [],
        tools,
        _success_executor,
        planner,
        logger,
    )

    assert result.recovery_success is True
    assert result.recovered_tool == "fallback"
    assert planner_calls == [("Original question stays intact", {"search"})]


def test_duplicate_replan_action_is_blocked(tmp_path) -> None:
    failure = _failure("UNEXPECTED_RESPONSE", tool="search", error=None)
    logger = _logger(tmp_path, failure)
    calls = 0

    def should_not_run(tool_name, tool, arguments):
        nonlocal calls
        calls += 1
        return _success_executor(tool_name, tool, arguments)

    result = RecoveryEngine(
        strategy_map={"UNEXPECTED_RESPONSE": ["REPLAN"]}
    ).recover(
        failure,
        "Original query",
        [],
        {"search": lambda args: "output"},
        should_not_run,
        lambda question, observations, excluded: {
            "action": "search",
            "arguments": {"query": question},
        },
        logger,
    )

    assert result.recovery_success is False
    assert calls == 0
    assert result.report["attempt_count"] == 1
    assert "Duplicate" in result.final_error


def test_total_recovery_attempt_cap_stops_strategy_chain(tmp_path) -> None:
    failure = _failure(
        "EMPTY_OUTPUT",
        tool="calculator",
        arguments={"expression": "3 + 4"},
        error=None,
    )
    logger = _logger(tmp_path, failure)
    result = RecoveryEngine(max_retries=5, max_total_attempts=2).recover(
        failure,
        "What is 3 + 4?",
        [],
        {
            "calculator": lambda args: None,
            "python_exec": lambda args: 7,
        },
        _success_executor,
        lambda question, observations, excluded: {},
        logger,
    )

    assert result.report["attempt_count"] == 2
    assert result.report["strategies_used"] == ["RETRY"]


def test_recovery_attempts_are_logged_separately_and_original_trace_is_preserved(
    tmp_path,
) -> None:
    failure = _failure("TIMEOUT")
    logger = _logger(tmp_path, failure)
    original_lines = logger.path.read_text(encoding="utf-8").splitlines()

    RecoveryEngine().recover(
        failure,
        "What is the capital of France?",
        [],
        {"search": lambda args: "Paris"},
        _success_executor,
        lambda question, observations, excluded: {},
        logger,
    )

    recovery_records = [
        json.loads(line)
        for line in logger.recovery_path.read_text(encoding="utf-8").splitlines()
    ]
    assert logger.path.read_text(encoding="utf-8").splitlines() == original_lines
    assert recovery_records[0]["record_type"] == "recovery_attempt"
    assert recovery_records[0]["original_step_id"] == 1
    assert recovery_records[0]["strategy"] == "RETRY"
    assert recovery_records[0]["recovery_success"] is True
    assert "execution_latency" in recovery_records[0]
    assert recovery_records[0]["timestamp"]


def test_agent_reports_failed_recovery_clearly(tmp_path) -> None:
    agent = ReactAgent(
        client=LLMClient(provider="local"),
        failure_injector=FailureInjector(
            failure_rate=100,
            seed=42,
            enabled_types=["timeout"],
        ),
        log_directory=tmp_path,
    )

    result = agent.run("What is the capital of France?")

    assert "could not be completed reliably" in result.answer
    assert result.recovery_report["total_detected_failures"] == 1
    assert result.recovery_report["total_recovery_attempts"] == 1
    assert result.recovery_report["successful_recoveries"] == 0
    assert result.recovery_report["failed_recoveries"] == 1


def test_agent_recovery_summary_is_zero_for_normal_operation(tmp_path) -> None:
    result = ReactAgent(
        client=LLMClient(provider="local"),
        failure_injector=FailureInjector(enabled=False),
        log_directory=tmp_path,
    ).run("What is the capital of France?")

    assert result.answer == "The capital of France is Paris."
    assert result.recovery_report["total_detected_failures"] == 0
    assert result.recovery_report["total_recovery_attempts"] == 0
    assert result.recovery_report["successful_recoveries"] == 0
    assert result.recovery_report["failed_recoveries"] == 0


def test_recovered_result_is_passed_back_to_the_agent(tmp_path) -> None:
    class FailFirstCall:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, tool_name, tool, arguments):
            self.calls += 1
            if self.calls == 1:
                return ToolExecution(
                    output=None,
                    error="TimeoutError: first call timed out",
                    failure_type=None,
                    failure_injected=False,
                    original_arguments=dict(arguments),
                    execution_arguments=dict(arguments),
                    execution_status="failed",
                )
            return _success_executor(tool_name, tool, arguments)

    result = ReactAgent(
        client=LLMClient(provider="local"),
        failure_injector=FailFirstCall(),
        log_directory=tmp_path,
    ).run("What is the capital of France?")

    assert result.answer == "The capital of France is Paris."
    assert result.recovery_report["successful_recoveries"] == 1
    persisted_report = json.loads(
        result.recovery_report_path.read_text(encoding="utf-8")
    )
    assert persisted_report["failures"][0]["recovered_output"] == (
        "The capital of France is Paris."
    )


def test_max_retries_caps_only_retry_and_allows_subsequent_strategy_within_total_cap(
    tmp_path,
) -> None:
    """Verify max_retries=1 bounds only RETRY while max_total_attempts=4 lets TOOL_SWITCH run."""
    failure = _failure(
        "EMPTY_OUTPUT",
        tool="calculator",
        arguments={"expression": "6 * 7"},
        error=None,
    )
    logger = _logger(tmp_path, failure)

    def executor(tool_name, tool, arguments):
        if tool_name == "calculator":
            return ToolExecution(
                output=None,
                error="TimeoutError: calculator still failing",
                failure_type=None,
                failure_injected=False,
                original_arguments=dict(arguments),
                execution_arguments=dict(arguments),
                execution_status="failed",
            )
        return _success_executor(tool_name, tool, arguments)

    result = RecoveryEngine(max_retries=1, max_total_attempts=4).recover(
        failure,
        "What is 6 * 7?",
        [],
        {"calculator": lambda args: None, "python_exec": lambda args: 42},
        executor,
        lambda question, observations, excluded: {},
        logger,
    )

    assert result.recovery_success is True
    assert result.recovered_tool == "python_exec"
    assert result.recovered_output == 42
    assert result.report["attempt_count"] == 2
    assert [a["strategy"] for a in result.report["attempts"]] == [
        "RETRY",
        "TOOL_SWITCH",
    ]


def test_max_total_attempts_caps_combined_multistrategy_chain(tmp_path) -> None:
    """Verify max_total_attempts=4 stops combined multi-strategy chains cleanly."""
    failure = _failure(
        "EMPTY_OUTPUT",
        tool="calculator",
        arguments={"expression": "6 * 7"},
        error=None,
    )
    logger = _logger(tmp_path, failure)

    def always_fail(tool_name, tool, arguments):
        return ToolExecution(
            output=None,
            error=f"RuntimeError: {tool_name} failed",
            failure_type=None,
            failure_injected=False,
            original_arguments=dict(arguments),
            execution_arguments=dict(arguments),
            execution_status="failed",
        )

    engine = RecoveryEngine(
        strategy_map={
            "EMPTY_OUTPUT": [
                RecoveryStrategy.RETRY,
                RecoveryStrategy.TOOL_SWITCH,
                RecoveryStrategy.REPLAN,
            ]
        },
        max_retries=3,
        max_total_attempts=4,
    )
    result = engine.recover(
        failure,
        "What is 6 * 7?",
        [],
        {
            "calculator": lambda args: None,
            "python_exec": lambda args: None,
            "search": lambda args: None,
        },
        always_fail,
        lambda q, obs, excl: {"action": "search", "arguments": {"query": q}},
        logger,
    )

    assert result.recovery_success is False
    assert result.report["attempt_count"] == 4
    assert result.report["strategies_used"] == ["RETRY", "TOOL_SWITCH"]


def test_max_duration_seconds_stops_recovery_between_attempts(
    tmp_path, monkeypatch
) -> None:
    """Verify max_duration_seconds=15.0 (or configured budget) halts between attempts."""
    failure = _failure(
        "EMPTY_OUTPUT",
        tool="calculator",
        arguments={"expression": "6 * 7"},
        error=None,
    )
    logger = _logger(tmp_path, failure)

    ticks = iter([100.0, 101.0, 102.0, 120.0])
    monkeypatch.setattr("healing.recovery.time.monotonic", lambda: next(ticks, 120.0))

    def always_fail(tool_name, tool, arguments):
        return ToolExecution(
            output=None,
            error="TimeoutError: timed out",
            failure_type=None,
            failure_injected=False,
            original_arguments=dict(arguments),
            execution_arguments=dict(arguments),
            execution_status="failed",
        )

    result = RecoveryEngine(
        max_retries=2,
        max_total_attempts=4,
        max_duration_seconds=15.0,
    ).recover(
        failure,
        "What is 6 * 7?",
        [],
        {"calculator": lambda args: None, "python_exec": lambda args: 42},
        always_fail,
        lambda question, observations, excluded: {},
        logger,
    )

    assert result.recovery_success is False
    assert result.report["attempt_count"] == 1
    assert "time limit" in (result.final_error or "").lower()


def test_default_strategy_map_respects_retry_and_total_attempt_bounds() -> None:
    """Confirm default RecoveryEngine configuration and strategy map bounds."""
    from healing.recovery import DEFAULT_STRATEGY_MAP

    engine = RecoveryEngine()
    assert engine.max_retries == 1
    assert engine.max_total_attempts == 4
    assert engine.max_duration_seconds == 15.0
    for strategies in DEFAULT_STRATEGY_MAP.values():
        assert 1 <= len(strategies) <= engine.max_total_attempts