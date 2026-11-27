"""Phase 7.2 Real Chromium Playwright E2E Test for Execution Mode Badges & Provider Health Panel."""

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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _find_browser_executable() -> str | None:
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(p).is_file():
            return p
    return None


def _wait_for_server(url: str, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"Live dashboard did not start at {url}")


@pytest.fixture()
def phase72_live_server(tmp_path: Path):
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    conns_dir = tmp_path / "connectors"
    convs_dir = tmp_path / "conversations"
    for d in (results_dir, logs_dir, conns_dir, convs_dir):
        d.mkdir(parents=True, exist_ok=True)

    src_flagship = PROJECT_ROOT / "results" / "phase8-run-20261123T183333634001Z"
    if src_flagship.is_dir():
        shutil.copytree(src_flagship, results_dir / src_flagship.name, dirs_exist_ok=True)

    port = _free_port()
    env = os.environ.copy()
    env["TRACERECOVER_RESULTS_DIR"] = str(results_dir)
    env["TRACERECOVER_LOGS_DIR"] = str(logs_dir)
    env["TRACERECOVER_CONNECTORS_DIR"] = str(conns_dir)
    env["TRACERECOVER_CONVERSATIONS_DIR"] = str(convs_dir)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "web_dashboard:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(f"{base_url}/api/health")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_phase72_workspace_health_check_exec_badge_and_claim_ids(phase72_live_server: str) -> None:
    exe = _find_browser_executable()
    launch_kwargs = {"headless": True}
    if exe:
        launch_kwargs["executable_path"] = exe

    console_errors: list[str] = []
    page_errors: list[str] = []

    with playwright_sync.sync_playwright() as p:
        try:
            browser = p.chromium.launch(**launch_kwargs)
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")

        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        page.goto(phase72_live_server, wait_until="networkidle")
        page.locator('button.nav-item[data-nav="workspace"]').click()
        page.wait_for_selector("#tab-workspace.active")

        # Click Check Provider Health button and verify inline status renders cleanly
        page.locator("#ws-health-btn").click()
        page.wait_for_function(
            """() => {
                const hs = document.getElementById('ws-health-status');
                return hs && hs.innerText.includes('offline_local');
            }"""
        )

        # Submit multi-step ticket query and assert 5-state OFFLINE badge, telemetry & Claim IDs
        page.fill("#ws-query-input", "Look up ticket T-401 and calculate (1200 * 1.15)")
        page.locator("#ws-submit-btn").click()
        page.wait_for_function(
            """() => {
                const box = document.getElementById('ws-answer-container');
                return box
                    && box.querySelector('[data-testid="ws-exec-mode-badge"]')
                    && box.querySelector('[data-testid="ws-claim-verification"]');
            }"""
        )

        badge_txt = page.locator('[data-testid="ws-exec-mode-badge"]').inner_text()
        assert badge_txt.strip() == "OFFLINE"
        full_txt = page.locator("#ws-answer-container").inner_text()
        assert "CL-1" in full_txt
        assert "1380" in full_txt
        assert "Usage" in full_txt or "(none)" in full_txt

        storage_counts = page.evaluate("() => ({ls: window.localStorage.length, ss: window.sessionStorage.length})")
        assert storage_counts == {"ls": 0, "ss": 0}
        assert console_errors == []
        assert page_errors == []

        context.close()
        browser.close()
