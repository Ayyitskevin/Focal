"""Offers — read-only operator review queue for Plutus print/album offers.

After Argus analyzes a gallery, Plutus proposes a print/album offer and Mise records the
summary on the gallery row (`plutus_last_*`). Today that status is scattered one-per-
gallery; this page consolidates every gallery with an offer into one newest-first review
queue — status, bundle count, estimated value, and click-through to the Plutus offer /
pitch — plus the total estimated pipeline value.

Offers are **proposals (A1 drafts)**: this surface is read-only and never charges, sends,
or creates an invoice. The operator reviews here and acts on Plutus (where the offer is
edited) or shares the offer link deliberately. Persisting an "approved" state would need a
schema column and is a separate red-light follow-up.
"""

import csv
import io
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

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


def _status_meta(status: str) -> dict:
    return _STATUS_META.get(status, {"label": status or "—", "bg": "#eceff1", "color": "#5b6b73"})


def _dollars(cents) -> str:
    return f"${cents / 100:,.2f}" if cents is not None else ""


def _rows(status: str) -> list[dict]:
    base = """SELECT g.id, g.slug, g.title, g.client_id, g.plutus_last_status,
                  g.plutus_last_offer_url, g.plutus_last_pitch_url, g.plutus_last_bundle_count,
                  g.plutus_last_estimated_cents, g.plutus_last_error, g.plutus_last_at,
                  c.name AS client_name, c.company
           FROM galleries g LEFT JOIN clients c ON c.id = g.client_id
           WHERE g.plutus_last_status IS NOT NULL """
    if status != "all":
        raw = db.all_(
            base + "AND g.plutus_last_status=? ORDER BY g.plutus_last_at DESC, g.id DESC LIMIT ?",
            (status, _LIMIT),
        )
    else:
        raw = db.all_(base + "ORDER BY g.plutus_last_at DESC, g.id DESC LIMIT ?", (_LIMIT,))
    out = []
    for r in raw:
        meta = _status_meta(r["plutus_last_status"])
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
            }
        )
    return out


def _counts() -> dict:
    raw = db.all_(
        "SELECT plutus_last_status AS s FROM galleries WHERE plutus_last_status IS NOT NULL "
        "ORDER BY plutus_last_at DESC, id DESC LIMIT ?",
        (_LIMIT,),
    )
    counts = {"all": len(raw), "done": 0, "error": 0}
    for r in raw:
        if r["s"] in counts:
            counts[r["s"]] += 1
    return counts


def _pipeline_value() -> str:
    """Total estimated value of ready ('done') offers — an at-a-glance upsell pipeline."""
    row = db.one(
        "SELECT COALESCE(SUM(plutus_last_estimated_cents), 0) AS cents FROM galleries "
        "WHERE plutus_last_status='done' AND plutus_last_estimated_cents IS NOT NULL"
    )
    return _dollars(row["cents"] if row else 0)


@router.get("", response_class=HTMLResponse)
async def offers_view(request: Request, status: str = "all"):
    if status not in _FILTERS:
        status = "all"
    counts = _counts()
    filters = [
        {
            "key": k,
            "label": "All" if k == "all" else _status_meta(k)["label"],
            "n": counts[k],
            "active": k == status,
        }
        for k in _FILTERS
    ]
    return templates.TemplateResponse(
        request,
        "admin/offers.html",
        {
            "events": _rows(status),
            "filters": filters,
            "status": status,
            "total": counts["all"],
            "pipeline_value": _pipeline_value(),
        },
    )


@router.get(".csv", response_class=PlainTextResponse)
async def offers_csv():
    """Open offers as CSV — an upsell pipeline snapshot for review."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Time", "Gallery", "Client", "Status", "Bundles", "Estimated", "Offer", "Pitch"])
    for e in _rows("all"):
        w.writerow(
            [
                _localtime(e["created_at"]),
                e["title"],
                e["client"],
                e["status"],
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
