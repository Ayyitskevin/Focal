"""AI cost & activity — read-only spend report over the ai_runs ledger.

The audit names cloud cost runaway (COGS) as a real risk. The provenance ledger already
records ``cost_usd`` per provider call, so this turns that into the monitoring view: total
spend and run volume over a window, broken down by capability and by day, with CSV export
for the evidence trail. Pure aggregation — it reads the append-only ledger and writes
nothing; cost is informational and never drives an action (ADR 0013/0015).
"""

import csv
import io
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .. import db, security
from ..render import templates

log = logging.getLogger("mise.admin.ai_cost")
router = APIRouter(prefix="/admin/ai-cost", dependencies=[Depends(security.require_admin)])

_WINDOWS = {7: "7 days", 30: "30 days", 90: "90 days"}
_DEFAULT_DAYS = 30
_CAP_LABEL = {"vision": "Vision", "offers": "Offers", "content": "Content", "albums": "Albums"}


def _modifier(days: int) -> str:
    return f"-{days} days"


def _money(cost) -> str:
    return f"${(cost or 0):,.4f}"


def _totals(days: int) -> dict:
    row = db.one(
        """SELECT COUNT(*) AS runs,
                  COALESCE(SUM(cost_usd), 0) AS cost,
                  SUM(CASE WHEN cost_usd IS NOT NULL THEN 1 ELSE 0 END) AS costed,
                  SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS errors
           FROM ai_runs WHERE created_at >= datetime('now', ?)""",
        (_modifier(days),),
    )
    return {
        "runs": row["runs"] if row else 0,
        "cost": _money(row["cost"] if row else 0),
        "costed": (row["costed"] or 0) if row else 0,
        "errors": (row["errors"] or 0) if row else 0,
    }


def _by_capability(days: int) -> list[dict]:
    rows = db.all_(
        """SELECT capability, COUNT(*) AS runs,
                  COALESCE(SUM(cost_usd), 0) AS cost,
                  SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS errors
           FROM ai_runs WHERE created_at >= datetime('now', ?)
           GROUP BY capability ORDER BY cost DESC, runs DESC""",
        (_modifier(days),),
    )
    return [
        {
            "capability": r["capability"],
            "label": _CAP_LABEL.get(r["capability"], r["capability"]),
            "runs": r["runs"],
            "cost": _money(r["cost"]),
            "errors": r["errors"],
        }
        for r in rows
    ]


def _by_day(days: int) -> list[dict]:
    rows = db.all_(
        """SELECT date(created_at) AS day, COUNT(*) AS runs,
                  COALESCE(SUM(cost_usd), 0) AS cost
           FROM ai_runs WHERE created_at >= datetime('now', ?)
           GROUP BY date(created_at) ORDER BY day DESC""",
        (_modifier(days),),
    )
    return [{"day": r["day"], "runs": r["runs"], "cost": _money(r["cost"])} for r in rows]


def _window(days) -> int:
    try:
        days = int(days)
    except (TypeError, ValueError):
        return _DEFAULT_DAYS
    return days if days in _WINDOWS else _DEFAULT_DAYS


@router.get("", response_class=HTMLResponse)
async def ai_cost_view(request: Request, days: int = _DEFAULT_DAYS):
    days = _window(days)
    return templates.TemplateResponse(
        request,
        "admin/ai_cost.html",
        {
            "days": days,
            "windows": sorted(_WINDOWS),
            "totals": _totals(days),
            "by_capability": _by_capability(days),
            "by_day": _by_day(days),
        },
    )


@router.get(".csv", response_class=PlainTextResponse)
async def ai_cost_csv(days: int = _DEFAULT_DAYS):
    """Per-day spend over the window as CSV — the COGS evidence artifact."""
    days = _window(days)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Day", "Runs", "Cost (USD)"])
    for d in _by_day(days):
        w.writerow([d["day"], d["runs"], d["cost"].lstrip("$")])
    return PlainTextResponse(
        buf.getvalue(),
        headers={"Content-Disposition": 'attachment; filename="kleephotography_ai_cost.csv"'},
    )
