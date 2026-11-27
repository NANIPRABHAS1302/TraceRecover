# TraceRecover — Provenance-Preserving Claim Extraction Architecture (Phase 7.3.1)

## 1. Root Cause Diagnosed in Phase 7.3

During Phase 7.2 / 7.3 validation, contradiction detection on the 48-question unseen benchmark (`data/phase7_2_live_unseen.json`, Mode B `MOCK`) achieved:

- `TP = 5`, `FP = 3`, `TN = 40`, `FN = 0`
- Contradiction precision: `62.50%` (recall: `100.00%`)
- False-positive tasks: **`P72-01`**, **`P72-04`**, **`P72-10`**

### Exact Defect Mechanism

1. Multi-paper synthesis combined results from two distinct papers:
   - `Study 1 (arXiv:2309.10412, N=200) evaluated ... and KV-cache transformer eviction gains (87.5% accuracy).`
   - `Journal Article 2 (doi:10.18653/v1/2025.naacl-long.18, sample N=540) corroborated ... and KV-cache transformer memory pruning (91.4% accuracy).`
2. The protected `ClaimExtractor` (`grounding/claim_extractor.py`) splits compound sentences at `,\s+(?:and|but)\s+(?=[A-Z0-9])` and sentence boundaries, producing standalone sub-claims:
   - `CL-2`: `"KV-cache transformer eviction gains (87.5% accuracy)."`
   - `CL-4`: `"KV-cache transformer memory pruning (91.4% accuracy)."`
3. Previously, `ClaimEvidenceVerifier.verify_answer()` called `self.extractor.extract(clean_answer)` directly without retaining sentence-level or clause-level attribution. Because `CL-2` and `CL-4` lost their leading `Study 1` / `Journal Article 2` labels and sample sizes (`N=200` vs `N=540`), per-claim verification cross-compared `87.5%` against `Journal Article 2` (`91.4%`) and flagged a false logical contradiction.
4. Notably, pairwise document-level verification had already succeeded (`contradiction_details = []`), confirming the defect originated strictly from attribution loss during claim splitting.

---

## 2. Provenance-Preserving Architecture

To preserve the protected core research module `grounding/claim_extractor.py` unmodified while fully eliminating attribution loss, Phase 7.3.1 adds a provenance-preserving extraction wrapper inside `connectors/sources/verifier.py`:

### 2.1 Wrapper Pipeline (`_extract_claims_with_provenance`)

1. **Citation-Safe Sentence Segmentation (`_SENTENCE_SPLIT_RE`)**:
   Splits each paragraph line into sentences while protecting scholarly abbreviations (`et al.`, `Dr.`, `Fig.`, `No.`, `vs.`, `e.g.`, `i.e.`) and decimal identifiers (`arXiv:2309.10412`, `doi:10.18653/...`, `87.5%`). Paragraph breaks reset anaphoric state so provenance never leaks across unrelated sections.
2. **Observed Experimental Context Extraction (`_extract_context_metadata`)**:
   Extracts strictly observed contextual fields without ever fabricating unavailable metadata (missing fields remain `None`):
   - `source_document_id` (arXiv ID, DOI, or matched URL)
   - `source_url`
   - `study_identity` (e.g., `Study 1`, `arXiv Study 1`, `Journal Article 2`, `Smith et al.`)
   - `paper_title`
   - `author_or_publication_identity`
   - `publication_year`
   - `research_subject`
   - `research_metric`
   - `measured_value`
   - `unit` (normalized: `%`, `ms`, `s`, `kpa`, `psi`, `km/s`, `v`, etc.)
   - `dataset` / `benchmark`
   - `sample_size` (`N=200`, `sample N=540`, `200 participants`, etc.)
   - `model_or_configuration`
   - `evaluation_metric_definition` (`top-1 accuracy`, `top-5 accuracy`, `macro-f1`, `micro-f1`, `p95 latency`, etc.)
   - `evaluation_split` (`test split`, `validation split`, `held-out`, `zero-shot`, etc.)
   - `timeframe`
   - `original_supporting_excerpt`
3. **Explicit & Inherited Attribution Propagation**:
   - **`explicit`**: The extracted sub-claim directly carries a study label or scholarly document ID. Sets `active_sent_ctx` and `prev_sentence_ctx`.
   - **`inherited`** (clause split): A sub-claim split from the same sentence (e.g., `, and KV-cache transformer eviction gains (87.5% accuracy).`) inherits its parent sentence's `study_identity`, `source_document_id`, `sample_size`, `dataset`, `model_or_configuration`, and `evaluation_split` without overwriting its own `measured_value` or `research_metric`.
   - **`inherited`** (anaphoric sentence continuation): A subsequent sentence starting with an anaphoric cue (`"It also reports..."`, `"The study..."`, `"This paper..."`, `"Furthermore..."`) inherits the immediately preceding sentence's study provenance.
   - **`unattributed`**: No explicit or inherited study provenance is present.
   - **`ambiguous`**: Set during verification when an unattributed study-specific/experimental claim matches two or more distinct studies with differing experimental measurements and no uniquely disambiguating value.

### 2.2 Study-Aligned Source Citation Mapping

During `ClaimEvidenceVerifier.verify_answer()`:

- Every corpus document is pre-indexed with `_extract_context_metadata(d_text, source_name=..., source_url=...)`.
- When a claim carries `explicit` or `inherited` study provenance (`study_key` or `source_document_id`), source matching prefers the originating study's corpus document (`aligned_best`), preventing cross-paper citation drift.
- When `attribution_status == "ambiguous"`, TraceRecover refuses to fabricate provenance: `study_identity`, `source_url`, `provenance["study_identity"]`, and `provenance["source_document_id"]` remain `None`, `contradiction_classification` is set to `insufficient_comparison_context`, and the claim is downgraded from `Supported` with an explicit explanation.
- Legitimate multi-study corroboration of general consensus claims (e.g., two studies agreeing on the same conclusion with no competing experimental measurements) remains `Supported`.

---

## 3. Verification Record Schema (`ClaimVerificationRecord`)

Every claim in `claim_evidence_report["claims"]` now exposes:

- `claim_id`, `claim_text`
- `study_identity` (`str | None`)
- `attribution_status` (`explicit | inherited | unattributed | ambiguous`)
- `contradiction_classification` (`none | bibliographic_metadata_difference | different_experimental_context | comparable_differing_research_results | logical_contradiction | insufficient_comparison_context`)
- `provenance` (20-key dictionary with `None` for all unobserved fields)
- `conflicting_research_findings` (structured cross-study disagreement entries)
- Plus all existing fields (`verification_status`, `verification_code`, `supporting_source`, `source_url`, `trust_tier`, `confidence_score`, `verification_reason`, `contradictory_evidence`, `supporting_source_urls`, etc.) for full backward compatibility.

The top-level `claim_evidence_report` also summarizes `study_attribution_summary`, `contradiction_classifications`, and `conflicting_research_findings`.
