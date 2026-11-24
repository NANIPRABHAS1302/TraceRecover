"""Combine grounding, tool, recovery, and optional consistency signals."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from healing.detector import FailureDetector


DEFAULT_SIGNAL_WEIGHTS = {
    "grounding": 0.45,
    "tool_success": 0.25,
    "recovery_reliability": 0.15,
    "self_consistency": 0.15,
}
_FAILED_STATUSES = {"error", "failed", "injected_failure", "timeout"}


class UncertaintyEstimator:
    """Estimate answer uncertainty from observed execution and grounding data.

    Available signals are combined as a weighted mean using only their
    configured weights. Missing signals are omitted and the remaining weights
    are renormalized; missing values are never replaced with guessed scores.
    """

    def __init__(
        self,
        weights: Mapping[str, float] | None = None,
        detector: FailureDetector | None = None,
        repeated_attempt_penalty: float = 0.05,
    ) -> None:
        if (
            isinstance(repeated_attempt_penalty, bool)
            or not isinstance(repeated_attempt_penalty, (int, float))
            or not math.isfinite(repeated_attempt_penalty)
            or not 0 <= repeated_attempt_penalty <= 1
        ):
            raise ValueError("repeated_attempt_penalty must be between 0 and 1.")
        configured = dict(DEFAULT_SIGNAL_WEIGHTS)
        if weights is not None:
            unknown = set(weights) - set(DEFAULT_SIGNAL_WEIGHTS)
            if unknown:
                raise ValueError(f"Unknown uncertainty signals: {sorted(unknown)}")
            configured.update(weights)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in configured.values()
        ):
            raise ValueError("Signal weights must be finite, non-negative numbers.")
        if sum(configured.values()) <= 0:
            raise ValueError("At least one uncertainty signal weight must be positive.")
        self.weights = {name: float(value) for name, value in configured.items()}
        self.detector = detector or FailureDetector()
        self.repeated_attempt_penalty = repeated_attempt_penalty

    def estimate(
        self,
        grounding_report: Mapping[str, Any],
        trace: Sequence[Mapping[str, Any]],
        *,
        recovery_report: Mapping[str, Any] | None = None,
        recovery_records: Sequence[Mapping[str, Any]] = (),
        self_consistency_samples: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Return individual reliability signals and a normalized uncertainty."""
        grounding_score = self._score(grounding_report.get("grounding_score"))
        (
            tool_success_ratio,
            tool_success_count,
            tool_execution_count,
            linked_recovery_steps,
        ) = self._tool_success(trace, recovery_report, recovery_records)
        recovery = self._recovery_reliability(recovery_report, recovery_records)
        consistency_score, consistency_count = self._self_consistency(
            self_consistency_samples
        )

        signal_values: dict[str, float | None] = {
            "grounding": grounding_score,
            "tool_success": tool_success_ratio,
            "recovery_reliability": recovery["recovery_reliability"],
            "self_consistency": consistency_score,
        }
        available_weights = {
            name: self.weights[name]
            for name, value in signal_values.items()
            if value is not None and self.weights[name] > 0
        }
        total_weight = sum(available_weights.values())
        normalized_weights = (
            {name: weight / total_weight for name, weight in available_weights.items()}
            if total_weight
            else {}
        )
        combined_reliability = (
            sum(
                float(signal_values[name]) * weight
                for name, weight in normalized_weights.items()
            )
            if normalized_weights
            else None
        )
        uncertainty_score = (
            1.0 - combined_reliability
            if combined_reliability is not None
            else None
        )

        return {
            "grounding_score": grounding_score,
            "supported_claim_count": self._nonnegative_int(
                grounding_report.get("supported_count"), 0
            ),
            "unsupported_claim_count": self._nonnegative_int(
                grounding_report.get("unsupported_count"), 0
            ),
            "contradicted_claim_count": self._nonnegative_int(
                grounding_report.get("contradicted_count"), 0
            ),
            "total_claim_count": self._nonnegative_int(
                grounding_report.get("total_claim_count"), 0
            ),
            "tool_success_ratio": tool_success_ratio,
            "tool_success_count": tool_success_count,
            "tool_execution_count": tool_execution_count,
            "recovery_reliability": recovery["recovery_reliability"],
            "recovery_attempt_count": recovery["recovery_attempt_count"],
            "recovery_incident_count": recovery["recovery_incident_count"],
            "recovery_success_count": recovery["recovery_success_count"],
            "recovery_failure_count": recovery["recovery_failure_count"],
            "recovery_trace_step_ids": [
                int(step_id) if step_id.isdecimal() else step_id
                for step_id in sorted(linked_recovery_steps)
            ],
            "self_consistency_score": consistency_score,
            "self_consistency_sample_count": consistency_count,
            "signal_values": signal_values,
            "signal_weights": dict(self.weights),
            "normalized_signal_weights": normalized_weights,
            "available_signals": {
                name: value is not None for name, value in signal_values.items()
            },
            "combined_reliability": combined_reliability,
            "uncertainty_score": uncertainty_score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _tool_success(
        self,
        trace: Sequence[Mapping[str, Any]],
        recovery_report: Mapping[str, Any] | None,
        recovery_records: Sequence[Mapping[str, Any]],
    ) -> tuple[float | None, int, int, set[str]]:
        linked_steps = self._linked_recovery_steps(recovery_report, recovery_records)
        successes = 0
        executions = 0
        for step in trace:
            tool = str(step.get("tool_name") or step.get("tool") or "")
            if not tool or tool in {"final", "llm"}:
                continue
            if self._id_key(step.get("step_id")) in linked_steps:
                continue
            executions += 1
            if self._execution_succeeded(step, tool):
                successes += 1

        attempt_records = self._recovery_attempt_records(
            recovery_report, recovery_records
        )
        for record in attempt_records:
            execution = record.get("recovery_execution_result")
            if not isinstance(execution, Mapping):
                continue
            tool = str(execution.get("tool_name") or record.get("recovery_tool") or "")
            if not tool:
                continue
            executions += 1
            if self._execution_succeeded(execution, tool):
                successes += 1

        ratio = successes / executions if executions else None
        return ratio, successes, executions, linked_steps

    def _execution_succeeded(self, execution: Mapping[str, Any], tool: str) -> bool:
        if execution.get("failure_injected") is True:
            return False
        if execution.get("detector_failure_detected") is True:
            return False
        if execution.get("execution_status") in _FAILED_STATUSES:
            return False
        if execution.get("error") is not None:
            return False
        return not self.detector.detect_step(
            {
                "step_id": execution.get("step_id", 1),
                "tool": tool,
                "input": execution.get("input", {}),
                "modified_input": execution.get(
                    "modified_input", execution.get("input", {})
                ),
                "output": execution.get("output"),
                "error": execution.get("error"),
                "execution_status": execution.get("execution_status"),
            }
        ).failure_detected

    def _recovery_reliability(
        self,
        report: Mapping[str, Any] | None,
        records: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        incidents = self._recovery_incidents(report, records)
        attempts = self._recovery_attempt_records(report, records)
        if report is None and not records:
            return {
                "recovery_reliability": None,
                "recovery_attempt_count": 0,
                "recovery_incident_count": 0,
                "recovery_success_count": 0,
                "recovery_failure_count": 0,
            }
        if not incidents:
            return {
                "recovery_reliability": None,
                "recovery_attempt_count": len(attempts),
                "recovery_incident_count": 0,
                "recovery_success_count": 0,
                "recovery_failure_count": 0,
            }

        values: list[float] = []
        success_count = 0
        failure_count = 0
        for incident in incidents:
            attempt_count = self._nonnegative_int(
                incident.get("attempt_count"),
                len(incident.get("attempts", []))
                if isinstance(incident.get("attempts"), list)
                else 0,
            )
            if incident.get("recovery_success") is True:
                success_count += 1
                repeated_attempts = max(0, attempt_count - 1)
                values.append(
                    max(
                        0.0,
                        1.0
                        - self.repeated_attempt_penalty * repeated_attempts,
                    )
                )
            else:
                failure_count += 1
                values.append(0.0)
        return {
            "recovery_reliability": sum(values) / len(values),
            "recovery_attempt_count": len(attempts),
            "recovery_incident_count": len(incidents),
            "recovery_success_count": success_count,
            "recovery_failure_count": failure_count,
        }

    @staticmethod
    def _recovery_incidents(
        report: Mapping[str, Any] | None,
        records: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        if report is not None and isinstance(report.get("failures"), list):
            reported = [
                incident
                for incident in report["failures"]
                if isinstance(incident, Mapping)
            ]
            if reported or not records:
                return reported
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for attempt in UncertaintyEstimator._recovery_attempt_records(report, records):
            grouped.setdefault(
                UncertaintyEstimator._id_key(attempt.get("original_step_id")), []
            ).append(attempt)
        return [
            {
                "attempt_count": len(attempts),
                "attempts": attempts,
                "recovery_success": any(
                    attempt.get("recovery_success") is True for attempt in attempts
                ),
            }
            for attempts in grouped.values()
        ]

    @staticmethod
    def _recovery_attempt_records(
        report: Mapping[str, Any] | None,
        records: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        logged = [
            record
            for record in records
            if record.get("record_type") == "recovery_attempt"
        ]
        if logged:
            return logged
        attempts: list[Mapping[str, Any]] = []
        if report is not None and isinstance(report.get("failures"), list):
            for incident in report["failures"]:
                if not isinstance(incident, Mapping):
                    continue
                incident_attempts = incident.get("attempts", [])
                if isinstance(incident_attempts, list):
                    attempts.extend(
                        attempt
                        for attempt in incident_attempts
                        if isinstance(attempt, Mapping)
                    )
        return attempts

    @classmethod
    def _linked_recovery_steps(
        cls,
        report: Mapping[str, Any] | None,
        records: Sequence[Mapping[str, Any]],
    ) -> set[str]:
        linked = {
            cls._id_key(record.get("step_id"))
            for record in records
            if record.get("record_type") == "recovery_trace_link"
            and record.get("recovery_success") is True
            and record.get("detector_validation_passed") is True
        }
        for attempt in cls._recovery_attempt_records(report, records):
            step_id = attempt.get("recovery_trace_step_id")
            if attempt.get("recovery_success") is True and step_id is not None:
                linked.add(cls._id_key(step_id))
        return linked

    @staticmethod
    def _self_consistency(
        samples: Sequence[str] | None,
    ) -> tuple[float | None, int]:
        if isinstance(samples, str):
            return None, 0
        normalized = [
            re.sub(r"[^a-z0-9]+", " ", sample.casefold()).strip()
            for sample in samples or ()
            if isinstance(sample, str) and sample.strip()
        ]
        if len(normalized) < 2:
            return None, len(normalized)
        pair_count = len(normalized) * (len(normalized) - 1) // 2
        agreement_count = sum(
            normalized[left] == normalized[right]
            for left in range(len(normalized))
            for right in range(left + 1, len(normalized))
        )
        return agreement_count / pair_count, len(normalized)

    @staticmethod
    def _score(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Grounding score must be a number between 0 and 1.")
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Grounding score must be a finite value between 0 and 1.")
        return float(value)

    @staticmethod
    def _nonnegative_int(value: Any, default: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return default
        return value

    @staticmethod
    def _id_key(value: Any) -> str:
        return "" if value is None else str(value)