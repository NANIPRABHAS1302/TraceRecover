"""Security, secret vault, redaction, rate-limiting, and permission engine for connectors."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from connectors.schemas import ConnectorConfig

_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(postgresql|postgres|mysql|redis|mongodb)://([^:\s]+):([^@\s]+)@"),
]

_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions"),
    re.compile(r"(?i)system\s*prompt"),
    re.compile(r"(?i)<\s*/?\s*system\s*>"),
    re.compile(r"(?i)reveal\s+(your\s+)?(api\s*key|secret|password|token)"),
]


def mask_secret(value: str | None) -> str:
    """Return a safe non-reversible preview of a secret."""
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}***{text[-2:]}"


def redact_secrets(text: str, extra_secrets: list[str] | None = None) -> str:
    """Scrub API keys, tokens, DB passwords, and known connector secrets from text."""
    if not text:
        return ""
    cleaned = str(text)
    for secret in extra_secrets or []:
        s = (secret or "").strip()
        if len(s) >= 6:
            cleaned = cleaned.replace(s, "***REDACTED***")
    for idx, pattern in enumerate(_SECRET_PATTERNS):
        if idx in (4,):  # tvly-
            cleaned = pattern.sub("***REDACTED***", cleaned)
        elif idx == 5:  # Bearer token
            cleaned = pattern.sub(r"\1***REDACTED***", cleaned)
        elif idx == 6:  # key=value
            cleaned = pattern.sub(r"\1***REDACTED***", cleaned)
        elif idx == 7:  # db://user:pass@
            cleaned = pattern.sub(r"\1://\2:***REDACTED***@", cleaned)
        else:
            cleaned = pattern.sub("***REDACTED***", cleaned)
    return cleaned


class SecretVault:
    """Server-side Fernet-encrypted connector secret vault with env-ref resolution."""

    def __init__(self, vault_dir: Path | str) -> None:
        self.vault_dir = Path(vault_dir)
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.vault_path = self.vault_dir / "vault.enc.json"
        self.key_path = self.vault_dir / ".vault.key"
        self._fernet = self._init_fernet()

    def _init_fernet(self) -> Fernet:
        env_key = os.environ.get("TRACERECOVER_VAULT_KEY", "").strip()
        if env_key:
            try:
                return Fernet(env_key.encode("utf-8"))
            except Exception:
                derived = base64.urlsafe_b64encode(hashlib.sha256(env_key.encode("utf-8")).digest())
                return Fernet(derived)
        if self.key_path.exists():
            raw = self.key_path.read_text(encoding="utf-8").strip().encode("utf-8")
            return Fernet(raw)
        generated = Fernet.generate_key()
        self.key_path.write_text(generated.decode("utf-8"), encoding="utf-8")
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return Fernet(generated)

    def security_posture(self) -> dict[str, Any]:
        """Report honest encryption-key storage posture without exposing key material."""
        env_key = os.environ.get("TRACERECOVER_VAULT_KEY", "").strip()
        if env_key:
            return {
                "key_source": "env_var",
                "encryption": "Fernet (AES-128-CBC + HMAC-SHA256)",
                "key_file_present": self.key_path.exists(),
                "production_ready_key_management": True,
                "production_warning": "",
            }
        return {
            "key_source": "local_keyfile",
            "encryption": "Fernet (AES-128-CBC + HMAC-SHA256)",
            "key_file_present": self.key_path.exists(),
            "production_ready_key_management": False,
            "production_warning": (
                "Vault key is stored locally in .vault.key beside encrypted data; in production set "
                "TRACERECOVER_VAULT_KEY via platform secret manager and protect volume backups."
            ),
        }

    def _read_store(self) -> dict[str, str]:
        if not self.vault_path.exists():
            return {}
        try:
            data = json.loads(self.vault_path.read_text(encoding="utf-8"))
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_store(self, store: dict[str, str]) -> None:
        self.vault_path.write_text(json.dumps(store, indent=2), encoding="utf-8")

    def set_secret(self, connector_id: str, secret_value: str) -> str:
        """Encrypt and persist a connector secret; returns opaque secret reference."""
        token = self._fernet.encrypt(secret_value.strip().encode("utf-8")).decode("utf-8")
        store = self._read_store()
        store[connector_id] = token
        self._write_store(store)
        return f"vault:{connector_id}"

    def delete_secret(self, connector_id: str) -> bool:
        """Remove a connector's secret permanently from the vault."""
        store = self._read_store()
        existed = connector_id in store
        if existed:
            del store[connector_id]
            self._write_store(store)
        return existed

    def has_secret(self, connector: ConnectorConfig) -> bool:
        """Check whether an effective credential exists via vault or environment variable."""
        if self.resolve_secret(connector):
            return True
        return False

    def resolve_secret(self, connector: ConnectorConfig) -> str:
        """Resolve secret strictly server-side from vault or environment variable."""
        ref = (connector.secret_ref or "").strip()
        if ref.startswith("env:"):
            env_name = ref[4:].strip()
            return os.environ.get(env_name, "").strip()
        if connector.env_var_name:
            env_val = os.environ.get(connector.env_var_name.strip(), "").strip()
            if env_val:
                return env_val
        store = self._read_store()
        encrypted = store.get(connector.id)
        if encrypted:
            try:
                return self._fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")
            except InvalidToken:
                return ""
        return ""

    def all_known_secrets(self) -> list[str]:
        """Collect all resolved non-empty secrets for outbound log redaction."""
        store = self._read_store()
        secrets: list[str] = []
        for token in store.values():
            try:
                secrets.append(self._fernet.decrypt(token.encode("utf-8")).decode("utf-8"))
            except InvalidToken:
                continue
        for key, value in os.environ.items():
            if any(marker in key.upper() for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
                if value and len(value.strip()) >= 6:
                    secrets.append(value.strip())
        return secrets


class PermissionEngine:
    """Enforces connector allowlists, rate limits, domain policies, approval gates, and data framing."""

    def __init__(self) -> None:
        self._rate_windows: dict[str, deque[float]] = {}

    @staticmethod
    def _is_disallowed_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return bool(
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        )

    @staticmethod
    def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        cleaned = (host or "").strip("[]").split("%")[0]
        if not cleaned:
            return None
        try:
            return ipaddress.ip_address(cleaned)
        except ValueError:
            pass
        # Catch decimal/hex/octal obfuscated IPv4 notations such as 2130706433 or 0x7f000001
        try:
            packed = socket.inet_aton(cleaned)
            return ipaddress.IPv4Address(packed)
        except OSError:
            return None

    def check_url(self, connector: ConnectorConfig, url: str) -> tuple[bool, str]:
        """Validate URL scheme, credentials, SSRF/DNS-rebinding private IPs, and allow/block domain lists."""
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in ("http", "https"):
            return False, f"Blocked non-HTTP URL scheme: {parsed.scheme or 'missing'}"
        if parsed.username or parsed.password:
            return False, "Blocked URL containing embedded credentials"
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return False, "Missing URL hostname"
        if host in ("169.254.169.254", "metadata.google.internal", "metadata.azure.com"):
            return False, f"Blocked metadata host: {host}"

        policy = connector.permissions
        for blocked in policy.blocked_domains:
            if host == blocked or host.endswith(f".{blocked}"):
                return False, f"Domain '{host}' is blocked by connector policy"

        allow_listed = any(host == d or host.endswith(f".{d}") for d in policy.allowed_domains)
        if policy.allowed_domains and not allow_listed:
            return False, f"Domain '{host}' is not in allowed_domains ({policy.allowed_domains})"

        # Check obfuscated / literal IP addresses
        literal_ip = self._parse_ip_literal(host)
        if literal_ip is not None:
            if self._is_disallowed_ip(literal_ip) and not allow_listed:
                return False, f"Blocked private/loopback/link-local IP host '{host}' (SSRF defense)"
            return True, "OK"

        # DNS resolution defense against localhost aliases and DNS rebinding into private space
        if host == "localhost" and not allow_listed:
            return False, "Blocked localhost host (SSRF defense)"
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                raw_ip = str(info[4][0]).split("%")[0]
                resolved_ip = self._parse_ip_literal(raw_ip)
                if resolved_ip is not None and self._is_disallowed_ip(resolved_ip) and not allow_listed:
                    return (
                        False,
                        f"Blocked host '{host}' resolving to non-public address {resolved_ip} (DNS rebinding/SSRF defense)",
                    )
        except socket.gaierror:
            pass

        return True, "OK"

    def check_execution(
        self,
        connector: ConnectorConfig,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        approved_actions: set[str] | None = None,
    ) -> tuple[bool, str, bool]:
        """Return (allowed, reason, requires_approval)."""
        if not connector.enabled:
            return False, f"Connector '{connector.id}' is disabled.", False

        policy = connector.permissions
        if tool_name in policy.denied_tools:
            return False, f"Tool '{tool_name}' is explicitly denied on connector '{connector.id}'.", False

        if policy.allowed_tools and tool_name not in policy.allowed_tools:
            return False, f"Tool '{tool_name}' is not in allowed_tools for '{connector.id}'.", False

        # Tool metadata check
        spec = next((t for t in connector.tools if t.name == tool_name), None)
        if policy.read_only and spec and not spec.read_only:
            action_key = f"{connector.id}:{tool_name}"
            if approved_actions and (action_key in approved_actions or tool_name in approved_actions):
                pass
            else:
                return (
                    False,
                    f"Action '{action_key}' is write/state-changing under read_only policy and requires explicit approval.",
                    True,
                )

        if tool_name in policy.requires_approval_tools or (spec and spec.requires_approval):
            action_key = f"{connector.id}:{tool_name}"
            if not approved_actions or (action_key not in approved_actions and tool_name not in approved_actions):
                return (
                    False,
                    f"Action '{action_key}' requires explicit user/admin approval before execution.",
                    True,
                )

        # URL check if arguments carry a URL
        args = arguments or {}
        for key in ("url", "target_url", "href"):
            if key in args and isinstance(args[key], str) and args[key].strip():
                ok, reason = self.check_url(connector, args[key])
                if not ok:
                    return False, reason, False

        # Sliding 60s rate limit check
        now = time.monotonic()
        window = self._rate_windows.setdefault(connector.id, deque())
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= policy.rate_limit_per_minute:
            return (
                False,
                f"Rate limit exceeded for '{connector.id}' ({policy.rate_limit_per_minute}/min).",
                False,
            )
        window.append(now)
        return True, "allowed", False

    @staticmethod
    def frame_untrusted_output(source_label: str, content: str) -> dict[str, Any]:
        """Wrap external connector output as untrusted data and flag injection markers."""
        raw = str(content or "")
        injection_detected = any(pat.search(raw) for pat in _INJECTION_PATTERNS)
        sanitized = raw
        if injection_detected:
            for pat in _INJECTION_PATTERNS:
                sanitized = pat.sub("[FILTERED_UNTRUSTED_DIRECTIVE]", sanitized)
        return {
            "untrusted_data": True,
            "source": source_label,
            "injection_warning": injection_detected,
            "framed_text": (
                f"[UNTRUSTED_EXTERNAL_DATA source=\"{source_label}\"]\n"
                f"{sanitized}\n"
                f"[/UNTRUSTED_EXTERNAL_DATA]"
            ),
            "raw_excerpt": sanitized[:4000],
        }
