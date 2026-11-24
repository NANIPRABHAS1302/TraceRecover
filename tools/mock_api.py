"""Deterministic simulated REST-style ticket lookup."""

from __future__ import annotations

from typing import Any


_TICKETS: dict[str, dict[str, Any]] = {
    # Core Phase 1-8 baseline tickets (preserved verbatim for existing tests)
    "T-104": {"ticket_id": "T-104", "status": "closed", "owner": "Avery"},
    "T-205": {"ticket_id": "T-205", "status": "open", "owner": "Jordan"},
    "T-309": {"ticket_id": "T-309", "status": "in_progress", "owner": "Sam"},
    # Enterprise SLA Tickets T-401..T-415 with extended FinOps/SLA schema
    "T-401": {
        "id": "T-401",
        "ticket_id": "T-401",
        "status": "open",
        "priority": "P1",
        "sla_hours": 4,
        "owner": "Elena",
        "cost_usd": 1200,
    },
    "T-402": {
        "id": "T-402",
        "ticket_id": "T-402",
        "status": "in_progress",
        "priority": "P1",
        "sla_hours": 6,
        "owner": "Marcus",
        "cost_usd": 850,
    },
    "T-403": {
        "id": "T-403",
        "ticket_id": "T-403",
        "status": "closed",
        "priority": "P2",
        "sla_hours": 12,
        "owner": "Priya",
        "cost_usd": 450,
    },
    "T-404": {
        "id": "T-404",
        "ticket_id": "T-404",
        "status": "open",
        "priority": "P1",
        "sla_hours": 2,
        "owner": "Devon",
        "cost_usd": 1950,
    },
    "T-405": {
        "id": "T-405",
        "ticket_id": "T-405",
        "status": "resolved",
        "priority": "P3",
        "sla_hours": 24,
        "owner": "Lin",
        "cost_usd": 300,
    },
    "T-406": {
        "id": "T-406",
        "ticket_id": "T-406",
        "status": "pending",
        "priority": "P2",
        "sla_hours": 8,
        "owner": "Mateo",
        "cost_usd": 620,
    },
    "T-407": {
        "id": "T-407",
        "ticket_id": "T-407",
        "status": "open",
        "priority": "P1",
        "sla_hours": 4,
        "owner": "Hannah",
        "cost_usd": 1500,
    },
    "T-408": {
        "id": "T-408",
        "ticket_id": "T-408",
        "status": "in_progress",
        "priority": "P2",
        "sla_hours": 10,
        "owner": "Tariq",
        "cost_usd": 780,
    },
    "T-409": {
        "id": "T-409",
        "ticket_id": "T-409",
        "status": "closed",
        "priority": "P3",
        "sla_hours": 18,
        "owner": "Nora",
        "cost_usd": 410,
    },
    "T-410": {
        "id": "T-410",
        "ticket_id": "T-410",
        "status": "open",
        "priority": "P1",
        "sla_hours": 3,
        "owner": "Kenji",
        "cost_usd": 2100,
    },
    "T-411": {
        "id": "T-411",
        "ticket_id": "T-411",
        "status": "resolved",
        "priority": "P2",
        "sla_hours": 12,
        "owner": "Chloe",
        "cost_usd": 540,
    },
    "T-412": {
        "id": "T-412",
        "ticket_id": "T-412",
        "status": "pending",
        "priority": "P2",
        "sla_hours": 16,
        "owner": "Victor",
        "cost_usd": 930,
    },
    "T-413": {
        "id": "T-413",
        "ticket_id": "T-413",
        "status": "open",
        "priority": "P1",
        "sla_hours": 5,
        "owner": "Amara",
        "cost_usd": 1340,
    },
    "T-414": {
        "id": "T-414",
        "ticket_id": "T-414",
        "status": "in_progress",
        "priority": "P2",
        "sla_hours": 9,
        "owner": "Lucas",
        "cost_usd": 670,
    },
    "T-415": {
        "id": "T-415",
        "ticket_id": "T-415",
        "status": "closed",
        "priority": "P3",
        "sla_hours": 20,
        "owner": "Zara",
        "cost_usd": 390,
    },
    # Incomplete / empty edge-case records for natural-failure detection & abstention
    "T-416": {
        "id": "T-416",
        "ticket_id": "T-416",
        "status": "open",
        "priority": "P1",
        "sla_hours": 4,
    },
    "T-417": {
        "id": "T-417",
        "ticket_id": "T-417",
        "status": "",
        "priority": "P2",
        "sla_hours": 8,
        "owner": "Dana",
        "cost_usd": 250,
    },
    "T-418": {
        "id": "T-418",
        "ticket_id": "T-418",
        "priority": "P2",
        "owner": "Nadia",
    },
    "T-419": {},
}


def get_ticket(ticket_id: str) -> dict[str, Any]:
    """Return a simulated ticket response or raise for an unknown ticket."""
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise ValueError("ticket_id must be a non-empty string.")
    ticket = _TICKETS.get(ticket_id.strip().upper())
    if ticket is None:
        raise LookupError(f"Ticket {ticket_id!r} was not found.")
    return dict(ticket)
