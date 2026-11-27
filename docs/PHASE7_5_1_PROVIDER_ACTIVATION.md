# TraceRecover — Phase 7.5.1 Provider Architecture Audit & Safe Credential Detection

**Audit Timestamp (UTC):** `2026-11-25T05:52:00Z`  
**Activation Outcome:** **`C. BLOCKED`** — Live provider activation stopped immediately per Task B because no genuine provider API key or local model server is configured on this host.

---

## 1. Task A — Provider Architecture Audit (Code-Verified)

Every supported provider integration was inspected directly in `config.py`, `agent/provider_adapter.py`, `agent/dynamic_planner.py`, `agent/synthesizer.py`, `agent/open_ended_runner.py`, `web_dashboard.py`, and `static/index.html`:

| Provider | Env Var | Actual Endpoint | Auth Mechanism | Default Model | Response & Token Parser |
|---|---|---|---|---|---|
| **Gemini** *(Priority 1)* | `GEMINI_API_KEY` | `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` (`config.py:270-292`) | `?key={key}` query parameter (masked by `_redact_secret_text` on errors) | `gemini-2.0-flash` | `candidates[0].content.parts[0].text`; `usageMetadata.promptTokenCount` / `candidatesTokenCount` |
| **OpenAI** | `OPENAI_API_KEY` | `POST https://api.openai.com/v1/chat/completions` (`config.py:322-346`) | `Authorization: Bearer {key}` | `gpt-4o-mini` | `choices[0].message.content`; `usage.prompt_tokens` / `completion_tokens` |
| **Anthropic** | `ANTHROPIC_API_KEY` | `POST https://api.anthropic.com/v1/messages` (`config.py:294-320`) | `x-api-key` + `anthropic-version: 2023-06-01` | `claude-3-5-haiku-latest` | `content[0].text`; `usage.input_tokens` / `output_tokens` |
| **Ollama** | `OLLAMA_BASE_URL` | `POST {base_url}/v1/chat/completions` (`agent/provider_adapter.py:528-576`) | Local HTTP(S) endpoint | `llama3.1` (`OLLAMA_MODEL`) | `choices[0].message.content`; `usage` or `usage_source="estimated"` |
| **Local OpenAI-Compat** | `LOCAL_LLM_BASE_URL` | `POST {base_url}/v1/chat/completions` (`agent/provider_adapter.py:528-576`) | Local HTTP(S) endpoint | `local-model` (`LOCAL_LLM_MODEL`) | `choices[0].message.content`; `usage` or `usage_source="estimated"` |

### Key Architecture Audit Observations
1. **Genuine Endpoints Only:** Zero IDE-internal, private CloudCode, or simulated endpoints exist anywhere in the provider stack.
2. **Automatic `.env` Loading:** `config.py:31-46` (`_load_env_file(PROJECT_ROOT / ".env")`) automatically loads `KEY=VALUE` entries from `.env` at import time without overriding process environment variables.
3. **Pricing Provenance:** `agent/provider_adapter.py:18-50` documents `PRICING_SOURCE = "config.MODEL_PRICING_PER_1M (published vendor rate cards)"` and `PRICING_RETRIEVED_DATE = "2026-11-24"`, distinguishing `zero_local`, `known_pricing_estimated`, and `unknown_pricing` (`None` cost when unknown).
4. **Honest Code-Audit Findings to Address Before/During First Live Run:**
   - **`agent/synthesizer.py:84-85`:** While `open_ended_runner.py` raises `ProviderUnavailableError` on planning failures (`no silent fallback`), `EvidenceGroundedSynthesizer.synthesize()` wraps its optional live synthesis call in `except Exception: pass`, which would silently fall back to deterministic synthesis if a live synthesis call failed mid-run. Once live credentials are available, this should record an explicit synthesis warning/failure telemetry flag rather than swallowing exceptions silently.
   - **Gemini API key transport (`config.py:274`):** Uses `?key=...` query string (supported by Google's REST API, though `x-goog-api-key` header is preferable to keep keys out of URL paths in proxy logs; `config.py` is protected so this is documented rather than modified).
   - **Health-check state granularity:** `health_check()` currently maps states to `unconfigured`, `connected`, or typed error categories (`missing_credentials`, `invalid_api_key`, `auth_failed`, `rate_limited`, `timeout`, `unreachable`, `empty_response`), cleanly separating `UNCONFIGURED` from `LIVE_RESPONSE_SUCCESS` and `LIVE_RESPONSE_FAILED`.

---

## 2. Task B — Safe Credential & Runtime Detection Results

All credential stores were inspected strictly for **presence and length** (zero secret values printed or logged):

| Source | `GEMINI_API_KEY` | `OPENAI_API_KEY` | `ANTHROPIC_API_KEY` | `OLLAMA_BASE_URL` | `LOCAL_LLM_BASE_URL` |
|---|---|---|---|---|---|
| Process Environment | Unset (`len=0`) | Unset (`len=0`) | Unset (`len=0`) | Unset (`len=0`) | Unset (`len=0`) |
| Windows User Registry (`HKCU\Environment`) | Unset (`len=0`) | Unset (`len=0`) | Unset (`len=0`) | Unset (`len=0`) | Unset (`len=0`) |
| Windows Machine Registry (`HKLM\...\Environment`) | Unset (`len=0`) | Unset (`len=0`) | Unset (`len=0`) | Unset (`len=0`) | Unset (`len=0`) |
| Local `.env` (`c:\tracerecover\.env`) | Absent | Absent | Absent | Absent | Absent |

- **Local Model Server Sockets Checked:** `127.0.0.1:11434` (Ollama: closed), `127.0.0.1:1234` (LM Studio: closed), `127.0.0.1:8001` (vLLM: closed), `127.0.0.1:8080` (llama.cpp: closed). `ollama` CLI on `PATH`: `False`.
- **Git Exclusion Confirmed:**
  - `.gitignore` lines 2–4 explicitly exclude `.env` and `.env.*` while keeping `.env.example`.
  - `git check-ignore .env` returned exit code `0` (ignored).
  - `git ls-files .env` returned empty (not tracked in Git).

---

## 3. Exact Missing Variables & Secure Local Configuration Steps

**Primary missing environment variable (prioritized):** `GEMINI_API_KEY`  
*(Alternatives: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`, or `LOCAL_LLM_BASE_URL`.)*

### How to Configure Securely Locally (Never Paste Keys in Chat or Commit `.env`)

**Option 1 — Local `.env` file (recommended; already git-ignored):**
Edit `c:\tracerecover\.env` locally on your machine and add ONE of:
```dotenv
# Option A: Google Gemini (prioritized)
GEMINI_API_KEY=<paste-key-locally-in-editor>
GEMINI_MODEL=gemini-2.0-flash

# Option B: OpenAI
# OPENAI_API_KEY=<paste-key-locally-in-editor>
# OPENAI_MODEL=gpt-4o-mini

# Option C: Local Ollama (zero API cost)
# OLLAMA_BASE_URL=http://127.0.0.1:11434
# OLLAMA_MODEL=llama3.1
```

**Option 2 — Session-scoped PowerShell environment variable (never written to disk):**
```powershell
$env:GEMINI_API_KEY = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText
```

Then restart the dashboard server (`uvicorn web_dashboard:app --host 127.0.0.1 --port 8000 --proxy-headers`) and verify live connectivity:
```powershell
.\venv\Scripts\python.exe eval\phase7_5_validation.py
```
