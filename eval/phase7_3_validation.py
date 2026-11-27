"""TraceRecover Phase 7.3 — Live Provider Audit, 6-Scenario Research Traces, Integration Proof & 4-Regime Evaluation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.dynamic_planner import DynamicQueryPlanner
from agent.open_ended_runner import OpenEndedAgentRunner
from agent.provider_adapter import (
    LLMProviderAdapter,
    ProviderAdapterError,
    get_pricing_metadata,
    list_provider_capabilities,
    select_preferred_live_provider,
)
from config import LLMCallMetrics
from connectors.manager import ConnectorManager
from connectors.schemas import ConnectorConfig
from eval.phase7_2_validation import (
    _mock_search_adapter_72,
    _run_live_connector_mode,
    _run_offline_or_mock,
)
from tools.executor import ToolExecutor
from tools.external_search import ExternalSearchAdapter
from tools.failure_injector import FailureInjector


def _extract_scenario_record(label: str, mode_tag: str, out: dict[str, Any]) -> dict[str, Any]:
    claim_rep = out.get("claim_evidence_report") or {}
    claims = claim_rep.get("claims") or []
    struct = out.get("structured_answer") or {}
    ext_srcs = out.get("external_sources") or []
    retrieved = out.get("retrieved_evidence") or []
    return {
        "scenario": label,
        "test_label": mode_tag,
        "original_query": out.get("question"),
        "resolved_query": out.get("resolved_query"),
        "research_plan": out.get("research_plan"),
        "tool_calls": out.get("tools_selected") or [],
        "retrieved_sources": [str(s.get("title") or s.get("source_name") or "") for s in (ext_srcs or retrieved)][:6],
        "source_urls": [str(s.get("url") or s.get("source_url")) for s in (ext_srcs or retrieved) if (s.get("url") or s.get("source_url"))][:6],
        "retrieved_excerpts": [str(e.get("text") or "")[:180] for e in retrieved[:4]],
        "extracted_claims": [c.get("claim_text") for c in claims],
        "verification_codes": [c.get("verification_code") for c in claims],
        "contradictions": struct.get("contradictions") or claim_rep.get("contradiction_details") or [],
        "final_decision": out.get("final_decision"),
        "final_answer": str(out.get("final_response") or "")[:600],
        "citations": struct.get("citations") or [],
        "abstention_category": out.get("abstention_category"),
        "abstention_reason": out.get("abstention_reason"),
        "execution_mode": out.get("execution_mode"),
        "execution_mode_label": out.get("execution_mode_label"),
        "integration_audit": out.get("integration_audit"),
        "trace_id": out.get("trace_id"),
        "trace_path": (out.get("artifacts") or {}).get("trace_path"),
    }


def _run_task1_and_task2_provider_audit() -> dict[str, Any]:
    caps = list_provider_capabilities()
    preferred = select_preferred_live_provider()
    health_by_provider = {
        p: LLMProviderAdapter(provider=p).health_check()
        for p in ("local", "gemini", "openai", "anthropic", "ollama", "local_openai")
    }
    if preferred["live_available"]:
        sel_p = preferred["selected_provider"]
        call_res = health_by_provider[sel_p]
        task2 = {
            "status": "LIVE_EXECUTED" if call_res.get("connection_status") == "connected" else "LIVE_FAILED",
            "label": "LIVE",
            "result": call_res,
        }
    else:
        task2 = {
            "status": "BLOCKED BY MISSING CREDENTIALS",
            "label": "BLOCKED",
            "reason": (
                "No real LLM API key (GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY) or "
                "local model server (OLLAMA_BASE_URL, LOCAL_LLM_BASE_URL) is configured in environment or .env."
            ),
            "gemini_health_check": health_by_provider["gemini"],
        }
    return {
        "capabilities": caps,
        "preferred": preferred,
        "health_checks": health_by_provider,
        "pricing_provenance": {
            m: get_pricing_metadata(m, "local" if m == "deterministic-local" else "remote")
            for m in ("gemini-2.0-flash", "gpt-4o-mini", "claude-3-5-haiku-latest", "llama3.1", "deterministic-local")
        },
        "task2_real_provider_call": task2,
    }


def _run_task3_six_scenarios(tmp_dir: Path) -> list[dict[str, Any]]:
    mgr = ConnectorManager(storage_dir=tmp_dir / "t3_conn")
    live_runner = OpenEndedAgentRunner(connector_manager=mgr, log_directory=tmp_dir / "t3_live_logs")
    mock_runner = OpenEndedAgentRunner(
        search_adapter=_mock_search_adapter_72(),
        connector_manager=mgr,
        log_directory=tmp_dir / "t3_mock_logs",
    )

    scenarios: list[dict[str, Any]] = []

    # A. Technical documentation (LIVE Chromium browser extraction of official Python dataclasses docs)
    b_cfg = ConnectorConfig(id="t3-docs-browser", name="Python Docs Browser", category="browser", transport="browser")
    b_cfg.permissions.allowed_domains = ["docs.python.org"]
    b_out = mgr.browser.execute_sync(
        action="read_content",
        url="https://docs.python.org/3/library/dataclasses.html",
        engine="chromium",
        connector=b_cfg,
    )
    scenarios.append(
        {
            "scenario": "A. Technical documentation (Python dataclasses frozen=True)",
            "test_label": "LIVE CONNECTOR",
            "original_query": "Inspect Python official dataclasses documentation for frozen=True immutability semantics",
            "resolved_query": "https://docs.python.org/3/library/dataclasses.html",
            "tool_calls": ["browser_inspect (chromium)"],
            "retrieved_sources": [b_out.get("title") or ""],
            "source_urls": [b_out.get("final_url") or "https://docs.python.org/3/library/dataclasses.html"],
            "retrieved_excerpts": [str(b_out.get("content_excerpt") or "")[:240]],
            "ok": bool(b_out.get("ok")),
            "frozen_mentioned": "frozen" in str(b_out.get("content_excerpt") or "").lower(),
            "latency_ms": b_out.get("latency_ms"),
            "execution_mode_label": "LIVE CONNECTOR",
        }
    )

    # B. Academic research (>=2 independent live academic sources: arXiv + Crossref/OpenAlex)
    res_b = live_runner.run(
        "Find arXiv and Crossref academic papers on conformal prediction uncertainty calibration",
        provider="local",
        enable_external_search=True,
        use_connectors=True,
    )
    scenarios.append(_extract_scenario_record("B. Academic research (2+ live sources)", "LIVE CONNECTOR", res_b))

    # C. Multi-step comparative reasoning (live external retrieval)
    res_c = live_runner.run(
        "Compare Raft consensus versus Paxos using Wikipedia and academic references",
        provider="local",
        enable_external_search=True,
        use_connectors=True,
    )
    scenarios.append(_extract_scenario_record("C. Multi-step comparative research", "LIVE CONNECTOR", res_c))

    # D. Contradictory evidence (controlled genuinely conflicting factual sources)
    res_d = mock_runner.run(
        "Verify conflicting advisories claiming TLS 1.3 RFC 8446 was published in 2018 versus 2023",
        provider="local",
        enable_external_search=True,
        use_connectors=False,
    )
    scenarios.append(_extract_scenario_record("D. Contradictory evidence detection", "MOCK", res_d))

    # E. Insufficient evidence (live connector query for non-existent confidential benchmark)
    res_e = live_runner.run(
        "What unreleased benchmark score will private internal model KESTREL-9X record next year?",
        provider="local",
        enable_external_search=True,
        use_connectors=True,
    )
    scenarios.append(_extract_scenario_record("E. Insufficient evidence abstention", "LIVE CONNECTOR", res_e))

    # F. Numerical / temporal reasoning (multi-step local ticket + exact calculation)
    res_f = live_runner.run(
        "Look up ticket T-401 and compute (1200 * 0.85) after a 15% SLA credit",
        provider="local",
        enable_external_search=False,
        use_connectors=False,
    )
    scenarios.append(_extract_scenario_record("F. Numerical / temporal reasoning", "OFFLINE", res_f))
    return scenarios


def _run_task4_llm_connector_integration_proof(tmp_dir: Path) -> dict[str, Any]:
    """Prove LLM planner + LIVE connector execution + post-synthesis claim verification using a controlled mock LLM client."""
    mgr = ConnectorManager(storage_dir=tmp_dir / "t4_conn")
    runner = OpenEndedAgentRunner(connector_manager=mgr, log_directory=tmp_dir / "t4_logs")

    class _IntegrationMockLLM:
        provider = "gemini"
        model = "gemini-2.0-flash"

        def __init__(self) -> None:
            self.calls = 0
            self.prompts: list[str] = []

        def usage_summary(self) -> dict[str, Any]:
            return {
                "provider": self.provider,
                "model": self.model,
                "call_count": self.calls,
                "prompt_tokens": self.calls * 120,
                "completion_tokens": self.calls * 35,
                "total_tokens": self.calls * 155,
                "total_latency_seconds": 0.04,
                "estimated_cost_usd": 0.00004,
                "usage_source": "estimated",
                **get_pricing_metadata(self.model, self.provider),
            }

        def complete_with_metadata(self, prompt: str) -> tuple[str, LLMCallMetrics]:
            self.calls += 1
            self.prompts.append(prompt)
            if self.calls == 1:
                payload = {
                    "thought": "Need peer-reviewed academic evidence via live research_sources.",
                    "action": "research_sources",
                    "arguments": {"query": "conformal prediction uncertainty calibration"},
                    "final_answer": None,
                }
            else:
                # Synthesize one evidence-grounded claim + one deliberate ungrounded claim to prove post-synthesis verifier catches it
                payload = {
                    "thought": "Synthesize retrieved conformal prediction papers into verifiable claims.",
                    "action": "final",
                    "arguments": {},
                    "final_answer": (
                        "Conformal prediction provides uncertainty calibration for machine learning models. "
                        "Mars moon Phobos hosts subterranean liquid oceans."
                    ),
                }
            return (
                json.dumps(payload),
                LLMCallMetrics(
                    provider=self.provider,
                    model=self.model,
                    prompt_tokens=120,
                    completion_tokens=35,
                    total_tokens=155,
                    latency_seconds=0.02,
                    estimated_cost_usd=0.00002,
                ),
            )

    stub = _IntegrationMockLLM()
    out = runner.run(
        "Research conformal prediction uncertainty calibration using academic papers",
        provider="gemini",
        client=stub,
        enable_external_search=True,
        use_connectors=True,
    )
    audit = out.get("integration_audit") or {}
    claim_rep = out.get("claim_evidence_report") or {}
    claims = claim_rep.get("claims") or []
    return {
        "label": "MOCK_LLM + LIVE_CONNECTOR",
        "note": "Uses a controlled stubbed Gemini client paired with REAL live research_sources HTTP connectors to prove pipeline wiring without fabricating real Gemini API traffic.",
        "prompt_fenced_with_untrusted_tags": all("<UNTRUSTED_EVIDENCE_DATA>" in p for p in stub.prompts),
        "second_prompt_received_live_evidence": len(stub.prompts) >= 2 and ("arxiv" in stub.prompts[1].lower() or "doi" in stub.prompts[1].lower()),
        "integration_audit": audit,
        "claims_summary": [
            {"claim_id": c.get("claim_id"), "status": c.get("verification_code"), "claim": c.get("claim_text")}
            for c in claims
        ],
        "hallucinated_claim_caught": any(
            "phobos" in str(c.get("claim_text") or "").lower() and c.get("verification_code") == "UNSUPPORTED"
            for c in claims
        ),
        "external_sources_count": len(out.get("external_sources") or []),
        "final_decision": out.get("final_decision"),
        "execution_mode_label": out.get("execution_mode_label"),
        "trace_id": out.get("trace_id"),
    }


def _run_task5_nine_failure_scenarios(tmp_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    old_env = {k: os.environ.get(k) for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "OLLAMA_BASE_URL")}
    try:
        # 1. Invalid API key
        os.environ["OPENAI_API_KEY"] = "changeme"
        h_inv = LLMProviderAdapter(provider="openai").health_check()
        rows.append({"scenario": "1. Invalid API key", "label": "OFFLINE", "passed": h_inv["error_category"] == "invalid_api_key", "category": h_inv["error_category"]})

        # 2-3. Timeout & rate limit
        os.environ["OPENAI_API_KEY"] = "sk-validformat1234567890"
        class _ErrC:
            def __init__(self, exc=None, reply="OK"):
                self.provider, self.model, self.exc, self.reply = "openai", "gpt-4o-mini", exc, reply
            def usage_summary(self): return {"call_count": 0, "estimated_cost_usd": 0.0}
            def complete_with_metadata(self, p):
                if self.exc: raise self.exc
                return (self.reply, LLMCallMetrics(provider="openai", model="gpt-4o-mini", prompt_tokens=1, completion_tokens=0, total_tokens=1, latency_seconds=0.01, estimated_cost_usd=0.0))

        for name, exc_o, exp_c in (
            ("2. Provider timeout", TimeoutError("timed out"), "timeout"),
            ("3. Provider rate limit", RuntimeError("HTTP 429 rate limit"), "rate_limited"),
        ):
            try:
                LLMProviderAdapter(client=_ErrC(exc=exc_o)).complete_with_budget("hi")
                got = "unexpected_pass"
            except ProviderAdapterError as e:
                got = e.category
            rows.append({"scenario": name, "label": "MOCK", "passed": got == exp_c, "category": got})

        # 4. Malformed response (bounded 1 repair retry -> 2 calls max)
        class _BadJ:
            provider, model, calls = "openai", "gpt-4o-mini", 0
            def usage_summary(self): return {"call_count": self.calls, "estimated_cost_usd": 0.0}
            def complete_with_metadata(self, p):
                self.calls += 1
                return ("{broken json", LLMCallMetrics(provider="openai", model="gpt-4o-mini", prompt_tokens=1, completion_tokens=1, total_tokens=2, latency_seconds=0.01, estimated_cost_usd=0.0))
        bad_j = _BadJ()
        pl = DynamicQueryPlanner()
        tex = ToolExecutor.with_default_tools()
        an = pl.analyze_query("What is 2 + 2?", available_tools=tex.list_tools(), provider="openai")
        try:
            pl.next_action("What is 2 + 2?", [], analysis=an, client=bad_j, tool_executor=tex, max_llm_calls=4)
            got_m = "unexpected_pass"
        except ProviderAdapterError as e:
            got_m = e.category
        rows.append({"scenario": "4. Malformed response (bounded retry)", "label": "MOCK", "passed": got_m == "malformed_output" and bad_j.calls == 2, "category": got_m, "calls": bad_j.calls})

        # 5. Empty response
        try:
            LLMProviderAdapter(client=_ErrC(reply="   ")).complete_with_budget("hi")
            got_e = "unexpected_pass"
        except ProviderAdapterError as e:
            got_e = e.category
        rows.append({"scenario": "5. Empty response", "label": "MOCK", "passed": got_e == "empty_response", "category": got_e})

        # 6. Connector unavailable (search adapter raises error -> graceful abstention, zero fabricated citations)
        def _broken_search(q: str, n: int):
            raise RuntimeError("Upstream search connector unavailable (503)")
        r_unav = OpenEndedAgentRunner(
            search_adapter=ExternalSearchAdapter(provider="auto", provider_fn=_broken_search),
            log_directory=tmp_dir / "t5_unav",
        ).run("Find latest release notes for nonexistent-framework-z99", enable_external_search=True)
        rows.append({
            "scenario": "6. Connector unavailable",
            "label": "MOCK",
            "passed": r_unav["final_decision"] == "ABSTAIN" and len(r_unav.get("external_sources") or []) == 0,
            "category": r_unav.get("abstention_category"),
        })

        # 7 & 8. Insufficient evidence & contradictory evidence
        mr = OpenEndedAgentRunner(search_adapter=_mock_search_adapter_72(), log_directory=tmp_dir / "t5_mc")
        r_ins = mr.run("What unreleased benchmark score will private internal model KESTREL-9X record?", enable_external_search=True)
        r_con = mr.run("Verify conflicting advisories claiming TLS 1.3 RFC 8446 was published in 2018 versus 2023", enable_external_search=True)
        rows.append({"scenario": "7. Insufficient evidence", "label": "MOCK", "passed": r_ins["final_decision"] == "ABSTAIN" and r_ins["abstention_category"] == "insufficient_evidence", "category": r_ins["abstention_category"]})
        rows.append({"scenario": "8. Contradictory evidence", "label": "MOCK", "passed": r_con["final_decision"] == "ABSTAIN" and r_con["abstention_category"] == "contradictory_evidence", "category": r_con["abstention_category"]})

        # 9. Budget exhaustion
        class _BudC:
            provider, model = "openai", "gpt-4o-mini"
            def usage_summary(self): return {"call_count": 2, "estimated_cost_usd": 0.01}
        try:
            LLMProviderAdapter(client=_BudC(), max_llm_calls=2).complete_with_budget("hi")
            got_b = "unexpected_pass"
        except ProviderAdapterError as e:
            got_b = e.category
        rows.append({"scenario": "9. Budget exhaustion", "label": "MOCK", "passed": got_b == "budget_exceeded", "category": got_b})
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return rows


def main() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = PROJECT_ROOT / "results" / f"phase7_3-validation-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset = json.loads((PROJECT_ROOT / "data" / "phase7_2_live_unseen.json").read_text(encoding="utf-8"))
    tasks = dataset["tasks"]

    provider_audit = _run_task1_and_task2_provider_audit()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        task3_scenarios = _run_task3_six_scenarios(tmp_dir)
        task4_integration = _run_task4_llm_connector_integration_proof(tmp_dir)
        task5_failures = _run_task5_nine_failure_scenarios(tmp_dir)

        reg_a = _run_offline_or_mock(tasks, mode="OFFLINE", tmp_dir=tmp_dir)
        reg_b = _run_offline_or_mock(tasks, mode="MOCK", tmp_dir=tmp_dir)
        reg_c = _run_live_connector_mode(tmp_dir=tmp_dir)
        reg_d = {
            "mode": "LIVE CONNECTOR + LIVE LLM",
            "status": "BLOCKED BY MISSING CREDENTIALS",
            "label": "BLOCKED",
            "questions_attempted": 0,
            "answered_count": 0,
            "abstained_count": 0,
            "failed_count": 0,
            "blocked_count": len(tasks),
            "overall_accuracy": None,
            "answered_accuracy": None,
            "reason": provider_audit["task2_real_provider_call"].get("reason"),
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "7.3",
        "overall_status": "PARTIALLY_COMPLETE_BLOCKED_ON_LIVE_LLM_CREDENTIALS" if not provider_audit["preferred"]["live_available"] else "COMPLETE",
        "provider_audit": provider_audit,
        "task3_research_scenarios": task3_scenarios,
        "task4_integration_proof": task4_integration,
        "task5_failure_validation": task5_failures,
        "task8_four_regime_evaluation": {
            "dataset": "data/phase7_2_live_unseen.json",
            "regime_a_offline": reg_a,
            "regime_b_mock": reg_b,
            "regime_c_live_connectors": reg_c,
            "regime_d_live_llm_and_connectors": reg_d,
        },
    }
    (out_dir / "phase7_3_validation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    md = [
        "# TraceRecover Phase 7.3 Evaluation & Integration Summary",
        f"- Generated: `{report['generated_at']}`",
        f"- Overall Status: `{report['overall_status']}`",
        f"- Dataset Preserved: `data/phase7_2_live_unseen.json` (`{len(tasks)}` questions across 14 domains)",
        "",
        "## Task 8 — Four Separate Regimes (Never Merged)",
        "",
        "| Metric | Regime A: OFFLINE (48q) | Regime B: MOCK (48q) | Regime C: LIVE CONNECTOR (12q) | Regime D: LIVE LLM + CONNECTOR |",
        "|---|---:|---:|---:|---:|",
        f"| Attempted / Answered / Abstained / Failed / Blocked | {reg_a['total_questions']} / {reg_a['answered_count']} / {reg_a['abstained_count']} / {reg_a['failed_count']} / {reg_a['blocked_count']} | {reg_b['total_questions']} / {reg_b['answered_count']} / {reg_b['abstained_count']} / {reg_b['failed_count']} / {reg_b['blocked_count']} | {reg_c['total_questions']} / {reg_c['answered_count']} / {reg_c['abstained_count']} / {reg_c['failed_count']} / {reg_c['blocked_count']} | 0 / 0 / 0 / 0 / {reg_d['blocked_count']} (BLOCKED) |",
        f"| Overall Accuracy | {reg_a['overall_accuracy']} | {reg_b['overall_accuracy']} | {reg_c['overall_accuracy']} | N/A |",
        f"| Answered Accuracy | {reg_a['answered_accuracy']} | {reg_b['answered_accuracy']} | {reg_c['answered_accuracy']} | N/A |",
        f"| Evidence Support Rate (Answered) | {reg_a['evidence_support_rate']} | {reg_b['evidence_support_rate']} | {reg_c['evidence_support_rate']} | N/A |",
        f"| Unsupported Claim Rate (Answered) | {reg_a['unsupported_claim_rate']} | {reg_b['unsupported_claim_rate']} | {reg_c['unsupported_claim_rate']} | N/A |",
        f"| Citation Precision / Coverage | {reg_a['citation_precision']} / {reg_a['citation_coverage']} | {reg_b['citation_precision']} / {reg_b['citation_coverage']} | {reg_c['citation_precision']} / {reg_c['citation_coverage']} | N/A |",
        f"| Contradiction Precision / Recall | N/A | {reg_b['contradiction_precision']} / {reg_b['contradiction_recall']} | {reg_c['contradiction_precision']} / {reg_c['contradiction_recall']} | N/A |",
        f"| Recovery Success Rate | {reg_a['recovery_success_rate']} | {reg_b['recovery_success_rate']} | {reg_c['recovery_success_rate']} | N/A |",
        f"| Avg Latency (ms) | {reg_a['avg_latency_ms']} | {reg_b['avg_latency_ms']} | {reg_c['avg_latency_ms']} | N/A |",
        "",
        "## Task 4 — LLM + Live Connector Integration Proof (`MOCK_LLM + LIVE_CONNECTOR`)",
        f"- Planner called model: `{task4_integration['integration_audit'].get('planner_called_model')}`",
        f"- Live connectors executed: `{task4_integration['integration_audit'].get('connectors_executed')}`",
        f"- Live external sources retrieved: `{task4_integration['external_sources_count']}`",
        f"- Untrusted evidence fencing verified: `{task4_integration['prompt_fenced_with_untrusted_tags']}`",
        f"- Live evidence reached model step 2: `{task4_integration['second_prompt_received_live_evidence']}`",
        f"- Post-synthesis verifier caught injected hallucination: `{task4_integration['hallucinated_claim_caught']}`",
        "",
        "## Task 5 — Controlled Failure Validation (`9 / 9` Scenarios)",
        "",
        "| Scenario | Label | Passed | Category |",
        "|---|---|---|---|",
    ]
    for r in task5_failures:
        md.append(f"| {r['scenario']} | `{r['label']}` | `{r['passed']}` | `{r.get('category')}` |")

    (out_dir / "phase7_3_validation_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("PHASE7_3_REPORT_DIR:", out_dir)
    return out_dir


if __name__ == "__main__":
    main()
