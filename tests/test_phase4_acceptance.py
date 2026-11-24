"""Final Phase 4 Chromium browser acceptance test across all 13 sections, buttons, and viewports."""

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
def isolated_phase4_server(tmp_path: Path):
    """Run an isolated Uvicorn instance seeded with flagship results for full acceptance checks."""
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

    log_file = (tmp_path / "uvicorn_phase4.log").open("w", encoding="utf-8")
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


def test_phase4_full_13_section_acceptance(isolated_phase4_server: str) -> None:
    """Verify all 13 UI sections, interactive workflows, network cleanliness, and viewports."""
    browser_exe = _find_browser_executable()
    with playwright_sync.sync_playwright() as p:
        launch_kwargs: dict[str, object] = {"headless": True}
        if browser_exe:
            launch_kwargs["executable_path"] = browser_exe
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        console_errors: list[str] = []
        page_errors: list[str] = []
        failed_requests: list[str] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "response",
            lambda r: failed_requests.append(f"{r.status} {r.url}")
            if r.status >= 400 and "/favicon.ico" not in r.url
            else None,
        )

        try:
            page.goto(isolated_phase4_server, wait_until="networkidle")
            page.wait_for_selector("#overview-kpis .kpi-card", timeout=10000)

            # All 13 sections must navigate cleanly
            all_sections = [
                "overview",
                "experiments",
                "analytics",
                "demo",
                "replay",
                "evidence",
                "datasets",
                "logs",
                "reports",
                "settings",
                "workspace",
                "unseen",
                "compare",
            ]
            for sec in all_sections:
                page.click(f'button.nav-item[data-nav="{sec}"]')
                assert page.locator(f"#tab-{sec}").is_visible(), f"Tab {sec} did not become visible"

            # Query Workspace -> follow-up conversation -> Replay & Evidence jumps
            page.click('button.nav-item[data-nav="workspace"]')
            page.fill("#ws-query-input", "Look up support ticket T-401 and calculate (1200 * 1.15)")
            page.click("#ws-submit-btn")
            page.wait_for_selector("#ws-answer-container .decision-box", timeout=20000)
            assert "1380" in page.locator("#ws-answer-container").inner_text()
            assert page.locator("#ws-timeline-stages .obs-card").count() >= 6

            # Click Replay Trace directly from Workspace answer card
            page.click('#ws-answer-container button:has-text("Replay Trace")')
            page.wait_for_function(
                "() => document.getElementById('replay-counter')?.textContent?.startsWith('Frame 1 of ')",
                timeout=10000,
            )
            assert page.locator("#tab-replay").is_visible()

            # Click back to Workspace and jump to Evidence Inspector
            page.click('button.nav-item[data-nav="workspace"]')
            page.click('#ws-answer-container button:has-text("Inspect Evidence")')
            page.wait_for_selector("#evidence-inspector-panel .decision-box", timeout=10000)
            assert page.locator("#tab-evidence").is_visible()

            # Check Unseen Eval Lab & Provider Compare
            page.click('button.nav-item[data-nav="unseen"]')
            page.click("#unseen-run-btn")
            page.wait_for_selector("#unseen-run-feedback .notice-success", timeout=30000)
            assert page.locator("#unseen-populations-grid .stage-block").count() == 5

            page.click('button.nav-item[data-nav="compare"]')
            page.click("#cmp-run-btn")
            page.wait_for_selector("#cmp-feedback .notice-success", timeout=20000)

            # Responsive desktop -> tablet -> mobile check across workspace/unseen/compare/overview
            for w, h in ((1440, 900), (768, 1024), (390, 844)):
                page.set_viewport_size({"width": w, "height": h})
                page.wait_for_timeout(150)
                overflow = page.evaluate(
                    "() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth})"
                )
                assert overflow["sw"] <= overflow["iw"] + 2, f"Overflow at {w}x{h}: {overflow}"

            assert console_errors == [], f"Console errors: {console_errors}"
            assert page_errors == [], f"Page errors: {page_errors}"
            assert failed_requests == [], f"Failed HTTP requests: {failed_requests}"
            assert page.evaluate("window.localStorage.length") == 0
            assert page.evaluate("window.sessionStorage.length") == 0
        finally:
            context.close()
            browser.close()
