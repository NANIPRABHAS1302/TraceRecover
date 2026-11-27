"""Phase 7.4 Holdout Integrity, Non-Overlap & Real Chromium Dashboard Transparency E2E Tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
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
DATA_DIR = PROJECT_ROOT / "data"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm_q(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def test_phase74_holdout_integrity_hashes_and_non_overlap() -> None:
    """Verify frozen SHA-256 hashes, strict gold separation, quotas, and zero overlap with prior benchmarks."""
    manifest = json.loads((DATA_DIR / "phase7_4_holdout_manifest.json").read_text(encoding="utf-8"))
    holdout_path = DATA_DIR / "phase7_4_independent_holdout.json"
    gold_path = DATA_DIR / "phase7_4_holdout_gold.json"

    assert _sha256_file(holdout_path) == manifest["files"]["phase7_4_independent_holdout.json"]["sha256"]
    assert _sha256_file(gold_path) == manifest["files"]["phase7_4_holdout_gold.json"]["sha256"]

    holdout = json.loads(holdout_path.read_text(encoding="utf-8"))
    gold = json.loads(gold_path.read_text(encoding="utf-8"))

    assert holdout["contains_gold_labels"] is False
    assert holdout["question_count"] >= 80
    assert holdout["domain_count"] >= 16
    assert len(holdout["tasks"]) == len(gold["tasks"])

    forbidden_gold_keys = {
        "expected_decision",
        "expected_answer_criteria",
        "expected_contradiction_label",
        "expected_contradiction_class",
        "expected_citation_relationships",
    }
    for t in holdout["tasks"]:
        assert not (forbidden_gold_keys & set(t.keys())), f"Gold leakage in public holdout task {t['task_id']}"

    # Verify all 9 category quotas
    cats = holdout["category_counts"]
    assert cats["general_factual_research"] >= 12
    assert cats["technical_documentation"] >= 12
    assert cats["academic_research"] >= 12
    assert cats["multi_step_research"] >= 10
    assert cats["multi_source_research"] >= 10
    assert cats["study_provenance_attribution"] >= 12
    assert cats["context_aware_contradiction"] >= 12
    assert cats["insufficient_evidence_abstention"] >= 8
    assert cats["numerical_temporal_verification"] >= 8

    # Verify zero overlap with all 5 historical datasets
    hist_norm: set[str] = set()
    for hf in (
        PROJECT_ROOT / "eval" / "tasks.json",
        DATA_DIR / "phase6_unseen_50.json",
        DATA_DIR / "phase7_unseen_60.json",
        DATA_DIR / "phase7_1_unseen.json",
        DATA_DIR / "phase7_2_live_unseen.json",
    ):
        if not hf.exists():
            continue
        raw = json.loads(hf.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("tasks", [])
        for it in items:
            q = it.get("question") or it.get("query") or ""
            if q:
                hist_norm.add(_norm_q(q))

    for t in holdout["tasks"]:
        assert _norm_q(t["question"]) not in hist_norm, f"Holdout overlaps prior benchmark: {t['task_id']}"


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
def phase74_live_server(tmp_path: Path):
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


def test_phase74_dashboard_workspace_transparency_and_abstention_e2e(phase74_live_server: str) -> None:
    """Task I: Verify all required execution, provenance, contradiction, abstention, and cost fields in Chromium."""
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

        page.goto(phase74_live_server, wait_until="networkidle")
        page.locator('button.nav-item[data-nav="workspace"]').click()
        page.wait_for_selector("#tab-workspace.active")

        # 1. Answerable multi-step query
        page.fill("#ws-query-input", "Look up ticket T-205 and compute (640 / 8) + 195")
        page.locator("#ws-submit-btn").click()
        page.wait_for_function(
            """() => {
                const box = document.getElementById('ws-answer-container');
                return box
                    && box.innerText.includes('275')
                    && box.querySelector('[data-testid="ws-exec-mode-badge"]')
                    && box.querySelector('[data-testid="ws-claim-verification"]');
            }"""
        )

        badge_txt = page.locator('[data-testid="ws-exec-mode-badge"]').inner_text().strip()
        assert badge_txt == "OFFLINE"
        assert "LIVE LLM" not in badge_txt

        ans_txt = page.locator("#ws-answer-container").inner_text()
        ans_low = ans_txt.lower()
        assert "deterministic-local" in ans_txt
        assert "Research Plan:" in ans_txt
        assert "Source Selection:" in ans_txt
        assert "Retrieved Evidence" in ans_txt
        assert "study / attribution" in ans_low
        assert "contradiction classification" in ans_low
        assert "CL-1" in ans_txt
        assert "SUPPORTED" in ans_txt
        assert "Tokens:" in ans_txt
        assert "zero_local" in ans_txt
        assert "Structured Synthesis & Limitations" in ans_txt

        # 2. Insufficient-evidence abstention query
        page.locator("#ws-new-conv-btn").click()
        page.fill(
            "#ws-query-input",
            "What exact throughput score will unreleased internal chip ORION-Z9 achieve in Q4 2032 benchmarks?",
        )
        page.locator("#ws-submit-btn").click()
        page.wait_for_function(
            """() => {
                const box = document.getElementById('ws-answer-container');
                return box && box.innerText.includes('ABSTAIN') && box.innerText.includes('Why / Abstention Reason:');
            }"""
        )
        abs_badge = page.locator('[data-testid="ws-exec-mode-badge"]').inner_text().strip()
        assert abs_badge == "OFFLINE"
        assert "LIVE LLM" not in abs_badge
        abs_txt = page.locator("#ws-answer-container").inner_text()
        assert "ABSTAIN" in abs_txt
        assert "Why / Abstention Reason:" in abs_txt

        storage_counts = page.evaluate("() => ({ls: window.localStorage.length, ss: window.sessionStorage.length})")
        assert storage_counts == {"ls": 0, "ss": 0}
        assert console_errors == []
        assert page_errors == []

        context.close()
        browser.close()
