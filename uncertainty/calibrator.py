"""Empirical validation-set calibration for uncertainty decision thresholds."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


DEFAULT_ANSWER_THRESHOLD = 0.15
DEFAULT_ABSTAIN_THRESHOLD = 0.55


@dataclass(frozen=True)
class CalibrationResult:
    """Calibrated thresholds and empirical validation diagnostics."""

    answer_threshold: float
    abstain_threshold: float
    target_abstain_precision: float
    achieved_abstain_precision: float | None
    achieved_abstain_recall: float | None
    achieved_abstain_f1: float | None
    achieved_answer_precision: float | None
    achieved_answer_coverage: float | None
    validation_sample_count: int
    calibrated: bool
    target_precision_met: bool
    candidate_evaluations: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """Serialize calibration outputs for experiment metadata and reports."""
        return {
            "answer_threshold": self.answer_threshold,
            "abstain_threshold": self.abstain_threshold,
            "target_abstain_precision": self.target_abstain_precision,
            "achieved_abstain_precision": self.achieved_abstain_precision,
            "achieved_abstain_recall": self.achieved_abstain_recall,
            "achieved_abstain_f1": self.achieved_abstain_f1,
            "achieved_answer_precision": self.achieved_answer_precision,
            "achieved_answer_coverage": self.achieved_answer_coverage,
            "validation_sample_count": self.validation_sample_count,
            "calibrated": self.calibrated,
            "target_precision_met": self.target_precision_met,
            "candidate_evaluations": list(self.candidate_evaluations),
        }


def calibrate_thresholds(
    validation_records: Sequence[Mapping[str, Any]],
    target_abstain_precision: float = 0.85,
    *,
    grid_steps: int = 19,
    min_threshold_gap: float = 0.05,
) -> CalibrationResult:
    """Empirically tune answer_threshold and abstain_threshold on validation records.

    Each validation record is expected to contain:
    - ``should_abstain`` (bool): whether abstaining is the desired decision.
    - ``uncertainty_score`` (float in [0, 1] or None): heuristic uncertainty.
    - Optional ``success`` (bool): task correctness if answered.
    - Optional ``contradicted_claim_count`` (int) / ``supported_claim_count`` (int)
      for reflecting hard grounding abstention gates during threshold evaluation.
    """
    if (
        isinstance(target_abstain_precision, bool)
        or not isinstance(target_abstain_precision, (int, float))
        or not math.isfinite(target_abstain_precision)
        or not 0.0 < target_abstain_precision <= 1.0
    ):
        raise ValueError("target_abstain_precision must be in (0, 1].")
    if grid_steps < 2:
        raise ValueError("grid_steps must be at least 2.")
    if not 0.0 < min_threshold_gap < 0.5:
        raise ValueError("min_threshold_gap must be in (0, 0.5).")

    usable = [
        record
        for record in validation_records
        if isinstance(record.get("should_abstain"), bool)
    ]
    if not usable:
        return CalibrationResult(
            answer_threshold=DEFAULT_ANSWER_THRESHOLD,
            abstain_threshold=DEFAULT_ABSTAIN_THRESHOLD,
            target_abstain_precision=float(target_abstain_precision),
            achieved_abstain_precision=None,
            achieved_abstain_recall=None,
            achieved_abstain_f1=None,
            achieved_answer_precision=None,
            achieved_answer_coverage=None,
            validation_sample_count=0,
            calibrated=False,
            target_precision_met=False,
            candidate_evaluations=[],
        )

    observed_scores = {
        round(float(score), 4)
        for record in usable
        if (score := _valid_score(record.get("uncertainty_score"))) is not None
    }
    base_grid = {
        round(0.05 + idx * (0.90 / (grid_steps - 1)), 4)
        for idx in range(grid_steps)
    }
    abstain_candidates = sorted(
        {t for t in (base_grid | observed_scores | {DEFAULT_ABSTAIN_THRESHOLD}) if min_threshold_gap < t <= 1.0}
    )

    candidate_evals: list[dict[str, Any]] = []
    for t_abs in abstain_candidates:
        metrics = _evaluate_abstain_threshold(usable, t_abs)
        candidate_evals.append({"abstain_threshold": t_abs, **metrics})

    meeting_target = [
        item
        for item in candidate_evals
        if item["precision"] is not None
        and item["precision"] >= target_abstain_precision
    ]
    if meeting_target:
        target_met = True
        best_abs = max(
            meeting_target,
            key=lambda item: (
                item["recall"] if item["recall"] is not None else -1.0,
                item["f1"] if item["f1"] is not None else -1.0,
                item["precision"] if item["precision"] is not None else -1.0,
                -abs(item["abstain_threshold"] - DEFAULT_ABSTAIN_THRESHOLD),
            ),
        )
    else:
        target_met = False
        best_abs = max(
            candidate_evals,
            key=lambda item: (
                item["f1"] if item["f1"] is not None else -1.0,
                item["precision"] if item["precision"] is not None else -1.0,
                item["recall"] if item["recall"] is not None else -1.0,
                -abs(item["abstain_threshold"] - DEFAULT_ABSTAIN_THRESHOLD),
            ),
        )

    chosen_abstain = float(best_abs["abstain_threshold"])

    # Calibrate answer_threshold strictly below chosen_abstain
    answer_candidates = sorted(
        {
            t
            for t in (base_grid | observed_scores | {0.0, DEFAULT_ANSWER_THRESHOLD})
            if 0.0 <= t <= round(chosen_abstain - min_threshold_gap, 4)
        }
    )
    if not answer_candidates:
        answer_candidates = [max(0.0, round(chosen_abstain - min_threshold_gap, 4))]

    best_ans = max(
        (
            {"answer_threshold": t_ans, **_evaluate_answer_threshold(usable, t_ans)}
            for t_ans in answer_candidates
        ),
        key=lambda item: (
            item["utility"],
            item["precision"] if item["precision"] is not None else -1.0,
            item["coverage"] if item["coverage"] is not None else -1.0,
            -abs(item["answer_threshold"] - DEFAULT_ANSWER_THRESHOLD),
        ),
    )

    return CalibrationResult(
        answer_threshold=float(best_ans["answer_threshold"]),
        abstain_threshold=chosen_abstain,
        target_abstain_precision=float(target_abstain_precision),
        achieved_abstain_precision=best_abs["precision"],
        achieved_abstain_recall=best_abs["recall"],
        achieved_abstain_f1=best_abs["f1"],
        achieved_answer_precision=best_ans["precision"],
        achieved_answer_coverage=best_ans["coverage"],
        validation_sample_count=len(usable),
        calibrated=True,
        target_precision_met=target_met,
        candidate_evaluations=candidate_evals,
    )


def _predict_abstain(record: Mapping[str, Any], abstain_threshold: float) -> bool:
    if _hard_grounding_abstain(record):
        return True
    score = _valid_score(record.get("uncertainty_score"))
    if score is None:
        return True
    return score >= abstain_threshold


def _hard_grounding_abstain(record: Mapping[str, Any]) -> bool:
    contradicted = record.get("contradicted_claim_count")
    if isinstance(contradicted, int) and not isinstance(contradicted, bool) and contradicted > 0:
        return True
    grounding_score = _valid_score(record.get("grounding_score"))
    if grounding_score is not None and grounding_score == 0.0:
        return True
    return False


def _evaluate_abstain_threshold(
    records: Sequence[Mapping[str, Any]], threshold: float
) -> dict[str, Any]:
    tp = fp = fn = 0
    for record in records:
        expected = bool(record["should_abstain"])
        predicted = _predict_abstain(record, threshold)
        if expected and predicted:
            tp += 1
        elif not expected and predicted:
            fp += 1
        elif expected and not predicted:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        (2 * precision * recall / (precision + recall))
        if (precision is not None and recall is not None and (precision + recall) > 0)
        else (0.0 if (precision is not None or recall is not None) else None)
    )
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _evaluate_answer_threshold(
    records: Sequence[Mapping[str, Any]], threshold: float
) -> dict[str, Any]:
    answered_correct = 0
    answered_total = 0
    answerable_total = 0
    for record in records:
        expected_abstain = bool(record["should_abstain"])
        is_answerable = not expected_abstain
        if is_answerable:
            answerable_total += 1
        if _hard_grounding_abstain(record):
            continue
        score = _valid_score(record.get("uncertainty_score"))
        if score is None or score > threshold:
            continue
        answered_total += 1
        if is_answerable and record.get("success", True) is not False:
            answered_correct += 1
    precision = answered_correct / answered_total if answered_total else None
    coverage = answered_correct / answerable_total if answerable_total else None
    if precision is not None and coverage is not None and (precision + coverage) > 0:
        # Weight precision higher than coverage (F0.5) to protect clean ANSWER decisions.
        beta_sq = 0.25
        utility = (1 + beta_sq) * precision * coverage / (beta_sq * precision + coverage)
    else:
        utility = 0.0
    return {
        "answered_total": answered_total,
        "answered_correct": answered_correct,
        "precision": precision,
        "coverage": coverage,
        "utility": utility,
    }


def _valid_score(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        return None
    return float(value)
