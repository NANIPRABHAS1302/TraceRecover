# TraceRecover Phase 7.1 Four-Regime Evaluation & Comparison
- Generated: `2026-11-24T18:17:37.194363+00:00`
- Phase 7.1 Unseen Suite: `70` questions across `16` domains (`data/phase7_1_unseen.json`)
- Note: Phase 6 (`52q`), Phase 7 (`64q`), and Phase 7.1 (`70q`) use distinct evaluation suites; Phase 7.1 excludes abstentions from `unsupported_claim_rate` and `evidence_support_rate`.

## Four-Regime Phase 7.1 Results

| Metric | Regime A: Offline (70q) | Regime B: Mock Connector (70q) | Regime C: Live Connector (10q) | Regime D: Live LLM |
|---|---:|---:|---:|---:|
| Answered / Abstained / Warn | 22 / 48 / 0 | 55 / 15 / 2 | 6 / 4 / 4 | BLOCKED BY MISSING CREDENTIALS |
| Overall Accuracy | 0.3857 | 0.8857 | 0.8 | N/A |
| Answered Accuracy | 0.8636 | 0.9455 | 1.0 | N/A |
| Evidence Support Rate (Answered) | 1.0 | 1.0 | 0.9176 | N/A |
| Unsupported Claim Rate (Answered) | 0.0 | 0.0 | 0.0824 | N/A |
| Citation Precision / Coverage | None / None | 1.0 / 1.0 | 1.0 / 1.0 | N/A |
| Contradiction Precision / Recall | N/A | 1.0 / 1.0 | 1.0 / None | N/A |
| False Contradiction Rate | N/A | 0.0 | 0.0 | N/A |
| Appropriate Abstention Rate | 0.8 | 1.0 | 1.0 | N/A |
| Unnecessary Abstention Rate | 0.6667 | 0.0833 | 0.25 | N/A |
| Recovery Success Rate | 1.0 | 1.0 | 0.0 | N/A |
| Avg Latency (ms) | 13.33 | 30.07 | 4266.08 | N/A |

## Phase 6 vs Phase 7 vs Phase 7.1 Comparison (Mock Connector & Offline)

| Metric | Phase 6 Mock (52q) | Phase 7 Mock (64q) | Phase 7.1 Mock (70q) | Phase 6 Offline (52q) | Phase 7 Offline (64q) | Phase 7.1 Offline (70q) |
|---|---:|---:|---:|---:|---:|---:|
| Correctness / Overall Acc | 0.8 | 0.8833 | 0.8857 | 0.35 | 0.35 | 0.3857 |
| Evidence Support Rate | 0.7067 | 0.8854 | 1.0 | 0.8317 | 0.3594 | 1.0 |
| Unsupported Claim Rate | 0.2788 | 0.1146 | 0.0 | 0.1538 | 0.6406 | 0.0 |
| Citation Precision | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | None |
| Recovery Success Rate | 0.8 | 1.0 | 1.0 | 0.8 | 1.0 | 1.0 |
