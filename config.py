"""Application settings and provider-swappable LLM client with token/cost tracking."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent
SUPPORTED_PROVIDERS = ("gemini", "anthropic", "openai", "local")

# Published USD pricing per 1,000,000 tokens: (input_per_1m, output_per_1m)
MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-flash": (0.075, 0.30),
    "claude-3-5-haiku-latest": (0.80, 4.00),
    "claude-3-5-sonnet-latest": (3.00, 15.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "deterministic-local": (0.0, 0.0),
}


def _load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding process variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    """Runtime settings populated from the environment and .env file."""

    provider: str = os.getenv("LLM_PROVIDER", "gemini").lower()
    request_timeout: float = float(os.getenv("LLM_REQUEST_TIMEOUT", "30"))


@dataclass(frozen=True)
class LLMCallMetrics:
    """Token usage, latency, and estimated USD cost for a single LLM call."""

    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_seconds: float
    estimated_cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize per-call metrics."""
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_seconds": self.latency_seconds,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


def estimate_cost_usd(
    model: str, prompt_tokens: int, completion_tokens: int
) -> float:
    """Compute estimated USD API cost from token counts and model pricing."""
    input_rate, output_rate = MODEL_PRICING_PER_1M.get(model, (0.0, 0.0))
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000.0


def _approximate_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, (len(stripped) + 3) // 4)


def get_settings() -> Settings:
    """Return the current application settings."""
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported LLM_PROVIDER {provider!r}; choose from "
            f"{', '.join(SUPPORTED_PROVIDERS)}."
        )
    try:
        timeout = float(os.getenv("LLM_REQUEST_TIMEOUT", "30"))
    except ValueError as exc:
        raise ValueError("LLM_REQUEST_TIMEOUT must be a positive number.") from exc
    if timeout <= 0:
        raise ValueError("LLM_REQUEST_TIMEOUT must be a positive number.")
    return Settings(provider=provider, request_timeout=timeout)


class LLMClient:
    """Text-completion client for Gemini, Anthropic, OpenAI, and local mode with usage telemetry."""

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self.provider = (provider or settings.provider).strip().lower()
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported provider {self.provider!r}; choose from "
                f"{', '.join(SUPPORTED_PROVIDERS)}."
            )
        self.model = model or os.getenv(
            {
                "gemini": "GEMINI_MODEL",
                "anthropic": "ANTHROPIC_MODEL",
                "openai": "OPENAI_MODEL",
                "local": "LOCAL_MODEL",
            }[self.provider],
            {
                "gemini": "gemini-2.0-flash",
                "anthropic": "claude-3-5-haiku-latest",
                "openai": "gpt-4o-mini",
                "local": "deterministic-local",
            }[self.provider],
        )
        self.timeout = timeout if timeout is not None else settings.request_timeout
        self.call_history: list[LLMCallMetrics] = []

    @property
    def last_usage(self) -> LLMCallMetrics | None:
        """Return metrics from the most recent completion call."""
        return self.call_history[-1] if self.call_history else None

    def reset_usage(self) -> None:
        """Clear accumulated call telemetry."""
        self.call_history.clear()

    def usage_summary(self) -> dict[str, Any]:
        """Return aggregate token usage, latency, and estimated USD cost."""
        prompt_tokens = sum(item.prompt_tokens for item in self.call_history)
        completion_tokens = sum(item.completion_tokens for item in self.call_history)
        total_tokens = sum(item.total_tokens for item in self.call_history)
        total_latency = sum(item.latency_seconds for item in self.call_history)
        total_cost = sum(item.estimated_cost_usd for item in self.call_history)
        return {
            "provider": self.provider,
            "model": self.model,
            "call_count": len(self.call_history),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "total_latency_seconds": total_latency,
            "estimated_cost_usd": total_cost,
        }

    def record_local_call(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_seconds: float = 0.0,
    ) -> LLMCallMetrics:
        """Record zero-cost deterministic local planning step telemetry."""
        metrics = LLMCallMetrics(
            provider=self.provider,
            model=self.model,
            prompt_tokens=max(0, int(prompt_tokens)),
            completion_tokens=max(0, int(completion_tokens)),
            total_tokens=max(0, int(prompt_tokens)) + max(0, int(completion_tokens)),
            latency_seconds=max(0.0, float(latency_seconds)),
            estimated_cost_usd=0.0,
        )
        self.call_history.append(metrics)
        return metrics

    def complete(self, prompt: str) -> str:
        """Return a completion from the configured remote provider."""
        if self.provider == "local":
            raise ValueError(
                "The local provider is deterministic and does not accept free-form "
                "prompts; use the local planner in ReactAgent."
            )
        text, _ = self.complete_with_metadata(prompt)
        return text

    def complete_with_metadata(self, prompt: str) -> tuple[str, LLMCallMetrics]:
        """Return completion text along with token, latency, and cost metrics."""
        if self.provider == "local":
            raise ValueError(
                "The local provider is deterministic and does not accept free-form "
                "prompts; use the local planner in ReactAgent."
            )
        started = time.perf_counter()
        if self.provider == "gemini":
            text, prompt_tokens, completion_tokens = self._complete_gemini(prompt)
        elif self.provider == "anthropic":
            text, prompt_tokens, completion_tokens = self._complete_anthropic(prompt)
        else:
            text, prompt_tokens, completion_tokens = self._complete_openai(prompt)
        latency = time.perf_counter() - started
        metrics = LLMCallMetrics(
            provider=self.provider,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            latency_seconds=latency,
            estimated_cost_usd=estimate_cost_usd(
                self.model, prompt_tokens, completion_tokens
            ),
        )
        self.call_history.append(metrics)
        return text, metrics

    def _api_key(self, variable: str) -> str:
        key = os.getenv(variable, "").strip()
        if not key:
            raise RuntimeError(
                f"{variable} is not set. Add it to .env or select "
                "LLM_PROVIDER=local for the offline demo."
            )
        return key

    def _post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(
                f"{self.provider} API returned HTTP {exc.code}."
            ) from None
        except URLError as exc:
            raise RuntimeError(
                f"{self.provider} request failed: {exc.reason}."
            ) from None
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{self.provider} returned invalid JSON.") from exc
        if not isinstance(result, dict):
            raise RuntimeError(f"{self.provider} returned an unexpected response.")
        return result

    def _complete_gemini(self, prompt: str) -> tuple[str, int, int]:
        key = self._api_key("GEMINI_API_KEY")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(self.model, safe='')}:generateContent?key={quote(key, safe='')}"
        )
        result = self._post_json(
            url,
            {"contents": [{"parts": [{"text": prompt}]}]},
            {"Content-Type": "application/json"},
        )
        try:
            text = str(result["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Gemini returned no text candidate.") from exc
        usage = result.get("usageMetadata") if isinstance(result.get("usageMetadata"), dict) else {}
        prompt_tokens = _extract_token_int(
            usage.get("promptTokenCount"), fallback=_approximate_tokens(prompt)
        )
        completion_tokens = _extract_token_int(
            usage.get("candidatesTokenCount"), fallback=_approximate_tokens(text)
        )
        return text, prompt_tokens, completion_tokens

    def _complete_anthropic(self, prompt: str) -> tuple[str, int, int]:
        key = self._api_key("ANTHROPIC_API_KEY")
        result = self._post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": self.model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            {
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            text = str(result["content"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Anthropic returned no text content.") from exc
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        prompt_tokens = _extract_token_int(
            usage.get("input_tokens"), fallback=_approximate_tokens(prompt)
        )
        completion_tokens = _extract_token_int(
            usage.get("output_tokens"), fallback=_approximate_tokens(text)
        )
        return text, prompt_tokens, completion_tokens

    def _complete_openai(self, prompt: str) -> tuple[str, int, int]:
        key = self._api_key("OPENAI_API_KEY")
        result = self._post_json(
            "https://api.openai.com/v1/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
            },
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
        )
        try:
            text = str(result["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("OpenAI returned no text completion.") from exc
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        prompt_tokens = _extract_token_int(
            usage.get("prompt_tokens"), fallback=_approximate_tokens(prompt)
        )
        completion_tokens = _extract_token_int(
            usage.get("completion_tokens"), fallback=_approximate_tokens(text)
        )
        return text, prompt_tokens, completion_tokens


def _extract_token_int(value: Any, *, fallback: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return fallback
