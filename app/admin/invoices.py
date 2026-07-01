"""Invoices — line items + optional deposit split. Send locks; Stripe handles payment."""

import json
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, config, db, jobs, security, workflows
from ..render import templates
from . import common
from .proposals import MAX_ITEM_ROWS, parse_items
from .studio import get_project

log = logging.getLogger("mise.admin.invoices")
router = APIRouter(prefix="/admin/studio", dependencies=[Depends(security.require_admin)])

# Cap net terms at a year — guards a typo (net 3000) from stamping a nonsense due date, while
# covering every real B2B term (net-15/30/45/60/90).
MAX_NET_DAYS = 365


def due_date_from_net_days(start: str, net_days: int) -> str | None:
    """Due date for a net-terms invoice: ``start`` + ``net_days``, as ``YYYY-MM-DD``.

    Returns None when ``net_days`` <= 0 (no net terms — the operator's manually-entered
    due_date stands instead). ``start`` is an ISO date/datetime string; only its date part is
    used. Pure function so the net-30 arithmetic is unit-tested without a DB or a send."""
    if net_days <= 0:
        return None
    return (date.fromisoformat(start[:10]) + timedelta(days=net_days)).isoformat()


def get_invoice(invoice_id: int) -> "db.sqlite3.Row":
    return db.get_or_404("SELECT * FROM invoices WHERE id=?", (invoice_id,))


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


def _overage_prefill_row(request: Request, d: "db.sqlite3.Row", n_items: int) -> dict | None:
    """A SYNTHETIC, display-only invoice row pre-filled from the retainer overage button.

    The retainer 'Add overage to draft' action redirects here with overage_label / overage_qty /
    overage_unit_cents query params. On a DRAFT with room for another line, we render ONE extra
    editable row seeded with those values — it is NOT persisted here (this is a GET). It becomes a
    real line ONLY if the operator saves the form (update_invoice → parse_items), exactly like any
    line they typed. Returns None on a locked invoice, a full line list, or absent/invalid params
    (§11.4: the system proposes an editable draft row; a human commits it)."""
    if d["status"] != "draft" or n_items >= MAX_ITEM_ROWS:
        return None
    label = (request.query_params.get("overage_label") or "").strip()
    if not label:
        return None
    try:
        qty = max(1, int(request.query_params.get("overage_qty") or "1"))
        unit_cents = max(0, int(request.query_params.get("overage_unit_cents") or "0"))
    except ValueError:
        return None
    return {"label": label, "qty": qty, "unit_cents": unit_cents}


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
async def invoice_detail(request: Request, invoice_id: int):
    d = get_invoice(invoice_id)
    p = get_project(d["project_id"])
    items = json.loads(d["line_items"])
    # Optional pre-filled overage row (display-only; persisted only when the operator saves).
    overage_row = _overage_prefill_row(request, d, len(items))
    display_items = items + ([overage_row] if overage_row else [])
    rows = display_items + [{} for _ in range(max(0, MAX_ITEM_ROWS - len(display_items)))]
    payments = db.all_("SELECT * FROM payments WHERE invoice_id=? ORDER BY id", (invoice_id,))
    paid_cents = sum(pay["amount_cents"] or 0 for pay in payments)
    paid_basis_cents = paid_cents
    if not paid_basis_cents and d["status"] == "deposit_paid":
        paid_basis_cents = d["deposit_cents"] or 0
    try:
        past_due = bool(d["due_date"]) and date.fromisoformat(d["due_date"][:10]) < date.today()
    except (TypeError, ValueError):
        past_due = False
    balance_cents = max((d["total_cents"] or 0) - paid_basis_cents, 0)
    ar_chase_url = None
    if d["status"] in {"sent", "viewed", "deposit_paid"} and past_due and balance_cents > 0:
        ar_chase_url = f"/admin/studio/companies/{p['client_id']}/ar-chase?invoice_id={invoice_id}"
    # Usage licences granted with this invoice (the rights↔money link, migration 078).
    licenses = db.all_(
        "SELECT id, title, status, usage_tier FROM licenses "
        "WHERE invoice_id=? AND deleted_at IS NULL ORDER BY id",
        (invoice_id,),
    )
    return templates.TemplateResponse(
        request,
        "admin/invoice.html",
        {
            "d": d,
            "p": p,
            "rows": rows,
            "payments": payments,
            "licenses": licenses,
            "overage_prefilled": overage_row is not None,
            "ar_chase_url": ar_chase_url,
            "base_url": config.BASE_URL,
        },
    )


@router.post("/invoices/{invoice_id}/grant-license")
async def grant_license(invoice_id: int, title: str = Form(...)):
    """Spawn a usage licence linked to this invoice (rights↔money coupling, ADR 0037). Creates a
    STUB — holder = the invoice's client, with project + invoice_id set — then hands off to the
    existing licence editor for term/territory/channels. Touches no money: the invoice total and
    line items are untouched; the licence is its own record the operator fills in and activates."""
    d = get_invoice(invoice_id)
    p = get_project(d["project_id"])
    if not p["client_id"]:
        raise HTTPException(status_code=400, detail="link this project to a client first")
    if not title.strip():
        raise HTTPException(status_code=400, detail="title required")
    with db.tx() as con:
        cur = con.execute(
            "INSERT INTO licenses (holder_client_id, title, project_id, invoice_id) VALUES (?,?,?,?)",
            (p["client_id"], title.strip(), d["project_id"], invoice_id),
        )
        lid = cur.lastrowid
        audit.log(
            con,
            "license",
            lid,
            "create",
            diff={
                "holder_client_id": p["client_id"],
                "title": title.strip(),
                "invoice_id": invoice_id,
            },
        )
    log.info("license %s granted with invoice %s (client %s)", lid, invoice_id, p["client_id"])
    return RedirectResponse(f"/admin/studio/licenses/{lid}", status_code=303)


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
    try:
        net_days = int(form.get("net_days") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="net terms must be a whole number of days")
    if net_days < 0 or net_days > MAX_NET_DAYS:
        raise HTTPException(status_code=400, detail=f"net terms must be 0–{MAX_NET_DAYS} days")
    db.run(
        """UPDATE invoices SET title=?, line_items=?, total_cents=?, deposit_cents=?,
              due_date=?, terms=?, po_number=?, net_days=? WHERE id=?""",
        (
            (form.get("title") or "").strip() or d["title"],
            items_json,
            total,
            deposit,
            (form.get("due_date") or "").strip() or None,
            (form.get("terms") or "").strip() or None,
            (form.get("po_number") or "").strip() or None,
            net_days,
            invoice_id,
        ),
    )
    return RedirectResponse(f"/admin/studio/invoices/{invoice_id}", status_code=303)


@router.post("/invoices/{invoice_id}/duplicate")
async def duplicate_invoice(invoice_id: int):
    """Clone a locked invoice (sent/viewed/paid) into a fresh editable draft.
    Copies title/line items/total/deposit/due date/terms/net-terms under a new slug; the new
    draft carries no payments, Stripe session, or paid status. The PO number is NOT copied — each
    order carries its own purchase-order ref. The original — and the payments recorded against it —
    is untouched."""
    d = get_invoice(invoice_id)
    did = db.run(
        """INSERT INTO invoices (project_id, slug, title, line_items,
                    total_cents, deposit_cents, due_date, terms, net_days)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            d["project_id"],
            security.new_slug(),
            d["title"],
            d["line_items"],
            d["total_cents"],
            d["deposit_cents"],
            d["due_date"],
            d["terms"],
            d["net_days"],
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
    # Net terms drive the due date: on send, "due = today + net_days" (net-30 etc.). With no net
    # terms (net_days = 0) the operator's manually-set due_date stands, unchanged.
    net_due = due_date_from_net_days(date.today().isoformat(), d["net_days"] or 0)
    if net_due:
        db.run(
            "UPDATE invoices SET status='sent', sent_at=datetime('now'), due_date=? WHERE id=?",
            (net_due, invoice_id),
        )
    else:
        db.run(
            "UPDATE invoices SET status='sent', sent_at=datetime('now') WHERE id=?", (invoice_id,)
        )
    workflows.record_project_event(
        d["project_id"],
        "invoice",
        f"Invoice sent: {d['title']}",
        ref_kind="invoice",
        ref_id=invoice_id,
        dedupe_key=f"invoice_sent:{invoice_id}",
    )
    workflows.fire_workflow("invoice_sent", d["project_id"], ref_kind="invoice", ref_id=invoice_id)
    jobs.enqueue("notion_sync_invoice", {"invoice_id": invoice_id})
    log.info("invoice %s marked sent (net_days=%s, due=%s)", invoice_id, d["net_days"], net_due)
    return RedirectResponse(f"/admin/studio/invoices/{invoice_id}", status_code=303)
