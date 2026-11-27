# TraceRecover — Phase 7.4 Independent Holdout Benchmark Design

**Dataset Version:** `1.0.0`  
**Evaluator Version:** `7.4.0`  
**Frozen Timestamp (UTC):** `2026-11-25T03:13:49.593792+00:00`

---

## 1. Benchmark Architecture & Holdout Protection

To prevent benchmark overfitting and circular self-evaluation, Phase 7.4 separates public task inputs from protected ground-truth labels across three frozen artifacts:

| Artifact | Purpose | SHA-256 Hash | Size (bytes) |
|---|---|---|---:|
| `data/phase7_4_independent_holdout.json` | Public question prompts ONLY (`contains_gold_labels = false`) | `a5d796b3c5ca73fa2d9d0572f296c78df9a713859f38b83f642fd7a30a38b1ef` | 44,022 |
| `data/phase7_4_holdout_gold.json` | Protected gold labels, rubric criteria, citation maps, Task D & E suites | `aade3b5dfec8cad13fda83b224efd05849811429105019980aa924810ded1106` | 153,646 |
| `data/phase7_4_holdout_manifest.json` | Frozen integrity manifest verified before every harness execution | Verified at runtime | — |

### Protection Guarantees
1. **Zero Gold Leakage:** `eval/phase7_4_validation.py` verifies both SHA-256 hashes before starting, loads ONLY `data/phase7_4_independent_holdout.json` during agent execution across all regimes, persists raw execution traces (`raw_traces_regime_*.jsonl`), and loads `data/phase7_4_holdout_gold.json` exclusively during post-hoc scoring.
2. **Zero Historical Overlap:** Automated normalization checks (`tests/test_phase7_4_holdout_and_e2e.py`) verify 0 overlapping questions with `eval/tasks.json`, `data/phase6_unseen_50.json`, `data/phase7_unseen_60.json`, `data/phase7_1_unseen.json`, and `data/phase7_2_live_unseen.json` (and zero reuse of `P72-01`, `P72-04`, `P72-10`).
3. **Immutable Holdout Policy:** When the initial blind holdout run (`results/phase7_4-validation-20261125T031926Z/`) uncovered 3 genuine verifier defects, the holdout and gold files were kept 100% unmodified (identical SHA-256 hashes), development regression tests were added outside the holdout (`tests/test_phase7_3_1_provenance_and_contradiction.py`), both pre-fix and post-fix runs were preserved on disk, and all remaining failures (`H74-16`, `H74-55`, `H74-67`, `H74-87`, and live-connector gaps) are reported openly.

---

## 2. Dataset Size, Domain Distribution & Overlapping Category Quotas

- **End-to-End Holdout Questions:** **92** (`H74-01` through `H74-92`)
- **Live Connector Evaluation Subset:** **22** (`live_probe = true`)
- **Dedicated Independent Provenance Audit Suite (Task D):** **12** cases (`PROV-01`–`PROV-12`)
- **Dedicated Independent Labelled Contradiction Suite (Task E):** **18** cases (`CONTRA-01`–`CONTRA-18`)
- **Distinct Domains:** **18** (minimum required: 16)

### Domain Distribution (92 End-to-End Questions)

| # | Domain | Questions | # | Domain | Questions |
|---:|---|---:|---:|---|---:|
| 1 | `operating_systems` | 4 | 10 | `natural_language_processing` | 5 |
| 2 | `computer_networking` | 4 | 11 | `computer_vision` | 4 |
| 3 | `database_internals` | 4 | 12 | `bioinformatics_and_genomics` | 4 |
| 4 | `compilers_and_languages` | 4 | 13 | `climate_and_geoscience` | 4 |
| 5 | `cryptography_and_privacy` | 4 | 14 | `astronomy_and_physics` | 4 |
| 6 | `distributed_consensus` | 4 | 15 | `quantitative_finance_and_math` | 6 |
| 7 | `cloud_native_infrastructure` | 4 | 16 | `formal_verification_and_pl` | 4 |
| 8 | `computer_architecture` | 4 | 17 | `provenance_and_attribution_audit` | 9 |
| 9 | `information_retrieval` | 6 | 18 | `contradiction_and_abstention_audit` | 14 |

### Overlapping Category Quotas

| Category | Required Minimum | Holdout Count | Status |
|---|---:|---:|---|
| A. General factual research | 12 | **28** | Met |
| B. Technical documentation | 12 | **20** | Met |
| C. Academic research | 12 | **29** | Met |
| D. Multi-step research | 10 | **10** | Met |
| E. Multi-source research | 10 | **25** | Met |
| F. Study provenance and claim attribution | 12 | **14** (+12 Task D) | Met |
| G. Context-aware contradiction detection | 12 | **12** (+18 Task E) | Met |
| H. Insufficient-evidence and abstention | 8 | **8** | Met |
| I. Numerical and temporal verification | 8 | **20** | Met |

---

## 3. Ground-Truth Construction Methodology

Every gold entry in `data/phase7_4_holdout_gold.json` specifies:
- `task_id`, `domain`, `categories`, `fixture_type` (`authoritative_primary_source` vs `synthetic_controlled_test`)
- `expected_decision` (`ANSWER` | `ABSTAIN`)
- `expected_answer_criteria` (`required_substrings` + `required_supporting_claims`)
- `expected_evidence_sources` and `expected_source_urls`
- `expected_citation_relationships` (claim hint → expected study identity & expected URL)
- `expected_contradiction_label` and `expected_contradiction_class`
- `expected_abstention_behavior`, `expected_tool`, and `evaluation_rationale`

### Source Honesty & Synthetic Labelling Policy
- **Authoritative Primary Sources (`fixture_type = "authoritative_primary_source"`):** Verifiable public standards and references including IETF RFCs (`RFC 9000`, `RFC 9114`, `RFC 6811`, `RFC 9106`), NIST standards (`FIPS 203`, `FIPS 197`, `SP 800-38D`, `CODATA`), Python `PEP 703`, Linux `man7.org`, PostgreSQL official docs, SQLite official docs, Kubernetes docs, W3C Trace Context, LLVM LangRef, Rust Book, IPCC AR6, NASA JWST, LIGO, and landmark arXiv/Nature papers (`arXiv:2205.14135`, `arXiv:2004.12832`, `arXiv:2104.09864`, `arXiv:2010.11929`, `arXiv:2006.11239`, `Nature s41586-021-03819-2`).
- **Explicitly Labelled Synthetic Fixtures (`fixture_type = "synthetic_controlled_test"`):** Controlled contradiction traps, multi-study experimental-context pairs, and unanswerable future/private queries are explicitly marked synthetic and never represented as real published papers.
- TraceRecover was never used as the source of its own ground truth.

---

## 4. Reproducibility Instructions

```powershell
# 1. Verify frozen holdout hashes, gold separation, and zero overlap
.\venv\Scripts\python.exe -m pytest tests/test_phase7_4_holdout_and_e2e.py -v

# 2. Run the independent Phase 7.4 holdout evaluation (all 4 regimes + Tasks D, E, H)
.\venv\Scripts\python.exe eval\phase7_4_validation.py

# 3. Run the complete repository regression & security test suite
.\venv\Scripts\python.exe -m pytest -v
```
