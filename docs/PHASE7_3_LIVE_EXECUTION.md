# TraceRecover Phase 7.3 Live Execution & Integration Audit

## Provider Audit Status (`2026-11-25`)

- **Real LLM API Calls (`BLOCKED`)**: Checked process environment, Windows User/Machine environment, `.env`, and local sockets (`127.0.0.1:11434`, `127.0.0.1:1234`). Zero non-empty credentials exist for `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`, or `LOCAL_LLM_BASE_URL`.
- **Health Check Responses (`OFFLINE` / `BLOCKED`)**:
  - `local`: `connection_status="offline_local"`, `usage_source="none"`, `cost_estimation_status="zero_local"`.
  - `gemini`, `openai`, `anthropic`, `ollama`, `local_openai`: `connection_status="unconfigured"`, `error_category="missing_credentials"`, zero silent fallback.
- **Pricing Provenance (`agent/provider_adapter.py`)**:
  - Source: `config.MODEL_PRICING_PER_1M (published vendor rate cards)`
  - Retrieved Date: `2026-11-24`
  - Unlisted models explicitly report `pricing_known=False`, `cost_estimation_status="unknown_pricing"`, and `estimated_cost_usd=None` on health checks so exact API costs are never fabricated.

## Task 3 — Six Research Scenarios Executed (`results/phase7_3-validation-20261125T022057Z`)

| Scenario | Label | Decision / Status | Live Sources / URLs | Verification Outcome |
|---|---|---|---|---|
| A. Technical documentation (Python `dataclasses` `frozen=True`) | `LIVE CONNECTOR` | `ok=True` (Chromium) | `https://docs.python.org/3/library/dataclasses.html` | `frozen` confirmed in live extracted excerpt |
| B. Academic research (conformal prediction calibration) | `LIVE CONNECTOR` | `WARN` | 6 live scholarly URLs (arXiv / Crossref / OpenAlex) | 11 `SUPPORTED`, 2 `UNSUPPORTED` claims |
| C. Multi-step comparative research (Raft vs Paxos) | `LIVE CONNECTOR` | `WARN` | 3 live reference URLs | 6 / 6 `SUPPORTED` claims |
| D. Contradictory evidence (TLS 1.3 RFC 8446 2018 vs 2023) | `MOCK` | `ABSTAIN` | 2 conflicting URLs | `CONTRADICTED` flagged (`contradictory_evidence`) |
| E. Insufficient evidence (`KESTREL-9X` future score) | `LIVE CONNECTOR` | `ABSTAIN` | 0 supporting URLs | Honest `insufficient_evidence` abstention |
| F. Numerical / temporal reasoning (`T-401` + `1200 * 0.85`) | `OFFLINE` | `ANSWER` | Local tool steps (`mock_api`, `calculator`) | 2 / 2 `SUPPORTED` claims (`1020.0`) |

## Task 4 — LLM + Live Connector Integration Proof (`MOCK_LLM + LIVE_CONNECTOR`)

Executed with a controlled stubbed `gemini-2.0-flash` client wired into **REAL live `research_sources` HTTP connectors** (`trace_id`: `trace-20261125T022121Z-2f29d39e`):
- `planner_called_model`: `True`
- `connectors_executed`: `['research_sources']` (`14` live external sources, `28` corpus evidence items)
- `prompt_fenced_with_untrusted_tags`: `True` (`<UNTRUSTED_EVIDENCE_DATA>`)
- `second_prompt_received_live_evidence`: `True`
- `retrieved_evidence_reached_synthesis`: `True`
- `verifier_evaluated_post_synthesis`: `True`
- `citations_traceable_to_corpus`: `True`
- **Post-Synthesis Hallucination Gate**: `CL-1` (`"Conformal prediction provides uncertainty calibration for machine learning models."`) -> `SUPPORTED`; injected hallucination `CL-2` (`"Mars moon Phobos hosts subterranean liquid oceans."`) -> `UNSUPPORTED` -> triggered safe `ABSTAIN`.
