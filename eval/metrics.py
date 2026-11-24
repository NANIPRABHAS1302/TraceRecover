"""Reproducible, denominator-aware evaluation metrics for agent runs."""

from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence


def task_success_rate(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return task success with a Wilson 95% interval and explicit sample size."""
    completed = [result for result in results if isinstance(result.get("success"), bool)]
    successes = sum(result["success"] for result in completed)
    rate = successes / len(completed) if completed else None
    return {
        "rate": rate,
        "successes": successes,
        "denominator": len(completed),
        "confidence_interval_95": wilson_interval(successes, len(completed)),
    }


def recovery_rate(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return recovered incidents over failures detected by the evaluation oracle."""
    detected = sum(_nonnegative_int(result.get("detected_failures")) for result in results)
    recovered = sum(
        _nonnegative_int(result.get("successful_recoveries")) for result in results
    )
    return {
        "rate": recovered / detected if detected else None,
        "successful_recoveries": recovered,
        "detected_failures": detected,
        "denominator": detected,
        "detected_injected_failures": sum(
            _nonnegative_int(result.get("detected_injected_failures"))
            for result in results
        ),
        "detected_natural_failures": sum(
            _nonnegative_int(result.get("detected_natural_failures"))
            for result in results
        ),
        "injected_tool_failures": sum(
            _nonnegative_int(result.get("injected_tool_failures"))
            for result in results
        ),
        "injected_recovery_failures": sum(
            _nonnegative_int(result.get("injected_recovery_failures"))
            for result in results
        ),
    }


def hallucination_rate(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Measure answers with unsupported/contradicted claims against task gold facts."""
    with_claims = [
        result
        for result in results
        if _nonnegative_int(result.get("factual_claim_count")) > 0
    ]
    hallucinating = sum(result.get("hallucination") is True for result in with_claims)
    unsupported = sum(
        _nonnegative_int(result.get("unsupported_claim_count"))
        for result in results
    )
    contradicted = sum(
        _nonnegative_int(result.get("contradicted_claim_count"))
        for result in results
    )
    return {
        "rate": hallucinating / len(with_claims) if with_claims else None,
        "hallucinating_answers": hallucinating,
        "answers_with_factual_claims": len(with_claims),
        "denominator": len(with_claims),
        "unsupported_claims": unsupported,
        "contradicted_claims": contradicted,
    }


def unsupported_claim_rate(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return claim-level unsupported and ungrounded claim ratios."""
    total_claims = sum(
        _nonnegative_int(result.get("factual_claim_count")) for result in results
    )
    unsupported = sum(
        _nonnegative_int(result.get("unsupported_claim_count"))
        for result in results
    )
    contradicted = sum(
        _nonnegative_int(result.get("contradicted_claim_count"))
        for result in results
    )
    return {
        "rate": unsupported / total_claims if total_claims else None,
        "ungrounded_rate": (
            (unsupported + contradicted) / total_claims if total_claims else None
        ),
        "unsupported_claims": unsupported,
        "contradicted_claims": contradicted,
        "total_claims": total_claims,
        "denominator": total_claims,
    }


def evidence_coverage(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate per-task grounding scores into condition-level evidence coverage."""
    values = [
        float(score)
        for result in results
        if (score := _valid_probability(result.get("grounding_score"))) is not None
    ]
    fully_grounded = sum(score == 1.0 for score in values)
    average_coverage = statistics.mean(values) if values else None
    return {
        "rate": average_coverage,
        "average_coverage": average_coverage,
        "median_coverage": statistics.median(values) if values else None,
        "fully_grounded_tasks": fully_grounded,
        "full_coverage_rate": fully_grounded / len(values) if values else None,
        "sample_count": len(values),
        "denominator": len(values),
    }


def cost_metrics(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate prompt/completion token counts and estimated USD API cost."""
    prompt_values: list[int] = []
    completion_values: list[int] = []
    total_token_values: list[int] = []
    cost_values: list[float] = []

    for result in results:
        prompt = _optional_nonnegative_int(result.get("prompt_tokens"))
        completion = _optional_nonnegative_int(result.get("completion_tokens"))
        total = _optional_nonnegative_int(result.get("total_tokens"))
        if total is None and (prompt is not None or completion is not None):
            total = (prompt or 0) + (completion or 0)
        cost = _valid_nonnegative_float(result.get("estimated_cost_usd"))

        if prompt is not None:
            prompt_values.append(prompt)
        if completion is not None:
            completion_values.append(completion)
        if total is not None:
            total_token_values.append(total)
        if cost is not None:
            cost_values.append(cost)

    sample_count = max(len(total_token_values), len(cost_values))
    return {
        "total_prompt_tokens": sum(prompt_values) if prompt_values else 0,
        "total_completion_tokens": sum(completion_values) if completion_values else 0,
        "total_tokens": sum(total_token_values) if total_token_values else 0,
        "average_tokens": (
            statistics.mean(total_token_values) if total_token_values else None
        ),
        "total_cost_usd": sum(cost_values) if cost_values else 0.0,
        "average_cost_usd": statistics.mean(cost_values) if cost_values else None,
        "sample_count": sample_count,
    }


def expected_calibration_error(
    results: Sequence[Mapping[str, Any]], bins: int = 10
) -> dict[str, Any]:
    """Calculate ECE with equal-width bins on [0, 1], the last including 1.

    Confidence is the observed Phase 6 heuristic ``1 - uncertainty``. ECE is an
    empirical diagnostic, not evidence that the underlying score is calibrated.
    """
    if bins < 1:
        raise ValueError("bins must be at least one.")
    samples = [
        (float(confidence), bool(result["success"]))
        for result in results
        if (confidence := _valid_probability(result.get("confidence"))) is not None
        and isinstance(result.get("success"), bool)
    ]
    if not samples:
        return {
            "ece": None,
            "sample_count": 0,
            "bin_count": bins,
            "binning": "equal-width [0,1], last bin includes 1",
            "bins": [],
        }

    bucketed: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for sample in samples:
        bucket_index = min(int(sample[0] * bins), bins - 1)
        bucketed[bucket_index].append(sample)

    details: list[dict[str, Any]] = []
    ece = 0.0
    for index, bucket in enumerate(bucketed):
        if not bucket:
            continue
        average_confidence = sum(item[0] for item in bucket) / len(bucket)
        accuracy = sum(item[1] for item in bucket) / len(bucket)
        ece += len(bucket) / len(samples) * abs(accuracy - average_confidence)
        details.append(
            {
                "bin": index,
                "lower_bound": index / bins,
                "upper_bound": (index + 1) / bins,
                "sample_count": len(bucket),
                "average_confidence": average_confidence,
                "accuracy": accuracy,
            }
        )
    return {
        "ece": ece,
        "sample_count": len(samples),
        "bin_count": bins,
        "binning": "equal-width [0,1], last bin includes 1",
        "bins": details,
    }


def abstention_accuracy(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return correct, incorrect, and missed abstentions plus precision/recall."""
    correct = 0
    incorrect = 0
    missed = 0
    for result in results:
        expected = result.get("should_abstain")
        if not isinstance(expected, bool):
            continue
        predicted = result.get("final_decision") == "ABSTAIN"
        if expected and predicted:
            correct += 1
        elif not expected and predicted:
            incorrect += 1
        elif expected and not predicted:
            missed += 1
    return {
        "correct_abstentions": correct,
        "incorrect_abstentions": incorrect,
        "missed_abstentions": missed,
        "abstention_precision": (
            correct / (correct + incorrect) if correct + incorrect else None
        ),
        "abstention_recall": (
            correct / (correct + missed) if correct + missed else None
        ),
        "ground_truth_abstention_tasks": correct + missed,
        "predicted_abstentions": correct + incorrect,
    }


def latency_metrics(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return mean and median measured task latencies in seconds."""
    values = [
        float(value)
        for result in results
        if (value := _valid_nonnegative_float(result.get("latency_seconds")))
        is not None
    ]
    return {
        "average_seconds": statistics.mean(values) if values else None,
        "median_seconds": statistics.median(values) if values else None,
        "sample_count": len(values),
    }


def summarize_metrics(
    results: Sequence[Mapping[str, Any]], ece_bins: int = 10
) -> dict[str, Any]:
    """Calculate the evaluation metric bundle for one system and condition."""
    return {
        "task_count": len(results),
        "task_success": task_success_rate(results),
        "recovery": recovery_rate(results),
        "hallucination": hallucination_rate(results),
        "unsupported_claim_rate": unsupported_claim_rate(results),
        "evidence_coverage": evidence_coverage(results),
        "ece": expected_calibration_error(results, bins=ece_bins),
        "abstention": abstention_accuracy(results),
        "latency": latency_metrics(results),
        "cost": cost_metrics(results),
    }


def wilson_interval(
    successes: int, total: int, z_score: float = 1.96
) -> dict[str, float] | None:
    """Return a two-sided Wilson score interval for a binomial proportion."""
    if total <= 0:
        return None
    proportion = successes / total
    z_squared = z_score * z_score
    denominator = 1 + z_squared / total
    center = (proportion + z_squared / (2 * total)) / denominator
    margin = (
        z_score
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z_squared / (4 * total * total)
        )
        / denominator
    )
    return {
        "lower": max(0.0, center - margin),
        "upper": min(1.0, center + margin),
    }


def _valid_probability(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        return None
    return float(value)


def _valid_nonnegative_float(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return float(value)


def _nonnegative_int(value: Any) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
