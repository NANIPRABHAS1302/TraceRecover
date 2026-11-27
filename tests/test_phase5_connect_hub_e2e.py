"""Real Chromium Playwright E2E tests verifying Connect Hub 9 subsections, MCP workflow, and storage hygiene."""

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
MOCK_MCP_SERVER = PROJECT_ROOT / "tests" / "fixtures" / "mock_mcp_server.py"


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
    for p in candidates:
        if Path(p).is_file():
            return p
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
    raise RuntimeError(f"Live dashboard did not respond at {url}")


@pytest.fixture()
def connect_hub_live_server(tmp_path: Path):
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    connectors_dir = tmp_path / "connectors"
    results_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    connectors_dir.mkdir(parents=True, exist_ok=True)

    src_flagship = PROJECT_ROOT / "results" / "phase8-run-20261123T183333634001Z"
    if src_flagship.is_dir():
        shutil.copytree(src_flagship, results_dir / src_flagship.name, dirs_exist_ok=True)

    port = _free_port()
    env = os.environ.copy()
    env["TRACERECOVER_RESULTS_DIR"] = str(results_dir)
    env["TRACERECOVER_LOGS_DIR"] = str(logs_dir)
    env["TRACERECOVER_CONNECTORS_DIR"] = str(connectors_dir)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "web_dashboard:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def test_connect_hub_9_subsections_mcp_add_connect_inspect_and_cleanup(connect_hub_live_server: str) -> None:
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
            pytest.skip(f"Chromium unavailable for Playwright E2E: {exc}")

        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        page.goto(connect_hub_live_server, wait_until="networkidle")
        page.locator('button.nav-item[data-nav="connecthub"]').click()
        page.wait_for_selector("#tab-connecthub.active")
        page.wait_for_selector("#ch-subsection-body")

        # Exercise all 9 Connect Hub subsections cleanly
        for sub in ("overview", "mcp", "browser", "search", "research", "developer", "custom_api", "logs", "security"):
            page.locator(f'button.ch-subnav-btn[data-ch-sub="{sub}"]').click()
            page.wait_for_timeout(150)

        # Back to MCP Servers & add real local stdio fixture MCP server
        page.locator('button.ch-subnav-btn[data-ch-sub="mcp"]').click()
        page.locator("#ch-toggle-add-btn").click()
        page.fill("#ch-name", "E2E Fixture MCP")
        page.select_option("#ch-category", "mcp")
        page.select_option("#ch-transport", "stdio")
        page.fill("#ch-command", sys.executable)
        page.fill("#ch-args", str(MOCK_MCP_SERVER))
        page.locator('#ch-connector-form button[type="submit"]').click()

        page.wait_for_selector('tr[data-connector-id="e2e-fixture-mcp"]')
        row = page.locator('tr[data-connector-id="e2e-fixture-mcp"]')

        # Click Connect -> runs real MCP handshake & tool discovery
        row.get_by_role("button", name="Connect", exact=True).click()
        page.wait_for_function(
            """() => {
                const r = document.querySelector('tr[data-connector-id="e2e-fixture-mcp"]');
                return r && r.innerText.includes('connected') && r.innerText.includes('Tools (3)');
            }"""
        )

        # Inspect discovered tools
        row.get_by_role("button", name="Tools (3)", exact=True).click()
        page.wait_for_selector("#ch-tools-drawer")
        assert "get_sensor_reading" in page.locator("#ch-tools-drawer").inner_text()

        # Verify browser inspection subsection against live local server (explicitly allowlisting 127.0.0.1)
        page.locator('button.ch-subnav-btn[data-ch-sub="browser"]').click()
        page.fill("#ch-browser-url", f"{connect_hub_live_server}/api/health")
        page.fill("#ch-browser-allow", "127.0.0.1")
        page.locator('#ch-browser-form button[type="submit"]').click()
        page.wait_for_function(
            """() => {
                const el = document.getElementById('ch-browser-result');
                return el && el.innerText.includes('HTTP 200');
            }"""
        )

        # Verify zero secrets or config in browser storage
        storage_counts = page.evaluate("() => ({ls: window.localStorage.length, ss: window.sessionStorage.length})")
        assert storage_counts == {"ls": 0, "ss": 0}

        # Accept confirm dialog and Remove the temporary fixture MCP connector cleanly
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator('button.ch-subnav-btn[data-ch-sub="mcp"]').click()
        page.locator('tr[data-connector-id="e2e-fixture-mcp"]').get_by_role("button", name="Remove", exact=True).click()
        page.wait_for_function("() => !document.querySelector('tr[data-connector-id=\"e2e-fixture-mcp\"]')")

        assert console_errors == []
        assert page_errors == []
        context.close()
        browser.close()
