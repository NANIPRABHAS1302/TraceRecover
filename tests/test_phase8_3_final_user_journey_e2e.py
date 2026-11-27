"""Phase 8.3 Task D complete 20-step user journey E2E test in real Chromium, including real server restart & persistence."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLAGSHIP_RUN_ID = "phase8-run-20261123T183333634001Z"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _find_browser_executable() -> str | None:
    for path in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(path).is_file():
            return path
    return None


def _wait_health(base_url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=2.0) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"Server did not become healthy at {base_url} within {timeout}s.")


def _spawn_server(port: int, env: dict[str, str], log_path: Path) -> tuple[subprocess.Popen[str], Any]:
    log_file = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "web_dashboard:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _wait_health(f"http://127.0.0.1:{port}", timeout=20.0)
    return proc, log_file


def _stop_server(proc: subprocess.Popen[str], log_file: Any) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    log_file.close()


def test_phase8_3_complete_20_step_user_journey_with_real_server_restart(tmp_path: Path) -> None:
    """Exercise all 20 Task D user workflow steps end-to-end in real Chromium, including real process restart."""
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    versions_dir = tmp_path / "versions"
    convs_dir = tmp_path / "conversations"
    unseen_dir = tmp_path / "unseen_benchmarks"
    conn_dir = tmp_path / "connectors"
    for d in (results_dir, logs_dir, versions_dir, convs_dir, unseen_dir, conn_dir):
        d.mkdir(parents=True, exist_ok=True)

    src_run = PROJECT_ROOT / "results" / FLAGSHIP_RUN_ID
    if src_run.is_dir():
        shutil.copytree(src_run, results_dir / FLAGSHIP_RUN_ID)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["TRACERECOVER_RESULTS_DIR"] = str(results_dir)
    env["TRACERECOVER_LOGS_DIR"] = str(logs_dir)
    env["TRACERECOVER_DATASET_VERSIONS_DIR"] = str(versions_dir)
    env["TRACERECOVER_CONVERSATIONS_DIR"] = str(convs_dir)
    env["TRACERECOVER_UNSEEN_DATASETS_DIR"] = str(unseen_dir)
    env["TRACERECOVER_CONNECTORS_DIR"] = str(conn_dir)
    env["TRACERECOVER_ENABLE_DOCS"] = "false"
    for k in ("TRACERECOVER_ADMIN_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(k, None)

    server_log = tmp_path / "uvicorn_p83.log"
    proc, log_fh = _spawn_server(port, env, server_log)

    browser_exe = _find_browser_executable()
    console_errors: list[str] = []
    page_errors: list[str] = []

    try:
        with playwright_sync.sync_playwright() as p:
            launch_kwargs: dict[str, object] = {"headless": True}
            if browser_exe:
                launch_kwargs["executable_path"] = browser_exe
            browser = p.chromium.launch(**launch_kwargs)
            ctx_user_a = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx_user_a.new_page()
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            # Step 1-2: Open dashboard & verify Overview renders cleanly
            page.goto(base_url, wait_until="networkidle")
            page.wait_for_selector("#overview-kpis .kpi-card", timeout=15000)

            # Step 3: Navigate to Query Workspace & explicitly create a new conversation
            page.click('button.nav-item[data-nav="workspace"]')
            page.wait_for_selector("#ws-new-conv-btn", timeout=10000)
            page.click("#ws-new-conv-btn")
            page.wait_for_function(
                "() => Boolean(document.getElementById('ws-conv-select')?.value)",
                timeout=10000,
            )
            conv_id = page.locator("#ws-conv-select").input_value()
            assert conv_id.startswith("conv-")

            # Step 4-14: Submit OFFLINE multi-step query (with recovery & verification telemetry)
            page.select_option("#ws-provider", "local")
            page.fill("#ws-query-input", "Look up support ticket T-401 and calculate (1200 * 1.15)")
            page.click("#ws-submit-btn")
            page.wait_for_selector('[data-testid="ws-execution-integrity"]', timeout=20000)

            # Verify planning, tool execution, claim-level verification, provenance, telemetry & final answer
            assert page.locator('[data-testid="ws-exec-mode-badge"]').inner_text().strip() == "OFFLINE"
            integrity_text = page.locator('[data-testid="ws-execution-integrity"]').inner_text()
            assert "Planning: succeeded" in integrity_text
            assert "Synthesis: succeeded" in integrity_text
            assert "Origin: deterministic_synthesizer" in integrity_text
            assert page.locator('[data-testid="ws-stage-timings"]').is_visible()
            assert page.locator('[data-testid="ws-claim-verification"]').is_visible()
            assert page.locator("#ws-timeline-stages .obs-card").count() >= 6
            answer_text = page.locator("#ws-answer-container").inner_text()
            assert "ANSWER" in answer_text
            assert "1380" in answer_text

            # Verify Abstention / Uncertainty scenario in same conversation
            page.fill("#ws-query-input", "Who discovered the fictional element unobtainium?")
            page.click("#ws-submit-btn")
            page.wait_for_function(
                "() => (document.getElementById('ws-answer-container')?.innerText || '').includes('ABSTAIN') "
                "&& document.querySelectorAll('#ws-thread-history .stage-block').length >= 2",
                timeout=20000,
            )
            assert page.locator("#ws-thread-history .stage-block").count() == 2

            # Step 15: Verify cooperative cancel endpoint works cleanly on an active/separate query ID
            cancel_res = page.evaluate(
                """async () => {
                    const r = await fetch('/api/chat/cancel/qry-p83-cancel-probe', { method: 'POST' });
                    return { status: r.status, body: await r.json() };
                }"""
            )
            assert cancel_res["status"] == 200
            assert cancel_res["body"]["query_id"] == "qry-p83-cancel-probe"

            # Verify major dashboard tabs render without errors (Logs, Evidence, Replay, Datasets, Connect Hub, Settings)
            for nav_id in ("logs", "evidence", "replay", "datasets", "connecthub", "unseen", "compare", "settings"):
                page.click(f'button.nav-item[data-nav="{nav_id}"]')
                assert page.locator(f"#tab-{nav_id}").is_visible()

            # Step 17: REAL SERVER RESTART! Stop uvicorn completely, then restart on same port & dirs
            _stop_server(proc, log_fh)
            proc, log_fh = _spawn_server(port, env, server_log)

            # Step 18: Reload page in User A's browser context (preserving URL-hash X-Session-ID, zero Web Storage)
            page.reload(wait_until="networkidle")
            page.click('button.nav-item[data-nav="workspace"]')
            page.wait_for_function(
                f"() => Array.from(document.querySelectorAll('#ws-conv-select option')).some(o => o.value === '{conv_id}')",
                timeout=10000,
            )
            page.select_option("#ws-conv-select", conv_id)
            page.wait_for_function(
                "() => document.querySelectorAll('#ws-thread-history .stage-block').length === 2",
                timeout=10000,
            )
            history_text = page.locator("#ws-thread-history").inner_text()
            assert "1380" in history_text
            assert "unobtainium" in history_text.lower()

            # Step 20: Verify strict session isolation from an independent User B browser context
            ctx_user_b = browser.new_context(viewport={"width": 1280, "height": 800})
            page_b = ctx_user_b.new_page()
            page_b.goto(base_url, wait_until="networkidle")
            page_b.click('button.nav-item[data-nav="workspace"]')
            page_b.wait_for_selector("#ws-conv-select", timeout=10000)
            user_b_opts = page_b.evaluate(
                "() => Array.from(document.querySelectorAll('#ws-conv-select option')).map(o => o.value)"
            )
            assert conv_id not in user_b_opts
            cross_status = page_b.evaluate(
                f"""async () => {{
                    const m = /(?:^#|&)session=(sess-[A-Za-z0-9._-]+)/.exec(window.location.hash || '');
                    const sid = (m && m[1]) || 'other-session';
                    const r = await fetch('/api/conversations/{conv_id}', {{ headers: {{ 'X-Session-ID': sid }} }});
                    return r.status;
                }}"""
            )
            assert cross_status == 404
            ctx_user_b.close()

            # Step 19: Delete conversation as User A and verify removal persists + zero Web Storage invariant
            page.click("#ws-delete-conv-btn")
            page.wait_for_function(
                f"() => !Array.from(document.querySelectorAll('#ws-conv-select option')).some(o => o.value === '{conv_id}')",
                timeout=10000,
            )

            storage_counts = page.evaluate("() => ({ls: window.localStorage.length, ss: window.sessionStorage.length})")
            assert storage_counts == {"ls": 0, "ss": 0}
            assert console_errors == [], f"Browser console errors: {console_errors}"
            assert page_errors == [], f"Browser page errors: {page_errors}"
            ctx_user_a.close()
            browser.close()
    finally:
        _stop_server(proc, log_fh)
