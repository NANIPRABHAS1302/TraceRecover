# TraceRecover — Phase 8.1 Application Security Audit

**Audit Date:** 2026-11-25  
**Scope:** FastAPI backend (`web_dashboard.py`), conversation persistence (`agent/conversation_store.py`), provider adapter (`agent/provider_adapter.py`), connector security/vault/browser (`connectors/`), frontend SPA (`static/index.html`), and deployment configuration.  
**Disclaimer:** This is an engineering security audit documenting verified controls and honest limitations; it is **not** a formal third-party security certification.

---

## 1. Verified Security Controls

| Control Area | Implementation | Verified Behavior |
|---|---|---|
| **Server-side Credentials Only** | `config.py`, `agent/provider_adapter.py`, `connectors/permissions.py` | Provider API keys live solely in server environment / git-ignored `.env`. Connector credentials are encrypted at rest via `Fernet` (`AES-128-CBC + HMAC-SHA256`) in `SecretVault`. Zero secret literals exist in `static/index.html`. |
| **Secret Redaction** | `agent/provider_adapter.py`, `agent/conversation_store.py`, `connectors/permissions.py` | Redacts `AIza...`, `sk-...`, `sk-ant-...`, `ghp_...`, `github_pat_...`, `tvly-...`, `Bearer ...`, `x-goog-api-key`, `x-api-key`, `authorization`, `api_key=`, `access_token=`, and DB URI passwords before logging, returning errors, or persisting conversation JSON. |
| **Admin Authentication** | `_require_admin_if_configured` (`hmac.compare_digest`) in `web_dashboard.py` | Protects `/api/experiments/run`, `/api/datasets/import`, `/api/unseen/run`, `/api/connectors/browser/action`, and all mutating `/api/connectors/*` routes (`POST`/`PUT`/`DELETE`) via `Bearer` or `X-API-Key` when `TRACERECOVER_ADMIN_TOKEN` is configured. |
| **Session Isolation** | `ConversationStore` (`agent/conversation_store.py`) | Isolates conversations per sanitized `X-Session-ID` directory, verifies `record["session_id"] == safe_session` on read/clear/delete/append, and enforces `relative_to()` path-traversal guards. |
| **CORS Policy** | `CORSMiddleware` (`web_dashboard.py`) | Restricted to `TRACERECOVER_CORS_ORIGINS` (default localhost origins, `allow_credentials=False`), explicit methods (`GET, POST, PUT, DELETE, OPTIONS`) and headers (`Content-Type, Authorization, X-API-Key, X-Session-ID`). |
| **Request Size Cap** | `security_and_limits_middleware` (`web_dashboard.py`) | Enforces `TRACERECOVER_MAX_REQUEST_BYTES` (default 64 KB) on explicit `Content-Length` headers **and** chunked/headerless `POST`/`PUT`/`PATCH` bodies (`HTTP 413`), rejecting non-numeric `Content-Length` (`HTTP 400`). |
| **Rate Limiting** | `_SlidingWindowRateLimiter` (`web_dashboard.py`) + `PermissionEngine` (`connectors/permissions.py`) | Enforces per-IP sliding-window limits on query execution, experiments, dataset validation/import, conversation mutations, query cancellation, provider health probes, and connector actions (`HTTP 429` + `Retry-After`). |
| **SSRF & Browser Safety** | `PermissionEngine.check_url()`, `BrowserIntelligenceManager`, `CustomAPIConnector` | Blocks non-HTTP(S) schemes, embedded URL credentials, cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`, `metadata.azure.com`), private/loopback/link-local/obfuscated IPs, DNS rebinding into private space, subrequest/redirect policy violations, and unapproved file downloads/form submissions. |
| **MCP Tool Permissions & Framing** | `PermissionEngine.check_execution()`, `frame_untrusted_output()` | Enforces connector enabled flag, `denied_tools`, `allowed_tools`, `read_only` approval gates, and wraps external outputs in `<UNTRUSTED_EVIDENCE_DATA>` / `[UNTRUSTED_EXTERNAL_DATA]` framing with prompt-injection pattern filtering. |
| **Dataset Import Validation** | `_validate_dataset_payload()` (`web_dashboard.py`) | Validates uploaded JSON tasks against canonical schema (`eval.runner._validate_task`), writes only to versioned `data/versions/` (never overwriting `eval/tasks.json`), and requires admin auth + rate limiting. |
| **Defensive Headers & Safe Errors** | `security_and_limits_middleware`, `sanitized_exception_handler` | Sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, `Content-Security-Policy`, and conditional HSTS over HTTPS; generic HTTP 500 JSON prevents Python traceback or filesystem path leakage. |

---

## 2. Phase 8.1 Security Defects Fixed

1. **Chunked/Headerless Body Size Bypass (Fixed):** Previously only `Content-Length` was checked; `security_and_limits_middleware` now inspects actual body length for `POST`/`PUT`/`PATCH` when `Content-Length` is omitted and rejects malformed headers with `400`.
2. **Cross-Request Browser Allowlist Pollution (Fixed):** `api_connectors_browser_action` previously mutated `mgr.registry.get(...)` in place when `payload.allowed_domains` was provided; it now uses `copy.deepcopy(base_cfg)` per request.
3. **CORS Mismatch on Session & Mutation Endpoints (Fixed):** Added `PUT`, `DELETE`, and `X-Session-ID` to explicit CORS allowlists without relaxing origin restrictions.
4. **Unthrottled Conversation & Cancel Endpoints (Fixed):** Applied `_enforce_rate_limit` to conversation create/clear/delete, query cancellation, and dataset validation.
5. **Conversation Disk Secret Scrubbing (Expanded):** Added `x-goog-api-key`, `x-api-key`, `authorization`, and `api_key=` / `access_token=` redaction patterns to `agent/conversation_store.py`.

---

## 3. Remaining Security Limitations (Honest Disclosure)

1. **Local Open Mode when `TRACERECOVER_ADMIN_TOKEN` is unset:** Intended strictly for local single-user development/demos. Any shared or internet-facing deployment **must** set a high-entropy `TRACERECOVER_ADMIN_TOKEN` and `TRACERECOVER_VAULT_KEY`.
2. **Client-supplied `X-Session-ID`:** Session isolation partitions workspaces cleanly for demo users, but is not cryptographic user authentication; multi-tenant production use requires authenticated user sessions (e.g., signed cookies/JWTs).
3. **Local keyfile fallback in `SecretVault`:** When `TRACERECOVER_VAULT_KEY` is unset, `SecretVault` stores `.vault.key` beside `vault.enc.json` (`security_posture()` flags `production_ready_key_management: false`). Production deployments must inject `TRACERECOVER_VAULT_KEY` via a secret manager.
4. **CSP `'unsafe-inline'`:** Required by the single-file `static/index.html` SPA; migrating scripts/styles to external static bundles with nonces would eliminate `'unsafe-inline'`.
5. **In-memory single-process rate limiting:** Does not coordinate across multiple Uvicorn workers or containers; place behind an edge/reverse-proxy rate limiter in multi-worker production setups.

