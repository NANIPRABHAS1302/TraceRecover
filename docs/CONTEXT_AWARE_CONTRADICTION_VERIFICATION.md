# TraceRecover — Context-Aware Contradiction Verification (Phase 7.3.1)

## 1. Design Principle

A numeric or textual difference between two research statements is **not** automatically a logical contradiction. Before flagging a logical contradiction, TraceRecover's `_evaluate_contradiction_with_context()` evaluates whether both statements actually refer to the same experimental target and measurement definition:

1. Study identity / scholarly document ID (`Study 1` vs `Journal Article 2`, `arXiv:...` vs `doi:...`)
2. Research subject and local context tokens
3. Metric name and metric definition (`accuracy` vs `latency`, `macro-F1` vs `micro-F1`, `top-1 accuracy` vs `top-5 accuracy`)
4. Unit (`%` vs `ms`, `kPa` vs `psi`, `km/s`, `V`)
5. Dataset / benchmark (`MNLI` vs `PubMedQA`, `ImageNet`, `SQuAD`)
6. Sample size (`N=200` vs `N=540`)
7. Model or configuration (`BERT-base` vs `RoBERTa-large`, `4-bit` vs `FP16`)
8. Evaluation split (`test split` vs `validation split`, `zero-shot` vs `few-shot`)
9. Timeframe / version evolution (`v1` -> `v2`, `2020` vs `2024` upgrade)

---

## 2. Six-Class Contradiction Taxonomy

| Classification | Triggers `CONTRADICTED`? | Semantics & Example |
|---|---|---|
| `none` | No | Corroborating or non-overlapping evidence with no discrepancy. |
| `bibliographic_metadata_difference` | No | Different publication years, arXiv IDs, DOIs, citation counts, or study numbering across complementary papers (e.g., `Paper 1 (2022)` vs `Article 2 (2025)`). |
| `different_experimental_context` | No | Research measurements differ because of distinct studies, datasets, sample sizes (`N=200` vs `N=540`), models/configurations, splits, units (`%` vs `ms`), or metric definitions (`macro-F1` vs `micro-F1`, `top-1` vs `top-5`). |
| `comparable_differing_research_results` | Yes (Preserved distinctly) | Two independent studies reach genuinely opposing conclusions on the same research proposition (e.g., Study 1 concludes sparse attention improves retrieval accuracy vs Study 2 concludes it does not). Recorded in `conflicting_research_findings` and `contradiction_details` without collapsing into a single-experiment contradiction. |
| `logical_contradiction` | Yes | Incompatible factual claims on the same entity/property (e.g., TLS 1.3 RFC 8446 published in 2018 vs 2023; Earth escape velocity `11.2 km/s` vs `4.5 km/s`; direct polarity refutation on the same entity), OR the **same study** reporting incompatible values for the same metric and experimental configuration (e.g., Study 1 reporting `87.5%` vs `71.0%` on MNLI test split with BERT-base, `N=200`). |
| `insufficient_comparison_context` | No (Flags uncertainty) | Empty evidence corpus or ambiguous unattributed multi-study claim where comparison context is insufficient to establish single-study provenance. |

---

## 3. Preserving True Contradiction Recall

Phase 7.3.1 strengthens precision **without** weakening recall:

- **Same-study incompatibility (Case C)**: Both claim-vs-document and pairwise corpus checks detect when the same explicit study reports conflicting values under matching configuration (`same_explicit_study = True` and `context_diffs = []`).
- **Incompatible facts & constants**: Non-measurement factual claims (RFC publication dates, hardware voltages, physical constants, version release years, direct polarity/negation refutations) remain strictly enforced as `logical_contradiction`.
- **Cross-study opposing conclusions (Case D)**: Genuine opposing conclusions across independent studies (`improves` vs `does not improve` / `fails to` / `no benefit`) are preserved with `comparable_differing_research_results` and surfaced in both `conflicting_research_findings` and the dashboard Claim-Level Verification table.
- **Unit & metric-definition guards (Case H)**: Incompatible units (`%` vs `ms`) and metric definitions (`macro-F1` vs `micro-F1`, `top-1 accuracy` vs `top-5 accuracy`) prevent invalid numeric comparison while still allowing polarity checks if an explicit refutation exists.

---

## 4. Dashboard Transparency

The Query Workspace Claim-Level Verification table (`static/index.html`) displays:

- **Claim ID** (`CL-1`, `CL-2`, ...)
- **Extracted Claim**
- **Study / Attribution** (originating study label + badge for `explicit`, `inherited`, `unattributed`, or `ambiguous`)
- **Supporting Source** (clickable URL + trust tier, or `Ambiguous (multiple studies)` / `Unverified`)
- **Verification Status** (`Supported`, `Partially supported`, `Contradicted`, `Unsupported`, `Insufficient evidence`)
- **Contradiction Classification** (color-coded badge for all 6 taxonomy classes)
- **Confidence**
- **Verification Reason** (explicit human-readable explanation)

When `conflicting_research_findings` are present, the workspace also renders an informational callout distinguishing cross-study research disagreements from single-experiment logical contradictions.
