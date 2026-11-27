"""Schemas and data models for the TraceRecover Connector & Source Registry."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ConnectorCategory(str, Enum):
    MCP = "mcp"
    BROWSER = "browser"
    SEARCH = "search"
    RESEARCH = "research"
    DEVELOPER = "developer"
    PRODUCTIVITY = "productivity"
    INFRASTRUCTURE = "infrastructure"
    CUSTOM_API = "custom_api"


class ConnectorStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    AUTH_REQUIRED = "auth_required"
    DISABLED = "disabled"
    ERROR = "error"


class SourceTrustTier(str, Enum):
    TIER_1_OFFICIAL = "tier1"
    TIER_2_ACADEMIC = "tier2"
    TIER_3_EDITORIAL = "tier3"
    TIER_4_OPEN_WEB = "tier4"


TRUST_TIER_METADATA: dict[str, dict[str, Any]] = {
    "tier1": {
        "tier": "tier1",
        "rank": 1,
        "label": "Tier 1: Official & Authoritative",
        "weight": 1.0,
        "examples": ["docs.python.org", "developer.mozilla.org", "sec.gov", "who.int", "cdc.gov", "nasa.gov"],
        "caveat": "Prioritizes official provenance, never guarantees truth on its own.",
    },
    "tier2": {
        "tier": "tier2",
        "rank": 2,
        "label": "Tier 2: Academic & Scientific",
        "weight": 0.9,
        "examples": ["arxiv.org", "api.semanticscholar.org", "api.crossref.org", "api.openalex.org", "pubmed.ncbi.nlm.nih.gov"],
        "caveat": "Peer-reviewed or preprint literature; preprints require corroboration.",
    },
    "tier3": {
        "tier": "tier3",
        "rank": 3,
        "label": "Tier 3: Reputable Reference & Engineering Docs",
        "weight": 0.75,
        "examples": ["en.wikipedia.org", "github.com", "fastapi.tiangolo.com", "playwright.dev"],
        "caveat": "Strong secondary/reference sources; verify claims against Tier 1/2 where critical.",
    },
    "tier4": {
        "tier": "tier4",
        "rank": 4,
        "label": "Tier 4: Open Web & Community",
        "weight": 0.5,
        "examples": ["stackoverflow.com", "duckduckgo.com", "blogs", "forums"],
        "caveat": "Treat as noisy unverified web evidence requiring cross-source corroboration.",
    },
}


@dataclass
class ConnectorToolSpec:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    read_only: bool = True
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConnectorToolSpec":
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            input_schema=dict(data.get("input_schema") or {}),
            read_only=bool(data.get("read_only", True)),
            requires_approval=bool(data.get("requires_approval", False)),
        )


@dataclass
class ConnectorPermissionPolicy:
    read_only: bool = True
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    requires_approval_tools: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    allow_downloads: bool = False
    allow_form_submit: bool = False
    timeout_seconds: float = 20.0
    rate_limit_per_minute: int = 30

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ConnectorPermissionPolicy":
        raw = data or {}
        return cls(
            read_only=bool(raw.get("read_only", True)),
            allowed_tools=[str(x) for x in (raw.get("allowed_tools") or [])],
            denied_tools=[str(x) for x in (raw.get("denied_tools") or [])],
            requires_approval_tools=[str(x) for x in (raw.get("requires_approval_tools") or [])],
            allowed_domains=[str(x).lower() for x in (raw.get("allowed_domains") or [])],
            blocked_domains=[str(x).lower() for x in (raw.get("blocked_domains") or [])],
            allow_downloads=bool(raw.get("allow_downloads", False)),
            allow_form_submit=bool(raw.get("allow_form_submit", False)),
            timeout_seconds=max(1.0, min(float(raw.get("timeout_seconds", 20.0)), 120.0)),
            rate_limit_per_minute=max(1, min(int(raw.get("rate_limit_per_minute", 30)), 300)),
        )


@dataclass
class ConnectorConfig:
    id: str
    name: str
    category: str
    transport: str  # stdio | streamable_http | sse | rest | browser_engine | builtin
    description: str = ""
    enabled: bool = False
    builtin: bool = False
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    auth_type: str = "none"  # none | api_key | bearer | env_var | connection_string
    env_var_name: str = ""
    secret_ref: str = ""
    browser_engine: str = ""  # chromium | firefox | webkit
    trust_tier: str = "tier3"
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    permissions: ConnectorPermissionPolicy = field(default_factory=ConnectorPermissionPolicy)
    tools: list[ConnectorToolSpec] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    status: str = ConnectorStatus.DISCONNECTED.value
    last_error: str = ""
    last_tested_at: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["permissions"] = self.permissions.to_dict()
        payload["tools"] = [t.to_dict() for t in self.tools]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConnectorConfig":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            category=str(data.get("category", ConnectorCategory.CUSTOM_API.value)),
            transport=str(data.get("transport", "rest")),
            description=str(data.get("description", "")),
            enabled=bool(data.get("enabled", False)),
            builtin=bool(data.get("builtin", False)),
            command=str(data.get("command", "")),
            args=[str(x) for x in (data.get("args") or [])],
            url=str(data.get("url", "")),
            headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
            auth_type=str(data.get("auth_type", "none")),
            env_var_name=str(data.get("env_var_name", "")),
            secret_ref=str(data.get("secret_ref", "")),
            browser_engine=str(data.get("browser_engine", "")),
            trust_tier=str(data.get("trust_tier", "tier3")),
            tags=[str(x) for x in (data.get("tags") or [])],
            metadata=dict(data.get("metadata") or {}),
            permissions=ConnectorPermissionPolicy.from_dict(data.get("permissions")),
            tools=[ConnectorToolSpec.from_dict(t) for t in (data.get("tools") or [])],
            resources=[dict(r) for r in (data.get("resources") or [])],
            prompts=[dict(p) for p in (data.get("prompts") or [])],
            status=str(data.get("status", ConnectorStatus.DISCONNECTED.value)),
            last_error=str(data.get("last_error", "")),
            last_tested_at=str(data.get("last_tested_at", "")),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
        )


@dataclass
class ConnectorHealth:
    connector_id: str
    status: str
    latency_ms: float | None = None
    last_checked_at: str = field(default_factory=utc_now_iso)
    consecutive_failures: int = 0
    total_calls: int = 0
    failed_calls: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConnectorTestResult:
    connector_id: str
    ok: bool
    status: str
    message: str
    latency_ms: float = 0.0
    discovered_tools: list[ConnectorToolSpec] = field(default_factory=list)
    discovered_resources: list[dict[str, Any]] = field(default_factory=list)
    discovered_prompts: list[dict[str, Any]] = field(default_factory=list)
    auth_configured: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "ok": self.ok,
            "status": self.status,
            "message": self.message,
            "latency_ms": round(self.latency_ms, 2),
            "discovered_tools": [t.to_dict() for t in self.discovered_tools],
            "discovered_resources": self.discovered_resources,
            "discovered_prompts": self.discovered_prompts,
            "auth_configured": self.auth_configured,
            "details": self.details,
        }


@dataclass
class ConnectorExecutionRecord:
    timestamp: str
    connector_id: str
    connector_name: str
    category: str
    tool_name: str
    ok: bool
    latency_ms: float
    permission_status: str  # allowed | denied | approval_required
    arguments_preview: str
    result_preview: str
    error: str = ""
    untrusted_data_wrapped: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceDocument:
    title: str
    url: str
    snippet: str
    provider: str
    trust_tier: str
    domain: str = ""
    published_at: str = ""
    relevance_score: float = 0.0
    citation: str = ""
    corroborated: bool = False
    contradiction_flag: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
