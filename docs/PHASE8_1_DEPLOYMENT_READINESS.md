# TraceRecover — Phase 8.1 Deployment Readiness Audit

**Audit Date:** 2026-11-25  
**Target:** Reproducible local capstone demo and containerized single-node deployment.

---

## 1. Verified Local Runtime Checks

Executed directly against `web_dashboard:app` via Uvicorn (`2026-11-25`):

| Check | Result | Measured Evidence |
|---|---|---|
| **Application Startup** | PASS | Uvicorn bound cleanly on ephemeral localhost port and became ready in < 2s. |
| **Health Endpoint (`/api/health`)** | PASS | Returned `HTTP 200`, `status: "ok"`, `storage.conversations_persistent: true`, `storage.conversations_backend: "filesystem_json_atomic"`, `security.server_side_credentials_only: true`, `security.request_max_bytes: 65536`. |
| **Static SPA Serving (`/`)** | PASS | Returned `HTTP 200`, `227,839` bytes (`static/index.html`) with `Cache-Control: no-store`. |
| **Graceful Shutdown** | PASS | Clean process termination within 5s timeout. |
| **Dependency Integrity (`pip check`)** | PASS | `No broken requirements found.` |
| **Frontend JS Syntax (`node --check`)** | PASS | `JS_OK` |
| **Full Automated Test Suite** | PASS | `220 passed, 0 failed, 0 skipped` (including 9 Playwright Chromium E2E tests). |

---

## 2. Deployment Configuration Inventory

| Artifact | Purpose | Hardening Status |
|---|---|---|
| `Dockerfile` | Python `3.12-slim` image, non-root `tracerecover` user, persistent `/var/data/*` dirs, `TRACERECOVER_ENABLE_DOCS=false`, `HEALTHCHECK` against `/api/health`, `uvicorn --proxy-headers`. | Verified statically. |
| `docker-compose.yml` | Local single-node orchestration with named `tracerecover_data` volume mounted at `/var/data`, **loopback-only** port bind (`127.0.0.1:8000:8000`), and container healthcheck. | Added in Phase 8.1. |
| `render.yaml` | PaaS Blueprint with 2 GB persistent disk at `/var/data`, `healthCheckPath: /api/health`, `TRACERECOVER_ENABLE_DOCS: "false"`, and auto-generated `TRACERECOVER_ADMIN_TOKEN`. | Verified statically. |
| `.env.example` | Safe placeholder template covering LLM providers, local model servers, admin token, CORS origins, proxy trust, storage dirs, and rate/size/retention limits. | Updated in Phase 8.1; zero secrets. |
| `.gitignore` / `.dockerignore` | Excludes `.env`, `.env.*`, `venv/`, `logs/`, `data/connectors/`, `data/versions/`, `data/unseen_benchmarks/`, and caches. | Verified (`git check-ignore .env == 0`). |

---

## 3. Container Build Status (Honest Disclosure)

- `docker.exe` and `docker-compose.exe` are installed on the host, **but the Docker Desktop daemon (`//./pipe/docker_engine`) was not running during Phase 8.1**.
- Therefore, a live `docker build` / `docker compose up` smoke test was **not executed** and no live container deployment is claimed.
- To verify containerization once Docker Desktop is started:
  ```powershell
  docker compose build
  docker compose up -d
  Invoke-RestMethod http://127.0.0.1:8000/api/health
  docker compose down
  ```

---

## 4. Mandatory Pre-Public-Deployment Checklist

Do **not** expose TraceRecover directly to the public internet without completing every step below:

1. **Set `TRACERECOVER_ADMIN_TOKEN`** to a high-entropy secret (`>= 32` random bytes) to protect experiment execution, dataset import, unseen evaluation, and all connector/browser mutations.
2. **Set `TRACERECOVER_VAULT_KEY`** via your platform secret manager so encrypted connector credentials do not rely on a local `.vault.key` file.
3. **Restrict `TRACERECOVER_CORS_ORIGINS`** to your exact HTTPS origin(s); never use `*`.
4. **Terminate TLS (HTTPS)** at a reverse proxy (Nginx / Caddy / Cloudflare / Render edge) and set `TRACERECOVER_TRUST_PROXY=true` only when behind a trusted proxy that overwrites `X-Forwarded-For` and `X-Forwarded-Proto`.
5. **Keep `TRACERECOVER_ENABLE_DOCS=false`** in production.
6. **Mount persistent storage** at `/var/data` (or configure `TRACERECOVER_*_DIR` variables) with regular backups so traces, conversations, and connector state persist across redeploys.
7. **Configure external rate limiting** at the reverse proxy if running multiple Uvicorn workers.

