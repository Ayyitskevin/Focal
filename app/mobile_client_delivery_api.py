"""Read-only client delivery aggregates for the native Mise API.

Each route accepts exactly one capability principal. A portal session cannot
be promoted into gallery or document authority, while a workspace returns only
the same child slugs already linked by its browser page. Document actions stay
on canonical server-rendered pages so accepting, signing, and paying retain
their existing integrity and transaction boundaries.

Response models are assembled field by field. Credential material, filesystem
paths, Stripe identifiers, private notes, and signer network data therefore
cannot enter the JSON contract through a raw database row.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import unicodedata
from enum import StrEnum
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import db, mobile_auth, urls
from .public import portal as public_portal

_CURRENCY = "USD"
_INT64_MAX = 2**63 - 1
_MAX_PORTAL_ITEMS = 500
_MAX_WORKSPACE_RESOURCES = 500
_MAX_LINE_ITEMS = 250
_MAX_LINE_ITEM_JSON_CHARS = 1_000_000
_MAX_PAYMENTS = 500
_MAX_DOCUMENT_DETAIL = 500_000
_MAX_QUANTITY = 1_000_000
_SLUG_MAX = 255


class ClientDeliveryModel(BaseModel):
    """Strict immutable base for client delivery response DTOs."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Money(ClientDeliveryModel):
    minor_units: int = Field(ge=0, le=_INT64_MAX)
    currency_code: Literal["USD"] = _CURRENCY


class LineItem(ClientDeliveryModel):
    label: str = Field(min_length=1, max_length=500)
    quantity: int = Field(ge=1, le=_MAX_QUANTITY)
    unit_price: Money
    sku: str | None = Field(default=None, min_length=1, max_length=128)


class Payment(ClientDeliveryModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    invoice_id: int = Field(gt=0, le=_INT64_MAX)
    amount: Money
    kind: Literal["deposit", "balance", "full"]
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        return _aware_utc(value)


class PortalGalleryCard(ClientDeliveryModel):
    """Metadata only: deliberately has no URL or media authorization."""

    id: int = Field(gt=0, le=_INT64_MAX)
    title: str = Field(min_length=1, max_length=2000)
    slug: str = Field(min_length=1, max_length=_SLUG_MAX)
    expires_on: dt.date | None = None
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        return _aware_utc(value)


class PortalBrandAssetCard(ClientDeliveryModel):
    """A discoverable portal item, not a server path or download capability."""

    id: int = Field(gt=0, le=_INT64_MAX)
    filename: str = Field(min_length=1, max_length=1000)
    byte_count: int | None = Field(default=None, ge=0, le=_INT64_MAX)
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        return _aware_utc(value)


class UsageLicense(ClientDeliveryModel):
    title: str = Field(min_length=1, max_length=2000)
    scope: str = Field(max_length=20_000)
    tier: str = Field(min_length=1, max_length=255)
    exclusive: bool
    territory: list[str] = Field(max_length=100)
    channels: list[str] = Field(max_length=100)
    term: str = Field(min_length=1, max_length=1000)


class PortalDelivery(ClientDeliveryModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    client_display_name: str = Field(min_length=1, max_length=2000)
    galleries: list[PortalGalleryCard] = Field(max_length=_MAX_PORTAL_ITEMS)
    brand_assets: list[PortalBrandAssetCard] = Field(max_length=_MAX_PORTAL_ITEMS)
    licenses: list[UsageLicense] = Field(max_length=_MAX_PORTAL_ITEMS)
    usage_rights_note: str | None = Field(default=None, max_length=20_000)


class WorkspaceResourceKind(StrEnum):
    PROPOSAL = "proposal"
    CONTRACT = "contract"
    INVOICE = "invoice"
    GALLERY = "gallery"


class WorkspaceResource(ClientDeliveryModel):
    kind: WorkspaceResourceKind
    id: int = Field(gt=0, le=_INT64_MAX)
    title: str = Field(min_length=1, max_length=2000)
    status: str = Field(min_length=1, max_length=64)
    slug: str = Field(min_length=1, max_length=_SLUG_MAX)
    total: Money | None = None
    due_on: dt.date | None = None
    action_url: str = Field(min_length=1, max_length=2048)

    @field_validator("action_url")
    @classmethod
    def action_url_is_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("action_url must be a credential-free HTTPS URL")
        return value


class WorkspaceDelivery(ClientDeliveryModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    title: str = Field(min_length=1, max_length=2000)
    client_display_name: str = Field(min_length=1, max_length=2000)
    resources: list[WorkspaceResource] = Field(max_length=_MAX_WORKSPACE_RESOURCES)


class DocumentKind(StrEnum):
    PROPOSAL = "proposal"
    CONTRACT = "contract"
    INVOICE = "invoice"


_DOCUMENT_ACTIONS = {
    DocumentKind.PROPOSAL: "respond",
    DocumentKind.CONTRACT: "sign",
    DocumentKind.INVOICE: "checkout",
}


class DocumentDelivery(ClientDeliveryModel):
    kind: DocumentKind
    id: int = Field(gt=0, le=_INT64_MAX)
    project_id: int = Field(gt=0, le=_INT64_MAX)
    title: str = Field(min_length=1, max_length=2000)
    project_title: str = Field(min_length=1, max_length=2000)
    client_display_name: str = Field(min_length=1, max_length=2000)
    status: str = Field(min_length=1, max_length=64)
    detail: str | None = Field(default=None, max_length=_MAX_DOCUMENT_DETAIL)
    line_items: list[LineItem] = Field(max_length=_MAX_LINE_ITEMS)
    total: Money | None = None
    deposit: Money | None = None
    paid: Money | None = None
    balance: Money | None = None
    payments: list[Payment] = Field(max_length=_MAX_PAYMENTS)
    payment_count: int = Field(ge=0, le=_INT64_MAX)
    payments_truncated: bool
    due_on: dt.date | None = None
    sent_at: dt.datetime | None = None
    viewed_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    document_etag: str | None = Field(default=None, max_length=80)
    can_act: bool
    action_url: str = Field(min_length=1, max_length=2048)

    @field_validator("sent_at", "viewed_at", "completed_at")
    @classmethod
    def timestamps_are_utc(cls, value: dt.datetime | None) -> dt.datetime | None:
        return _aware_utc(value) if value is not None else None

    @field_validator("action_url")
    @classmethod
    def action_url_is_https(cls, value: str) -> str:
        return WorkspaceResource.action_url_is_https(value)


def _aware_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(dt.UTC)


def _utc_timestamp(value: str | dt.datetime | None) -> dt.datetime | None:
    """Interpret SQLite offset-less timestamps as UTC."""

    if value is None:
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        normalized = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("stored timestamp is not valid ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _date_only(value: str | dt.date | None) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise ValueError("stored date is not a valid ISO date") from exc


def _money(cents: int) -> Money:
    return Money(minor_units=int(cents), currency_code=_CURRENCY)


def _clean_text(value: object, *, maximum: int, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError("required display text is missing")
        return None
    # Keep newlines and tabs in authored client copy while removing control bytes.
    raw = str(value)
    cleaned = "".join(
        " " if unicodedata.category(char) == "Cc" and char not in "\n\t" else char for char in raw
    ).strip()
    cleaned = cleaned[:maximum].rstrip()
    if not cleaned:
        if required:
            raise ValueError("required display text is empty")
        return None
    return cleaned


def _line_items(raw: object) -> list[LineItem]:
    """Return only bounded, display-safe line items from legacy JSON."""

    serialized = str(raw or "[]")
    if len(serialized) > _MAX_LINE_ITEM_JSON_CHARS:
        return []
    try:
        values = json.loads(serialized)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    result: list[LineItem] = []
    for value in values[:_MAX_LINE_ITEMS]:
        if not isinstance(value, dict):
            continue
        label = _clean_text(value.get("label"), maximum=500)
        quantity = value.get("qty", value.get("quantity"))
        unit_cents = value.get("unit_cents")
        if (
            label is None
            or isinstance(quantity, bool)
            or isinstance(unit_cents, bool)
            or not isinstance(quantity, int)
            or not isinstance(unit_cents, int)
            or not 1 <= quantity <= _MAX_QUANTITY
            or not 0 <= unit_cents <= _INT64_MAX
            or quantity * unit_cents > _INT64_MAX
        ):
            continue
        sku = _clean_text(value.get("sku"), maximum=128)
        result.append(
            LineItem(
                label=label,
                quantity=quantity,
                unit_price=_money(unit_cents),
                sku=sku,
            )
        )
    return result


def _insufficient_scope(detail: str) -> mobile_auth.MobileAuthError:
    return mobile_auth.MobileAuthError(403, "auth.insufficient_scope", detail)


def _revoked_capability() -> mobile_auth.MobileAuthError:
    # Keep resource disappearance indistinguishable from credential revocation.
    return mobile_auth.MobileAuthError(
        401,
        "auth.invalid_token",
        "The token is invalid or expired.",
    )


def require_portal_guest(request: Request) -> mobile_auth.Principal:
    principal = mobile_auth.authenticate_request(request)
    resource_id = principal.resource_id
    if (
        principal.kind != mobile_auth.PORTAL_GUEST
        or resource_id is None
        or resource_id <= 0
        or not principal.has_scope(f"portal:{resource_id}:read")
    ):
        raise _insufficient_scope("This resource requires its client portal capability.")
    return principal


def require_workspace_guest(request: Request) -> mobile_auth.Principal:
    principal = mobile_auth.authenticate_request(request)
    resource_id = principal.resource_id
    if (
        principal.kind != mobile_auth.WORKSPACE_GUEST
        or resource_id is None
        or resource_id <= 0
        or not principal.has_scope(f"workspace:{resource_id}:read")
    ):
        raise _insufficient_scope("This resource requires its project workspace capability.")
    return principal


def require_document_guest(request: Request) -> mobile_auth.Principal:
    principal = mobile_auth.authenticate_request(request)
    resource_id = principal.resource_id
    variant = principal.resource_variant
    if (
        principal.kind != mobile_auth.DOCUMENT_GUEST
        or resource_id is None
        or resource_id <= 0
        or variant not in {item.value for item in DocumentKind}
        or not principal.has_scope(f"document:{variant}:{resource_id}:read")
    ):
        raise _insufficient_scope("This resource requires its exact document capability.")
    return principal


router = APIRouter(tags=["client delivery"])


def _cache_headers(etag: str) -> dict[str, str]:
    return {
        "Cache-Control": "private, no-cache",
        "ETag": etag,
        "Vary": "Authorization",
    }


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False

    def normalized(value: str) -> str:
        value = value.strip()
        return value[2:] if value.startswith("W/") else value

    expected = normalized(etag)
    return any(part.strip() == "*" or normalized(part) == expected for part in header.split(","))


def _conditional(
    request: Request,
    response: Response,
    payload: ClientDeliveryModel,
) -> ClientDeliveryModel | Response:
    canonical = payload.model_dump_json().encode("utf-8")
    etag = f'"{hashlib.sha256(canonical).hexdigest()}"'
    headers = _cache_headers(etag)
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    for key, value in headers.items():
        response.headers[key] = value
    return payload


def _normalized_origin(value: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        return None
    normalized_port = None if port in (None, 443) else port
    return "https", parsed.hostname.casefold(), normalized_port


def _action_url(request: Request, prefix: str, slug: object) -> str:
    """Build a same-origin HTTPS URL from a fixed route prefix and DB slug."""

    raw_slug = str(slug or "")
    if not raw_slug or len(raw_slug) > _SLUG_MAX:
        raise _revoked_capability()
    canonical = urls.public_base_url(request)
    request_origin = urls.request_origin(request)
    canonical_parts = _normalized_origin(canonical)
    request_parts = _normalized_origin(request_origin)
    if canonical_parts is None or canonical_parts != request_parts:
        raise mobile_auth.MobileAuthError(
            400,
            "request.invalid_origin",
            "A canonical HTTPS request origin is required.",
        )
    _, hostname, port = canonical_parts
    netloc = hostname if port is None else f"{hostname}:{port}"
    encoded_slug = quote(raw_slug, safe="")
    path = f"/{prefix}/{encoded_slug}"
    result = urlunsplit(("https", netloc, path, "", ""))
    parsed = urlsplit(result)
    if parsed.path != path or parsed.query or parsed.fragment:
        raise RuntimeError("unsafe client action URL construction")
    return result


def _bounded_client_licenses(client_id: int) -> list[dict]:
    """Match the browser portal's active-license hierarchy with bounded reads."""

    rows: dict[int, dict] = {}
    select = (
        "l.id, l.title, l.scope, l.usage_tier, l.exclusivity, l.territory, "
        "l.channels, l.starts_on, l.ends_on, l.perpetual"
    )

    def add(sql: str, params: tuple) -> None:
        remaining = _MAX_PORTAL_ITEMS - len(rows)
        if remaining <= 0:
            return
        for item in db.all_(f"{sql} LIMIT ?", (*params, remaining)):
            rows[int(item["id"])] = public_portal._friendly_license(item)

    add(
        f"SELECT {select} FROM licenses l "
        "WHERE l.holder_client_id=? AND l.status='active' AND l.deleted_at IS NULL "
        "ORDER BY l.id",
        (client_id,),
    )
    # A parent graph should be a tree, but malformed direct SQL can introduce a
    # cycle. Keep a delimiter-wrapped ID path so a repeated ancestor is never
    # traversed. A hard depth cap also bounds a very deep, otherwise valid tree.
    # Do not use UNION with a depth column here: every trip around A -> B -> A
    # has a new depth and would therefore remain distinct forever.
    ancestors = [
        int(item["id"])
        for item in db.all_(
            """WITH RECURSIVE sup(id, parent_id, depth, path) AS (
                   SELECT id, parent_id, 0, ',' || id || ',' FROM clients WHERE id=?
                   UNION ALL
                   SELECT c.id, c.parent_id, sup.depth+1, sup.path || c.id || ','
                     FROM clients c JOIN sup ON c.id=sup.parent_id
                    WHERE sup.depth < ?
                      AND instr(sup.path, ',' || c.id || ',') = 0
               )
               SELECT id FROM sup WHERE id<>?
               ORDER BY depth LIMIT ?""",
            (client_id, _MAX_PORTAL_ITEMS, client_id, _MAX_PORTAL_ITEMS),
        )
    ]
    if ancestors and len(rows) < _MAX_PORTAL_ITEMS:
        placeholders = ",".join("?" * len(ancestors))
        add(
            f"SELECT {select} FROM licenses l "
            "WHERE l.coverage_scope='holder_and_descendants' "
            f"AND l.holder_client_id IN ({placeholders}) "
            "AND l.status='active' AND l.deleted_at IS NULL ORDER BY l.id",
            tuple(ancestors),
        )
    add(
        f"SELECT {select} FROM licenses l "
        "JOIN license_clients lc ON lc.license_id=l.id "
        "WHERE lc.client_id=? AND l.coverage_scope='specific' "
        "AND l.status='active' AND l.deleted_at IS NULL ORDER BY l.id",
        (client_id,),
    )
    return sorted(rows.values(), key=lambda item: (item["title"] or "").lower())


def _portal_payload(portal_id: int) -> PortalDelivery:
    row = db.one(
        """SELECT p.id, p.client_id, c.name, c.company, c.usage_rights
             FROM portals p JOIN clients c ON c.id=p.client_id
            WHERE p.id=? AND p.published=1""",
        (portal_id,),
    )
    if row is None:
        raise _revoked_capability()

    galleries = [
        PortalGalleryCard(
            id=int(item["id"]),
            title=_clean_text(item["title"], maximum=2000, required=True),
            slug=_clean_text(item["slug"], maximum=_SLUG_MAX, required=True),
            expires_on=_date_only(item["expires_at"]),
            created_at=_utc_timestamp(item["created_at"]),
        )
        for item in db.all_(
            """SELECT id, title, slug, expires_at, created_at
                 FROM galleries
                WHERE client_id=? AND published=1
                ORDER BY created_at DESC, id DESC LIMIT ?""",
            (row["client_id"], _MAX_PORTAL_ITEMS),
        )
    ]
    brand_assets = [
        PortalBrandAssetCard(
            id=int(item["id"]),
            filename=_clean_text(item["filename"], maximum=1000, required=True),
            byte_count=(
                int(item["bytes"])
                if item["bytes"] is not None and 0 <= int(item["bytes"]) <= _INT64_MAX
                else None
            ),
            created_at=_utc_timestamp(item["created_at"]),
        )
        for item in db.all_(
            """SELECT id, filename, bytes, created_at
                 FROM brand_assets WHERE client_id=?
                ORDER BY created_at DESC, id DESC LIMIT ?""",
            (row["client_id"], _MAX_PORTAL_ITEMS),
        )
    ]
    licenses = [
        UsageLicense(
            title=_clean_text(item["title"], maximum=2000, required=True),
            scope=_clean_text(item["scope"], maximum=20_000) or "",
            tier=_clean_text(item["tier"], maximum=255, required=True),
            exclusive=bool(item["exclusive"]),
            territory=[
                text
                for value in item["territory"][:100]
                if (text := _clean_text(value, maximum=255)) is not None
            ],
            channels=[
                text
                for value in item["channels"][:100]
                if (text := _clean_text(value, maximum=255)) is not None
            ],
            term=_clean_text(item["term"], maximum=1000, required=True),
        )
        for item in _bounded_client_licenses(int(row["client_id"]))
    ]
    return PortalDelivery(
        id=int(row["id"]),
        client_display_name=_clean_text(row["company"] or row["name"], maximum=2000, required=True),
        galleries=galleries,
        brand_assets=brand_assets,
        licenses=licenses,
        usage_rights_note=_clean_text(row["usage_rights"], maximum=20_000),
    )


@router.get("/client/portal", response_model=PortalDelivery)
def client_portal(
    request: Request,
    response: Response,
    principal: mobile_auth.Principal = Depends(require_portal_guest),
) -> PortalDelivery | Response:
    payload = _portal_payload(int(principal.resource_id))
    return _conditional(request, response, payload)


def _workspace_payload(request: Request, project_id: int) -> WorkspaceDelivery:
    row = db.one(
        """SELECT p.id, p.title, c.name, c.company
             FROM projects p JOIN clients c ON c.id=p.client_id
            WHERE p.id=? AND p.workspace_published=1""",
        (project_id,),
    )
    if row is None:
        raise _revoked_capability()

    resources: list[WorkspaceResource] = []
    for kind, prefix, table in (
        (WorkspaceResourceKind.PROPOSAL, "p", "proposals"),
        (WorkspaceResourceKind.CONTRACT, "c", "contracts"),
        (WorkspaceResourceKind.INVOICE, "i", "invoices"),
    ):
        remaining = _MAX_WORKSPACE_RESOURCES - len(resources)
        if remaining <= 0:
            break
        select = "id, title, status, slug, created_at"
        if kind == WorkspaceResourceKind.PROPOSAL:
            select += ", total_cents, NULL AS due_date"
        elif kind == WorkspaceResourceKind.INVOICE:
            select += ", total_cents, due_date"
        else:
            select += ", NULL AS total_cents, NULL AS due_date"
        for item in db.all_(
            f"""SELECT {select} FROM {table}
                 WHERE project_id=? AND status!='draft'
                 ORDER BY created_at DESC, id DESC LIMIT ?""",
            (project_id, remaining),
        ):
            resources.append(
                WorkspaceResource(
                    kind=kind,
                    id=int(item["id"]),
                    title=_clean_text(item["title"], maximum=2000, required=True),
                    status=str(item["status"]),
                    slug=_clean_text(item["slug"], maximum=_SLUG_MAX, required=True),
                    total=(
                        _money(int(item["total_cents"]))
                        if item["total_cents"] is not None
                        else None
                    ),
                    due_on=_date_only(item["due_date"]),
                    action_url=_action_url(request, prefix, item["slug"]),
                )
            )
    if len(resources) < _MAX_WORKSPACE_RESOURCES:
        gallery = db.one(
            """SELECT g.id, g.title, g.slug
                 FROM projects p JOIN galleries g ON g.id=p.gallery_id
                WHERE p.id=? AND g.published=1""",
            (project_id,),
        )
        if gallery is not None:
            resources.append(
                WorkspaceResource(
                    kind=WorkspaceResourceKind.GALLERY,
                    id=int(gallery["id"]),
                    title=_clean_text(gallery["title"], maximum=2000, required=True),
                    status="published",
                    slug=_clean_text(gallery["slug"], maximum=_SLUG_MAX, required=True),
                    total=None,
                    due_on=None,
                    action_url=_action_url(request, "g", gallery["slug"]),
                )
            )
    return WorkspaceDelivery(
        id=int(row["id"]),
        title=_clean_text(row["title"], maximum=2000, required=True),
        client_display_name=_clean_text(row["company"] or row["name"], maximum=2000, required=True),
        resources=resources,
    )


@router.get("/client/workspace", response_model=WorkspaceDelivery)
def client_workspace(
    request: Request,
    response: Response,
    principal: mobile_auth.Principal = Depends(require_workspace_guest),
) -> WorkspaceDelivery | Response:
    payload = _workspace_payload(request, int(principal.resource_id))
    return _conditional(request, response, payload)


def _document_base(table: str, resource_id: int, *, connection=None):
    if table not in {"proposals", "contracts", "invoices"}:
        raise ValueError("unsupported document table")
    query = f"""SELECT d.*, p.title AS project_title, c.name AS client_name, c.company
                  FROM {table} d
                  JOIN projects p ON p.id=d.project_id
                  JOIN clients c ON c.id=p.client_id
                 WHERE d.id=? AND d.status!='draft'"""
    row = (
        connection.execute(query, (resource_id,)).fetchone()
        if connection is not None
        else db.one(query, (resource_id,))
    )
    if row is None:
        raise _revoked_capability()
    return row


def _document_common(row, kind: DocumentKind, request: Request) -> dict:
    return {
        "kind": kind,
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "title": _clean_text(row["title"], maximum=2000, required=True),
        "project_title": _clean_text(row["project_title"], maximum=2000, required=True),
        "client_display_name": _clean_text(
            row["company"] or row["client_name"], maximum=2000, required=True
        ),
        "status": str(row["status"]),
        "sent_at": _utc_timestamp(row["sent_at"]),
        "viewed_at": _utc_timestamp(row["viewed_at"]),
        "action_url": _action_url(
            request,
            {
                DocumentKind.PROPOSAL: "p",
                DocumentKind.CONTRACT: "c",
                DocumentKind.INVOICE: "i",
            }[kind],
            row["slug"],
        ),
    }


def _proposal_payload(
    request: Request,
    resource_id: int,
    *,
    action_authorized: bool,
) -> DocumentDelivery:
    row = _document_base("proposals", resource_id)
    common = _document_common(row, DocumentKind.PROPOSAL, request)
    return DocumentDelivery(
        **common,
        detail=_clean_text(row["intro"], maximum=100_000),
        line_items=_line_items(row["line_items"]),
        total=_money(int(row["total_cents"])),
        deposit=None,
        paid=None,
        balance=None,
        payments=[],
        payment_count=0,
        payments_truncated=False,
        due_on=None,
        completed_at=_utc_timestamp(row["accepted_at"]),
        document_etag=None,
        can_act=action_authorized and row["status"] in ("sent", "viewed"),
    )


def _contract_payload(
    request: Request,
    resource_id: int,
    *,
    action_authorized: bool,
) -> DocumentDelivery:
    row = _document_base("contracts", resource_id)
    body = str(row["body"])
    if len(body) > _MAX_DOCUMENT_DETAIL:
        raise mobile_auth.MobileAuthError(
            413,
            "document.too_large",
            "This document is too large for native delivery.",
        )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    stored_digest = str(row["body_sha256"] or "")
    integrity_ok = bool(re.fullmatch(r"[0-9a-f]{64}", stored_digest)) and hmac.compare_digest(
        stored_digest, digest
    )
    if not integrity_ok:
        raise mobile_auth.MobileAuthError(
            409,
            "document.integrity_failed",
            "This document could not be verified.",
        )
    common = _document_common(row, DocumentKind.CONTRACT, request)
    return DocumentDelivery(
        **common,
        # A legal snapshot is returned exactly as authored; unlike ordinary
        # display strings it must not be normalized behind its integrity hash.
        detail=body,
        line_items=[],
        total=None,
        deposit=None,
        paid=None,
        balance=None,
        payments=[],
        payment_count=0,
        payments_truncated=False,
        due_on=None,
        completed_at=_utc_timestamp(row["signed_at"]),
        document_etag=f"sha256:{digest}",
        can_act=action_authorized and row["status"] in ("sent", "viewed"),
    )


def _invoice_snapshot(resource_id: int):
    """Read the invoice, history, and authoritative totals from one snapshot."""

    connection = db.connect()
    try:
        connection.execute("BEGIN")
        row = _document_base("invoices", resource_id, connection=connection)
        payment_summary = connection.execute(
            """SELECT COUNT(*) AS payment_count,
                      COALESCE(SUM(amount_cents), 0) AS paid_cents
                 FROM payments WHERE invoice_id=?""",
            (resource_id,),
        ).fetchone()
        payment_rows = connection.execute(
            """SELECT id, invoice_id, amount_cents, kind, created_at
                 FROM payments WHERE invoice_id=?
                 ORDER BY created_at DESC, id DESC LIMIT ?""",
            (resource_id, _MAX_PAYMENTS),
        ).fetchall()
        assert payment_summary is not None
        return row, payment_summary, payment_rows
    finally:
        connection.rollback()
        connection.close()


def _invoice_payload(
    request: Request,
    resource_id: int,
    *,
    action_authorized: bool,
) -> DocumentDelivery:
    row, payment_summary, payment_rows = _invoice_snapshot(resource_id)
    common = _document_common(row, DocumentKind.INVOICE, request)
    payments = [
        Payment(
            id=int(item["id"]),
            invoice_id=int(item["invoice_id"]),
            amount=_money(int(item["amount_cents"])),
            kind=item["kind"],
            created_at=_utc_timestamp(item["created_at"]),
        )
        for item in payment_rows
    ]
    total_cents = int(row["total_cents"])
    payment_count = int(payment_summary["payment_count"])
    paid_cents = int(payment_summary["paid_cents"])
    balance_cents = max(0, total_cents - paid_cents)
    return DocumentDelivery(
        **common,
        detail=_clean_text(row["terms"], maximum=100_000),
        line_items=_line_items(row["line_items"]),
        total=_money(total_cents),
        deposit=_money(int(row["deposit_cents"])),
        paid=_money(paid_cents),
        balance=_money(balance_cents),
        payments=payments,
        payment_count=payment_count,
        payments_truncated=payment_count > len(payments),
        due_on=_date_only(row["due_date"]),
        completed_at=_utc_timestamp(row["paid_at"]),
        document_etag=None,
        can_act=action_authorized and row["status"] != "paid" and balance_cents > 0,
    )


@router.get("/client/document", response_model=DocumentDelivery)
def client_document(
    request: Request,
    response: Response,
    principal: mobile_auth.Principal = Depends(require_document_guest),
) -> DocumentDelivery | Response:
    resource_id = int(principal.resource_id)
    variant = DocumentKind(principal.resource_variant)
    action_authorized = principal.has_scope(
        f"document:{variant.value}:{resource_id}:{_DOCUMENT_ACTIONS[variant]}"
    )
    payload = {
        DocumentKind.PROPOSAL: _proposal_payload,
        DocumentKind.CONTRACT: _contract_payload,
        DocumentKind.INVOICE: _invoice_payload,
    }[variant](request, resource_id, action_authorized=action_authorized)
    return _conditional(request, response, payload)
