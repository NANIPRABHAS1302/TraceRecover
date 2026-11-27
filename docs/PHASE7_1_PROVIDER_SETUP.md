# TraceRecover Phase 7.1 LLM Provider Setup & Validation Status

## Supported Providers & Configuration Variables

| Provider ID | Mode | Environment Variable | Default Model | Host Status |
|---|---|---|---|---|
| `local` | Offline deterministic | None | `deterministic-local` | Configured (Default) |
| `gemini` | Live remote LLM | `GEMINI_API_KEY` | `gemini-2.0-flash` | Unconfigured (missing key) |
| `openai` | Live remote LLM | `OPENAI_API_KEY` | `gpt-4o-mini` | Unconfigured (missing key) |
| `anthropic` | Live remote LLM | `ANTHROPIC_API_KEY` | `claude-3-5-haiku-latest` | Unconfigured (missing key) |
| `ollama` | Local model server | `OLLAMA_BASE_URL` | `llama3.1` | Unconfigured (`127.0.0.1:11434` closed) |
| `local_openai` | Local OpenAI-compatible server | `LOCAL_LLM_BASE_URL` | `local-model` | Unconfigured |

## Enabling a Live Provider Safely

1. Never commit credentials to `.env` in version control or hardcode them into source files.
2. Export your API key or local endpoint in the runtime shell (or keep in an untracked local `.env`):
   - **Gemini**: `$env:GEMINI_API_KEY="AIza..."`
   - **OpenAI**: `$env:OPENAI_API_KEY="sk-..."`
   - **Anthropic**: `$env:ANTHROPIC_API_KEY="sk-ant-..."`
   - **Ollama (local)**: `$env:OLLAMA_BASE_URL="http://127.0.0.1:11434"`
3. Check `/api/providers` or call `LLMProviderAdapter(provider="<id>").validate_configuration()` to verify status without exposing secret values.
4. Budget caps default to `max_llm_calls=6` and `max_cost_usd=0.25` per query; TraceRecover refuses silent provider substitution when a requested provider is misconfigured, unauthorized (`401`/`403`), rate-limited (`429`), timed out, or unreachable.
