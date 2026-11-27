"""Reproducible Phase 8.2 performance, concurrency, storage & dashboard benchmark suite.

Runs strictly in OFFLINE and MOCK modes without requiring any external LLM API keys.
Separates measurements by execution mode (OFFLINE, MOCK, Backend API, Concurrency,
ConversationStore scaling, and Chromium Dashboard responsiveness).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import platform
import socket
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.conversation_store import ConversationStore
from agent.open_ended_runner import OpenEndedAgentRunner
from config import LLMCallMetrics, LLMClient
from tools.external_search import ExternalSearchAdapter
import web_dashboard


class _DeterministicMockClient(LLMClient):
    """Fast deterministic mock LLM client for controlled MOCK-mode benchmarking."""

    def __init__(self, *, provider: str = "gemini", model: str = "gemini-2.0-flash") -> None:
        super().__init__(provider=provider, model=model)
        self._step = 0

    def complete_with_metadata(self, prompt: str) -> tuple[str, LLMCallMetrics]:
        self._step += 1
        if "OBSERVATIONS" in prompt and "external_search" not in prompt and self._step % 2 == 1:
            reply = json.dumps(
                {
                    "thought": "Compute exact arithmetic result using calculator.",
                    "action": "calculator",
                    "arguments": {"expression": "19 * 23 + 4"},
                    "final_answer": None,
                }
            )
        elif self._step % 2 == 1:
            reply = json.dumps(
                {
                    "thought": "Retrieve evidence via external_search.",
                    "action": "external_search",
                    "arguments": {"query": "FastAPI lifespan events"},
                    "final_answer": None,
                }
            )
        else:
            reply = json.dumps(
                {
                    "thought": "Synthesize grounded answer from observed output.",
                    "action": "final",
                    "arguments": {},
                    "final_answer": "FastAPI recommends using an async context manager passed to the lifespan parameter.",
                }
            )
        metrics = LLMCallMetrics(
            provider=self.provider,
            model=self.model,
            prompt_tokens=32,
            completion_tokens=20,
            total_tokens=52,
            latency_seconds=0.001,
            estimated_cost_usd=0.0,
        )
        self.call_history.append(metrics)
        return reply, metrics


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return round(ordered[f], 3)
    return round(ordered[f] + (ordered[c] - ordered[f]) * (k - f), 3)


def _summarize_latencies(
    latencies_ms: list[float],
    *,
    total_requests: int,
    successes: int,
    failures: int,
    wall_seconds: float,
    cpu_seconds: float,
    peak_memory_mb: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "requests": total_requests,
        "successful_requests": successes,
        "failed_requests": failures,
        "wall_seconds": round(wall_seconds, 4),
        "throughput_rps": round(successes / wall_seconds, 2) if wall_seconds > 0 else 0.0,
        "mean_ms": round(statistics.fmean(latencies_ms), 3) if latencies_ms else None,
        "median_ms": round(statistics.median(latencies_ms), 3) if latencies_ms else None,
        "p95_ms": _percentile(latencies_ms, 95.0),
        "p99_ms": _percentile(latencies_ms, 99.0) if len(latencies_ms) >= 10 else None,
        "min_ms": round(min(latencies_ms), 3) if latencies_ms else None,
        "max_ms": round(max(latencies_ms), 3) if latencies_ms else None,
        "cpu_seconds": round(cpu_seconds, 4),
        "peak_tracemalloc_mb": round(peak_memory_mb, 3),
    }
    if extra:
        out.update(extra)
    return out


def _make_mock_search_adapter() -> ExternalSearchAdapter:
    def _fn(query: str, max_results: int = 3) -> list[dict[str, Any]]:
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

    return ExternalSearchAdapter(provider="auto", provider_fn=_fn)


def bench_offline_execution(tmp_dir: Path, iterations: int = 12) -> dict[str, Any]:
    """Category A: OFFLINE deterministic execution (clean + 40% fault recovery)."""
    queries = [
        ("What is 19 * 23 + 4?", 0.0, 42),
        ("Look up support ticket T-401 and calculate (1200 * 1.15)", 0.0, 42),
        ("What is the capital of France?", 0.0, 42),
        ("What is 19 * 23 + 4?", 40.0, 13),  # deterministic recovery case
    ]
    runner = OpenEndedAgentRunner(log_directory=tmp_dir / "offline_logs")
    latencies: list[float] = []
    successes = 0
    failures = 0
    recovered_ok = 0
    recovery_runs = 0
    tracemalloc.start()
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    for i in range(iterations):
        q, frate, seed = queries[i % len(queries)]
        t0 = time.perf_counter()
        try:
            res = runner.run(q, provider="local", failure_rate=frate, seed=seed)
            dt = (time.perf_counter() - t0) * 1000.0
            latencies.append(dt)
            if res.get("final_decision") in {"ANSWER", "WARN", "ABSTAIN"}:
                successes += 1
            else:
                failures += 1
            if frate > 0:
                recovery_runs += 1
                if res.get("recovery_report", {}).get("successful_recoveries", 0) > 0:
                    recovered_ok += 1
        except Exception:
            failures += 1
    wall = time.perf_counter() - wall0
    cpu = time.process_time() - cpu0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return _summarize_latencies(
        latencies,
        total_requests=iterations,
        successes=successes,
        failures=failures,
        wall_seconds=wall,
        cpu_seconds=cpu,
        peak_memory_mb=peak / (1024 * 1024),
        extra={
            "mode": "OFFLINE",
            "recovery_runs": recovery_runs,
            "recovery_successes": recovered_ok,
        },
    )


def bench_mock_execution(tmp_dir: Path, iterations: int = 12) -> dict[str, Any]:
    """Category B: MOCK remote LLM + mock external connector execution."""
    runner = OpenEndedAgentRunner(
        search_adapter=_make_mock_search_adapter(),
        log_directory=tmp_dir / "mock_logs",
    )
    latencies: list[float] = []
    successes = 0
    failures = 0
    tracemalloc.start()
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    for _ in range(iterations):
        client = _DeterministicMockClient()
        t0 = time.perf_counter()
        try:
            res = runner.run(
                "How does FastAPI recommend managing lifespan events?",
                provider="gemini",
                client=client,
                enable_external_search=True,
                max_steps=4,
            )
            dt = (time.perf_counter() - t0) * 1000.0
            latencies.append(dt)
            if res.get("execution_mode_label") == "MOCK" and res.get("external_sources"):
                successes += 1
            else:
                failures += 1
        except Exception:
            failures += 1
    wall = time.perf_counter() - wall0
    cpu = time.process_time() - cpu0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return _summarize_latencies(
        latencies,
        total_requests=iterations,
        successes=successes,
        failures=failures,
        wall_seconds=wall,
        cpu_seconds=cpu,
        peak_memory_mb=peak / (1024 * 1024),
        extra={"mode": "MOCK"},
    )


def bench_backend_api_endpoints(iterations: int = 15) -> dict[str, Any]:
    """Category D: Core FastAPI read endpoints against real workspace artifacts."""
    web_dashboard.reset_rate_limiter()
    client = TestClient(web_dashboard.app)
    endpoints = [
        "/api/health",
        "/api/traces?limit=50",
        "/api/datasets",
        "/api/analytics",
        "/api/experiments",
        "/api/settings",
        "/api/connectors/overview",
    ]
    results: dict[str, Any] = {}
    for ep in endpoints:
        latencies: list[float] = []
        ok = 0
        fail = 0
        payload_bytes = 0
        tracemalloc.start()
        cpu0 = time.process_time()
        wall0 = time.perf_counter()
        for _ in range(iterations):
            t0 = time.perf_counter()
            r = client.get(ep)
            dt = (time.perf_counter() - t0) * 1000.0
            latencies.append(dt)
            payload_bytes = max(payload_bytes, len(r.content))
            if r.status_code == 200:
                ok += 1
            else:
                fail += 1
        wall = time.perf_counter() - wall0
        cpu = time.process_time() - cpu0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results[ep] = _summarize_latencies(
            latencies,
            total_requests=iterations,
            successes=ok,
            failures=fail,
            wall_seconds=wall,
            cpu_seconds=cpu,
            peak_memory_mb=peak / (1024 * 1024),
            extra={"max_payload_bytes": payload_bytes},
        )
    return results


def bench_storage_scaling(tmp_dir: Path) -> dict[str, Any]:
    """Category F: ConversationStore atomic write/read/list latency at 5 vs 40 conversations."""
    store = ConversationStore(tmp_dir / "conv_bench", max_conversations_per_session=60)
    write_lats: list[float] = []
    list_small_lats: list[float] = []
    list_large_lats: list[float] = []
    read_lats: list[float] = []
    conv_ids: list[str] = []

    tracemalloc.start()
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    for idx in range(40):
        t0 = time.perf_counter()
        c = store.create_conversation("bench-session", title=f"Thread {idx}")
        cid = c["conversation_id"]
        conv_ids.append(cid)
        for turn_i in range(4):
            store.append_turn(
                "bench-session",
                cid,
                {
                    "query_id": f"q-{idx}-{turn_i}",
                    "question": f"Sample question {idx}-{turn_i}",
                    "final_decision": "ANSWER",
                    "final_response": f"Grounded response payload {idx}-{turn_i} " + ("x" * 200),
                    "provider": "local",
                },
            )
        write_lats.append((time.perf_counter() - t0) * 1000.0)
        if idx == 4:
            for _ in range(10):
                lt0 = time.perf_counter()
                listed = store.list_conversations("bench-session")
                list_small_lats.append((time.perf_counter() - lt0) * 1000.0)
                assert len(listed) == 5

    for _ in range(15):
        lt0 = time.perf_counter()
        listed = store.list_conversations("bench-session")
        list_large_lats.append((time.perf_counter() - lt0) * 1000.0)
        assert len(listed) == 40

    for cid in conv_ids[:20]:
        rt0 = time.perf_counter()
        rec = store.get_conversation("bench-session", cid)
        read_lats.append((time.perf_counter() - rt0) * 1000.0)
        assert rec is not None and len(rec["turns"]) == 4

    wall = time.perf_counter() - wall0
    cpu = time.process_time() - cpu0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "create_plus_4_turns_ms": _summarize_latencies(
            write_lats,
            total_requests=len(write_lats),
            successes=len(write_lats),
            failures=0,
            wall_seconds=wall,
            cpu_seconds=cpu,
            peak_memory_mb=peak / (1024 * 1024),
        ),
        "list_5_conversations_ms": {
            "mean_ms": round(statistics.fmean(list_small_lats), 3),
            "median_ms": round(statistics.median(list_small_lats), 3),
            "p95_ms": _percentile(list_small_lats, 95.0),
        },
        "list_40_conversations_ms": {
            "mean_ms": round(statistics.fmean(list_large_lats), 3),
            "median_ms": round(statistics.median(list_large_lats), 3),
            "p95_ms": _percentile(list_large_lats, 95.0),
        },
        "get_conversation_ms": {
            "mean_ms": round(statistics.fmean(read_lats), 3),
            "median_ms": round(statistics.median(read_lats), 3),
            "p95_ms": _percentile(read_lats, 95.0),
        },
        "leftover_tmp_files": len(list((tmp_dir / "conv_bench").rglob("*.tmp"))),
    }


def bench_concurrent_sessions(tmp_dir: Path, workers: int = 6, requests_per_worker: int = 3) -> dict[str, Any]:
    """Category E: Controlled concurrent multi-session OFFLINE queries & API calls."""
    orig_conv = web_dashboard.CONVERSATIONS_DIR
    orig_logs = web_dashboard.LOGS_DIR
    web_dashboard.CONVERSATIONS_DIR = tmp_dir / "conc_conv"
    web_dashboard.LOGS_DIR = tmp_dir / "conc_logs"
    web_dashboard.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    web_dashboard.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    web_dashboard._CONVERSATION_STORE_CACHE.clear()
    web_dashboard.reset_rate_limiter()
    old_limit = os.environ.get("TRACERECOVER_RATE_LIMIT_RUN")
    os.environ["TRACERECOVER_RATE_LIMIT_RUN"] = "200"

    latencies: list[float] = []
    successes = 0
    failures = 0
    isolation_ok = True
    lock = threading.Lock()
    client = TestClient(web_dashboard.app)

    def _worker(wid: int) -> None:
        nonlocal successes, failures, isolation_ok
        sid = f"conc-sess-{wid}"
        c_res = client.post("/api/conversations", json={"title": f"Worker {wid}"}, headers={"X-Session-ID": sid})
        if c_res.status_code != 200:
            with lock:
                failures += requests_per_worker
            return
        cid = c_res.json()["conversation"]["conversation_id"]
        for req_i in range(requests_per_worker):
            t0 = time.perf_counter()
            r = client.post(
                "/api/chat/query",
                json={
                    "question": f"What is {10 + wid} * {2 + req_i}?",
                    "provider": "local",
                    "conversation_id": cid,
                },
                headers={"X-Session-ID": sid},
            )
            dt = (time.perf_counter() - t0) * 1000.0
            with lock:
                latencies.append(dt)
                if r.status_code == 200 and r.json().get("final_decision") == "ANSWER":
                    successes += 1
                else:
                    failures += 1
        # Verify cross-session isolation
        other_sid = f"conc-sess-{(wid + 1) % workers}"
        cross = client.get(f"/api/conversations/{cid}", headers={"X-Session-ID": other_sid})
        if cross.status_code != 404:
            with lock:
                isolation_ok = False

    tracemalloc.start()
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_worker, w) for w in range(workers)]
            for f in futs:
                f.result()
    finally:
        wall = time.perf_counter() - wall0
        cpu = time.process_time() - cpu0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if old_limit is None:
            os.environ.pop("TRACERECOVER_RATE_LIMIT_RUN", None)
        else:
            os.environ["TRACERECOVER_RATE_LIMIT_RUN"] = old_limit
        web_dashboard.CONVERSATIONS_DIR = orig_conv
        web_dashboard.LOGS_DIR = orig_logs
        web_dashboard._CONVERSATION_STORE_CACHE.clear()
        web_dashboard.reset_rate_limiter()

    return _summarize_latencies(
        latencies,
        total_requests=workers * requests_per_worker,
        successes=successes,
        failures=failures,
        wall_seconds=wall,
        cpu_seconds=cpu,
        peak_memory_mb=peak / (1024 * 1024),
        extra={
            "workers": workers,
            "requests_per_worker": requests_per_worker,
            "session_isolation_verified": isolation_ok,
        },
    )


def bench_dashboard_chromium(runs: int = 3) -> dict[str, Any]:
    """Category G: Real Chromium initial load, network request count, and tab switch latency."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {"available": False, "reason": "playwright not installed"}

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
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])

    config = uvicorn.Config(web_dashboard.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 12.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)

    load_ms: list[float] = []
    tab_switch_ms: list[float] = []
    api_calls_initial: list[int] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    try:
        with sync_playwright() as p:
            kwargs: dict[str, Any] = {"headless": True}
            if exe_path:
                kwargs["executable_path"] = exe_path
            browser = p.chromium.launch(**kwargs)
            for _ in range(runs):
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                req_count = {"api": 0}
                page.on("request", lambda req: req_count.__setitem__("api", req_count["api"] + 1) if "/api/" in req.url else None)
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda err: page_errors.append(str(err)))
                t0 = time.perf_counter()
                page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")
                page.wait_for_selector("#overview-kpis .kpi-card", timeout=15000)
                load_ms.append((time.perf_counter() - t0) * 1000.0)
                api_calls_initial.append(req_count["api"])

                t1 = time.perf_counter()
                page.click('button.nav-item[data-nav="logs"]')
                page.wait_for_selector("#logs-table-wrap table, #logs-table-wrap .empty-state", timeout=10000)
                page.click('button.nav-item[data-nav="workspace"]')
                page.wait_for_selector("#ws-query-input", timeout=10000)
                tab_switch_ms.append((time.perf_counter() - t1) * 1000.0)
                page.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)

    return {
        "available": True,
        "runs": runs,
        "initial_load_mean_ms": round(statistics.fmean(load_ms), 2) if load_ms else None,
        "initial_load_median_ms": round(statistics.median(load_ms), 2) if load_ms else None,
        "tab_switch_logs_to_workspace_mean_ms": round(statistics.fmean(tab_switch_ms), 2) if tab_switch_ms else None,
        "initial_api_requests_per_load": api_calls_initial,
        "console_error_count": len(console_errors),
        "page_error_count": len(page_errors),
    }


def run_benchmark(label: str = "baseline", *, include_browser: bool = True) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="tracerecover_p82_") as tmp_str:
        tmp_dir = Path(tmp_str)
        report: dict[str, Any] = {
            "label": label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "environment": {
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "live_llm_used": False,
            },
            "offline_execution": bench_offline_execution(tmp_dir),
            "mock_execution": bench_mock_execution(tmp_dir),
            "backend_api": bench_backend_api_endpoints(),
            "storage_scaling": bench_storage_scaling(tmp_dir),
            "concurrent_sessions": bench_concurrent_sessions(tmp_dir),
            "dashboard_chromium": bench_dashboard_chromium() if include_browser else {"available": False, "skipped": True},
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="TraceRecover Phase 8.2 Performance Benchmark")
    parser.add_argument("--label", default="baseline", help="Benchmark run label (e.g. before or after)")
    parser.add_argument("--output", default="", help="Optional JSON output file path")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Chromium dashboard benchmark")
    args = parser.parse_args()

    result = run_benchmark(label=args.label, include_browser=not args.skip_browser)
    rendered = json.dumps(result, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
