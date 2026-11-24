"""Unit tests for validation-set threshold calibration."""

from __future__ import annotations

import pytest

from uncertainty.calibrator import (
    DEFAULT_ABSTAIN_THRESHOLD,
    DEFAULT_ANSWER_THRESHOLD,
    calibrate_thresholds,
)


def test_calibrate_thresholds_optimizes_for_target_abstain_precision() -> None:
    validation_records = [
        # Clearly answerable (low uncertainty)
        {"uncertainty_score": 0.00, "should_abstain": False, "success": True},
        {"uncertainty_score": 0.05, "should_abstain": False, "success": True},
        {"uncertainty_score": 0.10, "should_abstain": False, "success": True},
        {"uncertainty_score": 0.15, "should_abstain": False, "success": True},
        {"uncertainty_score": 0.20, "should_abstain": False, "success": True},
        # Ambiguous / warning zone
        {"uncertainty_score": 0.35, "should_abstain": False, "success": True},
        {"uncertainty_score": 0.45, "should_abstain": False, "success": False},
        # High uncertainty abstention cases
        {"uncertainty_score": 0.65, "should_abstain": True, "success": True},
        {"uncertainty_score": 0.75, "should_abstain": True, "success": True},
        {"uncertainty_score": 0.90, "should_abstain": True, "success": True},
        {"uncertainty_score": 1.00, "should_abstain": True, "success": True},
    ]

    result = calibrate_thresholds(
        validation_records, target_abstain_precision=0.85
    )

    assert result.calibrated is True
    assert result.target_precision_met is True
    assert result.validation_sample_count == len(validation_records)
    assert 0.0 <= result.answer_threshold < result.abstain_threshold <= 1.0
    assert result.achieved_abstain_precision >= 0.85
    assert result.achieved_abstain_recall == pytest.approx(1.0)
    assert result.achieved_answer_precision == pytest.approx(1.0)
    assert result.abstain_threshold >= 0.50
    serialized = result.to_dict()
    assert serialized["calibrated"] is True
    assert len(serialized["candidate_evaluations"]) > 0


def test_calibrate_thresholds_handles_empty_validation_set() -> None:
    result = calibrate_thresholds([])

    assert result.calibrated is False
    assert result.target_precision_met is False
    assert result.answer_threshold == DEFAULT_ANSWER_THRESHOLD
    assert result.abstain_threshold == DEFAULT_ABSTAIN_THRESHOLD
    assert result.validation_sample_count == 0


def test_calibrate_thresholds_respects_hard_contradiction_gates_and_validates_args() -> None:
    records = [
        {"uncertainty_score": 0.10, "should_abstain": False, "success": True},
        {
            "uncertainty_score": 0.25,
            "should_abstain": True,
            "contradicted_claim_count": 1,
            "success": True,
        },
        {"uncertainty_score": 0.80, "should_abstain": True, "success": True},
    ]
    result = calibrate_thresholds(records, target_abstain_precision=0.90)
    assert result.achieved_abstain_recall == pytest.approx(1.0)
    assert result.achieved_abstain_precision == pytest.approx(1.0)

    with pytest.raises(ValueError, match="target_abstain_precision"):
        calibrate_thresholds(records, target_abstain_precision=0.0)
    with pytest.raises(ValueError, match="grid_steps"):
        calibrate_thresholds(records, grid_steps=1)
