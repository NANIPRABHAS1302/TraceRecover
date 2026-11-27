# TraceRecover Connector & MCP Integration Guide

TraceRecover includes an extensible **Connect Hub** (`connectors/`) implementing the official Model Context Protocol (`mcp` Python SDK) alongside multi-browser automation, federated search, trust-tiered academic sources, and custom REST APIs.

## Architecture

- `connectors/mcp/client.py`: Official `mcp` SDK client supporting `stdio`, `streamable_http`, and `sse` transports, handshake (`initialize`), and discovery (`list_tools`, `list_resources`, `list_prompts`).
- `connectors/mcp/catalog.py`: Built-in catalog presets for Developer (GitHub, Google Developer Knowledge, Documentation Retrieval, Repo Inspection), Research (arXiv, Semantic Scholar, Crossref, OpenAlex), Productivity (Notion, Google Drive), Infrastructure (PostgreSQL, Redis, Docker, Observability), and Browser MCP (Playwright MCP, Chrome DevTools MCP).
- `connectors/sources/registry.py`: Trust Tiers 1–4 (`tier1` official, `tier2` academic, `tier3` reference/editorial, `tier4` open web), URL/title deduplication, weighted ranking, date extraction, citation formatting, and basic contradiction detection.
- `connectors/manager.py`: Central lifecycle manager (`add`, `edit`, `connect`, `disconnect`, `enable`, `disable`, `test`, `remove`), health checks, redacted audit logs, and dynamic registration on `tools/executor.py`.

## Dynamic Agent Integration

When **Use Connected Tools** (`use_connectors=True`) is enabled in **Query Workspace** (`11. Query Workspace`) or `/api/chat/query`:

1. Enabled and healthy connectors are registered on `ToolExecutor` (`mcp_*`, `connector_*`, `browser_inspect`, `research_sources`).
2. Every call flows through `PermissionEngine`, `FailureDetector`, `FailureLocalizer`, `RecoveryEngine`, `EvidenceChecker`, `UncertaintyEstimator`, `AbstentionPolicy`, and `TraceLogger`.
3. External outputs are strictly framed as `[UNTRUSTED_EXTERNAL_DATA]` rather than trusted instructions.

## Epistemic Caveat

Source Trust Tiers prioritize provenance only (`tier1 > tier2 > tier3 > tier4`) and **never guarantee factual truth** on their own; claims must be verified against evidence and cross-source corroboration.
