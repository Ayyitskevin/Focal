"""Owner gallery/calendar resources and bounded booking commands for the native API.

This router is intentionally independent of :mod:`app.mobile_api` so the mounted
API can include it without an import cycle.  Every route requires both the
``studio:read`` scope and the exact ``studio_owner`` principal.  Tenant authority
continues to come exclusively from the request host and the database context
selected by the parent SaaS middleware.

Gallery manifests are safe metadata manifests, not file-serving shortcuts.  They
select no PIN or ``assets.stored`` value, include only ready assets permitted by
the shared cull delivery gate, and point media links at the bearer-authenticated
``/api/v1/media`` routes (:mod:`app.mobile_media`), which re-derive scope and
delivery gates on every byte request.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import re
from collections.abc import Sequence
from typing import Annotated, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
)
from fastapi.security import HTTPBearer
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from . import (
    audit,
    booking_notify,
    booking_workflow,
    config,
    db,
    delivery_gate,
    mobile_auth,
    mobile_idempotency,
    mobile_media,
    scheduler,
    scheduling,
)
from .admin import studio as admin_studio
from .mobile_api_helpers import MAX_CURSOR_LENGTH as _MAX_CURSOR_LENGTH
from .mobile_api_helpers import decode_keyset_cursor as _decode_cursor
from .mobile_api_helpers import encode_keyset_cursor as _encode_cursor
from .mobile_api_helpers import etag_matches as _etag_matches
from .mobile_api_helpers import private_headers as _private_headers
from .mobile_api_helpers import set_private_headers as _set_private_headers
from .mobile_api_schemas import APIProblem

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
_RFC3339_WHOLE_SECOND = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.0+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_BOOKING_RESCHEDULE_COMMAND = "booking.reschedule.v1"
_IDEMPOTENCY_KEY_DOMAIN = b"mise-mobile-idempotency-key-v1\0"
_IDEMPOTENCY_REQUEST_DOMAIN = b"mise-mobile-idempotency-request-v1\0"
_MOBILE_BEARER = HTTPBearer(
    auto_error=False,
    bearerFormat="opaque",
    scheme_name="MobileBearer",
)
log = logging.getLogger("mise.mobile_booking")


def _problem_contract(description: str) -> dict:
    return {
        "description": description,
        "content": {
            "application/problem+json": {
                "schema": {"$ref": f"#/components/schemas/{APIProblem.__name__}"}
            }
        },
    }


_BOOKING_COMMAND_PROBLEM_RESPONSES = {
    401: _problem_contract("Authentication failed"),
    403: _problem_contract("Insufficient scope"),
    404: _problem_contract("Booking not found"),
    409: _problem_contract("Booking or idempotency conflict"),
    422: _problem_contract("Request validation failed"),
    429: _problem_contract("Rate limited"),
    503: _problem_contract("Booking workflow unavailable"),
}


class MobileReadModel(BaseModel):
    """Strict, immutable Pydantic 2 wire model for native API resources."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class APIPage[ItemT: MobileReadModel](MobileReadModel):
    items: list[ItemT] = Field(default_factory=list, max_length=100)
    next_cursor: str | None = Field(default=None, max_length=_MAX_CURSOR_LENGTH)
    has_more: bool


class GallerySummary(MobileReadModel):
    id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    slug: str = Field(min_length=1, max_length=255)
    client_id: int | None = Field(default=None, ge=1)
    project_id: int | None = Field(default=None, ge=1)
    client_name: str | None = Field(default=None, max_length=500)
    type: Literal["gallery", "drop"]
    published: bool
    requires_pin: bool
    content_revision: int = Field(ge=0)
    cover_asset_id: int | None = Field(default=None, ge=1)
    expires_on: dt.date | None = None
    asset_count: int = Field(ge=0)
    favorite_count: int = Field(ge=0)
    download_count: int = Field(ge=0)
    delivery_state: Literal["draft", "proofing", "expiring", "delivered"]
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        return _aware_utc(value)


class GallerySection(MobileReadModel):
    id: int = Field(ge=1)
    gallery_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=500)
    caption: str | None = Field(default=None, max_length=10_000)
    position: int
    proof_target: int | None = Field(default=None, ge=1)
    selected_count: int = Field(ge=0)


class MediaLinks(MobileReadModel):
    thumbnail_url: AnyHttpUrl | None = None
    preview_url: AnyHttpUrl | None = None
    poster_url: AnyHttpUrl | None = None
    download_url: AnyHttpUrl | None = None


class GalleryAsset(MobileReadModel):
    id: int = Field(ge=1)
    gallery_id: int = Field(ge=1)
    section_id: int | None = Field(default=None, ge=1)
    kind: Literal["photo", "video"]
    status: Literal["ready"]
    filename: str = Field(min_length=1, max_length=1000)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    duration_seconds: float | None = Field(default=None, ge=0)
    byte_count: int | None = Field(default=None, ge=0)
    position: int
    created_at: dt.datetime
    is_favorite: bool
    favorite_count: int = Field(ge=0)
    links: MediaLinks
    alt_text: str | None = Field(default=None, max_length=10_000)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    keeper_score: float | None = None
    hero_potential: float | None = None
    cull_state: Literal["keep", "cut"] | None = None

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        return _aware_utc(value)


class GalleryVisionSummary(MobileReadModel):
    status: str = Field(min_length=1, max_length=255)
    run_id: str | None = Field(default=None, max_length=255)
    job_id: str | None = Field(default=None, max_length=255)
    last_run_at: dt.datetime | None = None
    analyzed_asset_count: int | None = Field(default=None, ge=0)
    hero_asset_ids: list[int] = Field(default_factory=list, max_length=1000)
    error: str | None = Field(default=None, max_length=500)

    @field_validator("last_run_at")
    @classmethod
    def last_run_at_is_utc(cls, value: dt.datetime | None) -> dt.datetime | None:
        return _aware_utc(value) if value is not None else None


class GalleryDetail(MobileReadModel):
    summary: GallerySummary
    sections: list[GallerySection] = Field(default_factory=list, max_length=1000)
    assets: list[GalleryAsset] = Field(default_factory=list, max_length=10_000)
    hero_asset_ids: list[int] = Field(default_factory=list, max_length=1000)
    vision: GalleryVisionSummary | None = None

    @model_validator(mode="after")
    def children_belong_to_gallery(self) -> GalleryDetail:
        gallery_id = self.summary.id
        if any(section.gallery_id != gallery_id for section in self.sections):
            raise ValueError("section belongs to another gallery")
        if any(asset.gallery_id != gallery_id for asset in self.assets):
            raise ValueError("asset belongs to another gallery")
        asset_ids = {asset.id for asset in self.assets}
        if len(self.hero_asset_ids) != len(set(self.hero_asset_ids)):
            raise ValueError("hero asset ids must be unique")
        if not set(self.hero_asset_ids).issubset(asset_ids):
            raise ValueError("hero assets must be present in the manifest")
        return self


class EventType(MobileReadModel):
    id: int = Field(ge=1)
    slug: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=500)
    description: str = Field(max_length=10_000)
    duration_minutes: int = Field(ge=1, le=1440)
    location: str = Field(max_length=1000)
    color_hex: str = Field(pattern=r"^#[0-9A-F]{6}$")
    buffer_before_minutes: int = Field(ge=0)
    buffer_after_minutes: int = Field(ge=0)
    minimum_notice_hours: int = Field(ge=0)
    maximum_per_day: int | None = Field(default=None, ge=1)
    booking_window_days: int = Field(ge=1)
    slot_step_minutes: int = Field(ge=1)
    active: bool


class Booking(MobileReadModel):
    id: int = Field(ge=1)
    event_type_id: int = Field(ge=1)
    event_name: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=500)
    email: str = Field(min_length=3, max_length=320)
    phone: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=10_000)
    start_at: dt.datetime
    end_at: dt.datetime
    time_zone: str = Field(min_length=1, max_length=255)
    status: Literal["confirmed", "cancelled"]
    client_id: int | None = Field(default=None, ge=1)
    project_id: int | None = Field(default=None, ge=1)
    rescheduled_from_id: int | None = Field(default=None, ge=1)
    cancel_reason: str | None = Field(default=None, max_length=2000)
    cancelled_at: dt.datetime | None = None
    created_at: dt.datetime

    @field_validator("start_at", "end_at", "cancelled_at", "created_at")
    @classmethod
    def timestamp_is_utc(cls, value: dt.datetime | None) -> dt.datetime | None:
        return _aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def valid_time_range(self) -> Booking:
        if self.end_at <= self.start_at:
            raise ValueError("booking must end after it starts")
        return self


class BookingRescheduleRequest(MobileReadModel):
    start_at: dt.datetime
    time_zone: str = Field(min_length=1, max_length=255)

    @field_validator("start_at", mode="before")
    @classmethod
    def parse_start_at(cls, value: object) -> object:
        # FastAPI supplies an already-decoded Python dict, so the strict model
        # will not apply Pydantic's JSON-only datetime coercion. Parse the one
        # RFC 3339 boundary field explicitly, then keep all internal validation
        # and models strict.
        if not isinstance(value, str):
            return value
        raw = value
        if _RFC3339_WHOLE_SECOND.fullmatch(raw) is None:
            raise ValueError("start_at must be an RFC 3339 timestamp")
        candidate = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
        try:
            return dt.datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ValueError("start_at must be an RFC 3339 timestamp") from exc

    @field_validator("start_at")
    @classmethod
    def start_at_is_utc_whole_second(cls, value: dt.datetime) -> dt.datetime:
        normalized = _aware_utc(value)
        if normalized.microsecond:
            raise ValueError("start_at must use whole-second precision")
        return normalized

    @field_validator("time_zone")
    @classmethod
    def time_zone_is_iana(cls, value: str) -> str:
        cleaned = value.strip()
        try:
            ZoneInfo(cleaned)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("time_zone must be an IANA time zone") from exc
        return cleaned


class BookingRescheduleResult(MobileReadModel):
    status: Literal["rescheduled"]
    workflow_id: UUID
    delivery_status: Literal["pending"]
    original_booking_id: int = Field(ge=1)
    replacement_booking_id: int = Field(ge=1)
    start_at: dt.datetime
    end_at: dt.datetime

    @field_validator("start_at", "end_at")
    @classmethod
    def result_timestamp_is_utc(cls, value: dt.datetime) -> dt.datetime:
        return _aware_utc(value)

    @model_validator(mode="after")
    def valid_transition(self) -> BookingRescheduleResult:
        if self.original_booking_id == self.replacement_booking_id:
            raise ValueError("replacement booking must be distinct")
        if self.end_at <= self.start_at:
            raise ValueError("replacement booking must end after it starts")
        return self


class BookingWorkflowEffect(MobileReadModel):
    kind: Literal[
        "client_cancel_ics",
        "client_request_ics",
        "studio_reschedule_notice",
        "notion_booking_patch",
        "notion_session_link",
        "google_calendar_move",
    ]
    sequence: int = Field(ge=10, le=60)
    status: Literal["pending", "running", "retry", "succeeded", "skipped", "blocked"]
    attempts: int = Field(ge=0)
    next_attempt_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    provider_ref: str | None = Field(default=None, max_length=255)
    error_class: str | None = Field(default=None, max_length=96)
    error_code: str | None = Field(default=None, max_length=96)

    @field_validator("next_attempt_at", "completed_at")
    @classmethod
    def effect_timestamp_is_utc(
        cls,
        value: dt.datetime | None,
    ) -> dt.datetime | None:
        return _aware_utc(value) if value is not None else None


class BookingWorkflowStatus(MobileReadModel):
    workflow_id: UUID
    status: Literal["pending", "running", "retry", "succeeded", "blocked"]
    source_booking_id: int = Field(ge=1)
    replacement_booking_id: int = Field(ge=1)
    effects: list[BookingWorkflowEffect] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def valid_effect_order(self) -> BookingWorkflowStatus:
        kinds = [effect.kind for effect in self.effects]
        sequences = [effect.sequence for effect in self.effects]
        if len(kinds) != len(set(kinds)):
            raise ValueError("workflow effect kinds must be unique")
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("workflow effect sequence must be unique and ordered")
        return self


def require_studio_owner(request: Request) -> mobile_auth.Principal:
    """Authenticate an owner bearer token without browser-cookie fallback."""

    principal = mobile_auth.authenticate_request(request, required_scopes=("studio:read",))
    if principal.kind != mobile_auth.STUDIO_OWNER:
        raise mobile_auth.MobileAuthError(
            403,
            "auth.insufficient_scope",
            "The token lacks this scope.",
        )
    return principal


router = APIRouter(dependencies=[Depends(require_studio_owner)])


def _aware_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include an offset")
    return value.astimezone(dt.UTC)


def _sqlite_utc(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("invalid stored UTC timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _optional_text(value: object, *, maximum: int) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned[:maximum] or None


def _safe_filename(value: object, asset_id: int) -> str:
    # Original client filenames are useful display metadata, but normalize both
    # POSIX and Windows separators so a legacy path can never cross the wire.
    filename = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return filename[:1000] or f"Asset {asset_id}"


def _keywords(value: object) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    result: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()[:200]
        if cleaned and cleaned not in result:
            result.append(cleaned)
        if len(result) == 100:
            break
    return result


def _unit_score(value: object) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) and 0.0 <= score <= 1.0 else None


def _hero_ids(value: object, allowed_asset_ids: set[int]) -> list[int]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    result: list[int] = []
    for item in parsed:
        if isinstance(item, bool):
            continue
        try:
            asset_id = int(item)
        except (TypeError, ValueError):
            continue
        if asset_id in allowed_asset_ids and asset_id not in result:
            result.append(asset_id)
        if len(result) == 1000:
            break
    return result


def _studio_today() -> dt.date:
    return admin_studio._today()


def _delivery_state(row) -> Literal["draft", "proofing", "expiring", "delivered"]:
    if not bool(row["published"]):
        return "draft"
    expiry = dt.date.fromisoformat(row["expires_at"]) if row["expires_at"] else None
    today = _studio_today()
    if expiry is not None and expiry <= today + dt.timedelta(days=7):
        return "expiring"
    if int(row["proof_section_count"]) and int(row["pending_proof_section_count"]):
        return "proofing"
    return "delivered"


def _gallery_query(
    *,
    after: tuple[str, int] | None = None,
    gallery_id: int | None = None,
    gallery_ids: Sequence[int] | None = None,
    row_limit: int | None = None,
):
    gate = delivery_gate.clause("a")
    where: list[str] = []
    params: list[object] = []
    if gallery_id is not None:
        where.append("g.id=?")
        params.append(gallery_id)
    if gallery_ids is not None:
        if not gallery_ids:
            where.append("0")
        else:
            placeholders = ",".join("?" * len(gallery_ids))
            where.append(f"g.id IN ({placeholders})")
            params.extend(int(gid) for gid in gallery_ids)
    if after is not None:
        where.append("(g.created_at < ? OR (g.created_at = ? AND g.id < ?))")
        params.extend((after[0], after[0], after[1]))
    predicate = f"WHERE {' AND '.join(where)}" if where else ""
    limit_clause = ""
    if row_limit is not None:
        if not 1 <= row_limit <= 101:
            raise ValueError("gallery row limit is outside the API page bound")
        limit_clause = "LIMIT ?"
        params.append(row_limit)
    sql = f"""SELECT g.id, g.slug, g.title, g.client_id, g.project_id,
                     COALESCE(NULLIF(g.client_name, ''), c.name) AS resolved_client_name,
                     g.type, g.require_pin, g.published, g.content_rev,
                     CASE WHEN EXISTS (
                       SELECT 1 FROM assets a WHERE a.id=g.cover_asset_id
                         AND a.gallery_id=g.id AND a.status='ready'{gate}
                     ) THEN g.cover_asset_id END AS safe_cover_asset_id,
                     g.expires_at, g.created_at,
                     g.argus_last_run_id, g.argus_last_job_id, g.argus_last_status,
                     g.argus_last_at, g.argus_analyzed_count, g.argus_hero_asset_ids,
                     g.argus_last_error,
                     (SELECT COUNT(*) FROM assets a
                       WHERE a.gallery_id=g.id AND a.status='ready'{gate}) AS asset_count,
                     (SELECT COUNT(*) FROM favorites f
                       JOIN assets a ON a.id=f.asset_id
                       WHERE a.gallery_id=g.id AND a.status='ready'{gate}) AS favorite_count,
                     (SELECT COUNT(*) FROM downloads d
                       WHERE d.gallery_id=g.id) AS download_count,
                     (SELECT COUNT(*) FROM sections s
                       WHERE s.gallery_id=g.id AND s.proof_target > 0) AS proof_section_count,
                     (SELECT COUNT(*) FROM sections s
                       WHERE s.gallery_id=g.id AND s.proof_target > 0
                         AND (SELECT COUNT(DISTINCT f.asset_id)
                              FROM favorites f JOIN assets a ON a.id=f.asset_id
                              WHERE a.gallery_id=g.id AND a.section_id=s.id
                                AND a.status='ready'{gate}) < s.proof_target
                     ) AS pending_proof_section_count
              FROM galleries g LEFT JOIN clients c ON c.id=g.client_id
              {predicate}
              ORDER BY g.created_at DESC, g.id DESC
              {limit_clause}"""
    return db.all_(sql, tuple(params))


def _gallery_summary(row) -> GallerySummary:
    return GallerySummary(
        id=int(row["id"]),
        title=str(row["title"]).strip()[:500] or f"Gallery {row['id']}",
        slug=str(row["slug"]),
        client_id=int(row["client_id"]) if row["client_id"] is not None else None,
        project_id=int(row["project_id"]) if row["project_id"] is not None else None,
        client_name=_optional_text(row["resolved_client_name"], maximum=500),
        type="drop" if row["type"] == "drop" else "gallery",
        published=bool(row["published"]),
        requires_pin=bool(row["require_pin"]),
        content_revision=max(0, int(row["content_rev"] or 0)),
        cover_asset_id=(
            int(row["safe_cover_asset_id"]) if row["safe_cover_asset_id"] is not None else None
        ),
        expires_on=dt.date.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        asset_count=max(0, int(row["asset_count"])),
        favorite_count=max(0, int(row["favorite_count"])),
        download_count=max(0, int(row["download_count"])),
        delivery_state=_delivery_state(row),
        created_at=_sqlite_utc(row["created_at"]),
    )


def _gallery_sections(gallery_id: int) -> list[GallerySection]:
    gate = delivery_gate.clause("a")
    rows = db.all_(
        f"""SELECT s.id, s.gallery_id, s.name, s.caption, s.position, s.proof_target,
                    (SELECT COUNT(DISTINCT f.asset_id)
                       FROM favorites f JOIN assets a ON a.id=f.asset_id
                       WHERE a.gallery_id=? AND a.section_id=s.id
                         AND a.status='ready'{gate}) AS selected_count
              FROM sections s WHERE s.gallery_id=?
              ORDER BY s.position, s.id""",
        (gallery_id, gallery_id),
    )
    return [
        GallerySection(
            id=int(row["id"]),
            gallery_id=int(row["gallery_id"]),
            name=str(row["name"]).strip()[:500] or f"Section {row['id']}",
            caption=_optional_text(row["caption"], maximum=10_000),
            position=int(row["position"]),
            proof_target=(
                int(row["proof_target"])
                if row["proof_target"] is not None and int(row["proof_target"]) > 0
                else None
            ),
            selected_count=max(0, int(row["selected_count"])),
        )
        for row in rows
    ]


def _gallery_assets(gallery_id: int, request: Request) -> list[GalleryAsset]:
    gate = delivery_gate.clause("a")
    rows = db.all_(
        f"""SELECT a.id, a.gallery_id, a.section_id, a.kind, a.status,
                    a.filename, a.width, a.height, a.duration, a.bytes,
                    a.position, a.created_at, a.argus_alt_text, a.argus_keywords,
                    a.argus_keeper_score, a.argus_hero_potential, a.cull_state,
                    COUNT(f.visitor_id) AS favorite_count
              FROM assets a LEFT JOIN favorites f ON f.asset_id=a.id
              WHERE a.gallery_id=? AND a.status='ready'{gate}
              GROUP BY a.id
              ORDER BY a.section_id IS NULL, a.section_id, a.position, a.id""",
        (gallery_id,),
    )
    assets: list[GalleryAsset] = []
    for row in rows:
        favorite_count = max(0, int(row["favorite_count"]))
        asset_id = int(row["id"])
        kind = "video" if row["kind"] == "video" else "photo"
        links = MediaLinks(**mobile_media.build_media_links(request, gallery_id, asset_id, kind))
        assets.append(
            GalleryAsset(
                id=asset_id,
                gallery_id=int(row["gallery_id"]),
                section_id=int(row["section_id"]) if row["section_id"] is not None else None,
                kind=kind,
                status="ready",
                filename=_safe_filename(row["filename"], asset_id),
                width=int(row["width"]) if row["width"] and int(row["width"]) > 0 else None,
                height=(int(row["height"]) if row["height"] and int(row["height"]) > 0 else None),
                duration_seconds=(
                    max(0.0, float(row["duration"])) if row["duration"] is not None else None
                ),
                byte_count=max(0, int(row["bytes"])) if row["bytes"] is not None else None,
                position=int(row["position"]),
                created_at=_sqlite_utc(row["created_at"]),
                is_favorite=favorite_count > 0,
                favorite_count=favorite_count,
                links=links,
                alt_text=_optional_text(row["argus_alt_text"], maximum=10_000),
                keywords=_keywords(row["argus_keywords"]),
                keeper_score=_unit_score(row["argus_keeper_score"]),
                hero_potential=_unit_score(row["argus_hero_potential"]),
                cull_state=(row["cull_state"] if row["cull_state"] in {"keep", "cut"} else None),
            )
        )
    return assets


def _vision(row, hero_asset_ids: list[int]) -> GalleryVisionSummary | None:
    status = _optional_text(row["argus_last_status"], maximum=255)
    if status is None:
        return None
    raw_job_id = _optional_text(row["argus_last_job_id"], maximum=255)
    safe_job_id = raw_job_id if raw_job_id and _SAFE_JOB_ID.fullmatch(raw_job_id) else None
    has_error = bool(_optional_text(row["argus_last_error"], maximum=1))
    return GalleryVisionSummary(
        status=status,
        run_id=str(row["argus_last_run_id"]) if row["argus_last_run_id"] is not None else None,
        job_id=safe_job_id,
        last_run_at=_sqlite_utc(row["argus_last_at"]),
        analyzed_asset_count=(
            max(0, int(row["argus_analyzed_count"]))
            if row["argus_analyzed_count"] is not None
            else None
        ),
        hero_asset_ids=hero_asset_ids,
        # Provider errors can contain local paths or upstream response fragments.
        error="Analysis failed." if has_error else None,
    )


def _collection_response[PageT: MobileReadModel](
    request: Request,
    response: Response,
    page: PageT,
    *,
    resource: str,
) -> PageT | Response:
    digest = hashlib.sha256(page.model_dump_json().encode()).hexdigest()
    etag = f'"{resource}-{digest[:32]}"'
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=_private_headers(etag))
    _set_private_headers(response, etag)
    return page


@router.get("/galleries", response_model=APIPage[GallerySummary], tags=["galleries"])
def list_galleries(
    request: Request,
    response: Response,
    cursor: Annotated[str | None, Query(max_length=_MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> APIPage[GallerySummary]:
    decoded = _decode_cursor(cursor, "galleries", (str, int))
    after = (str(decoded[0]), int(decoded[1])) if decoded is not None else None
    page_rows = _gallery_query(after=after, row_limit=limit + 1)
    has_more = len(page_rows) > limit
    page_rows = page_rows[:limit]
    items = [_gallery_summary(row) for row in page_rows]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = _encode_cursor("galleries", (str(last["created_at"]), int(last["id"])))
    page = APIPage[GallerySummary](items=items, next_cursor=next_cursor, has_more=has_more)
    return _collection_response(request, response, page, resource="galleries")


@router.get("/galleries/{gallery_id}", response_model=GalleryDetail, tags=["galleries"])
def gallery_detail(
    request: Request,
    response: Response,
    gallery_id: Annotated[int, Path(ge=1)],
) -> GalleryDetail | Response:
    rows = _gallery_query(gallery_id=gallery_id, row_limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="Gallery not found.")
    row = rows[0]
    assets = _gallery_assets(gallery_id, request)
    asset_ids = {asset.id for asset in assets}
    hero_asset_ids = _hero_ids(row["argus_hero_asset_ids"], asset_ids)
    detail = GalleryDetail(
        summary=_gallery_summary(row),
        sections=_gallery_sections(gallery_id),
        assets=assets,
        hero_asset_ids=hero_asset_ids,
        vision=_vision(row, hero_asset_ids),
    )
    digest = hashlib.sha256(detail.model_dump_json().encode()).hexdigest()
    etag = f'"gallery-{digest[:32]}"'
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=_private_headers(etag))
    _set_private_headers(response, etag)
    return detail


@router.get("/event-types", response_model=APIPage[EventType], tags=["scheduling"])
def list_event_types(
    request: Request,
    response: Response,
    cursor: Annotated[str | None, Query(max_length=_MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> APIPage[EventType]:
    decoded = _decode_cursor(cursor, "event-types", (int, int))
    params: tuple[object, ...] = ()
    predicate = ""
    if decoded is not None:
        position, event_id = int(decoded[0]), int(decoded[1])
        predicate = "WHERE position > ? OR (position = ? AND id > ?)"
        params = (position, position, event_id)
    rows = db.all_(
        f"""SELECT id, slug, name, description, duration_min, location, color,
                    buffer_before_min, buffer_after_min, min_notice_hours,
                    max_per_day, booking_window_days, slot_step_min, active, position
              FROM event_types {predicate}
              ORDER BY position, id LIMIT ?""",
        (*params, limit + 1),
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = []
    for row in page_rows:
        color = str(row["color"] or "").upper()
        if not _HEX_COLOR.fullmatch(color):
            color = "#B3552E"
        duration = max(1, min(1440, int(row["duration_min"])))
        items.append(
            EventType(
                id=int(row["id"]),
                slug=str(row["slug"]),
                name=str(row["name"]).strip()[:500] or f"Event {row['id']}",
                description=str(row["description"] or "")[:10_000],
                duration_minutes=duration,
                location=str(row["location"] or "")[:1000],
                color_hex=color,
                buffer_before_minutes=max(0, int(row["buffer_before_min"])),
                buffer_after_minutes=max(0, int(row["buffer_after_min"])),
                minimum_notice_hours=max(0, int(row["min_notice_hours"])),
                maximum_per_day=(
                    int(row["max_per_day"]) if int(row["max_per_day"] or 0) > 0 else None
                ),
                booking_window_days=max(1, int(row["booking_window_days"])),
                slot_step_minutes=max(1, int(row["slot_step_min"] or duration)),
                active=bool(row["active"]),
            )
        )
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = _encode_cursor("event-types", (int(last["position"]), int(last["id"])))
    page = APIPage[EventType](items=items, next_cursor=next_cursor, has_more=has_more)
    return _collection_response(request, response, page, resource="event-types")


def _booking_from_row(row) -> Booking:
    return Booking(
        id=int(row["id"]),
        event_type_id=int(row["event_type_id"]),
        event_name=str(row["event_name"]).strip()[:500] or "Booking",
        name=str(row["name"]).strip()[:500] or "Client",
        email=str(row["email"]).strip()[:320],
        phone=_optional_text(row["phone"], maximum=100),
        notes=_optional_text(row["notes"], maximum=10_000),
        start_at=_sqlite_utc(row["start_utc"]),
        end_at=_sqlite_utc(row["end_utc"]),
        time_zone=_optional_text(row["tz"], maximum=255) or config.TIMEZONE,
        status="confirmed" if row["status"] == "confirmed" else "cancelled",
        client_id=int(row["client_id"]) if row["client_id"] is not None else None,
        project_id=int(row["project_id"]) if row["project_id"] is not None else None,
        rescheduled_from_id=(
            int(row["reschedule_of"]) if row["reschedule_of"] is not None else None
        ),
        cancel_reason=_optional_text(row["cancel_reason"], maximum=2000),
        cancelled_at=_sqlite_utc(row["cancelled_at"]),
        created_at=_sqlite_utc(row["created_at"]),
    )


@router.get("/bookings", response_model=APIPage[Booking], tags=["scheduling"])
def list_bookings(
    request: Request,
    response: Response,
    cursor: Annotated[str | None, Query(max_length=_MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> APIPage[Booking]:
    decoded = _decode_cursor(cursor, "bookings", (str, int))
    cursor_predicate = ""
    params: list[object] = []
    if decoded is not None:
        start_utc, booking_id = str(decoded[0]), int(decoded[1])
        cursor_predicate = "AND (b.start_utc > ? OR (b.start_utc = ? AND b.id > ?))"
        params.extend((start_utc, start_utc, booking_id))
    rows = db.all_(
        f"""SELECT b.id, b.event_type_id, e.name AS event_name, b.name,
                    b.email, b.phone, b.notes, b.start_utc, b.end_utc, b.tz,
                    b.status, b.client_id, b.project_id, b.reschedule_of,
                    b.cancel_reason, b.cancelled_at, b.created_at
              FROM bookings b JOIN event_types e ON e.id=b.event_type_id
              WHERE b.status='confirmed' AND b.start_utc >= datetime('now')
                {cursor_predicate}
              ORDER BY b.start_utc, b.id LIMIT ?""",
        (*params, limit + 1),
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = [_booking_from_row(row) for row in page_rows]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = _encode_cursor("bookings", (str(last["start_utc"]), int(last["id"])))
    page = APIPage[Booking](items=items, next_cursor=next_cursor, has_more=has_more)
    return _collection_response(request, response, page, resource="bookings")


# One booking row, shaped for _booking_from_row (same columns as the list read).
_BOOKING_BY_ID = """SELECT b.id, b.event_type_id, e.name AS event_name, b.name,
                           b.email, b.phone, b.notes, b.start_utc, b.end_utc, b.tz,
                           b.status, b.client_id, b.project_id, b.reschedule_of,
                           b.cancel_reason, b.cancelled_at, b.created_at
                      FROM bookings b JOIN event_types e ON e.id=b.event_type_id
                      WHERE b.id=?"""

_RESCHEDULE_SOURCE_BY_ID = """SELECT id, event_type_id, name, email, phone, notes,
                                     start_utc, tz, status, client_id, project_id,
                                     venue_address, dish_count, parking_notes,
                                     style_refs, onsite_contact, inquiry_id,
                                     google_event_id, notion_page_id,
                                     notion_session_id
                                FROM bookings WHERE id=?"""


def _require_studio_write(principal: mobile_auth.Principal) -> None:
    """A mutation needs studio:write; the router already proved studio:read + owner."""
    if "studio:write" not in principal.scopes:
        raise mobile_auth.MobileAuthError(
            403,
            "auth.insufficient_scope",
            "This action requires studio write access.",
        )


def _booking_reschedule_key_hash(key: str) -> str:
    return hashlib.sha256(_IDEMPOTENCY_KEY_DOMAIN + key.encode("ascii")).hexdigest()


def _booking_reschedule_request_hash(
    booking_id: int,
    *,
    start_utc: str,
    time_zone: str,
) -> str:
    canonical = json.dumps(
        {
            "booking_id": booking_id,
            "command": _BOOKING_RESCHEDULE_COMMAND,
            "start_at": start_utc,
            "time_zone": time_zone,
            "v": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(_IDEMPOTENCY_REQUEST_DOMAIN + canonical).hexdigest()


def _rollback_quietly(con) -> None:
    try:
        con.execute("ROLLBACK")
    except Exception:
        pass


def _booking_workflow_status(workflow_id: UUID) -> BookingWorkflowStatus:
    summary = booking_workflow.summary(str(workflow_id))
    if summary is None:
        raise mobile_auth.MobileAuthError(
            404,
            "booking.workflow_not_found",
            "Booking workflow not found.",
        )
    effects = [
        {
            **effect,
            "next_attempt_at": (
                dt.datetime.fromtimestamp(effect["next_attempt_at"], tz=dt.UTC)
                if effect["next_attempt_at"] is not None
                else None
            ),
            "completed_at": (
                dt.datetime.fromtimestamp(effect["completed_at"], tz=dt.UTC)
                if effect["completed_at"] is not None
                else None
            ),
        }
        for effect in summary["effects"]
    ]
    return BookingWorkflowStatus.model_validate(
        {**summary, "workflow_id": workflow_id, "effects": effects}
    )


def _retry_booking_workflow(
    workflow_id: UUID,
    *,
    principal: mobile_auth.Principal,
) -> int:
    """Authorize, reset, and audit a workflow retry under one writer lock."""
    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        # The writer lock may have waited behind a concurrent session revocation.
        # Re-read both authorization state and time only after that wait.
        now_ts = mobile_idempotency.now_ts()
        session = con.execute(
            """SELECT tenant_key, principal_kind, absolute_expires_at, revoked_at
                 FROM api_sessions WHERE id=?""",
            (principal.session_id,),
        ).fetchone()
        if (
            session is None
            or session["tenant_key"] != principal.tenant_key
            or session["principal_kind"] != principal.kind
            or session["principal_kind"] != mobile_auth.STUDIO_OWNER
            or session["revoked_at"] is not None
            or int(session["absolute_expires_at"]) <= now_ts
        ):
            raise mobile_auth.MobileAuthError(
                401,
                "auth.invalid_token",
                "The token is invalid or expired.",
            )

        if not booking_workflow.available():
            raise mobile_auth.MobileAuthError(
                503,
                "booking.reschedule_unavailable",
                "Booking reschedule delivery is not configured.",
                retry_after=max(1, config.BOOKING_WORKFLOW_POLL_SECONDS),
            )

        workflow = con.execute(
            """SELECT replacement_booking_id
                 FROM booking_workflow_effects
                WHERE workflow_id=?
                ORDER BY sequence_no LIMIT 1""",
            (str(workflow_id),),
        ).fetchone()
        if workflow is None:
            raise mobile_auth.MobileAuthError(
                404,
                "booking.workflow_not_found",
                "Booking workflow not found.",
            )

        effect_count = booking_workflow.retry_in_transaction(con, str(workflow_id))
        if effect_count == 0:
            raise mobile_auth.MobileAuthError(
                409,
                "booking.workflow_not_retryable",
                "This booking workflow has no blocked effects to retry.",
            )
        audit.log(
            con,
            "booking",
            int(workflow["replacement_booking_id"]),
            "workflow_retry",
            diff={
                "effect_count": effect_count,
                "session_id": principal.session_id,
                "workflow_id": str(workflow_id),
            },
            actor="owner",
        )
        con.execute("COMMIT")
        return effect_count
    except Exception:
        _rollback_quietly(con)
        raise
    finally:
        con.close()


def _reschedule_booking(
    booking_id: int,
    *,
    principal: mobile_auth.Principal,
    idempotency_key: str,
    body: BookingRescheduleRequest,
) -> tuple[BookingRescheduleResult, bool]:
    start_utc = body.start_at.strftime("%Y-%m-%d %H:%M:%S")
    key_hash = _booking_reschedule_key_hash(idempotency_key)
    request_hash = _booking_reschedule_request_hash(
        booking_id,
        start_utc=start_utc,
        time_zone=body.time_zone,
    )
    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        # Read the clock only after the potentially-blocking writer lock. A
        # session that expires while waiting must not authorize the command.
        now_ts = mobile_idempotency.now_ts()

        # Authentication happened before this transaction. Recheck the stable
        # session row after acquiring the writer lock so a concurrent revocation
        # cannot race a consequential command into the database.
        session = con.execute(
            """SELECT tenant_key, principal_kind, absolute_expires_at, revoked_at
                 FROM api_sessions WHERE id=?""",
            (principal.session_id,),
        ).fetchone()
        if (
            session is None
            or session["tenant_key"] != principal.tenant_key
            or session["principal_kind"] != mobile_auth.STUDIO_OWNER
            or session["revoked_at"] is not None
            or int(session["absolute_expires_at"]) <= now_ts
        ):
            raise mobile_auth.MobileAuthError(
                401,
                "auth.invalid_token",
                "The token is invalid or expired.",
            )

        mobile_idempotency.prune_expired_in_transaction(con, cutoff=now_ts)
        replay = con.execute(
            """SELECT command_kind, request_hash, response_status, response_json
                 FROM api_idempotency_replays
                WHERE session_id=? AND key_hash=?""",
            (principal.session_id, key_hash),
        ).fetchone()
        if replay is not None:
            if (
                replay["command_kind"] != _BOOKING_RESCHEDULE_COMMAND
                or replay["request_hash"] != request_hash
            ):
                raise mobile_auth.MobileAuthError(
                    409,
                    "idempotency.key_conflict",
                    "This idempotency key was already used for another request.",
                )
            if int(replay["response_status"]) != 200:
                raise RuntimeError("unsupported booking reschedule replay status")
            result = BookingRescheduleResult.model_validate_json(replay["response_json"])
            con.execute("COMMIT")
            return result, False

        if not booking_workflow.available():
            raise mobile_auth.MobileAuthError(
                503,
                "booking.reschedule_unavailable",
                "Booking reschedule delivery is not configured.",
                retry_after=max(1, config.BOOKING_WORKFLOW_POLL_SECONDS),
            )

        workflow_id = uuid4()

        source = con.execute(_RESCHEDULE_SOURCE_BY_ID, (booking_id,)).fetchone()
        if source is None:
            raise HTTPException(status_code=404, detail="Booking not found.")
        if source["status"] != "confirmed":
            raise mobile_auth.MobileAuthError(
                409,
                "booking.not_reschedulable",
                "Only a confirmed booking can be rescheduled.",
            )
        if source["start_utc"] == start_utc:
            raise mobile_auth.MobileAuthError(
                409,
                "booking.unchanged",
                "Choose a different time for the replacement booking.",
            )

        event_type = con.execute(
            "SELECT * FROM event_types WHERE id=? AND active=1",
            (source["event_type_id"],),
        ).fetchone()
        if event_type is None:
            raise mobile_auth.MobileAuthError(
                409,
                "booking.event_unavailable",
                "This booking type is no longer available for rescheduling.",
            )

        replacement_id, _ = scheduling.book_in_transaction(
            con,
            event_type,
            start_utc,
            source["name"],
            source["email"],
            source["phone"],
            source["notes"],
            body.time_zone,
            booking_id,
        )
        con.execute(
            """UPDATE bookings
                  SET client_id=?, project_id=?, venue_address=?, dish_count=?,
                      parking_notes=?, style_refs=?, onsite_contact=?,
                      inquiry_id=?, google_event_id=?, notion_page_id=?,
                      notion_session_id=?
                WHERE id=?""",
            (
                source["client_id"],
                source["project_id"],
                source["venue_address"],
                source["dish_count"],
                source["parking_notes"],
                source["style_refs"],
                source["onsite_contact"],
                source["inquiry_id"],
                source["google_event_id"],
                source["notion_page_id"],
                source["notion_session_id"],
                replacement_id,
            ),
        )
        if source["inquiry_id"] is not None:
            con.execute(
                """UPDATE inquiries
                      SET shoot_date=?, service=?, message=?
                    WHERE id=?""",
                (
                    start_utc[:10],
                    event_type["name"],
                    (
                        f"Rescheduled {event_type['name']} for {start_utc[:10]}."
                        f"\n\n{source['notes']}"
                    ),
                    source["inquiry_id"],
                ),
            )
        if source["project_id"] is not None:
            con.execute(
                "UPDATE projects SET shoot_date=? WHERE id=?",
                (start_utc[:10], source["project_id"]),
            )
        if not scheduling.cancel_in_transaction(
            con,
            booking_id,
            "Rescheduled from the studio app",
        ):
            raise RuntimeError("booking reschedule lost source transition")
        con.execute(
            """UPDATE bookings
                  SET inquiry_id=NULL,
                      google_event_id=NULL,
                      notion_page_id=NULL
                WHERE id=?""",
            (booking_id,),
        )

        audit.log(
            con,
            "booking",
            booking_id,
            "reschedule",
            diff={
                "replacement_booking_id": replacement_id,
                "session_id": principal.session_id,
                "workflow_id": str(workflow_id),
                "start_utc": [source["start_utc"], start_utc],
                "status": ["confirmed", "cancelled"],
            },
            actor="owner",
        )
        audit.log(
            con,
            "booking",
            replacement_id,
            "reschedule_create",
            diff={
                "session_id": principal.session_id,
                "source_booking_id": booking_id,
                "workflow_id": str(workflow_id),
                "start_utc": start_utc,
                "status": [None, "confirmed"],
            },
            actor="owner",
        )

        replacement = con.execute(
            "SELECT id, start_utc, end_utc FROM bookings WHERE id=?",
            (replacement_id,),
        ).fetchone()
        if replacement is None:
            raise RuntimeError("booking reschedule replacement vanished")
        result = BookingRescheduleResult(
            status="rescheduled",
            workflow_id=workflow_id,
            delivery_status="pending",
            original_booking_id=booking_id,
            replacement_booking_id=int(replacement["id"]),
            start_at=_sqlite_utc(replacement["start_utc"]),
            end_at=_sqlite_utc(replacement["end_utc"]),
        )
        booking_workflow.enqueue_reschedule(
            con,
            source_booking_id=booking_id,
            replacement_booking_id=replacement_id,
            workflow_id=str(workflow_id),
        )
        response_json = result.model_dump_json()
        con.execute(
            """INSERT INTO api_idempotency_replays
               (session_id, key_hash, command_kind, request_hash,
                response_status, response_json, created_at, expires_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                principal.session_id,
                key_hash,
                _BOOKING_RESCHEDULE_COMMAND,
                request_hash,
                200,
                response_json,
                now_ts,
                int(session["absolute_expires_at"]),
            ),
        )
        con.execute("COMMIT")
        return result, True
    except scheduling.SlotTaken as exc:
        _rollback_quietly(con)
        raise mobile_auth.MobileAuthError(
            409,
            "booking.slot_unavailable",
            "That time is no longer available.",
        ) from exc
    except booking_workflow.WorkflowBusy as exc:
        _rollback_quietly(con)
        raise mobile_auth.MobileAuthError(
            409,
            "booking.workflow_in_progress",
            "Booking delivery is still in progress. Try again shortly.",
            retry_after=max(1, config.BOOKING_WORKFLOW_POLL_SECONDS),
        ) from exc
    except Exception:
        _rollback_quietly(con)
        raise
    finally:
        con.close()


@router.post("/bookings/{booking_id}/cancel", response_model=Booking, tags=["scheduling"])
def cancel_booking(
    booking_id: Annotated[int, Path(ge=1)],
    principal: Annotated[mobile_auth.Principal, Depends(require_studio_owner)],
    response: Response,
) -> Booking:
    """Owner-authoritative booking cancel (M4a).

    The canonical transactional cancellation guard also supersedes queued
    replacement effects. A repeat call remains a no-op with no second notice or
    audit row; running provider work returns a retryable conflict.
    """
    _require_studio_write(principal)
    transitioned = False
    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT status FROM bookings WHERE id=?",
            (booking_id,),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Booking not found.")
        if existing["status"] == "confirmed":
            if not scheduling.cancel_in_transaction(
                con,
                booking_id,
                "Cancelled from the studio app",
            ):
                raise RuntimeError("booking cancel lost confirmed transition")
            audit.log(
                con,
                "booking",
                booking_id,
                "cancel",
                diff={"status": ["confirmed", "cancelled"]},
                actor="owner",
            )
            transitioned = True
        con.execute("COMMIT")
    except booking_workflow.WorkflowBusy as exc:
        _rollback_quietly(con)
        raise mobile_auth.MobileAuthError(
            409,
            "booking.workflow_in_progress",
            "Booking delivery is still in progress. Try again shortly.",
            retry_after=max(1, config.BOOKING_WORKFLOW_POLL_SECONDS),
        ) from exc
    except Exception:
        _rollback_quietly(con)
        raise
    finally:
        con.close()
    if transitioned:
        booking_notify.cancelled(booking_id, by_admin=True)
    _set_private_headers(response)
    return _booking_from_row(db.one(_BOOKING_BY_ID, (booking_id,)))


@router.post(
    "/bookings/{booking_id}/reschedule",
    response_model=BookingRescheduleResult,
    responses=_BOOKING_COMMAND_PROBLEM_RESPONSES,
    dependencies=[Depends(_MOBILE_BEARER)],
    tags=["scheduling"],
)
def reschedule_booking(
    booking_id: Annotated[int, Path(ge=1)],
    body: BookingRescheduleRequest,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    principal: Annotated[mobile_auth.Principal, Depends(require_studio_owner)],
    response: Response,
) -> BookingRescheduleResult:
    """Atomically replace one confirmed owner booking at a server-valid slot.

    The replay receipt, replacement, source cancellation, audit rows, and durable
    delivery workflow share one immediate transaction. A successful response means
    the booking changed and delivery was queued; it does not claim provider delivery.
    """
    _require_studio_write(principal)
    result, created = _reschedule_booking(
        booking_id,
        principal=principal,
        idempotency_key=str(idempotency_key),
        body=body,
    )
    if created:
        try:
            scheduler.wake_booking_workflows()
        except Exception as exc:
            log.error(
                "booking reschedule workflow wake failed: workflow=%s type=%s",
                result.workflow_id,
                type(exc).__name__,
            )
    _set_private_headers(response)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "/booking-workflows/{workflow_id}",
    response_model=BookingWorkflowStatus,
    dependencies=[Depends(_MOBILE_BEARER)],
    tags=["scheduling"],
)
def booking_workflow_status(
    workflow_id: Annotated[UUID, Path()],
    response: Response,
) -> BookingWorkflowStatus:
    """Return bounded, contact-free delivery state for one tenant workflow."""
    result = _booking_workflow_status(workflow_id)
    _set_private_headers(response)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post(
    "/booking-workflows/{workflow_id}/retry",
    response_model=BookingWorkflowStatus,
    responses=_BOOKING_COMMAND_PROBLEM_RESPONSES,
    dependencies=[Depends(_MOBILE_BEARER)],
    tags=["scheduling"],
)
def retry_booking_workflow(
    workflow_id: Annotated[UUID, Path()],
    principal: Annotated[mobile_auth.Principal, Depends(require_studio_owner)],
    response: Response,
) -> BookingWorkflowStatus:
    """Reset only terminal blocked effects, then wake this durable workflow."""
    _require_studio_write(principal)
    _retry_booking_workflow(workflow_id, principal=principal)
    try:
        scheduler.wake_booking_workflows()
    except Exception as exc:
        log.error(
            "booking workflow retry wake failed: workflow=%s type=%s",
            workflow_id,
            type(exc).__name__,
        )
    result = _booking_workflow_status(workflow_id)
    _set_private_headers(response)
    response.headers["Cache-Control"] = "no-store"
    return result
