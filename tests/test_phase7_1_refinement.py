"""Phase 7.1 Adversarial Academic Contradiction, Evidence Verification, Provider Hardening & Security Tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from agent.open_ended_runner import OpenEndedAgentRunner, ProviderUnavailableError
from agent.provider_adapter import LLMProviderAdapter, ProviderAdapterError, _redact_secret_text
from agent.synthesizer import EvidenceGroundedSynthesizer
from agent.uncertainty_taxonomy import ABSTENTION_CATEGORIES, classify_abstention_and_uncertainty
from connectors.manager import ConnectorManager
from connectors.permissions import PermissionEngine
from connectors.schemas import ConnectorConfig
from connectors.sources.verifier import ClaimEvidenceVerifier
from tools.external_search import ExternalSearchAdapter
import web_dashboard

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Task A: 7 Mandatory Adversarial Academic & Factual Contradiction Cases
# ---------------------------------------------------------------------------

def test_task_a1_different_publication_years_not_contradictory() -> None:
    verifier = ClaimEvidenceVerifier()
    corpus = [
        {
            "source_name": "Paper A (2021)",
            "source_url": "https://arxiv.org/abs/2104.00001",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "timestamp": "2021-04-01T00:00:00Z",
            "text": "Paper A (2021) demonstrated selective conformal prediction improves uncertainty calibration for RAG agents.",
        },
        {
            "source_name": "Paper B (2025)",
            "source_url": "https://arxiv.org/abs/2502.00002",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "timestamp": "2025-02-01T00:00:00Z",
            "text": "Paper B (2025) confirmed conformal prediction improves uncertainty calibration across multi-step RAG agents.",
        },
    ]
    rep = verifier.verify_answer(
        "Conformal prediction improves uncertainty calibration for RAG agents.",
        corpus,
    )
    assert rep["contradicted_count"] == 0
    assert rep["claims"][0]["verification_code"] == "SUPPORTED"


def test_task_a2_different_arxiv_ids_and_dois_not_contradictory() -> None:
    verifier = ClaimEvidenceVerifier()
    corpus = [
        {
            "source_name": "arXiv Preprint",
            "source_url": "https://arxiv.org/abs/2308.11111",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "timestamp": "2023-08-01T00:00:00Z",
            "text": "arXiv:2308.11111 introduces grounded verification loops mitigating tool-call hallucinations.",
        },
        {
            "source_name": "Crossref Article",
            "source_url": "https://doi.org/10.18653/v1/2024.acl-long.99",
            "source_type": "crossref",
            "trust_tier": "tier2",
            "timestamp": "2024-08-01T00:00:00Z",
            "text": "doi:10.18653/v1/2024.acl-long.99 validates grounded verification loops reducing hallucinations.",
        },
    ]
    rep = verifier.verify_answer(
        "Grounded verification loops mitigate tool-call hallucinations.",
        corpus,
    )
    assert rep["contradicted_count"] == 0
    assert rep["independent_sources_count"] >= 1


def test_task_a3_different_study_accuracy_and_sample_sizes_not_contradictory() -> None:
    verifier = ClaimEvidenceVerifier()
    corpus = [
        {
            "source_name": "Study 1",
            "source_url": "https://arxiv.org/abs/2401.10001",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "timestamp": "2024-01-10T00:00:00Z",
            "text": "Study 1 evaluated DeBERTa NLI on N=150 medical QA pairs and achieved 86.4% accuracy.",
        },
        {
            "source_name": "Study 2",
            "source_url": "https://doi.org/10.1000/study2",
            "source_type": "crossref",
            "trust_tier": "tier2",
            "timestamp": "2025-01-10T00:00:00Z",
            "text": "Study 2 evaluated DeBERTa NLI on sample size N=600 legal QA pairs and achieved 91.2% accuracy.",
        },
    ]
    rep = verifier.verify_answer(
        "Independent studies report DeBERTa NLI evaluation accuracy across multiple benchmark sample sizes.",
        corpus,
    )
    assert rep["contradicted_count"] == 0


def test_task_a4_incompatible_software_release_date_contradicted() -> None:
    verifier = ClaimEvidenceVerifier()
    corpus = [
        {
            "source_name": "Python Official Docs",
            "source_url": "https://docs.python.org/3.11/",
            "source_type": "official_docs",
            "trust_tier": "tier1",
            "timestamp": "2026-01-01T00:00:00Z",
            "text": "Python 3.11 was released in October 2022.",
        },
        {
            "source_name": "Conflicting Mirror",
            "source_url": "https://example.org/py311",
            "source_type": "web",
            "trust_tier": "tier4",
            "timestamp": "2026-01-01T00:00:00Z",
            "text": "Python 3.11 was released in October 2025.",
        },
    ]
    rep = verifier.verify_answer("Python 3.11 was released in October 2022.", corpus)
    assert rep["contradicted_count"] >= 1
    assert any(c["verification_code"] == "CONTRADICTED" for c in rep["claims"])


def test_task_a5_incompatible_scientific_constant_contradicted() -> None:
    verifier = ClaimEvidenceVerifier()
    corpus = [
        {
            "source_name": "NIST WebBook",
            "source_url": "https://webbook.nist.gov/water",
            "source_type": "official_docs",
            "trust_tier": "tier1",
            "timestamp": "2026-01-01T00:00:00Z",
            "text": "Standard boiling point of water at 1 atm pressure is 100 Celsius.",
        },
        {
            "source_name": "Faulty Blog",
            "source_url": "https://example.org/water",
            "source_type": "web",
            "trust_tier": "tier4",
            "timestamp": "2026-01-01T00:00:00Z",
            "text": "Standard boiling point of water at 1 atm pressure is 75 Celsius.",
        },
    ]
    rep = verifier.verify_answer("Standard boiling point of water at 1 atm pressure is 100 Celsius.", corpus)
    assert rep["contradicted_count"] >= 1
    assert rep["claims"][0]["verification_code"] == "CONTRADICTED"


def test_task_a6_direct_polarity_refutation_contradicted() -> None:
    verifier = ClaimEvidenceVerifier()
    corpus = [
        {
            "source_name": "Engine Manual",
            "source_url": "https://docs.python.org/engine-a",
            "source_type": "official_docs",
            "trust_tier": "tier1",
            "timestamp": "2026-01-01T00:00:00Z",
            "text": "Engine X-40 supports optimistic locking for distributed commits.",
        },
        {
            "source_name": "Engine Errata",
            "source_url": "https://docs.python.org/engine-b",
            "source_type": "web",
            "trust_tier": "tier3",
            "timestamp": "2026-01-01T00:00:00Z",
            "text": "Engine X-40 never uses optimistic locking for distributed commits.",
        },
    ]
    rep = verifier.verify_answer("Engine X-40 supports optimistic locking for distributed commits.", corpus)
    assert rep["contradicted_count"] >= 1


def test_task_a7_evolution_over_time_not_contradictory() -> None:
    verifier = ClaimEvidenceVerifier()
    corpus = [
        {
            "source_name": "Release Archive 2020",
            "source_url": "https://docs.python.org/v1",
            "source_type": "official_docs",
            "trust_tier": "tier1",
            "timestamp": "2020-05-01T00:00:00Z",
            "text": "Originally in 2020 Engine v1.0 used single-threaded synchronous queue polling.",
        },
        {
            "source_name": "Release Archive 2024",
            "source_url": "https://docs.python.org/v2",
            "source_type": "official_docs",
            "trust_tier": "tier1",
            "timestamp": "2024-05-01T00:00:00Z",
            "text": "Later in 2024 Engine v2.0 upgraded to async TaskGroup queue polling.",
        },
    ]
    rep = verifier.verify_answer(
        "Engine v2.0 in 2024 upgraded to async TaskGroup queue polling.",
        corpus,
    )
    assert rep["contradicted_count"] == 0


# ---------------------------------------------------------------------------
# Task B: All 5 Verification Outcomes + Duplicate vs Independent Sources
# ---------------------------------------------------------------------------

def test_task_b_five_verification_codes_and_duplicate_dedup() -> None:
    verifier = ClaimEvidenceVerifier()

    empty_rep = verifier.verify_answer("Conformal prediction controls calibration error.", [])
    assert empty_rep["claims"][0]["verification_code"] == "INSUFFICIENT_EVIDENCE"
    assert empty_rep["claims"][0]["verification_status"] == "Insufficient evidence"
    assert empty_rep["insufficient_evidence_count"] == 1

    corpus = [
        {
            "source_name": "Source 1",
            "source_url": "https://arxiv.org/abs/2401.00001",
            "source_type": "arxiv",
            "trust_tier": "tier2",
            "timestamp": "2025-01-01T00:00:00Z",
            "text": "Fernet encryption uses AES-128-CBC and HMAC-SHA256 authenticated tokens.",
        },
        {
            "source_name": "Mirror Copy",
            "source_url": "https://mirror.example.org/fernet",
            "source_type": "web",
            "trust_tier": "tier4",
            "timestamp": "2025-01-02T00:00:00Z",
            "text": "Fernet encryption uses AES-128-CBC and HMAC-SHA256 authenticated tokens.",
        },
        {
            "source_name": "Release Log",
            "source_url": "https://docs.python.org/3.12",
            "source_type": "official_docs",
            "trust_tier": "tier1",
            "timestamp": "2025-01-02T00:00:00Z",
            "text": "Python 3.12 shipped in October 2023.",
        },
    ]
    rep = verifier.verify_answer(
        "Fernet encryption uses AES-128-CBC and HMAC-SHA256 authenticated tokens. "
        "Python 3.12 shipped in 2023 with 42 new opcodes. "
        "Python 3.12 shipped in October 2029. "
        "Unicorns run quantum databases on Mars.",
        corpus,
    )
    codes = [c["verification_code"] for c in rep["claims"]]
    assert codes[0] == "SUPPORTED"
    assert rep["claims"][0]["duplicate_source_count"] >= 1
    assert rep["claims"][0]["independent_source_count"] == 1
    assert codes[1] == "PARTIALLY_SUPPORTED"
    assert codes[2] == "CONTRADICTED"
    assert codes[3] == "UNSUPPORTED"
    assert all(c["verification_reason"] for c in rep["claims"])


# ---------------------------------------------------------------------------
# Task C: Provider Adapter Error Categories & Zero Secret Exposure
# ---------------------------------------------------------------------------

def test_task_c_provider_validation_errors_and_secret_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "changeme")
    bad = LLMProviderAdapter(provider="openai")
    diag = bad.validate_configuration()
    assert diag["configured"] is False
    assert diag["credential_status"] == "invalid_api_key"

    monkeypatch.setenv("OLLAMA_BASE_URL", "ftp://invalid-endpoint")
    bad_url = LLMProviderAdapter(provider="ollama")
    with pytest.raises(ProviderAdapterError) as exc_cfg:
        bad_url.check_credentials()
    assert exc_cfg.value.category == "invalid_configuration"

    # Simulated timeout & rate-limit error mapping without exposing API key
    class _TimeoutClient:
        provider = "openai"
        model = "gpt-4o-mini"
        def usage_summary(self):
            return {"call_count": 0, "estimated_cost_usd": 0.0}
        def complete_with_metadata(self, prompt: str):
            raise RuntimeError("HTTP 429 rate limit exceeded for sk-supersecretkey1234567890")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-validformatkey12345678")
    rate_adapter = LLMProviderAdapter(client=_TimeoutClient())
    with pytest.raises(ProviderAdapterError) as exc_rl:
        rate_adapter.complete_with_budget("hello")
    assert exc_rl.value.category == "rate_limited"
    assert "sk-supersecretkey" not in str(exc_rl.value)
    assert _redact_secret_text("Bearer tok_abcdefgh123456") == "[REDACTED_KEY]"

    class _SleepyClient:
        provider = "gemini"
        model = "gemini-2.0-flash"
        def usage_summary(self):
            return {"call_count": 0, "estimated_cost_usd": 0.0}
        def complete_with_metadata(self, prompt: str):
            raise TimeoutError("request timed out")

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyValidFormatTestKey12345")
    t_adapter = LLMProviderAdapter(client=_SleepyClient())
    with pytest.raises(ProviderAdapterError) as exc_to:
        t_adapter.complete_with_budget("hello")
    assert exc_to.value.category == "timeout"


# ---------------------------------------------------------------------------
# Task D & E: Structured Synthesis & Abstention Taxonomy Distinction
# ---------------------------------------------------------------------------

def test_task_d_and_e_structured_synthesis_and_taxonomy_distinction() -> None:
    synth = EvidenceGroundedSynthesizer()
    obs = [
        {
            "tool": "research_sources",
            "output": {
                "documents": [
                    {
                        "title": "Conformal Study (2024)",
                        "url": "https://arxiv.org/abs/2402.00001",
                        "snippet": "Conformal prediction calibrates epistemic uncertainty safely.",
                        "trust_tier": "tier2",
                    }
                ]
            },
        }
    ]
    ans = synth.synthesize("What does conformal prediction do?", obs)
    struct = synth.synthesize_structured(
        "What does conformal prediction do?",
        obs,
        raw_answer=ans,
        external_sources=[{"title": "Conformal Study (2024)", "url": "https://arxiv.org/abs/2402.00001", "provider": "arxiv", "trust_tier": "tier2"}],
        claim_evidence_report={"supported_count": 1, "partially_supported_count": 0, "contradicted_count": 0, "unsupported_count": 0},
        uncertainty_report={"final_decision": "ANSWER", "abstention_category": "grounded_confident", "decision_explanation": "All claims supported."},
    )
    assert set(struct.keys()) >= {"direct_answer", "explanation", "supporting_evidence", "citations", "contradictions", "limitations"}
    assert struct["citations"][0]["url"] == "https://arxiv.org/abs/2402.00001"

    # tool_failure vs insufficient_evidence distinction
    tax_tool_fail = classify_abstention_and_uncertainty(
        final_decision="ABSTAIN",
        decision_explanation="Abstaining due to error.",
        blocked_by_guard=False,
        offline_scope_limited=False,
        grounding_report={"supported_count": 0, "contradicted_count": 0},
        claim_evidence_report={"supported_count": 0, "contradicted_count": 0, "unsupported_count": 0, "insufficient_evidence_count": 1},
        failure_report={"total_failures": 1},
        recovery_report={"failed_recoveries": 1},
        uncertainty_signals={"uncertainty_score": 0.9, "tool_success_ratio": 0.0},
    )
    assert tax_tool_fail["abstention_category"] == "tool_failure"

    tax_insuff = classify_abstention_and_uncertainty(
        final_decision="ABSTAIN",
        decision_explanation="No evidence found.",
        blocked_by_guard=False,
        offline_scope_limited=False,
        grounding_report={"supported_count": 0, "contradicted_count": 0},
        claim_evidence_report={"supported_count": 0, "contradicted_count": 0, "unsupported_count": 0, "insufficient_evidence_count": 1},
        failure_report={"total_failures": 0},
        recovery_report={"failed_recoveries": 0},
        uncertainty_signals={"uncertainty_score": 0.7, "tool_success_ratio": 1.0},
    )
    assert tax_insuff["abstention_category"] == "insufficient_evidence"
    assert set(ABSTENTION_CATEGORIES) >= {
        "grounded_confident", "low_confidence_synthesis", "insufficient_evidence",
        "contradictory_evidence", "tool_failure", "missing_credentials",
        "unanswerable_question", "policy_blocked",
    }


# ---------------------------------------------------------------------------
# Task F & H: Dataset Integrity & Security Regression Guardrails
# ---------------------------------------------------------------------------

def test_task_f_dataset_and_h_security_guardrails(tmp_path: Path) -> None:
    ds = json.loads((PROJECT_ROOT / "data" / "phase7_1_unseen.json").read_text(encoding="utf-8"))
    tasks = ds["tasks"]
    assert 60 <= len(tasks) <= 80
    domains = {t["domain"] for t in tasks}
    assert len(domains) >= 15
    assert any(t.get("expected_contradiction") is True for t in tasks)
    assert any(t.get("scenario") == "multi_paper_metadata_diff" for t in tasks)

    # SSRF / loopback guard via PermissionEngine
    perms = PermissionEngine()
    cfg = ConnectorConfig(id="ssrf-test", name="SSRF Test", category="browser", transport="browser")
    ok, reason = perms.check_url(cfg, "http://127.0.0.1:8000/secret")
    assert ok is False and ("loopback" in reason.lower() or "private" in reason.lower() or "ssrf" in reason.lower())

    # Cross-session isolation and rate limiter sanity
    client = TestClient(web_dashboard.app)
    r1 = client.post("/api/conversations", json={"title": "P71-A"}, headers={"X-Session-ID": "p71-sess-a"})
    assert r1.status_code == 200
    cid = r1.json()["conversation"]["conversation_id"]
    assert client.get(f"/api/conversations/{cid}", headers={"X-Session-ID": "p71-sess-b"}).status_code == 404
