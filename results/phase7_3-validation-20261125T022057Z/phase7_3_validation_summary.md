# TraceRecover Phase 7.3 Evaluation & Integration Summary
- Generated: `2026-11-25T02:22:27.544819+00:00`
- Overall Status: `PARTIALLY_COMPLETE_BLOCKED_ON_LIVE_LLM_CREDENTIALS`
- Dataset Preserved: `data/phase7_2_live_unseen.json` (`48` questions across 14 domains)

## Task 8 — Four Separate Regimes (Never Merged)

| Metric | Regime A: OFFLINE (48q) | Regime B: MOCK (48q) | Regime C: LIVE CONNECTOR (12q) | Regime D: LIVE LLM + CONNECTOR |
|---|---:|---:|---:|---:|
| Attempted / Answered / Abstained / Failed / Blocked | 48 / 10 / 38 / 0 / 0 | 48 / 34 / 14 / 0 / 0 | 12 / 6 / 6 / 0 / 0 | 0 / 0 / 0 / 0 / 48 (BLOCKED) |
| Overall Accuracy | 0.4167 | 0.8958 | 0.75 | N/A |
| Answered Accuracy | 1.0 | 0.9706 | 1.0 | N/A |
| Evidence Support Rate (Answered) | 0.9 | 0.9706 | 0.9378 | N/A |
| Unsupported Claim Rate (Answered) | 0.1 | 0.0294 | 0.0622 | N/A |
| Citation Precision / Coverage | None / None | 1.0 / 1.0 | 1.0 / 1.0 | N/A |
| Contradiction Precision / Recall | N/A | 0.625 / 1.0 | 1.0 / None | N/A |
| Recovery Success Rate | 1.0 | 1.0 | 1.0 | N/A |
| Avg Latency (ms) | 12.18 | 35.3 | 4357.85 | N/A |

## Task 4 — LLM + Live Connector Integration Proof (`MOCK_LLM + LIVE_CONNECTOR`)
- Planner called model: `True`
- Live connectors executed: `['research_sources']`
- Live external sources retrieved: `14`
- Untrusted evidence fencing verified: `True`
- Live evidence reached model step 2: `True`
- Post-synthesis verifier caught injected hallucination: `True`

## Task 5 — Controlled Failure Validation (`9 / 9` Scenarios)

| Scenario | Label | Passed | Category |
|---|---|---|---|
| 1. Invalid API key | `OFFLINE` | `True` | `invalid_api_key` |
| 2. Provider timeout | `MOCK` | `True` | `timeout` |
| 3. Provider rate limit | `MOCK` | `True` | `rate_limited` |
| 4. Malformed response (bounded retry) | `MOCK` | `True` | `malformed_output` |
| 5. Empty response | `MOCK` | `True` | `empty_response` |
| 6. Connector unavailable | `MOCK` | `True` | `tool_failure` |
| 7. Insufficient evidence | `MOCK` | `True` | `insufficient_evidence` |
| 8. Contradictory evidence | `MOCK` | `True` | `contradictory_evidence` |
| 9. Budget exhaustion | `MOCK` | `True` | `budget_exceeded` |
