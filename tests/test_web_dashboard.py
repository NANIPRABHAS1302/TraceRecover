"""Integration, analytics, export, and security tests for the TraceRecover web dashboard."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web_dashboard


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide a TestClient isolated to a temporary logs directory for write endpoints."""
    monkeypatch.setattr(web_dashboard, "LOGS_DIR", tmp_path)
    monkeypatch.delenv("TRACERECOVER_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("TRACERECOVER_RATE_LIMIT_RUN", raising=False)
    monkeypatch.delenv("TRACERECOVER_RATE_LIMIT_EXPERIMENTS", raising=False)
    monkeypatch.delenv("TRACERECOVER_MAX_REQUEST_BYTES", raising=False)
    web_dashboard.reset_rate_limiter()
    return TestClient(web_dashboard.app)


def test_health_endpoint_masks_keys_and_reports_active_benchmark(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "ok"
    assert body["test_suite"]["verified_core_tests"] == 122
    assert "122/122" in body["test_suite"]["label"]

    provider_ids = {p["id"] for p in body["providers"]}
    assert {"local", "gemini", "openai", "anthropic"}.issubset(provider_ids)
    # Verify no raw secret values or host C:\ paths leak
    serialized = str(body)
    assert "C:\\" not in serialized
    for env_key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TRACERECOVER_ADMIN_TOKEN"):
        secret = os.getenv(env_key, "").strip()
        if secret:
            assert secret not in serialized

    assert body["active_benchmark"] is not None
    assert body["active_benchmark"]["task_count"] == 100
    assert body["active_benchmark"]["has_statistical_validation"] is True
    assert body["active_benchmark"]["plot_count"] == 6


def test_presets_endpoint_returns_six_scenarios(client: TestClient) -> None:
    response = client.get("/api/presets")
    assert response.status_code == 200
    body = response.json()

    assert body["count"] == 6
    assert len(body["presets"]) == 6
    ids = {p["id"] for p in body["presets"]}
    assert ids == {
        "normal_answer",
        "multi_hop_lookup",
        "fault_recovery_40",
        "missing_entity_abstain",
        "contradiction_guard",
        "ablation_toggle",
    }


def test_run_endpoint_normal_recovery_abstain_and_contradiction(client: TestClient) -> None:
    # 1. Normal grounded ANSWER
    res_ok = client.post(
        "/api/run",
        json={
            "question": "What is the capital of France?",
            "provider": "local",
            "failure_rate": 0,
            "seed": 42,
            "use_nli": True,
            "enable_recovery": True,
            "enable_grounding": True,
            "enable_uncertainty": True,
        },
    )
    assert res_ok.status_code == 200
    ok_data = res_ok.json()
    assert ok_data["final_decision"] == "ANSWER"
    assert ok_data["grounding_report"]["supported_count"] >= 1
    assert ok_data["provenance"]["committed_steps"] >= 1
    assert ok_data["provenance"]["discarded_steps"] == 0
    assert ok_data["provider_scope"] == "offline_deterministic_local"

    # 2. 40% Fault Injection + Self-Healing Recovery (seed=13 triggers wrong_output -> TOOL_SWITCH)
    res_heal = client.post(
        "/api/run",
        json={
            "question": "What is 19 * 23 + 4?",
            "provider": "local",
            "failure_rate": 40,
            "seed": 13,
            "use_nli": True,
            "enable_recovery": True,
            "enable_grounding": True,
            "enable_uncertainty": True,
        },
    )
    assert res_heal.status_code == 200
    heal_data = res_heal.json()
    assert heal_data["failure_report"]["total_failures"] >= 1
    assert heal_data["recovery_report"]["successful_recoveries"] >= 1
    assert heal_data["provenance"]["discarded_steps"] >= 1
    assert heal_data["provenance"]["committed_steps"] >= 1
    assert heal_data["final_decision"] in {"ANSWER", "WARN"}

    # 3. Missing entity ABSTAIN
    res_abs = client.post(
        "/api/run",
        json={
            "question": "Who discovered the fictional element unobtainium?",
            "provider": "local",
            "failure_rate": 0,
            "seed": 42,
            "use_nli": True,
            "enable_recovery": True,
            "enable_grounding": True,
            "enable_uncertainty": True,
        },
    )
    assert res_abs.status_code == 200
    assert res_abs.json()["final_decision"] == "ABSTAIN"

    # 4. Contradiction probe -> ABSTAIN
    res_con = client.post(
        "/api/run",
        json={
            "question": "What is the capital of France?",
            "provider": "local",
            "scenario_mode": "contradiction_probe",
            "use_nli": True,
        },
    )
    assert res_con.status_code == 200
    con_data = res_con.json()
    assert con_data["final_decision"] == "ABSTAIN"
    assert con_data["grounding_report"]["contradicted_count"] >= 1


def test_run_endpoint_validates_thresholds(client: TestClient) -> None:
    response = client.post(
        "/api/run",
        json={
            "question": "What is 2 + 2?",
            "provider": "local",
            "answer_threshold": 0.6,
            "abstain_threshold": 0.4,
        },
    )
    assert response.status_code == 422


def test_traces_list_and_detail_endpoints(client: TestClient) -> None:
    run_resp = client.post(
        "/api/run",
        json={
            "question": "What is 19 * 23 + 4?",
            "provider": "local",
            "failure_rate": 40,
            "seed": 13,
            "use_nli": True,
        },
    )
    assert run_resp.status_code == 200
    trace_id = run_resp.json()["trace_id"]

    list_resp = client.get("/api/traces")
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body["total_files"] >= 1
    assert any(item["trace_id"] == trace_id for item in list_body["traces"])

    detail_resp = client.get(f"/api/traces/{trace_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["trace_id"] == trace_id
    assert len(detail["steps"]) >= 2
    states = {step["provenance_state"] for step in detail["steps"]}
    assert "discarded" in states
    assert "committed" in states

    # Path traversal must be rejected
    bad_resp = client.get("/api/traces/..%2Fconfig")
    assert bad_resp.status_code in {400, 404}


def test_benchmark_plots_and_settings_endpoints(client: TestClient) -> None:
    bench_resp = client.get("/api/benchmark")
    assert bench_resp.status_code == 200
    bench = bench_resp.json()

    assert bench["run_id"] == "phase8-run-20261123T183333634001Z"
    assert len(bench["metrics_summary"]) == 15
    assert bench["statistical_validation"] is not None
    assert len(bench["plots"]) == 6

    first_plot = bench["plots"][0]
    plot_resp = client.get(first_plot["url"])
    assert plot_resp.status_code == 200
    assert "image/svg+xml" in plot_resp.headers["content-type"]
    assert "<svg" in plot_resp.text

    settings_resp = client.get("/api/settings")
    assert settings_resp.status_code == 200
    settings = settings_resp.json()
    assert settings["recovery"]["max_retries"] == 1
    assert settings["uncertainty_and_abstention"]["default_thresholds"] == {
        "answer_threshold": 0.15,
        "abstain_threshold": 0.55,
    }
    assert "DeBERTa" in settings["grounding_and_nli"]["default_nli_model"]


def test_root_serves_compiled_frontend_spa(client: TestClient) -> None:
    root_resp = client.get("/")
    assert root_resp.status_code == 200
    assert "text/html" in root_resp.headers["content-type"]
    assert "TraceRecover" in root_resp.text
    assert 'id="root"' in root_resp.text


def test_dataset_preview_and_analytics_and_exports(client: TestClient) -> None:
    ds_resp = client.get("/api/dataset-preview")
    assert ds_resp.status_code == 200
    ds = ds_resp.json()
    assert ds["all_installed"] is True
    assert ds["total_datasets"] == 4
    assert all("full_path" not in item for item in ds["datasets"])

    exps_resp = client.get("/api/experiments")
    assert exps_resp.status_code == 200
    exps = exps_resp.json()
    assert exps["active_run_id"] == "phase8-run-20261123T183333634001Z"
    assert exps["count"] >= 1

    detail_resp = client.get(f"/api/experiments/{exps['active_run_id']}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert len(detail["category_breakdown"]) == 15  # 5 categories x 3 rates

    analytics_resp = client.get("/api/analytics")
    assert analytics_resp.status_code == 200
    analytics = analytics_resp.json()
    assert analytics["best_configuration"]["winner"] == "tracerecover"
    assert analytics["evaluation_context"]["provider_mode"] == "offline_deterministic_local"
    assert len(analytics["category_breakdown"]) == 15

    csv_resp = client.get("/api/export/csv")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "tracerecover" in csv_resp.text

    json_resp = client.get("/api/export/json")
    assert json_resp.status_code == 200
    assert "application/json" in json_resp.headers["content-type"]
    assert json_resp.json()["run_id"] == "phase8-run-20261123T183333634001Z"

    rep_resp = client.get("/api/export/report")
    assert rep_resp.status_code == 200
    assert "text/markdown" in rep_resp.headers["content-type"]
    assert "# TraceRecover Experiment Report" in rep_resp.text


def test_experiment_execution_and_security_controls(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_results = tmp_path / "results"
    isolated_logs = tmp_path / "logs"
    monkeypatch.setattr(web_dashboard, "RESULTS_DIR", isolated_results)
    monkeypatch.setattr(web_dashboard, "LOGS_DIR", isolated_logs)

    # 1. Security headers must always be present
    health_resp = client.get("/api/health")
    assert health_resp.headers["x-content-type-options"] == "nosniff"
    assert health_resp.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in health_resp.headers["content-security-policy"]

    # 2. Admin token enforcement on POST /api/experiments/run
    monkeypatch.setenv("TRACERECOVER_ADMIN_TOKEN", "secret-review-token-123")
    unauth = client.post(
        "/api/experiments/run",
        json={"failure_rates": [0.0, 20.0], "num_tasks": 5, "seed": 42},
    )
    assert unauth.status_code == 401

    wrong_auth = client.post(
        "/api/experiments/run",
        headers={"Authorization": "Bearer wrong-token"},
        json={"failure_rates": [0.0, 20.0], "num_tasks": 5, "seed": 42},
    )
    assert wrong_auth.status_code == 401

    ok_exp = client.post(
        "/api/experiments/run",
        headers={"Authorization": "Bearer secret-review-token-123"},
        json={
            "failure_rates": [0.0, 20.0],
            "num_tasks": 5,
            "seed": 42,
            "run_statistical_validation": True,
        },
    )
    assert ok_exp.status_code == 200
    exp_body = ok_exp.json()
    assert exp_body["status"] == "completed"
    assert exp_body["summary"]["task_count"] == 5
    assert (isolated_results / exp_body["run_id"] / "comparison_summary.json").is_file()

    # 3. Rate limiting on /api/run
    monkeypatch.setenv("TRACERECOVER_RATE_LIMIT_RUN", "2")
    web_dashboard.reset_rate_limiter()
    assert client.post("/api/run", json={"question": "What is 2 + 2?"}).status_code == 200
    assert client.post("/api/run", json={"question": "What is 3 + 3?"}).status_code == 200
    limited = client.post("/api/run", json={"question": "What is 4 + 4?"})
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers

    # 4. Request size cap (HTTP 413)
    monkeypatch.setenv("TRACERECOVER_MAX_REQUEST_BYTES", "1024")
    oversized = client.post("/api/run", json={"question": "x" * 1500})
    assert oversized.status_code == 413

    # 5. Sanitized HTTP 500 (never leaks tracebacks or file paths)
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("secret-internal-failure C:\\private\\path")

    monkeypatch.setattr(web_dashboard, "_latest_results_dir", _boom)
    no_leak_client = TestClient(web_dashboard.app, raise_server_exceptions=False)
    err_resp = no_leak_client.get("/api/benchmark")
    assert err_resp.status_code == 500
    assert "secret-internal-failure" not in err_resp.text
    assert "Traceback" not in err_resp.text


def test_replay_datasets_pdf_and_budget_explanation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify Replay, Datasets CRUD/validation, PDF export, and recovery budget settings."""
    canonical_tasks = Path(__file__).resolve().parents[1] / "eval" / "tasks.json"
    canonical_before = canonical_tasks.read_bytes()

    isolated_logs = tmp_path / "logs"
    isolated_versions = tmp_path / "versions"
    isolated_logs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(web_dashboard, "LOGS_DIR", isolated_logs)
    monkeypatch.setattr(web_dashboard, "DATASET_VERSIONS_DIR", isolated_versions)
    monkeypatch.delenv("TRACERECOVER_ADMIN_TOKEN", raising=False)
    web_dashboard.reset_rate_limiter()

    # 1. Create a real live trace in isolated_logs (never dirties repo logs/)
    run_resp = client.post(
        "/api/run",
        json={
            "question": "Calculate (18 * 4) + 10",
            "provider": "local",
            "failure_rate": 40.0,
            "seed": 42,
            "use_nli": False,
        },
    )
    assert run_resp.status_code == 200
    live_trace_id = run_resp.json()["trace_id"]

    # 2. Replay sources & step-by-step live/eval replay (read-only, no re-execution)
    sources = client.get("/api/replay/sources")
    assert sources.status_code == 200
    s_body = sources.json()
    assert s_body["live_count"] >= 1
    assert any(t["trace_id"] == live_trace_id for t in s_body["live_traces"])

    live_replay = client.get("/api/replay/trace", params={"source": "live", "trace_id": live_trace_id})
    assert live_replay.status_code == 200
    lr_body = live_replay.json()
    assert lr_body["source"] == "live"
    assert lr_body["frame_count"] >= 5
    stages = [f["stage"] for f in lr_body["frames"]]
    assert "planning" in stages
    assert "evidence_verification" in stages
    assert "final_decision" in stages

    if s_body["eval_count"] > 0:
        first_eval = s_body["eval_traces"][0]
        eval_replay = client.get(
            "/api/replay/trace",
            params={
                "source": "eval",
                "run_id": first_eval["run_id"],
                "rate": first_eval["rate"],
                "task_id": first_eval["task_id"],
            },
        )
        assert eval_replay.status_code == 200
        assert eval_replay.json()["frame_count"] >= 5

    # Traversal / malformed replay IDs blocked
    bad_replay = client.get("/api/replay/trace", params={"source": "live", "trace_id": "../secret"})
    assert bad_replay.status_code == 400

    # 3. Datasets catalog, paginated inspection, validation & versioned import
    ds_list = client.get("/api/datasets")
    assert ds_list.status_code == 200
    ds_ids = [d["id"] for d in ds_list.json()["datasets"]]
    assert "eval_tasks" in ds_ids
    assert "execution_telemetry" in ds_ids

    ds_tasks = client.get(
        "/api/datasets/eval_tasks/tasks",
        params={"abstain": "true", "limit": 10, "offset": 0},
    )
    assert ds_tasks.status_code == 200
    assert ds_tasks.json()["total_tasks"] == 100
    assert all(t["should_abstain"] is True for t in ds_tasks.json()["tasks"])

    sample_tasks = [
        {
            "task_id": "CUSTOM-01",
            "query": "Compute (12 * 5) + 3",
            "category": "multi_hop_search_math",
            "evaluation_criteria": "Contains 63",
            "final_answer_template": "The answer is {0}.",
            "expected_answer": "63",
            "expected_facts": ["63"],
            "contradictory_facts": ["999"],
            "should_abstain": False,
            "oracle_plan": [{"tool": "calculator", "arguments": {"expression": "12 * 5 + 3"}}],
            "expected_tool_category": ["calculator"],
        }
    ]
    val_ok = client.post(
        "/api/datasets/validate",
        json={"name": "Custom Unit Set", "dataset_version": "2.1-test", "tasks": sample_tasks},
    )
    assert val_ok.status_code == 200
    assert val_ok.json()["valid"] is True
    assert val_ok.json()["task_count"] == 1

    val_bad = client.post(
        "/api/datasets/validate",
        json={"name": "Invalid Set", "dataset_version": "1.0", "tasks": [{"task_id": "BAD"}]},
    )
    assert val_bad.status_code == 200
    assert val_bad.json()["valid"] is False
    assert len(val_bad.json()["errors"]) >= 1

    # Admin token guards dataset import when configured
    monkeypatch.setenv("TRACERECOVER_ADMIN_TOKEN", "ds-secret-token")
    unauth_imp = client.post(
        "/api/datasets/import",
        json={"name": "Custom Unit Set", "dataset_version": "2.1-test", "tasks": sample_tasks},
    )
    assert unauth_imp.status_code == 401

    auth_imp = client.post(
        "/api/datasets/import",
        headers={"Authorization": "Bearer ds-secret-token"},
        json={"name": "Custom Unit Set", "dataset_version": "2.1-test", "tasks": sample_tasks},
    )
    assert auth_imp.status_code == 200
    imported_id = auth_imp.json()["dataset"]["id"]

    # Imported version shows up in catalog & version history while canonical eval/tasks.json is unchanged
    after_ds = client.get("/api/datasets").json()
    assert any(d["id"] == imported_id for d in after_ds["datasets"])
    assert len(after_ds["version_history"]) >= 1
    assert canonical_tasks.read_bytes() == canonical_before

    # 4. PDF Export returns valid %PDF- bytes
    pdf_resp = client.get("/api/export/pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"].startswith("application/pdf")
    assert pdf_resp.content[:5] == b"%PDF-"
    assert len(pdf_resp.content) > 3000

    # 5. Settings clarifies recovery two-tier budget & includes zero exposed API keys
    settings_resp = client.get("/api/settings")
    assert settings_resp.status_code == 200
    s_data = settings_resp.json()
    assert s_data["recovery"]["max_retries"] == 1
    assert s_data["recovery"]["max_total_attempts"] == 4
    assert "RETRY" in s_data["recovery"]["budget_explanation"]
    assert all(
        "api_key" not in {str(k).casefold() for k in p.keys()}
        and "secret" not in {str(k).casefold() for k in p.keys()}
        for p in s_data["providers"]
    )
    assert "AIza" not in str(s_data) and "sk-" not in str(s_data)

