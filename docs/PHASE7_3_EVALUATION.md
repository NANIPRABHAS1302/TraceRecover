# TraceRecover Phase 7.3 Four-Regime Evaluation & Failure Matrix

- **Run Artifact**: `results/phase7_3-validation-20261125T022057Z/phase7_3_validation_report.json`
- **Summary**: `results/phase7_3-validation-20261125T022057Z/phase7_3_validation_summary.md`
- **Benchmark Dataset**: `data/phase7_2_live_unseen.json` (`48` questions across `14` domains; canonical `eval/tasks.json` and earlier datasets untouched)

## Separate Regime Breakdown (Never Merged)

| Metric | Regime A: `OFFLINE` (48q) | Regime B: `MOCK` (48q) | Regime C: `LIVE CONNECTOR` (12q) | Regime D: `LIVE LLM + CONNECTOR` |
|---|---:|---:|---:|---:|
| Attempted | 48 | 48 | 12 | 0 |
| Answered | 10 | 34 | 6 | 0 |
| Abstained | 38 | 14 | 6 | 0 |
| Failed | 0 | 0 | 0 | 0 |
| Blocked | 0 | 0 | 0 | 48 (`BLOCKED`) |
| Overall Accuracy | `0.4167` | `0.8958` | `0.7500` | N/A |
| Answered Accuracy | `1.0000` | `0.9706` | `1.0000` | N/A |
| Evidence Support Rate (Answered) | `0.9000` | `0.9706` | `0.9378` | N/A |
| Unsupported Claim Rate (Answered) | `0.1000` | `0.0294` | `0.0622` | N/A |
| Citation Precision / Coverage | `None` / `None` | `1.0000` / `1.0000` | `1.0000` / `1.0000` | N/A |
| Contradiction Precision / Recall | N/A | `0.6250` / `1.0000` | `1.0000` / N/A | N/A |
| Recovery Success Rate | `1.0000` | `1.0000` | `1.0000` | N/A |
| Avg Latency (ms) | `12.18` | `35.30` | `4357.85` | N/A |
| Provider Tokens (Live LLM) | `0` (`none`) | `0` (`none`) | `0` (`none`) | `BLOCKED` |

- **Live Chromium Documentation Probe (`LIVE CONNECTOR`)**: `https://docs.python.org/3/library/dataclasses.html` -> `ok=True`, `title="dataclasses — Data Classes — Python 3.15.2 documentation"`, `excerpt_len=4000`, `2993.33 ms`.

## Task 5 — Controlled Failure Validation (`9 / 9` Passed)

1. Invalid API key (`OFFLINE`) -> `invalid_api_key` (`PASSED`)
2. Provider timeout (`MOCK`) -> `timeout` (`PASSED`)
3. Provider rate limit (`MOCK`) -> `rate_limited` (`PASSED`)
4. Malformed response with bounded 1-retry repair (`MOCK`) -> `malformed_output`, `calls=2` (`PASSED`)
5. Empty response (`MOCK`) -> `empty_response` (`PASSED`)
6. Connector unavailable (`MOCK`) -> `tool_failure` abstention, zero fabricated citations (`PASSED`)
7. Insufficient evidence (`MOCK`) -> `insufficient_evidence` abstention (`PASSED`)
8. Contradictory evidence (`MOCK`) -> `contradictory_evidence` abstention (`PASSED`)
9. Budget exhaustion (`MOCK`) -> `budget_exceeded` (`PASSED`)
