"""Real Chromium E2E tests for Phase 3 Open-Ended Workspace, Unseen Eval Lab, and Provider Compare."""

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


def _wait_for_server(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"Server did not start at {url} within {timeout}s.")


@pytest.fixture()
def isolated_phase3_server(tmp_path: Path):
    """Run an isolated Uvicorn instance so Phase 3 E2E never pollutes real repo data."""
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    versions_dir = tmp_path / "versions"
    convs_dir = tmp_path / "conversations"
    unseen_dir = tmp_path / "unseen_benchmarks"
    for d in (results_dir, logs_dir, versions_dir, convs_dir, unseen_dir):
        d.mkdir(parents=True, exist_ok=True)

    src_run = PROJECT_ROOT / "results" / FLAGSHIP_RUN_ID
    if src_run.is_dir():
        shutil.copytree(src_run, results_dir / FLAGSHIP_RUN_ID)

    port = _free_port()
    env = os.environ.copy()
    env["TRACERECOVER_RESULTS_DIR"] = str(results_dir)
    env["TRACERECOVER_LOGS_DIR"] = str(logs_dir)
    env["TRACERECOVER_DATASET_VERSIONS_DIR"] = str(versions_dir)
    env["TRACERECOVER_CONVERSATIONS_DIR"] = str(convs_dir)
    env["TRACERECOVER_UNSEEN_DATASETS_DIR"] = str(unseen_dir)
    env["TRACERECOVER_ENABLE_DOCS"] = "false"
    env.pop("TRACERECOVER_ADMIN_TOKEN", None)
    env.pop("GEMINI_API_KEY", None)

    log_file = (tmp_path / "uvicorn_phase3.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "web_dashboard:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(f"{base_url}/api/health", timeout=20.0)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log_file.close()


def test_phase3_workspace_unseen_and_compare_browser_e2e(isolated_phase3_server: str) -> None:
    """Verify Sections 11, 12, and 13 end-to-end in real Chromium without storage/console errors."""
    browser_exe = _find_browser_executable()
    canonical_tasks = PROJECT_ROOT / "eval" / "tasks.json"
    canonical_before = canonical_tasks.read_bytes()

    with playwright_sync.sync_playwright() as p:
        launch_kwargs: dict[str, object] = {"headless": True}
        if browser_exe:
            launch_kwargs["executable_path"] = browser_exe
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        console_errors: list[str] = []
        page_errors: list[str] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        try:
            page.goto(isolated_phase3_server, wait_until="networkidle")
            page.wait_for_selector("#overview-kpis .kpi-card", timeout=10000)

            # 11. Query Workspace: multi-line Unicode submit -> live 10-stage SSE timeline -> follow-up -> history controls
            page.click('button.nav-item[data-nav="workspace"]')
            assert page.locator("#tab-workspace").is_visible()

            page.fill("#ws-query-input", "Line 1 (€ Unicode probe):\nLook up support ticket T-401 and calculate (1200 * 1.15)")
            page.click("#ws-submit-btn")
            page.wait_for_selector("#ws-answer-container .decision-box", timeout=20000)
            ans_text = page.locator("#ws-answer-container").inner_text()
            assert "ANSWER" in ans_text
            assert "1380" in ans_text
            assert page.locator("#ws-timeline-stages .obs-card").count() >= 6
            assert "10. Final Decision" in page.locator("#ws-timeline-stages").inner_text()

            # Consecutive follow-up inside the created conversation
            page.fill("#ws-query-input", "Compute (50 * 4) + 25")
            page.click("#ws-submit-btn")
            page.wait_for_function(
                "() => (document.getElementById('ws-answer-container')?.innerText || '').includes('225') "
                "&& document.querySelectorAll('#ws-thread-history .stage-block').length >= 2",
                timeout=20000,
            )
            assert page.locator("#ws-thread-history .stage-block").count() >= 2

            # Clear & delete controls + New Conversation
            page.click("#ws-clear-conv-btn")
            page.wait_for_timeout(300)
            page.click("#ws-delete-conv-btn")
            page.wait_for_timeout(300)
            page.click("#ws-new-conv-btn")
            page.wait_for_timeout(300)

            # 12. Unseen Eval Lab: generate & run 13-task benchmark + inspect population separation
            page.click('button.nav-item[data-nav="unseen"]')
            assert page.locator("#tab-unseen").is_visible()
            page.select_option("#unseen-num-tasks", "13")
            page.click("#unseen-run-btn")
            page.wait_for_selector("#unseen-run-feedback .notice-success", timeout=30000)
            assert page.locator("#unseen-populations-grid .stage-block").count() == 5
            assert page.locator("#unseen-category-table tbody tr").count() == 13
            assert page.locator("#unseen-tasks-table tbody tr").count() == 13

            # 13. Provider Compare: compare local + unavailable gemini cleanly
            page.click('button.nav-item[data-nav="compare"]')
            assert page.locator("#tab-compare").is_visible()
            page.fill("#cmp-question", "Compute (14 * 7) + 2")
            page.click("#cmp-run-btn")
            page.wait_for_selector("#cmp-feedback .notice-success", timeout=20000)
            cmp_text = page.locator("#cmp-results-wrap").inner_text()
            assert "offline_deterministic_local" in cmp_text
            assert "unavailable" in cmp_text

            # Mobile responsive check across Phase 3 tabs
            page.set_viewport_size({"width": 390, "height": 844})
            for sec in ("workspace", "unseen", "compare"):
                page.click("#mobile-menu-btn")
                page.click(f'button.nav-item[data-nav="{sec}"]')
                page.wait_for_timeout(150)
                overflow = page.evaluate(
                    "() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth})"
                )
                assert overflow["sw"] <= overflow["iw"] + 2, f"Overflow on mobile tab {sec}: {overflow}"

            assert console_errors == [], f"Console errors: {console_errors}"
            assert page_errors == [], f"Page errors: {page_errors}"
            assert page.evaluate("window.localStorage.length") == 0
            assert page.evaluate("window.sessionStorage.length") == 0
        finally:
            context.close()
            browser.close()

    assert canonical_tasks.read_bytes() == canonical_before
