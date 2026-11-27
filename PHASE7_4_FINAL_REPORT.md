# TraceRecover — Phase 7.4 Final Report
## Independent Holdout Evaluation & Research Reliability Audit

**Date:** 2026-11-25  
**Evaluator Version:** `7.4.0`  
**Holdout Dataset Version:** `1.0.0` (92 End-to-End Holdout Questions, 18 Domains, 12 Provenance Cases, 18 Contradiction Cases)  
**Final Capstone Readiness Classification:** **`2. CONDITIONALLY READY`** *(Do not begin Phase 8 until Regime D live LLM validation is completed)*

---

## 1. Executive Summary

Phase 7.4 independently evaluated TraceRecover on a newly created, frozen, zero-overlap holdout benchmark (`data/phase7_4_independent_holdout.json`) strictly separated from its protected gold labels (`data/phase7_4_holdout_gold.json`).

### Frozen Holdout Integrity Hashes (Recorded Before Execution & Unchanged Throughout)
- `data/phase7_4_independent_holdout.json` SHA-256: `a5d796b3c5ca73fa2d9d0572f296c78df9a713859f38b83f642fd7a30a38b1ef` (92 questions, 18 domains, `contains_gold_labels = false`)
- `data/phase7_4_holdout_gold.json` SHA-256: `aade3b5dfec8cad13fda83b224efd05849811429105019980aa924810ded1106` (92 E2E gold specs + 12 Task D provenance cases + 18 Task E contradiction cases)
- Zero overlap verified programmatically against `eval/tasks.json`, `data/phase6_unseen_50.json`, `data/phase7_unseen_60.json`, `data/phase7_1_unseen.json`, and `data/phase7_2_live_unseen.json`.

### Genuine Defects Discovered by the Blind Holdout & Resolved via Task C Protocol
The initial blind holdout run (`results/phase7_4-validation-20261125T031926Z/`) uncovered **3 real, generalizable defects** in `connectors/sources/verifier.py`:
1. **Title-vs-negated-body self-contradiction** (`15 FP` in blind Regime B): a short affirmative document title falsely contradicted its own negated body sentence at clause level.
2. **RFC standard IDs & integer major software versions** (`2 FP` in Regime B + `1 FP` in Regime C): `RFC 9000` vs `RFC 9114` and `PostgreSQL 14` vs `PostgreSQL 17` were treated as conflicting numeric facts.
3. **`Author et al. (YYYY)` lookbehind bug** (`2` attribution misses on `PROV-12`): `(?<!\bal)` omitted the literal dot `\.`, causing sentence splits at `"al."`.

All three defects were recorded (preserving the initial blind run artifact untouched), covered by development regression tests outside the holdout (`tests/test_phase7_3_1_provenance_and_contradiction.py`), fixed in `connectors/sources/verifier.py`, and re-evaluated against the **exact unmodified frozen holdout** (`results/phase7_4-validation-20261125T040247Z/`).

---

## 2. Four-Regime Holdout Results (Post-Fix Frozen Holdout)

| Metric | Regime A: OFFLINE (n=92) | Regime B: MOCK (n=92) | Regime C: LIVE CONNECTOR (n=22) | Regime D: LIVE LLM + LIVE CONNECTOR (n=92) |
|---|---:|---:|---:|---:|
| Answered / Warn / Abstained / Failed / Blocked | `9 / 0 / 83 / 0 / 0` | `76 / 23 / 16 / 0 / 0` | `12 / 11 / 10 / 0 / 0` | `0 / 0 / 0 / 0 / 92` |
| Overall Accuracy | `21/92 (22.83%)` | **`88/92 (95.65%)`** *(blind: 71/92)* | `8/22 (36.36%)` | `N/A (BLOCKED)` |
| Answered Accuracy | `7/9 (77.78%)` | **`74/76 (97.37%)`** | `5/12 (41.67%)` | `N/A (BLOCKED)` |
| Evidence Support Rate | `100.00% (n=9)` | **`99.74% (n=76)`** | `98.61% (n=12)` | `N/A (BLOCKED)` |
| Unsupported Claim Rate | `0.00% (n=9)` | **`0.26% (n=76)`** | `1.39% (n=12)` | `N/A (BLOCKED)` |
| Abstention Precision / Recall | `14/83 (16.87%)` / `14/15 (93.33%)` | `14/16 (87.50%)` / `14/15 (93.33%)` | `3/10 (30.00%)` / `3/4 (75.00%)` | `N/A (BLOCKED)` |
| Incorrect Abstention Rate | `69/77 (89.61%)` | `2/77 (2.60%)` | `7/18 (38.89%)` | `N/A (BLOCKED)` |
| Citation Precision / Coverage | `N/A` / `N/A` | **`100.0% (n=68)`** / `67/68 (98.53%)` | **`100.0% (n=11)`** / **`11/11 (100.0%)`** | `N/A (BLOCKED)` |
| Claim-to-Source Mapping / Traceability | `N/A` / `N/A` | **`79/79 (100.0%)`** / **`269/269 (100.0%)`** | `N/A` / **`63/63 (100.0%)`** | `N/A (BLOCKED)` |
| Study Attribution Accuracy (E2E) | `N/A` | **`24/24 (100.0%)`** | `N/A` | `N/A (BLOCKED)` |
| Contradiction (`TP / FP / TN / FN`) | `0 / 0 / 85 / 0` | **`6 / 0 / 85 / 1`** *(blind: 6/17/68/1)* | `0 / 0 / 22 / 0` | `N/A (BLOCKED)` |
| Contradiction Precision / Recall / F1 | `N/A / N/A / N/A` | **`100.0% (6/6)` / `85.71% (6/7)` / `0.9231`** | `N/A / N/A / N/A` | `N/A (BLOCKED)` |
| False Contradiction Rate | `0/85 (0.00%)` | **`0/85 (0.00%)`** | **`0/22 (0.00%)`** | `N/A (BLOCKED)` |
| Tool Selection / Research Plan Quality | `89/92 (96.74%)` / `90/92 (97.83%)` | `90/92 (97.83%)` / `89/92 (96.74%)` | `21/22 (95.45%)` / `22/22 (100.0%)` | `N/A (BLOCKED)` |
| Recovery Success | `3/4 (75.00%)` | `3/4 (75.00%)` | `N/A (0/0)` | `N/A (BLOCKED)` |
| Mean Latency | `15.90 ms` | `99.37 ms` | `4,392.34 ms` | `N/A (BLOCKED)` |
| Provider-Reported / Estimated Tokens | `0 / 0 (none)` | `0 / 0 (none)` | `0 / 0 (none)` | `0 / 0 (BLOCKED)` |

---

## 3. Dedicated Independent Audits Summary

- **Task D — Provenance Audit (12 cases, 9 patterns):**
  - `12/12` cases passed (`100.0%` study attribution `12/12`, `100.0%` claim-to-source mapping `15/15`, `100.0%` ambiguity detection `17/17`, `100.0%` metadata completeness `31/31`, **`0.0%` fabricated metadata rate `0/47`**).
- **Task E — Labelled Contradiction Audit (18 cases, 10 scenarios, 6 classes):**
  - Binary: `TP=6, FP=0, TN=12, FN=0` → **Precision `100.0% (6/6)`, Recall `100.0% (6/6)`, F1 `1.0000`, Accuracy `18/18 (100.0%)`**.
  - 6-Class Exact Classification: **`17/18 (94.44%)`** (1 bibliographic reprint case mapped to `NONE` rather than `BIBLIOGRAPHIC_METADATA_DIFFERENCE`; zero false or missed contradictions).
- **Task H — Live Connector Reliability (6 Connectors, Substantive Content-Support Checked):**
  - **`6/6` passed**: `arXiv` (`1329 ms`, `1735 chars`, `SUPPORTED` sim `0.700`), `Crossref` (`9185 ms`, `627 chars`, `SUPPORTED` sim `0.778`), `OpenAlex` (`5263 ms`, `363 chars`, `SUPPORTED` sim `0.657`), `Wikipedia` (`3205 ms`, `500 chars`, `SUPPORTED` sim `0.667`), `DuckDuckGo` (`1719 ms`, `1085 chars`, `SUPPORTED` sim `1.000`), and `Playwright Chromium` on `https://www.sqlite.org/stricttables.html` (`6355 ms`, `4000 chars`, `SUPPORTED` sim `0.818`).
- **Tasks I & J — Dashboard E2E, Full Regression & Security Suite:**
  - Full repository `pytest -v`: **`202 passed, 0 failed, 0 skipped`** (`243.57s`), including all 7 Chromium Playwright E2E suites (including new `tests/test_phase7_4_holdout_and_e2e.py`), Phase 7.1/7.2/7.3/7.3.1 suites, secret-redaction tests, prompt-injection tests, and SSRF/connector security tests.
  - Protected core modules (`grounding/`, `agent/react_agent.py`, `agent/planner.py`, `healing/`, `uncertainty/`, `eval/runner.py`, `eval/metrics.py`, `eval/tasks.json`, `tools/`, `config.py`) and all historical `results/` folders remain untouched.

---

## 4. Final Classification & Honest Limitations

**Classification:** **`2. CONDITIONALLY READY`**

- **Why not `READY FOR PHASE 8`:**
  1. **Regime D (`LIVE LLM + LIVE CONNECTOR`) remains `BLOCKED` (`92/92`)** because no `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`, or `LOCAL_LLM_BASE_URL` is configured on this host.
  2. **Open-web deterministic retrieval without a live LLM (`Regime C`) achieved only `8/22 (36.36%)` overall accuracy** (`5/12 = 41.67%` answered) and emitted a `WARN` on future-event query `H74-91` when Wikipedia returned a general Nobel Prize article.
  3. **Four honest Regime B failures remain unfixed** (`H74-55` and `H74-87` due to static local KB keyword preemption; `H74-16` due to trailing prose after Python expression; `H74-67` due to intentional AST security block on Python generator expressions).
- **Detailed Companion Documents:**
  - `docs/PHASE7_4_BENCHMARK_DESIGN.md`
  - `docs/PHASE7_4_RELIABILITY_EVALUATION.md`
  - `docs/PHASE7_4_CAPSTONE_READINESS.md`
