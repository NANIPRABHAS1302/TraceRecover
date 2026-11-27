"""Official Model Context Protocol (MCP) SDK client for stdio and remote HTTP/SSE transports."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from connectors.permissions import PermissionEngine, SecretVault, redact_secrets
from connectors.schemas import ConnectorConfig, ConnectorTestResult, ConnectorToolSpec

_MCP_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tracerecover-mcp")

_MUTATING_PREFIXES = ("create", "write", "update", "delete", "remove", "push", "post", "drop", "execute", "insert", "stop", "upload", "submit")


def _classify_tool(name: str, description: str, input_schema: dict[str, Any], annotations: Any = None) -> ConnectorToolSpec:
    lowered = (name or "").lower()
    read_only_hint = getattr(annotations, "readOnlyHint", None) if annotations is not None else None
    if read_only_hint is not None:
        is_read_only = bool(read_only_hint)
    else:
        is_read_only = not any(lowered.startswith(prefix) or f"_{prefix}" in lowered for prefix in _MUTATING_PREFIXES)
    return ConnectorToolSpec(
        name=name,
        description=description or "",
        input_schema=dict(input_schema or {}),
        read_only=is_read_only,
        requires_approval=not is_read_only,
    )


def _format_mcp_content(result: Any) -> str:
    content = getattr(result, "content", None) or []
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
        elif isinstance(block, dict) and "text" in block:
            parts.append(str(block["text"]))
        else:
            parts.append(str(block))
    if not parts and getattr(result, "structuredContent", None) is not None:
        parts.append(str(result.structuredContent))
    return "\n".join(parts).strip()


class MCPClientAdapter:
    """Real MCP SDK client implementing initialize, list_tools/resources/prompts, call_tool, and read_resource."""

    def __init__(self, vault: SecretVault, permission_engine: PermissionEngine) -> None:
        self.vault = vault
        self.permissions = permission_engine

    def _build_stdio_env(self, connector: ConnectorConfig) -> dict[str, str]:
        env = dict(os.environ)
        secret = self.vault.resolve_secret(connector)
        if secret and connector.env_var_name:
            env[connector.env_var_name.strip()] = secret
        return env

    def _build_http_headers(self, connector: ConnectorConfig) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in (connector.headers or {}).items()}
        secret = self.vault.resolve_secret(connector)
        if secret:
            if connector.auth_type in ("bearer", "oauth_token", "api_key"):
                headers.setdefault("Authorization", f"Bearer {secret}")
        return headers

    def _validate_prerequisites(self, connector: ConnectorConfig) -> ConnectorTestResult | None:
        secret = self.vault.resolve_secret(connector)
        if connector.auth_type != "none" and not secret:
            hint = connector.env_var_name or connector.auth_type
            return ConnectorTestResult(
                connector_id=connector.id,
                ok=False,
                status="auth_required",
                auth_configured=False,
                message=f"Authentication required ({hint}). Configure a secret in Connect Hub vault or environment.",
            )
        if connector.transport == "stdio":
            cmd = (connector.command or "").strip()
            if not cmd:
                return ConnectorTestResult(
                    connector_id=connector.id,
                    ok=False,
                    status="error",
                    auth_configured=bool(secret or connector.auth_type == "none"),
                    message="Missing stdio command executable.",
                )
            resolved_cmd = cmd if os.path.exists(cmd) else (shutil.which(cmd) or "")
            if not resolved_cmd:
                return ConnectorTestResult(
                    connector_id=connector.id,
                    ok=False,
                    status="unavailable",
                    auth_configured=bool(secret or connector.auth_type == "none"),
                    message=f"Executable '{cmd}' was not found on system PATH.",
                )
        elif connector.transport in ("streamable_http", "sse"):
            url = (connector.url or "").strip()
            if not url:
                return ConnectorTestResult(
                    connector_id=connector.id,
                    ok=False,
                    status="error",
                    auth_configured=bool(secret or connector.auth_type == "none"),
                    message="Remote MCP endpoint URL is not configured.",
                )
            ok_url, reason = self.permissions.check_url(connector, url)
            if not ok_url:
                return ConnectorTestResult(
                    connector_id=connector.id,
                    ok=False,
                    status="error",
                    auth_configured=bool(secret or connector.auth_type == "none"),
                    message=reason,
                )
        else:
            return ConnectorTestResult(
                connector_id=connector.id,
                ok=False,
                status="error",
                message=f"Unsupported MCP transport '{connector.transport}'.",
            )
        return None

    @asynccontextmanager
    async def _open_session(self, connector: ConnectorConfig) -> AsyncIterator[ClientSession]:
        timeout = float(connector.permissions.timeout_seconds or 20.0)
        if connector.transport == "stdio":
            params = StdioServerParameters(
                command=connector.command.strip(),
                args=list(connector.args or []),
                env=self._build_stdio_env(connector),
            )
            async with stdio_client(params) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream, read_timeout_seconds=timeout) as session:
                    await asyncio.wait_for(session.initialize(), timeout=timeout)
                    yield session
            return

        headers = self._build_http_headers(connector)
        if connector.transport == "sse":
            async with sse_client(connector.url.strip(), headers=headers or None, timeout=timeout) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream, read_timeout_seconds=timeout) as session:
                    await asyncio.wait_for(session.initialize(), timeout=timeout)
                    yield session
            return

        # streamable_http
        http_client = None
        try:
            import httpx2

            if headers:
                http_client = httpx2.AsyncClient(headers=headers, timeout=timeout)
        except Exception:
            http_client = None

        try:
            async with streamable_http_client(connector.url.strip(), http_client=http_client) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream, read_timeout_seconds=timeout) as session:
                    await asyncio.wait_for(session.initialize(), timeout=timeout)
                    yield session
        finally:
            if http_client is not None:
                await http_client.aclose()

    async def discover_async(self, connector: ConnectorConfig) -> ConnectorTestResult:
        prereq_fail = self._validate_prerequisites(connector)
        if prereq_fail is not None:
            return prereq_fail

        started = time.perf_counter()
        timeout = float(connector.permissions.timeout_seconds or 20.0)
        try:
            async with self._open_session(connector) as session:
                tools_resp = await asyncio.wait_for(session.list_tools(), timeout=timeout)
                discovered_tools = [
                    _classify_tool(
                        t.name,
                        getattr(t, "description", "") or "",
                        getattr(t, "inputSchema", {}) or {},
                        getattr(t, "annotations", None),
                    )
                    for t in (getattr(tools_resp, "tools", None) or [])
                ]
                resources: list[dict[str, Any]] = []
                prompts: list[dict[str, Any]] = []
                try:
                    res_resp = await asyncio.wait_for(session.list_resources(), timeout=min(timeout, 5.0))
                    resources = [
                        {"uri": str(r.uri), "name": getattr(r, "name", str(r.uri)), "description": getattr(r, "description", "")}
                        for r in (getattr(res_resp, "resources", None) or [])
                    ]
                except Exception:
                    pass
                try:
                    prm_resp = await asyncio.wait_for(session.list_prompts(), timeout=min(timeout, 5.0))
                    prompts = [
                        {"name": p.name, "description": getattr(p, "description", "")}
                        for p in (getattr(prm_resp, "prompts", None) or [])
                    ]
                except Exception:
                    pass

            latency = (time.perf_counter() - started) * 1000.0
            return ConnectorTestResult(
                connector_id=connector.id,
                ok=True,
                status="connected",
                message=f"Connected via MCP ({connector.transport}); discovered {len(discovered_tools)} tool(s).",
                latency_ms=latency,
                discovered_tools=discovered_tools,
                discovered_resources=resources,
                discovered_prompts=prompts,
                auth_configured=True,
            )
        except Exception as exc:
            latency = (time.perf_counter() - started) * 1000.0
            msg = redact_secrets(str(exc), self.vault.all_known_secrets())
            return ConnectorTestResult(
                connector_id=connector.id,
                ok=False,
                status="error",
                message=f"MCP handshake/discovery failed: {msg}",
                latency_ms=latency,
                auth_configured=bool(self.vault.has_secret(connector) or connector.auth_type == "none"),
            )

    async def call_tool_async(
        self,
        connector: ConnectorConfig,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prereq_fail = self._validate_prerequisites(connector)
        if prereq_fail is not None:
            return {"ok": False, "status": prereq_fail.status, "error": prereq_fail.message}

        started = time.perf_counter()
        timeout = float(connector.permissions.timeout_seconds or 20.0)
        try:
            async with self._open_session(connector) as session:
                raw_res = await asyncio.wait_for(session.call_tool(tool_name, arguments or {}), timeout=timeout)
            text = _format_mcp_content(raw_res)
            cleaned = redact_secrets(text, self.vault.all_known_secrets())
            framed = self.permissions.frame_untrusted_output(f"mcp:{connector.id}:{tool_name}", cleaned)
            is_err = bool(getattr(raw_res, "isError", False))
            return {
                "ok": not is_err,
                "status": "error" if is_err else "connected",
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "text": cleaned,
                "framed_output": framed,
                "error": cleaned if is_err else "",
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "error",
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "error": redact_secrets(str(exc), self.vault.all_known_secrets()),
            }

    async def read_resource_async(self, connector: ConnectorConfig, uri: str) -> dict[str, Any]:
        prereq_fail = self._validate_prerequisites(connector)
        if prereq_fail is not None:
            return {"ok": False, "error": prereq_fail.message}
        timeout = float(connector.permissions.timeout_seconds or 20.0)
        try:
            async with self._open_session(connector) as session:
                res = await asyncio.wait_for(session.read_resource(uri), timeout=timeout)
            contents = [str(getattr(c, "text", None) or getattr(c, "blob", "")) for c in (getattr(res, "contents", None) or [])]
            cleaned = redact_secrets("\n".join(contents), self.vault.all_known_secrets())
            return {
                "ok": True,
                "uri": uri,
                "framed_output": self.permissions.frame_untrusted_output(f"mcp:{connector.id}:{uri}", cleaned),
                "text": cleaned,
            }
        except Exception as exc:
            return {"ok": False, "error": redact_secrets(str(exc), self.vault.all_known_secrets())}

    def discover_sync(self, connector: ConnectorConfig) -> ConnectorTestResult:
        return self._run_sync(self.discover_async(connector), timeout=connector.permissions.timeout_seconds + 10.0)

    def call_tool_sync(self, connector: ConnectorConfig, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._run_sync(self.call_tool_async(connector, tool_name, arguments), timeout=connector.permissions.timeout_seconds + 10.0)

    def _run_sync(self, coro: Any, *, timeout: float = 30.0) -> Any:
        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False
        if in_loop:
            return _MCP_EXECUTOR.submit(lambda: asyncio.run(coro)).result(timeout=timeout)
        return asyncio.run(coro)
