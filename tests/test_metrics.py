"""Unit tests for evaluation metric formulas and undefined denominators."""

import pytest

from eval.metrics import (
    abstention_accuracy,
    cost_metrics,
    evidence_coverage,
    expected_calibration_error,
    hallucination_rate,
    latency_metrics,
    recovery_rate,
    summarize_metrics,
    task_success_rate,
    unsupported_claim_rate,
)


def test_task_success_rate_reports_sample_size_and_interval() -> None:
    result = task_success_rate(
        [{"success": True}, {"success": False}, {"success": True}]
    )

    assert result["rate"] == pytest.approx(2 / 3)
    assert result["successes"] == 2
    assert result["denominator"] == 3
    assert result["confidence_interval_95"]["lower"] < result["rate"]
    assert result["confidence_interval_95"]["upper"] > result["rate"]


def test_zero_denominators_are_unavailable_not_zero() -> None:
    assert task_success_rate([])["rate"] is None
    assert recovery_rate([{"detected_failures": 0}])["rate"] is None
    assert hallucination_rate([{"factual_claim_count": 0}])["rate"] is None
    assert expected_calibration_error([{"success": True}])["ece"] is None
    assert latency_metrics([{}])["average_seconds"] is None


def test_recovery_rate_uses_only_detected_failure_denominator() -> None:
    result = recovery_rate(
        [
            {
                "detected_failures": 2,
                "successful_recoveries": 1,
                "detected_injected_failures": 1,
                "detected_natural_failures": 1,
                "injected_tool_failures": 2,
                "injected_recovery_failures": 1,
            },
            {
                "detected_failures": 1,
                "successful_recoveries": 1,
                "detected_injected_failures": 1,
                "detected_natural_failures": 0,
                "injected_tool_failures": 1,
                "injected_recovery_failures": 0,
            },
        ]
    )

    assert result["rate"] == pytest.approx(2 / 3)
    assert result["successful_recoveries"] == 2
    assert result["detected_failures"] == 3
    assert result["detected_injected_failures"] == 2
    assert result["detected_natural_failures"] == 1
    assert result["injected_tool_failures"] == 3
    assert result["injected_recovery_failures"] == 1


def test_hallucination_rate_separates_unsupported_and_contradicted_claims() -> None:
    result = hallucination_rate(
        [
            {
                "factual_claim_count": 2,
                "hallucination": True,
                "unsupported_claim_count": 1,
                "contradicted_claim_count": 1,
            },
            {
                "factual_claim_count": 1,
                "hallucination": False,
                "unsupported_claim_count": 0,
                "contradicted_claim_count": 0,
            },
            {
                "factual_claim_count": 0,
                "hallucination": False,
                "unsupported_claim_count": 0,
                "contradicted_claim_count": 0,
            },
        ]
    )

    assert result["rate"] == pytest.approx(0.5)
    assert result["answers_with_factual_claims"] == 2
    assert result["unsupported_claims"] == 1
    assert result["contradicted_claims"] == 1


def test_ece_uses_equal_width_bins_and_success_as_observed_accuracy() -> None:
    result = expected_calibration_error(
        [
            {"confidence": 0.8, "success": True},
            {"confidence": 0.2, "success": False},
            {"confidence": 1.0, "success": True},
            {"confidence": None, "success": True},
        ],
        bins=2,
    )

    assert result["ece"] == pytest.approx(2 / 15)
    assert result["sample_count"] == 3
    assert result["bin_count"] == 2
    assert result["binning"] == "equal-width [0,1], last bin includes 1"
    assert sum(item["sample_count"] for item in result["bins"]) == 3


def test_abstention_accuracy_reports_precision_recall_and_error_types() -> None:
    result = abstention_accuracy(
        [
            {"should_abstain": True, "final_decision": "ABSTAIN"},
            {"should_abstain": False, "final_decision": "ABSTAIN"},
            {"should_abstain": True, "final_decision": "WARN"},
            {"should_abstain": False, "final_decision": "ANSWER"},
        ]
    )

    assert result["correct_abstentions"] == 1
    assert result["incorrect_abstentions"] == 1
    assert result["missed_abstentions"] == 1
    assert result["abstention_precision"] == pytest.approx(0.5)
    assert result["abstention_recall"] == pytest.approx(0.5)


def test_latency_reports_mean_median_and_usable_sample_count() -> None:
    result = latency_metrics(
        [
            {"latency_seconds": 1.0},
            {"latency_seconds": 2.0},
            {"latency_seconds": 9.0},
            {"latency_seconds": -1},
            {"latency_seconds": "unknown"},
        ]
    )

    assert result == {
        "average_seconds": 4.0,
        "median_seconds": 2.0,
        "sample_count": 3,
    }


def test_metric_summary_keeps_unavailable_values_null() -> None:
    result = summarize_metrics([])

    assert result["task_count"] == 0
    assert result["task_success"]["rate"] is None
    assert result["recovery"]["rate"] is None
    assert result["hallucination"]["rate"] is None
    assert result["unsupported_claim_rate"]["rate"] is None
    assert result["evidence_coverage"]["rate"] is None
    assert result["ece"]["ece"] is None
    assert result["latency"]["median_seconds"] is None
    assert result["cost"]["average_cost_usd"] is None


def test_unsupported_claim_rate_evidence_coverage_and_cost_metrics() -> None:
    records = [
        {
            "factual_claim_count": 2,
            "unsupported_claim_count": 1,
            "contradicted_claim_count": 0,
            "grounding_score": 0.5,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "estimated_cost_usd": 0.001,
        },
        {
            "factual_claim_count": 2,
            "unsupported_claim_count": 0,
            "contradicted_claim_count": 1,
            "grounding_score": 1.0,
            "prompt_tokens": 200,
            "completion_tokens": 50,
            "estimated_cost_usd": 0.002,
        },
    ]

    ucr = unsupported_claim_rate(records)
    assert ucr["rate"] == pytest.approx(0.25)
    assert ucr["ungrounded_rate"] == pytest.approx(0.5)
    assert ucr["total_claims"] == 4

    cov = evidence_coverage(records)
    assert cov["rate"] == pytest.approx(0.75)
    assert cov["fully_grounded_tasks"] == 1
    assert cov["full_coverage_rate"] == pytest.approx(0.5)

    cost = cost_metrics(records)
    assert cost["total_prompt_tokens"] == 300
    assert cost["total_completion_tokens"] == 100
    assert cost["total_tokens"] == 400
    assert cost["average_tokens"] == pytest.approx(200.0)
    assert cost["total_cost_usd"] == pytest.approx(0.003)
    assert cost["average_cost_usd"] == pytest.approx(0.0015)
