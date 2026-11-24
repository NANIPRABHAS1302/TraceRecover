"""Tests for deterministic answer claim extraction."""

from grounding.claim_extractor import ClaimExtractor


def test_extracts_single_factual_claim() -> None:
    claims = ClaimExtractor().extract("The capital of France is Paris.")

    assert len(claims) == 1
    assert claims[0].claim_id == "claim_1"
    assert claims[0].claim == "The capital of France is Paris."


def test_extracts_multiple_claims_from_sentences_and_bullets() -> None:
    claims = ClaimExtractor().extract(
        "- Paris is in France.\n- Berlin is in Germany."
    )

    assert [claim.claim for claim in claims] == [
        "Paris is in France.",
        "Berlin is in Germany.",
    ]
    assert [claim.claim_id for claim in claims] == ["claim_1", "claim_2"]


def test_empty_and_malformed_answers_have_no_claims() -> None:
    extractor = ClaimExtractor()

    assert extractor.extract("") == []
    assert extractor.extract(" \n ") == []
    assert extractor.extract(None) == []
    assert extractor.extract({"answer": "not a string"}) == []
