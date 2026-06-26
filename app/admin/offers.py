"""Offers — operator review queue for Plutus print/album offers.

After Argus analyzes a gallery, Plutus proposes a print/album offer and Mise records the
summary on the gallery row (`plutus_last_*`). This page consolidates every gallery with an
offer into one newest-first review queue — status, bundle count, estimated value, and
click-through to the Plutus offer / pitch — plus the estimated pipeline value.

Offers are **proposals (A1 drafts)**. The operator triages them here: **approve** the ones
worth pursuing or **reject** the rest, persisted per gallery (`plutus_offer_decision`,
migration 068). A decision records the human's call ONLY — it never charges, sends, or
creates an invoice; the offer is still edited in Plutus and shared deliberately.
"""

import csv
import io
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .. import db, security
from ..render import _localtime, templates

log = logging.getLogger("mise.admin.offers")
router = APIRouter(prefix="/admin/offers", dependencies=[Depends(security.require_admin)])

_LIMIT = 500  # newest N offers — a review queue, not a full dump

_FILTERS = ["all", "done", "error"]
_STATUS_META = {
    "done": {"label": "Ready", "bg": "#e1f2e9", "color": "#2f7d57"},
    "error": {"label": "Error", "bg": "#f3e3e5", "color": "#7C2F38"},
}

# Operator decision on a proposed offer. NULL/'' = undecided.
_DECISIONS = ("approved", "rejected")
_DECISION_FILTERS = ["any", "undecided", "approved", "rejected"]
_DECISION_META = {
    "approved": {"label": "Approved", "bg": "#e1f2e9", "color": "#2f7d57"},
    "rejected": {"label": "Rejected", "bg": "#f3e3e5", "color": "#7C2F38"},
}


def _status_meta(status: str) -> dict:
    return _STATUS_META.get(status, {"label": status or "—", "bg": "#eceff1", "color": "#5b6b73"})


def _decision_meta(decision: str | None) -> dict:
    return _DECISION_META.get(
        decision or "", {"label": "Undecided", "bg": "#eceff1", "color": "#5b6b73"}
    )


def _dollars(cents) -> str:
    return f"${cents / 100:,.2f}" if cents is not None else ""


def _redirect(msg: str = "", err: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    return RedirectResponse(
        f"/admin/offers{('?' + urlencode(params)) if params else ''}", status_code=303
    )


def _set_decision(gallery_id: int, decision: str | None) -> None:
    """Persist (or clear) the operator's decision on a gallery's offer. ``decision`` is
    'approved' / 'rejected', or None to reset to undecided. Raises on an unknown value."""
    if decision is not None and decision not in _DECISIONS:
        raise ValueError(f"invalid offer decision: {decision!r}")
    if decision is None:
        db.run(
            "UPDATE galleries SET plutus_offer_decision=NULL, plutus_offer_decided_at=NULL "
            "WHERE id=?",
            (gallery_id,),
        )
    else:
        db.run(
            "UPDATE galleries SET plutus_offer_decision=?, plutus_offer_decided_at=datetime('now') "
            "WHERE id=?",
            (decision, gallery_id),
        )


def _rows(status: str, decision: str = "any") -> list[dict]:
    base = """SELECT g.id, g.slug, g.title, g.client_id, g.plutus_last_status,
                  g.plutus_last_offer_url, g.plutus_last_pitch_url, g.plutus_last_bundle_count,
                  g.plutus_last_estimated_cents, g.plutus_last_error, g.plutus_last_at,
                  g.plutus_offer_decision, g.plutus_offer_decided_at,
                  c.name AS client_name, c.company
           FROM galleries g LEFT JOIN clients c ON c.id = g.client_id
           WHERE g.plutus_last_status IS NOT NULL """
    params: list = []
    if status != "all":
        base += "AND g.plutus_last_status=? "
        params.append(status)
    if decision == "undecided":
        base += "AND g.plutus_offer_decision IS NULL "
    elif decision in _DECISIONS:
        base += "AND g.plutus_offer_decision=? "
        params.append(decision)
    base += "ORDER BY g.plutus_last_at DESC, g.id DESC LIMIT ?"
    params.append(_LIMIT)
    raw = db.all_(base, tuple(params))
    out = []
    for r in raw:
        meta = _status_meta(r["plutus_last_status"])
        dmeta = _decision_meta(r["plutus_offer_decision"])
        out.append(
            {
                "gallery_id": r["id"],
                "slug": r["slug"],
                "title": r["title"] or r["slug"],
                "client": (r["company"] or r["client_name"] or "").strip(),
                "status": r["plutus_last_status"],
                "status_label": meta["label"],
                "status_bg": meta["bg"],
                "status_color": meta["color"],
                "bundle_count": r["plutus_last_bundle_count"],
                "estimated": _dollars(r["plutus_last_estimated_cents"]),
                "offer_url": r["plutus_last_offer_url"] or "",
                "pitch_url": r["plutus_last_pitch_url"] or "",
                "error": r["plutus_last_error"] or "",
                "created_at": r["plutus_last_at"],
                "decision": r["plutus_offer_decision"] or "",
                "decision_label": dmeta["label"],
                "decision_bg": dmeta["bg"],
                "decision_color": dmeta["color"],
            }
        )
    return out


def _counts() -> dict:
    # Count over the WHOLE offer set (no LIMIT) so the filter-tab counts and the pipeline
    # totals describe the same population; only the listing in _rows is capped at _LIMIT.
    raw = db.all_(
        "SELECT plutus_last_status AS s FROM galleries WHERE plutus_last_status IS NOT NULL"
    )
    counts = {"all": len(raw), "done": 0, "error": 0}
    for r in raw:
        if r["s"] in counts:
            counts[r["s"]] += 1
    return counts


def _decision_counts() -> dict:
    raw = db.all_(
        "SELECT plutus_offer_decision AS d FROM galleries WHERE plutus_last_status IS NOT NULL"
    )
    counts = {"any": len(raw), "undecided": 0, "approved": 0, "rejected": 0}
    for r in raw:
        key = "undecided" if r["d"] is None else r["d"]
        if key in counts:
            counts[key] += 1
    return counts


def _pipeline_value(*, approved_only: bool = False) -> str:
    """Total estimated value of ready ('done') offers. With ``approved_only`` it sums only
    offers the operator approved — the committed pipeline vs. the full proposed pipeline."""
    sql = (
        "SELECT COALESCE(SUM(plutus_last_estimated_cents), 0) AS cents FROM galleries "
        "WHERE plutus_last_status='done' AND plutus_last_estimated_cents IS NOT NULL"
    )
    if approved_only:
        sql += " AND plutus_offer_decision='approved'"
    row = db.one(sql)
    return _dollars(row["cents"] if row else 0)


@router.get("", response_class=HTMLResponse)
async def offers_view(
    request: Request, status: str = "all", decision: str = "any", msg: str = "", err: str = ""
):
    if status not in _FILTERS:
        status = "all"
    if decision not in _DECISION_FILTERS:
        decision = "any"
    counts = _counts()
    dcounts = _decision_counts()
    filters = [
        {
            "key": k,
            "label": "All" if k == "all" else _status_meta(k)["label"],
            "n": counts[k],
            "active": k == status,
        }
        for k in _FILTERS
    ]
    decision_filters = [
        {
            "key": k,
            "label": "Any decision" if k == "any" else _decision_meta(k)["label"],
            "n": dcounts.get(k, 0),
            "active": k == decision,
        }
        for k in _DECISION_FILTERS
    ]
    return templates.TemplateResponse(
        request,
        "admin/offers.html",
        {
            "events": _rows(status, decision),
            "filters": filters,
            "decision_filters": decision_filters,
            "status": status,
            "decision": decision,
            "total": counts["all"],
            "pipeline_value": _pipeline_value(),
            "approved_value": _pipeline_value(approved_only=True),
            "msg": msg,
            "err": err,
        },
    )


def _decision_route(gallery_id: int, decision: str | None, what: str) -> RedirectResponse:
    row = db.one("SELECT plutus_last_status FROM galleries WHERE id=?", (gallery_id,))
    if not row:
        return _redirect(err=f"No gallery #{gallery_id}.")
    if row["plutus_last_status"] is None:
        return _redirect(err=f"Gallery #{gallery_id} has no offer to decide on.")
    _set_decision(gallery_id, decision)
    log.info("offer for gallery %s -> %s", gallery_id, decision or "undecided")
    return _redirect(msg=what)


@router.post("/{gallery_id}/approve")
async def approve(gallery_id: int):
    return _decision_route(gallery_id, "approved", "Offer approved.")


@router.post("/{gallery_id}/reject")
async def reject(gallery_id: int):
    return _decision_route(gallery_id, "rejected", "Offer rejected.")


@router.post("/{gallery_id}/reset")
async def reset(gallery_id: int):
    return _decision_route(gallery_id, None, "Decision cleared.")


@router.get(".csv", response_class=PlainTextResponse)
async def offers_csv():
    """Open offers as CSV — an upsell pipeline snapshot for review."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "Time",
            "Gallery",
            "Client",
            "Status",
            "Decision",
            "Bundles",
            "Estimated",
            "Offer",
            "Pitch",
        ]
    )
    for e in _rows("all"):
        w.writerow(
            [
                _localtime(e["created_at"]),
                e["title"],
                e["client"],
                e["status"],
                e["decision"] or "undecided",
                e["bundle_count"],
                e["estimated"],
                e["offer_url"],
                e["pitch_url"],
            ]
        )
    return PlainTextResponse(
        buf.getvalue(),
        headers={"Content-Disposition": 'attachment; filename="kleephotography_offers.csv"'},
    )
