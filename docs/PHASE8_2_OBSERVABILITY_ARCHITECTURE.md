# TraceRecover — Phase 8.2 Observability & Operational Monitoring Architecture

## 1. Scope & Honest Boundaries

TraceRecover implements **local structured JSON observability**, **per-stage execution timing**, **JSONL trace persistence**, and **operational subsystem health reporting** with zero external infrastructure dependencies.

> **Important:** TraceRecover does **not** include an OpenTelemetry/Jaeger distributed tracing collector; trace correlation connects backend structured logs (`tracerecover.observability`), JSONL files in `logs/`, and Query Workspace UI cards via `trace_id` and `query_id`.

---

## 2. Structured Execution Logging (`tracerecover.observability`)

Implemented in `agent/open_ended_runner.py` and `web_dashboard.py` using Python's standard `logging` module with consistent JSON event payloads:

| Event Name | Severity | Key Fields |
|---|---|---|
| `query.started` | `INFO` | `event`, `timestamp`, `query_id`, `session_ref`, `requested_provider`, `enable_external_search`, `use_connectors` |
| `query.completed` | `INFO` | `event`, `timestamp`, `trace_id`, `query_id`, `session_ref`, `execution_mode`, `execution_mode_label`, `final_decision`, `synthesis_status`, `total_duration_ms`, `tool_calls`, `recovery_successes` |
| `query.failed` | `WARNING` | Same as `query.completed`, plus `failure_category` and `synthesis_status: "failed"` |
| `query.cancelled` | `WARNING` | `event`, `timestamp`, `query_id`, `session_ref`, `elapsed_ms` |
| `http.request_error` | `ERROR` | `event`, `timestamp`, `method`, `path`, `error_type` (exception class name only — no stack trace or payload secrets) |

### Privacy & Redaction Rules
- Every string field passes through `redact_secret_text()` before emission (`AIza`, `sk-`, `Bearer`, `x-goog-api-key`, `x-api-key`, `authorization`, `api_key=`).
- Raw `X-Session-ID` values are never logged; instead `_mask_session_id()` hashes them into `sess_<10-char-sha256>`.
- No raw environment variables or user Authorization headers are emitted.

---

## 3. Per-Stage Execution Telemetry (`observability` Response Block)

Every response from `OpenEndedAgentRunner.run()` (and `/api/chat/query` / `/api/chat/stream`) includes:

```json
{
  "observability": {
    "trace_id": "trace-20261125T...",
    "query_id": "qry-...",
    "conversation_id": "conv-...",
    "session_ref": "sess_a1b2c3d4e5",
    "execution_mode": "offline_deterministic",
    "execution_mode_label": "OFFLINE",
    "started_at": "2026-11-25T08:30:00.000000+00:00",
    "completed_at": "2026-11-25T08:30:00.017000+00:00",
    "total_duration_ms": 17.0,
    "stage_timings_ms": {
      "intent_and_planning_ms": 1.2,
      "tool_and_connector_ms": 0.4,
      "failure_detection_and_recovery_ms": 2.1,
      "synthesis_ms": 0.1,
      "evidence_verification_ms": 3.5,
      "claim_verification_ms": 2.8,
      "uncertainty_ms": 0.7
    },
    "tool_call_summary": {
      "total_calls": 1,
      "succeeded": 1,
      "failed": 0,
      "recovered": 0,
      "tools": ["calculator"]
    },
    "retry_count": 0,
    "recovery_outcome": {"attempted": false, "successful": 0, "failed": 0, "strategies_used": []},
    "final_decision": "ANSWER",
    "abstention_category": "grounded_confident",
    "abstention_reason": "...",
    "provider_status": "offline_local",
    "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "usage_source": "none"}
  }
}
```

The Query Workspace renders these stage durations directly inside `data-testid="ws-stage-timings"` inside `data-testid="ws-execution-integrity"`.

---

## 4. Operational Health Contract (`GET /api/health`)

`GET /api/health` succeeds in OFFLINE mode with zero external API keys and exposes `subsystems` explicitly distinguishing **installed**, **configured**, **reachable**, **authenticated**, and **operational**:

- **`subsystems.storage`:** Reports `operational`, `conversations_persistent: true`, `conversations_backend: "filesystem_json_atomic"`, and `single_node_limitation: true`.
- **`subsystems.execution_modes`:** Separates `offline` (`operational: true`), `mock` (`operational: true`), `live_connectors` (`operational: true`), and `live_llm` (`configured: false, authenticated: false, operational: false` when credentials are absent).
- **`subsystems.connectors`:** Counts `registered_count`, `enabled_count`, `connected_count`, `auth_required_count`, and `operational_count` (strictly requiring `enabled=True` AND `status in {"connected", "available"}`).
- **`subsystems.browser_automation`:** Reports `installed`, `available_engines` (engine names only — zero host filesystem paths), and `operational`.
- **`subsystems.mcp_runtime`:** Reports `installed` (`mcp` SDK importable), `configured_servers`, `connected_servers`, `reachable_servers`, and `operational` (`connected_servers > 0`).
