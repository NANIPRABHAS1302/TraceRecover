"""TraceRecover Universal Connector & External Tool Ecosystem (Phase 5)."""

from connectors.health import HealthMonitor
from connectors.manager import ConnectorManager
from connectors.permissions import PermissionEngine, SecretVault, redact_secrets
from connectors.schemas import (
    ConnectorCategory,
    ConnectorConfig,
    ConnectorExecutionRecord,
    ConnectorHealth,
    ConnectorPermissionPolicy,
    ConnectorStatus,
    ConnectorTestResult,
    ConnectorToolSpec,
    SourceDocument,
    SourceTrustTier,
)

__all__ = [
    "ConnectorCategory",
    "ConnectorConfig",
    "ConnectorExecutionRecord",
    "ConnectorHealth",
    "ConnectorManager",
    "ConnectorPermissionPolicy",
    "ConnectorStatus",
    "ConnectorTestResult",
    "ConnectorToolSpec",
    "HealthMonitor",
    "PermissionEngine",
    "SecretVault",
    "SourceDocument",
    "SourceTrustTier",
    "redact_secrets",
]
