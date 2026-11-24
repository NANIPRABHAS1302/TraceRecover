"""Modular external search adapter for real-time retrieval with provenance."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus, urlparse
from urllib.request import Request, urlopen


SUPPORTED_SEARCH_PROVIDERS = ("auto", "wikipedia", "duckduckgo", "tavily", "serpapi", "disabled")
NO_EXTERNAL_EVIDENCE_PREFIX = "No external search evidence found for:"

_FILLER_PREFIX_RE = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+|would you\s+|tell me\s+|explain\s+|search(?:\s+for)?\s+|look up\s+)+",
    re.IGNORECASE,
)
_FOLLOWUP_PRONOUN_RE = re.compile(r"\b(it|its|that|this|they|them|those|these|one|such|same)\b", re.IGNORECASE)
_ALLOWED_URL_SCHEMES = {"http", "https"}


@dataclass(frozen=True)
class ExternalSearchResult:
    """One verifiable externally retrieved source with URL and snippet."""

    title: str
    url: str
    snippet: str
    provider: str
    retrieved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at,
        }


@dataclass(frozen=True)
class ExternalSearchResponse:
    """Structured search response preserving retrieved evidence or failure details."""

    query: str
    generated_query: str
    provider: str
    results: list[ExternalSearchResult]
    status: str  # "ok", "no_results", "error", "disabled"
    error: str | None = None
    latency_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "generated_query": self.generated_query,
            "provider": self.provider,
            "status": self.status,
            "error": self.error,
            "latency_seconds": round(self.latency_seconds, 6),
            "result_count": len(self.results),
            "results": [item.to_dict() for item in self.results],
            "sources": [
                {"title": item.title, "url": item.url, "provider": item.provider}
                for item in self.results
            ],
            "summary": self.to_evidence_text(),
        }

    def to_evidence_text(self) -> str:
        if not self.results:
            return f"{NO_EXTERNAL_EVIDENCE_PREFIX} {self.generated_query or self.query}"
        parts: list[str] = []
        for idx, item in enumerate(self.results, start=1):
            snip = item.snippet.strip()
            if snip and snip[-1] not in ".!?":
                snip += "."
            parts.append(f"[{idx}] {item.title} ({item.url}): {snip}")
        return "\n".join(parts)


def is_valid_http_url(url: Any) -> bool:
    """Verify URL uses http/https with a non-empty network location."""
    if not isinstance(url, str):
        return False
    cleaned = url.strip()
    if not cleaned or len(cleaned) > 2048:
        return False
    parsed = urlparse(cleaned)
    return parsed.scheme.lower() in _ALLOWED_URL_SCHEMES and bool(parsed.netloc)


def generate_search_query(
    question: str,
    context_messages: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Generate a concise search query, resolving follow-up pronouns from recent context."""
    cleaned = " ".join(str(question or "").strip().split())
    if not cleaned:
        return ""
    stripped = _FILLER_PREFIX_RE.sub("", cleaned).strip(" ?!.")
    base = stripped or cleaned.strip(" ?!.")

    if context_messages and _FOLLOWUP_PRONOUN_RE.search(base):
        prior_topic = ""
        for msg in reversed(context_messages):
            role = str(msg.get("role", "")).lower()
            content = " ".join(str(msg.get("content", "")).strip().split())
            if role == "user" and content:
                candidate = _FILLER_PREFIX_RE.sub("", content).strip(" ?!.")
                if candidate and candidate.casefold() != base.casefold():
                    prior_topic = candidate[:120]
                    break
        if prior_topic:
            merged = f"{prior_topic} {base}".strip()
            return merged[:220]
    return base[:220]


def _default_http_get(url: str, timeout: float, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
    req_headers = {
        "User-Agent": "TraceRecover-ResearchAgent/1.0 (+https://localhost)",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = Request(url, headers=req_headers, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"External search HTTP {exc.code}.") from None
    except URLError as exc:
        raise RuntimeError(f"External search connection error: {exc.reason}.") from None
    except TimeoutError:
        raise TimeoutError("External search request timed out.") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"External search returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("External search returned unexpected payload.")
    return data


def _default_http_post(
    url: str,
    payload: Mapping[str, Any],
    timeout: float,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "TraceRecover-ResearchAgent/1.0",
    }
    if headers:
        req_headers.update(headers)
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=req_headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"External search HTTP {exc.code}.") from None
    except URLError as exc:
        raise RuntimeError(f"External search connection error: {exc.reason}.") from None
    except TimeoutError:
        raise TimeoutError("External search request timed out.") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"External search returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("External search returned unexpected payload.")
    return data


class ExternalSearchAdapter:
    """Configurable external web/reference search adapter with strict provenance."""

    def __init__(
        self,
        provider: str | None = None,
        *,
        timeout: float = 8.0,
        max_results: int = 3,
        http_get: Callable[[str, float, Mapping[str, str] | None], dict[str, Any]] | None = None,
        http_post: Callable[[str, Mapping[str, Any], float, Mapping[str, str] | None], dict[str, Any]] | None = None,
        provider_fn: Callable[[str, int], Sequence[Mapping[str, Any]]] | None = None,
    ) -> None:
        raw_provider = (provider or os.getenv("TRACERECOVER_SEARCH_PROVIDER", "auto")).strip().lower()
        if raw_provider not in SUPPORTED_SEARCH_PROVIDERS:
            raise ValueError(
                f"Unsupported search provider {raw_provider!r}; expected one of "
                f"{', '.join(SUPPORTED_SEARCH_PROVIDERS)}."
            )
        if timeout <= 0:
            raise ValueError("Search timeout must be positive.")
        if max_results < 1 or max_results > 10:
            raise ValueError("max_results must be between 1 and 10.")
        self.provider = raw_provider
        self.timeout = float(timeout)
        self.max_results = int(max_results)
        self._http_get = http_get or _default_http_get
        self._http_post = http_post or _default_http_post
        self._provider_fn = provider_fn

    def search(
        self,
        query: str,
        *,
        context_messages: Sequence[Mapping[str, Any]] = (),
        raise_on_failure: bool = True,
    ) -> ExternalSearchResponse:
        """Retrieve verifiable external search snippets without fabricating URLs."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("External search query must be a non-empty string.")
        started = time.perf_counter()
        generated = generate_search_query(query, context_messages)

        if self.provider == "disabled":
            msg = "External search provider is disabled."
            if raise_on_failure:
                raise RuntimeError(msg)
            return ExternalSearchResponse(
                query=query,
                generated_query=generated,
                provider="disabled",
                results=[],
                status="disabled",
                error=msg,
                latency_seconds=time.perf_counter() - started,
            )

        try:
            if self._provider_fn is not None:
                raw_items = list(self._provider_fn(generated, self.max_results))
                results = self._sanitize_results(raw_items, default_provider=self.provider)
            elif self.provider == "wikipedia":
                results = self._search_wikipedia(generated)
            elif self.provider == "duckduckgo":
                results = self._search_duckduckgo(generated)
            elif self.provider == "tavily":
                results = self._search_tavily(generated)
            elif self.provider == "serpapi":
                results = self._search_serpapi(generated)
            else:  # "auto"
                results = self._search_auto(generated)
        except Exception as exc:
            if raise_on_failure:
                raise
            return ExternalSearchResponse(
                query=query,
                generated_query=generated,
                provider=self.provider,
                results=[],
                status="error",
                error=str(exc),
                latency_seconds=time.perf_counter() - started,
            )

        status = "ok" if results else "no_results"
        return ExternalSearchResponse(
            query=query,
            generated_query=generated,
            provider=self.provider,
            results=results[: self.max_results],
            status=status,
            error=None,
            latency_seconds=time.perf_counter() - started,
        )

    def _sanitize_results(
        self, items: Sequence[Mapping[str, Any]], *, default_provider: str
    ) -> list[ExternalSearchResult]:
        clean: list[ExternalSearchResult] = []
        seen_urls: set[str] = set()
        for item in items:
            if not isinstance(item, Mapping):
                continue
            title = " ".join(str(item.get("title", "")).split())
            url = str(item.get("url", "")).strip()
            snippet = " ".join(str(item.get("snippet", "")).split())
            prov = str(item.get("provider") or default_provider).strip() or default_provider
            if not title or not snippet or not is_valid_http_url(url):
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            clean.append(
                ExternalSearchResult(
                    title=title[:180],
                    url=url,
                    snippet=snippet[:600],
                    provider=prov,
                )
            )
            if len(clean) >= self.max_results:
                break
        return clean

    def _search_auto(self, query: str) -> list[ExternalSearchResult]:
        errors: list[str] = []
        # 1. Prefer Tavily/SerpAPI automatically only if backend key is explicitly configured
        if os.getenv("TAVILY_API_KEY", "").strip():
            try:
                res = self._search_tavily(query)
                if res:
                    return res
            except Exception as exc:
                errors.append(f"tavily: {exc}")

        # 2. Keyless public references: Wikipedia MediaWiki search API + DuckDuckGo Instant Answer
        combined: list[ExternalSearchResult] = []
        try:
            combined.extend(self._search_wikipedia(query))
        except Exception as exc:
            errors.append(f"wikipedia: {exc}")

        if len(combined) < self.max_results:
            try:
                combined.extend(self._search_duckduckgo(query))
            except Exception as exc:
                errors.append(f"duckduckgo: {exc}")

        if combined:
            return self._sanitize_results(
                [r.to_dict() for r in combined], default_provider="auto"
            )
        if errors and len(errors) >= 2:
            raise RuntimeError("; ".join(errors))
        return []

    def _search_wikipedia(self, query: str) -> list[ExternalSearchResult]:
        url = (
            "https://en.wikipedia.org/w/api.php?"
            f"action=query&list=search&srsearch={quote_plus(query)}"
            f"&srlimit={self.max_results}&utf8=1&format=json"
        )
        data = self._http_get(url, self.timeout, None)
        hits = ((data.get("query") or {}).get("search")) if isinstance(data.get("query"), Mapping) else []
        if not isinstance(hits, list):
            return []
        raw: list[dict[str, Any]] = []
        for entry in hits:
            if not isinstance(entry, Mapping):
                continue
            title = str(entry.get("title", "")).strip()
            raw_snippet = re.sub(r"<[^>]+>", "", str(entry.get("snippet", "")))
            if not title or not raw_snippet.strip():
                continue
            page_url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='/_()-')}"
            raw.append(
                {
                    "title": f"{title} — Wikipedia",
                    "url": page_url,
                    "snippet": raw_snippet,
                    "provider": "wikipedia",
                }
            )
        return self._sanitize_results(raw, default_provider="wikipedia")

    def _search_duckduckgo(self, query: str) -> list[ExternalSearchResult]:
        url = (
            "https://api.duckduckgo.com/?"
            f"q={quote_plus(query)}&format=json&no_html=1&skip_disambig=1"
        )
        data = self._http_get(url, self.timeout, None)
        raw: list[dict[str, Any]] = []
        abstract = str(data.get("AbstractText", "")).strip()
        abstract_url = str(data.get("AbstractURL", "")).strip()
        heading = str(data.get("Heading", "")).strip() or query
        if abstract and is_valid_http_url(abstract_url):
            raw.append(
                {
                    "title": heading,
                    "url": abstract_url,
                    "snippet": abstract,
                    "provider": "duckduckgo",
                }
            )
        related = data.get("RelatedTopics")
        if isinstance(related, list):
            for item in related:
                if isinstance(item, Mapping) and item.get("Text") and item.get("FirstURL"):
                    text = str(item["Text"]).strip()
                    raw.append(
                        {
                            "title": text.split(" - ", 1)[0][:120],
                            "url": str(item["FirstURL"]).strip(),
                            "snippet": text,
                            "provider": "duckduckgo",
                        }
                    )
        return self._sanitize_results(raw, default_provider="duckduckgo")

    def _search_tavily(self, query: str) -> list[ExternalSearchResult]:
        key = os.getenv("TAVILY_API_KEY", "").strip()
        if not key:
            raise RuntimeError("TAVILY_API_KEY is not configured on the server.")
        data = self._http_post(
            "https://api.tavily.com/search",
            {
                "api_key": key,
                "query": query,
                "max_results": self.max_results,
                "include_answer": False,
            },
            self.timeout,
            None,
        )
        items = data.get("results", [])
        if not isinstance(items, list):
            return []
        raw = [
            {
                "title": entry.get("title", ""),
                "url": entry.get("url", ""),
                "snippet": entry.get("content", "") or entry.get("snippet", ""),
                "provider": "tavily",
            }
            for entry in items
            if isinstance(entry, Mapping)
        ]
        return self._sanitize_results(raw, default_provider="tavily")

    def _search_serpapi(self, query: str) -> list[ExternalSearchResult]:
        key = os.getenv("SERPAPI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("SERPAPI_API_KEY is not configured on the server.")
        url = (
            "https://serpapi.com/search.json?"
            f"engine=google&q={quote_plus(query)}&num={self.max_results}&api_key={quote(key, safe='')}"
        )
        data = self._http_get(url, self.timeout, None)
        items = data.get("organic_results", [])
        if not isinstance(items, list):
            return []
        raw = [
            {
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "snippet": entry.get("snippet", ""),
                "provider": "serpapi",
            }
            for entry in items
            if isinstance(entry, Mapping)
        ]
        return self._sanitize_results(raw, default_provider="serpapi")


def create_external_search_tool(
    adapter: ExternalSearchAdapter | None = None,
    *,
    context_messages: Sequence[Mapping[str, Any]] = (),
) -> Callable[[dict[str, Any]], dict[str, Any] | str]:
    """Build a ToolExecutor-compatible external_search tool callable."""
    active_adapter = adapter or ExternalSearchAdapter()

    def _run(arguments: dict[str, Any]) -> dict[str, Any] | str:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Argument 'query' must be a non-empty string.")
        resp = active_adapter.search(
            query,
            context_messages=context_messages,
            raise_on_failure=True,
        )
        if not resp.results:
            return f"{NO_EXTERNAL_EVIDENCE_PREFIX} {resp.generated_query or query}"
        return resp.to_dict()

    return _run
