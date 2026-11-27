# TraceRecover — Phase 7.5 Final Report
## Genuine Live LLM Activation & End-to-End Intelligence Validation

**Date:** 2026-11-25  
**Validation Artifact:** `results/phase7_5-validation-20261125T043318Z/phase7_5_validation_report.json`  
**Final Classification:** **`3. BLOCKED — no genuine provider execution possible`** *(Phase 8 NOT started)*

---

## 1. Executive Summary & Honest Classification

Phase 7.5 audited genuine live LLM provider activation, executed a 10-scenario / 4-regime evaluation across all non-blocked modes, investigated the Phase 7.4 live connector-only weakness (`36.36%` overall, `41.67%` answered, `98.61%` evidence support), verified security/quality controls, and ran the complete repository test suite.

### Why Final Classification Is `3. BLOCKED`
1. **Zero live LLM credentials or local model runtimes are configured on this host:**
   - `GEMINI_API_KEY`: unset (`len=0`) in process environment and `.env`
   - `OPENAI_API_KEY`: unset (`len=0`) in process environment and `.env`
   - `ANTHROPIC_API_KEY`: unset (`len=0`) in process environment and `.env`
   - `OLLAMA_BASE_URL`: unset (`len=0`); `127.0.0.1:11434` closed; `ollama` CLI absent
   - `LOCAL_LLM_BASE_URL`: unset (`len=0`); `127.0.0.1:1234` closed
2. Every live provider's `health_check()` honestly returned `connection_status = "unconfigured"` and `error_category = "missing_credentials"` without silent fallback.
3. No mock completion was substituted for Regime D (`10/10` scenarios recorded as **`BLOCKED`**).

---

## 2. Historical Phase 7.4 Preservation Notice

Both Phase 7.4 holdout artifacts remain preserved untouched on disk alongside the unmodified frozen holdout (`data/phase7_4_independent_holdout.json`, SHA-256 `a5d796b3c5ca73fa2d9d0572f296c78df9a713859f38b83f642fd7a30a38b1ef`) and protected gold labels (`data/phase7_4_holdout_gold.json`, SHA-256 `aade3b5dfec8cad13fda83b224efd05849811429105019980aa924810ded1106`):
- **Initial blind Phase 7.4 run:** `results/phase7_4-validation-20261125T031926Z/phase7_4_validation_report.json`
- **Post-fix Phase 7.4 run (same frozen dataset — NOT an untouched blind evaluation):** `results/phase7_4-validation-20261125T040247Z/phase7_4_validation_report.json`

---

## 3. Phase 7.5 Evaluation Results Across All Four Regimes (Tasks C & D)

| Metric | Regime A: OFFLINE (`n=10`) | Regime B: MOCK (`n=10`) | Regime C: LIVE CONNECTOR (`n=7` non-synthetic) | Regime D: LIVE LLM + LIVE CONNECTOR (`n=10`) |
|---|---:|---:|---:|---:|
| Answered / Abstained / Blocked | `2 / 8 / 0` | `8 / 2 / 0` | `6 / 1 / 0` | `0 / 0 / 10 (BLOCKED)` |
| Overall Accuracy | `4/10 (40.0%)` | **`10/10 (100.0%)`** | **`7/7 (100.0%)`** | `N/A (BLOCKED)` |
| Answered Accuracy | `2/2 (100.0%)` | **`8/8 (100.0%)`** | **`6/6 (100.0%)`** | `N/A (BLOCKED)` |
| Evidence Support Rate | `100.0% (n=2)` | **`100.0% (n=8)`** | **`97.22% (n=6)`** | `N/A (BLOCKED)` |
| Citation Precision | `100.0% (n=2)` | **`100.0% (n=8)`** | **`100.0% (n=6)`** | `N/A (BLOCKED)` |
| Appropriate Abstention Recall | `2/2 (100.0%)` | **`2/2 (100.0%)`** | **`1/1 (100.0%)`** | `N/A (BLOCKED)` |
| Mean Latency (ms) | `11.67 ms` | `23.93 ms` | `1,950.45 ms` | `N/A (BLOCKED)` |
| Provider-Reported Tokens | `0 (none)` | `0 (none)` | `0 (none)` | `0 (BLOCKED)` |

- Regime C executed real live external connectors (`arXiv`, `Crossref`, `Wikipedia`, `DuckDuckGo`) on all 7 non-synthetic scenarios (`P75-01`, `P75-02` with 8 real arXiv/Crossref FlashAttention papers, `P75-03`, `P75-04`, `P75-05`, `P75-06`, and `P75-09` cleanly abstaining on future 2033 Nobel Prize).
- Synthetic scenarios (`P75-07` contradiction, `P75-08` multi-study experimental contexts `85.4%` vs `91.2%`, `P75-10` prompt-injected document) were verified cleanly in Regime B without conflating synthetic fixtures with live web traffic.

---

## 4. Phase 7.4 Live Connector-Only Weakness Audit (Task E)

All 14 failed/warned questions from the historical Phase 7.4 Regime C run (`8/22 = 36.36%` overall, `5/12 = 41.67%` answered, `98.61%` evidence support) were classified:
- **`query_interpretation_failure` (4):** `H74-01`, `H74-09`, `H74-13`, `H74-25` (14–15 word raw questions yielded 0 Wikipedia/DDG hits).
- **`missing_source` (4):** `H74-31`, `H74-38`, `H74-47` (academic papers not routed to arXiv/Crossref); `H74-55` (static local KB preemption).
- **`retrieval_coverage_failure` (4):** `H74-05`, `H74-17`, `H74-51`, `H74-56` (exact right Wikipedia page retrieved, but short lead snippet omitted deep-section spec/number).
- **`irrelevant_evidence` (2):** `H74-03` (NetBSD/Windows for Linux 2.6), `H74-91` (`John M. Martinis`/`1976` for future year `2033`).

### Generalizable Fixes Implemented & Honest Trade-Off Observed
Added `_wrap_external_search_with_hygiene()` in `agent/open_ended_runner.py` (HTML entity unescaping, off-topic noise pruning, future-year abstention guard, and compact-query fallback) with 5 new development regression tests in `tests/test_phase7_5_live_readiness_and_hygiene.py`.
- Post-fix diagnostic on the same 22 live-probe questions (NOT an untouched blind holdout):
  - **`H74-91` (`Nobel Prize 2033`) fixed → `ABSTAIN`.**
  - **`H74-13`, `H74-31`, `H74-38`, `H74-47` gained live retrieval (`1–3` sources).**
  - **Honest empirical finding:** Overall accuracy remained `8/22 (36.36%)` because short Wikipedia lead snippets still lack deep-section figures and compacting `H74-81` (`HELIOS-88`) triggered generic Wikipedia matches (`WARN`). This empirically confirms that deterministic keyword heuristics cannot replace genuine live LLM query planning, deep-page reading, and semantic relevance filtering.

---

## 5. Quality, Security & Full Regression Verification (Tasks F & G)

- **Task F Security & Quality Checks:** **`4/4` passed** (`no_silent_provider_substitution`, `token_call_and_cost_budget_enforced`, `secret_redaction`, `bounded_retries_and_timeout_classification`) plus prompt-injection fencing (`P75-10` + unit test).
- **Complete Repository Regression Suite (`pytest -v`):** **`207 passed, 0 failed, 0 skipped`** in `197.90s` (`0:03:17`), including all 7 Playwright Chromium E2E suites, Phase 7.1, 7.2, 7.3, 7.3.1, 7.4, and 7.5 tests, secret-redaction tests, prompt-injection tests, and SSRF/connector security tests.
- **Protected Core Modules & Historical Benchmarks:** Untouched.

---

## 6. Companion Documentation Created (Task H)
- `docs/PHASE7_5_LIVE_INTELLIGENCE.md`
- `docs/PHASE7_5_LIVE_EVALUATION.md`
- `docs/PHASE7_5_DEMO_GUIDE.md`
- `PHASE7_5_FINAL_REPORT.md`
