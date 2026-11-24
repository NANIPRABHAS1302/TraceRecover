"""Trace-step localization and structured failure-report generation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .detector import DetectionResult, FailureDetector


class FailureLocalizer:
    """Map detected failures back to their exact tool trace steps."""

    def __init__(self, detector: FailureDetector | None = None) -> None:
        self.detector = detector or FailureDetector()

    def localize(
        self,
        trace_entries: Sequence[Mapping[str, Any]],
        detections: Sequence[DetectionResult] | None = None,
    ) -> list[dict[str, Any]]:
        """Return one location record for every detected failure."""
        predictions = (
            list(detections)
            if detections is not None
            else self.detector.detect_trace(trace_entries)
        )
        entries_by_id = {
            entry.get("step_id"): entry
            for entry in trace_entries
            if entry.get("step_id") is not None
        }
        localized: list[dict[str, Any]] = []
        for detection in predictions:
            if not detection.failure_detected:
                continue
            entry = entries_by_id.get(detection.step_id)
            if entry is None:
                continue
            localized.append(
                self._localize_one(entry, detection)
            )
        return localized

    def build_report(
        self,
        trace_entries: Sequence[Mapping[str, Any]],
        detections: Sequence[DetectionResult] | None = None,
        localized_failures: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a report containing all predictions and localized failures."""
        predictions = (
            list(detections)
            if detections is not None
            else self.detector.detect_trace(trace_entries)
        )
        failures = (
            list(localized_failures)
            if localized_failures is not None
            else self.localize(trace_entries, predictions)
        )
        return {
            "failure_detected": bool(failures),
            "total_failures": len(failures),
            "failures": failures,
            "step_detections": [prediction.to_dict() for prediction in predictions],
        }

    @staticmethod
    def _localize_one(
        entry: Mapping[str, Any], detection: DetectionResult
    ) -> dict[str, Any]:
        """Combine a detector prediction with its source trace record."""
        return {
            "step_id": detection.step_id,
            "tool_name": detection.tool_name,
            "failure_detected": detection.failure_detected,
            "failure_category": detection.failure_category.value,
            "severity": detection.severity.value,
            "original_input": entry.get("original_input", entry.get("input")),
            "modified_input": entry.get("modified_input"),
            "actual_output": entry.get("output"),
            "error_message": entry.get("error"),
            "localization_explanation": (
                f"Failure localized to trace step {detection.step_id} "
                f"for tool {detection.tool_name!r}: {detection.explanation}"
            ),
            "evidence": detection.evidence,
            "confidence": detection.confidence,
            "failure_origin": detection.failure_origin,
            "reported_failure_injected": detection.reported_failure_injected,
            "reported_failure_type": detection.reported_failure_type,
        }