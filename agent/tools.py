"""SafetyLens tools: factory safety anomaly detection with in-memory synthetic data.

Four tools cover the full closed loop:
  ingest_report       → parse a raw report into a structured event and log it
  get_asset_timeline  → fetch the anomaly history for an asset (detects normalised deviance)
  draft_work_order    → produce a structured work order for an asset + hazard
  get_risk_ledger     → return the Ignored Warnings Ledger (open risks, days open, cost exposure)
  raise_alert         → formally raise a safety alert (guarded by SafetyAlertGuard)
  send_evidence_brief → send a one-page evidence brief to the asset owner (guarded)

Data is in-memory synthetic data so the demo runs anywhere without AWS.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any

from strands import tool

# ---------------------------------------------------------------------------
# Synthetic in-memory data
# ---------------------------------------------------------------------------

_TODAY = datetime.date(2026, 10, 23)

# Per-asset anomaly timelines  (asset_id → list of events, oldest first)
ASSET_TIMELINES: dict[str, list[dict[str, Any]]] = {
    "PUMP-07": [
        {"date": "2026-07-14", "hazard": "seal leak", "barrier": "containment tray", "barrier_status": "bypassed",
         "source": "maintenance log ML-2240", "resolved": False},
        {"date": "2026-08-02", "hazard": "seal leak", "barrier": "containment tray", "barrier_status": "bypassed",
         "source": "shift handover SH-0812", "resolved": False},
        {"date": "2026-09-18", "hazard": "seal leak", "barrier": "containment tray", "barrier_status": "bypassed",
         "source": "near-miss NM-0314", "resolved": False},
        {"date": "2026-10-21", "hazard": "seal leak", "barrier": "containment tray", "barrier_status": "bypassed",
         "source": "alarm log AL-1021", "resolved": False},
    ],
    "CONVEYOR-03": [
        {"date": "2026-09-05", "hazard": "belt misalignment", "barrier": "emergency stop tested",
         "barrier_status": "degraded", "source": "near-miss NM-0305", "resolved": False},
        {"date": "2026-10-10", "hazard": "belt misalignment", "barrier": "emergency stop tested",
         "barrier_status": "degraded", "source": "maintenance log ML-2291", "resolved": False},
    ],
    "BOILER-01": [
        {"date": "2026-10-20", "hazard": "pressure relief valve stuck closed", "barrier": "PRV",
         "barrier_status": "failed", "source": "alarm log AL-1020", "resolved": False},
    ],
    "ELEC-PANEL-04": [
        {"date": "2026-08-22", "hazard": "overheating", "barrier": "thermal cutout", "barrier_status": "bypassed",
         "source": "maintenance log ML-2251", "resolved": True},
        {"date": "2026-10-15", "hazard": "overheating", "barrier": "thermal cutout", "barrier_status": "bypassed",
         "source": "shift handover SH-1015", "resolved": False},
    ],
}

# Open risk ledger  (what a CEO sees on one screen)
RISK_LEDGER: list[dict[str, Any]] = [
    {"asset_id": "PUMP-07", "hazard": "seal leak", "barrier": "containment tray",
     "barrier_status": "bypassed", "first_seen": "2026-07-14", "recurrences": 4,
     "days_open": (_TODAY - datetime.date(2026, 7, 14)).days,
     "cost_exposure_usd": 85000, "owner": "Plant Engineer Arun"},
    {"asset_id": "CONVEYOR-03", "hazard": "belt misalignment", "barrier": "emergency stop",
     "barrier_status": "degraded", "first_seen": "2026-09-05", "recurrences": 2,
     "days_open": (_TODAY - datetime.date(2026, 9, 5)).days,
     "cost_exposure_usd": 42000, "owner": "Maintenance Lead Meena"},
    {"asset_id": "BOILER-01", "hazard": "pressure relief valve stuck closed", "barrier": "PRV",
     "barrier_status": "failed", "first_seen": "2026-10-20", "recurrences": 1,
     "days_open": (_TODAY - datetime.date(2026, 10, 20)).days,
     "cost_exposure_usd": 220000, "owner": "Safety Manager Priya"},
    {"asset_id": "ELEC-PANEL-04", "hazard": "overheating", "barrier": "thermal cutout",
     "barrier_status": "bypassed", "first_seen": "2026-10-15", "recurrences": 1,
     "days_open": (_TODAY - datetime.date(2026, 10, 15)).days,
     "cost_exposure_usd": 30000, "owner": "Maintenance Lead Meena"},
]

# Issued work orders and alerts (mutated at runtime)
WORK_ORDERS: list[dict[str, Any]] = []
ALERTS: list[dict[str, Any]] = []
BRIEFS: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def ingest_report(report_text: str) -> dict[str, Any]:
    """Parse a raw near-miss report, maintenance note, or shift handover into a structured event
    (asset_id, hazard, barrier, barrier_status, source) and append it to the asset timeline."""
    # Simple keyword extraction — good enough for synthetic demo data
    text_lower = report_text.lower()
    asset_id = "UNKNOWN"
    for aid in ASSET_TIMELINES:
        if aid.lower() in text_lower or aid.replace("-", " ").lower() in text_lower:
            asset_id = aid
            break

    hazard_keywords = {
        "seal leak": ["seal", "leak", "drip"],
        "belt misalignment": ["belt", "misalign", "conveyor"],
        "pressure relief": ["pressure", "relief", "prv", "valve"],
        "overheating": ["overheat", "thermal", "temperature", "hot"],
    }
    hazard = "unclassified hazard"
    for h, kws in hazard_keywords.items():
        if any(k in text_lower for k in kws):
            hazard = h
            break

    barrier_status = "unknown"
    if any(w in text_lower for w in ["bypass", "bypassed", "disabled", "removed"]):
        barrier_status = "bypassed"
    elif any(w in text_lower for w in ["fail", "failed", "stuck", "broken"]):
        barrier_status = "failed"
    elif any(w in text_lower for w in ["degrad", "worn", "partial"]):
        barrier_status = "degraded"

    event = {
        "date": str(_TODAY),
        "hazard": hazard,
        "barrier": "safety barrier",
        "barrier_status": barrier_status,
        "source": f"ingested report ({report_text[:60].strip()}...)",
        "resolved": False,
    }
    if asset_id != "UNKNOWN":
        ASSET_TIMELINES.setdefault(asset_id, []).append(event)

    return {"asset_id": asset_id, "event": event, "timeline_length": len(ASSET_TIMELINES.get(asset_id, []))}


@tool
def get_asset_timeline(asset_id: str) -> dict[str, Any]:
    """Return the full anomaly timeline for an asset, with a normalised-deviance flag
    (True if the same barrier failure appears 3 or more times unresolved)."""
    asset_id = asset_id.strip().upper()
    timeline = ASSET_TIMELINES.get(asset_id, [])
    if not timeline:
        return {"asset_id": asset_id, "error": "no timeline found", "events": []}

    # Detect normalised deviance: same hazard, barrier not resolved, 3+ times
    from collections import Counter
    open_hazards = [e["hazard"] for e in timeline if not e.get("resolved")]
    counts = Counter(open_hazards)
    normalised_deviance = any(c >= 3 for c in counts.values())
    repeated = [h for h, c in counts.items() if c >= 3]

    return {
        "asset_id": asset_id,
        "events": timeline,
        "event_count": len(timeline),
        "open_count": len(open_hazards),
        "normalised_deviance": normalised_deviance,
        "repeated_hazards": repeated,
    }


@tool
def get_risk_ledger() -> dict[str, Any]:
    """Return the Ignored Warnings Ledger: every open risk, how long it has been open,
    and its cost exposure. This is the screen CEOs remember."""
    total_exposure = sum(r["cost_exposure_usd"] for r in RISK_LEDGER)
    critical = [r for r in RISK_LEDGER if r["cost_exposure_usd"] >= 100000]
    return {
        "open_risks": RISK_LEDGER,
        "total_open": len(RISK_LEDGER),
        "total_cost_exposure_usd": total_exposure,
        "critical_items": len(critical),
    }


@tool
def raise_alert(asset_id: str, hazard: str, severity: str, evidence: str) -> dict[str, Any]:
    """Formally raise a safety alert for an asset (irreversible action, checked by SafetyAlertGuard).
    severity: low | medium | high | critical."""
    alert = {
        "alert_id": f"ALR-{len(ALERTS) + 1:04d}",
        "asset_id": asset_id.strip().upper(),
        "hazard": hazard,
        "severity": severity,
        "evidence": evidence,
        "raised_at": str(_TODAY),
        "status": "open",
    }
    ALERTS.append(alert)
    return alert


@tool
def draft_work_order(asset_id: str, hazard: str, evidence: str, urgency: str = "normal") -> dict[str, Any]:
    """Draft a maintenance work order for an asset and hazard, with evidence linked to source lines
    (irreversible action, checked by SafetyAlertGuard).
    urgency: low | normal | urgent | emergency."""
    wo = {
        "work_order_id": f"WO-{len(WORK_ORDERS) + 1:04d}",
        "asset_id": asset_id.strip().upper(),
        "hazard": hazard,
        "urgency": urgency,
        "description": (
            f"Inspect and repair {asset_id} for {hazard}. "
            f"Evidence: {evidence[:300]}"
        ),
        "evidence_lines": [e.strip() for e in evidence.split(".") if e.strip()][:5],
        "created_at": str(_TODAY),
        "status": "draft",
    }
    WORK_ORDERS.append(wo)
    return wo


@tool
def send_evidence_brief(asset_id: str, owner: str, summary: str, evidence: str) -> dict[str, Any]:
    """Send a one-page evidence brief to the asset owner with every claim linked to its source line
    (irreversible action, checked by SafetyAlertGuard)."""
    brief = {
        "brief_id": f"BRIEF-{len(BRIEFS) + 1:04d}",
        "asset_id": asset_id.strip().upper(),
        "owner": owner,
        "summary": summary,
        "evidence": evidence,
        "sent_at": str(_TODAY),
        "status": "sent",
    }
    BRIEFS.append(brief)
    return {"sent": True, "brief_id": brief["brief_id"], "recipient": owner}


TOOLS = [
    ingest_report,
    get_asset_timeline,
    get_risk_ledger,
    raise_alert,
    draft_work_order,
    send_evidence_brief,
]
