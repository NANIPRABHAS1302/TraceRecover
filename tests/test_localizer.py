"""Tests for trace localization, reporting, and agent integration."""

import json
from typing import Any

from agent.react_agent import ReactAgent
from config import LLMClient
from healing.detector import FailureDetector
from healing.localizer import FailureLocalizer
from tools.failure_injector import FailureInjector


def _step(
    step_id: int,
    tool: str,
    input_data: dict[str, Any],
    output: Any,
    error: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    step = {
        "step_id": step_id,
        "tool": tool,
        "input": input_data,
        "output": output,
        "error": error,
    }
    step.update(extra)
    return step


def test_localizer_finds_exact_failure_step_and_tool() -> None:
    trace = [
        _step(1, "search", {"query": "capital France"}, "Paris"),
        _step(
            2,
            "calculator",
            {"expression": "8 / 0"},
            None,
            "ZeroDivisionError: division by zero",
        ),
    ]

    localized = FailureLocalizer().localize(trace)

    assert len(localized) == 1
    assert localized[0]["step_id"] == 2
    assert localized[0]["tool_name"] == "calculator"
    assert localized[0]["original_input"] == {"expression": "8 / 0"}
    assert localized[0]["actual_output"] is None
    assert localized[0]["error_message"].startswith("ZeroDivisionError")


def test_localizer_returns_all_failed_steps_in_order() -> None:
    trace = [
        _step(4, "search", {"query": "q"}, None, "TimeoutError: timed out"),
        _step(5, "mock_api", {"ticket_id": "T-104"}, {"unexpected": True}),
    ]

    localized = FailureLocalizer().localize(trace)

    assert [item["step_id"] for item in localized] == [4, 5]
    assert [item["tool_name"] for item in localized] == ["search", "mock_api"]


def test_report_includes_predictions_and_localizations() -> None:
    trace = [
        _step(1, "search", {"query": "capital France"}, "Paris"),
        _step(2, "search", {"query": "capital France"}, None),
    ]

    report = FailureLocalizer().build_report(trace)

    assert report["failure_detected"] is True
    assert report["total_failures"] == 1
    assert report["failures"][0]["step_id"] == 2
    assert report["failures"][0]["failure_category"] == "EMPTY_OUTPUT"
    assert len(report["step_detections"]) == 2


def test_report_separates_detector_prediction_from_injection_provenance() -> None:
    trace = [
        _step(
            1,
            "search",
            {"query": "capital France"},
            "[INJECTED WRONG OUTPUT]",
            failure_injected=True,
            failure_type="wrong_output",
        )
    ]

    failure = FailureLocalizer().build_report(trace)["failures"][0]

    assert failure["failure_detected"] is True
    assert failure["failure_category"] == "UNEXPECTED_RESPONSE"
    assert failure["failure_origin"] == "injected"
    assert failure["reported_failure_type"] == "wrong_output"


def test_disabled_injection_stays_normal_and_writes_separate_analysis(tmp_path) -> None:
    agent = ReactAgent(
        client=LLMClient(provider="local"),
        failure_injector=FailureInjector(enabled=False, failure_rate=100),
        log_directory=tmp_path,
    )

    result = agent.run("What is the capital of France?")

    assert result.answer == "The capital of France is Paris."
    assert result.failure_report["failure_detected"] is False
    assert result.failure_report["total_failures"] == 0
    original_trace = [
        json.loads(line)
        for line in result.log_path.read_text(encoding="utf-8").splitlines()
    ]
    analysis_records = [
        json.loads(line)
        for line in result.analysis_path.read_text(encoding="utf-8").splitlines()
    ]
    saved_report = json.loads(result.report_path.read_text(encoding="utf-8"))

    assert [step["tool"] for step in original_trace] == ["search", "final"]
    assert len(analysis_records) == 1
    assert analysis_records[0]["detection"]["failure_detected"] is False
    assert saved_report["failure_detected"] is False
    assert result.trace == original_trace


def test_agent_detects_and_localizes_injected_timeout(tmp_path) -> None:
    agent = ReactAgent(
        client=LLMClient(provider="local"),
        failure_injector=FailureInjector(
            failure_rate=100,
            seed=9,
            enabled_types=["timeout"],
        ),
        detector=FailureDetector(),
        log_directory=tmp_path,
    )

    result = agent.run("What is the capital of France?")

    assert result.failure_report["total_failures"] == 1
    failure = result.failure_report["failures"][0]
    assert failure["step_id"] == 1
    assert failure["tool_name"] == "search"
    assert failure["failure_category"] == "TIMEOUT"
    assert failure["failure_origin"] == "injected"
    analysis = json.loads(result.analysis_path.read_text(encoding="utf-8").splitlines()[0])
    assert analysis["localizations"][0]["step_id"] == 1
