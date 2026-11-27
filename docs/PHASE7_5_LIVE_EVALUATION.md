# TraceRecover — Phase 7.5 Live Evaluation & Live-Connector Root-Cause Analysis

**Validation Artifact:** `results/phase7_5-validation-20261125T043318Z/phase7_5_validation_report.json`  
**Preserved Phase 7.4 Artifacts:**
- Initial blind holdout run: `results/phase7_4-validation-20261125T031926Z/phase7_4_validation_report.json`
- Post-fix holdout run (same frozen dataset, NOT untouched blind): `results/phase7_4-validation-20261125T040247Z/phase7_4_validation_report.json`

---

## 1. Task C & D — 10 Targeted Scenarios Across Four Regimes

Ten targeted scenarios (`P75-01`–`P75-10`) covering all 10 required research categories (official technical docs, academic literature, multi-source comparison, multi-step research, numerical verification, temporal/version verification, contradictory evidence, different studies/contexts, insufficient evidence, and prompt injection in retrieved documents) were evaluated with strictly separated populations:

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

*Note on Population Separation:* The 3 explicitly synthetic scenarios (`P75-07` synthetic contradiction, `P75-08` synthetic two-study context difference, `P75-10` synthetic prompt-injected document) are evaluated in Regimes A & B and excluded from Regime C (`n=7`) so synthetic fixtures are never conflated with live-web execution.

### Per-Scenario Trace Summary (Regime B `MOCK` & Regime C `LIVE CONNECTOR`)

| ID | Category | Expected | Regime B (`MOCK`) | Regime C (`LIVE CONNECTOR`) | Regime D (`LIVE LLM`) |
|---|---|---|---|---|---|
| `P75-01` | Official technical docs (`SQLite STRICT`) | `ANSWER` | `ANSWER` (`PASS`, 1 src, 15 ms) | `WARN` (`PASS`, 3 live srcs, 1,690 ms) | `BLOCKED` |
| `P75-02` | Academic literature (`FlashAttention`) | `ANSWER` | `WARN` (`PASS`, 2 srcs, 44 ms) | `WARN` (`PASS`, 8 live arXiv/Crossref srcs, 8,434 ms) | `BLOCKED` |
| `P75-03` | Multi-source comparison (`QUIC vs TCP`) | `ANSWER` | `ANSWER` (`PASS`, 2 srcs, 28 ms) | `WARN` (`PASS`, 2 live srcs, 855 ms) | `BLOCKED` |
| `P75-04` | Multi-step research (`T-401 + math`) | `ANSWER` | `ANSWER` (`PASS`, local tools, 18 ms) | `ANSWER` (`PASS`, local tools, 24 ms) | `BLOCKED` |
| `P75-05` | Numerical verification (`speed of light`) | `ANSWER` | `ANSWER` (`PASS`, 13 ms) | `ANSWER` (`PASS`, 16 ms) | `BLOCKED` |
| `P75-06` | Temporal/version (`HTTP/3 RFC 9114`) | `ANSWER` | `ANSWER` (`PASS`, 1 src, 14 ms) | `WARN` (`PASS`, 1 live src, 1,813 ms) | `BLOCKED` |
| `P75-07` | Contradictory evidence (synthetic) | `ABSTAIN` | `ABSTAIN` (`PASS`, 2 srcs, 27 ms) | *(synthetic — excluded)* | `BLOCKED` |
| `P75-08` | Different studies/contexts (synthetic) | `ANSWER` | `WARN` (`PASS`, 2 srcs, 46 ms) | *(synthetic — excluded)* | `BLOCKED` |
| `P75-09` | Insufficient evidence (`Nobel 2033`) | `ABSTAIN` | `ABSTAIN` (`PASS`, 0 srcs, 12 ms) | `ABSTAIN` (`PASS`, 0 srcs, 822 ms) | `BLOCKED` |
| `P75-10` | Prompt injection in retrieved doc (synthetic) | `ANSWER` | `WARN` (`PASS`, 1 src, 0 leaked, 21 ms) | *(synthetic — excluded)* | `BLOCKED` |

---

## 2. Task E — Deep Investigation of Phase 7.4 Live Connector-Only Weakness

### Historical Phase 7.4 Regime C Baseline (Preserved Unchanged)
- **Overall Accuracy:** `8/22 (36.36%)`
- **Answered Accuracy:** `5/12 (41.67%)`
- **Evidence Support Rate:** `98.61% (n=12)`
- **Abstention Recall:** `3/4 (75.00%)`

### Why 98.61% Evidence Support Coexisted with 41.67% Answered Accuracy
`ClaimEvidenceVerifier` verifies **claim-to-retrieved-snippet fidelity**. In deterministic mode without a live LLM, the synthesizer echoes sentences from whatever Wikipedia snippets were retrieved. When Wikipedia returned off-topic pages (`NetBSD` for Linux 2.6 on `H74-03`, or `John M. Martinis / 1976` for `"Nobel Prize 2033"` on `H74-91`) or intro-only snippets missing deep-section numbers (`H74-05`, `H74-17`, `H74-51`, `H74-56`), the echoed claims were 100% entailed by the retrieved snippets — yielding `98.61%` evidence support despite failing the question rubric.

### Error Taxonomy Across All 14 Failed/Warned Phase 7.4 Regime C Questions

| Primary Error Class | Count | Task IDs | Root Cause |
|---|---:|---|---|
| `query_interpretation_failure` (+ retrieval coverage) | **4** | `H74-01`, `H74-09`, `H74-13`, `H74-25` | 14–15 word raw questions passed verbatim to Wikipedia search returned `0 results`. |
| `missing_source` (+ query/routing) | **4** | `H74-31`, `H74-38`, `H74-47`, `H74-55` | Academic paper topics (`FlashAttention`, `RoPE`, `AlphaFold2`) did not route to arXiv/Crossref; `H74-55` hit built-in local KB (`299,792 km/s`) before external search. |
| `retrieval_coverage_failure` (lead-snippet truncation) | **4** | `H74-05`, `H74-17`, `H74-51`, `H74-56` | Exact target Wikipedia page retrieved (`QUIC`, `NIST PQC`, `Methane`, `JWST`), but short lead snippet omitted deep-section facts (`9000`, `203`, `27`, `L2`/`1.5 million`). |
| `irrelevant_evidence` (+ missing future-year guard) | **2** | `H74-03`, `H74-91` | Off-topic Wikipedia hits (`NetBSD` for Linux 2.6; `John M. Martinis`/`1976` for future year `2033`). |

### Post-Fix Diagnostic on the Same 22 Live-Probe Questions *(Clearly Labeled Post-Fix Diagnostic, Not Blind Holdout)*
- **`H74-91` (`Nobel Prize 2033`) FIXED:** Now cleanly returns `ABSTAIN` (`0 sources`).
- **Retrieval coverage expanded:** `H74-13`, `H74-31`, `H74-38`, `H74-47` moved from `0 sources (ABSTAIN)` to retrieving `1–3` live sources via compact-query fallback (answered count rose from `12` → `16`).
- **Critical Honest Finding:** Overall accuracy remained `8/22 (36.36%)` and answered accuracy shifted to `5/16 (31.25%)` because:
  1. Compacting `H74-81` (`"confidential dataroom HELIOS-88"`) dropped the private identifier and matched generic Wikipedia pages (`WARN` instead of `ABSTAIN`), highlighting a clear trade-off of aggressive keyword compaction on unanswerable private entities.
  2. Short Wikipedia search snippets still omit deep-section technical values (`9000`, `203`, `27`, `L2`, `SRAM`, `Evoformer`), proving that **live LLM query reformulation, deep document reading (Playwright/full-page fetch), and semantic relevance gating** — not just keyword compaction — are required for high open-web accuracy.

---

## 3. Task F — Quality & Security Verification

| Check | Result |
|---|---|
| `no_silent_provider_substitution` | **PASSED** (`ProviderUnavailableError` raised when `gemini` unconfigured) |
| `token_call_and_cost_budget_enforced` | **PASSED** (`budget_exceeded` raised on call/cost limit) |
| `secret_redaction` | **PASSED** (`sk-...` and `AIza...` masked to `[REDACTED_KEY]`) |
| `bounded_retries_and_timeout_classification` | **PASSED** (`attempts=1`, `category=timeout`) |
| Prompt-injection in retrieved doc (`P75-10` + unit test) | **PASSED** (zero secret leakage, strictly grounded) |
