# TraceRecover — Execution Integrity & Synthesis Failure-Handling Specification (Phase 7.5.2)

## 1. Purpose

This document specifies the runtime contract governing LLM provider invocation, failure propagation, post-retrieval evidence preservation, execution-mode labeling, and Query Workspace UI reporting in TraceRecover.

Its core invariant is **Execution Honesty**:

> TraceRecover must **never** silently substitute deterministic synthesis for a failed live LLM call, and must **never** label a failed or mock LLM run as a successful `LIVE LLM` response.

---

## 2. Synthesis Failure-Propagation Contract (`agent/synthesizer.py`)

`EvidenceGroundedSynthesizer` exposes two entry points:

- `synthesize_with_metadata(question, observations, *, provider_adapter=None, execution_mode=None, allow_deterministic_fallback=False) -> dict[str, Any]`
- `synthesize(question, observations, *, provider_adapter=None, allow_deterministic_fallback=False) -> str`

### Policy Rules

1. **Offline / Deterministic Mode (`provider_adapter is None` or `provider == "local"`):**
   - Synthesizes directly from committed tool observations.
   - Returns `synthesis_status="succeeded"`, `answer_origin="deterministic_synthesizer"`, `fallback_used=False`, `llm_produced_final_answer=False`.

2. **Remote / Custom LLM Mode (`provider != "local"`):**
   - Wraps retrieved snippets inside `<UNTRUSTED_EVIDENCE_DATA>...</UNTRUSTED_EVIDENCE_DATA>` to mitigate indirect prompt injection.
   - Invokes `complete_with_budget()` (or `complete_with_metadata()`).
   - **Empty responses** and **any provider exception** are classified via `classify_provider_exception()` and scrubbed via `redact_secret_text()`.
   - Because `allow_deterministic_fallback` defaults to `False`, failures **raise `SynthesisFailureError(ProviderAdapterError)`** with `.category`, `.provider`, and `.stage = "synthesis"`.
   - Even if an explicit diagnostic caller passes `allow_deterministic_fallback=True`, the response explicitly records `synthesis_status="fallback_deterministic"`, `answer_origin="deterministic_synthesizer"`, `fallback_used=True`, `fallback_reason`, `failure_category`, `failure_reason`, and `llm_produced_final_answer=False`.

---

## 3. Post-Retrieval Evidence Preservation (`agent/open_ended_runner.py`)

`OpenEndedAgentRunner.run()` separates **pre-retrieval** provider failures from **post-retrieval** synthesis failures:

1. **Pre-retrieval failure (missing/invalid credentials, or Step 1 planning failure with zero committed observations):**
   - Raises `ProviderUnavailableError` immediately (HTTP 503 via `/api/chat/query` and SSE `error` event via `/api/chat/stream`).
   - Refuses silent fallback to `local` mode.

2. **Post-retrieval failure (Step 2+ LLM failure after valid tool/connector observations exist, OR post-loop `EvidenceGroundedSynthesizer` failure):**
   - Logs a failed `llm` step to `TraceLogger` with redacted error metadata and emits a `"synthesis"` stage event (`status="failed"`).
   - **Preserves** all committed `observations`, `external_sources`, `retrieved_evidence` (`claim_corpus`), `structured_answer.citations`, `structured_answer.supporting_evidence`, and `trace_steps`.
   - Skips `create_llm_entailment_evaluator` to avoid unnecessary secondary LLM calls after provider failure.
   - Sets:
     - `final_decision = "ABSTAIN"`
     - `abstention_category = "provider_synthesis_failure"`
     - `knowledge_source = "synthesis_failed_evidence_preserved"` (when evidence exists)
     - `synthesis_status = "failed"`
     - `failed_stage = "synthesis"`
     - `answer_origin = "none_failed"`
     - `fallback_used = False`
     - `llm_produced_final_answer = False`
   - Never fabricates an answer or substitutes deterministic prose as if the LLM succeeded.

---

## 4. `execution_integrity` Response Schema

Every response returned by `OpenEndedAgentRunner.run()` (and streamed by `/api/chat/query` and `/api/chat/stream`) includes a top-level `execution_integrity` dictionary (plus mirrored top-level and `provider_info` fields):

```json
{
  "requested_provider": "gemini",
  "actual_provider": "gemini",
  "actual_model": "gemini-2.0-flash",
  "execution_mode": "live_llm",
  "execution_mode_label": "LIVE CONNECTOR (LIVE LLM SYNTHESIS FAILED)",
  "planning_status": "succeeded",
  "connector_status": "succeeded",
  "synthesis_status": "failed",
  "answer_origin": "none_failed",
  "llm_produced_final_answer": false,
  "fallback_used": false,
  "fallback_reason": null,
  "failed_stage": "synthesis",
  "failure_category": "rate_limited",
  "failure_reason": "Provider 'gemini' failed during synthesis (rate_limited): ...",
  "preserved_external_sources_count": 2,
  "preserved_evidence_count": 2,
  "live_llm_succeeded": false
}
```

### Valid `answer_origin` Values

- `real_llm` — Genuine configured remote/local-server LLM (`client is None`) succeeded and produced the final answer.
- `mock_provider` — Injected test/mock LLM client produced the final answer (never labeled `LIVE LLM`).
- `deterministic_synthesizer` — Offline local planner or deterministic evidence synthesizer produced the answer.
- `none_failed` — Configured LLM failed during synthesis; no answer was fabricated.
- `abstained` — Run abstained before synthesis (e.g., safety/prompt-injection guard or zero evidence).

---

## 5. Failure Categories & Secret Redaction (`agent/provider_adapter.py`)

Canonical failure categories produced by `classify_provider_exception()`:

- `missing_credentials`
- `invalid_api_key`
- `invalid_configuration`
- `auth_failed`
- `timeout`
- `rate_limited`
- `unreachable`
- `empty_response`
- `malformed_output`
- `budget_exceeded`

All error messages pass through `redact_secret_text()`, which replaces `sk-...`, `AIza...`, `Bearer ...`, `x-goog-api-key`, `x-api-key`, `authorization`, and `api_key=` / `access_token=` values with `[REDACTED_KEY]`.

---

## 6. Query Workspace UI Integrity (`static/index.html`)

`renderWorkspaceAnswer(data)` enforces three visual integrity guarantees:

1. **Execution Mode Badge (`data-testid="ws-exec-mode-badge"`):** Renders `pill-red` whenever `synthesis_status === "failed"` or `execModeLabel` contains `"FAILED"`. A failed live run can never render a green `pill-green` `LIVE LLM` badge.
2. **Synthesis Failure Banner (`data-testid="ws-synthesis-failure-banner"`):** Displayed on synthesis failure to separate `Connector Retrieval: succeeded (N external source(s))` from `Answer Synthesis Failed (<category>)`, while keeping preserved external source links visible and clickable below.
3. **Execution Integrity Strip (`data-testid="ws-execution-integrity"`):** Displays requested provider, actual provider/model, planning status, connector status, synthesis status, answer origin, fallback status, and redacted failure reason.
