"""Threshold-based final-answer, warning, or abstention decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class AbstentionDecision:
    """A final disposition and the response safe to show to the user."""

    final_decision: str
    decision_explanation: str
    final_response: str

    def to_dict(self) -> dict[str, str]:
        """Return the decision in its report representation."""
        return {
            "final_decision": self.final_decision,
            "decision_explanation": self.decision_explanation,
            "final_response": self.final_response,
        }


class AbstentionPolicy:
    """Apply strict evidence gates followed by configurable score thresholds.

    Uncertainty at or below ``answer_threshold`` permits an answer. Values
    between the thresholds produce a warning; values at or above
    ``abstain_threshold`` abstain.
    """

    def __init__(
        self,
        answer_threshold: float = 0.15,
        abstain_threshold: float = 0.55,
        require_grounding: bool = True,
    ) -> None:
        if (
            isinstance(answer_threshold, bool)
            or isinstance(abstain_threshold, bool)
            or not isinstance(answer_threshold, (int, float))
            or not isinstance(abstain_threshold, (int, float))
            or not math.isfinite(answer_threshold)
            or not math.isfinite(abstain_threshold)
            or not 0 <= answer_threshold < abstain_threshold <= 1
        ):
            raise ValueError(
                "Thresholds must satisfy 0 <= answer_threshold "
                "< abstain_threshold <= 1."
            )
        self.answer_threshold = float(answer_threshold)
        self.abstain_threshold = float(abstain_threshold)
        if not isinstance(require_grounding, bool):
            raise TypeError("require_grounding must be a boolean.")
        self.require_grounding = require_grounding

    def decide(
        self,
        grounding_report: Mapping[str, Any],
        uncertainty_report: Mapping[str, Any],
    ) -> AbstentionDecision:
        """Choose ANSWER, WARN, or ABSTAIN without promoting weak claims."""
        claims = grounding_report.get("claims", [])
        total = self._count(grounding_report.get("total_claim_count"), len(claims))
        supported = self._count(grounding_report.get("supported_count"), 0)
        unsupported = self._count(grounding_report.get("unsupported_count"), 0)
        contradicted = self._count(grounding_report.get("contradicted_count"), 0)
        uncertainty = uncertainty_report.get("uncertainty_score")

        if self.require_grounding:
            if total == 0:
                return self._abstain(
                    "No factual claims were extracted, so there is no verified answer."
                )
            if contradicted > 0:
                return self._abstain(
                    "Tool evidence directly contradicts at least one answer claim."
                )
            if supported == 0:
                return self._abstain(
                    "No answer claim has usable supporting evidence in the tool trace."
                )
        if uncertainty is None:
            return self._abstain(
                "No uncertainty score could be calculated from the available signals."
            )
        if (
            isinstance(uncertainty, bool)
            or not isinstance(uncertainty, (int, float))
            or not 0 <= uncertainty <= 1
        ):
            raise ValueError("Uncertainty score must be between 0 and 1.")
        if uncertainty >= self.abstain_threshold:
            return self._abstain(
                f"Uncertainty {uncertainty:.3f} meets or exceeds the abstention "
                f"threshold {self.abstain_threshold:.3f}."
            )
        if self.require_grounding and unsupported > 0:
            return self._warn(
                "Some factual claims are unsupported; they are labeled in the response.",
                str(grounding_report.get("grounded_answer", "")),
            )
        if uncertainty > self.answer_threshold:
            return self._warn(
                f"Uncertainty {uncertainty:.3f} is above the answer threshold "
                f"{self.answer_threshold:.3f}.",
                str(grounding_report.get("grounded_answer", "")),
            )
        return AbstentionDecision(
            final_decision="ANSWER",
            decision_explanation=(
                "All extracted claims are supported and uncertainty is within "
                "the answer threshold."
            ),
            final_response=str(grounding_report.get("grounded_answer", "")),
        )

    @staticmethod
    def _count(value: Any, default: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return default
        return value

    @staticmethod
    def _abstain(explanation: str) -> AbstentionDecision:
        return AbstentionDecision(
            final_decision="ABSTAIN",
            decision_explanation=explanation,
            final_response=f"I am abstaining: {explanation}",
        )

    @staticmethod
    def _warn(explanation: str, grounded_answer: str) -> AbstentionDecision:
        return AbstentionDecision(
            final_decision="WARN",
            decision_explanation=explanation,
            final_response=(
                "Uncertainty warning: "
                + explanation
                + "\n\n"
                + grounded_answer
            ),
        )