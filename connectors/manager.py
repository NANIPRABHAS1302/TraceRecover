"""Central ConnectorManager coordinating MCP, Browser, Search, Research, Custom API, permissions, vault, and agent tools."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from config import PROJECT_ROOT
from connectors.api.custom_api import CustomAPIConnector
from connectors.browser.engine import BrowserIntelligenceManager
from connectors.health import HealthMonitor
from connectors.mcp.client import MCPClientAdapter
from connectors.permissions import PermissionEngine, SecretVault, mask_secret, redact_secrets
from connectors.registry import ConnectorRegistry
from connectors.schemas import (
    ConnectorConfig,
    ConnectorExecutionRecord,
    ConnectorStatus,
    ConnectorTestResult,
    utc_now_iso,
)
from connectors.search.providers import SearchProviderHub
from connectors.sources.registry import SourceRegistry


class ConnectorManager:
    """Facade managing external connectors, security boundaries, execution logs, and dynamic ToolExecutor wiring."""

    def __init__(self, storage_dir: Path | str | None = None) -> None:
        base = Path(storage_dir) if storage_dir else Path(os.environ.get("TRACE_CONNECTORS_DIR", PROJECT_ROOT / "data" / "connectors"))
        self.storage_dir = base
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.logs_path = self.storage_dir / "execution_logs.jsonl"
        self.artifacts_dir = self.storage_dir / "browser_artifacts"

        self.registry = ConnectorRegistry(self.storage_dir)
        self.vault = SecretVault(self.storage_dir)
        self.permissions = PermissionEngine()
        self.health = HealthMonitor()
        self.sources = SourceRegistry()
        self.search_hub = SearchProviderHub(self.vault, self.sources)
        self.browser = BrowserIntelligenceManager(self.permissions, self.artifacts_dir)
        self.mcp_client = MCPClientAdapter(self.vault, self.permissions)
        self.custom_api = CustomAPIConnector(self.permissions, self.vault)

    def sanitize_connector(self, cfg: ConnectorConfig) -> dict[str, Any]:
        """Serialize connector metadata without ever exposing plaintext credentials."""
        payload = cfg.to_dict()
        secret = self.vault.resolve_secret(cfg)
        payload["secret_configured"] = bool(secret)
        payload["secret_preview"] = mask_secret(secret) if secret else ""
        payload["health"] = self.health.get(cfg.id, cfg.status).to_dict()
        if "setup_hint" not in (payload.get("metadata") or {}):
            payload.setdefault("metadata", {})["setup_hint"] = ""
        # Mask any accidental token placed inside headers
        safe_headers: dict[str, str] = {}
        for k, v in (payload.get("headers") or {}).items():
            if any(marker in k.lower() for marker in ("auth", "key", "token", "secret")):
                safe_headers[k] = mask_secret(v)
            else:
                safe_headers[k] = redact_secrets(v)
        payload["headers"] = safe_headers
        return payload

    def list_connectors(self, category: str | None = None) -> list[dict[str, Any]]:
        browser_engines = {e["engine"]: e for e in self.browser.detect_engines()}
        items: list[dict[str, Any]] = []
        for cfg in self.registry.list(category=category):
            if cfg.category == "browser" and cfg.browser_engine in browser_engines:
                eng = browser_engines[cfg.browser_engine]
                if not eng["available"] and cfg.status in ("available", "connected"):
                    cfg.status = ConnectorStatus.UNAVAILABLE.value
                    cfg.last_error = eng["reason"]
                elif eng["available"] and cfg.enabled and cfg.status == ConnectorStatus.DISCONNECTED.value:
                    cfg.status = ConnectorStatus.AVAILABLE.value
            items.append(self.sanitize_connector(cfg))
        return items

    def get_connector(self, connector_id: str) -> dict[str, Any] | None:
        cfg = self.registry.get(connector_id)
        return self.sanitize_connector(cfg) if cfg else None

    def overview(self) -> dict[str, Any]:
        all_items = self.list_connectors()
        by_category: dict[str, int] = {}
        by_status: dict[str, int] = {}
        enabled_count = 0
        for item in all_items:
            cat = item.get("category", "other")
            st = item.get("status", "disconnected")
            by_category[cat] = by_category.get(cat, 0) + 1
            by_status[st] = by_status.get(st, 0) + 1
            if item.get("enabled"):
                enabled_count += 1
        return {
            "total_connectors": len(all_items),
            "enabled_connectors": enabled_count,
            "by_category": by_category,
            "by_status": by_status,
            "browser_engines": self.browser.detect_engines(),
            "trust_tiers": self.sources.tier_catalog(),
            "vault_posture": self.vault.security_posture(),
            "health": self.health.summary(),
            "recent_executions": len(self.get_execution_logs(limit=100)),
        }

    def add_connector(self, payload: dict[str, Any]) -> dict[str, Any]:
        secret_value = str(payload.pop("secret_value", "") or "").strip()
        cfg = self.registry.create(payload)
        if secret_value:
            cfg.secret_ref = self.vault.set_secret(cfg.id, secret_value)
            self.registry.upsert(cfg)
        return self.sanitize_connector(cfg)

    def edit_connector(self, connector_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        secret_value = patch.pop("secret_value", None)
        clear_secret = bool(patch.pop("clear_secret", False))
        cfg = self.registry.update(connector_id, patch)
        if cfg is None:
            return None
        if clear_secret:
            self.vault.delete_secret(connector_id)
            cfg.secret_ref = ""
            self.registry.upsert(cfg)
        elif isinstance(secret_value, str) and secret_value.strip():
            cfg.secret_ref = self.vault.set_secret(connector_id, secret_value)
            if cfg.status == ConnectorStatus.AUTH_REQUIRED.value:
                cfg.status = ConnectorStatus.DISCONNECTED.value
            self.registry.upsert(cfg)
        return self.sanitize_connector(cfg)

    def remove_connector(self, connector_id: str, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return {"ok": False, "error": "Explicit confirmation required before removing a connector."}
        cfg = self.registry.get(connector_id)
        if cfg is None:
            return {"ok": False, "error": f"Connector '{connector_id}' not found."}
        self.vault.delete_secret(connector_id)
        deleted = self.registry.delete(connector_id)
        return {"ok": deleted, "connector_id": connector_id}

    def set_enabled(self, connector_id: str, enabled: bool) -> dict[str, Any] | None:
        cfg = self.registry.get(connector_id)
        if cfg is None:
            return None
        cfg.enabled = enabled
        if not enabled:
            cfg.status = ConnectorStatus.DISABLED.value
        else:
            if cfg.auth_type != "none" and not self.vault.has_secret(cfg):
                cfg.status = ConnectorStatus.AUTH_REQUIRED.value
            elif cfg.transport == "rest" or cfg.category in ("search", "research"):
                cfg.status = ConnectorStatus.AVAILABLE.value
            else:
                cfg.status = ConnectorStatus.DISCONNECTED.value
        self.registry.upsert(cfg)
        return self.sanitize_connector(cfg)

    def disconnect_connector(self, connector_id: str) -> dict[str, Any] | None:
        cfg = self.registry.get(connector_id)
        if cfg is None:
            return None
        cfg.enabled = False
        cfg.status = ConnectorStatus.DISCONNECTED.value
        cfg.last_error = ""
        self.registry.upsert(cfg)
        self.health.record_check(cfg, ok=True, status="disconnected", detail="Disconnected cleanly by user.")
        return self.sanitize_connector(cfg)

    def test_connector(self, connector_id: str) -> dict[str, Any] | None:
        cfg = self.registry.get(connector_id)
        if cfg is None:
            return None

        started = time.perf_counter()
        result: ConnectorTestResult

        if cfg.transport in ("stdio", "streamable_http", "sse"):
            result = self.mcp_client.discover_sync(cfg)
            if result.ok and result.discovered_tools:
                cfg.tools = result.discovered_tools
                cfg.resources = result.discovered_resources
                cfg.prompts = result.discovered_prompts
        elif cfg.category == "browser" and cfg.transport == "browser_engine":
            eng = next((e for e in self.browser.detect_engines() if e["engine"] == cfg.browser_engine), None)
            ok = bool(eng and eng["available"])
            result = ConnectorTestResult(
                connector_id=cfg.id,
                ok=ok,
                status="connected" if ok else "unavailable",
                message=eng["reason"] if eng else "Browser engine unavailable.",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                discovered_tools=cfg.tools,
                auth_configured=True,
                details=eng or {},
            )
        elif cfg.category == "custom_api":
            res = self.custom_api.test_endpoint(cfg)
            result = ConnectorTestResult(
                connector_id=cfg.id,
                ok=bool(res.get("ok")),
                status=str(res.get("status", "error")),
                message=str(res.get("message", "")),
                latency_ms=float(res.get("latency_ms") or ((time.perf_counter() - started) * 1000.0)),
                auth_configured=bool(self.vault.has_secret(cfg) or cfg.auth_type == "none"),
                details=res,
            )
        else:
            # Built-in search or research REST adapter: execute a real lightweight connectivity probe
            if cfg.auth_type != "none" and not self.vault.has_secret(cfg):
                result = ConnectorTestResult(
                    connector_id=cfg.id,
                    ok=False,
                    status="auth_required",
                    message=f"Missing required credential ({cfg.env_var_name or cfg.auth_type}).",
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    auth_configured=False,
                )
            else:
                probe_url = self._probe_url_for_builtin(cfg)
                headers = {"User-Agent": "TraceRecoverResearchAgent/1.0 (https://tracerecover.local; contact=research@tracerecover.local)"}
                try:
                    with httpx.Client(timeout=min(cfg.permissions.timeout_seconds, 8.0), follow_redirects=True) as client:
                        resp = client.get(probe_url, headers=headers)
                    latency = (time.perf_counter() - started) * 1000.0
                    ok = 200 <= resp.status_code < 400
                    result = ConnectorTestResult(
                        connector_id=cfg.id,
                        ok=ok,
                        status="connected" if ok else "error",
                        message=f"Live probe HTTP {resp.status_code} at {probe_url}" if ok else f"Endpoint HTTP {resp.status_code}",
                        latency_ms=latency,
                        discovered_tools=cfg.tools,
                        auth_configured=True,
                        details={"http_status": resp.status_code, "probe_url": probe_url},
                    )
                except Exception as exc:
                    latency = (time.perf_counter() - started) * 1000.0
                    result = ConnectorTestResult(
                        connector_id=cfg.id,
                        ok=False,
                        status="unavailable",
                        message=f"Connectivity probe failed: {redact_secrets(str(exc), self.vault.all_known_secrets())}",
                        latency_ms=latency,
                        auth_configured=True,
                        details={"probe_url": probe_url},
                    )

        cfg.status = result.status
        cfg.last_tested_at = utc_now_iso()
        cfg.last_error = "" if result.ok else result.message
        if result.ok:
            cfg.enabled = True
        self.registry.upsert(cfg)
        self.health.record_check(
            cfg,
            ok=result.ok,
            status=result.status,
            latency_ms=result.latency_ms,
            detail=result.message,
        )
        return {"test": result.to_dict(), "connector": self.sanitize_connector(cfg)}

    @staticmethod
    def _probe_url_for_builtin(cfg: ConnectorConfig) -> str:
        probes = {
            "search-wikipedia": "https://en.wikipedia.org/w/api.php?action=query&meta=siteinfo&format=json",
            "search-duckduckgo": "https://api.duckduckgo.com/?q=tracerecover&format=json",
            "research-arxiv": "https://export.arxiv.org/api/query?search_query=all:llm&max_results=1",
            "research-crossref": "https://api.crossref.org/works?rows=1",
            "research-openalex": "https://api.openalex.org/works?per-page=1",
            "research-semantic-scholar": "https://api.semanticscholar.org/graph/v1/paper/search?query=llm&limit=1",
        }
        return probes.get(cfg.id, cfg.url or "https://example.com")

    def connect_connector(self, connector_id: str) -> dict[str, Any] | None:
        cfg = self.registry.get(connector_id)
        if cfg is None:
            return None
        cfg.enabled = True
        self.registry.upsert(cfg)
        return self.test_connector(connector_id)

    def _append_log(self, rec: ConnectorExecutionRecord) -> None:
        try:
            with self.logs_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_dict()) + "\n")
        except Exception:
            pass

    def get_execution_logs(self, limit: int = 50, connector_id: str | None = None) -> list[dict[str, Any]]:
        if not self.logs_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.logs_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if connector_id and item.get("connector_id") != connector_id:
                    continue
                rows.append(item)
            except Exception:
                continue
        return list(reversed(rows[-limit:]))

    def execute_connector_tool(
        self,
        connector_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        approved_actions: set[str] | None = None,
    ) -> dict[str, Any]:
        cfg = self.registry.get(connector_id)
        if cfg is None:
            return {"ok": False, "error": f"Unknown connector '{connector_id}'."}

        started = time.perf_counter()
        secrets = self.vault.all_known_secrets()
        args_preview = redact_secrets(json.dumps(arguments or {}, default=str)[:300], secrets)

        allowed, reason, requires_approval = self.permissions.check_execution(
            cfg,
            tool_name,
            arguments,
            approved_actions=approved_actions,
        )
        if not allowed:
            perm_status = "approval_required" if requires_approval else "denied"
            self._append_log(
                ConnectorExecutionRecord(
                    timestamp=utc_now_iso(),
                    connector_id=cfg.id,
                    connector_name=cfg.name,
                    category=cfg.category,
                    tool_name=tool_name,
                    ok=False,
                    latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
                    permission_status=perm_status,
                    arguments_preview=args_preview,
                    result_preview="",
                    error=reason,
                )
            )
            return {
                "ok": False,
                "blocked": True,
                "requires_approval": requires_approval,
                "permission_status": perm_status,
                "error": reason,
            }

        args = arguments or {}
        if cfg.transport in ("stdio", "streamable_http", "sse"):
            res = self.mcp_client.call_tool_sync(cfg, tool_name, args)
        elif cfg.category == "browser" and cfg.transport == "browser_engine":
            res = self.browser.execute_sync(
                action=tool_name or "read_content",
                url=str(args.get("url") or ""),
                engine=cfg.browser_engine or "chromium",
                connector=cfg,
                selector=str(args.get("selector") or ""),
                query=str(args.get("query") or ""),
                approved_actions=approved_actions,
            )
        elif cfg.category == "custom_api":
            res = self.custom_api.call(
                cfg,
                method=str(args.get("method") or "GET"),
                path=str(args.get("path") or ""),
                params=args.get("params"),
                json_body=args.get("json_body"),
                approved_actions=approved_actions,
            )
        elif cfg.id == "search-wikipedia":
            res = self.search_hub.search_wikipedia(str(args.get("query") or ""))
        elif cfg.id == "search-duckduckgo":
            res = self.search_hub.search_duckduckgo(str(args.get("query") or ""))
        elif cfg.id == "search-tavily":
            res = self.search_hub.search_tavily(cfg, str(args.get("query") or ""))
        elif cfg.id == "search-serpapi":
            res = self.search_hub.search_serpapi(cfg, str(args.get("query") or ""))
        elif cfg.id in ("research-arxiv", "research-crossref", "research-openalex", "research-semantic-scholar"):
            q = str(args.get("query") or "")
            try:
                if cfg.id == "research-arxiv":
                    docs = self.sources.search_arxiv(q)
                elif cfg.id == "research-crossref":
                    docs = self.sources.search_crossref(q)
                elif cfg.id == "research-openalex":
                    docs = self.sources.search_openalex(q)
                else:
                    docs = self.sources.search_semantic_scholar(q, api_key=self.vault.resolve_secret(cfg))
                res = {"ok": True, **self.sources.aggregate_and_rank(q, docs)}
            except Exception as exc:
                res = {
                    "ok": False,
                    "status": "unavailable",
                    "error": redact_secrets(str(exc), secrets),
                    "documents": [],
                }
        else:
            res = {"ok": False, "error": f"No runtime execution handler for '{cfg.id}' ({cfg.transport})."}

        latency = round((time.perf_counter() - started) * 1000.0, 2)
        preview = redact_secrets(json.dumps(res, default=str)[:400], secrets)
        err_msg = redact_secrets(str(res.get("error") or ""), secrets)
        self.health.record_check(
            cfg,
            ok=bool(res.get("ok")),
            status="connected" if res.get("ok") else str(res.get("status") or "error"),
            latency_ms=latency,
            detail=err_msg or "ok",
        )
        self._append_log(
            ConnectorExecutionRecord(
                timestamp=utc_now_iso(),
                connector_id=cfg.id,
                connector_name=cfg.name,
                category=cfg.category,
                tool_name=tool_name,
                ok=bool(res.get("ok")),
                latency_ms=latency,
                permission_status="allowed",
                arguments_preview=args_preview,
                result_preview=preview,
                error=err_msg,
            )
        )
        return res

    def query_research_sources(self, query: str, provider_ids: list[str] | None = None, max_results: int = 4) -> dict[str, Any]:
        """Run multi-source scholarly/web search across enabled sources, deduplicating & cross-checking tiers."""
        targets = provider_ids or ["research-arxiv", "research-crossref", "research-openalex", "search-wikipedia"]
        collected_docs = []
        provider_errors: dict[str, str] = {}
        used_providers: list[str] = []

        for cid in targets:
            cfg = self.registry.get(cid)
            if cfg is None or not cfg.enabled:
                continue
            used_providers.append(cid)
            try:
                if cid == "research-arxiv":
                    collected_docs.extend(self.sources.search_arxiv(query, max_results=max_results))
                elif cid == "research-crossref":
                    collected_docs.extend(self.sources.search_crossref(query, max_results=max_results))
                elif cid == "research-openalex":
                    collected_docs.extend(self.sources.search_openalex(query, max_results=max_results))
                elif cid == "research-semantic-scholar":
                    collected_docs.extend(self.sources.search_semantic_scholar(query, max_results=max_results, api_key=self.vault.resolve_secret(cfg)))
                elif cid == "search-wikipedia":
                    collected_docs.extend(self.sources.search_wikipedia(query, max_results=max_results))
                elif cid == "search-duckduckgo":
                    ddg = self.search_hub.search_duckduckgo(query, max_results=max_results)
                    if not ddg.get("ok"):
                        provider_errors[cid] = str(ddg.get("error") or "unavailable")
                    else:
                        from connectors.schemas import SourceDocument

                        for d in ddg.get("documents") or []:
                            collected_docs.append(SourceDocument(**d))
            except Exception as exc:
                provider_errors[cid] = redact_secrets(str(exc), self.vault.all_known_secrets())

        ranked = self.sources.aggregate_and_rank(query, collected_docs)
        ranked["providers_queried"] = used_providers
        ranked["provider_errors"] = provider_errors
        return ranked

    @staticmethod
    def _unpack_tool_args(arguments: Any = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(arguments) if isinstance(arguments, dict) else {}
        if extra:
            merged.update(extra)
        return merged

    def register_agent_tools(self, executor: Any, *, approved_actions: set[str] | None = None) -> list[str]:
        """Dynamically bind enabled connected external tools into a ToolExecutor safely."""
        registered: list[str] = []
        for cfg in self.registry.list():
            if not cfg.enabled or cfg.status in ("unavailable", "auth_required", "disabled"):
                continue
            # Skip built-in search providers already covered by ExternalSearchAdapter unless custom/mcp/browser/api
            if cfg.category in ("mcp", "developer", "productivity", "infrastructure", "custom_api") and cfg.transport in ("stdio", "streamable_http", "sse", "rest"):
                if cfg.category == "custom_api":
                    tname = f"connector_{cfg.id.replace('-', '_')}_call"

                    def _make_api_fn(cid: str) -> Any:
                        def _call(arguments: Any = None, **kwargs: Any) -> dict[str, Any]:
                            args = self._unpack_tool_args(arguments, kwargs)
                            res = self.execute_connector_tool(
                                cid,
                                "call",
                                {"path": str(args.get("path", "")), "method": str(args.get("method", "GET"))},
                                approved_actions=approved_actions,
                            )
                            if not res.get("ok"):
                                raise RuntimeError(str(res.get("error") or f"Custom API connector '{cid}' failed."))
                            return res

                        return _call

                    executor.register(tname, _make_api_fn(cfg.id), description=f"Call custom API connector {cfg.name}")
                    registered.append(tname)
                elif cfg.tools:
                    for tspec in cfg.tools:
                        tname = f"mcp_{cfg.id.replace('-', '_')}__{tspec.name}"

                        def _make_mcp_fn(cid: str, raw_tool: str) -> Any:
                            def _call(arguments: Any = None, **kwargs: Any) -> dict[str, Any]:
                                args = self._unpack_tool_args(arguments, kwargs)
                                res = self.execute_connector_tool(
                                    cid,
                                    raw_tool,
                                    args,
                                    approved_actions=approved_actions,
                                )
                                if not res.get("ok"):
                                    raise RuntimeError(str(res.get("error") or f"MCP tool '{raw_tool}' failed."))
                                return res

                            return _call

                        executor.register(tname, _make_mcp_fn(cfg.id, tspec.name), description=tspec.description or cfg.description)
                        registered.append(tname)
            elif cfg.category == "browser" and cfg.browser_engine == "chromium":
                tname = "browser_inspect"

                def _browser_fn(arguments: Any = None, **kwargs: Any) -> dict[str, Any]:
                    args = self._unpack_tool_args(arguments, kwargs)
                    res = self.execute_connector_tool(
                        cfg.id,
                        str(args.get("action") or "read_content"),
                        {"url": str(args.get("url") or ""), "selector": str(args.get("selector") or "")},
                        approved_actions=approved_actions,
                    )
                    if not res.get("ok"):
                        raise RuntimeError(str(res.get("error") or "Browser inspection failed."))
                    return res

                executor.register(tname, _browser_fn, required_fields={"url": str}, description="Permission-controlled read-only browser inspection")
                registered.append(tname)
        return registered
