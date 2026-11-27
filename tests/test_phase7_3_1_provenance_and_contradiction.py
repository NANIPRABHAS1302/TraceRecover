"""Phase 7.3.1 regression tests: Provenance-preserving claim extraction & context-aware contradiction verification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.open_ended_runner import OpenEndedAgentRunner
from connectors.manager import ConnectorManager
from connectors.sources.verifier import (
    ClaimEvidenceVerifier,
    _evaluate_contradiction_with_context,
    _extract_claims_with_provenance,
)
from eval.phase7_2_validation import _install_mock_academic_papers_72, _mock_search_adapter_72
from grounding.claim_extractor import ClaimExtractor

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PROVENANCE_KEYS = {
    "claim_id",
    "claim_text",
    "source_document_id",
    "source_url",
    "study_identity",
    "paper_title",
    "author_or_publication_identity",
    "publication_year",
    "research_subject",
    "research_metric",
    "measured_value",
    "unit",
    "dataset",
    "sample_size",
    "model_or_configuration",
    "evaluation_metric_definition",
    "evaluation_split",
    "benchmark",
    "timeframe",
    "original_supporting_excerpt",
}


def test_case_a_independent_papers_different_datasets_no_contradiction() -> None:
    """Case A: Two independent papers reporting different accuracy values on different datasets."""
    corpus = [
        {
            "source_name": "Paper 1 (2023): MNLI Verification Benchmark",
            "source_url": "https://arxiv.org/abs/2301.10001",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "text": "Paper 1 (arXiv:2301.10001) evaluates RoBERTa-large on the MNLI dataset and reports 87.5% accuracy.",
        },
        {
            "source_name": "Paper 2 (2024): PubMedQA Verification Benchmark",
            "source_url": "https://doi.org/10.18653/v1/2024.acl-long.42",
            "source_type": "crossref",
            "trust_tier": "tier2",
            "text": "Paper 2 (doi:10.18653/v1/2024.acl-long.42) evaluates RoBERTa-large on the PubMedQA dataset and reports 91.4% accuracy.",
        },
    ]
    answer = (
        "Paper 1 (arXiv:2301.10001) evaluates RoBERTa-large on the MNLI dataset and reports 87.5% accuracy.\n"
        "Paper 2 (doi:10.18653/v1/2024.acl-long.42) evaluates RoBERTa-large on the PubMedQA dataset and reports 91.4% accuracy."
    )
    rep = ClaimEvidenceVerifier().verify_answer(answer, corpus)
    assert rep["contradicted_count"] == 0
    assert rep["contradiction_details"] == []
    assert rep["supported_count"] == 2
    urls = {c["source_url"] for c in rep["claims"]}
    assert urls == {
        "https://arxiv.org/abs/2301.10001",
        "https://doi.org/10.18653/v1/2024.acl-long.42",
    }
    for cl in rep["claims"]:
        assert REQUIRED_PROVENANCE_KEYS <= set(cl["provenance"].keys())
        assert cl["contradiction_classification"] in ("different_experimental_context", "none")
        assert cl["provenance"]["dataset"] in ("MNLI", "PubMedQA")


def test_case_b_different_sample_populations_contextual_difference() -> None:
    """Case B: Two studies using different sample populations (N=200 vs N=540)."""
    corpus = [
        {
            "source_name": "Study 1 (2023)",
            "source_url": "https://arxiv.org/abs/2309.10412",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "text": "Study 1 (arXiv:2309.10412, N=200) evaluated KV-cache transformer eviction and achieved 87.5% accuracy.",
        },
        {
            "source_name": "Study 2 (2025)",
            "source_url": "https://doi.org/10.18653/v1/2025.naacl-long.18",
            "source_type": "crossref",
            "trust_tier": "tier2",
            "text": "Study 2 (doi:10.18653/v1/2025.naacl-long.18, N=540) evaluated KV-cache transformer eviction and achieved 91.4% accuracy.",
        },
    ]
    answer = (
        "Study 1 (arXiv:2309.10412, N=200) evaluated KV-cache transformer eviction and achieved 87.5% accuracy.\n"
        "Study 2 (doi:10.18653/v1/2025.naacl-long.18, N=540) evaluated KV-cache transformer eviction and achieved 91.4% accuracy."
    )
    rep = ClaimEvidenceVerifier().verify_answer(answer, corpus)
    assert rep["contradicted_count"] == 0
    assert rep["supported_count"] >= 2
    assert any(c["contradiction_classification"] == "different_experimental_context" for c in rep["claims"])
    sizes = {c["provenance"]["sample_size"] for c in rep["claims"]}
    assert sizes == {200, 540}


def test_case_c_same_study_incompatible_values_detected_as_logical_contradiction() -> None:
    """Case C: Same study reporting incompatible values for the same metric & experimental configuration."""
    # 1. Claim-vs-document contradiction for same study & identical configuration
    corpus_single = [
        {
            "source_name": "Study 1: Calibration Benchmark",
            "source_url": "https://example.org/study1-v1",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "text": "Study 1 evaluated BERT-base on MNLI test split (N=200) and reports 87.5% accuracy.",
        }
    ]
    bad_claim_answer = "Study 1 evaluated BERT-base on MNLI test split (N=200) and reports 71.0% accuracy."
    rep_claim = ClaimEvidenceVerifier().verify_answer(bad_claim_answer, corpus_single)
    assert rep_claim["contradicted_count"] >= 1
    assert rep_claim["claims"][0]["verification_code"] == "CONTRADICTED"
    assert rep_claim["claims"][0]["contradiction_classification"] == "logical_contradiction"

    # 2. Pairwise corpus contradiction when two documents report conflicting values for the SAME study & config
    corpus_pair = [
        {
            "source_name": "Study 1: Official Version",
            "source_url": "https://example.org/study1-official",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "text": "Study 1 evaluated BERT-base on MNLI test split (N=200) and reports 87.5% accuracy.",
        },
        {
            "source_name": "Study 1: Conflicting Errata",
            "source_url": "https://example.org/study1-errata",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "text": "Study 1 evaluated BERT-base on MNLI test split (N=200) and reports 71.0% accuracy.",
        },
    ]
    rep_pair = ClaimEvidenceVerifier().verify_answer(
        "Study 1 evaluated BERT-base on MNLI test split (N=200) and reports 87.5% accuracy.",
        corpus_pair,
    )
    assert rep_pair["contradicted_count"] >= 1
    assert any(d["classification"] == "logical_contradiction" for d in rep_pair["contradiction_details"])


def test_case_d_two_studies_genuinely_conflicting_conclusions_preserved() -> None:
    """Case D: Two studies reporting genuinely conflicting conclusions about the same research question."""
    corpus = [
        {
            "source_name": "Study 1 (2024): Sparse Attention Positive",
            "source_url": "https://arxiv.org/abs/2402.00001",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "text": "Study 1 concludes that sparse attention improves long-context clinical retrieval accuracy on medical benchmarks.",
        },
        {
            "source_name": "Study 2 (2025): Sparse Attention Negative",
            "source_url": "https://doi.org/10.18653/v1/2025.acl-long.99",
            "source_type": "crossref",
            "trust_tier": "tier2",
            "text": "Study 2 concludes that sparse attention does not improve long-context clinical retrieval accuracy on medical benchmarks.",
        },
    ]
    answer = (
        "Study 1 concludes that sparse attention improves long-context clinical retrieval accuracy on medical benchmarks.\n"
        "Study 2 concludes that sparse attention does not improve long-context clinical retrieval accuracy on medical benchmarks."
    )
    rep = ClaimEvidenceVerifier().verify_answer(answer, corpus)
    assert rep["contradicted_count"] >= 1
    assert len(rep["conflicting_research_findings"]) >= 1
    assert any(
        c["contradiction_classification"] == "comparable_differing_research_results"
        for c in rep["claims"]
    )
    assert any(
        f["classification"] == "comparable_differing_research_results"
        for f in rep["conflicting_research_findings"]
    )


def test_case_e_bibliographic_metadata_differences_no_false_contradiction() -> None:
    """Case E: Bibliographic metadata differences (publication years, arXiv IDs, DOIs, study numbering)."""
    corpus = [
        {
            "source_name": "Paper 1 (2022)",
            "source_url": "https://arxiv.org/abs/2205.01234",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "text": "Paper 1 (arXiv:2205.01234, Published 2022) shows that chain-of-thought verification reduces ungrounded errors. Cited by 42 works.",
        },
        {
            "source_name": "Article 2 (2025)",
            "source_url": "https://doi.org/10.1145/3600000.3600001",
            "source_type": "crossref",
            "trust_tier": "tier2",
            "text": "Article 2 (doi:10.1145/3600000.3600001, Published 2025) shows that chain-of-thought verification reduces ungrounded errors. Cited by 19 works.",
        },
    ]
    answer = (
        "Paper 1 (arXiv:2205.01234, Published 2022) shows that chain-of-thought verification reduces ungrounded errors.\n"
        "Article 2 (doi:10.1145/3600000.3600001, Published 2025) shows that chain-of-thought verification reduces ungrounded errors."
    )
    rep = ClaimEvidenceVerifier().verify_answer(answer, corpus)
    assert rep["contradicted_count"] == 0
    assert rep["contradiction_details"] == []
    assert rep["supported_count"] == 2
    assert all(
        c["contradiction_classification"] in ("bibliographic_metadata_difference", "none")
        for c in rep["claims"]
    )


def test_case_f_sentence_and_clause_splitting_preserves_inherited_study_attribution() -> None:
    """Case F: Sentence splitting with inherited study attribution retains study identity & citation."""
    extractor = ClaimExtractor()
    anaphora_text = "Study 1 reports 87.5% accuracy on N=200. It also reports 12% memory reduction."
    extracted = _extract_claims_with_provenance(anaphora_text, extractor)
    assert len(extracted) == 2
    assert extracted[0]["attribution_status"] == "explicit"
    assert extracted[0]["context"]["study_identity"] == "Study 1"
    assert extracted[0]["context"]["sample_size"] == 200
    assert extracted[1]["attribution_status"] == "inherited"
    assert extracted[1]["context"]["study_identity"] == "Study 1"
    assert extracted[1]["context"]["sample_size"] == 200

    corpus = [
        {
            "source_name": "Study 1 (2023): Memory Pruning",
            "source_url": "https://arxiv.org/abs/2309.10412",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "text": "Study 1 (arXiv:2309.10412, N=200) reports 87.5% accuracy and 12% memory reduction.",
        },
        {
            "source_name": "Journal Article 2 (2025): Pruning Corroboration",
            "source_url": "https://doi.org/10.18653/v1/2025.naacl-long.18",
            "source_type": "crossref",
            "trust_tier": "tier2",
            "text": "Journal Article 2 (doi:10.18653/v1/2025.naacl-long.18, N=540) reports 91.4% accuracy and 28% memory reduction.",
        },
    ]
    rep = ClaimEvidenceVerifier().verify_answer(anaphora_text, corpus)
    assert rep["contradicted_count"] == 0
    assert len(rep["claims"]) == 2
    for cl in rep["claims"]:
        assert cl["study_identity"] == "Study 1"
        assert cl["source_url"] == "https://arxiv.org/abs/2309.10412"
        assert cl["verification_code"] == "SUPPORTED"
    assert rep["study_attribution_summary"]["explicit"] == 1
    assert rep["study_attribution_summary"]["inherited"] == 1


def test_case_g_ambiguous_study_attribution_flags_uncertainty_without_fabricating() -> None:
    """Case G: Unclear study attribution flags ambiguity instead of inventing provenance."""
    corpus = [
        {
            "source_name": "Study 1 (2023)",
            "source_url": "https://arxiv.org/abs/2309.10412",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "text": "Study 1 (N=200) evaluated KV-cache transformer eviction pruning and observed 87.5% accuracy.",
        },
        {
            "source_name": "Study 2 (2025)",
            "source_url": "https://doi.org/10.18653/v1/2025.naacl-long.18",
            "source_type": "crossref",
            "trust_tier": "tier2",
            "text": "Study 2 (N=540) evaluated KV-cache transformer eviction pruning and observed 91.4% accuracy.",
        },
    ]
    # Unattributed claim with no study identity and no uniquely disambiguating measurement
    unattributed_answer = "The experiment evaluated KV-cache transformer eviction pruning and observed strong accuracy gains."
    rep = ClaimEvidenceVerifier().verify_answer(unattributed_answer, corpus)
    assert len(rep["claims"]) == 1
    cl = rep["claims"][0]
    assert cl["attribution_status"] == "ambiguous"
    assert cl["study_identity"] is None
    assert cl["source_url"] is None
    assert cl["provenance"]["study_identity"] is None
    assert cl["provenance"]["source_document_id"] is None
    assert cl["contradiction_classification"] == "insufficient_comparison_context"
    assert cl["verification_status"] != "Supported"
    assert "ambiguous" in cl["verification_reason"].lower()


def test_case_h_different_units_and_metric_definitions_not_compared_as_equivalent() -> None:
    """Case H: Numeric values with different units (% vs ms) or metric definitions (macro-F1 vs micro-F1, top-1 vs top-5)."""
    is_c1, cls1, _ = _evaluate_contradiction_with_context(
        "Model X achieved 87.5% accuracy on MNLI.",
        "Model X achieved 87.5 ms latency on MNLI.",
        is_claim_vs_doc=True,
    )
    assert is_c1 is False
    assert cls1 in ("different_experimental_context", "none")

    is_c2, cls2, _ = _evaluate_contradiction_with_context(
        "Model X achieved 0.82 macro-F1 on PubMedQA.",
        "Model X achieved 0.91 micro-F1 on PubMedQA.",
        is_claim_vs_doc=True,
    )
    assert is_c2 is False
    assert cls2 == "different_experimental_context"

    is_c3, cls3, _ = _evaluate_contradiction_with_context(
        "Model X achieved 78.2% top-1 accuracy on ImageNet.",
        "Model X achieved 94.1% top-5 accuracy on ImageNet.",
        is_claim_vs_doc=True,
    )
    assert is_c3 is False
    assert cls3 == "different_experimental_context"


@pytest.mark.parametrize("task_id", ["P72-01", "P72-04", "P72-10"])
def test_phase72_false_positive_tasks_resolved_with_accurate_study_provenance(tmp_path: Path, task_id: str) -> None:
    """Exact Phase 7.2 false-positive tasks (P72-01, P72-04, P72-10) must NOT falsely abstain or report contradictions."""
    tasks_data = json.loads((PROJECT_ROOT / "data" / "phase7_2_live_unseen.json").read_text(encoding="utf-8"))
    task = next(t for t in tasks_data["tasks"] if t["id"] == task_id)

    mgr = ConnectorManager(storage_dir=tmp_path / f"conn_{task_id}")
    _install_mock_academic_papers_72(mgr)
    runner = OpenEndedAgentRunner(
        search_adapter=_mock_search_adapter_72(),
        connector_manager=mgr,
        log_directory=tmp_path / f"logs_{task_id}",
    )

    out = runner.run(
        task["question"],
        provider="local",
        enable_external_search=True,
        use_connectors=True,
        seed=42,
    )
    claim_rep = out["claim_evidence_report"]
    assert out["final_decision"] in ("ANSWER", "WARN"), f"{task_id} falsely abstained: {out.get('abstention_reason')}"
    assert claim_rep["contradicted_count"] == 0
    assert claim_rep["contradiction_details"] == []

    claims = claim_rep["claims"]
    assert len(claims) >= 2
    for cl in claims:
        assert REQUIRED_PROVENANCE_KEYS <= set(cl["provenance"].keys())

    # Specifically verify the split sub-claims that previously caused FP=3 retain study provenance and exact citations
    study1_claims = [c for c in claims if "87.5%" in c["claim_text"]]
    article2_claims = [c for c in claims if "91.4%" in c["claim_text"]]
    assert study1_claims, f"Expected 87.5% claim in {task_id}"
    assert article2_claims, f"Expected 91.4% claim in {task_id}"
    for cl in study1_claims:
        assert cl["study_identity"] is not None and "Study 1" in cl["study_identity"]
        assert cl["attribution_status"] in ("explicit", "inherited")
        assert cl["source_url"] == "https://arxiv.org/abs/2309.10412"
        assert cl["provenance"]["sample_size"] == 200
        assert cl["verification_code"] == "SUPPORTED"
    for cl in article2_claims:
        assert cl["study_identity"] is not None and "Journal Article 2" in cl["study_identity"]
        assert cl["attribution_status"] in ("explicit", "inherited")
        assert cl["source_url"] == "https://doi.org/10.18653/v1/2025.naacl-long.18"
        assert cl["provenance"]["sample_size"] == 540
        assert cl["verification_code"] == "SUPPORTED"


def test_phase72_genuine_contradictions_still_detected_at_100_percent_recall(tmp_path: Path) -> None:
    """All 5 true contradiction tasks in Phase 7.2 (P72-37..P72-41) must still be caught with 100% recall."""
    tasks_data = json.loads((PROJECT_ROOT / "data" / "phase7_2_live_unseen.json").read_text(encoding="utf-8"))
    contra_tasks = [t for t in tasks_data["tasks"] if t.get("expected_contradiction") is True]
    assert len(contra_tasks) == 5

    mgr = ConnectorManager(storage_dir=tmp_path / "conn_contra")
    _install_mock_academic_papers_72(mgr)
    runner = OpenEndedAgentRunner(
        search_adapter=_mock_search_adapter_72(),
        connector_manager=mgr,
        log_directory=tmp_path / "logs_contra",
    )

    for t in contra_tasks:
        out = runner.run(
            t["question"],
            provider="local",
            enable_external_search=True,
            use_connectors=False,
            seed=42,
        )
        claim_rep = out["claim_evidence_report"]
        assert out["final_decision"] == "ABSTAIN", f"{t['id']} failed to abstain on genuine contradiction"
        assert int(claim_rep.get("contradicted_count") or 0) >= 1, f"{t['id']} missed contradiction"


def test_dev_regression_title_vs_negated_body_and_rfc_ids_and_et_al_attribution() -> None:
    """Development regression tests (outside Phase 7.4 holdout) for defects discovered during Phase 7.4 audit."""
    verifier = ClaimEvidenceVerifier()

    # 1. Title-prefixed claim containing a negation must not self-contradict its own corpus title
    corpus_self = [
        {
            "source_name": "Transport Protocol Spec",
            "source_url": "https://example.org/spec-a",
            "source_type": "external_search",
            "trust_tier": "tier1",
            "text": "Transport Protocol Spec. Multiplexed streams ensure packet loss on one stream does not stall unrelated streams.",
        }
    ]
    rep_self = verifier.verify_answer(
        "Transport Protocol Spec: Multiplexed streams ensure packet loss on one stream does not stall unrelated streams.",
        corpus_self,
    )
    assert rep_self["contradicted_count"] == 0
    assert rep_self["claims"][0]["verification_code"] == "SUPPORTED"

    # 2. Distinct RFC standard identifiers in complementary docs must not be treated as conflicting numeric facts
    corpus_rfcs = [
        {
            "source_name": "IETF RFC 7540",
            "source_url": "https://datatracker.ietf.org/doc/html/rfc7540",
            "source_type": "external_search",
            "trust_tier": "tier1",
            "text": "IETF RFC 7540 defines binary framing for HTTP/2 over TCP.",
        },
        {
            "source_name": "IETF RFC 8446",
            "source_url": "https://datatracker.ietf.org/doc/html/rfc8446",
            "source_type": "external_search",
            "trust_tier": "tier1",
            "text": "IETF RFC 8446 defines TLS 1.3 handshake security.",
        },
    ]
    rep_rfcs = verifier.verify_answer(
        "IETF RFC 7540 defines binary framing for HTTP/2 over TCP.\nIETF RFC 8446 defines TLS 1.3 handshake security.",
        corpus_rfcs,
    )
    assert rep_rfcs["contradicted_count"] == 0

    # 3. 'Author et al. (YYYY)' must not split on 'al.' and must preserve study identity on the measurement claim
    extracted = _extract_claims_with_provenance(
        "Kim et al. (2024) evaluated pruning on SQuAD (N=400) and reported 85.2% accuracy.",
        ClaimExtractor(),
    )
    meas_claims = [c for c in extracted if "85.2%" in c["claim_text"]]
    assert len(meas_claims) == 1
    assert meas_claims[0]["context"]["study_identity"] == "Kim et al."
    assert meas_claims[0]["attribution_status"] in ("explicit", "inherited")

