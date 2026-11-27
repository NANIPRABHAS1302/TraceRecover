# TraceRecover Phase 7.2 Evaluation Methodology & Measured Results

## Dataset & Execution Regimes

- **Dataset**: `data/phase7_2_live_unseen.json` — **48 unseen questions** across **14 domains** (`academic_research`, `artificial_intelligence`, `machine_learning`, `software_engineering`, `programming_documentation`, `mathematics`, `science`, `cybersecurity`, `technology_comparisons`, `numerical_reasoning`, `multi_hop_reasoning`, `contradictory_evidence`, `insufficient_evidence`, `fault_recovery`), distinct from Phase 7.1 (`data/phase7_1_unseen.json`).
- **Category Coverage Verified**: `15` multi-step, `17` multi-source comparison, `8` technical docs, `7` numerical/temporal reasoning, `5` contradiction scenarios, `5` insufficient-evidence/security scenarios, `2` fault recovery scenarios.
- **Canonical Output**: `results/phase7_2-validation-20261124T183941Z/`

## Four-Mode Separate Comparison (Never Merged)

| Metric | Mode A: OFFLINE (48q) | Mode B: MOCK (48q) | Mode C: LIVE CONNECTOR (12q) | Mode D: LIVE CONNECTOR + LIVE LLM |
|---|---:|---:|---:|---:|
| Answered / Abstained / Warn / Failed / Blocked | 10 / 38 / 0 / 0 / 0 | 34 / 14 / 0 / 0 / 0 | 7 / 5 / 4 / 0 / 0 | BLOCKED BY MISSING CREDENTIALS |
| Overall Accuracy | 0.4167 | 0.8958 | 0.8333 | N/A |
| Answered Accuracy | 1.0000 | 0.9706 | 1.0000 | N/A |
| Evidence Support Rate (Answered) | 0.9000 | 0.9706 | 0.9365 | N/A |
| Unsupported Claim Rate (Answered) | 0.1000 | 0.0294 | 0.0635 | N/A |
| Citation Precision / Coverage | None / None | 1.0000 / 1.0000 | 1.0000 / 1.0000 | N/A |
| Contradiction Precision / Recall | N/A | 0.6250 / 1.0000 | 1.0000 / N/A | N/A |
| False Contradiction Count | N/A | 3 / 43 (`fp=3, tp=5, tn=40, fn=0`) | 0 / 12 (`tp=0, fp=0, tn=12, fn=0`) | N/A |
| Appropriate / Incorrect Abstention | 1.0000 / 0.7368 | 1.0000 / 0.1053 | 1.0000 / 0.2222 | N/A |
| Tool Selection / Planning Quality | 1.0000 / 0.9792 | 0.9583 / 0.9792 | 1.0000 / 0.9167 | N/A |
| Recovery Success Rate | 1.0000 | 1.0000 | 1.0000 | N/A |
| Avg Latency (ms) | 4.34 | 14.16 | 4309.55 | N/A |

- **Live Chromium Documentation Inspection (`LIVE`)**: `https://docs.python.org/3/library/dataclasses.html` returned `ok=True`, `title="dataclasses — Data Classes — Python 3.15.2 documentation"`, `excerpt_len=4000` in `1744.75 ms`.
- **Mode D Honest Block Status (`BLOCKED`)**: `select_preferred_live_provider()` reported `live_available=False`; `LLMProviderAdapter(provider="gemini").health_check()` reported `connection_status="unconfigured"`, `error_category="missing_credentials"`. Zero live LLM calls or fabricated numbers were recorded.

## Task E Failure & Recovery Matrix (`10 / 10` Passed)

All 10 failure scenarios executed and passed (`eval/phase7_2_validation.py`):
- `OFFLINE`: Invalid API key (`invalid_api_key`), Unreachable local server (`timeout`), Multi-step tool timeout recovery (`recovered=1`).
- `MOCK`: Provider timeout (`timeout`), Rate limit (`rate_limited`), Empty response (`empty_response`), Malformed structured JSON after bounded retry (`malformed_output`, `calls=2`), Missing research evidence (`insufficient_evidence`), Contradictory source evidence (`contradictory_evidence`), LLM budget exhaustion (`budget_exceeded`).
