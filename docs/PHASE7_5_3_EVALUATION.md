# TraceRecover — Phase 7.5.3 Evaluation & Retrieval Readiness Summary

**Live LLM Evaluation Status:** **BLOCKED** (no genuine live LLM provider configured)

---

## 1. Preserved Historical Evaluation Baselines (Untouched)

All historical evaluation results and frozen holdout datasets remain preserved bit-for-bit:

| Evaluation Regime / Artifact | Scope | Verified Result | Integrity Hash / Status |
|---|---|---|---|
| **Phase 7.4 Frozen Holdout** (`data/phase7_4_independent_holdout.json`) | 92 questions, 18 domains | Mock overall accuracy: `95.65%` (post-fix); answered accuracy: `97.37%`; evidence support: `99.74%` *(post-fix diagnostic on same frozen set, not blind)* | SHA-256 `a5d796b3c5ca73fa2d9d0572f296c78df9a713859f38b83f642fd7a30a38b1ef` |
| **Phase 7.4 Protected Gold** (`data/phase7_4_holdout_gold.json`) | 92 gold labels | Preserved untouched | SHA-256 `aade3b5dfec8cad13fda83b224efd05849811429105019980aa924810ded1106` |
| **Phase 7.4 Regime C (Live Connectors Only, No Live LLM)** | 22 live-probe questions | Overall accuracy: `8/22 (36.36%)`; answered accuracy: `5/12 (41.67%)`; evidence support: `98.61%` | Preserved in `results/` |
| **Phase 7.3.1 Provenance & Contradiction Audit** | Targeted split-claim & binary contradiction suites | Provenance: `12/12`; dedicated binary contradiction audit: `18/18` | Preserved |
| **Phase 7.5.2 Full Automated Regression Suite** | Entire repository (`pytest -v`) | **214/214 passed**, 0 failed, 0 skipped (113.43s), including 8 Chromium E2E suites | Verified `2026-11-25` |
| **Phase 7.5.3 Regime D (Genuine Live LLM + Live Connectors)** | 5 live scenarios + matched holdout comparison | **BLOCKED** (`missing_credentials`) | Zero fabricated metrics |

---

## 2. Retrieval Bottlenecks Ready to Test Once a Live LLM Is Configured

From the Phase 7.4 and Phase 7.5 live-connector failure diagnostics (`docs/PHASE7_5_1_RETRIEVAL_ANALYSIS.md`), four measured failure modes account for all 14 errors in the `8/22 (36.36%)` Regime C baseline:

1. **Verbose query formulation (4 cases: `H74-01`, `H74-09`, `H74-13`, `H74-25`):** Natural-language questions passed verbatim to search connectors produced zero hits.
2. **Missing academic/primary source routing (4 cases: `H74-31`, `H74-38`, `H74-47`, `H74-55`):** Paper-level topics did not route to scholarly APIs (`arXiv`/`Crossref`/`OpenAlex`).
3. **Lead-snippet truncation (4 cases: `H74-05`, `H74-17`, `H74-51`, `H74-56`):** Correct articles were retrieved, but short intro snippets omitted deep-section numerical/technical facts.
4. **Irrelevant hits & private/future entities (2 cases: `H74-03`, `H74-91`, plus `H74-81` compaction trade-off):** Deterministic keyword compaction alone cannot reliably preserve rare private identifiers while removing stop-words.

**Evaluation Discipline:** Phase 7.5.3 does **not** assume that adding a live LLM will automatically resolve all four bottlenecks. Once a live provider is configured, Regime D must be measured directly on matched queries and scored against the unchanged gold labels.

---

## 3. Execution-Integrity Guarantees Verified Before Live Activation

All Phase 7.5.2 protections remain active and verified by automated tests (`tests/test_phase7_5_2_synthesis_and_execution_integrity.py`):

- Failed live planning (`Step 1`) fails closed with `ProviderUnavailableError` and zero silent fallback to `local`.
- Failed live synthesis (`Step 2+` or post-loop `EvidenceGroundedSynthesizer`) propagates/records `synthesis_status="failed"`, `failed_stage="synthesis"`, `answer_origin="none_failed"`, `fallback_used=False`, abstains with `provider_synthesis_failure`, and preserves all retrieved `external_sources`, `retrieved_evidence`, `citations`, and `trace_steps`.
- Dashboard Query Workspace renders a red `FAILED` badge (`pill-red`), explicit retrieval-vs-synthesis failure banner, and `ws-execution-integrity` strip — never misrepresenting a failed live call as a successful `LIVE LLM` result.
- Secret redaction masks `sk-`, `AIza`, `Bearer`, `x-goog-api-key`, `x-api-key`, `authorization`, and `api_key=` values across exceptions, traces, API payloads, and UI outputs.
