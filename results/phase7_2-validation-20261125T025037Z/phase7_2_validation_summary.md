# TraceRecover Phase 7.2 Four-Mode Evaluation & Failure Validation
- Generated: `2026-11-25T02:51:25.428079+00:00`
- Dataset: `data/phase7_2_live_unseen.json` (`48` questions across `14` domains)

## Separate Mode Results (Never Merged)

| Metric | Mode A: OFFLINE (48q) | Mode B: MOCK (48q) | Mode C: LIVE CONNECTOR (12q) | Mode D: LIVE CONNECTOR + LIVE LLM |
|---|---:|---:|---:|---:|
| Answered / Abstained / Warn / Failed / Blocked | 10 / 38 / 0 / 0 / 0 | 37 / 11 / 3 / 0 / 0 | 6 / 6 / 3 / 0 / 0 | BLOCKED BY MISSING CREDENTIALS |
| Overall Accuracy | 0.4167 | 0.9583 | 0.75 | N/A |
| Answered Accuracy | 1.0 | 0.973 | 1.0 | N/A |
| Evidence Support Rate (Answered) | 0.9 | 0.973 | 0.9491 | N/A |
| Unsupported Claim Rate (Answered) | 0.1 | 0.027 | 0.0508 | N/A |
| Citation Precision / Coverage | None / None | 1.0 / 1.0 | 1.0 / 1.0 | N/A |
| Contradiction Precision / Recall | N/A | 1.0 / 1.0 | 1.0 / None | N/A |
| Appropriate / Incorrect Abstention | 1.0 / 0.7368 | 1.0 / 0.0263 | 1.0 / 0.3333 | N/A |
| Tool Selection / Planning Quality | 1.0 / 0.9792 | 0.9583 / 0.9792 | 1.0 / 0.9167 | N/A |
| Recovery Success Rate | 1.0 | 1.0 | 1.0 | N/A |
| Avg Latency (ms) | 10.35 | 31.24 | 3503.96 | N/A |
| Tokens (In/Out/Total) & Est. Cost | 0 ($0.0) | 0 ($0.0) | 0 ($0.0) | N/A |

## Task E Failure & Recovery Matrix

| Scenario | Label | Passed | Category / Details |
|---|---|---|---|
| 1. Invalid API key | `OFFLINE` | `True` | `invalid_api_key` |
| 2. Provider timeout | `MOCK` | `True` | `timeout` |
| 3. Rate limit (429) | `MOCK` | `True` | `rate_limited` |
| 4. Unreachable provider | `OFFLINE` | `True` | `timeout` |
| 5. Empty model response | `MOCK` | `True` | `empty_response` |
| 6. Malformed structured output (retry exhausted) | `MOCK` | `True` | `malformed_output` |
| 7. Missing research evidence | `MOCK` | `True` | `insufficient_evidence` |
| 8. Contradictory source evidence | `MOCK` | `True` | `contradictory_evidence` |
| 9. Multi-step fault & recovery | `OFFLINE` | `True` | `1` |
| 10. LLM budget exhaustion | `MOCK` | `True` | `budget_exceeded` |
