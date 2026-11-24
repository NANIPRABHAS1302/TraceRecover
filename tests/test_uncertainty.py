"""Tests for Phase 6 uncertainty signals and score composition."""

import json
from typing import Any

from agent.react_agent import ReactAgent
from config import LLMClient
from uncertainty.estimator import UncertaintyEstimator


def _step(
    step_id: int,
    output: Any,
    *,
    error: str | None = None,
    **metadata: Any,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "tool": "search",
        "input": {"query": "local test evidence"},
        "output": output,
        "error": error,
        **metadata,
    }


def _grounding(score: float | None) -> dict[str, Any]:
    return {"grounding_score": score}


def test_high_grounding_and_successful_tools_produce_low_uncertainty() -> None:
    report = UncertaintyEstimator().estimate(
        _grounding(1.0),
        [_step(1, "Paris"), _step(2, "Berlin")],
        recovery_report={"failures": []},
    )

    assert report["tool_success_ratio"] == 1.0
    assert report["recovery_reliability"] is None
    assert report["uncertainty_score"] == 0.0
    assert report["uncertainty_score"] is not None


def test_low_grounding_and_tool_success_raise_uncertainty() -> None:
    report = UncertaintyEstimator().estimate(
        _grounding(0.25),
        [
            _step(1, "Paris"),
            _step(2, None, error="TimeoutError: simulated"),
        ],
        recovery_report={
            "failures": [
                {
                    "attempt_count": 1,
                    "attempts": [],
                    "recovery_success": False,
                }
            ]
        },
    )

    assert report["tool_success_ratio"] == 0.5
    assert report["recovery_reliability"] == 0.0
    assert report["uncertainty_score"] > 0.5


def test_recovery_attempts_are_counted_and_success_is_not_over_penalized() -> None:
    attempt = {
        "record_type": "recovery_attempt",
        "original_step_id": 1,
        "attempt_number": 1,
        "recovery_success": True,
        "recovery_trace_step_id": 2,
        "recovery_execution_result": {
            "tool_name": "search",
            "input": {"query": "local test evidence"},
            "modified_input": {"query": "local test evidence"},
            "output": "Recovered evidence",
            "error": None,
            "execution_status": "success",
            "failure_injected": False,
            "detector_failure_detected": False,
        },
    }
    trace = [
        _step(1, None, error="TimeoutError: initial failure"),
        _step(
            2,
            "Recovered evidence",
            recovered=True,
            recovery_success=True,
            original_step_id=1,
        ),
    ]
    report = UncertaintyEstimator().estimate(
        _grounding(1.0),
        trace,
        recovery_report={
            "failures": [
                {
                    "attempt_count": 1,
                    "attempts": [attempt],
                    "recovery_success": True,
                }
            ]
        },
    )

    assert report["recovery_reliability"] == 1.0
    assert report["tool_execution_count"] == 2
    assert report["tool_success_count"] == 1
    assert report["tool_success_ratio"] == 0.5
    assert report["recovery_attempt_count"] == 1


def test_repeated_recovery_is_penalized_and_failed_recovery_is_zero() -> None:
    estimator = UncertaintyEstimator()
    repeated = estimator.estimate(
        _grounding(1.0),
        [_step(1, "Paris")],
        recovery_report={
            "failures": [
                {
                    "attempt_count": 3,
                    "attempts": [{}, {}, {}],
                    "recovery_success": True,
                }
            ]
        },
    )
    failed = estimator.estimate(
        _grounding(1.0),
        [_step(1, "Paris")],
        recovery_report={
            "failures": [
                {
                    "attempt_count": 2,
                    "attempts": [{}, {}],
                    "recovery_success": False,
                }
            ]
        },
    )

    assert repeated["recovery_reliability"] == 0.9
    assert failed["recovery_reliability"] == 0.0


def test_self_consistency_is_null_without_samples_and_scored_when_available() -> None:
    estimator = UncertaintyEstimator()
    unavailable = estimator.estimate(_grounding(1.0), [], recovery_report={})
    one_sample = estimator.estimate(
        _grounding(1.0),
        [],
        recovery_report={},
        self_consistency_samples=["Paris is the capital."],
    )
    agreement = estimator.estimate(
        _grounding(1.0),
        [],
        recovery_report={},
        self_consistency_samples=["Paris is the capital.", "PARIS is the capital!"],
    )
    disagreement = estimator.estimate(
        _grounding(1.0),
        [],
        recovery_report={},
        self_consistency_samples=["Paris", "Rome"],
    )

    assert unavailable["self_consistency_score"] is None
    assert unavailable["available_signals"]["self_consistency"] is False
    assert one_sample["self_consistency_score"] is None
    assert one_sample["self_consistency_sample_count"] == 1
    assert agreement["self_consistency_score"] == 1.0
    assert disagreement["self_consistency_score"] == 0.0


def test_weights_are_configurable_and_uncertainty_stays_normalized() -> None:
    estimator = UncertaintyEstimator(
        weights={
            "grounding": 1.0,
            "tool_success": 0.0,
            "recovery_reliability": 0.0,
            "self_consistency": 0.0,
        }
    )
    report = estimator.estimate(
        _grounding(0.6),
        [_step(1, "Paris")],
        recovery_report={},
    )

    assert report["signal_weights"]["grounding"] == 1.0
    assert report["combined_reliability"] == 0.6
    assert report["uncertainty_score"] == 0.4
    assert 0.0 <= report["uncertainty_score"] <= 1.0


def test_uncertainty_is_null_when_every_signal_is_unavailable() -> None:
    report = UncertaintyEstimator().estimate(
        _grounding(None),
        [],
        recovery_report=None,
    )

    assert report["combined_reliability"] is None
    assert report["uncertainty_score"] is None


def test_agent_persists_uncertainty_and_returns_cited_answer(tmp_path) -> None:
    result = ReactAgent(
        client=LLMClient(provider="local"),
        log_directory=tmp_path,
    ).run("What is the capital of France?")

    saved_report = json.loads(
        result.uncertainty_report_path.read_text(encoding="utf-8")
    )
    assert result.final_decision == "ANSWER"
    assert "evidence: step 1" in result.final_response
    assert saved_report["original_answer"] == result.answer
    assert saved_report["self_consistency_score"] is None
    assert saved_report["uncertainty_score"] == 0.0
