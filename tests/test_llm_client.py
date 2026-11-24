"""Mocked unit tests for LLMClient multi-provider adapters, token tracking, and cost."""

from __future__ import annotations

import json
from typing import Any

import pytest

from config import LLMClient, estimate_cost_usd


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_openai_adapter_tracks_tokens_latency_and_cost(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(
        "config.urlopen",
        lambda req, timeout=30: _FakeHTTPResponse(
            {
                "choices": [{"message": {"content": '{"action": "final", "final_answer": "42"}'}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            }
        ),
    )

    client = LLMClient(provider="openai", model="gpt-4o-mini")
    text, metrics = client.complete_with_metadata("Test prompt")

    assert "42" in text
    assert metrics.prompt_tokens == 1000
    assert metrics.completion_tokens == 200
    assert metrics.total_tokens == 1200
    assert metrics.latency_seconds >= 0.0
    assert metrics.estimated_cost_usd == pytest.approx(
        estimate_cost_usd("gpt-4o-mini", 1000, 200)
    )
    summary = client.usage_summary()
    assert summary["call_count"] == 1
    assert summary["total_tokens"] == 1200


def test_gemini_adapter_parses_usage_metadata(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(
        "config.urlopen",
        lambda req, timeout=30: _FakeHTTPResponse(
            {
                "candidates": [{"content": {"parts": [{"text": "Paris"}]}}],
                "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 50},
            }
        ),
    )

    client = LLMClient(provider="gemini", model="gemini-2.0-flash")
    text = client.complete("Capital of France?")

    assert text == "Paris"
    assert client.last_usage is not None
    assert client.last_usage.prompt_tokens == 500
    assert client.last_usage.completion_tokens == 50
    assert client.last_usage.estimated_cost_usd > 0.0


def test_anthropic_adapter_parses_usage_and_cost(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr(
        "config.urlopen",
        lambda req, timeout=30: _FakeHTTPResponse(
            {
                "content": [{"text": "Jupiter"}],
                "usage": {"input_tokens": 400, "output_tokens": 100},
            }
        ),
    )

    client = LLMClient(provider="anthropic", model="claude-3-5-haiku-latest")
    text = client.complete("Largest planet?")

    assert text == "Jupiter"
    assert client.usage_summary()["prompt_tokens"] == 400
    assert client.usage_summary()["completion_tokens"] == 100
    assert client.usage_summary()["estimated_cost_usd"] == pytest.approx(
        estimate_cost_usd("claude-3-5-haiku-latest", 400, 100)
    )


def test_local_client_records_zero_cost_telemetry() -> None:
    client = LLMClient(provider="local")
    client.record_local_call(latency_seconds=0.002)

    summary = client.usage_summary()
    assert summary["call_count"] == 1
    assert summary["total_tokens"] == 0
    assert summary["estimated_cost_usd"] == 0.0
    client.reset_usage()
    assert client.usage_summary()["call_count"] == 0
