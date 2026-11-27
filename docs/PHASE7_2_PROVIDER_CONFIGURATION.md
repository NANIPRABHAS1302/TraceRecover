# TraceRecover Phase 7.2 Live Provider Configuration & Verification Guide

## Priority Selection Order

When no provider is explicitly passed, `select_preferred_live_provider()` inspects configured credentials in this priority order:
1. `gemini` (`GEMINI_API_KEY`) — preferred default live provider
2. `openai` (`OPENAI_API_KEY`)
3. `anthropic` (`ANTHROPIC_API_KEY`)
4. `ollama` (`OLLAMA_BASE_URL`)
5. `local_openai` (`LOCAL_LLM_BASE_URL`)
6. `local` — offline deterministic fallback if none of the above are configured

Explicitly selecting an unconfigured provider refuses silent substitution and raises an explicit `missing_credentials` / `invalid_api_key` / `invalid_configuration` diagnostic.

## Host Environment Check (`2026-11-24`)

- `.env` present with zero non-empty remote API keys (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` empty/unset).
- Local model endpoints closed (`127.0.0.1:11434` and `127.0.0.1:1234` refused/closed; `ollama` CLI not installed).
- Health check API routes available at:
  - `GET /api/providers/status`
  - `POST /api/providers/health` with optional JSON body `{"provider": "gemini"}`
  - Interactive `"Check Provider Health"` button in Query Workspace UI.

## Quick Setup Commands (PowerShell)

```powershell
# Preferred live provider (Google Gemini)
$env:GEMINI_API_KEY="AIza..."
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/providers/health -ContentType application/json -Body '{"provider":"gemini"}'

# Or run Mode D after configuring a key:
.\venv\Scripts\python.exe eval\phase7_2_validation.py
```

Never commit real API keys to `.env`, Python source files, JS, or HTML, and never store credentials in browser storage.
