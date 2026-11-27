# TraceRecover — Phase 8.3 Deployment Validation Report

**Date:** 2026-11-25  
**Validation Scope:** Local Uvicorn server lifecycle, real server restart persistence, static asset delivery, operational health endpoint, and static/CLI container configuration validation.

---

## 1. Local Application Runtime Validation (Verified)

| Validation Step | Method | Result |
|---|---|---|
| Dependency integrity | `.\venv\Scripts\python.exe -m pip check` | PASS (`No broken requirements found.`) |
| Server startup & readiness | `uvicorn web_dashboard:app --host 127.0.0.1 --port <ephemeral>` | PASS (healthy on `/api/health` within < 2s) |
| Operational health check | `GET /api/health` | PASS (`HTTP 200`, `subsystems.storage.operational=true`, `subsystems.execution_modes.offline.operational=true`, `subsystems.execution_modes.live_llm.configured=false`, zero host paths) |
| Static SPA delivery | `GET /` (`static/index.html`) | PASS (`HTTP 200`, `Cache-Control: no-store`, `node --check` → `JS_OK`) |
| Real server restart & persistence | `tests/test_phase8_3_final_user_journey_e2e.py` (terminate & respawn Uvicorn mid-session) | PASS (2-turn conversation recovered via `#session=sess-...` URL hash & atomic JSON store; zero `localStorage`/`sessionStorage`) |
| Graceful shutdown | Process termination during E2E & standalone startup probe | PASS (exits cleanly within timeout, zero leftover `.tmp` files) |
| Live local dashboard daemon | `http://127.0.0.1:8000` | RUNNING |

---

## 2. Docker & Container Configuration Validation (Tasks B)

| Check | Command / Inspection | Result |
|---|---|---|
| Host Docker Engine status | `docker info --format "{{.ServerVersion}}"` | **UNAVAILABLE** (`//./pipe/dockerDesktopLinuxEngine` stopped on host) |
| `Dockerfile` source paths | Verified all 12 `COPY` targets exist on disk | **PASS** (`missing_copies = []`) |
| `Dockerfile` hardening | Non-root `tracerecover` user, `/var/data/*` dirs, `TRACERECOVER_ENABLE_DOCS=false`, `HEALTHCHECK` | **PASS** (static audit) |
| `docker-compose.yml` schema | `docker-compose config --quiet` | **PASS** (exit code `0`) |
| `docker-compose.yml` network bind | Loopback-only `127.0.0.1:8000:8000`, named volume `/var/data` | **PASS** (not publicly exposed) |
| `render.yaml` blueprint | 2 GB persistent disk, auto-generated `TRACERECOVER_ADMIN_TOKEN`, docs disabled | **PASS** (static audit) |
| Live container build / run | `docker compose build && docker compose up` | **NOT EXECUTED** (Docker Engine stopped; no build fabricated) |

### Commands to Execute Container Verification Once Docker Desktop Is Running

```powershell
# 1. Build and start the container (bound strictly to 127.0.0.1:8000)
docker compose up --build -d

# 2. Verify health endpoint and static dashboard
Invoke-RestMethod http://127.0.0.1:8000/api/health
(Invoke-WebRequest http://127.0.0.1:8000/).StatusCode

# 3. Inspect container logs for errors or secret leakage
docker compose logs --tail=100

# 4. Graceful shutdown (add -v only if you want to remove the persistent /var/data volume)
docker compose down
```

---

## 3. Local Capstone Demo vs. Public Multi-User Deployment Requirements

| Requirement | Local Capstone Demo (`127.0.0.1`) | Public Multi-User Deployment |
|---|---|---|
| `TRACERECOVER_ADMIN_TOKEN` | Optional (local open mode) | **Mandatory** (high-entropy secret) |
| `TRACERECOVER_VAULT_KEY` | Optional (`.vault.key` fallback) | **Mandatory** (external secret manager) |
| TLS / HTTPS | Local HTTP loopback | **Mandatory** (reverse proxy TLS + HSTS) |
| `TRACERECOVER_CORS_ORIGINS` | Default localhost origins | **Mandatory** exact HTTPS origin allowlist |
| `TRACERECOVER_ENABLE_DOCS` | `false` recommended | **Mandatory** `false` |
| Persistence & Rate Limiting | Single-node atomic JSON + in-memory limiter | Migrate to relational DB + Redis / edge rate limiter |
| User Authentication | Per-tab `#session=` namespace | Cryptographic user auth (OAuth / signed session cookies) |
