"""Persistent connector configuration registry with default multi-category catalog seeding."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from connectors.mcp.catalog import BUILTIN_MCP_CATALOG
from connectors.schemas import ConnectorConfig, ConnectorPermissionPolicy, ConnectorToolSpec, utc_now_iso

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _builtin_default_connectors() -> list[dict[str, Any]]:
    """Seed built-in Search, Research, Browser, and MCP Catalog entries cleanly."""
    defaults: list[dict[str, Any]] = [
        # --- Search Providers ---
        {
            "id": "search-wikipedia",
            "name": "Wikipedia Reference Search",
            "category": "search",
            "transport": "rest",
            "url": "https://en.wikipedia.org/w/api.php",
            "auth_type": "none",
            "trust_tier": "tier3",
            "enabled": True,
            "builtin": True,
            "status": "available",
            "description": "Free encyclopedic background lookup via Wikipedia REST/MediaWiki API.",
            "tags": ["search", "wikipedia", "free"],
            "tools": [{"name": "search", "description": "Search Wikipedia reference summaries", "read_only": True}],
        },
        {
            "id": "search-duckduckgo",
            "name": "DuckDuckGo Instant Answer",
            "category": "search",
            "transport": "rest",
            "url": "https://api.duckduckgo.com/",
            "auth_type": "none",
            "trust_tier": "tier4",
            "enabled": True,
            "builtin": True,
            "status": "available",
            "description": "Key-free fallback web entity & instant answer lookup via DuckDuckGo.",
            "tags": ["search", "web", "free"],
            "tools": [{"name": "search", "description": "Search DuckDuckGo instant answers", "read_only": True}],
        },
        {
            "id": "search-tavily",
            "name": "Tavily AI Web Search",
            "category": "search",
            "transport": "rest",
            "url": "https://api.tavily.com/search",
            "auth_type": "api_key",
            "env_var_name": "TAVILY_API_KEY",
            "trust_tier": "tier3",
            "enabled": False,
            "builtin": True,
            "status": "auth_required",
            "description": "Real-time neural search & snippets via Tavily API (requires API key).",
            "tags": ["search", "tavily", "web"],
            "tools": [{"name": "search", "description": "Query Tavily live web search", "read_only": True}],
        },
        {
            "id": "search-serpapi",
            "name": "SerpAPI Web Search",
            "category": "search",
            "transport": "rest",
            "url": "https://serpapi.com/search.json",
            "auth_type": "api_key",
            "env_var_name": "SERPAPI_API_KEY",
            "trust_tier": "tier4",
            "enabled": False,
            "builtin": True,
            "status": "auth_required",
            "description": "Google/organic SERP results via SerpAPI (requires API key).",
            "tags": ["search", "serpapi", "web"],
            "tools": [{"name": "search", "description": "Query SerpAPI organic search results", "read_only": True}],
        },
        # --- Research Sources ---
        {
            "id": "research-arxiv",
            "name": "arXiv Preprint Source",
            "category": "research",
            "transport": "rest",
            "url": "https://export.arxiv.org/api/query",
            "auth_type": "none",
            "trust_tier": "tier2",
            "enabled": True,
            "builtin": True,
            "status": "available",
            "description": "Academic preprint abstracts, dates, and URLs directly from arXiv Atom API.",
            "tags": ["research", "arxiv", "papers"],
            "tools": [{"name": "search_papers", "description": "Search arXiv scholarly preprints", "read_only": True}],
        },
        {
            "id": "research-crossref",
            "name": "Crossref DOI Metadata",
            "category": "research",
            "transport": "rest",
            "url": "https://api.crossref.org/works",
            "auth_type": "none",
            "trust_tier": "tier2",
            "enabled": True,
            "builtin": True,
            "status": "available",
            "description": "DOI, journal, and publication date verification via Crossref API.",
            "tags": ["research", "doi", "citations"],
            "tools": [{"name": "search_papers", "description": "Search Crossref bibliographic works", "read_only": True}],
        },
        {
            "id": "research-openalex",
            "name": "OpenAlex Scholarly Graph",
            "category": "research",
            "transport": "rest",
            "url": "https://api.openalex.org/works",
            "auth_type": "none",
            "trust_tier": "tier2",
            "enabled": True,
            "builtin": True,
            "status": "available",
            "description": "Open scholarly works, venue provenance, and citation counts via OpenAlex.",
            "tags": ["research", "openalex", "citations"],
            "tools": [{"name": "search_papers", "description": "Search OpenAlex scholarly index", "read_only": True}],
        },
        {
            "id": "research-semantic-scholar",
            "name": "Semantic Scholar Graph API",
            "category": "research",
            "transport": "rest",
            "url": "https://api.semanticscholar.org/graph/v1/paper/search",
            "auth_type": "none",
            "env_var_name": "SEMANTIC_SCHOLAR_API_KEY",
            "trust_tier": "tier2",
            "enabled": True,
            "builtin": True,
            "status": "available",
            "description": "Academic papers, abstracts, and citation metrics via Semantic Scholar.",
            "tags": ["research", "papers", "citations"],
            "tools": [{"name": "search_papers", "description": "Search Semantic Scholar abstracts", "read_only": True}],
        },
        # --- Browser Connections ---
        {
            "id": "browser-chromium",
            "name": "Playwright Chromium Engine",
            "category": "browser",
            "transport": "browser_engine",
            "browser_engine": "chromium",
            "auth_type": "none",
            "trust_tier": "tier3",
            "enabled": True,
            "builtin": True,
            "status": "available",
            "description": "Permission-controlled Chromium/Chrome/Edge automation for reading docs, screenshots, and diagnostics.",
            "tags": ["browser", "chromium", "playwright"],
            "tools": [
                {"name": "navigate", "description": "Navigate to an allowlisted HTTPS/HTTP URL", "read_only": True},
                {"name": "read_content", "description": "Extract clean text, headings, links, and metadata", "read_only": True},
                {"name": "inspect_section", "description": "Inspect structured text under a CSS selector", "read_only": True},
                {"name": "search_docs", "description": "Search extracted page text for matching terms", "read_only": True},
                {"name": "screenshot", "description": "Capture viewport screenshot to artifacts", "read_only": True},
                {"name": "diagnostics", "description": "Collect console errors, failed network requests, and timing", "read_only": True},
            ],
            "permissions": {"read_only": True, "allow_downloads": False, "allow_form_submit": False},
        },
        {
            "id": "browser-firefox",
            "name": "Playwright Firefox Engine",
            "category": "browser",
            "transport": "browser_engine",
            "browser_engine": "firefox",
            "auth_type": "none",
            "trust_tier": "tier3",
            "enabled": False,
            "builtin": True,
            "status": "disconnected",
            "description": "Cross-browser Firefox inspection (available when Playwright Firefox or system Firefox is installed).",
            "tags": ["browser", "firefox", "playwright"],
            "tools": [{"name": "navigate", "description": "Navigate with Firefox engine", "read_only": True}],
            "permissions": {"read_only": True, "allow_downloads": False, "allow_form_submit": False},
        },
        {
            "id": "browser-webkit",
            "name": "Playwright WebKit Engine",
            "category": "browser",
            "transport": "browser_engine",
            "browser_engine": "webkit",
            "auth_type": "none",
            "trust_tier": "tier3",
            "enabled": False,
            "builtin": True,
            "status": "disconnected",
            "description": "Safari/WebKit compatibility inspection (requires playwright install webkit).",
            "tags": ["browser", "webkit", "playwright"],
            "tools": [{"name": "navigate", "description": "Navigate with WebKit engine", "read_only": True}],
            "permissions": {"read_only": True, "allow_downloads": False, "allow_form_submit": False},
        },
    ]

    for item in BUILTIN_MCP_CATALOG:
        entry = dict(item)
        entry.setdefault("enabled", False)
        entry.setdefault("builtin", True)
        entry.setdefault("status", "auth_required" if entry.get("auth_type", "none") != "none" else "disconnected")
        defaults.append(entry)
    return defaults


class ConnectorRegistry:
    """Manages persistent storage and CRUD for all TraceRecover connectors."""

    def __init__(self, storage_dir: Path | str) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.storage_dir / "connectors.json"
        self._connectors: dict[str, ConnectorConfig] = {}
        self._load_or_seed()

    def _load_or_seed(self) -> None:
        if self.registry_path.exists():
            try:
                raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    for item in raw:
                        cfg = ConnectorConfig.from_dict(item)
                        if cfg.id:
                            self._connectors[cfg.id] = cfg
                    if self._connectors:
                        return
            except Exception:
                pass
        for item in _builtin_default_connectors():
            cfg = ConnectorConfig.from_dict(item)
            self._connectors[cfg.id] = cfg
        self._save()

    def _save(self) -> None:
        data = [cfg.to_dict() for cfg in self._connectors.values()]
        self.registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list(self, category: str | None = None) -> list[ConnectorConfig]:
        items = list(self._connectors.values())
        if category and category != "all":
            items = [c for c in items if c.category == category]
        return items

    def get(self, connector_id: str) -> ConnectorConfig | None:
        return self._connectors.get(connector_id)

    def upsert(self, cfg: ConnectorConfig) -> ConnectorConfig:
        cfg.updated_at = utc_now_iso()
        self._connectors[cfg.id] = cfg
        self._save()
        return cfg

    def create(self, payload: dict[str, Any]) -> ConnectorConfig:
        raw_id = str(payload.get("id") or payload.get("name") or "connector").strip().lower()
        slug = _SLUG_RE.sub("-", raw_id).strip("-") or "connector"
        cid = slug
        suffix = 1
        while cid in self._connectors:
            suffix += 1
            cid = f"{slug}-{suffix}"

        data = dict(payload)
        data["id"] = cid
        data.setdefault("created_at", utc_now_iso())
        data["updated_at"] = utc_now_iso()
        cfg = ConnectorConfig.from_dict(data)
        self._connectors[cid] = cfg
        self._save()
        return cfg

    def update(self, connector_id: str, patch: dict[str, Any]) -> ConnectorConfig | None:
        cfg = self._connectors.get(connector_id)
        if cfg is None:
            return None
        for key in (
            "name",
            "category",
            "transport",
            "description",
            "enabled",
            "command",
            "args",
            "url",
            "headers",
            "auth_type",
            "env_var_name",
            "secret_ref",
            "browser_engine",
            "trust_tier",
            "tags",
            "metadata",
            "status",
            "last_error",
            "last_tested_at",
        ):
            if key in patch and patch[key] is not None:
                setattr(cfg, key, patch[key])
        if "permissions" in patch and isinstance(patch["permissions"], dict):
            cfg.permissions = ConnectorPermissionPolicy.from_dict(patch["permissions"])
        if "tools" in patch and isinstance(patch["tools"], list):
            cfg.tools = [ConnectorToolSpec.from_dict(t) for t in patch["tools"]]
        cfg.updated_at = utc_now_iso()
        self._save()
        return cfg

    def delete(self, connector_id: str) -> bool:
        if connector_id not in self._connectors:
            return False
        del self._connectors[connector_id]
        self._save()
        return True
