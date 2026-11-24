# TraceRecover Production Deployment Guide

This guide covers deploying the TraceRecover web dashboard and FastAPI backend safely in production using Docker, persistent storage, HTTPS, and server-side secret management.

## 1. Security Checklist Before Public Exposure

1. **Never commit secrets:** Keep `.env` untracked (already in `.gitignore`). Copy `.env.example` only as a reference.
2. **Protect compute-heavy experiment endpoints:** Set `TRACERECOVER_ADMIN_TOKEN` to a strong random secret (e.g. 32+ hex bytes) so `POST /api/experiments/run` requires `Authorization: Bearer <token>` or `X-API-Key: <token>`.
3. **Restrict CORS origins:** Set `TRACERECOVER_CORS_ORIGINS` to your exact HTTPS public URL (e.g. `https://tracerecover.onrender.com`).
4. **Disable interactive API docs if not needed publicly:** Keep `TRACERECOVER_ENABLE_DOCS=false` in production (already default in `Dockerfile` and `render.yaml`).
5. **Keep provider keys server-side only:** Set `GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY` only in the hosting platform's secret manager if remote LLM calls are enabled; use `LLM_PROVIDER=local` for zero-cost deterministic evaluation.

## 2. Local Production Run (Without Reload)

```powershell
.\venv\Scripts\python.exe -m uvicorn web_dashboard:app --host 127.0.0.1 --port 8000 --proxy-headers
```

Open `http://127.0.0.1:8000`. Health check: `GET /api/health`.

## 3. Docker Container Build & Run (With Persistent Volume)

```bash
# Build production image
docker build -t tracerecover:latest .

# Create a persistent Docker volume for results and execution logs
docker volume create tracerecover_data

# Seed the flagship benchmark results into the persistent volume (first run)
docker run --rm -v tracerecover_data:/var/data tracerecover:latest \
  sh -c "mkdir -p /var/data/results /var/data/logs && cp -rn /app/results/. /var/data/results/"

# Run production server (non-root user, no reload)
docker run -d --name tracerecover \
  -p 8000:8000 \
  -v tracerecover_data:/var/data \
  -e LLM_PROVIDER=local \
  -e TRACERECOVER_RESULTS_DIR=/var/data/results \
  -e TRACERECOVER_LOGS_DIR=/var/data/logs \
  -e TRACERECOVER_ADMIN_TOKEN=replace-with-strong-random-token \
  -e TRACERECOVER_CORS_ORIGINS=https://your-domain.example \
  tracerecover:latest
```

## 4. Cloud Deployment (Render.com Blueprint — Recommended)

`render.yaml` is included in the repository root and configures:
- Docker runtime built from `Dockerfile`
- Automatic HTTPS & reverse proxy (`--proxy-headers` enabled)
- Health check endpoint at `/api/health`
- Persistent 2 GB SSD disk mounted at `/var/data` (`TRACERECOVER_RESULTS_DIR=/var/data/results`, `TRACERECOVER_LOGS_DIR=/var/data/logs`)
- Auto-generated `TRACERECOVER_ADMIN_TOKEN` and secret-manager placeholders (`sync: false`) for `TRACERECOVER_CORS_ORIGINS` and optional LLM provider keys.

### Steps to Publish Publicly:
1. Initialize a Git repository and push this folder to a private/public GitHub repository (`git init && git add . && git commit -m "Initial TraceRecover release"`). Verify `.env` is excluded before pushing.
2. In Render Dashboard, select **New -> Blueprint** and connect the GitHub repository (`render.yaml` is auto-detected).
3. If mounting a fresh `/var/data` disk, run a one-off shell command on the service to seed `/app/results/*` into `/var/data/results/` (`cp -rn /app/results/. /var/data/results/`) or omit `TRACERECOVER_RESULTS_DIR` if ephemeral container storage of built-in results is sufficient.
4. Set `TRACERECOVER_CORS_ORIGINS` to the assigned `https://<your-service>.onrender.com` URL.
5. Verify the live deployment with:
   - `curl https://<your-service>.onrender.com/api/health`
   - Open `https://<your-service>.onrender.com/` in a browser and test all 10 dashboard sections.
