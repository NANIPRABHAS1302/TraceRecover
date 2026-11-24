# Issue Resolution Report

## Scope

This report records the concrete defects identified during the 2026-11-23 read-only audit of `C:\tracerecover`, the root causes, all corrective engineering actions taken across Phases 1–6, and verified test/benchmark outcomes.

## Issues Identified and Resolved

### 1. Empty `eval/metrics.py` (0 bytes — Critical Blocker)
- **Cause:** `eval/metrics.py` had been truncated to 0 bytes, causing 3 pytest collection `ImportError` failures (blocking 19 evaluation tests) and breaking `eval/runner.py`, `eval/run_baseline.py`, `eval/run_tracerecover.py`, and `eval/run_all.py`.
- **Resolution:** Reconstructed `eval/metrics.py` with all original functions (`wilson_interval`, `task_success_rate`, `recovery_rate`, `hallucination_rate`, `expected_calibration_error`, `abstention_accuracy`, `latency_metrics`, `summarize_metrics`) plus three new metric functions: claim-level `unsupported_claim_rate()`, aggregated `evidence_coverage()`, and `cost_metrics()`.
- **Verification:** `tests/test_metrics.py`, `tests/test_evaluation.py`, and `tests/test_phase8_evaluation_tools.py` all collect and pass cleanly.

### 2. Empty `.gitignore` (0 bytes — Security/Hygiene Risk)
- **Cause:** `.gitignore` existed on disk as an empty file, leaving `.env`, `venv/`, `__pycache__/`, `.pytest_cache/`, and `logs/` unprotected.
- **Resolution:** Populated `.gitignore` with standard Python, virtual environment, secret `.env`, and runtime log exclusion patterns.

### 3. Cross-Tool Numeric False Contradictions in `grounding/evidence_checker.py`
- **Cause:** On multi-hop `search` + `calculator` tasks (`MHSM-01..20` and `NCT-11..20`), `_numeric_relation()` treated any descriptive prose sentence containing a single number as a `result_claim` and cross-compared it against unrelated `calculator` integer outputs (and vice versa), triggering 30 spurious `CONTRADICTED` abstentions at 0% failure rate.
- **Resolution:** Refined `EvidenceChecker._numeric_relation()` so scalar numeric outputs are compared only against bare numbers or claims carrying explicit numeric/result markers, and non-numeric string outputs require lexical overlap (>=2 shared content words) before cross-comparing isolated numbers. Added a dedicated regression test (`test_multihop_search_prose_and_calculator_result_do_not_cross_contradict`).
- **Verification:** 0% failure-rate task success for TraceRecover rose from 70/100 to 100/100 (0 incorrect abstentions, 30/30 correct abstentions, 0.000 hallucination rate).

### 4. Missing Architectural Components (Phases 2–6)
- **Expanded 100-Task Benchmark & Data Artifacts:** Expanded `tools/search_tool.py` (metrics M-1..M-20, sensors S-81..S-100, and 10 additional domain references across 5 domains with record-ID guard) and `tools/mock_api.py` (tickets T-401..T-415 + incomplete/empty edge cases T-416..T-419). Created 100-task `eval/tasks.json` (version `2.0`), `data/tracerecover_synthetic_benchmark_100.json`, `data/tracerecover_execution_telemetry.csv` (600 rows), and `data/tracerecover_step_level_telemetry.csv` (1,769 rows).
- **Layer 4 JSON Linearization & DeBERTa-v3 NLI Verification:** Added `linearize_json()` in `grounding/evidence_checker.py`, created `grounding/nli_verifier.py` (`DebertaNLIVerifier` with `transformers`/`torch` loader + deterministic offline CI fallback), integrated `use_nli: bool = False` into `EvidenceChecker` and `ReactAgent`, and enforced strict `output_state != "discarded"` filtering.
- **Layer 5 Validation Threshold Calibration:** Created `uncertainty/calibrator.py` (`calibrate_thresholds()`) to optimize `answer_threshold` and `abstain_threshold` empirically on validation records.
- **Layers 1 & 2 ToolExecutor, Caching, & Provider Cost Telemetry:** Created `tools/executor.py` (`ToolExecutor` with centralized registration, AST/schema pre-validation, injector-aware deterministic caching, and `committed`/`discarded` tagging via `TraceLogger.log_step(..., output_state=...)`). Upgraded `config.py` (`LLMClient`) to track `prompt_tokens`, `completion_tokens`, latency, and estimated USD cost across OpenAI, Gemini, Anthropic, and local modes.
- **Extended Evaluation Exports:** Wired `unsupported_claim_rate`, `evidence_coverage`, and `cost_metrics` through `eval/runner.py` and `eval/run_tracerecover.py` into both `comparison_summary.json` and `metrics_summary.csv`.

## Verification Commands and Results

```powershell
.\venv\Scripts\python.exe -m pytest -q
# Result: 122 passed in 11.57s (0 failed, 0 errors, 0 skipped)

.\venv\Scripts\python.exe -m eval.run_all --seed 42
# Output directory: C:\tracerecover\results\phase8-run-20261123T183333634001Z
# Generated: baseline_results.json, tracerecover_results.json, ablation_results.json,
#            comparison_summary.json, metrics_summary.csv, and 6 SVG plots.
```
