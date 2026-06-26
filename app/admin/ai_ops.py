"""AI operations — one read-only pane over every AI capability Mise consolidated.

The consolidation shipped four separate surfaces (the ai_runs ledger, the validation gate,
the offers queue, the album drafts). This page is the morning glance that ties them
together: what needs my attention across all of them, and how is the AI spend/activity
trending. It is the "one pane of glass" the Solo Studio OS arc was building toward.

Pure aggregation — it reads the ai_runs ledger, the per-gallery offer summary, album
drafts, and the validation gate, and writes nothing. Every actionable item links to the
queue that owns the action; nothing is decided or promoted here.
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .. import config, db, security, validation
from ..render import templates

log = logging.getLogger("mise.admin.ai_ops")
router = APIRouter(prefix="/admin/ai-ops", dependencies=[Depends(security.require_admin)])

_CAP_LABEL = {"vision": "Vision", "offers": "Offers", "content": "Content", "albums": "Albums"}


def _dollars(cents) -> str:
    return f"${cents / 100:,.2f}" if cents is not None else "$0.00"


def _ledger() -> dict:
    """Summary of the ai_runs provenance ledger: volume, recent volume, non-OK runs, and
    reported cost. by_capability powers a small breakdown."""
    row = db.one(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS errors,
                  SUM(CASE WHEN created_at >= datetime('now','-7 days') THEN 1 ELSE 0 END) AS last7,
                  COALESCE(SUM(cost_usd), 0) AS cost
           FROM ai_runs"""
    )
    by_cap = [
        {
            "capability": r["capability"],
            "label": _CAP_LABEL.get(r["capability"], r["capability"]),
            "n": r["n"],
        }
        for r in db.all_(
            "SELECT capability, COUNT(*) AS n FROM ai_runs GROUP BY capability ORDER BY n DESC"
        )
    ]
    total = row["total"] if row else 0
    return {
        "total": total,
        "errors": (row["errors"] or 0) if row else 0,
        "last7": (row["last7"] or 0) if row else 0,
        "cost": f"${(row['cost'] or 0):,.4f}" if row else "$0.0000",
        "by_capability": by_cap,
    }


def _offers_pending() -> dict:
    row = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(plutus_last_estimated_cents), 0) AS cents
           FROM galleries
           WHERE plutus_last_status = 'done' AND plutus_offer_decision IS NULL"""
    )
    return {"count": row["n"] if row else 0, "value": _dollars(row["cents"] if row else 0)}


def _albums_pending() -> int:
    row = db.one("SELECT COUNT(*) AS n FROM album_drafts WHERE status = 'draft'")
    return row["n"] if row else 0


def _vision_gate() -> dict:
    rep = validation.promotion_report("vision", "argus", config.VISION_CHALLENGER_MODEL)
    return {
        "challenger": config.VISION_CHALLENGER_MODEL,
        "ready": rep.ready,
        "verdict": "Ready to promote" if rep.ready else "Not ready",
        "paired": rep.paired,
        "min_paired": rep.min_paired,
        "total_items": rep.total_items,
    }


@router.get("", response_class=HTMLResponse)
async def ai_ops_view(request: Request):
    offers = _offers_pending()
    albums_pending = _albums_pending()
    gate = _vision_gate()
    ledger = _ledger()
    # The "needs attention" tiles, in triage order. attention=True draws the eye.
    attention = [
        {
            "label": "Offers awaiting a decision",
            "value": f"{offers['count']}",
            "sub": f"{offers['value']} proposed",
            "href": "/admin/offers?decision=undecided",
            "attention": offers["count"] > 0,
        },
        {
            "label": "Album drafts to review",
            "value": f"{albums_pending}",
            "sub": "proposed, awaiting approve/reject",
            "href": "/admin/albums?status=draft",
            "attention": albums_pending > 0,
        },
        {
            "label": "Vision promotion gate",
            "value": gate["verdict"],
            "sub": f"paired {gate['paired']}/{gate['min_paired']} · {gate['total_items']} in set",
            "href": "/admin/validation",
            "attention": not gate["ready"],
        },
        {
            "label": "Provider errors in ledger",
            "value": f"{ledger['errors']}",
            "sub": "non-OK runs recorded",
            "href": "/admin/ai-runs",
            "attention": ledger["errors"] > 0,
        },
    ]
    return templates.TemplateResponse(
        request,
        "admin/ai_ops.html",
        {"attention": attention, "ledger": ledger, "gate": gate},
    )
