"""Read-only owner gallery and calendar resources for the native API.

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

import base64
import datetime as dt
import hashlib
import hmac
import json
import math
import re
from collections.abc import Sequence
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from . import config, db, delivery_gate, mobile_auth, mobile_media
from .admin import studio as admin_studio

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
_PRIVATE_REVALIDATE = "private, no-cache"
_MAX_CURSOR_LENGTH = 1024


class MobileReadModel(BaseModel):
    """Strict, immutable Pydantic 2 wire model for owner read APIs."""

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


def _cursor_problem() -> mobile_auth.MobileAuthError:
    return mobile_auth.MobileAuthError(
        422,
        "pagination.invalid_cursor",
        "The pagination cursor is invalid.",
    )


def _encode_cursor(kind: str, values: Sequence[str | int]) -> str:
    payload = json.dumps(
        {"v": 1, "kind": kind, "values": list(values)},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    if not config.SECRET_KEY:
        raise RuntimeError("MISE_SECRET_KEY is not set")
    signature = hmac.new(
        config.SECRET_KEY.encode(),
        b"mise-mobile-pagination\0" + payload,
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str | None, kind: str, types: Sequence[type]) -> list[str | int] | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
        raise _cursor_problem()
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        if len(raw) <= hashlib.sha256().digest_size or not config.SECRET_KEY:
            raise ValueError("invalid signed cursor")
        payload_bytes = raw[: -hashlib.sha256().digest_size]
        supplied_signature = raw[-hashlib.sha256().digest_size :]
        expected_signature = hmac.new(
            config.SECRET_KEY.encode(),
            b"mise-mobile-pagination\0" + payload_bytes,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("invalid cursor signature")
        payload = json.loads(payload_bytes)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise _cursor_problem() from exc
    if not isinstance(payload, dict) or set(payload) != {"v", "kind", "values"}:
        raise _cursor_problem()
    values = payload["values"]
    if payload["v"] != 1 or payload["kind"] != kind or not isinstance(values, list):
        raise _cursor_problem()
    if len(values) != len(types):
        raise _cursor_problem()
    for value, expected in zip(values, types, strict=True):
        if expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise _cursor_problem()
        elif not isinstance(value, expected):
            raise _cursor_problem()
    return values


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


def _private_headers(etag: str | None = None) -> dict[str, str]:
    headers = {"Cache-Control": _PRIVATE_REVALIDATE, "Vary": "Authorization"}
    if etag is not None:
        headers["ETag"] = etag
    return headers


def _set_private_headers(response: Response, etag: str | None = None) -> None:
    for key, value in _private_headers(etag).items():
        response.headers[key] = value


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    for candidate in header.split(","):
        value = candidate.strip()
        if value == "*":
            return True
        if value.startswith("W/"):
            value = value[2:].strip()
        if value == etag:
            return True
    return False


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
