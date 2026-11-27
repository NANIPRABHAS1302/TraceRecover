# TraceRecover Phase 7.2 Live LLM Integration Architecture

## End-to-End Pipeline

```text
USER QUERY
  -> sanitize_and_guard_query() & Intent/Ambiguity Analysis
  -> MultiStepResearchPlanner + IntelligentSourceSelector
  -> Live Connector / Browser / Local Tool Execution (ToolExecutor + FailureInjector)
  -> Evidence Collection -> Untrusted Delimiter Fencing (<UNTRUSTED_EVIDENCE_DATA>)
  -> Live LLM Planning / Synthesis (LLMProviderAdapter budget & retry controls)
  -> ClaimEvidenceVerifier (claim_id CL-1..N + optional LLM entailment + deterministic numeric/date guards)
  -> Contradiction Detection (context-aware subject/property vs metadata filtering)
  -> Uncertainty Classification (8 taxonomy categories + abstention_reason)
  -> Dashboard Execution Mode Badge & Telemetry Panels
```

## Key Extensions in Phase 7.2

1. **Provider Abstraction & Health Diagnostics (`agent/provider_adapter.py`, `web_dashboard.py`)**
   - `select_preferred_live_provider()`: Selects `gemini` first when credentials are configured, then `openai`, `anthropic`, `ollama`, `local_openai`, falling back to `local` offline deterministic mode only by default when no explicit live provider was requested (never silently substituting a failed requested provider).
   - `LLMProviderAdapter.health_check()`: Performs a bounded probe recording `provider`, `model`, `timestamp`, `connection_status`, `latency_seconds`, `error_category`, redacted errors, `token_usage`, and `usage_source` (`provider_reported` vs `estimated` vs `none`).
   - Telemetry counters: tracks `retry_count`, `failed_requests`, `max_output_tokens`, `max_llm_calls`, `max_cost_usd`, and empty-response detection (`empty_response`).
   - API endpoints: `GET /api/providers/status` and `POST /api/providers/health`.

2. **Prompt Injection Fencing & Malformed Output Resilience (`agent/dynamic_planner.py`, `agent/synthesizer.py`)**
   - Remote planner prompts and live synthesis prompts isolate retrieved snippets inside `<UNTRUSTED_EVIDENCE_DATA>...</UNTRUSTED_EVIDENCE_DATA>` tags and prohibit URL/citation/number fabrication.
   - `DynamicQueryPlanner.next_action()` handles empty model replies (`empty_response`) and malformed JSON replies via a single budget-checked repair retry before raising `ProviderAdapterError("malformed_output")` with zero silent fallback.

3. **Live Claim Verification (`connectors/sources/verifier.py`, `agent/open_ended_runner.py`)**
   - Every claim receives a deterministic `claim_id` (`CL-1`, `CL-2`, ...).
   - `create_llm_entailment_evaluator(provider_adapter)` allows bounded live entailment confirmation when a live provider is active while keeping deterministic numeric/date/version checks, bibliographic metadata stripping, duplicate deduplication, and `UNCERTAIN` preservation authoritative.

4. **Dashboard Observability (`static/index.html`)**
   - Adds 5-state `execution_mode_label` badge (`OFFLINE` | `MOCK` | `LIVE CONNECTOR` | `LIVE LLM` | `LIVE CONNECTOR + LIVE LLM`), interactive `"Check Provider Health"` button (`#ws-health-btn`), Claim ID column, and detailed Provider/Research/Verification/Final Answer panels with zero client-side browser storage (`localStorage.length === 0`).
