"""Tests for confidence-aware answer, warning, and abstention decisions."""

import pytest

from uncertainty.abstention import AbstentionPolicy
from agent.react_agent import ReactAgent
from config import LLMClient


def _grounding(
    *,
    total: int = 1,
    supported: int = 1,
    unsupported: int = 0,
    contradicted: int = 0,
    answer: str = "Paris [SUPPORTED; evidence: step 1]",
) -> dict[str, object]:
    return {
        "total_claim_count": total,
        "supported_count": supported,
        "unsupported_count": unsupported,
        "contradicted_count": contradicted,
        "grounded_answer": answer,
    }


def _uncertainty(score: float | None) -> dict[str, float | None]:
    return {"uncertainty_score": score}


def test_answer_decision_returns_cited_answer() -> None:
    decision = AbstentionPolicy().decide(
        _grounding(),
        _uncertainty(0.1),
    )

    assert decision.final_decision == "ANSWER"
    assert decision.final_response == "Paris [SUPPORTED; evidence: step 1]"


def test_warn_discloses_uncertainty_and_includes_labeled_answer() -> None:
    answer = (
        "Paris [SUPPORTED; evidence: step 1]\n"
        "It is the oldest city [UNSUPPORTED]"
    )
    decision = AbstentionPolicy().decide(
        _grounding(
            total=2,
            supported=1,
            unsupported=1,
            answer=answer,
        ),
        _uncertainty(0.1),
    )

    assert decision.final_decision == "WARN"
    assert "Uncertainty warning:" in decision.final_response
    assert answer in decision.final_response


@pytest.mark.parametrize(
    ("grounding", "uncertainty", "expected_reason"),
    [
        (
            _grounding(contradicted=1, supported=0),
            0.0,
            "contradicts",
        ),
        (
            _grounding(total=0, supported=0, answer=""),
            0.0,
            "No factual claims",
        ),
        (
            _grounding(supported=0, unsupported=1),
            0.2,
            "No answer claim has usable supporting evidence",
        ),
    ],
)
def test_strict_evidence_gates_abstain(
    grounding: dict[str, object],
    uncertainty: float,
    expected_reason: str,
) -> None:
    decision = AbstentionPolicy().decide(grounding, _uncertainty(uncertainty))

    assert decision.final_decision == "ABSTAIN"
    assert expected_reason in decision.decision_explanation
    assert "Paris" not in decision.final_response


def test_configured_thresholds_select_all_three_decisions() -> None:
    policy = AbstentionPolicy(answer_threshold=0.2, abstain_threshold=0.7)

    answer = policy.decide(_grounding(), _uncertainty(0.2))
    warning = policy.decide(_grounding(), _uncertainty(0.4))
    abstain = policy.decide(_grounding(), _uncertainty(0.7))

    assert answer.final_decision == "ANSWER"
    assert warning.final_decision == "WARN"
    assert abstain.final_decision == "ABSTAIN"


def test_missing_uncertainty_score_abstains() -> None:
    decision = AbstentionPolicy().decide(_grounding(), _uncertainty(None))

    assert decision.final_decision == "ABSTAIN"
    assert "No uncertainty score" in decision.decision_explanation


def test_invalid_thresholds_are_rejected() -> None:
    with pytest.raises(ValueError, match="Thresholds must satisfy"):
        AbstentionPolicy(answer_threshold=0.8, abstain_threshold=0.5)


def test_agent_abstains_when_search_has_no_local_evidence(tmp_path) -> None:
    result = ReactAgent(
        client=LLMClient(provider="local"),
        log_directory=tmp_path,
    ).run("Who discovered the fictional element unobtainium?")

    assert result.final_decision == "ABSTAIN"
    assert result.grounding_report["unsupported_count"] == 1
    assert result.grounding_report["claims"][0]["evidence"] == []
    assert "abstaining" in result.final_response.casefold()


def test_agent_abstains_when_answer_claim_contradicts_search_evidence(tmp_path) -> None:
    class ContradictionPlanner:
        def next_action(self, question, observations, excluded_tools=None):
            del question, excluded_tools
            if not observations:
                return {
                    "thought": "Retrieve local evidence.",
                    "action": "search",
                    "arguments": {"query": "What is the capital of France?"},
                    "final_answer": None,
                }
            return {
                "thought": "Return a claim conflicting with the observation.",
                "action": "final",
                "arguments": {},
                "final_answer": "The capital of France is Rome.",
            }

    result = ReactAgent(
        client=LLMClient(provider="local"),
        planner=ContradictionPlanner(),
        log_directory=tmp_path,
    ).run("What is the capital of France?")

    claim = result.grounding_report["claims"][0]
    assert claim["status"] == "CONTRADICTED"
    assert claim["evidence"][0]["step_id"] == 1
    assert result.final_decision == "ABSTAIN"
    assert "Rome" not in result.final_response
