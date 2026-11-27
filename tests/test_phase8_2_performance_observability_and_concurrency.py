"""Phase 8.2 regression tests: observability, operational health, storage caching, concurrency, cancellation & resource bounds."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.conversation_store import ConversationStore
from agent.open_ended_runner import OpenEndedAgentRunner, QueryCancelledError
from eval.phase8_2_performance import run_benchmark
import web_dashboard


@pytest.fixture()
def isolated_p82_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    conv_dir = tmp_path / "conversations"
    logs_dir = tmp_path / "logs"
    conn_dir = tmp_path / "connectors"
    for d in (conv_dir, logs_dir, conn_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(web_dashboard, "CONVERSATIONS_DIR", conv_dir)
    monkeypatch.setattr(web_dashboard, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(web_dashboard, "CONNECTORS_DIR", conn_dir)
    monkeypatch.setenv("TRACERECOVER_CONNECTORS_DIR", str(conn_dir))
    monkeypatch.setenv("TRACERECOVER_RATE_LIMIT_RUN", "200")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
    web_dashboard._CONVERSATION_STORE_CACHE.clear()
    web_dashboard._CONNECTOR_MANAGER_CACHE.clear()
    web_dashboard._clear_artifact_caches()
    web_dashboard.reset_rate_limiter()
    yield TestClient(web_dashboard.app)
    web_dashboard._CONVERSATION_STORE_CACHE.clear()
    web_dashboard._CONNECTOR_MANAGER_CACHE.clear()
    web_dashboard._clear_artifact_caches()
    web_dashboard.reset_rate_limiter()


def test_observability_stage_timings_and_redacted_structured_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Task D: Verify stage timings, trace/session correlation, and zero secret leakage in structured logs."""
    caplog.set_level(logging.INFO, logger="tracerecover.observability")
    runner = OpenEndedAgentRunner(log_directory=tmp_path)
    res = runner.run(
        "What is 19 * 23 + 4?",
        provider="local",
        failure_rate=40.0,
        seed=13,
        session_id="sensitive-user-session-abc",
    )

    obs = res["observability"]
    assert obs["trace_id"] == res["trace_id"]
    assert obs["query_id"] == res["query_id"]
    assert obs["session_ref"] and obs["session_ref"].startswith("sess_")
    assert "sensitive-user-session-abc" not in str(obs["session_ref"])
    assert obs["execution_mode"] == "offline_deterministic"
    assert obs["started_at"] <= obs["completed_at"]
    assert obs["total_duration_ms"] >= 0.0
    timings = obs["stage_timings_ms"]
    for key in (
        "intent_and_planning_ms",
        "tool_and_connector_ms",
        "failure_detection_and_recovery_ms",
        "synthesis_ms",
        "evidence_verification_ms",
        "claim_verification_ms",
        "uncertainty_ms",
    ):
        assert key in timings and timings[key] >= 0.0
    assert obs["tool_call_summary"]["total_calls"] >= 1
    assert obs["recovery_outcome"]["successful"] >= 1
    assert obs["token_usage"]["usage_source"] == "none"

    events = [json.loads(r.message) for r in caplog.records if r.name == "tracerecover.observability"]
    event_names = [e.get("event") for e in events]
    assert "query.started" in event_names
    assert "query.completed" in event_names
    completed_evt = next(e for e in events if e.get("event") == "query.completed")
    assert completed_evt["trace_id"] == res["trace_id"]
    assert "sensitive-user-session-abc" not in json.dumps(events)


def test_health_endpoint_honest_subsystem_readiness_and_no_host_path_leakage(
    isolated_p82_client: TestClient,
) -> None:
    """Task E: Verify /api/health distinguishes installed/configured/operational and hides host paths."""
    r = isolated_p82_client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    subs = body["subsystems"]
    assert subs["storage"]["operational"] is True
    assert subs["storage"]["single_node_limitation"] is True
    assert subs["execution_modes"]["offline"]["operational"] is True
    assert subs["execution_modes"]["mock"]["operational"] is True
    assert subs["execution_modes"]["live_llm"]["configured"] is False
    assert subs["execution_modes"]["live_llm"]["operational"] is False
    assert subs["connectors"]["installed"] is True
    assert subs["connectors"]["operational_count"] <= subs["connectors"]["enabled_count"]
    assert subs["mcp_runtime"]["installed"] is True
    assert subs["mcp_runtime"]["connected_servers"] == 0
    assert subs["mcp_runtime"]["operational"] is False

    raw = json.dumps(body)
    assert "C:\\\\" not in raw
    assert "C:/" not in raw
    assert "chrome.exe" not in raw.lower()


def test_storage_summary_cache_consistency_and_retention(tmp_path: Path) -> None:
    """Task G: Verify mtime-validated summary cache stays consistent across external edits, deletes & retention."""
    store = ConversationStore(tmp_path / "conv", max_conversations_per_session=5)
    created_ids: list[str] = []
    for i in range(7):
        c = store.create_conversation("sess-ret", title=f"Conv {i}")
        created_ids.append(c["conversation_id"])
        time.sleep(0.015)

    listed = store.list_conversations("sess-ret")
    assert len(listed) == 5  # Retention enforced

    # External disk modification must invalidate cached summary immediately
    target_id = listed[0]["conversation_id"]
    target_path = tmp_path / "conv" / "sess-ret" / f"{target_id}.json"
    rec = json.loads(target_path.read_text(encoding="utf-8"))
    rec["title"] = "Externally Updated Title"
    time.sleep(0.02)
    target_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    os.utime(target_path, None)

    relisted = store.list_conversations("sess-ret")
    matched = next(x for x in relisted if x["conversation_id"] == target_id)
    assert matched["title"] == "Externally Updated Title"

    # Deletion must evict from cache immediately
    assert store.delete_conversation("sess-ret", target_id) is True
    after_del = store.list_conversations("sess-ret")
    assert all(x["conversation_id"] != target_id for x in after_del)
    assert len(list((tmp_path / "conv").rglob("*.tmp"))) == 0


def test_concurrency_cancellation_and_delete_during_load(
    isolated_p82_client: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Task C: Verify multi-session load, delete during concurrent ops, and clean cancellation."""
    caplog.set_level(logging.INFO, logger="tracerecover.observability")

    # 1. Pre-cancelled query aborts cleanly without corrupting state
    cancel_ev = threading.Event()
    cancel_ev.set()
    runner = OpenEndedAgentRunner(log_directory=tmp_path / "cancel_logs")
    with pytest.raises(QueryCancelledError):
        runner.run("What is 2 + 2?", provider="local", cancel_event=cancel_ev, session_id="cancel-sess")
    events = [json.loads(r.message) for r in caplog.records if r.name == "tracerecover.observability"]
    assert any(e.get("event") == "query.cancelled" for e in events)

    # 2. Concurrent multi-session queries + delete on an unrelated thread
    disposable = isolated_p82_client.post(
        "/api/conversations", json={"title": "To Delete"}, headers={"X-Session-ID": "sess-del"}
    ).json()["conversation"]["conversation_id"]

    def _run_session(idx: int) -> tuple[int, str]:
        sid = f"p82-worker-{idx}"
        r = isolated_p82_client.post(
            "/api/chat/query",
            json={"question": f"What is {idx + 5} * 6?", "provider": "local"},
            headers={"X-Session-ID": sid},
        )
        return r.status_code, r.json().get("final_decision", "")

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futs = [pool.submit(_run_session, i) for i in range(4)]
        del_fut = pool.submit(
            lambda: isolated_p82_client.delete(
                f"/api/conversations/{disposable}", headers={"X-Session-ID": "sess-del"}
            ).status_code
        )
        for f in futs:
            status, dec = f.result()
            assert status == 200 and dec == "ANSWER"
        assert del_fut.result() == 200


def test_resource_bounds_and_benchmark_script_smoke(isolated_p82_client: TestClient) -> None:
    """Task I & H: Verify API resource bounds and reproducible performance benchmark script in OFFLINE+MOCK modes."""
    # Rejects excessive max_steps, max_llm_calls, max_cost_usd, and oversized question
    for bad_body in (
        {"question": "hi", "max_steps": 99},
        {"question": "hi", "max_llm_calls": 99},
        {"question": "hi", "max_cost_usd": 50.0},
        {"question": "x" * 5000},
    ):
        resp = isolated_p82_client.post("/api/chat/query", json=bad_body)
        assert resp.status_code == 422

    # Smoke-run the reproducible benchmark harness (non-browser fast mode)
    report = run_benchmark(label="smoke", include_browser=False)
    assert report["environment"]["live_llm_used"] is False
    assert report["offline_execution"]["failed_requests"] == 0
    assert report["mock_execution"]["failed_requests"] == 0
    assert report["concurrent_sessions"]["session_isolation_verified"] is True
    assert report["storage_scaling"]["leftover_tmp_files"] == 0
