"""Permission-checked custom HTTP/REST API connector client."""

from __future__ import annotations

import time
from typing import Any

import httpx

from connectors.permissions import PermissionEngine, SecretVault, redact_secrets
from connectors.schemas import ConnectorConfig


class CustomAPIConnector:
    """Executes SSRF-guarded HTTP calls against registered custom REST APIs."""

    def __init__(self, permission_engine: PermissionEngine, vault: SecretVault) -> None:
        self.permissions = permission_engine
        self.vault = vault

    def _build_headers(self, connector: ConnectorConfig) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in (connector.headers or {}).items()}
        secret = self.vault.resolve_secret(connector)
        if secret:
            if connector.auth_type == "bearer":
                headers.setdefault("Authorization", f"Bearer {secret}")
            elif connector.auth_type == "api_key":
                key_header = str(connector.metadata.get("api_key_header") or "X-API-Key")
                headers.setdefault(key_header, secret)
        return headers

    def test_endpoint(self, connector: ConnectorConfig) -> dict[str, Any]:
        target_url = (connector.url or "").strip()
        ok_url, reason = self.permissions.check_url(connector, target_url)
        if not ok_url:
            return {"ok": False, "status": "error", "message": reason}
        if connector.auth_type in ("api_key", "bearer") and not self.vault.has_secret(connector):
            return {
                "ok": False,
                "status": "auth_required",
                "message": f"Credential required ({connector.auth_type}) before testing {connector.name}.",
            }
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=connector.permissions.timeout_seconds, follow_redirects=False) as client:
                resp = client.get(target_url, headers=self._build_headers(connector))
            latency = round((time.perf_counter() - started) * 1000.0, 2)
            ok = 200 <= resp.status_code < 400
            return {
                "ok": ok,
                "status": "connected" if ok else "error",
                "http_status": resp.status_code,
                "latency_ms": latency,
                "message": f"HTTP {resp.status_code}" if ok else f"Endpoint returned HTTP {resp.status_code}",
            }
        except Exception as exc:
            return {"ok": False, "status": "error", "message": redact_secrets(str(exc), self.vault.all_known_secrets())}

    def call(
        self,
        connector: ConnectorConfig,
        *,
        method: str = "GET",
        path: str = "",
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        approved_actions: set[str] | None = None,
    ) -> dict[str, Any]:
        verb = (method or "GET").upper()
        if verb not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            return {"ok": False, "error": f"Unsupported HTTP method '{verb}'"}
        if verb != "GET" and connector.permissions.read_only:
            action_key = f"{connector.id}:http_{verb.lower()}"
            if not approved_actions or action_key not in approved_actions:
                return {
                    "ok": False,
                    "requires_approval": True,
                    "error": f"HTTP {verb} requires explicit approval under read_only policy.",
                }
        base = (connector.url or "").rstrip("/")
        full_url = f"{base}/{path.lstrip('/')}" if path else base
        ok_url, reason = self.permissions.check_url(connector, full_url)
        if not ok_url:
            return {"ok": False, "error": reason}

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=connector.permissions.timeout_seconds, follow_redirects=False) as client:
                resp = client.request(
                    verb,
                    full_url,
                    params=params,
                    json=json_body,
                    headers=self._build_headers(connector),
                )
            latency = round((time.perf_counter() - started) * 1000.0, 2)
            cleaned_body = redact_secrets(resp.text[:8000], self.vault.all_known_secrets())
            framed = self.permissions.frame_untrusted_output(f"custom_api:{connector.id}", cleaned_body)
            return {
                "ok": 200 <= resp.status_code < 400,
                "http_status": resp.status_code,
                "latency_ms": latency,
                "framed_output": framed,
                "raw_excerpt": cleaned_body[:3000],
            }
        except Exception as exc:
            return {"ok": False, "error": redact_secrets(str(exc), self.vault.all_known_secrets())}
