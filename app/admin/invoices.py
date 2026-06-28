"""Invoices — line items + optional deposit split. Send locks; Stripe handles payment."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config, db, jobs, plutus_recommend, security
from ..render import templates
from . import common
from .proposals import MAX_ITEM_ROWS, parse_items
from .studio import get_project

log = logging.getLogger("mise.admin.invoices")
router = APIRouter(prefix="/admin/studio", dependencies=[Depends(security.require_admin)])


def get_invoice(invoice_id: int) -> "db.sqlite3.Row":
    return db.get_or_404("SELECT * FROM invoices WHERE id=?", (invoice_id,))


def _approved_offer_line_items(project_id: int) -> list[dict]:
    """Invoice line items from APPROVED Plutus offers on this project's galleries, each carrying
    the offer's stable sku (ADR 0022 piece 2). This is the opt-in PRE-FILL source the operator
    can pull into a draft — it never creates, charges, or sends an invoice (audit §11.4)."""
    rows = db.all_(
        """SELECT plutus_last_bundles FROM galleries
           WHERE project_id=? AND plutus_offer_decision='approved'
                 AND plutus_last_bundles IS NOT NULL
           ORDER BY plutus_offer_decided_at DESC""",
        (project_id,),
    )
    out: list[dict] = []
    for r in rows:
        try:
            bundles = json.loads(r["plutus_last_bundles"])
        except (ValueError, TypeError):
            continue
        out.extend(plutus_recommend.bundles_to_line_items(bundles))
    return out


def _addable_offer_items(project_id: int, existing: list[dict]) -> list[dict]:
    """Approved-offer line items whose sku isn't already on the invoice — so a repeat add is a
    no-op (idempotent) and the operator only ever sees fresh items to pull in."""
    seen = {it.get("sku") for it in existing if it.get("sku")}
    return [it for it in _approved_offer_line_items(project_id) if it["sku"] not in seen]


@router.post("/projects/{project_id}/invoices")
async def create_invoice(project_id: int):
    p = get_project(project_id)
    accepted = db.one(
        """SELECT line_items, total_cents FROM proposals
                         WHERE project_id=? AND status='accepted'
                         ORDER BY accepted_at DESC LIMIT 1""",
        (project_id,),
    )
    items = accepted["line_items"] if accepted else "[]"
    total = accepted["total_cents"] if accepted else 0
    did = db.run(
        """INSERT INTO invoices (project_id, slug, title, line_items, total_cents)
                    VALUES (?,?,?,?,?)""",
        (project_id, security.new_slug(), f"Invoice — {p['title']}", items, total),
    )
    log.info("invoice %s created for project %s (seeded=%s)", did, project_id, bool(accepted))
    return RedirectResponse(f"/admin/studio/invoices/{did}", status_code=303)


@router.post("/invoices/from-offer/{gallery_id}")
async def create_invoice_from_offer(gallery_id: int):
    """One-click from the offers queue: create a draft invoice for the gallery's project,
    pre-filled with the approved offer's line items (with SKUs) — so an approved upsell becomes an
    editable, SKU-tagged draft in a click instead of a re-type. Mirrors create_invoice + the
    add-offer-items pre-fill; nothing is sent or charged (audit §11.4)."""
    g = db.one(
        "SELECT id, project_id, plutus_offer_decision FROM galleries WHERE id=?", (gallery_id,)
    )
    if not g:
        raise HTTPException(status_code=404, detail="gallery not found")
    if g["project_id"] is None:
        raise HTTPException(status_code=400, detail="gallery has no project to invoice")
    if g["plutus_offer_decision"] != "approved":
        raise HTTPException(status_code=400, detail="offer is not approved")
    p = get_project(g["project_id"])
    items = _approved_offer_line_items(g["project_id"])
    total = sum(it["qty"] * it["unit_cents"] for it in items)
    did = db.run(
        "INSERT INTO invoices (project_id, slug, title, line_items, total_cents) VALUES (?,?,?,?,?)",
        (g["project_id"], security.new_slug(), f"Invoice — {p['title']}", json.dumps(items), total),
    )
    log.info(
        "invoice %s built from approved offer on gallery %s (%s offer lines)",
        did,
        gallery_id,
        len(items),
    )
    return RedirectResponse(f"/admin/studio/invoices/{did}", status_code=303)


@router.post("/invoices/from-album/{draft_id}")
async def create_invoice_from_album(draft_id: int):
    """One-click from an ORDERED album draft: create a draft invoice for the gallery's project
    with a single 'Album — <size>' line for the operator to price. Bridges the record-only album
    order (ADR 0019) to billing so it stops being a fulfillment dead-end.

    Deliberately a CLEAN line with NO sku — album orders aren't Plutus offers, so this is not
    counted as offer-attributed upsell (the scorecard's attribution stays offer→sale). The line
    is priced $0 for the operator to fill; nothing is sent or charged (audit §11.4)."""
    d = db.one(
        """SELECT d.id, d.ordered_at, d.order_size, g.project_id, g.title AS gtitle
           FROM album_drafts d JOIN galleries g ON g.id = d.gallery_id WHERE d.id=?""",
        (draft_id,),
    )
    if not d:
        raise HTTPException(status_code=404, detail="album draft not found")
    if d["project_id"] is None:
        raise HTTPException(status_code=400, detail="gallery has no project to invoice")
    if not d["ordered_at"]:
        raise HTTPException(status_code=400, detail="album is not marked ordered")
    p = get_project(d["project_id"])
    label = "Album" + (f" — {d['order_size']}" if d["order_size"] else "")
    line = {"label": label, "qty": 1, "unit_cents": 0}  # operator sets the price; clean, no sku
    did = db.run(
        "INSERT INTO invoices (project_id, slug, title, line_items, total_cents) VALUES (?,?,?,?,?)",
        (d["project_id"], security.new_slug(), f"Invoice — {p['title']}", json.dumps([line]), 0),
    )
    log.info("invoice %s built from ordered album draft %s", did, draft_id)
    return RedirectResponse(f"/admin/studio/invoices/{did}", status_code=303)


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
async def invoice_detail(request: Request, invoice_id: int):
    d = get_invoice(invoice_id)
    p = get_project(d["project_id"])
    items = json.loads(d["line_items"])
    rows = items + [{} for _ in range(max(0, MAX_ITEM_ROWS - len(items)))]
    payments = db.all_("SELECT * FROM payments WHERE invoice_id=? ORDER BY id", (invoice_id,))
    # Count approved-offer line items the operator could still pull into this draft (ADR 0022).
    offer_items_addable = (
        len(_addable_offer_items(d["project_id"], items)) if d["status"] == "draft" else 0
    )
    return templates.TemplateResponse(
        request,
        "admin/invoice.html",
        {
            "d": d,
            "p": p,
            "rows": rows,
            "payments": payments,
            "base_url": config.BASE_URL,
            "offer_items_addable": offer_items_addable,
        },
    )


@router.post("/invoices/{invoice_id}")
async def update_invoice(request: Request, invoice_id: int):
    d = get_invoice(invoice_id)
    if d["status"] != "draft":
        raise HTTPException(status_code=400, detail="sent invoices are locked")
    form = await request.form()
    items_json, total = parse_items(form)
    try:
        deposit = common.parse_form_cents(form, "deposit")
    except ValueError:
        raise HTTPException(status_code=400, detail="bad deposit amount")
    if deposit < 0 or deposit > total:
        raise HTTPException(status_code=400, detail="deposit must be between 0 and the total")
    db.run(
        """UPDATE invoices SET title=?, line_items=?, total_cents=?, deposit_cents=?,
              due_date=?, terms=? WHERE id=?""",
        (
            (form.get("title") or "").strip() or d["title"],
            items_json,
            total,
            deposit,
            (form.get("due_date") or "").strip() or None,
            (form.get("terms") or "").strip() or None,
            invoice_id,
        ),
    )
    return RedirectResponse(f"/admin/studio/invoices/{invoice_id}", status_code=303)


@router.post("/invoices/{invoice_id}/add-offer-items")
async def add_offer_items(invoice_id: int):
    """Opt-in pre-fill: append the approved offer's line items (with SKUs) to this DRAFT, so an
    accepted upsell links to invoice lines for revenue attribution (ADR 0022 piece 2). The
    operator clicks to add; the lines are then fully editable and nothing is sent. Idempotent —
    items whose sku is already present are skipped — and refused once the invoice is locked."""
    d = get_invoice(invoice_id)
    if d["status"] != "draft":
        raise HTTPException(status_code=400, detail="sent invoices are locked")
    existing = json.loads(d["line_items"])
    additions = _addable_offer_items(d["project_id"], existing)
    if additions:
        merged = existing + additions
        total = sum(it["qty"] * it["unit_cents"] for it in merged)
        db.run(
            "UPDATE invoices SET line_items=?, total_cents=? WHERE id=?",
            (json.dumps(merged), total, invoice_id),
        )
        log.info("invoice %s: pre-filled %s approved-offer line items", invoice_id, len(additions))
    return RedirectResponse(f"/admin/studio/invoices/{invoice_id}", status_code=303)


@router.post("/invoices/{invoice_id}/duplicate")
async def duplicate_invoice(invoice_id: int):
    """Clone a locked invoice (sent/viewed/paid) into a fresh editable draft.
    Copies title/line items/total/deposit/due date/terms under a new slug; the new
    draft carries no payments, Stripe session, or paid status. The original — and the
    payments recorded against it — is untouched."""
    d = get_invoice(invoice_id)
    did = db.run(
        """INSERT INTO invoices (project_id, slug, title, line_items,
                    total_cents, deposit_cents, due_date, terms)
                    VALUES (?,?,?,?,?,?,?,?)""",
        (
            d["project_id"],
            security.new_slug(),
            d["title"],
            d["line_items"],
            d["total_cents"],
            d["deposit_cents"],
            d["due_date"],
            d["terms"],
        ),
    )
    log.info("invoice %s duplicated → %s (new draft)", invoice_id, did)
    return RedirectResponse(f"/admin/studio/invoices/{did}", status_code=303)


@router.post("/invoices/{invoice_id}/send")
async def mark_invoice_sent(invoice_id: int):
    d = get_invoice(invoice_id)
    if d["status"] != "draft":
        raise HTTPException(status_code=400, detail="already sent")
    if d["total_cents"] <= 0:
        raise HTTPException(status_code=400, detail="invoice total must be above zero")
    db.run("UPDATE invoices SET status='sent', sent_at=datetime('now') WHERE id=?", (invoice_id,))
    jobs.enqueue("notion_sync_invoice", {"invoice_id": invoice_id})
    log.info("invoice %s marked sent", invoice_id)
    return RedirectResponse(f"/admin/studio/invoices/{invoice_id}", status_code=303)
