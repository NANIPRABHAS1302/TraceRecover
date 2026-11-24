"""Tests for rule-based tool failure detection."""

from typing import Any

from healing.detector import FailureCategory, FailureDetector, Severity


def _step(
    *,
    step_id: int = 1,
    tool: str = "search",
    input_data: dict[str, Any] | None = None,
    output: Any = "The capital of France is Paris.",
    error: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    step = {
        "step_id": step_id,
        "tool": tool,
        "input": input_data or {"query": "What is the capital of France?"},
        "output": output,
        "error": error,
        "execution_status": "success" if error is None else "failed",
    }
    step.update(extra)
    return step


def test_successful_tool_execution_is_not_a_failure() -> None:
    result = FailureDetector().detect_step(_step())

    assert result.failure_detected is False
    assert result.failure_category is FailureCategory.NONE
    assert result.severity is Severity.LOW


def test_injection_label_alone_does_not_trigger_failure() -> None:
    result = FailureDetector().detect_step(
        _step(
            failure_injected=True,
            failure_type="timeout",
            execution_status="injected_failure",
        )
    )

    assert result.failure_detected is False
    assert result.failure_origin == "injected"
    assert result.reported_failure_type == "timeout"


def test_timeout_error_is_detected_without_injection_label() -> None:
    result = FailureDetector().detect_step(
        _step(error="TimeoutError: remote search took too long")
    )

    assert result.failure_detected is True
    assert result.failure_category is FailureCategory.TIMEOUT
    assert result.severity is Severity.HIGH
    assert result.confidence == 1.0
    assert result.failure_origin == "unknown"


def test_empty_tool_output_is_detected() -> None:
    result = FailureDetector().detect_step(_step(output=None))

    assert result.failure_detected is True
    assert result.failure_category is FailureCategory.EMPTY_OUTPUT


def test_wrong_calculator_result_is_detected_from_input_and_output() -> None:
    result = FailureDetector().detect_step(
        _step(
            tool="calculator",
            input_data={"expression": "2 + 2"},
            output=5,
        )
    )

    assert result.failure_detected is True
    assert result.failure_category is FailureCategory.UNEXPECTED_RESPONSE
    assert "does not match" in result.explanation


def test_wrong_output_format_is_detected() -> None:
    result = FailureDetector().detect_step(_step(output={"answer": "Paris"}))

    assert result.failure_detected is True
    assert result.failure_category is FailureCategory.UNEXPECTED_OUTPUT_FORMAT


def test_bad_arguments_are_detected_from_effective_input() -> None:
    result = FailureDetector().detect_step(
        _step(
            error="KeyError: 'query'",
            modified_input={},
            failure_injected=False,
        )
    )

    assert result.failure_detected is True
    assert result.failure_category is FailureCategory.INVALID_ARGUMENTS


def test_missing_output_field_is_detected() -> None:
    step = _step()
    step.pop("output")

    result = FailureDetector().detect_step(step)

    assert result.failure_detected is True
    assert result.failure_category is FailureCategory.MISSING_OUTPUT


def test_tool_execution_error_is_detected() -> None:
    result = FailureDetector().detect_step(
        _step(
            tool="mock_api",
            input_data={"ticket_id": "T-999"},
            error="LookupError: Ticket 'T-999' was not found.",
        )
    )

    assert result.failure_detected is True
    assert result.failure_category is FailureCategory.TOOL_EXECUTION_ERROR


def test_missing_required_mock_api_fields_are_detected() -> None:
    result = FailureDetector().detect_step(
        _step(
            tool="mock_api",
            input_data={"ticket_id": "T-104"},
            output={"ticket_id": "T-104"},
        )
    )

    assert result.failure_detected is True
    assert result.failure_category is FailureCategory.MISSING_REQUIRED_FIELDS


def test_detector_reports_every_failed_tool_step() -> None:
    trace = [
        _step(step_id=1),
        _step(step_id=2, error="TimeoutError: simulated"),
        _step(
            step_id=3,
            tool="calculator",
            input_data={"expression": "10 - 4"},
            output=0,
        ),
    ]

    results = FailureDetector().detect_trace(trace)

    assert [result.step_id for result in results if result.failure_detected] == [2, 3]
    assert all(0 <= result.confidence <= 1 for result in results)