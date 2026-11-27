"""Unified LLM provider adapter supporting Gemini, OpenAI, Anthropic, local deterministic mode, and local OpenAI-compatible runtimes."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import LLMCallMetrics, LLMClient, MODEL_PRICING_PER_1M, SUPPORTED_PROVIDERS, estimate_cost_usd

EXTENDED_PROVIDERS: tuple[str, ...] = tuple(SUPPORTED_PROVIDERS) + ("ollama", "local_openai")
_LIVE_PROVIDER_PRIORITY: tuple[str, ...] = ("gemini", "openai", "anthropic", "ollama", "local_openai")
PRICING_SOURCE = "config.MODEL_PRICING_PER_1M (published vendor rate cards)"
PRICING_RETRIEVED_DATE = "2026-11-24"


def get_pricing_metadata(model: str, provider: str) -> dict[str, Any]:
    """Return explicit pricing provenance so estimated costs are never confused with exact billing."""
    if provider == "local" or model == "deterministic-local":
        return {
            "pricing_known": True,
            "cost_estimation_status": "zero_local",
            "pricing_source": PRICING_SOURCE,
            "pricing_retrieved_date": PRICING_RETRIEVED_DATE,
            "input_per_1m_usd": 0.0,
            "output_per_1m_usd": 0.0,
        }
    if model in MODEL_PRICING_PER_1M:
        in_r, out_r = MODEL_PRICING_PER_1M[model]
        return {
            "pricing_known": True,
            "cost_estimation_status": "known_pricing_estimated",
            "pricing_source": PRICING_SOURCE,
            "pricing_retrieved_date": PRICING_RETRIEVED_DATE,
            "input_per_1m_usd": in_r,
            "output_per_1m_usd": out_r,
        }
    return {
        "pricing_known": False,
        "cost_estimation_status": "unknown_pricing",
        "pricing_source": PRICING_SOURCE,
        "pricing_retrieved_date": PRICING_RETRIEVED_DATE,
        "input_per_1m_usd": None,
        "output_per_1m_usd": None,
    }
_SECRET_LEAK_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}"
    r"|AIza[0-9A-Za-z_-]{12,}"
    r"|Bearer\s+[A-Za-z0-9._-]{8,}"
    r"|(?:x-goog-api-key|x-api-key|authorization)\s*[:=]\s*[^\s,;\"']{6,}"
    r"|(?:api[_-]?key|access[_-]?token)\s*[:=]\s*[^\s&\"',;]{6,})",
    re.IGNORECASE,
)
_PLACEHOLDER_KEYS = {"your-api-key", "changeme", "placeholder", "none", "null", "xxx", "test"}


def _redact_secret_text(text: str) -> str:
    return _SECRET_LEAK_RE.sub("[REDACTED_KEY]", text or "")


redact_secret_text = _redact_secret_text


def classify_provider_exception(exc: BaseException, *, provider: str = "unknown") -> tuple[str, str]:
    """Classify a provider/LLM exception into a canonical failure category with secrets redacted."""
    msg = _redact_secret_text(str(exc))
    if isinstance(exc, ProviderAdapterError):
        return exc.category, msg
    if isinstance(exc, TimeoutError):
        return "timeout", msg or f"{provider} request timed out."
    low = msg.lower()
    if "budget" in low or "max_llm_calls" in low or "max_cost_usd" in low:
        return "budget_exceeded", msg
    if "empty" in low and "response" in low:
        return "empty_response", msg
    if "malformed" in low or "invalid json" in low or "json" in low and ("parse" in low or "decode" in low or "validation" in low):
        return "malformed_output", msg
    if "missing" in low and ("key" in low or "credential" in low or "env" in low or "is not set" in low):
        return "missing_credentials", msg
    if "is not set" in low:
        return "missing_credentials", msg
    if "invalid_api_key" in low or "placeholder" in low or "malformed" in low and "key" in low:
        return "invalid_api_key", msg
    if "401" in low or "403" in low or "invalid api key" in low or "unauthorized" in low or "forbidden" in low or "auth" in low:
        return "auth_failed", msg
    if "429" in low or "rate limit" in low or "too many requests" in low or "quota" in low:
        return "rate_limited", msg
    if "timeout" in low or "timed out" in low:
        return "timeout", msg
    return "unreachable", msg or f"{provider} provider request failed."


def _is_malformed_api_key(val: str) -> bool:
    s = (val or "").strip()
    if not s:
        return False
    low = s.lower()
    if low in _PLACEHOLDER_KEYS or low.startswith("your_") or low.startswith("<") or len(s) < 8:
        return True
    return any(ch.isspace() for ch in s)


def _is_valid_http_url(url: str) -> bool:
    s = (url or "").strip().lower()
    return s.startswith(("http://", "https://")) and " " not in s and len(s) > 10


class ProviderAdapterError(RuntimeError):
    """Structured provider failure preserving error category without exposing secret keys."""

    def __init__(self, category: str, message: str, *, provider: str) -> None:
        super().__init__(message)
        self.category = category  # missing_credentials | auth_failed | rate_limited | timeout | unreachable | budget_exceeded
        self.provider = provider


def list_provider_capabilities() -> list[dict[str, Any]]:
    """Return server-side provider availability metadata without exposing any credentials."""
    caps: list[dict[str, Any]] = [
        {
            "id": "local",
            "mode": "offline_deterministic",
            "configured": True,
            "model": os.getenv("LOCAL_MODEL", "deterministic-local"),
            "env_var": None,
        },
        {
            "id": "gemini",
            "mode": "live_remote_llm",
            "configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
            "model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
            "env_var": "GEMINI_API_KEY",
        },
        {
            "id": "openai",
            "mode": "live_remote_llm",
            "configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
            "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "env_var": "OPENAI_API_KEY",
        },
        {
            "id": "anthropic",
            "mode": "live_remote_llm",
            "configured": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
            "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
            "env_var": "ANTHROPIC_API_KEY",
        },
        {
            "id": "ollama",
            "mode": "local_model_server",
            "configured": bool(os.getenv("OLLAMA_BASE_URL", "").strip()),
            "model": os.getenv("OLLAMA_MODEL", "llama3.1"),
            "env_var": "OLLAMA_BASE_URL",
        },
        {
            "id": "local_openai",
            "mode": "local_model_server",
            "configured": bool(os.getenv("LOCAL_LLM_BASE_URL", "").strip()),
            "model": os.getenv("LOCAL_LLM_MODEL", "local-model"),
            "env_var": "LOCAL_LLM_BASE_URL",
        },
    ]
    return caps


def select_preferred_live_provider(requested: str | None = None) -> dict[str, Any]:
    """Select preferred live LLM provider (Gemini first when configured) without silent fallback if explicit."""
    caps = list_provider_capabilities()
    by_id = {c["id"]: c for c in caps}
    if requested:
        req_id = requested.strip().lower()
        info = by_id.get(req_id)
        if not info:
            return {
                "selected_provider": req_id,
                "live_available": False,
                "configured": False,
                "mode": "unsupported",
                "reason": f"Unsupported provider {req_id!r}.",
            }
        return {
            "selected_provider": req_id,
            "live_available": bool(req_id != "local" and info["configured"]),
            "configured": bool(info["configured"]),
            "mode": info["mode"],
            "model": info["model"],
            "env_var": info["env_var"],
            "reason": (
                f"Requested provider '{req_id}' is configured."
                if info["configured"]
                else f"Requested provider '{req_id}' missing {info['env_var']}."
            ),
        }

    for pid in _LIVE_PROVIDER_PRIORITY:
        info = by_id.get(pid)
        if info and info["configured"]:
            return {
                "selected_provider": pid,
                "live_available": True,
                "configured": True,
                "mode": info["mode"],
                "model": info["model"],
                "env_var": info["env_var"],
                "reason": f"Selected configured live provider '{pid}' (priority order: gemini first).",
            }
    local_info = by_id["local"]
    return {
        "selected_provider": "local",
        "live_available": False,
        "configured": True,
        "mode": local_info["mode"],
        "model": local_info["model"],
        "env_var": None,
        "reason": (
            "No live LLM credentials configured (checked GEMINI_API_KEY, OPENAI_API_KEY, "
            "ANTHROPIC_API_KEY, OLLAMA_BASE_URL, LOCAL_LLM_BASE_URL); falling back to offline local mode by default."
        ),
    }


class LLMProviderAdapter:
    """Budget-enforced abstraction over Gemini, OpenAI, Anthropic, local deterministic mode, and local OpenAI-compatible APIs."""

    def __init__(
        self,
        *,
        provider: str = "local",
        model: str | None = None,
        client: LLMClient | None = None,
        max_llm_calls: int = 6,
        max_cost_usd: float = 0.25,
        max_output_tokens: int = 1024,
        timeout: float = 30.0,
    ) -> None:
        self.provider = (client.provider if client else provider).strip().lower()
        if self.provider not in EXTENDED_PROVIDERS:
            raise ProviderAdapterError(
                "unsupported_provider",
                f"Unsupported provider {self.provider!r}; choose from {EXTENDED_PROVIDERS}.",
                provider=self.provider,
            )
        self.max_llm_calls = max(1, int(max_llm_calls))
        self.max_cost_usd = max(0.0, float(max_cost_usd))
        self.max_output_tokens = max(16, int(max_output_tokens))
        self.timeout = float(timeout)
        self._custom_history: list[LLMCallMetrics] = []
        self.last_error_category: str | None = None
        self.retry_count: int = 0
        self.failed_requests: int = 0
        self.last_usage_source: str = "none" if self.provider == "local" else "unexecuted"

        if self.provider in SUPPORTED_PROVIDERS:
            self.client: LLMClient | None = client or LLMClient(provider=self.provider, model=model, timeout=timeout)
            self.model = self.client.model
        else:
            self.client = None
            self.model = model or (
                os.getenv("OLLAMA_MODEL", "llama3.1")
                if self.provider == "ollama"
                else os.getenv("LOCAL_LLM_MODEL", "local-model")
            )

    @property
    def is_remote_or_custom(self) -> bool:
        return self.provider != "local"

    @property
    def execution_mode(self) -> str:
        if self.provider == "local":
            return "offline_deterministic"
        if self.provider in ("ollama", "local_openai"):
            return "local_model_server"
        return "live_remote_llm"

    def validate_configuration(self) -> dict[str, Any]:
        """Validate provider credentials, endpoint URL format, and budget bounds without exposing secrets."""
        try:
            self.check_credentials()
            status = "configured"
            valid = True
            reason = "Provider configuration validated."
        except ProviderAdapterError as exc:
            status = exc.category
            valid = False
            reason = _redact_secret_text(str(exc))
        return {
            "provider": self.provider,
            "model": self.model,
            "execution_mode": self.execution_mode,
            "configured": valid,
            "credential_status": status,
            "validation_message": reason,
            "max_llm_calls": self.max_llm_calls,
            "max_cost_usd": self.max_cost_usd,
            "timeout_seconds": self.timeout,
        }

    def provider_status_summary(self) -> dict[str, Any]:
        """Safe diagnostic summary suitable for API/dashboard rendering (zero secrets exposed)."""
        cfg = self.validate_configuration()
        usage = self.usage_summary()
        conn_status = (
            "offline_local"
            if self.provider == "local"
            else ("ready" if cfg["configured"] and not self.last_error_category else (self.last_error_category or cfg["credential_status"]))
        )
        pricing = get_pricing_metadata(self.model, self.provider)
        return {
            **cfg,
            **pricing,
            "connection_status": conn_status,
            "call_count": usage.get("call_count", 0),
            "retry_count": usage.get("retry_count", 0),
            "failed_requests": usage.get("failed_requests", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "usage_source": usage.get("usage_source", "none"),
            "total_latency_seconds": usage.get("total_latency_seconds", 0.0),
            "estimated_cost_usd": usage.get("estimated_cost_usd", 0.0),
            "max_output_tokens": self.max_output_tokens,
            "last_error_category": self.last_error_category,
        }

    def record_retry(self) -> None:
        self.retry_count += 1

    def health_check(self, *, probe_prompt: str = "Respond with exactly: OK") -> dict[str, Any]:
        """Execute a single bounded health check verifying live provider connectivity without secret exposure."""
        ts = datetime.now(timezone.utc).isoformat()
        if self.provider == "local":
            return {
                "provider": "local",
                "model": self.model,
                "timestamp": ts,
                "connection_status": "offline_local",
                "configured": True,
                "latency_seconds": 0.0,
                "error_category": None,
                "error_message": None,
                "token_usage": None,
                "usage_source": "none",
                "note": "Offline deterministic mode active; no external LLM call made.",
            }
        try:
            self.check_credentials()
        except ProviderAdapterError as exc:
            return {
                "provider": self.provider,
                "model": self.model,
                "timestamp": ts,
                "connection_status": "unconfigured" if exc.category == "missing_credentials" else exc.category,
                "configured": False,
                "latency_seconds": 0.0,
                "error_category": exc.category,
                "error_message": _redact_secret_text(str(exc)),
                "token_usage": None,
                "usage_source": "none",
            }

        started = time.perf_counter()
        try:
            reply, metrics = self.complete_with_budget(probe_prompt)
            lat = round(time.perf_counter() - started, 4)
            pricing = get_pricing_metadata(self.model, self.provider)
            return {
                "provider": self.provider,
                "model": self.model,
                "timestamp": ts,
                "connection_status": "connected",
                "configured": True,
                "latency_seconds": lat,
                "error_category": None,
                "error_message": None,
                "response_preview": reply.strip()[:80],
                "token_usage": {
                    "prompt_tokens": metrics.prompt_tokens,
                    "completion_tokens": metrics.completion_tokens,
                    "total_tokens": metrics.total_tokens,
                    "estimated_cost_usd": metrics.estimated_cost_usd if pricing["pricing_known"] else None,
                },
                "usage_source": self.last_usage_source,
                **pricing,
            }
        except ProviderAdapterError as exc:
            lat = round(time.perf_counter() - started, 4)
            return {
                "provider": self.provider,
                "model": self.model,
                "timestamp": ts,
                "connection_status": exc.category,
                "configured": True,
                "latency_seconds": lat,
                "error_category": exc.category,
                "error_message": _redact_secret_text(str(exc)),
                "token_usage": None,
                "usage_source": "none",
            }

    def check_credentials(self) -> None:
        """Verify required credentials or local base URL without silent fallback."""
        if self.provider == "local":
            return
        if self.provider in ("gemini", "openai", "anthropic"):
            env_name = f"{self.provider.upper()}_API_KEY"
            raw_val = os.getenv(env_name, "").strip()
            if not raw_val:
                self.last_error_category = "missing_credentials"
                raise ProviderAdapterError(
                    "missing_credentials",
                    f"Requested provider '{self.provider}' is not configured ({env_name} is missing); refusing silent fallback.",
                    provider=self.provider,
                )
            if _is_malformed_api_key(raw_val):
                self.last_error_category = "invalid_api_key"
                raise ProviderAdapterError(
                    "invalid_api_key",
                    f"Configured credential {env_name} appears malformed or a placeholder; refusing silent fallback.",
                    provider=self.provider,
                )
        elif self.provider == "ollama":
            base_url = os.getenv("OLLAMA_BASE_URL", "").strip()
            if not base_url:
                self.last_error_category = "missing_credentials"
                raise ProviderAdapterError(
                    "missing_credentials",
                    "Requested local provider 'ollama' requires OLLAMA_BASE_URL to be configured.",
                    provider="ollama",
                )
            if not _is_valid_http_url(base_url):
                self.last_error_category = "invalid_configuration"
                raise ProviderAdapterError(
                    "invalid_configuration",
                    "OLLAMA_BASE_URL must be a valid http:// or https:// endpoint URL.",
                    provider="ollama",
                )
        elif self.provider == "local_openai":
            base_url = os.getenv("LOCAL_LLM_BASE_URL", "").strip()
            if not base_url:
                self.last_error_category = "missing_credentials"
                raise ProviderAdapterError(
                    "missing_credentials",
                    "Requested local provider 'local_openai' requires LOCAL_LLM_BASE_URL to be configured.",
                    provider="local_openai",
                )
            if not _is_valid_http_url(base_url):
                self.last_error_category = "invalid_configuration"
                raise ProviderAdapterError(
                    "invalid_configuration",
                    "LOCAL_LLM_BASE_URL must be a valid http:// or https:// endpoint URL.",
                    provider="local_openai",
                )

    def record_local_call(self, *, latency_seconds: float = 0.0) -> None:
        if self.client is not None and hasattr(self.client, "record_local_call"):
            self.client.record_local_call(latency_seconds=latency_seconds)

    def usage_summary(self) -> dict[str, Any]:
        pricing = get_pricing_metadata(self.model, self.provider)
        if self.client is not None:
            summary = dict(self.client.usage_summary())
            summary.setdefault("execution_mode", self.execution_mode)
            summary["last_error_category"] = self.last_error_category
            summary["retry_count"] = self.retry_count
            summary["failed_requests"] = self.failed_requests
            summary["usage_source"] = "none" if self.provider == "local" else (
                "provider_reported" if summary.get("total_tokens", 0) > 0 and self.last_usage_source == "provider_reported"
                else (self.last_usage_source if self.last_usage_source != "unexecuted" else "unknown")
            )
            summary.update(pricing)
            return summary
        return {
            "provider": self.provider,
            "model": self.model,
            "execution_mode": self.execution_mode,
            "last_error_category": self.last_error_category,
            "retry_count": self.retry_count,
            "failed_requests": self.failed_requests,
            "usage_source": self.last_usage_source if self.last_usage_source != "unexecuted" else "unknown",
            "call_count": len(self._custom_history),
            "prompt_tokens": sum(m.prompt_tokens for m in self._custom_history),
            "completion_tokens": sum(m.completion_tokens for m in self._custom_history),
            "total_tokens": sum(m.total_tokens for m in self._custom_history),
            "total_latency_seconds": round(sum(getattr(m, "latency_seconds", 0.0) for m in self._custom_history), 6),
            "estimated_cost_usd": sum(m.estimated_cost_usd for m in self._custom_history),
            **pricing,
        }

    def complete_with_metadata(self, prompt: str) -> tuple[str, LLMCallMetrics]:
        """Delegate standard client call interface through budget-enforced path."""
        return self.complete_with_budget(prompt)

    def complete_with_budget(self, prompt: str) -> tuple[str, LLMCallMetrics]:
        """Execute completion under strict call/cost budgets and classify provider errors cleanly."""
        try:
            self.check_credentials()
        except ProviderAdapterError:
            self.failed_requests += 1
            raise
        summary = self.usage_summary()
        if summary["call_count"] >= self.max_llm_calls:
            self.last_error_category = "budget_exceeded"
            self.failed_requests += 1
            raise ProviderAdapterError(
                "budget_exceeded",
                f"LLM call budget exceeded ({self.max_llm_calls} calls max).",
                provider=self.provider,
            )
        if summary["estimated_cost_usd"] >= self.max_cost_usd:
            self.last_error_category = "budget_exceeded"
            self.failed_requests += 1
            raise ProviderAdapterError(
                "budget_exceeded",
                f"LLM cost budget exceeded (${self.max_cost_usd:.4f} max).",
                provider=self.provider,
            )

        if self.client is not None:
            try:
                text, metrics = self.client.complete_with_metadata(prompt)
                if not str(text or "").strip():
                    self.last_error_category = "empty_response"
                    self.failed_requests += 1
                    raise ProviderAdapterError(
                        "empty_response",
                        f"Provider '{self.provider}' returned an empty response.",
                        provider=self.provider,
                    )
                self.last_usage_source = (
                    "provider_reported"
                    if (getattr(metrics, "prompt_tokens", 0) > 0 or getattr(metrics, "completion_tokens", 0) > 0)
                    else "estimated"
                )
                return str(text), metrics
            except ProviderAdapterError:
                raise
            except TimeoutError as exc:
                self.last_error_category = "timeout"
                self.failed_requests += 1
                raise ProviderAdapterError("timeout", _redact_secret_text(f"{self.provider} timed out: {exc}"), provider=self.provider) from exc
            except RuntimeError as exc:
                msg = _redact_secret_text(str(exc))
                low = msg.lower()
                if "is not set" in low or "missing" in low:
                    cat = "missing_credentials"
                elif "401" in low or "403" in low or "invalid api key" in low or "unauthorized" in low:
                    cat = "auth_failed"
                elif "429" in low or "rate limit" in low or "quota" in low:
                    cat = "rate_limited"
                elif "timeout" in low or "timed out" in low:
                    cat = "timeout"
                else:
                    cat = "unreachable"
                self.last_error_category = cat
                self.failed_requests += 1
                raise ProviderAdapterError(cat, msg, provider=self.provider) from exc

        # OpenAI-compatible local endpoint (Ollama / vLLM / LM Studio)
        base_url = (
            os.getenv("OLLAMA_BASE_URL", "").strip()
            if self.provider == "ollama"
            else os.getenv("LOCAL_LLM_BASE_URL", "").strip()
        ).rstrip("/")
        endpoint = f"{base_url}/v1/chat/completions" if not base_url.endswith("/v1") else f"{base_url}/chat/completions"
        started = time.perf_counter()
        req = Request(
            endpoint,
            data=json.dumps(
                {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": self.max_output_tokens,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = str((((data.get("choices") or [{}])[0]).get("message") or {}).get("content") or "")
            if not text.strip():
                self.last_error_category = "empty_response"
                self.failed_requests += 1
                raise ProviderAdapterError(
                    "empty_response",
                    f"Provider '{self.provider}' returned an empty response.",
                    provider=self.provider,
                )
            usage = data.get("usage") or {}
            has_reported = bool(usage.get("prompt_tokens") or usage.get("completion_tokens"))
            self.last_usage_source = "provider_reported" if has_reported else "estimated"
            pt = int(usage.get("prompt_tokens") or max(1, len(prompt) // 4))
            ct = int(usage.get("completion_tokens") or max(1, len(text) // 4))
            metrics = LLMCallMetrics(
                provider=self.provider,
                model=self.model,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=pt + ct,
                latency_seconds=time.perf_counter() - started,
                estimated_cost_usd=estimate_cost_usd(self.model, pt, ct),
            )
            self._custom_history.append(metrics)
            return text, metrics
        except ProviderAdapterError:
            raise
        except TimeoutError:
            self.last_error_category = "timeout"
            self.failed_requests += 1
            raise ProviderAdapterError("timeout", f"{self.provider} request timed out after {self.timeout}s", provider=self.provider) from None
        except HTTPError as exc:
            cat = "auth_failed" if exc.code in (401, 403) else ("rate_limited" if exc.code == 429 else "unreachable")
            self.last_error_category = cat
            self.failed_requests += 1
            raise ProviderAdapterError(cat, f"{self.provider} HTTP {exc.code}", provider=self.provider) from None
        except URLError as exc:
            reason_str = str(exc.reason or "")
            cat = "timeout" if "timed out" in reason_str.lower() else "unreachable"
            self.last_error_category = cat
            self.failed_requests += 1
            raise ProviderAdapterError(cat, _redact_secret_text(f"{self.provider} unreachable: {reason_str}"), provider=self.provider) from None
