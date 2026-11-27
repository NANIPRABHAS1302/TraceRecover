# TraceRecover — Phase 7.5 Live Activation & Demonstration Guide

This guide explains how to activate a genuine live LLM provider (Gemini, OpenAI, Anthropic, Ollama, or a local OpenAI-compatible runtime) and execute the complete Phase 7.5 live intelligence validation without modifying any TraceRecover code.

---

## 1. Option A — Activate Gemini (Recommended Remote Provider)

Set `GEMINI_API_KEY` in your shell or `.env` file (never commit `.env` or print keys):

```powershell
$env:GEMINI_API_KEY = "<your-real-gemini-api-key>"
# Optional model override (default: gemini-2.0-flash)
$env:GEMINI_MODEL = "gemini-2.0-flash"
```

## 2. Option B — Activate OpenAI or Anthropic

```powershell
# OpenAI
$env:OPENAI_API_KEY = "<your-real-openai-api-key>"

# OR Anthropic
$env:ANTHROPIC_API_KEY = "<your-real-anthropic-api-key>"
```

## 3. Option C — Activate a Local Model Server (Zero API Cost via Ollama)

1. Install Ollama and pull a model (e.g. `ollama pull llama3.1`).
2. Start Ollama (`ollama serve`, listening on `http://127.0.0.1:11434`).
3. Configure TraceRecover:

```powershell
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "llama3.1"
```

---

## 4. Verify Live Provider Health & Run Phase 7.5 Validation

```powershell
# 1. Verify live provider connection, actual model ID, latency, and provider-reported tokens
.\venv\Scripts\python.exe -c "import json; from agent.provider_adapter import select_preferred_live_provider, LLMProviderAdapter; p=select_preferred_live_provider(); print(json.dumps(p, indent=2)); print(json.dumps(LLMProviderAdapter(provider=p['selected_provider']).health_check(), indent=2))"

# 2. Execute the complete Phase 7.5 10-scenario & 4-regime evaluation (runs Regime D automatically once live!)
.\venv\Scripts\python.exe eval\phase7_5_validation.py

# 3. Also run Regime D on the frozen Phase 7.4 92-question independent holdout
.\venv\Scripts\python.exe eval\phase7_4_validation.py
```

---

## 5. Interactive Dashboard Live Demo (`http://127.0.0.1:8000`)

1. Start the dashboard:
   ```powershell
   .\venv\Scripts\python.exe -m uvicorn web_dashboard:app --host 127.0.0.1 --port 8000 --proxy-headers
   ```
2. Open `http://127.0.0.1:8000`, navigate to **Query Workspace**, select your configured provider (`gemini`, `openai`, `anthropic`, or `ollama`), and click **Check Provider Health** to confirm `connected` status.
3. Enable **Enable External Search** and **Use Connected Tools**, submit a multi-source academic or technical query, and inspect:
   - Execution mode badge (`LIVE LLM + LIVE CONNECTOR` — never confused with `OFFLINE` or `MOCK`)
   - Live 10-stage SSE timeline
   - Research plan, source selection, and retrieved evidence
   - Claim-Level Verification table (`Claim ID`, `Study / Attribution`, `Supporting Source`, `Verification Status`, `Contradiction Classification`, `Verification Reason`)
   - Provider-reported token usage and cost estimation provenance
