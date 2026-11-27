# TraceRecover — Phase 8.2 Reliability, Concurrency & Regression Testing Report

**Date:** 2026-11-25  
**Full Repository Command:** `.\venv\Scripts\python.exe -m pytest -v`  
**Result:** **225 passed, 0 failed, 0 skipped** (`118.58s`)

---

## 1. Concurrency, Cancellation & Storage Reliability Verification

Verified across `tests/test_phase8_1_hardening_and_stability.py`, `tests/test_phase8_2_performance_observability_and_concurrency.py`, and `eval/phase8_2_performance.py`:

| Scenario | Test Mechanism | Measured Outcome |
|---|---|---|
| **Multi-session concurrent queries** | 6 threads × 3 OFFLINE queries via `TestClient` | `18/18` succeeded, `0` failures, `session_isolation_verified: true`, peak `2.656 MB` |
| **Concurrent appends to same conversation** | 8 threads calling `ConversationStore.append_turn()` | All 8 turns preserved intact, `0` JSON corruption, `0` `.tmp` files left behind |
| **Delete during concurrent queries** | Concurrent `DELETE /api/conversations/{id}` alongside 4 active query threads | `200` on delete and all 4 queries; zero cross-session interference |
| **Cooperative cancellation** | Pre-set `threading.Event` passed to `OpenEndedAgentRunner.run()` | Clean `QueryCancelledError` raised, `query.cancelled` structured event emitted, unrelated state intact |
| **External modification & retention cache coherence** | `ConversationStore` with `max_conversations_per_session=5`, external file edit, deletion | Retention enforced (`5` kept), external title update immediately invalidated mtime cache, deletion evicted cache cleanly |
| **Corrupted conversation JSON resilience** | Malformed JSON written into session directory | `GET`/`clear` return `404` safely (never `500`), owner `DELETE` cleans up file cleanly |
| **Resource & budget controls** | Excessive `max_steps=99`, `max_llm_calls=99`, `max_cost_usd=50.0`, 5,000-char prompt, oversized body | Rejected with `422` / `413`; rate limiting returns `429` + `Retry-After` |

---

## 2. Complete Regression Suite Breakdown (225/225 Passed)

| Test Group | Files | Passed | Failed |
|---|---|---:|---:|
| **Core Offline Engine (Phases 1–2, 8)** | `test_abstention` (10), `test_calibrator` (3), `test_claim_extractor` (3), `test_detector` (11), `test_evaluation` (7), `test_evidence_checker` (13), `test_executor` (4), `test_failure_injector` (14), `test_localizer` (6), `test_metrics` (9), `test_nli_verifier` (5), `test_phase1` (4), `test_phase8_evaluation_tools` (4), `test_recovery` (21), `test_uncertainty` (8) | 122 | 0 |
| **Phase 3–4 Open-Ended & Web API** | `test_web_dashboard` (10), `test_phase3_open_ended` (6), `test_phase4_acceptance` (1) | 17 | 0 |
| **Phase 5–6 Connectors, MCP & Security** | `test_phase5_connectors` (7), `test_phase6_validation_and_security` (6) | 13 | 0 |
| **Phase 7–7.5.2 Adaptive, Provenance, Holdout & Integrity** | `test_llm_client` (4), `test_phase7_adaptive_agent` (6), `test_phase7_1_refinement` (11), `test_phase7_2_live_llm_and_research` (5), `test_phase7_3_1_provenance_and_contradiction` (13), `test_phase7_3_activation_and_audit` (2), `test_phase7_4_holdout_and_e2e` (1 non-E2E), `test_phase7_5_live_readiness_and_hygiene` (5), `test_phase7_5_2_synthesis_and_execution_integrity` (6 non-E2E) | 53 | 0 |
| **Phase 8.1 Hardening & Stability** | `test_phase8_1_hardening_and_stability` (6) | 6 | 0 |
| **Phase 8.2 Performance, Observability & Concurrency** | `test_phase8_2_performance_observability_and_concurrency` (5) | 5 | 0 |
| **Chromium Dashboard Playwright E2E** | `test_browser_e2e` (1), `test_phase3_browser_e2e` (1), `test_phase5_connect_hub_e2e` (1), `test_phase6_dashboard_e2e` (1), `test_phase7_1_dashboard_e2e` (1), `test_phase7_2_dashboard_e2e` (1), `test_phase7_workspace_e2e` (1), `test_phase7_4_holdout_and_e2e` (1 E2E), `test_phase7_5_2_synthesis_and_execution_integrity` (1 E2E) | 9 | 0 |
| **Total** | **38 test modules** | **225** | **0** |

---

## 3. Single-Node Operational Limitations

- `ConversationStore` uses per-process thread locks and local atomic JSON files; it is verified for single-process concurrency only and is **not** multi-replica or multi-worker safe.
- `_SlidingWindowRateLimiter` and mtime-validated artifact caches live in single-process memory; multi-worker deployments should place rate limiting at a reverse proxy and use shared storage.
