# TraceRecover Phase 7.1 Architecture

## Overview

Phase 7.1 refines TraceRecover's evidence verification, scholarly multi-paper reasoning, structured answer synthesis, uncertainty taxonomy, and provider error discipline while preserving the canonical 100-task research core (`agent/react_agent.py`, `agent/planner.py`, `grounding/`, `healing/`, `uncertainty/`, `eval/runner.py`, `eval/metrics.py`, `eval/tasks.json`, `tools/*`, and `config.py`).

## Core Refinements

### 1. Context-Aware Contradiction & Claim Verification (`connectors/sources/verifier.py`)
- **Bibliographic Identity Stripping (`_strip_identity_metadata`)**: Masks `arXiv:\d{4}\.\d+`, arXiv URLs, DOIs, parenthetical author-year citations `(Smith et al., 2024)`, scholarly `Published <year>` headers, `Cited by N works`, and `Study N` / `Paper N` / `Experiment N` index numbers before factual comparison.
- **Structured Mention Extraction (`_extract_structured_mentions`)**: Classifies numeric and version expressions into three disjoint categories:
  1. `identity_metadata` (bibliographic identifiers, citation counts, study indices, venue dates) — never compared as conflicting facts.
  2. `research_measurement` (accuracy, F1, sample sizes `N=...`, latency across distinct studies/datasets) — separate studies comparing different architectures/cohorts do not contradict unless opposing claims address the identical subject and measurement.
  3. `factual_claim` (software version/release years, physical constants like boiling point or pressure, explicit numeric specifications) — triggers `CONTRADICTED` when independent sources disagree on matching subject & property.
- **Temporal Evolution Exception (`_is_temporal_evolution`)**: Recognizes milestone progression (e.g., `2020 v1.0` transitioning to `2024 v2.0`) without triggering false contradictions.
- **Wrapper Deduplication Fix in `build_evidence_corpus`**: Skips serializing outer wrapper dictionaries (such as `contradiction_detected=False`) whenever `documents` or `results` lists are already extracted, preventing JSON metadata booleans from triggering false polarity conflicts.
- **Canonical Verification Records (`ClaimVerificationRecord`)**: Records `verification_status` (`Supported`, `Partially supported`, `Contradicted`, `Unsupported`, `Insufficient evidence`), `verification_code` (`SUPPORTED`, `PARTIALLY_SUPPORTED`, `CONTRADICTED`, `UNSUPPORTED`, `INSUFFICIENT_EVIDENCE`), `supporting_source_urls`, `source_identity`, `trust_tier`, `retrieval_timestamp`, `confidence_score`, `verification_reason`, `independent_source_count`, and `duplicate_source_count`. Trust tiers remain strictly metadata; `tier1` provenance never overrides a detected factual contradiction.

### 2. Structured Evidence Synthesis & Abstention Taxonomy
- **Synthesizer (`agent/synthesizer.py`)**: `EvidenceGroundedSynthesizer.synthesize_structured()` structures responses into `direct_answer`, `explanation`, `supporting_evidence`, verified `citations`, detected `contradictions`, and explicit `limitations`, while keeping `raw_answer` concise and grounded for `EvidenceChecker` alignment.
- **Abstention Taxonomy (`agent/uncertainty_taxonomy.py`)**: Separates all 8 categories (`grounded_confident`, `low_confidence_synthesis`, `insufficient_evidence`, `contradictory_evidence`, `tool_failure`, `missing_credentials`, `unanswerable_question`, `policy_blocked`) and attaches an explicit `abstention_reason`. Unrecovered tool errors map to `tool_failure` rather than being mislabeled `insufficient_evidence`, and multi-paper academic summaries no longer trigger false `contradictory_evidence` abstentions.

### 3. Provider Hardening & Query Workspace UI
- **Provider Adapter (`agent/provider_adapter.py`)**: Adds `validate_configuration()`, `provider_status_summary()`, placeholder/malformed API key detection (`invalid_api_key`), malformed endpoint checks (`invalid_configuration`), HTTP 401/403/429/timeout classification, call/cost caps, `_redact_secret_text()` leak prevention, and zero silent fallback across providers.
- **Open-Ended Runner & Dashboard (`agent/open_ended_runner.py`, `static/index.html`)**: Emits `execution_mode`, `provider_info`, `plan_stats` (`total_subtasks`, `completed_subtasks`, `failed_subtasks`, `gap_fill_attempts`, `stop_reason`), `structured_answer`, and `abstention_reason`, surfaced in Query Workspace under an explicit light SaaS layout with zero client-side storage (`localStorage.length === 0`).
