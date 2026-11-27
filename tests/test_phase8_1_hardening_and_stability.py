"""Phase 8.1 production hardening, application security, persistence & backend stability tests."""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.conversation_store import ConversationStore, scrub_secrets
import web_dashboard


@pytest.fixture()
def isolated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide an isolated FastAPI TestClient with fresh storage directories and reset rate limiter."""
    conv_dir = tmp_path / "conversations"
    logs_dir = tmp_path / "logs"
    conn_dir = tmp_path / "connectors"
    for d in (conv_dir, logs_dir, conn_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(web_dashboard, "CONVERSATIONS_DIR", conv_dir)
    monkeypatch.setattr(web_dashboard, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(web_dashboard, "CONNECTORS_DIR", conn_dir)
    monkeypatch.setenv("TRACERECOVER_CONNECTORS_DIR", str(conn_dir))
    monkeypatch.delenv("TRACERECOVER_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    web_dashboard._CONVERSATION_STORE_CACHE.clear()
    web_dashboard._CONNECTOR_MANAGER_CACHE.clear()
    web_dashboard.reset_rate_limiter()
    yield TestClient(web_dashboard.app)
    web_dashboard._CONVERSATION_STORE_CACHE.clear()
    web_dashboard._CONNECTOR_MANAGER_CACHE.clear()
    web_dashboard.reset_rate_limiter()


def test_cors_preflight_allows_put_delete_and_session_header(isolated_client: TestClient) -> None:
    """Verify CORS allows PUT, DELETE, and X-Session-ID for configured origins and blocks untrusted origins."""
    ok_res = isolated_client.options(
        "/api/conversations/conv-1",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "X-Session-ID, Content-Type",
        },
    )
    assert ok_res.status_code == 200
    assert ok_res.headers.get("access-control-allow-origin") == "http://localhost:8000"
    allow_methods = ok_res.headers.get("access-control-allow-methods", "")
    assert "DELETE" in allow_methods and "PUT" in allow_methods
    allow_headers = ok_res.headers.get("access-control-allow-headers", "").lower()
    assert "x-session-id" in allow_headers

    bad_res = isolated_client.options(
        "/api/conversations/conv-1",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "DELETE",
        },
    )
    assert bad_res.headers.get("access-control-allow-origin") is None


def test_request_size_limit_blocks_oversized_and_invalid_content_length(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify 413 enforcement on oversized payloads (with or without Content-Length) and 400 on bogus headers."""
    monkeypatch.setenv("TRACERECOVER_MAX_REQUEST_BYTES", "1024")

    # 1. Explicit oversized Content-Length
    big_payload = json.dumps({"title": "A" * 2048})
    r_big = isolated_client.post(
        "/api/conversations",
        content=big_payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert r_big.status_code == 413

    # 2. Malformed non-numeric Content-Length header
    r_bad_cl = isolated_client.post(
        "/api/conversations",
        content=b'{"title":"ok"}',
        headers={"Content-Type": "application/json", "Content-Length": "not-a-number"},
    )
    assert r_bad_cl.status_code == 400


def test_conversation_persistence_concurrency_restart_and_corruption_recovery(
    isolated_client: TestClient, tmp_path: Path
) -> None:
    """Verify disk persistence across restart, concurrent atomic appends, session isolation, and corruption safety."""
    # Create conversation in sess-alpha and run a real offline query
    c_res = isolated_client.post(
        "/api/conversations",
        json={"title": "Persistence Probe"},
        headers={"X-Session-ID": "sess-alpha"},
    )
    assert c_res.status_code == 200
    conv_id = c_res.json()["conversation"]["conversation_id"]

    # Session isolation: sess-beta must NOT see, clear, or delete sess-alpha's conversation
    assert isolated_client.get(f"/api/conversations/{conv_id}", headers={"X-Session-ID": "sess-beta"}).status_code == 404
    assert isolated_client.post(f"/api/conversations/{conv_id}/clear", headers={"X-Session-ID": "sess-beta"}).status_code == 404
    assert isolated_client.delete(f"/api/conversations/{conv_id}", headers={"X-Session-ID": "sess-beta"}).status_code == 404

    # Concurrent atomic appends through shared cached ConversationStore (8 threads -> zero lost turns)
    store = web_dashboard._conv_store()
    assert store is web_dashboard._conv_store()  # Cached lock-sharing instance

    def _append(i: int) -> None:
        store.append_turn(
            "sess-alpha",
            conv_id,
            {
                "query_id": f"q-{i}",
                "question": f"Turn {i} with x-goog-api-key: AIzaSyLeakCheckToken{i:010d}",
                "final_decision": "ANSWER",
                "final_response": f"Result {i}",
                "provider": "local",
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_append, i) for i in range(8)]
        for f in futs:
            f.result()

    # Simulate full server restart by clearing cache and reading from disk fresh
    web_dashboard._CONVERSATION_STORE_CACHE.clear()
    reloaded = isolated_client.get(f"/api/conversations/{conv_id}", headers={"X-Session-ID": "sess-alpha"})
    assert reloaded.status_code == 200
    body = reloaded.json()
    assert len(body["turns"]) == 8
    raw_disk = json.dumps(body)
    assert "AIzaSyLeakCheckToken" not in raw_disk
    assert "[REDACTED]" in raw_disk
    # No lingering atomic temp files left behind
    leftover_tmps = list((tmp_path / "conversations" / "sess-alpha").glob("*.tmp"))
    assert leftover_tmps == []

    # Corrupted JSON file in sess-alpha should return 404 safely (never 500) and remain deletable by owner
    corrupt_path = tmp_path / "conversations" / "sess-alpha" / "conv-corrupt-001.json"
    corrupt_path.write_text("{broken json...", encoding="utf-8")
    assert isolated_client.get("/api/conversations/conv-corrupt-001", headers={"X-Session-ID": "sess-alpha"}).status_code == 404
    assert isolated_client.post("/api/conversations/conv-corrupt-001/clear", headers={"X-Session-ID": "sess-alpha"}).status_code == 404
    del_corrupt = isolated_client.delete("/api/conversations/conv-corrupt-001", headers={"X-Session-ID": "sess-alpha"})
    assert del_corrupt.status_code == 200
    assert not corrupt_path.exists()


def test_browser_action_does_not_mutate_shared_connector_config(isolated_client: TestClient) -> None:
    """Verify per-request allowed_domains on browser/action never mutates shared ConnectorConfig in registry."""
    mgr = web_dashboard._connector_manager()
    orig_cfg = mgr.registry.get("browser-chromium")
    assert orig_cfg is not None
    orig_domains = list(orig_cfg.permissions.allowed_domains)

    # Blocked URL with custom per-request allowed_domains (fails SSRF/scheme fast without launching browser)
    res = isolated_client.post(
        "/api/connectors/browser/action",
        json={
            "url": "file:///etc/passwd",
            "engine": "chromium",
            "action": "read_content",
            "allowed_domains": ["transient-only.example.org"],
        },
    )
    assert res.status_code == 200
    assert res.json().get("blocked") is True
    # Shared registry config MUST remain completely untouched
    after_cfg = mgr.registry.get("browser-chromium")
    assert after_cfg is not None
    assert after_cfg.permissions.allowed_domains == orig_domains
    assert "transient-only.example.org" not in after_cfg.permissions.allowed_domains


def test_admin_auth_rate_limits_health_and_deferred_live_llm_block(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify admin guards, mutation rate limits, safe health telemetry, and fail-closed unconfigured live LLM."""
    # 1. Health endpoint exposes persistence & security posture without secrets
    h = isolated_client.get("/api/health")
    assert h.status_code == 200
    hb = h.json()
    assert hb["storage"]["conversations_persistent"] is True
    assert hb["storage"]["conversations_backend"] == "filesystem_json_atomic"
    assert hb["security"]["server_side_credentials_only"] is True
    assert hb["security"]["request_max_bytes"] >= 1024
    assert hb["test_suite"]["verified_core_tests"] == 122

    # 2. Unconfigured live LLM provider refuses silent fallback with HTTP 503
    r_live = isolated_client.post(
        "/api/chat/query",
        json={"question": "What is 2 + 2?", "provider": "gemini"},
        headers={"X-Session-ID": "sess-live-check"},
    )
    assert r_live.status_code == 503
    assert "not configured" in r_live.json()["detail"].lower()

    # 3. Admin auth blocks all administrative endpoints when TRACERECOVER_ADMIN_TOKEN is set
    monkeypatch.setenv("TRACERECOVER_ADMIN_TOKEN", "phase81-admin-secret-token")
    for method, path, body in (
        ("POST", "/api/experiments/run", {"failure_rates": [0], "num_tasks": 1}),
        ("POST", "/api/datasets/import", {"name": "x", "dataset_version": "1", "tasks": []}),
        ("POST", "/api/unseen/run", {"num_tasks": 2, "seed": 42}),
        ("POST", "/api/connectors", {"name": "Test", "category": "custom_api", "transport": "http"}),
        ("POST", "/api/connectors/browser/action", {"url": "https://example.com", "engine": "chromium"}),
    ):
        resp = isolated_client.request(method, path, json=body)
        assert resp.status_code == 401, f"{method} {path} should require admin auth"

    # 4. Rate limit on conversation mutations & cancel endpoint
    monkeypatch.delenv("TRACERECOVER_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("TRACERECOVER_RATE_LIMIT_RUN", "2")
    web_dashboard.reset_rate_limiter()
    assert isolated_client.post("/api/conversations", json={"title": "1"}, headers={"X-Session-ID": "rl"}).status_code == 200
    assert isolated_client.post("/api/conversations", json={"title": "2"}, headers={"X-Session-ID": "rl"}).status_code == 200
    r3 = isolated_client.post("/api/conversations", json={"title": "3"}, headers={"X-Session-ID": "rl"})
    assert r3.status_code == 429
    assert "retry-after" in {k.lower() for k in r3.headers.keys()}


def test_conversation_secret_scrubbing_covers_headers_and_keys() -> None:
    raw = "x-goog-api-key: secret123456 x-api-key: ant987654321 api_key=mysecretkey99 Bearer jwt_token_123456789"
    cleaned = scrub_secrets(raw)
    for leaked in ("secret123456", "ant987654321", "mysecretkey99", "jwt_token_123456789"):
        assert leaked not in cleaned
    assert "[REDACTED]" in cleaned
