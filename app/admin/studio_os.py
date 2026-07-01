"""MicroSaaS studio OS surface: packages, presets, rules, and reminders."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, preset_packs, security
from ..render import templates

log = logging.getLogger("mise.admin.studio_os")
router = APIRouter(prefix="/admin/studio", dependencies=[Depends(security.require_admin)])

VALID_TRIGGERS = {
    "proposal_sent": "Proposal sent",
    "proposal_accepted": "Proposal accepted",
    "contract_sent": "Contract sent",
    "contract_signed": "Contract signed",
    "invoice_sent": "Invoice sent",
    "deposit_paid": "Deposit paid",
    "invoice_paid": "Invoice paid",
    "gallery_published": "Gallery published",
    "status:inquiry_received": "Project moved to inquiry",
    "status:consultation_call": "Project moved to consultation",
    "status:proposal_sent": "Project moved to proposal",
    "status:contract_signed": "Project moved to contract signed",
    "status:retainer_paid": "Project moved to retainer paid",
    "status:session_planning": "Project moved to session planning",
}


def _parse_price_cents(raw: str) -> int:
    try:
        cents = int((Decimal(raw or "0") * Decimal("100")).to_integral_value())
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail="bad price")
    if cents < 0:
        raise HTTPException(status_code=400, detail="price cannot be negative")
    return cents


@router.get("/automation", response_class=HTMLResponse)
async def automation_home(request: Request):
    rules = db.all_("SELECT * FROM workflow_rules ORDER BY active DESC, trigger_key, delay_days")
    packages = db.all_("SELECT * FROM packages ORDER BY active DESC, name")
    reminders = db.all_(
        """SELECT t.*, p.title AS project_title
           FROM tasks t
           LEFT JOIN projects p ON p.id=t.project_id
           WHERE t.done=0
           ORDER BY (t.due_date IS NULL), t.due_date, t.id DESC
           LIMIT 20"""
    )
    package_leads = db.all_(
        """SELECT pl.*, pk.name AS package_name
           FROM package_leads pl
           JOIN packages pk ON pk.id=pl.package_id
           ORDER BY pl.created_at DESC LIMIT 20"""
    )
    return templates.TemplateResponse(
        request,
        "admin/studio_automation.html",
        {
            "packs": preset_packs.PRESET_PACKS,
            "rules": rules,
            "packages": packages,
            "package_leads": package_leads,
            "reminders": reminders,
            "triggers": VALID_TRIGGERS,
        },
    )


@router.post("/automation/presets/{key}/install")
async def install_preset_pack(key: str):
    if key not in preset_packs.PRESET_PACKS:
        raise HTTPException(status_code=404)
    counts = preset_packs.install_pack(key)
    log.info("preset pack %s installed: %s", key, counts)
    return RedirectResponse("/admin/studio/automation", status_code=303)


@router.post("/automation/workflow-rules")
async def create_workflow_rule(
    name: str = Form(...),
    trigger_key: str = Form(...),
    task_title: str = Form(...),
    delay_days: int = Form(0),
):
    if trigger_key not in VALID_TRIGGERS:
        raise HTTPException(status_code=400, detail="bad trigger")
    if delay_days < 0 or delay_days > 365:
        raise HTTPException(status_code=400, detail="delay must be 0-365 days")
    if not name.strip() or not task_title.strip():
        raise HTTPException(status_code=400, detail="name and task title required")
    db.run(
        """INSERT OR IGNORE INTO workflow_rules
           (name, trigger_key, action_key, task_title, delay_days)
           VALUES (?,?, 'task', ?, ?)""",
        (name.strip(), trigger_key, task_title.strip(), delay_days),
    )
    return RedirectResponse("/admin/studio/automation", status_code=303)


@router.post("/automation/workflow-rules/{rule_id}/toggle")
async def toggle_workflow_rule(rule_id: int):
    rule = db.get_or_404("SELECT * FROM workflow_rules WHERE id=?", (rule_id,))
    db.run("UPDATE workflow_rules SET active=? WHERE id=?", (0 if rule["active"] else 1, rule_id))
    return RedirectResponse("/admin/studio/automation", status_code=303)


@router.post("/automation/packages")
async def create_package(
    slug: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    price_dollars: str = Form("0"),
):
    slug = slug.strip().lower()
    if not slug.replace("-", "").isalnum() or slug.startswith("-") or slug.endswith("-"):
        raise HTTPException(
            status_code=400, detail="slug must be lowercase words separated by hyphens"
        )
    price_cents = _parse_price_cents(price_dollars)
    db.run(
        """INSERT INTO packages (slug, name, description, price_cents)
           VALUES (?,?,?,?)
           ON CONFLICT(slug) DO UPDATE SET
             name=excluded.name,
             description=excluded.description,
             price_cents=excluded.price_cents,
             active=1""",
        (slug, name.strip(), description.strip() or None, price_cents),
    )
    return RedirectResponse("/admin/studio/automation", status_code=303)


@router.post("/automation/packages/{package_id}/toggle")
async def toggle_package(package_id: int):
    package = db.get_or_404("SELECT * FROM packages WHERE id=?", (package_id,))
    db.run("UPDATE packages SET active=? WHERE id=?", (0 if package["active"] else 1, package_id))
    return RedirectResponse("/admin/studio/automation", status_code=303)
