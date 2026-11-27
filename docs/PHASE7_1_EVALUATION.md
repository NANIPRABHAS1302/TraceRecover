# TraceRecover Phase 7.1 Evaluation Methodology & Results

## Evaluation Suite & Regimes

- **Dataset**: `data/phase7_1_unseen.json` — **70 unseen questions** across **16 domains** (`ml_research`, `nlp_evaluation`, `software_releases`, `distributed_systems`, `databases`, `cybersecurity`, `physics_constants`, `biomedical_research`, `cloud_architecture`, `economics_metrics`, `climate_energy`, `numerical_comparisons`, `browser_inspection`, `followup_context`, `adversarial_contradiction`, `insufficient_unanswerable_recovery`).
- **Evaluation Runner**: `eval/phase7_1_validation.py`
- **Canonical Output**: `results/phase7_1-validation-20261124T181638Z/`
  - `phase7_1_validation_report.json`
  - `phase7_1_validation_summary.md`

### Key Metric Refinement in Phase 7.1
In Phase 7 (`eval/phase7_validation.py`), abstained responses were included in the denominator/mean of `unsupported_claim_rate` and `evidence_support_rate`, artificially inflating offline `unsupported_claim_rate` to `0.6406` whenever the agent safely refused unanswerable queries. Phase 7.1 separates `answered_count`, `abstained_count`, and `warn_count`, computing `evidence_support_rate` and `unsupported_claim_rate` strictly across **answered** responses (`actual_decision != "ABSTAIN"`), while reporting `appropriate_abstention_rate`, `unnecessary_abstention_rate`, `contradiction_detection_precision`, `contradiction_detection_recall`, and `false_contradiction_rate` independently.

## Measured Four-Regime Summary

| Metric | Regime A: Offline (70q) | Regime B: Mock Connector (70q) | Regime C: Live Connector (10q) | Regime D: Live LLM |
|---|---:|---:|---:|---:|
| Answered / Abstained / Warn | 22 / 48 / 0 | 55 / 15 / 2 | 6 / 4 / 4 | BLOCKED BY MISSING CREDENTIALS |
| Overall Accuracy | 0.3857 | 0.8857 | 0.8000 | N/A |
| Answered Accuracy | 0.8636 | 0.9455 | 1.0000 | N/A |
| Evidence Support Rate (Answered) | 1.0000 | 1.0000 | 0.9176 | N/A |
| Unsupported Claim Rate (Answered) | 0.0000 | 0.0000 | 0.0824 | N/A |
| Citation Precision / Coverage | N/A | 1.0000 / 1.0000 | 1.0000 / 1.0000 | N/A |
| Contradiction Precision / Recall | N/A | 1.0000 / 1.0000 | 1.0000 / N/A | N/A |
| False Contradiction Rate | N/A | 0.0000 (0/65) | 0.0000 (0/10) | N/A |
| Appropriate Abstention Rate | 0.8000 | 1.0000 | 1.0000 | N/A |
| Unnecessary Abstention Rate | 0.6667 | 0.0833 | 0.2500 | N/A |
| Recovery Success Rate | 1.0000 | 1.0000 | 0.0000 | N/A |
| Avg Latency (ms) | 13.33 | 30.07 | 4266.08 | N/A |

### Regime C Live Keyless & Chromium Probes
- `research-arxiv`: `ok=True`, 5 papers returned (`873.52 ms`)
- `research-crossref`: `ok=True`, 4 works returned (`2186.86 ms`)
- `research-openalex`: `ok=True`, 5 works returned (`4866.12 ms`)
- `search-wikipedia`: `ok=True`, 4 articles returned (`1570.89 ms`)
- Playwright Chromium probe (`https://docs.python.org/3/whatsnew/3.12.html`): `ok=True`, `title="What’s New In Python 3.12 — Python 3.15.2 documentation"`, `excerpt_len=4000` (`3256.18 ms`).

### Phase 6 vs Phase 7 vs Phase 7.1 Comparison
*Note: Suites differ across phases (`52` vs `64` vs `70` questions), and Phase 7.1 excludes abstentions from `unsupported_claim_rate`.*

| Metric | Phase 6 Mock (52q) | Phase 7 Mock (64q) | Phase 7.1 Mock (70q) |
|---|---:|---:|---:|
| Correctness / Overall Accuracy | 0.8000 | 0.8833 | 0.8857 |
| Evidence Support Rate | 0.7067 | 0.8854 | 1.0000 |
| Unsupported Claim Rate | 0.2788 | 0.1146 | 0.0000 |
| Citation Precision | 1.0000 | 1.0000 | 1.0000 |
| Recovery Success Rate | 0.8000 | 1.0000 | 1.0000 |
