"""Owner commercial-spine reads for the native API — Milestone 4 read slice.

Native mirror of the operator's F&B commercial surfaces (ADRs 0039–0046): the
company next-action ranking, the studio commercial action queue, AR chase
assist + cadence, and project closeout readiness. Every route is owner-only
(``studio_owner`` + ``studio:read``) and purely read. The derivations are the
same ones the admin HTML pages use, imported from ``app/commercial.py`` (queue
S7) so the two surfaces never drift.

Two boundaries this module enforces:

* A "company" is a **root client** (``parent_id IS NULL``) standing for its
  descendant group. ``{company_id}`` must be a root; anything else is 404.
* The admin derivations carry ``href`` links into admin HTML pages. Those are
  never serialized. Each actionable row is translated to a typed ``target`` the
  app routes itself; the only URL ever emitted is a public workspace link.

No value is sent, charged, published, or mutated here. The AR-chase *send*
(the one mutation on the admin side) is not part of this read slice.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from . import commercial, config, db
from .mobile_owner_api import (
    _DEFAULT_PAGE_SIZE as _DEFAULT_PAGE_SIZE,
)
from .mobile_owner_api import (
    _MAX_PAGE_SIZE as _MAX_PAGE_SIZE,
)
from .mobile_owner_api import (
    Money,
    OwnerAPIModel,
    _conditional,
    _decode_cursor,
    _encode_cursor,
    _studio_today,
    _utc_timestamp,
    require_studio_owner,
)

router = APIRouter(dependencies=[Depends(require_studio_owner)], tags=["commercial"])

_INT64_MAX = 2**63 - 1


def _money(cents) -> Money:
    return Money(minor_units=int(cents or 0))


# ── structured targets (never leak admin hrefs) ──────────────────────────────

TargetKind = Literal["company", "ar_chase", "project", "invoice", "gallery", "workspace", "other"]

_RE_AR_CHASE = re.compile(r"/companies/(\d+)/ar-chase\b")
_RE_COMPANY = re.compile(r"/companies/(\d+)(?:$|[#?])")
_RE_CLIENT = re.compile(r"/clients/(\d+)(?:$|[#?])")
_RE_INVOICE = re.compile(r"/invoices/(\d+)(?:$|[#?])")
_RE_PROJECT = re.compile(r"/projects/(\d+)(?:$|[#?])")
_RE_GALLERY = re.compile(r"/galleries/(\d+)(?:$|[#?])")
_RE_WORKSPACE = re.compile(r"/w/[^/?#]+$")


class ActionTarget(OwnerAPIModel):
    """Where a row points, resolved by the app's own router (no admin URLs)."""

    kind: TargetKind
    company_id: int | None = None
    project_id: int | None = None
    invoice_id: int | None = None
    gallery_id: int | None = None
    section: str | None = None
    url: str | None = None


def _target_from_href(href: str | None, *, company_id: int | None) -> ActionTarget:
    """Translate one admin href to a typed target. Unknown/screen-less admin paths
    (retainers, licences) fall back to ``other`` with the company for context — an
    admin URL is never returned; only a public workspace link is passed through."""
    if not href:
        return ActionTarget(kind="other", company_id=company_id)
    if _RE_WORKSPACE.search(href):
        return ActionTarget(kind="workspace", company_id=company_id, url=href)
    if m := _RE_AR_CHASE.search(href):
        qs = parse_qs(urlparse(href).query)
        raw_inv = qs.get("invoice_id", [None])[0]
        invoice_id = int(raw_inv) if raw_inv and raw_inv.isdigit() else None
        return ActionTarget(kind="ar_chase", company_id=int(m.group(1)), invoice_id=invoice_id)
    if m := _RE_INVOICE.search(href):
        return ActionTarget(kind="invoice", company_id=company_id, invoice_id=int(m.group(1)))
    if m := _RE_GALLERY.search(href):
        return ActionTarget(kind="gallery", company_id=company_id, gallery_id=int(m.group(1)))
    if m := _RE_PROJECT.search(href):
        fragment = urlparse(href).fragment or None
        return ActionTarget(
            kind="project", company_id=company_id, project_id=int(m.group(1)), section=fragment
        )
    if m := (_RE_COMPANY.search(href) or _RE_CLIENT.search(href)):
        return ActionTarget(kind="company", company_id=int(m.group(1)))
    return ActionTarget(kind="other", company_id=company_id)


# ── DTOs ─────────────────────────────────────────────────────────────────────

Severity = Literal["ok", "attention", "missing"]
_TONE_TO_SEVERITY: dict[str, Severity] = {"ok": "ok", "warn": "attention", "gap": "missing"}


class NextAction(OwnerAPIModel):
    priority: int
    severity: Severity
    title: str
    detail: str
    meta: str | None = None
    target: ActionTarget


class CommercialAction(NextAction):
    company_id: int
    company_name: str


class CompanySummary(OwnerAPIModel):
    id: int
    name: str
    email: str | None = None
    billing_email: str | None = None


class CompanyNextActions(OwnerAPIModel):
    company_id: int
    company_name: str
    actions: list[NextAction]


class ArChaseCadence(OwnerAPIModel):
    status: Literal["never", "recent", "due"]
    followup_due: bool
    days_since: int | None = None
    last_sent_at: object | None = None
    last_sent_to: str | None = None
    next_due_on: str | None = None
    summary: str
    detail: str


class OverdueInvoice(OwnerAPIModel):
    invoice_id: int
    title: str | None = None
    status: str
    due_date: str | None = None
    total: Money
    paid: Money
    owed: Money
    project_id: int | None = None
    project_title: str | None = None
    client_id: int | None = None
    client_name: str | None = None
    public_url: str


class ArChaseDraft(OwnerAPIModel):
    to: str
    subject: str
    body: str


class ArChaseAssist(OwnerAPIModel):
    company_id: int
    company_name: str
    owed: Money
    overdue_invoices: list[OverdueInvoice]
    cadence: ArChaseCadence
    draft: ArChaseDraft


class CloseoutItem(OwnerAPIModel):
    key: str
    title: str
    severity: Severity
    badge: str
    detail: str
    target: ActionTarget | None = None


class ProjectCloseout(OwnerAPIModel):
    project_id: int
    ready: bool
    ok_count: int
    attention_count: int
    missing_count: int
    total: int
    items: list[CloseoutItem]


class CompanyPage(OwnerAPIModel):
    items: list[CompanySummary]
    next_cursor: str | None = None
    has_more: bool


class CommercialActionPage(OwnerAPIModel):
    items: list[CommercialAction]
    next_cursor: str | None = None
    has_more: bool


# ── helpers ──────────────────────────────────────────────────────────────────


def _root_company_or_404(company_id: int):
    row = db.one(
        "SELECT id, name, company, email, billing_email FROM clients WHERE id=? AND parent_id IS NULL",
        (company_id,),
    )
    if not row:
        from .mobile_auth import MobileAuthError

        raise MobileAuthError(404, "company.not_found", "No such company.")
    return row


def _next_action(action: dict, *, company_id: int) -> NextAction:
    return NextAction(
        priority=int(action["rank"]),
        severity=_TONE_TO_SEVERITY.get(action["tone"], "attention"),
        title=action["title"],
        detail=action["label"],
        meta=action.get("meta"),
        target=_target_from_href(action.get("href"), company_id=company_id),
    )


# ── endpoints ────────────────────────────────────────────────────────────────


@router.get("/companies", response_model=CompanyPage)
def companies(
    request: Request,
    response: Response,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_SIZE)] = _DEFAULT_PAGE_SIZE,
) -> CompanyPage | Response:
    last_id = _decode_cursor("companies", cursor)
    where = "AND id < ?" if last_id is not None else ""
    params = (last_id, limit + 1) if last_id is not None else (limit + 1,)
    rows = db.all_(
        f"""SELECT id, name, company, email, billing_email
              FROM clients WHERE parent_id IS NULL {where}
              ORDER BY id DESC LIMIT ?""",
        params,
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    payload = CompanyPage(
        items=[
            CompanySummary(
                id=int(r["id"]),
                name=r["company"] or r["name"],
                email=r["email"],
                billing_email=r["billing_email"],
            )
            for r in visible
        ],
        next_cursor=(
            _encode_cursor("companies", int(visible[-1]["id"])) if has_more and visible else None
        ),
        has_more=has_more,
    )
    return _conditional(request, response, payload)


@router.get("/commercial/actions", response_model=CommercialActionPage)
def commercial_actions(request: Request, response: Response) -> CommercialActionPage | Response:
    rows = commercial._ctx_commercial_actions(_studio_today())
    payload = CommercialActionPage(
        items=[
            CommercialAction(
                company_id=int(row["company_id"]),
                company_name=row["company_name"],
                priority=int(row["rank"]),
                severity=_TONE_TO_SEVERITY.get(row["tone"], "attention"),
                title=row["title"],
                detail=row["label"],
                meta=row.get("meta"),
                target=_target_from_href(row.get("href"), company_id=int(row["company_id"])),
            )
            for row in rows
        ],
        next_cursor=None,
        has_more=False,
    )
    return _conditional(request, response, payload)


@router.get("/companies/{company_id}/next-actions", response_model=CompanyNextActions)
def company_next_actions(
    request: Request,
    response: Response,
    company_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
) -> CompanyNextActions | Response:
    company = _root_company_or_404(company_id)
    ranked = commercial._ranked_company_actions(company_id, _studio_today())
    payload = CompanyNextActions(
        company_id=company_id,
        company_name=company["company"] or company["name"],
        actions=[_next_action(a, company_id=company_id) for a in ranked],
    )
    return _conditional(request, response, payload)


@router.get("/companies/{company_id}/ar-chase", response_model=ArChaseAssist)
def company_ar_chase(
    request: Request,
    response: Response,
    company_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
    invoice_id: Annotated[int | None, Query(ge=1, le=_INT64_MAX)] = None,
) -> ArChaseAssist | Response:
    company = _root_company_or_404(company_id)
    ctx = commercial._ar_chase_context(company_id, invoice_id)
    history = ctx["ar_history"]
    payload = ArChaseAssist(
        company_id=company_id,
        company_name=company["company"] or company["name"],
        owed=_money(ctx["owed_cents"]),
        overdue_invoices=[
            OverdueInvoice(
                invoice_id=int(r["id"]),
                title=r["title"],
                status=r["status"],
                due_date=r["due_date"],
                total=_money(r["total_cents"]),
                paid=_money(r["paid_cents"]),
                owed=_money(r["owed_cents"]),
                project_id=int(r["project_id"]) if r["project_id"] is not None else None,
                project_title=r["project_title"],
                client_id=int(r["client_id"]) if r["client_id"] is not None else None,
                client_name=r["client_name"],
                public_url=f"{config.BASE_URL}/i/{r['slug']}",
            )
            for r in ctx["rows"]
        ],
        cadence=ArChaseCadence(
            status=history["status"],
            followup_due=bool(history["followup_due"]),
            days_since=history["days_since"],
            last_sent_at=(
                _utc_timestamp(history["last_sent_at"]) if history["last_sent_at"] else None
            ),
            last_sent_to=history["last_to"] or None,
            next_due_on=history["next_due_on"],
            summary=history["action_meta"],
            detail=history["detail"],
        ),
        draft=ArChaseDraft(
            to=ctx["email_to"],
            subject=ctx["email_subject"],
            body=ctx["email_message"],
        ),
    )
    return _conditional(request, response, payload)


def _closeout_target(item: dict, project_id: int) -> ActionTarget:
    key = item["key"]
    href = item.get("href")
    if key == "workspace":
        return _target_from_href(href, company_id=None)  # /w/ url when live, else other
    if key == "gallery":
        return _target_from_href(href, company_id=None)  # /admin/galleries/{id} -> gallery
    if key == "invoice":
        return _target_from_href(href, company_id=None)  # /admin/studio/invoices/{id} -> invoice
    if key in {"shots", "deliverables", "ar"}:
        section = {"shots": "shots", "deliverables": "deliverables", "ar": "invoices"}[key]
        return ActionTarget(kind="project", project_id=project_id, section=section)
    # licence and anything else: no native screen -> point at the project.
    return ActionTarget(kind="project", project_id=project_id)


@router.get("/projects/{project_id}/closeout", response_model=ProjectCloseout)
def project_closeout(
    request: Request,
    response: Response,
    project_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
) -> ProjectCloseout | Response:
    p = db.one(
        "SELECT id, gallery_id, workspace_published, workspace_slug FROM projects WHERE id=?",
        (project_id,),
    )
    if not p:
        from .mobile_auth import MobileAuthError

        raise MobileAuthError(404, "project.not_found", "No such project.")
    data = commercial._project_closeout(project_id, p)
    payload = ProjectCloseout(
        project_id=project_id,
        ready=bool(data["ready"]),
        ok_count=int(data["ok"]),
        attention_count=int(data["warn"]),
        missing_count=int(data["gap"]),
        total=int(data["total"]),
        items=[
            CloseoutItem(
                key=item["key"],
                title=item["title"],
                severity=_TONE_TO_SEVERITY.get(item["tone"], "attention"),
                badge=item["badge"],
                detail=item["label"],
                target=_closeout_target(item, project_id),
            )
            for item in data["rows"]
        ],
    )
    return _conditional(request, response, payload)
