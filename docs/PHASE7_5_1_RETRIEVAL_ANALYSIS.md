# TraceRecover — Phase 7.5.1 Live Retrieval Quality Analysis

**Live LLM Retrieval Comparison Status:** **`BLOCKED`** — Before-vs-after live LLM retrieval comparison (`Regime C` vs `Regime D`) cannot be measured until a genuine live LLM provider is configured.

---

## 1. Consolidated Diagnosis of the 14 Phase 7.4 Live-Connector Failures

The frozen Phase 7.4 holdout (`data/phase7_4_independent_holdout.json`, SHA-256 `a5d796b3c5ca73fa2d9d0572f296c78df9a713859f38b83f642fd7a30a38b1ef`) and protected gold file (`data/phase7_4_holdout_gold.json`, SHA-256 `aade3b5dfec8cad13fda83b224efd05849811429105019980aa924810ded1106`) remain untouched.

Across the 22 live-probe questions in the historical Phase 7.4 Regime C run (`8/22 = 36.36%` overall, `5/12 = 41.67%` answered, `98.61%` evidence support), all 14 failed/warned cases belong to four verified structural bottlenecks:

| Bottleneck | Count | Task IDs | Why Deterministic Live-Connector Mode Struggles | What Genuine Live LLM + Deep Retrieval Must Prove |
|---|---:|---|---|---|
| **1. Verbose query formulation** | 4 | `H74-01`, `H74-09`, `H74-13`, `H74-25` | Passing 14–15 word natural-language questions verbatim to Wikipedia/DDG yields `0` results. | LLM query reformulation into targeted canonical search terms (`"Linux epoll EPOLLET"`, `"PostgreSQL MVCC VACUUM"`). |
| **2. Missing academic / primary source routing** | 4 | `H74-31`, `H74-38`, `H74-47`, `H74-55` | Paper topics (`FlashAttention`, `RoPE`, `AlphaFold2`) did not trigger `research_sources` (arXiv/Crossref/OpenAlex); `H74-55` was preempted by static local KB (`299,792 km/s`). | Domain-aware routing to scholarly APIs (`arXiv`/`Crossref`/`OpenAlex`) and official specs when external verification is requested. |
| **3. Lead-snippet truncation** | 4 | `H74-05`, `H74-17`, `H74-51`, `H74-56` | Correct Wikipedia article retrieved (`QUIC`, `NIST PQC`, `Methane`, `JWST`), but 250-char intro snippet omitted deep-section facts (`9000`, `203`, `27`, `L2`/`1.5 million`). | Deep-page inspection (`Playwright Chromium` `inspect_section` / `read_content`) + LLM evidence extraction across full sections. |
| **4. Irrelevant hits & private/future entities** | 2 | `H74-03`, `H74-91` *(plus `H74-81` trade-off after compaction)* | Wikipedia returned unrelated historical pages (`NetBSD`, `1976`), and `ClaimEvidenceVerifier` verified snippet fidelity rather than query-to-evidence relevance. | Semantic relevance gating + preserving rare/private identifiers (`HELIOS-88`) so unanswerable premises always `ABSTAIN`. |

---

## 2. Empirical Lesson from Phase 7.5 Post-Fix Diagnostic

Phase 7.5 proved empirically on the same 22 live-probe questions that **deterministic keyword compaction alone is not a substitute for LLM intelligence**:
- Future-year abstention (`H74-91`) was cleanly fixed (`WARN` → `ABSTAIN`), and 4 previously zero-hit queries (`H74-13`, `H74-31`, `H74-38`, `H74-47`) gained live sources.
- However, overall live-connector accuracy remained `8/22 (36.36%)` because short Wikipedia lead snippets still lacked deep-section facts and compacting private-entity query `H74-81` (`HELIOS-88`) dropped the private token and matched generic pages.
- **Conclusion:** Any genuine accuracy jump on open-web research must be measured directly with a live LLM (`Regime D`) paired with deep-page extraction and strict verifier/abstention gating — never assumed in advance.
