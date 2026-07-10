"""Read-only studio-owner resources for the native Mise API.

This router is intentionally safe to mount on its own: every route inherits an
exact owner-principal dependency.  Bearer authentication is re-evaluated for
every request (including every cursor page), and pagination cursors carry only
ordering state -- never tenant identity or authorization.

Response DTOs are purpose-built wire models rather than serialized SQLite rows.
That keeps notes, credential material, server paths, and other internal columns
out of the native contract by construction.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
from enum import StrEnum
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import config, db, mobile_auth
from .admin import common as admin_common
from .admin import studio as admin_studio

_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100
_CURRENCY = "USD"
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


class OwnerAPIModel(BaseModel):
    """Strict Pydantic 2 base for owner read responses."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProjectStatus(StrEnum):
    INQUIRY_RECEIVED = "inquiry_received"
    CONSULTATION_CALL = "consultation_call"
    PROPOSAL_SENT = "proposal_sent"
    CONTRACT_SIGNED = "contract_signed"
    RETAINER_PAID = "retainer_paid"
    SESSION_PLANNING = "session_planning"
    PROJECT_CLOSED = "project_closed"
    ARCHIVED = "archived"


class Money(OwnerAPIModel):
    minor_units: int = Field(ge=_INT64_MIN, le=_INT64_MAX)
    currency_code: Literal["USD"] = _CURRENCY


class MoneyCount(OwnerAPIModel):
    count: int = Field(ge=0)
    amount: Money


class DashboardKPIs(OwnerAPIModel):
    inquiries_delta_7_days: int
    bookings_delta_7_days: int
    collected_7_days: Money


class TaskSummary(OwnerAPIModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    title: str = Field(min_length=1, max_length=2000)
    due_on: dt.date | None = None
    project_id: int | None = Field(default=None, gt=0, le=_INT64_MAX)
    project_title: str | None = Field(default=None, min_length=1, max_length=2000)
    is_overdue: bool


class UpcomingProject(OwnerAPIModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    title: str = Field(min_length=1, max_length=2000)
    client_display_name: str = Field(min_length=1, max_length=2000)
    shoot_on: dt.date
    days_out: int


class InvoiceStatus(StrEnum):
    SENT = "sent"
    VIEWED = "viewed"
    DEPOSIT_PAID = "deposit_paid"


class InvoiceSummary(OwnerAPIModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    project_id: int = Field(gt=0, le=_INT64_MAX)
    title: str = Field(min_length=1, max_length=2000)
    client_display_name: str = Field(min_length=1, max_length=2000)
    total: Money
    balance: Money
    status: InvoiceStatus
    due_on: dt.date | None = None
    is_overdue: bool


class ActivityItem(OwnerAPIModel):
    id: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=2000)
    detail: str | None = Field(default=None, max_length=2000)
    occurred_at: dt.datetime

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(dt.UTC)


class DashboardSummary(OwnerAPIModel):
    generated_at: dt.datetime
    new_inquiries: int = Field(ge=0)
    outstanding: MoneyCount
    upcoming_projects_14_days: int = Field(ge=0)
    overdue_invoice_count: int = Field(ge=0)
    retainer_draft_count: int = Field(ge=0)
    tasks_due_count: int = Field(ge=0)
    action_item_count: int = Field(ge=0)
    kpis: DashboardKPIs
    open_tasks: list[TaskSummary]
    upcoming_shoots: list[UpcomingProject]
    open_invoices: list[InvoiceSummary]
    recent_activity: list[ActivityItem]

    @field_validator("generated_at")
    @classmethod
    def generated_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value.astimezone(dt.UTC)


class ClientSummary(OwnerAPIModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    name: str = Field(min_length=1, max_length=2000)
    company: str | None = Field(default=None, max_length=2000)
    email: str | None = Field(default=None, max_length=2000)
    phone: str | None = Field(default=None, max_length=2000)
    market: str = Field(min_length=1, max_length=255)
    project_count: int = Field(ge=0)
    portal_published: bool
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        return _ensure_utc(value)


class ProjectSummary(OwnerAPIModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    client_id: int = Field(gt=0, le=_INT64_MAX)
    client_display_name: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=2000)
    status: ProjectStatus
    gallery_id: int | None = Field(default=None, gt=0, le=_INT64_MAX)
    shoot_on: dt.date | None = None
    workspace_published: bool
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        return _ensure_utc(value)


class APIPage[T: BaseModel](OwnerAPIModel):
    items: list[T]
    next_cursor: str | None = None
    has_more: bool


def _ensure_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(dt.UTC)


def _utc_timestamp(value: str | dt.datetime) -> dt.datetime:
    """Interpret SQLite's offset-less ``datetime('now')`` values as UTC."""

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


def _studio_today() -> dt.date:
    """Use the same monkeypatchable wall clock as admin financial decisions."""

    return admin_studio._today()


def require_studio_owner(request: Request) -> mobile_auth.Principal:
    """Require an explicit owner bearer token; browser cookies are never read."""

    principal = mobile_auth.authenticate_request(request, required_scopes=("studio:read",))
    if principal.kind != mobile_auth.STUDIO_OWNER:
        raise mobile_auth.MobileAuthError(
            403,
            "auth.insufficient_scope",
            "This resource requires a studio owner.",
        )
    return principal


router = APIRouter(
    dependencies=[Depends(require_studio_owner)],
    tags=["owner companion"],
)


def _cursor_secret() -> bytes:
    if not config.SECRET_KEY:
        raise RuntimeError("MISE_SECRET_KEY is not set")
    return config.SECRET_KEY.encode("utf-8")


def _encode_cursor(resource: str, last_id: int) -> str:
    payload = f"v1:{resource}:{last_id}".encode("ascii")
    signature = hmac.new(_cursor_secret(), payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def _cursor_problem() -> mobile_auth.MobileAuthError:
    return mobile_auth.MobileAuthError(
        422,
        "pagination.invalid_cursor",
        "The pagination cursor is invalid.",
    )


def _decode_cursor(resource: str, cursor: str | None) -> int | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 512:
        raise _cursor_problem()
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload, signature = decoded[:-16], decoded[-16:]
        expected = hmac.new(_cursor_secret(), payload, hashlib.sha256).digest()[:16]
        version, encoded_resource, raw_id = payload.decode("ascii").split(":", 2)
        last_id = int(raw_id)
    except (UnicodeDecodeError, ValueError, TypeError):
        raise _cursor_problem() from None
    if (
        len(decoded) <= 16
        or not hmac.compare_digest(signature, expected)
        or version != "v1"
        or encoded_resource != resource
        or last_id <= 0
        or last_id > _INT64_MAX
    ):
        raise _cursor_problem()
    return last_id


def _cache_headers(etag: str) -> dict[str, str]:
    return {
        "Cache-Control": "private, no-cache",
        "ETag": etag,
        "Vary": "Authorization",
    }


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False

    def weak_value(value: str) -> str:
        value = value.strip()
        return value[2:] if value.startswith("W/") else value

    expected = weak_value(etag)
    return any(part.strip() == "*" or weak_value(part) == expected for part in header.split(","))


def _conditional(
    request: Request,
    response: Response,
    payload: OwnerAPIModel,
    *,
    exclude: set[str] | None = None,
) -> OwnerAPIModel | Response:
    canonical = payload.model_dump_json(exclude=exclude).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    # Weak is intentional for dashboard: generated_at is excluded so an unchanged
    # semantic snapshot can revalidate even though the observation time advances.
    etag = f'W/"{digest}"'
    headers = _cache_headers(etag)
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    for key, value in headers.items():
        response.headers[key] = value
    return payload


def _dashboard_summary() -> DashboardSummary:
    today = _studio_today()
    today_iso = today.isoformat()
    horizon_iso = (today + dt.timedelta(days=14)).isoformat()

    new_inquiries = int(
        db.one(
            "SELECT COUNT(*) AS n FROM inquiries "
            "WHERE converted_at IS NULL AND dismissed_at IS NULL"
        )["n"]
    )
    outstanding_row = admin_common.open_invoice_balance()
    upcoming_count = int(
        db.one(
            """SELECT COUNT(*) AS n FROM projects
               WHERE status != 'archived' AND shoot_date IS NOT NULL
                 AND shoot_date >= ? AND shoot_date <= ?""",
            (today_iso, horizon_iso),
        )["n"]
    )
    overdue_invoice_count = int(
        db.one(
            """SELECT COUNT(*) AS n FROM invoices
               WHERE status IN ('sent','viewed','deposit_paid')
                 AND due_date IS NOT NULL AND due_date < ?""",
            (today_iso,),
        )["n"]
    )
    retainer_draft_count = int(
        db.one(
            """SELECT COUNT(*) AS n FROM invoices
               WHERE recurring_plan_id IS NOT NULL AND status='draft'"""
        )["n"]
    )
    tasks_due_count = int(
        db.one(
            """SELECT COUNT(*) AS n FROM tasks
               WHERE done=0 AND due_date IS NOT NULL AND due_date <= ?""",
            (today_iso,),
        )["n"]
    )

    inq_7d = int(
        db.one(
            "SELECT COUNT(*) AS n FROM inquiries WHERE created_at >= datetime('now', '-7 days')"
        )["n"]
    )
    inq_previous = int(
        db.one(
            """SELECT COUNT(*) AS n FROM inquiries
               WHERE created_at >= datetime('now', '-14 days')
                 AND created_at < datetime('now', '-7 days')"""
        )["n"]
    )
    bookings_7d = int(
        db.one(
            """SELECT COUNT(*) AS n FROM projects
               WHERE shoot_date IS NOT NULL
                 AND created_at >= datetime('now', '-7 days')"""
        )["n"]
    )
    bookings_previous = int(
        db.one(
            """SELECT COUNT(*) AS n FROM projects
               WHERE shoot_date IS NOT NULL
                 AND created_at >= datetime('now', '-14 days')
                 AND created_at < datetime('now', '-7 days')"""
        )["n"]
    )
    collected_7d = int(
        db.one(
            """SELECT COALESCE(SUM(total_cents), 0) AS cents FROM invoices
               WHERE paid_at >= datetime('now', '-7 days')"""
        )["cents"]
    )

    open_tasks = [
        TaskSummary(
            id=int(row["id"]),
            title=row["title"],
            due_on=_date_only(row["due_date"]),
            project_id=int(row["project_id"]) if row["project_id"] is not None else None,
            project_title=row["project_title"],
            is_overdue=bool(row["overdue"]),
        )
        for row in db.all_(
            """SELECT t.id, t.title, t.due_date, t.project_id,
                      p.title AS project_title,
                      (t.due_date IS NOT NULL AND t.due_date < ?) AS overdue
               FROM tasks t LEFT JOIN projects p ON p.id=t.project_id
               WHERE t.done=0
               ORDER BY (t.due_date IS NULL), t.due_date ASC, t.id DESC
               LIMIT 6""",
            (today_iso,),
        )
    ]
    upcoming_shoots = [
        UpcomingProject(
            id=int(row["id"]),
            title=row["title"],
            client_display_name=row["client_display_name"],
            shoot_on=_date_only(row["shoot_date"]),
            days_out=int(row["days_out"]),
        )
        for row in db.all_(
            """SELECT p.id, p.title, p.shoot_date,
                      COALESCE(NULLIF(c.company, ''), c.name) AS client_display_name,
                      CAST(julianday(p.shoot_date) - julianday(?) AS INTEGER) AS days_out
               FROM projects p JOIN clients c ON c.id=p.client_id
               WHERE p.status != 'archived' AND p.shoot_date IS NOT NULL
                 AND p.shoot_date >= ? AND p.shoot_date <= ?
               ORDER BY p.shoot_date ASC, p.id DESC LIMIT 6""",
            (today_iso, today_iso, horizon_iso),
        )
    ]
    open_invoices = [
        InvoiceSummary(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            title=row["title"],
            client_display_name=row["client_display_name"],
            total=_money(row["total_cents"]),
            balance=_money(
                row["total_cents"] - row["deposit_cents"]
                if row["status"] == "deposit_paid"
                else row["total_cents"]
            ),
            status=InvoiceStatus(row["status"]),
            due_on=_date_only(row["due_date"]),
            is_overdue=bool(row["overdue"]),
        )
        for row in db.all_(
            """SELECT i.id, i.project_id, i.title, i.total_cents,
                      i.deposit_cents, i.status, i.due_date,
                      COALESCE(NULLIF(c.company, ''), c.name) AS client_display_name,
                      (i.due_date IS NOT NULL AND i.due_date < ?) AS overdue
               FROM invoices i
               JOIN projects p ON p.id=i.project_id
               JOIN clients c ON c.id=p.client_id
               WHERE i.status IN ('sent','viewed','deposit_paid')
               ORDER BY (i.due_date IS NULL), i.due_date ASC, i.id DESC LIMIT 6""",
            (today_iso,),
        )
    ]
    recent_activity = [
        ActivityItem(
            id=f"{row['kind']}:{row['source_id']}",
            kind=row["kind"],
            title=row["title"],
            detail=row["detail"],
            occurred_at=_utc_timestamp(row["occurred_at"]),
        )
        for row in db.all_(
            """SELECT 'inquiry' AS kind, i.id AS source_id,
                      i.name AS title, i.business AS detail,
                      i.created_at AS occurred_at
                 FROM inquiries i
                WHERE i.created_at >= datetime('now', '-24 hours')
               UNION ALL
               SELECT 'download', d.id, g.title, v.email, d.created_at
                 FROM downloads d JOIN galleries g ON g.id=d.gallery_id
                 LEFT JOIN visitors v ON v.id=d.visitor_id
                WHERE d.created_at >= datetime('now', '-24 hours')
               UNION ALL
               SELECT 'email', e.id, e.subject, c.name, e.created_at
                 FROM emails_log e
                 LEFT JOIN projects p ON p.id=e.project_id
                 LEFT JOIN clients c ON c.id=p.client_id
                WHERE e.created_at >= datetime('now', '-24 hours')
               ORDER BY occurred_at DESC LIMIT 8"""
        )
    ]

    return DashboardSummary(
        generated_at=dt.datetime.now(dt.UTC),
        new_inquiries=new_inquiries,
        outstanding=MoneyCount(
            count=int(outstanding_row["n"]),
            amount=_money(outstanding_row["cents"]),
        ),
        upcoming_projects_14_days=upcoming_count,
        overdue_invoice_count=overdue_invoice_count,
        retainer_draft_count=retainer_draft_count,
        tasks_due_count=tasks_due_count,
        action_item_count=overdue_invoice_count + retainer_draft_count + tasks_due_count,
        kpis=DashboardKPIs(
            inquiries_delta_7_days=inq_7d - inq_previous,
            bookings_delta_7_days=bookings_7d - bookings_previous,
            collected_7_days=_money(collected_7d),
        ),
        open_tasks=open_tasks,
        upcoming_shoots=upcoming_shoots,
        open_invoices=open_invoices,
        recent_activity=recent_activity,
    )


@router.get("/dashboard", response_model=DashboardSummary)
def dashboard(request: Request, response: Response) -> DashboardSummary | Response:
    summary = _dashboard_summary()
    return _conditional(request, response, summary, exclude={"generated_at"})


def _client_summary(row) -> ClientSummary:
    return ClientSummary(
        id=int(row["id"]),
        name=row["name"],
        company=row["company"],
        email=row["email"],
        phone=row["phone"],
        market=row["market"],
        project_count=int(row["project_count"]),
        portal_published=bool(row["portal_published"]),
        created_at=_utc_timestamp(row["created_at"]),
    )


@router.get("/clients", response_model=APIPage[ClientSummary])
def clients(
    request: Request,
    response: Response,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_SIZE)] = _DEFAULT_PAGE_SIZE,
) -> APIPage[ClientSummary] | Response:
    last_id = _decode_cursor("clients", cursor)
    where = "WHERE c.id < ?" if last_id is not None else ""
    params = (last_id, limit + 1) if last_id is not None else (limit + 1,)
    rows = db.all_(
        f"""SELECT c.id, c.name, c.company, c.email, c.phone, c.market, c.created_at,
                   (SELECT COUNT(*) FROM projects p WHERE p.client_id=c.id)
                     AS project_count,
                   EXISTS(SELECT 1 FROM portals po
                          WHERE po.client_id=c.id AND po.published=1)
                     AS portal_published
              FROM clients c
              {where}
              ORDER BY c.id DESC LIMIT ?""",
        params,
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    payload = APIPage[ClientSummary](
        items=[_client_summary(row) for row in visible],
        next_cursor=(
            _encode_cursor("clients", int(visible[-1]["id"])) if has_more and visible else None
        ),
        has_more=has_more,
    )
    return _conditional(request, response, payload)


def _project_summary(row) -> ProjectSummary:
    return ProjectSummary(
        id=int(row["id"]),
        client_id=int(row["client_id"]),
        client_display_name=row["client_display_name"],
        title=row["title"],
        status=ProjectStatus(row["status"]),
        gallery_id=int(row["gallery_id"]) if row["gallery_id"] is not None else None,
        shoot_on=_date_only(row["shoot_date"]),
        workspace_published=bool(row["workspace_published"]),
        created_at=_utc_timestamp(row["created_at"]),
    )


@router.get("/projects", response_model=APIPage[ProjectSummary])
def projects(
    request: Request,
    response: Response,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_SIZE)] = _DEFAULT_PAGE_SIZE,
) -> APIPage[ProjectSummary] | Response:
    last_id = _decode_cursor("projects", cursor)
    where = "WHERE p.id < ?" if last_id is not None else ""
    params = (last_id, limit + 1) if last_id is not None else (limit + 1,)
    rows = db.all_(
        f"""SELECT p.id, p.client_id, p.title, p.status, p.gallery_id,
                   p.shoot_date, p.workspace_published, p.created_at,
                   COALESCE(NULLIF(c.company, ''), c.name) AS client_display_name
              FROM projects p JOIN clients c ON c.id=p.client_id
              {where}
              ORDER BY p.id DESC LIMIT ?""",
        params,
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    payload = APIPage[ProjectSummary](
        items=[_project_summary(row) for row in visible],
        next_cursor=(
            _encode_cursor("projects", int(visible[-1]["id"])) if has_more and visible else None
        ),
        has_more=has_more,
    )
    return _conditional(request, response, payload)
