# TraceRecover Phase 6 Validation Summary
- Generated: `2026-11-24T17:06:19.737696+00:00`
- Unseen Benchmark: `52` questions (`data/phase6_unseen_50.json`)

## Stage 2 Connector Probes
| Connector | Transport | Status | Handshake | Read OK | Tools | Latency (ms) |
|---|---|---|---|---|---|---:|
| GitHub MCP | `stdio` | `auth_required` | False | False | 0 | - |
| Google Developer Knowledge MCP | `streamable_http` | `auth_required` | False | False | 0 | - |
| Playwright MCP | `stdio` | `connected` | True | False | 25 | 1383.3 |
| arXiv | `rest` | `unavailable` | False | False | 0 | 8400.52 |
| Semantic Scholar | `rest` | `error` | False | False | 1 | 1758.98 |
| Crossref | `rest` | `connected` | True | True | 1 | 2227.6 |
| OpenAlex | `rest` | `connected` | True | True | 1 | 1196.69 |
| Documentation Retrieval | `streamable_http` | `error` | False | False | 0 | - |
| Wikipedia Reference | `rest` | `connected` | True | True | 1 | 962.76 |
| DuckDuckGo Instant Answer | `rest` | `connected` | True | True | 1 | 828.37 |
| Local Official MCP Stdio Fixture | `stdio` | `connected` | True | True | 3 | 982.66 |

## Stage 7 Unseen Benchmark Regimes
- **Offline Deterministic Local**: answer_correctness=`0.35`, tool_selection_accuracy=`0.9231`, abstention_precision=`0.2973`, avg_latency_ms=`4.24`
- **Mock Connector Controlled**: answer_correctness=`0.8`, evidence_support_rate=`0.7067`, citation_accuracy=`1.0`, unsupported_claim_rate=`0.2788`, recovery_success_rate=`0.8`, abstention_precision=`0.5789`, incorrect_abstention_rate=`0.2`, tool_selection_accuracy=`0.9231`
- **Live External Connectors**: `Active local settings provider=gemini; GEMINI/OPENAI/ANTHROPIC API keys are not set, so live remote LLM answers are reported as UNCONFIGURED rather than fabricated.`
