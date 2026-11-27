"""Phase 7.5.2 regression suite: provider failure propagation, synthesis integrity, evidence preservation & UI honesty."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from agent.open_ended_runner import OpenEndedAgentRunner, ProviderUnavailableError
from agent.provider_adapter import LLMProviderAdapter, ProviderAdapterError, redact_secret_text
from agent.synthesizer import EvidenceGroundedSynthesizer, SynthesisFailureError
from config import LLMCallMetrics, LLMClient
from tools.external_search import ExternalSearchAdapter, ExternalSearchResult
import web_dashboard


class _FakeMetricsClient(LLMClient):
    """Deterministic test double for remote LLM providers (never makes network calls)."""

    def __init__(
        self,
        *,
        provider: str = "gemini",
        model: str = "gemini-2.0-flash",
        steps: list[Any] | None = None,
    ) -> None:
        super().__init__(provider=provider, model=model)
        self._steps = list(steps or [])
        self.calls: int = 0

    def complete_with_metadata(self, prompt: str) -> tuple[str, LLMCallMetrics]:
        idx = self.calls
        self.calls += 1
        item = self._steps[idx] if idx < len(self._steps) else self._steps[-1]
        if isinstance(item, BaseException):
            raise item
        text = str(item)
        metrics = LLMCallMetrics(
            provider=self.provider,
            model=self.model,
            prompt_tokens=20,
            completion_tokens=12,
            total_tokens=32,
            latency_seconds=0.02,
            estimated_cost_usd=0.0001,
        )
        self.call_history.append(metrics)
        return text, metrics


def _sample_connector_observations() -> list[dict[str, Any]]:
    return [
        {
            "tool": "external_search",
            "input": {"query": "FastAPI lifespan events"},
            "output": {
                "provider": "wikipedia",
                "results": [
                    {
                        "title": "FastAPI Lifespan Documentation",
                        "url": "https://fastapi.tiangolo.com/advanced/events/",
                        "snippet": "FastAPI recommends using an async context manager passed to the lifespan parameter.",
                        "provider": "official_docs",
                        "trust_tier": "tier1",
                    },
                    {
                        "title": "Starlette Lifespan Specification",
                        "url": "https://www.starlette.io/lifespan/",
                        "snippet": "Lifespan handlers replace deprecated startup and shutdown events in ASGI apps.",
                        "provider": "official_docs",
                        "trust_tier": "tier1",
                    },
                ],
            },
        }
    ]


def _make_mock_search_adapter() -> ExternalSearchAdapter:
    def _provider_fn(query: str, max_results: int = 3) -> list[dict[str, Any]]:
        return [
            {
                "title": "FastAPI Lifespan Documentation",
                "url": "https://fastapi.tiangolo.com/advanced/events/",
                "snippet": "FastAPI recommends using an async context manager passed to the lifespan parameter.",
                "provider": "official_docs",
                "trust_tier": "tier1",
            },
            {
                "title": "Starlette Lifespan Specification",
                "url": "https://www.starlette.io/lifespan/",
                "snippet": "Lifespan handlers replace deprecated startup and shutdown events in ASGI apps.",
                "provider": "official_docs",
                "trust_tier": "tier1",
            },
        ][:max_results]

    return ExternalSearchAdapter(provider="auto", provider_fn=_provider_fn)


# ---------------------------------------------------------------------------
# 1. EvidenceGroundedSynthesizer direct failure-propagation contract
# ---------------------------------------------------------------------------


def test_synthesizer_propagates_remote_failures_without_silent_fallback() -> None:
    """Task A & B: remote provider failure MUST raise SynthesisFailureError, never return deterministic text."""
    synth = EvidenceGroundedSynthesizer()
    obs = _sample_connector_observations()

    # Offline local deterministic mode works cleanly
    off_meta = synth.synthesize_with_metadata("How does FastAPI handle lifespan?", obs, provider_adapter=None)
    assert off_meta["synthesis_status"] == "succeeded"
    assert off_meta["answer_origin"] == "deterministic_synthesizer"
    assert off_meta["fallback_used"] is False
    assert "lifespan" in off_meta["answer"].lower()

    # Remote provider failures MUST NOT silently substitute deterministic_answer
    error_cases = [
        (TimeoutError("upstream socket timed out"), "timeout"),
        (RuntimeError("HTTP 429 rate limit exceeded with key sk-secret1234567890abcd"), "rate_limited"),
        (RuntimeError("HTTP 401 unauthorized x-goog-api-key: AIzaSySecretKeyValue1234567890"), "auth_failed"),
        (RuntimeError("HTTP 503 service unavailable"), "unreachable"),
        (ProviderAdapterError("budget_exceeded", "LLM call budget exceeded (2 calls max).", provider="gemini"), "budget_exceeded"),
    ]
    for exc_obj, expected_cat in error_cases:
        failing_client = _FakeMetricsClient(provider="gemini", steps=[exc_obj])
        with pytest.raises(SynthesisFailureError) as exc_info:
            synth.synthesize("How does FastAPI handle lifespan?", obs, provider_adapter=failing_client)
        assert exc_info.value.category == expected_cat
        assert exc_info.value.stage == "synthesis"
        assert "sk-secret1234567890abcd" not in str(exc_info.value)
        assert "AIzaSySecretKeyValue1234567890" not in str(exc_info.value)

    # Empty remote response MUST raise empty_response, never silently fall back
    empty_client = _FakeMetricsClient(provider="gemini", steps=["   \n\t  "])
    with pytest.raises(SynthesisFailureError) as empty_exc:
        synth.synthesize("How does FastAPI handle lifespan?", obs, provider_adapter=empty_client)
    assert empty_exc.value.category == "empty_response"


def test_synthesizer_explicit_opt_in_fallback_records_full_telemetry() -> None:
    """Even when explicitly opted in for diagnostics, fallback MUST record fallback_used=True and redacted reason."""
    synth = EvidenceGroundedSynthesizer()
    obs = _sample_connector_observations()
    failing_client = _FakeMetricsClient(
        provider="openai",
        steps=[RuntimeError("429 quota exceeded Bearer secret_tok_9876543210")],
    )
    meta = synth.synthesize_with_metadata(
        "How does FastAPI handle lifespan?",
        obs,
        provider_adapter=failing_client,
        allow_deterministic_fallback=True,
    )
    assert meta["synthesis_status"] == "fallback_deterministic"
    assert meta["answer_origin"] == "deterministic_synthesizer"
    assert meta["fallback_used"] is True
    assert meta["failure_category"] == "rate_limited"
    assert meta["llm_produced_final_answer"] is False
    assert "secret_tok_9876543210" not in str(meta["failure_reason"])
    assert "[REDACTED_KEY]" in str(meta["failure_reason"])


# ---------------------------------------------------------------------------
# 2. Task C: All 10 provider failure scenarios & zero secret leakage
# ---------------------------------------------------------------------------


def test_task_c_all_ten_provider_failure_scenarios(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify accurate classification, bounded retries, evidence preservation & zero secret leakage across all 10 cases."""
    for env_var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_BASE_URL", "LOCAL_LLM_BASE_URL"):
        monkeypatch.delenv(env_var, raising=False)

    # 1. Missing credentials
    with pytest.raises(ProviderAdapterError) as e_miss:
        LLMProviderAdapter(provider="gemini").check_credentials()
    assert e_miss.value.category == "missing_credentials"

    runner_no_cred = OpenEndedAgentRunner(log_directory=tmp_path)
    with pytest.raises(ProviderUnavailableError) as e_run_miss:
        runner_no_cred.run("What is 2 + 2?", provider="gemini")
    assert "missing_credentials" in str(e_run_miss.value) or "not configured" in str(e_run_miss.value).lower()

    # 2. Invalid credentials (malformed placeholder + HTTP 401 auth_failed)
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    with pytest.raises(ProviderAdapterError) as e_inv:
        LLMProviderAdapter(provider="openai").check_credentials()
    assert e_inv.value.category == "invalid_api_key"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-validformat1234567890abcdef")
    auth_client = _FakeMetricsClient(
        provider="openai",
        steps=[RuntimeError("HTTP 401 unauthorized: x-api-key: sk-leakcheck9999999999")],
    )
    with pytest.raises(ProviderAdapterError) as e_auth:
        LLMProviderAdapter(client=auth_client).complete_with_budget("hello")
    assert e_auth.value.category == "auth_failed"
    assert "sk-leakcheck9999999999" not in str(e_auth.value)

    # 3. Request timeout
    with pytest.raises(ProviderAdapterError) as e_to:
        LLMProviderAdapter(client=_FakeMetricsClient(provider="openai", steps=[TimeoutError("read timed out")])).complete_with_budget("hi")
    assert e_to.value.category == "timeout"

    # 4. Rate limit (HTTP 429)
    with pytest.raises(ProviderAdapterError) as e_rl:
        LLMProviderAdapter(client=_FakeMetricsClient(provider="openai", steps=[RuntimeError("HTTP 429 too many requests")])).complete_with_budget("hi")
    assert e_rl.value.category == "rate_limited"

    # 5. Provider unavailable (HTTP 503 / connection refused)
    with pytest.raises(ProviderAdapterError) as e_un:
        LLMProviderAdapter(client=_FakeMetricsClient(provider="openai", steps=[RuntimeError("HTTP 503 service unavailable")])).complete_with_budget("hi")
    assert e_un.value.category == "unreachable"

    # 6. Empty response
    with pytest.raises(ProviderAdapterError) as e_emp:
        LLMProviderAdapter(client=_FakeMetricsClient(provider="openai", steps=["   "])).complete_with_budget("hi")
    assert e_emp.value.category == "empty_response"

    # 7. Malformed structured response (bounded 1 repair retry, no infinite loop)
    malformed_client = _FakeMetricsClient(provider="openai", steps=["not valid json", "still not json"])
    runner_mal = OpenEndedAgentRunner(log_directory=tmp_path)
    with pytest.raises(ProviderUnavailableError) as e_mal:
        runner_mal.run("Explain ASGI", provider="openai", client=malformed_client, max_llm_calls=4)
    assert "malformed_output" in str(e_mal.value)
    assert malformed_client.calls == 2  # 1 initial + 1 bounded repair retry, never infinite

    # 8. Budget exhaustion (max_llm_calls)
    budget_client = _FakeMetricsClient(
        provider="openai",
        steps=[json.dumps({"thought": "Compute", "action": "calculator", "arguments": {"expression": "12 * 7"}, "final_answer": None})],
    )
    runner_bud = OpenEndedAgentRunner(log_directory=tmp_path)
    res_bud = runner_bud.run("What is 12 * 7?", provider="openai", client=budget_client, max_llm_calls=1, max_steps=3)
    assert res_bud["synthesis_status"] == "failed"
    assert res_bud["failure_category"] == "budget_exceeded"
    assert res_bud["failed_stage"] == "synthesis"
    assert res_bud["answer_origin"] == "none_failed"
    assert res_bud["fallback_used"] is False
    assert res_bud["final_decision"] == "ABSTAIN"
    assert res_bud["abstention_category"] == "provider_synthesis_failure"

    # 9. Synthesis failure after successful planning (local tool succeeds, Step 2 synthesis fails)
    plan_then_fail_client = _FakeMetricsClient(
        provider="gemini",
        steps=[
            json.dumps({"thought": "Calculate 19 * 23", "action": "calculator", "arguments": {"expression": "19 * 23"}, "final_answer": None}),
            TimeoutError("Gemini synthesis timed out after 30s (AIzaSySecretLeakTest123456)"),
        ],
    )
    runner_p9 = OpenEndedAgentRunner(log_directory=tmp_path)
    res_p9 = runner_p9.run("What is 19 * 23?", provider="gemini", client=plan_then_fail_client, max_steps=4)
    assert res_p9["planning_status"] == "succeeded"
    assert res_p9["synthesis_status"] == "failed"
    assert res_p9["failed_stage"] == "synthesis"
    assert res_p9["failure_category"] == "timeout"
    assert res_p9["answer_origin"] == "none_failed"
    assert res_p9["fallback_used"] is False
    assert res_p9["final_decision"] == "ABSTAIN"
    assert "437" in str(res_p9["retrieved_evidence"])
    assert "AIzaSySecretLeakTest123456" not in json.dumps(res_p9)

    # 10. Synthesis failure after successful connector retrieval (both post-retrieval Step 2 AND max_steps synthesizer path)
    conn_step_json = json.dumps(
        {
            "thought": "Search official FastAPI docs for lifespan guidance.",
            "action": "external_search",
            "arguments": {"query": "FastAPI lifespan events"},
            "final_answer": None,
        }
    )
    # 10a: Step 1 connector succeeds, Step 2 synthesis raises HTTP 429
    conn_fail_step2 = _FakeMetricsClient(
        provider="gemini",
        steps=[conn_step_json, RuntimeError("HTTP 429 rate limit exceeded api_key=AIzaSyHiddenKey999888777")],
    )
    runner_10a = OpenEndedAgentRunner(search_adapter=_make_mock_search_adapter(), log_directory=tmp_path)
    res_10a = runner_10a.run(
        "How does FastAPI recommend managing lifespan events?",
        provider="gemini",
        client=conn_fail_step2,
        enable_external_search=True,
        max_steps=3,
    )
    assert res_10a["planning_status"] == "succeeded"
    assert res_10a["connector_status"] == "succeeded"
    assert res_10a["synthesis_status"] == "failed"
    assert res_10a["failed_stage"] == "synthesis"
    assert res_10a["failure_category"] == "rate_limited"
    assert res_10a["answer_origin"] == "none_failed"
    assert res_10a["fallback_used"] is False
    assert res_10a["llm_produced_final_answer"] is False
    assert res_10a["final_decision"] == "ABSTAIN"
    assert res_10a["abstention_category"] == "provider_synthesis_failure"
    assert "FAILED" in res_10a["execution_mode_label"]
    assert res_10a["execution_mode_label"] not in {"LIVE LLM", "LIVE CONNECTOR + LIVE LLM"}
    assert len(res_10a["external_sources"]) == 2
    assert any("fastapi.tiangolo.com" in s["url"] for s in res_10a["external_sources"])
    assert len(res_10a["retrieved_evidence"]) >= 2
    assert len(res_10a["structured_answer"]["citations"]) == 2
    assert len(res_10a["structured_answer"]["supporting_evidence"]) >= 2
    assert "AIzaSyHiddenKey999888777" not in json.dumps(res_10a)

    # 10b: Step 1 connector succeeds at max_steps=1 -> post-loop EvidenceGroundedSynthesizer fails
    conn_fail_synth = _FakeMetricsClient(
        provider="gemini",
        steps=[conn_step_json, RuntimeError("HTTP 503 backend unavailable Bearer secret_header_token_12345")],
    )
    runner_10b = OpenEndedAgentRunner(search_adapter=_make_mock_search_adapter(), log_directory=tmp_path)
    res_10b = runner_10b.run(
        "How does FastAPI recommend managing lifespan events?",
        provider="gemini",
        client=conn_fail_synth,
        enable_external_search=True,
        max_steps=1,
    )
    assert res_10b["planning_status"] == "succeeded"
    assert res_10b["connector_status"] == "succeeded"
    assert res_10b["synthesis_status"] == "failed"
    assert res_10b["failed_stage"] == "synthesis"
    assert res_10b["failure_category"] == "unreachable"
    assert res_10b["answer_origin"] == "none_failed"
    assert res_10b["fallback_used"] is False
    assert len(res_10b["external_sources"]) == 2
    assert len(res_10b["retrieved_evidence"]) >= 2
    assert "secret_header_token_12345" not in json.dumps(res_10b)


# ---------------------------------------------------------------------------
# 3. Task B & D: Live-path (`client=None`) policy enforcement + OFFLINE & MOCK
# ---------------------------------------------------------------------------


def test_live_connector_plus_live_llm_synthesis_failure_never_claims_live_llm_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Simulate genuine `client=None` LIVE CONNECTOR + LIVE LLM where synthesis fails after retrieval."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyUnitTestOnlyFakeKey12345678")

    call_idx = {"n": 0}
    step1_plan = json.dumps(
        {
            "thought": "Retrieve FastAPI documentation via external_search.",
            "action": "external_search",
            "arguments": {"query": "FastAPI lifespan events"},
            "final_answer": None,
        }
    )

    def _fake_complete_with_budget(self: LLMProviderAdapter, prompt: str) -> tuple[str, LLMCallMetrics]:
        call_idx["n"] += 1
        if call_idx["n"] == 1:
            m = LLMCallMetrics(
                provider=self.provider,
                model=self.model,
                prompt_tokens=30,
                completion_tokens=18,
                total_tokens=48,
                latency_seconds=0.05,
                estimated_cost_usd=0.0001,
            )
            self._custom_history.append(m)
            return step1_plan, m
        self.last_error_category = "rate_limited"
        self.failed_requests += 1
        raise ProviderAdapterError(
            "rate_limited",
            "Gemini HTTP 429 quota exceeded (x-goog-api-key: AIzaSyUnitTestOnlyFakeKey12345678)",
            provider=self.provider,
        )

    monkeypatch.setattr(LLMProviderAdapter, "complete_with_budget", _fake_complete_with_budget)

    # Use a non-mock ExternalSearchAdapter whose network fetch is patched to simulate a live connector response
    live_style_adapter = ExternalSearchAdapter(provider="wikipedia")
    monkeypatch.setattr(
        live_style_adapter,
        "_search_wikipedia",
        lambda query: [
            ExternalSearchResult(
                title="FastAPI Lifespan Documentation",
                url="https://fastapi.tiangolo.com/advanced/events/",
                snippet="FastAPI recommends using an async context manager passed to the lifespan parameter.",
                provider="wikipedia",
            )
        ],
    )

    runner = OpenEndedAgentRunner(search_adapter=live_style_adapter, log_directory=tmp_path)
    res = runner.run(
        "How does FastAPI recommend managing lifespan events?",
        provider="gemini",
        enable_external_search=True,
        max_steps=1,  # Forces post-loop EvidenceGroundedSynthesizer path with client=None!
    )

    ei = res["execution_integrity"]
    assert ei["requested_provider"] == "gemini"
    assert ei["actual_provider"] == "gemini"
    assert ei["planning_status"] == "succeeded"
    assert ei["connector_status"] == "succeeded"
    assert ei["synthesis_status"] == "failed"
    assert ei["failed_stage"] == "synthesis"
    assert ei["failure_category"] == "rate_limited"
    assert ei["answer_origin"] == "none_failed"
    assert ei["fallback_used"] is False
    assert ei["llm_produced_final_answer"] is False
    assert ei["live_llm_succeeded"] is False
    assert ei["preserved_external_sources_count"] == 1
    assert res["execution_mode_label"] == "LIVE CONNECTOR (LIVE LLM SYNTHESIS FAILED)"
    assert res["final_decision"] == "ABSTAIN"
    assert res["abstention_category"] == "provider_synthesis_failure"
    assert res["external_sources"][0]["url"] == "https://fastapi.tiangolo.com/advanced/events/"
    assert "AIzaSyUnitTestOnlyFakeKey12345678" not in json.dumps(res)


def test_offline_and_mock_modes_and_recovery_remain_intact(tmp_path: Path) -> None:
    """Confirm OFFLINE, MOCK, and tool recovery continue working cleanly with accurate integrity metadata."""
    # OFFLINE mode with fault recovery
    runner_off = OpenEndedAgentRunner(log_directory=tmp_path)
    res_off = runner_off.run("What is 19 * 23 + 4?", provider="local", failure_rate=40.0, seed=13)
    assert res_off["execution_mode_label"] == "OFFLINE"
    assert res_off["requested_provider"] == "local"
    assert res_off["actual_provider"] == "local"
    assert res_off["planning_status"] == "succeeded"
    assert res_off["synthesis_status"] == "succeeded"
    assert res_off["answer_origin"] == "deterministic_synthesizer"
    assert res_off["fallback_used"] is False
    assert res_off["llm_produced_final_answer"] is False
    assert res_off["final_decision"] == "ANSWER"

    # MOCK mode: never misrepresented as LIVE LLM
    mock_client = _FakeMetricsClient(
        provider="openai",
        model="gpt-4o-mini",
        steps=[
            json.dumps({"thought": "Compute", "action": "calculator", "arguments": {"expression": "15 * 4"}, "final_answer": None}),
            json.dumps({"thought": "Answer", "action": "final", "arguments": {}, "final_answer": "15 * 4 equals 60."}),
        ],
    )
    runner_mock = OpenEndedAgentRunner(log_directory=tmp_path)
    res_mock = runner_mock.run("What is 15 * 4?", provider="openai", client=mock_client)
    assert res_mock["execution_mode"] == "mock_llm"
    assert res_mock["execution_mode_label"] == "MOCK"
    assert res_mock["answer_origin"] == "mock_provider"
    assert res_mock["llm_produced_final_answer"] is False
    assert res_mock["execution_integrity"]["live_llm_succeeded"] is False
    assert res_mock["synthesis_status"] == "succeeded"
    assert res_mock["final_decision"] == "ANSWER"


def test_secret_redaction_covers_api_keys_and_auth_headers() -> None:
    raw = (
        "Failed auth with sk-proj-abc1234567890 and AIzaSyTestKey1234567890 "
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload x-goog-api-key: secretval999 x-api-key: antkey999"
    )
    cleaned = redact_secret_text(raw)
    for secret in ("sk-proj-abc1234567890", "AIzaSyTestKey1234567890", "eyJhbGciOiJIUzI1NiJ9", "secretval999", "antkey999"):
        assert secret not in cleaned
    assert "[REDACTED_KEY]" in cleaned


# ---------------------------------------------------------------------------
# 4. Task E & F: Chromium Dashboard E2E Execution-Integrity Verification
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def phase7_5_2_live_server(tmp_path: Path):
    orig_logs = web_dashboard.LOGS_DIR
    web_dashboard.LOGS_DIR = tmp_path / "logs"
    web_dashboard.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    port = _find_free_port()
    config = uvicorn.Config(web_dashboard.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 12.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        web_dashboard.LOGS_DIR = orig_logs


def test_dashboard_chromium_execution_integrity_and_synthesis_failure_display(
    phase7_5_2_live_server: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify Query Workspace displays accurate execution integrity on OFFLINE runs AND red failure state on failed live synthesis."""
    playwright_sync = pytest.importorskip("playwright.sync_api")

    # Produce a real synthesis-failure payload from OpenEndedAgentRunner (live planning -> connector -> failed synthesis)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyUiIntegrityTestKey12345678")
    step1_plan = json.dumps(
        {
            "thought": "Retrieve FastAPI lifespan docs.",
            "action": "external_search",
            "arguments": {"query": "FastAPI lifespan events"},
            "final_answer": None,
        }
    )
    calls = {"n": 0}

    def _fail_on_synth(self: LLMProviderAdapter, prompt: str) -> tuple[str, LLMCallMetrics]:
        calls["n"] += 1
        if calls["n"] == 1:
            m = LLMCallMetrics(provider="gemini", model="gemini-2.0-flash", prompt_tokens=25, completion_tokens=15, total_tokens=40, latency_seconds=0.04, estimated_cost_usd=0.0001)
            self._custom_history.append(m)
            return step1_plan, m
        raise ProviderAdapterError("rate_limited", "Gemini HTTP 429 quota exceeded x-goog-api-key: AIzaSyUiIntegrityTestKey12345678", provider="gemini")

    monkeypatch.setattr(LLMProviderAdapter, "complete_with_budget", _fail_on_synth)
    live_adapter = ExternalSearchAdapter(provider="wikipedia")
    monkeypatch.setattr(
        live_adapter,
        "_search_wikipedia",
        lambda query: [
            ExternalSearchResult(
                title="FastAPI Lifespan Documentation",
                url="https://fastapi.tiangolo.com/advanced/events/",
                snippet="FastAPI recommends an async context manager via the lifespan parameter.",
                provider="wikipedia",
            )
        ],
    )
    failed_payload = OpenEndedAgentRunner(search_adapter=live_adapter, log_directory=tmp_path).run(
        "How does FastAPI recommend managing lifespan events?",
        provider="gemini",
        enable_external_search=True,
        max_steps=1,
    )

    exe_path = next(
        (
            p
            for p in (
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            )
            if Path(p).is_file()
        ),
        None,
    )
    with playwright_sync.sync_playwright() as p:
        launch_kwargs: dict[str, Any] = {"headless": True}
        if exe_path:
            launch_kwargs["executable_path"] = exe_path
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page_errors: list[str] = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        page.goto(phase7_5_2_live_server, wait_until="networkidle")
        page.click('button.nav-item[data-nav="workspace"]')

        # 1. Live OFFLINE query through the actual backend
        page.fill("#ws-query-input", "What is 15 * 4?")
        page.click("#ws-submit-btn")
        page.wait_for_selector('[data-testid="ws-execution-integrity"]', timeout=15000)

        off_badge = page.locator('[data-testid="ws-exec-mode-badge"]').inner_text()
        assert off_badge.strip() == "OFFLINE"
        off_integrity = page.locator('[data-testid="ws-execution-integrity"]').inner_text()
        assert "Requested: local" in off_integrity
        assert "Synthesis: succeeded" in off_integrity
        assert "Origin: deterministic_synthesizer" in off_integrity
        assert "Fallback: none" in off_integrity
        assert page.locator('[data-testid="ws-synthesis-failure-banner"]').count() == 0

        # 2. Render the genuine runner-produced live-synthesis-failure payload and verify honest failure display
        page.evaluate("(payload) => window.renderWorkspaceAnswer(payload)", failed_payload)
        page.wait_for_selector('[data-testid="ws-synthesis-failure-banner"]', timeout=5000)

        badge_el = page.locator('[data-testid="ws-exec-mode-badge"]')
        badge_text = badge_el.inner_text().strip()
        badge_class = badge_el.get_attribute("class") or ""
        assert "FAILED" in badge_text
        assert "pill-red" in badge_class
        assert "pill-green" not in badge_class

        fail_banner_text = page.locator('[data-testid="ws-synthesis-failure-banner"]').inner_text()
        assert "Answer Synthesis Failed" in fail_banner_text
        assert "rate_limited" in fail_banner_text
        assert "Connector Retrieval: succeeded" in fail_banner_text
        assert "1 external source(s)" in fail_banner_text

        fail_integrity_text = page.locator('[data-testid="ws-execution-integrity"]').inner_text()
        assert "Requested: gemini" in fail_integrity_text
        assert "Actual: gemini / gemini-2.0-flash" in fail_integrity_text
        assert "Planning: succeeded" in fail_integrity_text
        assert "Connectors: succeeded" in fail_integrity_text
        assert "Synthesis: failed" in fail_integrity_text
        assert "Origin: none_failed" in fail_integrity_text
        assert "Fallback: none" in fail_integrity_text

        # Preserved source link must remain clickable in DOM and secret must never leak
        answer_html = page.locator("#ws-answer-container").inner_html()
        assert "https://fastapi.tiangolo.com/advanced/events/" in answer_html
        assert "AIzaSyUiIntegrityTestKey12345678" not in page.locator("body").inner_text()
        assert page_errors == []

        browser.close()
