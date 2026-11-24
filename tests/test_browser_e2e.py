"""Real Chromium browser E2E tests for the TraceRecover 10-section white dashboard."""

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
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
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
    raise RuntimeError(f"Live dashboard server did not respond at {url} within {timeout}s.")


@pytest.fixture()
def isolated_live_server(tmp_path: Path):
    """Run a clean Uvicorn dashboard server against an isolated tmp workspace copy."""
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    versions_dir = tmp_path / "data_versions"
    results_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    versions_dir.mkdir(parents=True, exist_ok=True)

    src_run = PROJECT_ROOT / "results" / FLAGSHIP_RUN_ID
    if src_run.is_dir():
        shutil.copytree(src_run, results_dir / FLAGSHIP_RUN_ID)

    src_eval_logs = (
        PROJECT_ROOT
        / "logs"
        / "evaluation"
        / FLAGSHIP_RUN_ID
        / "tracerecover-full"
        / "failure-40"
        / "MHSM-01"
    )
    if src_eval_logs.is_dir():
        dst_eval = (
            logs_dir
            / "evaluation"
            / FLAGSHIP_RUN_ID
            / "tracerecover-full"
            / "failure-40"
            / "MHSM-01"
        )
        shutil.copytree(src_eval_logs, dst_eval)

    port = _free_port()
    env = os.environ.copy()
    env["TRACERECOVER_RESULTS_DIR"] = str(results_dir)
    env["TRACERECOVER_LOGS_DIR"] = str(logs_dir)
    env["TRACERECOVER_DATASET_VERSIONS_DIR"] = str(versions_dir)
    env["TRACERECOVER_ENABLE_DOCS"] = "false"
    env.pop("TRACERECOVER_ADMIN_TOKEN", None)

    log_file = (tmp_path / "uvicorn.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "web_dashboard:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
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


def test_full_dashboard_browser_e2e(isolated_live_server: str) -> None:
    """Exercise all 10 UI sections, replay, filters, exports, security, and responsive viewports."""
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
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        try:
            page.goto(isolated_live_server, wait_until="networkidle")

            # Verify initial white dashboard shell and client-side storage cleanliness
            assert "TraceRecover" in page.title()
            page.wait_for_selector("#overview-kpis .kpi-card", timeout=10000)
            assert page.evaluate("window.localStorage.length") == 0
            assert page.evaluate("window.sessionStorage.length") == 0
            assert page.locator("#overview-comparison-chart svg").count() >= 1
            assert page.locator("#overview-datasets-grid .card, #overview-datasets-grid > div").count() >= 1

            # 1-10 sidebar navigation check
            sections = [
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
            ]
            for sec in sections:
                page.click(f'button.nav-item[data-nav="{sec}"]')
                assert page.locator(f"#tab-{sec}").is_visible(), f"Section {sec} should be visible"

            # 2. Experiments: run quick 10-task reproducible benchmark
            page.click('button.nav-item[data-nav="experiments"]')
            page.select_option("#exp-num-tasks", "10")
            page.uncheck("#exp-run-stat")
            page.click("#run-experiment-btn")
            page.wait_for_selector("#exp-run-feedback .notice-success", timeout=25000)
            assert page.locator("#exp-history-table tr").count() >= 2

            # Switch global run back to flagship run for full analytics checks
            page.select_option("#global-run-select", FLAGSHIP_RUN_ID)
            page.wait_for_timeout(300)

            # 3. Results & Analytics: filters + real SVG charts + plots
            page.click('button.nav-item[data-nav="analytics"]')
            page.wait_for_selector("#analytics-svg-success svg", timeout=10000)
            assert page.locator("#analytics-svg-category svg").count() >= 1
            page.select_option("#analytics-rate-filter", "40")
            assert page.locator("#analytics-metrics-table tbody tr").count() >= 1
            assert page.locator("#analytics-plots-grid img").count() >= 1

            # 4. Live Demo: preset + fault injection execution
            page.click('button.nav-item[data-nav="demo"]')
            page.wait_for_selector('button[data-preset="normal_answer"]', timeout=10000)
            page.click('button[data-preset="normal_answer"]')
            page.click("#demo-submit-btn")
            page.wait_for_selector("#demo-output-container .decision-box", timeout=15000)
            demo_text = page.locator("#demo-output-container").inner_text()
            assert "ANSWER" in demo_text
            assert "COMMITTED" in demo_text

            page.click('button[data-preset="fault_recovery_40"]')
            page.click("#demo-submit-btn")
            page.wait_for_timeout(400)
            page.wait_for_selector("#demo-output-container .decision-box", timeout=15000)

            # 5. Execution Replay: load trace, Next, Prev, Play, Pause, Restart + eval trace
            page.click('button.nav-item[data-nav="replay"]')
            page.click("#replay-refresh-btn")
            page.wait_for_selector(
                '#replay-trace-select option[value="0"]', state="attached", timeout=10000
            )
            page.click("#replay-load-btn")
            page.wait_for_function(
                "() => document.getElementById('replay-counter')?.textContent?.startsWith('Frame 1 of ')",
                timeout=10000,
            )
            initial_counter = page.locator("#replay-counter").inner_text()
            assert initial_counter.startswith("Frame 1 of ")

            page.click("#replay-next-btn")
            second_counter = page.locator("#replay-counter").inner_text()
            assert second_counter.startswith("Frame 2 of ")

            page.click("#replay-prev-btn")
            assert page.locator("#replay-counter").inner_text().startswith("Frame 1 of ")

            page.click("#replay-play-btn")
            page.wait_for_timeout(1200)
            page.click("#replay-pause-btn")
            page.click("#replay-restart-btn")
            assert page.locator("#replay-counter").inner_text().startswith("Frame 1 of ")

            # Switch to eval stored trace replay (copied MHSM-01 @ 40%)
            page.select_option("#replay-source-type", "eval")
            eval_opts = page.locator("#replay-trace-select option").count()
            if eval_opts > 0:
                page.click("#replay-load-btn")
                page.wait_for_selector("#replay-stage-content", timeout=10000)
                assert page.locator("#replay-counter").inner_text().startswith("Frame 1 of ")

            # 6. Evidence Inspector
            page.click('button.nav-item[data-nav="evidence"]')
            page.wait_for_timeout(300)
            assert page.locator("#evidence-inspector-panel").is_visible()

            # 7. Datasets: catalog, filtering, detail inspection, validate + safe versioned import
            page.click('button.nav-item[data-nav="datasets"]')
            page.wait_for_selector("#datasets-catalog-grid", timeout=10000)
            page.select_option("#ds-abs-filter", "true")
            page.wait_for_timeout(400)
            first_row = page.locator("#ds-tasks-table tbody tr").first
            if first_row.count() > 0:
                first_row.click()
                page.wait_for_timeout(200)
                assert page.locator("#ds-task-detail").inner_text().strip() != ""

            page.click("#ds-load-sample-btn")
            page.click("#ds-validate-btn")
            page.wait_for_function(
                "() => (document.getElementById('ds-import-feedback')?.innerText || '').includes('Schema valid')",
                timeout=10000,
            )
            with page.expect_response(
                lambda r: "/api/datasets/import" in r.url, timeout=10000
            ) as imp_resp:
                page.click("#ds-import-btn")
            assert imp_resp.value.status == 200, (
                f"Import HTTP {imp_resp.value.status}: {imp_resp.value.text()} | "
                f"console={console_errors} | page={page_errors}"
            )
            page.wait_for_selector("#ds-history-table tbody tr", timeout=10000)
            fb_text = page.locator("#ds-import-feedback").inner_text()
            assert "Imported" in fb_text, f"Dataset import feedback unexpected: {fb_text!r}"
            assert page.locator("#ds-history-table tbody tr").count() >= 1

            # 8. Execution Logs
            page.click('button.nav-item[data-nav="logs"]')
            page.click("#logs-refresh-btn")
            page.wait_for_timeout(300)
            assert page.locator("#logs-table-wrap").is_visible()

            # 9. Research Reports & 4 Export formats (CSV, JSON, MD, PDF)
            page.click('button.nav-item[data-nav="reports"]')
            page.wait_for_function(
                "() => (document.getElementById('reports-md-preview')?.innerText || '').includes('TraceRecover')",
                timeout=10000,
            )
            assert "TraceRecover" in page.locator("#reports-md-preview").inner_text()

            for endpoint in (
                f"/api/export/csv?run_id={FLAGSHIP_RUN_ID}",
                f"/api/export/json?run_id={FLAGSHIP_RUN_ID}",
                f"/api/export/report?run_id={FLAGSHIP_RUN_ID}",
                f"/api/export/pdf?run_id={FLAGSHIP_RUN_ID}",
            ):
                res = page.request.get(f"{isolated_live_server}{endpoint}")
                assert res.status == 200
                if "/export/pdf" in endpoint:
                    assert res.body()[:5] == b"%PDF-"

            # 10. Settings
            page.click('button.nav-item[data-nav="settings"]')
            page.wait_for_selector("#settings-container", timeout=10000)
            settings_text = page.locator("#settings-container").inner_text()
            assert "max_total_attempts" in settings_text or "Two-tier" in settings_text

            # Responsive layout check: Tablet (768x1024)
            page.set_viewport_size({"width": 768, "height": 1024})
            page.wait_for_timeout(200)
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth + 2"
            )

            # Responsive layout check: Mobile (390x844) + drawer toggle
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            overflow = page.evaluate(
                "() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth})"
            )
            assert overflow["sw"] <= overflow["iw"] + 2, f"Mobile horizontal overflow: {overflow}"
            page.click("#mobile-menu-btn")
            assert "open" in (page.locator("#app-sidebar").get_attribute("class") or "")
            page.click('button.nav-item[data-nav="overview"]')
            assert "open" not in (page.locator("#app-sidebar").get_attribute("class") or "")

            assert console_errors == [], f"Console errors observed: {console_errors}"
            assert page_errors == [], f"Uncaught page errors observed: {page_errors}"
            assert page.evaluate("window.localStorage.length") == 0
            assert page.evaluate("window.sessionStorage.length") == 0
        finally:
            context.close()
            browser.close()

    assert canonical_tasks.read_bytes() == canonical_before
