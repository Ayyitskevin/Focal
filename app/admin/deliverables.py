"""Project deliverable specs (Domain F) — the contracted "what we owe" for a one-off shoot.

Retainers carry a recurring monthly quota (recurring.py); this is the one-off equivalent for a
project: the deliverables the operator committed to ("25 hero images, 5 reels, 1 social-crop ZIP"),
each with a contracted count, unit, format note, and a MANUAL delivered count so progress is
trackable. It complements the shot list (what to shoot) and the licence/invoice coupling
(rights + money). Local + studio-only; nothing here delivers, charges, or sends.

Mirrors shotlist.py exactly: per-project rows on a net-new table (migration 079), every mutation
through db.tx() so the row change + its audit_log entry (entity_type='project_deliverable') commit
together, soft-delete only. Routes hang off /admin/studio and redirect back to the owning project —
there is no standalone index; deliverables live inside project_detail.
"""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import audit, db, security
from ..usage_vocab import DELIVERABLE_TEMPLATES, DELIVERABLE_UNITS
from .studio import get_project

log = logging.getLogger("mise.admin.deliverables")
router = APIRouter(prefix="/admin/studio", dependencies=[Depends(security.require_admin)])

# Columns the diff/audit machinery tracks. Order = form/display order.
_FIELDS = ["label", "spec_qty", "unit", "spec_format", "delivered_qty", "sort_order", "note"]


def _int(form, key: str) -> int:
    """A non-negative int from the form (blank/non-numeric → 0); deliverable counts never go
    negative."""
    raw = (form.get(key) or "").strip()
    return max(0, int(raw)) if raw.lstrip("-").isdigit() else 0


def _parse_form(form) -> dict:
    """Normalize + validate a deliverable form: label required; unit must be in DELIVERABLE_UNITS
    (defaults 'images'); spec_qty / delivered_qty / sort_order are non-negative ints; format + note
    are free text."""
    label = (form.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="label required")
    unit = (form.get("unit") or "").strip() or "images"
    if unit not in DELIVERABLE_UNITS:
        raise HTTPException(status_code=400, detail="bad unit")
    return {
        "label": label,
        "spec_qty": _int(form, "spec_qty"),
        "unit": unit,
        "spec_format": (form.get("spec_format") or "").strip() or None,
        "delivered_qty": _int(form, "delivered_qty"),
        "sort_order": _int(form, "sort_order"),
        "note": (form.get("note") or "").strip() or None,
    }


def _get(deliverable_id: int) -> "db.sqlite3.Row":
    d = db.one(
        "SELECT * FROM project_deliverables WHERE id=? AND deleted_at IS NULL", (deliverable_id,)
    )
    if not d:
        raise HTTPException(status_code=404)
    return d


def _template(template_key: str) -> dict:
    tpl = DELIVERABLE_TEMPLATES.get((template_key or "").strip())
    if not tpl:
        raise HTTPException(status_code=400, detail="bad deliverable template")
    return tpl


@router.post("/projects/{project_id}/deliverables")
async def create_deliverable(request: Request, project_id: int):
    get_project(project_id)  # 404 if the project doesn't exist
    new = _parse_form(await request.form())
    with db.tx() as con:
        cur = con.execute(
            """INSERT INTO project_deliverables
                 (project_id, label, spec_qty, unit, spec_format, delivered_qty, sort_order, note)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                project_id,
                new["label"],
                new["spec_qty"],
                new["unit"],
                new["spec_format"],
                new["delivered_qty"],
                new["sort_order"],
                new["note"],
            ),
        )
        did = cur.lastrowid
        audit.log(
            con,
            "project_deliverable",
            did,
            "create",
            diff={"project_id": project_id, "label": new["label"], "spec_qty": new["spec_qty"]},
        )
    log.info("deliverable %s created on project %s (%s)", did, project_id, new["label"])
    return RedirectResponse(f"/admin/studio/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/deliverables/template")
async def clone_deliverable_template(project_id: int, template_key: str = Form(...)):
    get_project(project_id)  # 404 if the project doesn't exist
    tpl = _template(template_key)
    with db.tx() as con:
        row = con.execute(
            "SELECT COALESCE(MAX(sort_order), 0) AS n FROM project_deliverables "
            "WHERE project_id=? AND deleted_at IS NULL",
            (project_id,),
        ).fetchone()
        base_sort = row["n"] if row else 0
        created = 0
        for deliverable in tpl["deliverables"]:
            sort_order = base_sort + int(deliverable["sort_order"])
            cur = con.execute(
                """INSERT INTO project_deliverables
                     (project_id, label, spec_qty, unit, spec_format, delivered_qty,
                      sort_order, note)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    project_id,
                    deliverable["label"],
                    deliverable["spec_qty"],
                    deliverable["unit"],
                    deliverable.get("spec_format"),
                    0,
                    sort_order,
                    deliverable.get("note"),
                ),
            )
            did = cur.lastrowid
            audit.log(
                con,
                "project_deliverable",
                did,
                "create",
                diff={
                    "project_id": project_id,
                    "template": template_key,
                    "label": deliverable["label"],
                    "spec_qty": deliverable["spec_qty"],
                    "unit": deliverable["unit"],
                    "sort_order": sort_order,
                },
            )
            created += 1
    log.info(
        "deliverable template %s cloned on project %s (%d lines)",
        template_key,
        project_id,
        created,
    )
    return RedirectResponse(f"/admin/studio/projects/{project_id}", status_code=303)


@router.post("/deliverables/{deliverable_id}")
async def update_deliverable(request: Request, deliverable_id: int):
    d = _get(deliverable_id)
    new = _parse_form(await request.form())
    diff = {f: [d[f], new[f]] for f in _FIELDS if (d[f] or None) != (new[f] or None)}
    if not diff:
        return RedirectResponse(f"/admin/studio/projects/{d['project_id']}", status_code=303)
    with db.tx() as con:
        con.execute(
            """UPDATE project_deliverables SET label=?, spec_qty=?, unit=?, spec_format=?,
                 delivered_qty=?, sort_order=?, note=?, updated_at=datetime('now') WHERE id=?""",
            (
                new["label"],
                new["spec_qty"],
                new["unit"],
                new["spec_format"],
                new["delivered_qty"],
                new["sort_order"],
                new["note"],
                deliverable_id,
            ),
        )
        audit.log(con, "project_deliverable", deliverable_id, "update", diff=diff)
    log.info("deliverable %s updated (%d fields)", deliverable_id, len(diff))
    return RedirectResponse(f"/admin/studio/projects/{d['project_id']}", status_code=303)


@router.post("/deliverables/{deliverable_id}/delete")
async def delete_deliverable(deliverable_id: int):
    d = _get(deliverable_id)
    with db.tx() as con:
        con.execute(
            "UPDATE project_deliverables SET deleted_at=datetime('now') WHERE id=?",
            (deliverable_id,),
        )
        audit.log(
            con,
            "project_deliverable",
            deliverable_id,
            "soft_delete",
            diff={"label": d["label"]},
        )
    log.info("deliverable %s soft-deleted", deliverable_id)
    return RedirectResponse(f"/admin/studio/projects/{d['project_id']}", status_code=303)
