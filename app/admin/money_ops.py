"""Money operations — one read-only pane over the studio's money path.

The AR figure, collected revenue, and past-due invoices live on separate pages today
(financials, invoices). This is the money-path analog of /admin/ai-ops: the morning glance at
what needs chasing — invoices past due — plus the headline numbers (collected, outstanding AR).
Pure aggregation over the REAL invoices / payments tables; it writes nothing and every tile
links to the page that owns the action.
"""

import datetime as dt
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .. import db, security
from ..render import templates
from . import common

log = logging.getLogger("mise.admin.money_ops")
router = APIRouter(prefix="/admin/money-ops", dependencies=[Depends(security.require_admin)])

# AR aging bands past the due date — the "how stale is the money" breakdown.
_AGING_BANDS = [
    ("d1_30", "1–30 days"),
    ("d31_60", "31–60 days"),
    ("d61_90", "61–90 days"),
    ("d90", "90+ days"),
]


def _dollars(cents) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def aging_buckets(rows, today: dt.date) -> dict:
    """PURE (unit-tested): bucket open-invoice balances by days past the due date. Each row is
    ``{owed_cents, due_date}`` (due_date 'YYYY-MM-DD' or None). Not-yet-due and no-due-date land in
    'current'; everything else falls in 1–30 / 31–60 / 61–90 / 90+ by age. No I/O."""
    out = {k: {"cents": 0, "n": 0} for k in ["current", *(k for k, _ in _AGING_BANDS)]}
    for r in rows:
        owed = r["owed_cents"] or 0
        due = r["due_date"]
        days = (today - dt.date.fromisoformat(due)).days if due else 0
        if days <= 0:
            band = "current"
        elif days <= 30:
            band = "d1_30"
        elif days <= 60:
            band = "d31_60"
        elif days <= 90:
            band = "d61_90"
        else:
            band = "d90"
        out[band]["cents"] += owed
        out[band]["n"] += 1
    return out


def _collected_recent() -> dict:
    """Cash collected in the last 30 days, from the payments ledger."""
    row = db.one(
        "SELECT COALESCE(SUM(amount_cents), 0) AS cents, COUNT(*) AS n "
        "FROM payments WHERE created_at >= datetime('now', '-30 days')"
    )
    return {"cents": row["cents"] if row else 0, "n": row["n"] if row else 0}


def _overdue() -> dict:
    """Open invoices past their due date — AR that needs chasing. A deposit_paid invoice owes
    (total - deposit); sent/viewed owe the full total (mirrors common.open_invoice_balance)."""
    row = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(CASE
             WHEN status='deposit_paid' THEN total_cents - deposit_cents
             ELSE total_cents END), 0) AS cents
           FROM invoices
           WHERE status IN ('sent','viewed','deposit_paid')
                 AND due_date IS NOT NULL AND due_date < date('now')"""
    )
    return {"count": row["n"] if row else 0, "cents": row["cents"] if row else 0}


@router.get("", response_class=HTMLResponse)
async def money_ops_view(request: Request):
    ar = common.open_invoice_balance()
    collected = _collected_recent()
    overdue = _overdue()
    # AR aging — bucket every open invoice's balance by how far past due it is.
    open_rows = db.all_(
        """SELECT due_date, CASE WHEN status='deposit_paid' THEN total_cents - deposit_cents
                  ELSE total_cents END AS owed_cents
           FROM invoices WHERE status IN ('sent','viewed','deposit_paid')"""
    )
    buckets = aging_buckets(open_rows, dt.date.today())
    aging = [
        {
            "label": lbl,
            "cents": buckets[k]["cents"],
            "n": buckets[k]["n"],
            "stale": k in ("d61_90", "d90"),
        }
        for k, lbl in [("current", "Not yet due"), *_AGING_BANDS]
    ]
    # "Needs attention" tiles, in chase order. attention=True draws the eye.
    attention = [
        {
            "label": "Invoices past due",
            "value": f"{overdue['count']}",
            "sub": f"{_dollars(overdue['cents'])} owed, past the due date",
            "href": "/admin/financials",
            "attention": overdue["count"] > 0,
        },
    ]
    # Headline money tiles (informational).
    summary = [
        {
            "label": "Collected (30 days)",
            "value": _dollars(collected["cents"]),
            "sub": f"{collected['n']} payment{'' if collected['n'] == 1 else 's'}",
        },
        {
            "label": "Outstanding AR",
            "value": _dollars(ar["cents"]),
            "sub": f"{ar['n']} open invoice{'' if ar['n'] == 1 else 's'}",
        },
    ]
    return templates.TemplateResponse(
        request,
        "admin/money_ops.html",
        {"attention": attention, "summary": summary, "aging": aging, "_dollars": _dollars},
    )
