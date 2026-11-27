"""Connector health and latency monitoring."""

from __future__ import annotations

from collections import deque

from connectors.schemas import ConnectorConfig, ConnectorHealth, utc_now_iso


class HealthMonitor:
    """Tracks connector check results, call counts, and rolling latency stats."""

    def __init__(self) -> None:
        self._records: dict[str, ConnectorHealth] = {}
        self._latencies: dict[str, deque[float]] = {}

    def record_check(
        self,
        connector: ConnectorConfig,
        *,
        ok: bool,
        status: str,
        latency_ms: float | None = None,
        detail: str = "",
    ) -> ConnectorHealth:
        prev = self._records.get(connector.id)
        failures = 0 if ok else ((prev.consecutive_failures + 1) if prev else 1)
        total = (prev.total_calls if prev else 0) + 1
        failed = (prev.failed_calls if prev else 0) + (0 if ok else 1)
        if latency_ms is not None:
            dq = self._latencies.setdefault(connector.id, deque(maxlen=25))
            dq.append(float(latency_ms))
        health = ConnectorHealth(
            connector_id=connector.id,
            status=status,
            latency_ms=round(latency_ms, 2) if latency_ms is not None else None,
            last_checked_at=utc_now_iso(),
            consecutive_failures=failures,
            total_calls=total,
            failed_calls=failed,
            detail=detail,
        )
        self._records[connector.id] = health
        return health

    def get(self, connector_id: str, fallback_status: str = "disconnected") -> ConnectorHealth:
        if connector_id in self._records:
            return self._records[connector_id]
        return ConnectorHealth(connector_id=connector_id, status=fallback_status)

    def summary(self) -> dict[str, object]:
        items = [h.to_dict() for h in self._records.values()]
        all_lat = [v for dq in self._latencies.values() for v in dq]
        avg_latency = round(sum(all_lat) / len(all_lat), 2) if all_lat else 0.0
        return {
            "tracked_connectors": len(items),
            "avg_latency_ms": avg_latency,
            "items": items,
        }
