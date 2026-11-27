"""Fine-grained abstention taxonomy and semantic evidence reconciliation without weakening safety gates."""

from __future__ import annotations

from typing import Any, Mapping


ABSTENTION_CATEGORIES = (
    "grounded_confident",
    "low_confidence_synthesis",
    "insufficient_evidence",
    "contradictory_evidence",
    "tool_failure",
    "missing_credentials",
    "unanswerable_question",
    "policy_blocked",
)


def classify_abstention_and_uncertainty(
    *,
    final_decision: str,
    decision_explanation: str,
    blocked_by_guard: bool,
    offline_scope_limited: bool,
    grounding_report: Mapping[str, Any],
    claim_evidence_report: Mapping[str, Any],
    failure_report: Mapping[str, Any],
    recovery_report: Mapping[str, Any],
    uncertainty_signals: Mapping[str, Any],
    external_sources: list[dict[str, Any]] | None = None,
    direct_llm_no_tools: bool = False,
    answer_threshold: float = 0.15,
    abstain_threshold: float = 0.55,
) -> dict[str, Any]:
    """Reconcile semantic verification and assign an explicit root-cause taxonomy."""
    ext_count = len(external_sources or [])
    det_failures = int(failure_report.get("total_failures") or 0)
    rec_failed = int(recovery_report.get("failed_recoveries") or 0)
    contradict_count = int(claim_evidence_report.get("contradicted_count") or 0)
    ground_contradict = int(grounding_report.get("contradicted_count") or 0)
    # Use context-aware ClaimEvidenceVerifier as authority for contradictions so metadata differences never trigger false abstentions
    contradictions = contradict_count if "contradicted_count" in claim_evidence_report else ground_contradict
    sem_sup = int(claim_evidence_report.get("supported_count") or 0)
    sem_part = int(claim_evidence_report.get("partially_supported_count") or 0)
    sem_unsup = int(claim_evidence_report.get("unsupported_count") or 0)
    sem_insuff = int(claim_evidence_report.get("insufficient_evidence_count") or 0)
    sem_rate = claim_evidence_report.get("evidence_support_rate")
    tool_success_ratio = uncertainty_signals.get("tool_success_ratio")

    updated_decision = final_decision
    updated_explanation = decision_explanation
    upgraded_from_semantic = False

    # Safe reconciliation: reduce unnecessary lexical abstentions ONLY when external/connector sources were used,
    # zero contradictions exist, zero unsupported claims remain, and semantic similarity confirms support.
    if (
        updated_decision == "ABSTAIN"
        and not blocked_by_guard
        and not offline_scope_limited
        and contradictions == 0
        and sem_unsup == 0
        and sem_insuff == 0
        and sem_sup >= 1
        and (ext_count >= 1 or int(grounding_report.get("supported_count") or 0) >= 1)
        and (tool_success_ratio is None or float(tool_success_ratio) >= 0.5)
        and rec_failed == 0
    ):
        updated_decision = "WARN" if (sem_part > 0 or (sem_rate is not None and sem_rate < 0.85)) else "ANSWER"
        updated_explanation = (
            "Claims verified semantically against committed retrieved evidence with zero contradictions or unsupported statements."
        )
        upgraded_from_semantic = True

    # Classify taxonomy (distinct root causes: policy vs credentials vs contradiction vs tool_failure vs insufficient_evidence)
    expl_low = (updated_explanation or "").lower()
    if blocked_by_guard:
        category = "policy_blocked"
        abstention_reason = updated_explanation or "Blocked by safety/injection policy."
    elif "credential" in expl_low or "api key" in expl_low or "auth" in expl_low or "missing_credentials" in expl_low:
        category = "missing_credentials"
        abstention_reason = updated_explanation or "Required provider or connector credentials are missing or invalid."
    elif contradictions > 0:
        updated_decision = "ABSTAIN"
        category = "contradictory_evidence"
        updated_explanation = (
            "Conflicting factual claims detected across retrieved sources on matching subject/property; abstaining rather than guessing between contradictions."
        )
        abstention_reason = updated_explanation
    elif rec_failed > 0 or (det_failures > 0 and (tool_success_ratio == 0.0 or updated_decision == "ABSTAIN" and sem_sup == 0 and ext_count == 0)):
        updated_decision = "ABSTAIN" if updated_decision != "ANSWER" else updated_decision
        category = "tool_failure" if updated_decision == "ABSTAIN" else "low_confidence_synthesis"
        abstention_reason = "Unrecovered tool execution failure prevented reliable evidence retrieval."
    elif updated_decision == "ABSTAIN" and offline_scope_limited:
        category = "unanswerable_question"
        abstention_reason = updated_explanation or "Question requires live external retrieval unavailable in offline local mode."
    elif updated_decision == "ABSTAIN":
        category = "insufficient_evidence"
        abstention_reason = (
            "Evidence corpus was insufficient or unverified for reliable synthesis."
            if sem_insuff > 0 or ext_count == 0
            else (updated_explanation or "Insufficient corroborated evidence to support answer claims.")
        )
    elif direct_llm_no_tools or updated_decision == "WARN":
        category = "low_confidence_synthesis"
        abstention_reason = updated_explanation or "Partial support or unverified model synthesis; review recommended."
    else:
        category = "grounded_confident"
        abstention_reason = updated_explanation or "All claims grounded in corroborated evidence."

    return {
        "final_decision": updated_decision,
        "decision_explanation": updated_explanation,
        "abstention_category": category,
        "abstention_reason": abstention_reason,
        "reconciled_by_semantic_verifier": upgraded_from_semantic,
        "uncertainty_breakdown": {
            "uncertainty_score": uncertainty_signals.get("uncertainty_score"),
            "grounding_score": grounding_report.get("grounding_score"),
            "semantic_support_rate": sem_rate,
            "tool_success_ratio": tool_success_ratio,
            "contradicted_count": contradictions,
            "unsupported_count": sem_unsup,
            "answer_threshold": answer_threshold,
            "abstain_threshold": abstain_threshold,
        },
    }
