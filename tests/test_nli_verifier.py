"""Unit tests for JSON linearization and DeBERTa-v3 NLI verification."""

from __future__ import annotations

import pytest

from grounding.evidence_checker import EvidenceChecker, linearize_json
from grounding.nli_verifier import DebertaNLIVerifier, SUPPORTED_NLI_MODELS


def test_linearize_json_converts_nested_dict_and_list_into_sentences() -> None:
    payload = {
        "ticket_id": "T-401",
        "status": "open",
        "owner": "Elena",
        "cost_usd": 1200,
        "meta": {"region": "us-east", "tags": ["p1", "sla"]},
    }
    text = linearize_json(payload)

    assert text.startswith("Record T-401:")
    assert "status is open" in text
    assert "owner is Elena" in text
    assert "cost usd is 1200" in text
    assert "meta region is us-east" in text
    assert "meta tags are p1, sla" in text
    assert text.endswith(".")


def test_linearize_json_handles_scalars_and_empty_inputs() -> None:
    assert linearize_json(None) == ""
    assert linearize_json({}) == ""
    assert linearize_json([]) == ""
    assert linearize_json(441) == "441"
    assert linearize_json("Paris is the capital.") == "Paris is the capital."


def test_deberta_nli_verifier_offline_fallback_entailment_contradiction_neutral() -> None:
    verifier = DebertaNLIVerifier(
        model_name=SUPPORTED_NLI_MODELS[0], force_fallback=True
    )
    assert verifier.backend_name == "deterministic_offline_fallback"
    assert verifier.is_transformer_loaded is False

    ent = verifier.predict(
        "Record T-401: cost usd is 1200, owner is Elena, status is open.",
        "Ticket T-401 is open and owned by Elena.",
    )
    assert ent.label == "ENTAILMENT"
    assert verifier.to_claim_relation(ent) == "SUPPORTED"

    contra_num = verifier.predict(
        "Climate Grid Sensor S-81 recorded feeder load at 142 kilowatts.",
        "Climate Grid Sensor S-81 recorded feeder load at 392 kilowatts.",
    )
    assert contra_num.label == "CONTRADICTION"
    assert verifier.to_claim_relation(contra_num) == "CONTRADICTED"

    contra_ant = verifier.predict(
        "Record T-401: status is open, owner is Elena.",
        "Record T-401 status is closed.",
    )
    assert contra_ant.label == "CONTRADICTION"
    assert verifier.to_claim_relation(contra_ant) == "CONTRADICTED"

    neut = verifier.predict(
        "Aerospace Telemetry Metric M-1 reports pressure at 420 bar.",
        "Jane Austen wrote Pride and Prejudice.",
    )
    assert neut.label == "NEUTRAL"
    assert verifier.to_claim_relation(neut) is None


def test_evidence_checker_use_nli_verifies_committed_outputs_only() -> None:
    verifier = DebertaNLIVerifier(force_fallback=True)
    checker = EvidenceChecker(use_nli=True, nli_verifier=verifier)

    trace = [
        {
            "step_id": 1,
            "tool": "mock_api",
            "input": {"ticket_id": "T-401"},
            "output": {
                "ticket_id": "T-401",
                "status": "closed",
                "owner": "WrongOwner",
            },
            "error": None,
            "output_state": "discarded",
        },
        {
            "step_id": 2,
            "tool": "mock_api",
            "input": {"ticket_id": "T-401"},
            "output": {
                "id": "T-401",
                "ticket_id": "T-401",
                "status": "open",
                "priority": "P1",
                "sla_hours": 4,
                "owner": "Elena",
                "cost_usd": 1200,
            },
            "error": None,
            "output_state": "committed",
        },
    ]

    report = checker.verify(
        "Ticket T-401 has priority P1, cost usd 1200, status open, and owner Elena.",
        trace,
    )
    claim = report["claims"][0]
    assert claim["status"] == "SUPPORTED"
    assert [ev["step_id"] for ev in claim["evidence"]] == [2]
    assert "nli" in claim["evidence"][0]
    assert claim["evidence"][0]["nli"]["backend"] == "deterministic_offline_fallback"


def test_deberta_nli_verifier_batch_and_validation() -> None:
    verifier = DebertaNLIVerifier(force_fallback=True)
    results = verifier.predict_batch(
        [
            ("Water boils at 100 degrees Celsius.", "Water boils at 100 degrees Celsius."),
            ("Water boils at 100 degrees Celsius.", "Water does not boil at 100 degrees Celsius."),
        ]
    )
    assert [r.label for r in results] == ["ENTAILMENT", "CONTRADICTION"]

    with pytest.raises(ValueError, match="entailment_threshold"):
        DebertaNLIVerifier(entailment_threshold=1.5)
