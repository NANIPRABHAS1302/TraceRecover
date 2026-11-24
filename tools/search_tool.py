"""Deterministic local mock knowledge-base search."""

from __future__ import annotations

import re


KNOWLEDGE_BASE = (
    # Core Phase 1-8 baseline facts (preserved verbatim for existing tests)
    ("capital france", "The capital of France is Paris."),
    ("largest planet solar system", "The largest planet in the Solar System is Jupiter."),
    ("boiling point water celsius", "Water boils at 100 degrees Celsius at standard atmospheric pressure."),
    ("tallest mountain earth", "Mount Everest is Earth's tallest mountain above sea level."),
    ("author pride and prejudice", "Jane Austen wrote Pride and Prejudice."),
    ("speed light vacuum", "The speed of light in a vacuum is approximately 299,792 kilometres per second."),
    # Aerospace Telemetry (Metrics M-1..M-4 + domain references)
    (
        "aerospace telemetry metric m-1 orbital booster stage-1 chamber pressure",
        "Aerospace Telemetry Metric M-1 reports orbital booster stage-1 chamber pressure at 420 bar.",
    ),
    (
        "aerospace telemetry metric m-2 satellite thermal shield temperature",
        "Aerospace Telemetry Metric M-2 reports satellite thermal shield temperature at 185 kelvin.",
    ),
    (
        "aerospace telemetry metric m-3 downlink antenna signal margin",
        "Aerospace Telemetry Metric M-3 reports downlink antenna signal margin at 34 decibels.",
    ),
    (
        "aerospace telemetry metric m-4 attitude thruster propellant reserve",
        "Aerospace Telemetry Metric M-4 reports attitude thruster propellant reserve at 640 kilograms.",
    ),
    (
        "aerospace telemetry launch window hold limit countdown",
        "Aerospace Telemetry policy sets the orbital launch window hold limit at 180 seconds.",
    ),
    (
        "aerospace telemetry payload fairing acoustic ceiling",
        "Aerospace Telemetry policy sets the payload fairing acoustic ceiling at 140 decibels.",
    ),
    # Cloud FinOps (Metrics M-5..M-8 + domain references)
    (
        "cloud finops metric m-5 daily gpu cluster compute spend",
        "Cloud FinOps Metric M-5 reports daily GPU cluster compute spend at 1250 dollars.",
    ),
    (
        "cloud finops metric m-6 object storage tier monthly volume",
        "Cloud FinOps Metric M-6 reports object storage tier monthly volume at 480 terabytes.",
    ),
    (
        "cloud finops metric m-7 reserved instance coverage ratio",
        "Cloud FinOps Metric M-7 reports reserved instance coverage ratio at 78 percent.",
    ),
    (
        "cloud finops metric m-8 idle kubernetes node waste rate",
        "Cloud FinOps Metric M-8 reports idle Kubernetes node waste rate at 160 dollars per day.",
    ),
    (
        "cloud finops cross-region egress transfer tariff",
        "Cloud FinOps standard cross-region egress transfer tariff is 9 cents per gigabyte.",
    ),
    (
        "cloud finops cold archive retention policy window",
        "Cloud FinOps cold archive retention policy window spans 365 days.",
    ),
    # Enterprise SLA Tickets (Metrics M-9..M-12 + domain references)
    (
        "enterprise sla tickets metric m-9 critical incident mean recovery time",
        "Enterprise SLA Tickets Metric M-9 reports critical incident mean recovery time at 45 minutes.",
    ),
    (
        "enterprise sla tickets metric m-10 monthly priority-one ticket backlog",
        "Enterprise SLA Tickets Metric M-10 reports monthly priority-one ticket backlog at 28 tickets.",
    ),
    (
        "enterprise sla tickets metric m-11 regional escalation penalty fee",
        "Enterprise SLA Tickets Metric M-11 reports regional escalation penalty fee at 350 dollars per breach.",
    ),
    (
        "enterprise sla tickets metric m-12 tier-two support capacity",
        "Enterprise SLA Tickets Metric M-12 reports tier-two support capacity at 120 tickets per week.",
    ),
    (
        "enterprise sla platinum availability uptime commitment",
        "Enterprise SLA platinum tier availability uptime commitment stands at 99.99 percent.",
    ),
    (
        "enterprise sla standard acknowledgement response target",
        "Enterprise SLA standard acknowledgement response target is 4 hours.",
    ),
    # Climate Grid Sensors (Metrics M-13..M-16 + domain references)
    (
        "climate grid sensors metric m-13 coastal wind turbine array output",
        "Climate Grid Sensors Metric M-13 reports coastal wind turbine array output at 860 megawatts.",
    ),
    (
        "climate grid sensors metric m-14 solar substation inverter efficiency",
        "Climate Grid Sensors Metric M-14 reports solar substation inverter efficiency at 94 percent.",
    ),
    (
        "climate grid sensors metric m-15 grid frequency stabilization reserve",
        "Climate Grid Sensors Metric M-15 reports grid frequency stabilization reserve at 310 megawatts.",
    ),
    (
        "climate grid sensors metric m-16 alpine microgrid battery bank charge",
        "Climate Grid Sensors Metric M-16 reports alpine microgrid battery bank charge at 540 megawatt hours.",
    ),
    (
        "climate grid substation transformer thermal ceiling",
        "Climate Grid substation transformer thermal ceiling is 115 degrees Celsius.",
    ),
    (
        "climate grid peak demand load shedding trigger",
        "Climate Grid peak demand load shedding trigger activates at 950 megawatts.",
    ),
    # Clinical Equipment Logs (Metrics M-17..M-20 + domain references)
    (
        "clinical equipment logs metric m-17 mri superconducting magnet helium level",
        "Clinical Equipment Logs Metric M-17 reports MRI superconducting magnet helium level at 88 percent.",
    ),
    (
        "clinical equipment logs metric m-18 icu ventilator fleet active count",
        "Clinical Equipment Logs Metric M-18 reports ICU ventilator fleet active count at 64 units.",
    ),
    (
        "clinical equipment logs metric m-19 infusion pump calibration cycle interval",
        "Clinical Equipment Logs Metric M-19 reports infusion pump calibration cycle interval at 90 days.",
    ),
    (
        "clinical equipment logs metric m-20 surgical autoclave sterilization pressure",
        "Clinical Equipment Logs Metric M-20 reports surgical autoclave sterilization pressure at 215 kilopascals.",
    ),
    (
        "clinical equipment defibrillator lithium battery shelf life",
        "Clinical Equipment defibrillator lithium battery shelf life is 48 months.",
    ),
    (
        "clinical equipment blood bank ultra-low freezer setpoint",
        "Clinical Equipment blood bank ultra-low freezer setpoint is -30 degrees Celsius.",
    ),
    # Structured Sensor Logs (S-81..S-100 across Climate Grid, Aerospace, and Clinical)
    ("climate grid sensor s-81 feeder load", "Climate Grid Sensor S-81 recorded feeder load at 142 kilowatts."),
    ("climate grid sensor s-82 feeder load", "Climate Grid Sensor S-82 recorded feeder load at 218 kilowatts."),
    ("climate grid sensor s-83 feeder load", "Climate Grid Sensor S-83 recorded feeder load at 305 kilowatts."),
    ("climate grid sensor s-84 feeder load", "Climate Grid Sensor S-84 recorded feeder load at 176 kilowatts."),
    ("climate grid sensor s-85 feeder load", "Climate Grid Sensor S-85 recorded feeder load at 284 kilowatts."),
    ("climate grid sensor s-86 feeder load", "Climate Grid Sensor S-86 recorded feeder load at 195 kilowatts."),
    ("climate grid sensor s-87 feeder load", "Climate Grid Sensor S-87 recorded feeder load at 260 kilowatts."),
    ("aerospace telemetry sensor s-88 manifold pressure", "Aerospace Telemetry Sensor S-88 recorded manifold pressure at 312 kilopascals."),
    ("aerospace telemetry sensor s-89 manifold pressure", "Aerospace Telemetry Sensor S-89 recorded manifold pressure at 428 kilopascals."),
    ("aerospace telemetry sensor s-90 manifold pressure", "Aerospace Telemetry Sensor S-90 recorded manifold pressure at 190 kilopascals."),
    ("aerospace telemetry sensor s-91 manifold pressure", "Aerospace Telemetry Sensor S-91 recorded manifold pressure at 515 kilopascals."),
    ("aerospace telemetry sensor s-92 manifold pressure", "Aerospace Telemetry Sensor S-92 recorded manifold pressure at 274 kilopascals."),
    ("aerospace telemetry sensor s-93 manifold pressure", "Aerospace Telemetry Sensor S-93 recorded manifold pressure at 365 kilopascals."),
    ("aerospace telemetry sensor s-94 manifold pressure", "Aerospace Telemetry Sensor S-94 recorded manifold pressure at 480 kilopascals."),
    ("clinical equipment sensor s-95 oxygen flow", "Clinical Equipment Sensor S-95 recorded oxygen flow at 72 liters per minute."),
    ("clinical equipment sensor s-96 oxygen flow", "Clinical Equipment Sensor S-96 recorded oxygen flow at 98 liters per minute."),
    ("clinical equipment sensor s-97 oxygen flow", "Clinical Equipment Sensor S-97 recorded oxygen flow at 114 liters per minute."),
    ("clinical equipment sensor s-98 oxygen flow", "Clinical Equipment Sensor S-98 recorded oxygen flow at 66 liters per minute."),
    ("clinical equipment sensor s-99 oxygen flow", "Clinical Equipment Sensor S-99 recorded oxygen flow at 84 liters per minute."),
    ("clinical equipment sensor s-100 oxygen flow", "Clinical Equipment Sensor S-100 recorded oxygen flow at 108 liters per minute."),
)

_STOP_WORDS = {"a", "an", "and", "at", "by", "for", "in", "is", "of", "on", "the", "what"}
_RECORD_ID_PATTERN = re.compile(r"\b(?:m|s|t)-\d+\b", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())
        if token not in _STOP_WORDS
    }


def search(query: str) -> str:
    """Return the best matching local fact or an explicit no-evidence result."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Search query must be a non-empty string.")
    query_tokens = _tokens(query)
    if not query_tokens:
        return f"No local evidence found for: {query}"

    requested_ids = {match.lower() for match in _RECORD_ID_PATTERN.findall(query)}
    best_answer = ""
    best_score = 0
    for keywords, answer in KNOWLEDGE_BASE:
        entry_tokens = _tokens(keywords)
        if requested_ids and not requested_ids.intersection(entry_tokens):
            continue
        score = len(query_tokens.intersection(entry_tokens))
        if score > best_score:
            best_score = score
            best_answer = answer
    if best_score == 0:
        return f"No local evidence found for: {query}"
    return best_answer
