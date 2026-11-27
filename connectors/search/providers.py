"""Search provider implementations with honest key checks and trust-tiered citation normalization."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

import httpx

from connectors.permissions import SecretVault
from connectors.schemas import ConnectorConfig, SourceDocument
from connectors.sources.registry import SourceRegistry, classify_url_tier


class SearchProviderHub:
    """Executes real queries against configured search providers without inventing URLs."""

    def __init__(self, vault: SecretVault, registry: SourceRegistry | None = None, timeout: float = 10.0) -> None:
        self.vault = vault
        self.registry = registry or SourceRegistry(timeout=timeout)
        self.timeout = timeout

    def search_tavily(self, connector: ConnectorConfig, query: str, max_results: int = 5) -> dict[str, Any]:
        api_key = self.vault.resolve_secret(connector)
        if not api_key:
            return {
                "ok": False,
                "status": "auth_required",
                "provider": "tavily",
                "error": "Tavily API key not configured in encrypted vault or environment.",
                "documents": [],
            }
        try:
            with httpx.Client(timeout=connector.permissions.timeout_seconds) as client:
                resp = client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": api_key, "query": query, "max_results": max_results},
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            return {"ok": False, "status": "error", "provider": "tavily", "error": str(exc), "documents": []}

        docs: list[SourceDocument] = []
        for item in payload.get("results") or []:
            url = str(item.get("url") or "").strip()
            if not url.startswith("http"):
                continue
            tier, host = classify_url_tier(url, fallback="tier3")
            docs.append(
                SourceDocument(
                    title=str(item.get("title") or url),
                    url=url,
                    snippet=str(item.get("content") or "")[:500],
                    provider="tavily",
                    trust_tier=tier,
                    domain=host,
                    relevance_score=float(item.get("score") or 0.7),
                )
            )
        ranked = self.registry.aggregate_and_rank(query, docs)
        return {"ok": True, "status": "connected", "provider": "tavily", **ranked}

    def search_serpapi(self, connector: ConnectorConfig, query: str, max_results: int = 5) -> dict[str, Any]:
        api_key = self.vault.resolve_secret(connector)
        if not api_key:
            return {
                "ok": False,
                "status": "auth_required",
                "provider": "serpapi",
                "error": "SerpAPI key not configured in encrypted vault or environment.",
                "documents": [],
            }
        try:
            with httpx.Client(timeout=connector.permissions.timeout_seconds) as client:
                resp = client.get(
                    "https://serpapi.com/search.json",
                    params={"q": query, "api_key": api_key, "num": max_results},
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            return {"ok": False, "status": "error", "provider": "serpapi", "error": str(exc), "documents": []}

        docs: list[SourceDocument] = []
        for item in payload.get("organic_results") or []:
            url = str(item.get("link") or "").strip()
            if not url.startswith("http"):
                continue
            tier, host = classify_url_tier(url, fallback="tier4")
            docs.append(
                SourceDocument(
                    title=str(item.get("title") or url),
                    url=url,
                    snippet=str(item.get("snippet") or "")[:500],
                    provider="serpapi",
                    trust_tier=tier,
                    domain=host,
                    published_at=str(item.get("date") or ""),
                    relevance_score=0.72,
                )
            )
        ranked = self.registry.aggregate_and_rank(query, docs)
        return {"ok": True, "status": "connected", "provider": "serpapi", **ranked}

    def search_wikipedia(self, query: str, max_results: int = 4) -> dict[str, Any]:
        try:
            docs = self.registry.search_wikipedia(query, max_results=max_results)
            ranked = self.registry.aggregate_and_rank(query, docs)
            return {"ok": True, "status": "connected", "provider": "wikipedia", **ranked}
        except Exception as exc:
            return {"ok": False, "status": "error", "provider": "wikipedia", "error": str(exc), "documents": []}

    def search_duckduckgo(self, query: str, max_results: int = 5) -> dict[str, Any]:
        url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_html=1&skip_disambig=1"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": "TraceRecover/1.0"})
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            return {"ok": False, "status": "error", "provider": "duckduckgo", "error": str(exc), "documents": []}

        docs: list[SourceDocument] = []
        abstract_url = str(payload.get("AbstractURL") or "").strip()
        if abstract_url.startswith("http") and payload.get("AbstractText"):
            tier, host = classify_url_tier(abstract_url, fallback="tier3")
            docs.append(
                SourceDocument(
                    title=str(payload.get("Heading") or query),
                    url=abstract_url,
                    snippet=str(payload.get("AbstractText"))[:500],
                    provider="duckduckgo",
                    trust_tier=tier,
                    domain=host,
                    relevance_score=0.72,
                )
            )
        for topic in payload.get("RelatedTopics") or []:
            if len(docs) >= max_results:
                break
            first_url = str(topic.get("FirstURL") or "").strip()
            text = str(topic.get("Text") or "").strip()
            if first_url.startswith("http") and text:
                tier, host = classify_url_tier(first_url, fallback="tier4")
                docs.append(
                    SourceDocument(
                        title=text.split(" - ")[0][:120],
                        url=first_url,
                        snippet=text[:500],
                        provider="duckduckgo",
                        trust_tier=tier,
                        domain=host,
                        relevance_score=0.62,
                    )
                )
        ranked = self.registry.aggregate_and_rank(query, docs)
        return {"ok": True, "status": "connected", "provider": "duckduckgo", **ranked}
