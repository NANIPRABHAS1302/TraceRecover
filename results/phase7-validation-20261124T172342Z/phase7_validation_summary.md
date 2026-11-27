# TraceRecover Phase 7 vs Phase 6 Evaluation Summary
- Generated: `2026-11-24T17:23:47.299929+00:00`
- Unseen Suite: `64` questions across `16` domains (`data/phase7_unseen_60.json`)

| Metric | Phase 6 Offline (52q) | Phase 7 Offline (64q) | Phase 6 Mock (52q) | Phase 7 Mock (64q) |
|---|---:|---:|---:|---:|
| Answer Correctness | 0.35 | 0.35 | 0.8 | 0.8833 |
| Evidence Support Rate | 0.8317 | 0.3594 | 0.7067 | 0.8854 |
| Citation Precision | 1.0 | 1.0 | 1.0 | 1.0 |
| Unsupported Claim Rate | 0.1538 | 0.6406 | 0.2788 | 0.1146 |
| Abstention Precision | 0.2973 | 0.0732 | 0.5789 | 0.4444 |
| Incorrect Abstention Rate | 0.65 | 0.6333 | 0.2 | 0.0833 |
| Research Planning Quality | N/A | 0.3438 | N/A | 0.9844 |
| Tool Selection Accuracy | 0.9231 | 0.9688 | 0.9231 | 0.9844 |
| Recovery Success Rate | 0.8 | 1.0 | 0.8 | 1.0 |
| Avg Latency (ms) | 4.24 | 5.09 | 6.24 | 8.69 |
