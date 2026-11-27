"""Phase 7.3 Pricing Provenance, LLM+Connector Integration Audit, Failure Guardrails & Dashboard Cost Tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.open_ended_runner import OpenEndedAgentRunner
from agent.provider_adapter import (
    LLMProviderAdapter,
    PRICING_RETRIEVED_DATE,
    PRICING_SOURCE,
    get_pricing_metadata,
)
from config import LLMCallMetrics
from tools.external_search import ExternalSearchAdapter


def test_task6_pricing_provenance_known_local_and_unknown_models(monkeypatch: pytest.MonkeyPatch) -> None:
    loc = get_pricing_metadata("deterministic-local", "local")
    assert loc["pricing_known"] is True
    assert loc["cost_estimation_status"] == "zero_local"
    assert loc["pricing_source"] == PRICING_SOURCE
    assert loc["pricing_retrieved_date"] == PRICING_RETRIEVED_DATE

    gem = get_pricing_metadata("gemini-2.0-flash", "gemini")
    assert gem["pricing_known"] is True
    assert gem["cost_estimation_status"] == "known_pricing_estimated"
    assert gem["input_per_1m_usd"] == 0.10

    unk = get_pricing_metadata("custom-unlisted-model-70b", "local_openai")
    assert unk["pricing_known"] is False
    assert unk["cost_estimation_status"] == "unknown_pricing"
    assert unk["input_per_1m_usd"] is None

    # Unknown model health check MUST NOT invent an exact USD cost
    class _UnlistedClient:
        provider = "openai"
        model = "custom-unlisted-model-70b"
        def usage_summary(self):
            return {"call_count": 0, "estimated_cost_usd": 0.0}
        def complete_with_metadata(self, p: str):
            return (
                "OK",
                LLMCallMetrics(
                    provider="openai",
                    model="custom-unlisted-model-70b",
                    prompt_tokens=10,
                    completion_tokens=2,
                    total_tokens=12,
                    latency_seconds=0.01,
                    estimated_cost_usd=0.0,
                ),
            )

    monkeypatch.setenv("OPENAI_API_KEY", "sk-validformat1234567890")
    hc = LLMProviderAdapter(client=_UnlistedClient()).health_check()
    assert hc["cost_estimation_status"] == "unknown_pricing"
    assert hc["token_usage"]["estimated_cost_usd"] is None


def test_task4_llm_planner_connector_synthesis_and_post_verifier_audit(tmp_path: Path) -> None:
    def _mock_search(q: str, n: int):
        return [
            {
                "title": "Python 3.12 Official Docs",
                "url": "https://docs.python.org/3/whatsnew/3.12.html",
                "snippet": "Python 3.12 was released in October 2023 and introduced TaskGroup structured concurrency.",
                "provider": "mock",
            }
        ]

    class _TwoStepLLM:
        provider = "gemini"
        model = "gemini-2.0-flash"
        def __init__(self):
            self.calls = 0
            self.prompts: list[str] = []
        def usage_summary(self):
            return {
                "provider": self.provider,
                "model": self.model,
                "call_count": self.calls,
                "prompt_tokens": self.calls * 50,
                "completion_tokens": self.calls * 20,
                "total_tokens": self.calls * 70,
                "total_latency_seconds": 0.02,
                "estimated_cost_usd": 0.00001,
                "usage_source": "provider_reported",
            }
        def complete_with_metadata(self, prompt: str):
            self.calls += 1
            self.prompts.append(prompt)
            if self.calls == 1:
                out = {
                    "thought": "Query official Python 3.12 documentation via external_search.",
                    "action": "external_search",
                    "arguments": {"query": "Python 3.12 release date TaskGroup"},
                    "final_answer": None,
                }
            else:
                out = {
                    "thought": "Synthesize retrieved documentation into verifiable claims.",
                    "action": "final",
                    "arguments": {},
                    "final_answer": (
                        "Python 3.12 was released in October 2023 and introduced TaskGroup structured concurrency. "
                        "Mars moon Phobos hosts liquid nitrogen rivers."
                    ),
                }
            return (
                json.dumps(out),
                LLMCallMetrics(
                    provider=self.provider,
                    model=self.model,
                    prompt_tokens=50,
                    completion_tokens=20,
                    total_tokens=70,
                    latency_seconds=0.01,
                    estimated_cost_usd=0.000005,
                ),
            )

    runner = OpenEndedAgentRunner(
        search_adapter=ExternalSearchAdapter(provider="auto", provider_fn=_mock_search),
        log_directory=tmp_path / "logs",
    )
    llm = _TwoStepLLM()
    res = runner.run(
        "When was Python 3.12 released?",
        provider="gemini",
        client=llm,
        enable_external_search=True,
    )
    audit = res["integration_audit"]
    assert audit["planner_called_model"] is True
    assert "external_search" in audit["connectors_executed"]
    assert audit["retrieved_evidence_count"] >= 1
    assert audit["retrieved_evidence_reached_synthesis"] is True
    assert audit["verifier_evaluated_post_synthesis"] is True
    assert audit["citations_traceable_to_corpus"] is True
    assert audit["unsupported_or_contradicted_flagged"] >= 1
    assert all("<UNTRUSTED_EVIDENCE_DATA>" in p for p in llm.prompts)
    assert "October 2023" in llm.prompts[1]
    assert res["execution_mode_label"] == "MOCK"
    assert res["provider_info"]["cost_estimation_status"] == "known_pricing_estimated"
