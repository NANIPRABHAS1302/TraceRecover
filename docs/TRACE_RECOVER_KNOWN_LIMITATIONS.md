# TraceRecover — Known Limitations & Scope Boundaries

This document provides an honest engineering disclosure of TraceRecover's current technical, operational, and evaluation limitations for academic review and future deployment planning.

---

## 1. Live LLM Activation Status (Intentionally Deferred)

- **No external LLM API keys or local model servers** (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`, `LOCAL_LLM_BASE_URL`) are configured in the workspace.
- Genuine live LLM end-to-end research execution (`Regime D`) and live LLM vs. live-connector retrieval comparisons remain **deferred / BLOCKED** until valid credentials or a local Ollama server are provided.
- All automated tests (`226/226`) and demonstrations run in **OFFLINE** and **MOCK** modes with zero fabricated API calls, token counts, or billing estimates.

---

## 2. Deployment & Scaling Limitations

- **Docker container build unexecuted locally:** `Dockerfile` copy targets and `docker-compose.yml` syntax (`docker-compose config --quiet`) were validated, but Docker Engine was stopped on the host during Phase 8.1–8.3; a live `docker compose build` / container smoke test must be run once Docker Desktop is started.
- **Single-node filesystem persistence:** `ConversationStore` uses atomic per-session JSON files (`os.replace()`) protected by an in-process `threading.Lock`, and backend caches live in process memory. This is reliable for single-worker local demos, but **not** safe for multi-worker or multi-replica deployments without migrating to a shared database (e.g., PostgreSQL) and distributed locking.
- **Single-process in-memory rate limiting:** `_SlidingWindowRateLimiter` tracks per-IP hit windows inside one Python process; multi-worker deployments must enforce rate limiting at a reverse proxy or Redis store.

---

## 3. Security & Multi-Tenancy Boundaries

- **Local open mode by default:** When `TRACERECOVER_ADMIN_TOKEN` is unset, administrative endpoints (`/api/experiments/run`, `/api/datasets/import`, `/api/unseen/run`, and `/api/connectors/*` mutations) run without authentication for local convenience. Public exposure **requires** setting `TRACERECOVER_ADMIN_TOKEN`, `TRACERECOVER_VAULT_KEY`, HTTPS/TLS termination, `TRACERECOVER_ENABLE_DOCS=false`, and exact `TRACERECOVER_CORS_ORIGINS`.
- **Unauthenticated session namespaces:** Browser sessions use a client-held `#session=sess-...` URL hash sent via `X-Session-ID` (preserving zero `localStorage`/`sessionStorage` usage). This cleanly separates local demo workspaces, but is **not** cryptographic multi-tenant user authentication.
- **Local vault keyfile fallback:** When `TRACERECOVER_VAULT_KEY` is unset, `SecretVault` generates a local `.vault.key` beside `vault.enc.json`; production deployments must inject `TRACERECOVER_VAULT_KEY` via a secret manager.
- **CSP `'unsafe-inline'`:** Because `static/index.html` is a zero-build single-file SPA, `Content-Security-Policy` allows `'unsafe-inline'` for scripts and styles.
- **No formal security certification:** Security hardening is verified by unit and adversarial tests, not by an independent external penetration test or compliance audit.

---

## 4. Verification, Retrieval & Observability Boundaries

- **Offline heuristic verification:** In OFFLINE mode without an active NLI transformer or remote LLM entailer, complex paraphrases may be conservatively marked `Partially supported` or `Unsupported` (prioritizing precision over overclaiming).
- **Live connector-only baseline ceiling:** Without a live LLM for query reformulation and deep-page extraction, deterministic live-connector accuracy on the 22-question Phase 7.4 live-probe slice measured `36.36%` (`8/22`, with `98.61%` evidence support), whereas mock-grounded accuracy reached `95.65%` post-fix on the 92-question frozen holdout (post-fix diagnostic, not blind).
- **Local observability only:** Structured JSON logs (`tracerecover.observability`), stage timings, and JSONL traces operate locally; no OpenTelemetry or distributed tracing collector is configured.
