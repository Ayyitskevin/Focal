"""Albums — the operator review surface for Mnemosyne album drafts.

A draft is a proposed, ordered subset of a gallery's photos laid out into spreads. This
page is the human-review half of the audit's "model proposes, deterministic code
validates, human approves" loop (§11.4, ADR 0009/0011):

* **Propose** a baseline layout for a gallery (deterministic today; a registered Mnemosyne
  provider plugs into the same seam later). ``albums.propose_draft`` validates before it
  persists, so a bad proposal never becomes a stored draft.
* **Review** a draft's spreads and the photos it OMITTED — re-validated against the
  gallery's current photos at view time, so a since-deleted asset surfaces, never hides.
* **Approve / reject** — a human decision recorded on the draft. Nothing here prints,
  orders, or charges; that would be a separate, deliberate flow.

Writes are admin-gated; same-origin is enforced by the global CSRF middleware.
"""

import csv
import io
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .. import albums, db, security
from ..render import _localtime, templates

log = logging.getLogger("mise.admin.albums")
router = APIRouter(prefix="/admin/albums", dependencies=[Depends(security.require_admin)])

_FILTERS = ["all", "draft", "approved", "rejected"]
_STATUS_META = {
    "draft": {"label": "Draft", "bg": "#f7ecd2", "color": "#9a7a2c"},
    "approved": {"label": "Approved", "bg": "#e1f2e9", "color": "#2f7d57"},
    "rejected": {"label": "Rejected", "bg": "#f3e3e5", "color": "#7C2F38"},
}


def _status_meta(status: str) -> dict:
    return _STATUS_META.get(status, {"label": status or "—", "bg": "#eceff1", "color": "#5b6b73"})


def _redirect(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    # urlencode so free-form messages (which contain spaces and '#', e.g. "No gallery #9")
    # survive as query values instead of being truncated at the URL-fragment delimiter.
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    return RedirectResponse(f"{path}?{urlencode(params)}" if params else path, status_code=303)


def _rows(status: str) -> list[dict]:
    drafts = albums.list_drafts(status=None if status == "all" else status)
    out = []
    for d in drafts:
        meta = _status_meta(d["status"])
        out.append(
            {
                "id": d["id"],
                "gallery_id": d["gallery_id"],
                "title": d["title"] or d["slug"],
                "status": d["status"],
                "status_label": meta["label"],
                "status_bg": meta["bg"],
                "status_color": meta["color"],
                "spread_count": d["spread_count"],
                "placement_count": d["placement_count"],
                "provider": d["provider"] or "",
                "model": d["model"] or "",
                "created_at": d["created_at"],
                "ordered_at": d["ordered_at"],
            }
        )
    return out


def _counts() -> dict:
    counts = {k: 0 for k in _FILTERS}
    for d in albums.list_drafts():
        counts["all"] += 1
        if d["status"] in counts:
            counts[d["status"]] += 1
    return counts


@router.get("", response_class=HTMLResponse)
async def albums_view(request: Request, status: str = "all", msg: str = "", err: str = ""):
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
        "admin/albums.html",
        {
            "drafts": _rows(status),
            "filters": filters,
            "status": status,
            "total": counts["all"],
            "msg": msg,
            "err": err,
        },
    )


@router.get("/{draft_id}", response_class=HTMLResponse)
async def album_detail(request: Request, draft_id: int, msg: str = "", err: str = ""):
    draft = albums.get_draft(draft_id)
    if not draft:
        return _redirect("/admin/albums", err="No such album draft.")
    gallery = db.get_or_404(
        "SELECT id, slug, title, project_id FROM galleries WHERE id=?", (draft["gallery_id"],)
    )
    placements = albums.draft_placements(draft_id)
    # Re-validate against the gallery's CURRENT eligible photos: surfaces a placement whose
    # asset was deleted/unpublished since, and the photos this draft omits.
    revalidation = albums.validate_layout(
        draft["gallery_id"],
        [{"asset_id": p["asset_id"], "spread": p["spread"], "slot": p["slot"]} for p in placements],
    )
    # Group placements into spreads for display.
    spreads: dict[int, list[dict]] = {}
    for p in placements:
        spreads.setdefault(p["spread"], []).append(p)
    spread_view = [
        {"spread": s, "slots": sorted(spreads[s], key=lambda x: x["slot"])} for s in sorted(spreads)
    ]
    meta = _status_meta(draft["status"])
    return templates.TemplateResponse(
        request,
        "admin/album_detail.html",
        {
            "draft": draft,
            "gallery": dict(gallery),
            # An ordered album on a project can be turned into a draft invoice line in one click.
            "can_invoice": bool(draft["ordered_at"]) and gallery["project_id"] is not None,
            "spreads": spread_view,
            "placement_count": len(placements),
            "omitted": list(revalidation.omitted),
            "issues": [{"code": i.code, "detail": i.detail} for i in revalidation.issues],
            "valid": revalidation.ok,
            "status_label": meta["label"],
            "status_bg": meta["bg"],
            "status_color": meta["color"],
            "msg": msg,
            "err": err,
        },
    )


@router.post("/propose")
async def propose(request: Request):
    form = await request.form()
    try:
        gallery_id = int(form.get("gallery_id") or "")
    except (TypeError, ValueError):
        return _redirect("/admin/albums", err="A numeric gallery id is required.")
    if not db.one("SELECT id FROM galleries WHERE id=?", (gallery_id,)):
        return _redirect("/admin/albums", err=f"No gallery #{gallery_id}.")
    draft_id = albums.propose_draft(gallery_id)
    if draft_id is None:
        return _redirect(
            "/admin/albums", err=f"Gallery #{gallery_id} has no ready photos to lay out."
        )
    log.info("album draft %s proposed for gallery %s", draft_id, gallery_id)
    return _redirect(f"/admin/albums/{draft_id}", msg="Baseline album proposed — review below.")


@router.post("/{draft_id}/approve")
async def approve(draft_id: int):
    if not albums.get_draft(draft_id):
        return _redirect("/admin/albums", err="No such album draft.")
    albums.set_status(draft_id, "approved")
    log.info("album draft %s approved", draft_id)
    return _redirect(f"/admin/albums/{draft_id}", msg="Album approved.")


@router.post("/{draft_id}/reject")
async def reject(draft_id: int):
    if not albums.get_draft(draft_id):
        return _redirect("/admin/albums", err="No such album draft.")
    albums.set_status(draft_id, "rejected")
    log.info("album draft %s rejected", draft_id)
    return _redirect(f"/admin/albums/{draft_id}", msg="Album rejected.")


@router.post("/{draft_id}/order")
async def order(request: Request, draft_id: int):
    """Mark an approved album ordered with its spec — record-only (ADR 0019). Prints nothing,
    hands off to no vendor, charges nothing."""
    if not albums.get_draft(draft_id):
        return _redirect("/admin/albums", err="No such album draft.")
    form = await request.form()
    try:
        albums.mark_ordered(
            draft_id,
            size=form.get("size"),
            cover=form.get("cover"),
            notes=form.get("notes"),
        )
    except albums.OrderError as e:
        return _redirect(f"/admin/albums/{draft_id}", err=str(e))
    log.info("album draft %s marked ordered", draft_id)
    return _redirect(f"/admin/albums/{draft_id}", msg="Album marked ordered.")


@router.post("/{draft_id}/order/clear")
async def order_clear(draft_id: int):
    if not albums.get_draft(draft_id):
        return _redirect("/admin/albums", err="No such album draft.")
    albums.clear_order(draft_id)
    log.info("album draft %s order cleared", draft_id)
    return _redirect(f"/admin/albums/{draft_id}", msg="Order mark cleared.")


# ── print-ready export (record-only — for the operator to hand to their lab) ────────────────


def _export_data(draft_id: int):
    """Shared fetch for the print sheet + CSV: an APPROVED draft, its gallery, and its
    placements in spread/slot order. Returns ``(draft, gallery, placements)`` or None."""
    draft = albums.get_draft(draft_id)
    if not draft or draft["status"] != "approved":
        return None
    gallery = db.one("SELECT id, slug, title FROM galleries WHERE id=?", (draft["gallery_id"],))
    if not gallery:
        return None
    return draft, dict(gallery), albums.draft_placements(draft_id)


@router.get("/{draft_id}/order-sheet", response_class=HTMLResponse)
async def order_sheet(request: Request, draft_id: int):
    """A standalone, print-ready order sheet for an approved album — the operator prints it to
    PDF for their lab. Read-only; shows the spec + every photo in spread/slot order."""
    data = _export_data(draft_id)
    if data is None:
        return _redirect(
            "/admin/albums", err="The order sheet is available once the album is approved."
        )
    draft, gallery, placements = data
    spreads: dict[int, list[dict]] = {}
    for p in placements:
        spreads.setdefault(p["spread"], []).append(p)
    spread_view = [
        {"spread": s, "slots": sorted(spreads[s], key=lambda x: x["slot"])} for s in sorted(spreads)
    ]
    return templates.TemplateResponse(
        request,
        "admin/album_order_sheet.html",
        {
            "draft": draft,
            "gallery": gallery,
            "spreads": spread_view,
            "photo_count": len(placements),
        },
    )


@router.get("/{draft_id}/order.csv", response_class=PlainTextResponse)
async def order_csv(draft_id: int):
    """The album's photo manifest as CSV — the ordered file list a lab pulls from. Spec rows
    up top, then one row per photo in spread/slot order."""
    data = _export_data(draft_id)
    if data is None:
        return _redirect(
            "/admin/albums", err="The manifest is available once the album is approved."
        )
    draft, gallery, placements = data
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Album", gallery["title"] or gallery["slug"]])
    w.writerow(["Gallery", f"#{gallery['id']} {gallery['slug']}"])
    w.writerow(["Size", draft["order_size"] or ""])
    w.writerow(["Cover", draft["order_cover"] or ""])
    w.writerow(["Notes", draft["order_notes"] or ""])
    w.writerow(
        [
            "Ordered",
            _localtime(draft["ordered_at"]) if draft["ordered_at"] else "not marked ordered",
        ]
    )
    w.writerow(["Photos", len(placements)])
    w.writerow([])
    w.writerow(["Spread", "Slot", "Filename", "Asset ID"])
    for p in placements:
        w.writerow([p["spread"] + 1, p["slot"] + 1, p["filename"], p["asset_id"]])
    filename = f"album-{gallery['slug']}-manifest.csv"
    return PlainTextResponse(
        buf.getvalue(), headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
