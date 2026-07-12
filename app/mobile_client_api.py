"""Shared-client resources for the native API -- Milestone 3.

This router serves the four existing guest principals (``gallery_guest``,
``portal_guest``, ``workspace_guest``, ``document_guest``) that the web app
already supports as independent capabilities (see ``app/public/gallery.py``,
``portal.py``, ``workspace.py``, ``docs.py``). It deliberately does not invent
a client-wide account: each route re-derives exactly what one principal kind
is allowed to see, matching the resource-shaped scopes minted in
``app/mobile_auth.py``. A workspace exchange still cannot become a
client-wide session, and a gallery exchange still cannot read documents.

Reads only, plus one idempotent favorite toggle. Proposal acceptance,
contract signing, and invoice checkout stay server-authoritative HTML flows
for now (``/p``, ``/c``, ``/i``); this slice exposes each document's existing
public URL so the app can hand off to the web for those actions, per
docs/IOS-ARCHITECTURE.md's Milestone 3 scope.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from pydantic import Field

from . import db, delivery_gate, mobile_auth, urls
from .mobile_gallery_calendar_api import (
    APIPage,
    Booking,
    GalleryDetail,
    GallerySummary,
    MobileReadModel,
    _booking_from_row,
    _collection_response,
    _decode_cursor,
    _encode_cursor,
    _etag_matches,
    _gallery_assets,
    _gallery_query,
    _gallery_sections,
    _gallery_summary,
    _optional_text,
    _private_headers,
    _sqlite_utc,
)
from .mobile_owner_api import Money

router = APIRouter(tags=["client companion"])

_MAX_CURSOR_LENGTH = 1024


def _money(cents: int) -> Money:
    return Money(minor_units=int(cents), currency_code="USD")


def _insufficient_scope() -> mobile_auth.MobileAuthError:
    return mobile_auth.MobileAuthError(
        403, "auth.insufficient_scope", "The token lacks this scope."
    )


def _studio_display_name(request: Request) -> str:
    # Lazy import: mobile_api mounts this router, so a module-level import
    # back into mobile_api would be circular.
    from . import mobile_api

    return mobile_api._tenant_metadata(request, canonical=False)["display_name"]


def _document_public_url(request: Request, variant: str, slug: str) -> str:
    prefix = {"proposal": "p", "contract": "c", "invoice": "i"}[variant]
    return f"{urls.request_origin(request)}/{prefix}/{slug}"


# ── Galleries: gallery_guest (own gallery), workspace_guest (project's one   ─
# gallery), portal_guest (every published gallery for that client). ─────────


def _client_gallery_ids(principal: mobile_auth.Principal) -> list[int]:
    if principal.kind == mobile_auth.GALLERY_GUEST:
        return [principal.resource_id] if principal.resource_id else []
    if principal.kind == mobile_auth.WORKSPACE_GUEST:
        row = db.one("SELECT gallery_id FROM projects WHERE id=?", (principal.resource_id,))
        if not row or not row["gallery_id"]:
            return []
        published = db.one(
            "SELECT 1 AS x FROM galleries WHERE id=? AND published=1", (row["gallery_id"],)
        )
        return [int(row["gallery_id"])] if published else []
    if principal.kind == mobile_auth.PORTAL_GUEST:
        client_row = db.one("SELECT client_id FROM portals WHERE id=?", (principal.resource_id,))
        if not client_row:
            return []
        rows = db.all_(
            "SELECT id FROM galleries WHERE client_id=? AND published=1 ORDER BY created_at DESC",
            (client_row["client_id"],),
        )
        return [int(r["id"]) for r in rows]
    return []


@router.get("/client/galleries", response_model=APIPage[GallerySummary])
def client_galleries(
    request: Request,
    response: Response,
    cursor: Annotated[str | None, Query(max_length=_MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> APIPage[GallerySummary]:
    principal = mobile_auth.authenticate_request(request)
    ids = _client_gallery_ids(principal)
    decoded = _decode_cursor(cursor, "client-galleries", (str, int))
    after = (str(decoded[0]), int(decoded[1])) if decoded is not None else None
    page_rows = _gallery_query(after=after, gallery_ids=ids, row_limit=limit + 1)
    has_more = len(page_rows) > limit
    page_rows = page_rows[:limit]
    items = [_gallery_summary(row) for row in page_rows]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = _encode_cursor("client-galleries", (str(last["created_at"]), int(last["id"])))
    page = APIPage[GallerySummary](items=items, next_cursor=next_cursor, has_more=has_more)
    return _collection_response(request, response, page, resource="client-galleries")


@router.get("/client/galleries/{gallery_id}", response_model=GalleryDetail)
def client_gallery_detail(
    request: Request,
    response: Response,
    gallery_id: Annotated[int, Path(ge=1)],
) -> GalleryDetail | Response:
    principal = mobile_auth.authenticate_request(request)
    if gallery_id not in _client_gallery_ids(principal):
        raise HTTPException(status_code=404, detail="Gallery not found.")
    rows = _gallery_query(gallery_id=gallery_id, row_limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="Gallery not found.")
    detail = GalleryDetail(
        summary=_gallery_summary(rows[0]),
        sections=_gallery_sections(gallery_id),
        assets=_gallery_assets(gallery_id, request),
        hero_asset_ids=[],
        vision=None,
    )
    digest = hashlib.sha256(detail.model_dump_json().encode()).hexdigest()
    etag = f'"client-gallery-{digest[:32]}"'
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=_private_headers(etag))
    for key, value in _private_headers(etag).items():
        response.headers[key] = value
    return detail


class FavoriteState(MobileReadModel):
    asset_id: int = Field(ge=1)
    selected: bool
    section_selected_count: int | None = Field(default=None, ge=0)
    section_proof_target: int | None = Field(default=None, ge=1)


def _require_favorite_principal(request: Request, gallery_id: int) -> mobile_auth.Principal:
    principal = mobile_auth.authenticate_request(
        request, required_scopes=(f"gallery:{gallery_id}:favorite",)
    )
    if principal.kind != mobile_auth.GALLERY_GUEST or principal.resource_id != gallery_id:
        raise _insufficient_scope()
    if principal.gallery_visitor_id is None:
        raise _insufficient_scope()
    return principal


def _favorite_state(asset_id: int, gallery_id: int, visitor_id: int) -> FavoriteState:
    gate = delivery_gate.clause("a")
    row = db.one(
        f"""SELECT a.section_id, s.proof_target,
                   EXISTS(SELECT 1 FROM favorites f
                          WHERE f.visitor_id=? AND f.asset_id=a.id) AS selected
              FROM assets a LEFT JOIN sections s ON s.id=a.section_id
              WHERE a.id=? AND a.gallery_id=? AND a.status='ready'{gate}""",
        (visitor_id, asset_id, gallery_id),
    )
    section_selected: int | None = None
    section_target: int | None = None
    if row and row["section_id"] is not None and row["proof_target"]:
        section_target = int(row["proof_target"])
        picks = db.one(
            f"""SELECT COUNT(DISTINCT f.asset_id) AS n FROM favorites f
                  JOIN assets a ON a.id=f.asset_id
                  WHERE f.visitor_id=? AND a.section_id=?{delivery_gate.clause("a")}""",
            (visitor_id, row["section_id"]),
        )["n"]
        section_selected = max(0, int(picks))
    return FavoriteState(
        asset_id=asset_id,
        selected=bool(row["selected"]) if row else False,
        section_selected_count=section_selected,
        section_proof_target=section_target,
    )


def _ready_asset_for_favorite(gallery_id: int, asset_id: int):
    gate = delivery_gate.clause("a")
    return db.one(
        f"""SELECT a.id, a.section_id, s.proof_target FROM assets a
              LEFT JOIN sections s ON s.id=a.section_id
              WHERE a.id=? AND a.gallery_id=? AND a.status='ready'{gate}""",
        (asset_id, gallery_id),
    )


@router.put("/galleries/{gallery_id}/assets/{asset_id}/favorite", response_model=FavoriteState)
def select_favorite(
    request: Request,
    gallery_id: Annotated[int, Path(ge=1)],
    asset_id: Annotated[int, Path(ge=1)],
) -> FavoriteState:
    principal = _require_favorite_principal(request, gallery_id)
    visitor_id = principal.gallery_visitor_id
    assert visitor_id is not None
    asset = _ready_asset_for_favorite(gallery_id, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found.")
    existing = db.one(
        "SELECT 1 AS x FROM favorites WHERE visitor_id=? AND asset_id=?", (visitor_id, asset_id)
    )
    if not existing:
        if asset["proof_target"]:
            gate = delivery_gate.clause("x")
            picks = db.one(
                f"""SELECT COUNT(*) AS n FROM favorites f JOIN assets x ON x.id=f.asset_id
                      WHERE f.visitor_id=? AND x.section_id=?{gate}""",
                (visitor_id, asset["section_id"]),
            )["n"]
            if picks >= asset["proof_target"]:
                raise mobile_auth.MobileAuthError(
                    409,
                    "gallery.proofing_limit",
                    "This section already has its selections.",
                )
        db.run("INSERT INTO favorites (visitor_id, asset_id) VALUES (?,?)", (visitor_id, asset_id))
    return _favorite_state(asset_id, gallery_id, visitor_id)


@router.delete("/galleries/{gallery_id}/assets/{asset_id}/favorite", response_model=FavoriteState)
def unselect_favorite(
    request: Request,
    gallery_id: Annotated[int, Path(ge=1)],
    asset_id: Annotated[int, Path(ge=1)],
) -> FavoriteState:
    principal = _require_favorite_principal(request, gallery_id)
    visitor_id = principal.gallery_visitor_id
    assert visitor_id is not None
    if not _ready_asset_for_favorite(gallery_id, asset_id):
        raise HTTPException(status_code=404, detail="Asset not found.")
    db.run("DELETE FROM favorites WHERE visitor_id=? AND asset_id=?", (visitor_id, asset_id))
    return _favorite_state(asset_id, gallery_id, visitor_id)


# ── Documents: studio_owner (any project) or workspace_guest (their own     ─
# project only). document_guest never sees siblings -- see _document_preview.


class LineItem(MobileReadModel):
    label: str = Field(min_length=1, max_length=500)
    quantity: int = Field(ge=0)
    unit_price: Money
    sku: str | None = Field(default=None, max_length=200)


def _line_items(raw: str | None) -> list[LineItem]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    items: list[LineItem] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()[:500] or "Item"
        try:
            quantity = max(0, int(entry.get("qty") or 0))
        except (TypeError, ValueError):
            quantity = 0
        try:
            unit_cents = int(entry.get("unit_cents") or 0)
        except (TypeError, ValueError):
            unit_cents = 0
        items.append(LineItem(label=label, quantity=quantity, unit_price=_money(unit_cents)))
    return items


class Proposal(MobileReadModel):
    id: int = Field(ge=1)
    project_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=2000)
    intro: str | None = Field(default=None, max_length=10_000)
    line_items: list[LineItem] = Field(default_factory=list)
    total: Money
    status: Literal["draft", "sent", "viewed", "accepted", "declined"]
    can_accept: bool
    can_decline: bool
    sent_at: dt.datetime | None = None
    viewed_at: dt.datetime | None = None
    accepted_at: dt.datetime | None = None
    created_at: dt.datetime
    public_url: str


class Contract(MobileReadModel):
    id: int = Field(ge=1)
    project_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=2000)
    body: str = Field(max_length=200_000)
    status: Literal["draft", "sent", "viewed", "signed"]
    can_sign: bool
    fully_executed: bool
    document_etag: str
    signer_name: str | None = Field(default=None, max_length=500)
    sent_at: dt.datetime | None = None
    viewed_at: dt.datetime | None = None
    signed_at: dt.datetime | None = None
    countersigned_at: dt.datetime | None = None
    created_at: dt.datetime
    public_url: str


class Payment(MobileReadModel):
    id: int = Field(ge=1)
    invoice_id: int = Field(ge=1)
    amount: Money
    kind: Literal["deposit", "balance", "full"]
    created_at: dt.datetime


class Invoice(MobileReadModel):
    id: int = Field(ge=1)
    project_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=2000)
    line_items: list[LineItem] = Field(default_factory=list)
    total: Money
    deposit: Money
    paid: Money
    balance: Money
    status: Literal["draft", "sent", "viewed", "deposit_paid", "paid"]
    net_days: int | None = None
    due_on: dt.date | None = None
    terms: str | None = None
    purchase_order_number: str | None = None
    payments: list[Payment] = Field(default_factory=list)
    sent_at: dt.datetime | None = None
    viewed_at: dt.datetime | None = None
    paid_at: dt.datetime | None = None
    created_at: dt.datetime
    public_url: str


def _require_project_reader(request: Request, project_id: int) -> mobile_auth.Principal:
    principal = mobile_auth.authenticate_request(request)
    if principal.kind == mobile_auth.STUDIO_OWNER and principal.has_scope("studio:read"):
        return principal
    if principal.kind == mobile_auth.WORKSPACE_GUEST and principal.resource_id == project_id:
        return principal
    raise _insufficient_scope()


def _project_exists(project_id: int) -> bool:
    return db.one("SELECT 1 AS x FROM projects WHERE id=?", (project_id,)) is not None


def _invoice_payments(invoice_id: int) -> list[Payment]:
    rows = db.all_(
        """SELECT id, amount_cents, kind, created_at FROM payments
             WHERE invoice_id=? ORDER BY created_at""",
        (invoice_id,),
    )
    return [
        Payment(
            id=int(row["id"]),
            invoice_id=invoice_id,
            amount=_money(row["amount_cents"]),
            kind=row["kind"] if row["kind"] in {"deposit", "balance", "full"} else "full",
            created_at=_sqlite_utc(row["created_at"]),
        )
        for row in rows
    ]


def _project_proposals(request: Request, project_id: int) -> list[Proposal]:
    rows = db.all_(
        """SELECT id, project_id, slug, title, intro, line_items, total_cents,
                  status, sent_at, viewed_at, accepted_at, created_at
             FROM proposals WHERE project_id=? AND status != 'draft'
             ORDER BY created_at DESC""",
        (project_id,),
    )
    result = []
    for row in rows:
        status = (
            row["status"] if row["status"] in {"sent", "viewed", "accepted", "declined"} else "sent"
        )
        result.append(
            Proposal(
                id=int(row["id"]),
                project_id=int(row["project_id"]),
                title=str(row["title"]).strip()[:2000] or f"Proposal {row['id']}",
                intro=_optional_text(row["intro"], maximum=10_000),
                line_items=_line_items(row["line_items"]),
                total=_money(row["total_cents"]),
                status=status,
                can_accept=status in ("sent", "viewed"),
                can_decline=status in ("sent", "viewed"),
                sent_at=_sqlite_utc(row["sent_at"]),
                viewed_at=_sqlite_utc(row["viewed_at"]),
                accepted_at=_sqlite_utc(row["accepted_at"]),
                created_at=_sqlite_utc(row["created_at"]),
                public_url=_document_public_url(request, "proposal", row["slug"]),
            )
        )
    return result


def _project_contracts(request: Request, project_id: int) -> list[Contract]:
    rows = db.all_(
        """SELECT id, project_id, slug, title, body, body_sha256, status,
                  signer_name, signed_at, sent_at, viewed_at, created_at
             FROM contracts WHERE project_id=? AND status != 'draft'
             ORDER BY created_at DESC""",
        (project_id,),
    )
    result = []
    for row in rows:
        status = row["status"] if row["status"] in {"sent", "viewed", "signed"} else "sent"
        body = str(row["body"] or "")
        etag = row["body_sha256"] or hashlib.sha256(body.encode()).hexdigest()
        result.append(
            Contract(
                id=int(row["id"]),
                project_id=int(row["project_id"]),
                title=str(row["title"]).strip()[:2000] or f"Contract {row['id']}",
                body=body[:200_000],
                status=status,
                can_sign=status in ("sent", "viewed"),
                fully_executed=status == "signed",
                document_etag=etag,
                signer_name=_optional_text(row["signer_name"], maximum=500),
                sent_at=_sqlite_utc(row["sent_at"]),
                viewed_at=_sqlite_utc(row["viewed_at"]),
                signed_at=_sqlite_utc(row["signed_at"]),
                countersigned_at=None,
                created_at=_sqlite_utc(row["created_at"]),
                public_url=_document_public_url(request, "contract", row["slug"]),
            )
        )
    return result


def _project_invoices(request: Request, project_id: int) -> list[Invoice]:
    rows = db.all_(
        """SELECT id, project_id, slug, title, line_items, total_cents, deposit_cents,
                  due_date, status, sent_at, viewed_at, paid_at, created_at
             FROM invoices WHERE project_id=? AND status != 'draft'
             ORDER BY created_at DESC""",
        (project_id,),
    )
    result = []
    for row in rows:
        status = (
            row["status"] if row["status"] in {"sent", "viewed", "deposit_paid", "paid"} else "sent"
        )
        invoice_id = int(row["id"])
        payments = _invoice_payments(invoice_id)
        paid_cents = sum(p.amount.minor_units for p in payments)
        total_cents = int(row["total_cents"])
        result.append(
            Invoice(
                id=invoice_id,
                project_id=int(row["project_id"]),
                title=str(row["title"]).strip()[:2000] or f"Invoice {row['id']}",
                line_items=_line_items(row["line_items"]),
                total=_money(total_cents),
                deposit=_money(row["deposit_cents"]),
                paid=_money(paid_cents),
                balance=_money(max(0, total_cents - paid_cents)),
                status=status,
                due_on=dt.date.fromisoformat(row["due_date"]) if row["due_date"] else None,
                payments=payments,
                sent_at=_sqlite_utc(row["sent_at"]),
                viewed_at=_sqlite_utc(row["viewed_at"]),
                paid_at=_sqlite_utc(row["paid_at"]),
                created_at=_sqlite_utc(row["created_at"]),
                public_url=_document_public_url(request, "invoice", row["slug"]),
            )
        )
    return result


@router.get("/projects/{project_id}/proposals", response_model=APIPage[Proposal])
def project_proposals(
    request: Request, response: Response, project_id: Annotated[int, Path(ge=1)]
) -> APIPage[Proposal]:
    _require_project_reader(request, project_id)
    if not _project_exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found.")
    items = _project_proposals(request, project_id)
    page = APIPage[Proposal](items=items, next_cursor=None, has_more=False)
    return _collection_response(request, response, page, resource=f"project-{project_id}-proposals")


@router.get("/projects/{project_id}/contracts", response_model=APIPage[Contract])
def project_contracts(
    request: Request, response: Response, project_id: Annotated[int, Path(ge=1)]
) -> APIPage[Contract]:
    _require_project_reader(request, project_id)
    if not _project_exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found.")
    items = _project_contracts(request, project_id)
    page = APIPage[Contract](items=items, next_cursor=None, has_more=False)
    return _collection_response(request, response, page, resource=f"project-{project_id}-contracts")


@router.get("/projects/{project_id}/invoices", response_model=APIPage[Invoice])
def project_invoices(
    request: Request, response: Response, project_id: Annotated[int, Path(ge=1)]
) -> APIPage[Invoice]:
    _require_project_reader(request, project_id)
    if not _project_exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found.")
    items = _project_invoices(request, project_id)
    page = APIPage[Invoice](items=items, next_cursor=None, has_more=False)
    return _collection_response(request, response, page, resource=f"project-{project_id}-invoices")


# ── Home: one summary shaped by whichever capability the client entered     ─
# through. No unified client account -- see docs/IOS-ARCHITECTURE.md §11.


class NextStepAction(MobileReadModel):
    id: str = Field(min_length=1, max_length=255)
    kind: Literal["proposal", "contract", "invoice", "gallery"]
    title: str = Field(min_length=1, max_length=500)
    detail: str = Field(max_length=2000)
    document_variant: Literal["proposal", "contract", "invoice"] | None = None
    document_id: int | None = Field(default=None, ge=1)
    gallery_id: int | None = Field(default=None, ge=1)
    public_url: str | None = None


class ClientDocumentPreview(MobileReadModel):
    variant: Literal["proposal", "contract", "invoice"]
    id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=2000)
    status: str = Field(min_length=1, max_length=32)
    total: Money | None = None
    balance: Money | None = None
    public_url: str


class ClientHomeSummary(MobileReadModel):
    principal_kind: Literal["gallery", "portal", "workspace", "document"]
    studio_name: str = Field(min_length=1, max_length=500)
    client_display_name: str | None = Field(default=None, max_length=2000)
    project_id: int | None = Field(default=None, ge=1)
    project_title: str | None = Field(default=None, max_length=2000)
    gallery_id: int | None = Field(default=None, ge=1)
    gallery_count: int = Field(default=0, ge=0)
    next_steps: list[NextStepAction] = Field(default_factory=list)
    document: ClientDocumentPreview | None = None


def _document_preview(request: Request, variant: str, doc_id: int) -> ClientDocumentPreview | None:
    if variant == "proposal":
        row = db.one(
            "SELECT id, slug, title, status, total_cents FROM proposals WHERE id=?", (doc_id,)
        )
    elif variant == "invoice":
        row = db.one(
            "SELECT id, slug, title, status, total_cents FROM invoices WHERE id=?", (doc_id,)
        )
    else:
        row = db.one("SELECT id, slug, title, status FROM contracts WHERE id=?", (doc_id,))
    if not row:
        return None
    total = _money(row["total_cents"]) if variant in ("proposal", "invoice") else None
    balance = None
    if variant == "invoice":
        paid = int(
            db.one(
                "SELECT COALESCE(SUM(amount_cents),0) AS n FROM payments WHERE invoice_id=?",
                (doc_id,),
            )["n"]
        )
        balance = _money(max(0, row["total_cents"] - paid))
    return ClientDocumentPreview(
        variant=variant,
        id=int(row["id"]),
        title=str(row["title"]),
        status=str(row["status"]),
        total=total,
        balance=balance,
        public_url=_document_public_url(request, variant, row["slug"]),
    )


def _client_home_summary(request: Request, principal: mobile_auth.Principal) -> ClientHomeSummary:
    studio_name = _studio_display_name(request)

    if principal.kind == mobile_auth.GALLERY_GUEST:
        gallery = db.one("SELECT id, title FROM galleries WHERE id=?", (principal.resource_id,))
        next_steps = (
            [
                NextStepAction(
                    id=f"gallery:{gallery['id']}",
                    kind="gallery",
                    title="Review your gallery",
                    detail=str(gallery["title"]),
                    gallery_id=int(gallery["id"]),
                )
            ]
            if gallery
            else []
        )
        return ClientHomeSummary(
            principal_kind="gallery",
            studio_name=studio_name,
            gallery_id=int(gallery["id"]) if gallery else None,
            gallery_count=1 if gallery else 0,
            next_steps=next_steps,
        )

    if principal.kind == mobile_auth.PORTAL_GUEST:
        row = db.one(
            """SELECT p.client_id, COALESCE(NULLIF(c.company,''), c.name) AS client_name
                 FROM portals p JOIN clients c ON c.id=p.client_id WHERE p.id=?""",
            (principal.resource_id,),
        )
        gallery_count = 0
        if row:
            gallery_count = int(
                db.one(
                    "SELECT COUNT(*) AS n FROM galleries WHERE client_id=? AND published=1",
                    (row["client_id"],),
                )["n"]
            )
        next_steps = (
            [
                NextStepAction(
                    id="portal:galleries",
                    kind="gallery",
                    title="Review your galleries",
                    detail=(
                        f"{gallery_count} gallery delivered"
                        if gallery_count == 1
                        else f"{gallery_count} galleries delivered"
                    ),
                )
            ]
            if gallery_count
            else []
        )
        return ClientHomeSummary(
            principal_kind="portal",
            studio_name=studio_name,
            client_display_name=row["client_name"] if row else None,
            gallery_count=gallery_count,
            next_steps=next_steps,
        )

    if principal.kind == mobile_auth.WORKSPACE_GUEST:
        project = db.one(
            """SELECT pr.id, pr.title, pr.gallery_id,
                      COALESCE(NULLIF(c.company,''), c.name) AS client_name
                 FROM projects pr JOIN clients c ON c.id=pr.client_id WHERE pr.id=?""",
            (principal.resource_id,),
        )
        if not project:
            raise HTTPException(status_code=404, detail="Workspace not found.")
        gallery = None
        if project["gallery_id"]:
            gallery = db.one(
                "SELECT id, title FROM galleries WHERE id=? AND published=1",
                (project["gallery_id"],),
            )

        next_steps: list[NextStepAction] = []
        for proposal in _project_proposals(request, project["id"]):
            if proposal.can_accept:
                next_steps.append(
                    NextStepAction(
                        id=f"proposal:{proposal.id}",
                        kind="proposal",
                        title=f"Review {proposal.title}",
                        detail="Accept or decline your proposal.",
                        document_variant="proposal",
                        document_id=proposal.id,
                        public_url=proposal.public_url,
                    )
                )
        for contract in _project_contracts(request, project["id"]):
            if contract.can_sign:
                next_steps.append(
                    NextStepAction(
                        id=f"contract:{contract.id}",
                        kind="contract",
                        title=f"Sign {contract.title}",
                        detail="Your signature is needed.",
                        document_variant="contract",
                        document_id=contract.id,
                        public_url=contract.public_url,
                    )
                )
        for invoice in _project_invoices(request, project["id"]):
            if invoice.balance.minor_units > 0 and invoice.status in (
                "sent",
                "viewed",
                "deposit_paid",
            ):
                next_steps.append(
                    NextStepAction(
                        id=f"invoice:{invoice.id}",
                        kind="invoice",
                        title=f"Pay {invoice.title}",
                        detail="A balance is due.",
                        document_variant="invoice",
                        document_id=invoice.id,
                        public_url=invoice.public_url,
                    )
                )
        if gallery:
            next_steps.append(
                NextStepAction(
                    id=f"gallery:{gallery['id']}",
                    kind="gallery",
                    title="Review your wedding gallery",
                    detail=str(gallery["title"]),
                    gallery_id=int(gallery["id"]),
                )
            )

        return ClientHomeSummary(
            principal_kind="workspace",
            studio_name=studio_name,
            client_display_name=project["client_name"],
            project_id=int(project["id"]),
            project_title=project["title"],
            gallery_id=int(gallery["id"]) if gallery else None,
            gallery_count=1 if gallery else 0,
            next_steps=next_steps,
        )

    if principal.kind == mobile_auth.DOCUMENT_GUEST and principal.resource_variant:
        preview = _document_preview(request, principal.resource_variant, principal.resource_id)
        return ClientHomeSummary(
            principal_kind="document",
            studio_name=studio_name,
            document=preview,
        )

    raise _insufficient_scope()


@router.get("/client/home", response_model=ClientHomeSummary)
def client_home(request: Request, response: Response) -> ClientHomeSummary:
    principal = mobile_auth.authenticate_request(request)
    if principal.kind == mobile_auth.STUDIO_OWNER:
        raise _insufficient_scope()
    summary = _client_home_summary(request, principal)
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["Vary"] = "Authorization"
    return summary


# ── Bookings: workspace_guest and portal_guest resolve a real client_id;    ─
# gallery_guest/document_guest have no client-wide session to scope from.


def _client_id_for_principal(principal: mobile_auth.Principal) -> int | None:
    if principal.kind == mobile_auth.WORKSPACE_GUEST:
        row = db.one("SELECT client_id FROM projects WHERE id=?", (principal.resource_id,))
        return int(row["client_id"]) if row else None
    if principal.kind == mobile_auth.PORTAL_GUEST:
        row = db.one("SELECT client_id FROM portals WHERE id=?", (principal.resource_id,))
        return int(row["client_id"]) if row else None
    return None


@router.get("/client/bookings", response_model=APIPage[Booking])
def client_bookings(
    request: Request,
    response: Response,
    cursor: Annotated[str | None, Query(max_length=_MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> APIPage[Booking]:
    principal = mobile_auth.authenticate_request(request)
    client_id = _client_id_for_principal(principal)
    if client_id is None:
        raise _insufficient_scope()
    decoded = _decode_cursor(cursor, "client-bookings", (str, int))
    cursor_predicate = ""
    params: list[object] = [client_id]
    if decoded is not None:
        start_utc, booking_id = str(decoded[0]), int(decoded[1])
        cursor_predicate = "AND (b.start_utc > ? OR (b.start_utc = ? AND b.id > ?))"
        params.extend((start_utc, start_utc, booking_id))
    rows = db.all_(
        f"""SELECT b.id, b.event_type_id, e.name AS event_name, b.name, b.email, b.phone,
                   b.notes, b.start_utc, b.end_utc, b.tz, b.status, b.client_id, b.project_id,
                   b.reschedule_of, b.cancel_reason, b.cancelled_at, b.created_at
              FROM bookings b JOIN event_types e ON e.id=b.event_type_id
              WHERE b.client_id=? {cursor_predicate}
              ORDER BY b.start_utc, b.id LIMIT ?""",
        (*params, limit + 1),
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = [_booking_from_row(row) for row in page_rows]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = _encode_cursor("client-bookings", (str(last["start_utc"]), int(last["id"])))
    page = APIPage[Booking](items=items, next_cursor=next_cursor, has_more=has_more)
    return _collection_response(request, response, page, resource="client-bookings")
