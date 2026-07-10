"""Capability-bound native gallery delivery for scoped client sessions.

The browser gallery uses visitor cookies and slug-bearing URLs.  Native clients
instead receive an opaque bearer session whose principal is bound to one gallery
and one visitor.  This router never accepts a gallery id, slug, PIN, token, or
tenant selector from the request: authority comes only from that principal and
the host-selected tenant database.

Every asset query repeats the live-gallery, ready-file, section-parent, and cull
delivery gates.  Media paths are derived from trusted database ids and a single
safe stored basename, resolved below the tenant's media root, and never cross a
JSON boundary.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import sqlite3
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import config, db, delivery_gate, jobs, mobile_auth, reopen_notify, urls
from .admin import studio as admin_studio
from .mobile_gallery_calendar_api import (
    GalleryAsset,
    GalleryDetail,
    GallerySection,
    GallerySummary,
    MediaLinks,
    _hero_ids,
    _keywords,
    _optional_text,
    _safe_filename,
    _sqlite_utc,
)
from .public import gallery as public_gallery

log = logging.getLogger("mise.mobile_gallery_delivery_api")
router = APIRouter()

_PRIVATE_REVALIDATE = "private, no-cache"
_PRIVATE_DERIVATIVE = "private, max-age=86400"
_MAX_SECTIONS = 1_000
_MAX_ASSETS = 10_000
_MAX_COMMENTS = 500
_MAX_COMMENT_BODY = 4_000
_MAX_TIMECODE_SECONDS = 604_800.0


class MobileDeliveryModel(BaseModel):
    """Strict, immutable DTO base for native gallery mutations and comments."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FavoriteState(MobileDeliveryModel):
    asset_id: int = Field(ge=1)
    selected: bool
    section_selected_count: int | None = Field(default=None, ge=0)
    section_proof_target: int | None = Field(default=None, ge=1)


class VideoComment(MobileDeliveryModel):
    id: int = Field(ge=1)
    asset_id: int = Field(ge=1)
    parent_id: int | None = Field(default=None, ge=1)
    timecode_seconds: float = Field(ge=0, le=_MAX_TIMECODE_SECONDS)
    body: str = Field(min_length=1, max_length=_MAX_COMMENT_BODY)
    author_role: Literal["client", "admin"]
    status: str = Field(min_length=1, max_length=64)
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include an offset")
        return value.astimezone(dt.UTC)


class VideoCommentCreate(MobileDeliveryModel):
    body: str = Field(min_length=1, max_length=_MAX_COMMENT_BODY)
    timecode_seconds: float = Field(default=0.0, ge=0, le=_MAX_TIMECODE_SECONDS)
    parent_id: int | None = Field(default=None, ge=1)

    @field_validator("body")
    @classmethod
    def body_is_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("comment body is required")
        return cleaned


VideoCommentList = Annotated[list[VideoComment], Field(max_length=_MAX_COMMENTS)]


def _insufficient_scope() -> mobile_auth.MobileAuthError:
    return mobile_auth.MobileAuthError(
        403,
        "auth.insufficient_scope",
        "The token lacks this scope.",
    )


def _gallery_principal(request: Request, capability: str) -> mobile_auth.Principal:
    """Authenticate one exact gallery capability with no cookie/query fallback."""

    principal = mobile_auth.authenticate_request(request)
    gallery_id = principal.resource_id
    if (
        principal.kind != mobile_auth.GALLERY_GUEST
        or gallery_id is None
        or gallery_id < 1
        or principal.gallery_visitor_id is None
        or principal.gallery_visitor_id < 1
        or not principal.has_scope(f"gallery:{gallery_id}:{capability}")
    ):
        raise _insufficient_scope()
    return principal


def _expiry_date(value: object) -> dt.date | None:
    if value is None or not str(value).strip():
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        # Invalid persisted expiry data fails closed rather than extending access.
        raise HTTPException(status_code=410, detail="The gallery has expired.")


def _studio_today() -> dt.date:
    """Use the same configured studio clock as scheduling and owner delivery."""

    return admin_studio._today()


def _live_gallery(
    principal: mobile_auth.Principal,
    *,
    con: sqlite3.Connection | None = None,
) -> sqlite3.Row:
    executor = con.execute if con is not None else None
    sql = """SELECT g.id, g.slug, g.title, g.client_name, g.type, g.require_pin,
                    g.published, g.content_rev, g.cover_asset_id, g.expires_at,
                    g.created_at, g.argus_hero_asset_ids,
                    COALESCE(NULLIF(g.client_name, ''), c.name) AS resolved_client_name
               FROM galleries g
               JOIN visitors v ON v.gallery_id=g.id AND v.id=?
               LEFT JOIN clients c ON c.id=g.client_id
              WHERE g.id=? AND g.published=1"""
    params = (principal.gallery_visitor_id, principal.resource_id)
    row = executor(sql, params).fetchone() if executor is not None else db.one(sql, params)
    if row is None:
        raise HTTPException(status_code=404, detail="Gallery not found.")
    expiry = _expiry_date(row["expires_at"])
    if expiry is not None and expiry < _studio_today():
        raise HTTPException(status_code=410, detail="The gallery has expired.")
    return row


def _asset_row(
    con: sqlite3.Connection,
    gallery_id: int,
    asset_id: int,
    *,
    video_only: bool = False,
) -> sqlite3.Row:
    kind_clause = " AND a.kind='video'" if video_only else ""
    row = con.execute(
        f"""SELECT a.id, a.gallery_id, a.section_id, a.kind, a.status,
                   a.filename, a.stored, a.width, a.height, a.duration, a.bytes,
                   a.position, a.created_at, a.argus_alt_text, a.argus_keywords,
                   s.proof_target
              FROM assets a
              LEFT JOIN sections s ON s.id=a.section_id AND s.gallery_id=a.gallery_id
             WHERE a.id=? AND a.gallery_id=? AND a.status='ready'
               AND (a.section_id IS NULL OR s.id IS NOT NULL)
               {kind_clause}{delivery_gate.clause("a")}""",
        (asset_id, gallery_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    return row


def _origin(request: Request) -> str:
    origin = urls.origin_from_url(urls.request_origin(request))
    if origin is None or not origin.startswith(("http://", "https://")):
        raise mobile_auth.MobileAuthError(
            400,
            "request.invalid_origin",
            "A valid request host is required.",
        )
    return origin


def _media_links(
    request: Request,
    gallery_id: int,
    asset_id: int,
    kind: str,
    stored: object,
) -> MediaLinks:
    base = f"{_origin(request)}/api/v1/client/gallery/assets/{asset_id}"

    def available(variant: str) -> str | None:
        try:
            _safe_media_path(gallery_id, stored, variant, kind)
        except HTTPException:
            return None
        return f"{base}/{variant}"

    return MediaLinks(
        thumbnail_url=available("thumbnail"),
        preview_url=available("preview"),
        poster_url=available("poster") if kind == "video" else None,
        download_url=available("download"),
    )


def _finite_nonnegative(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _manifest_sections(
    con: sqlite3.Connection,
    gallery_id: int,
    visitor_id: int,
) -> list[GallerySection]:
    # V1 is one bounded manifest. Add cursor pagination before raising these DTO caps.
    rows = con.execute(
        f"""SELECT s.id, s.gallery_id, s.name, s.caption, s.position, s.proof_target,
                    (SELECT COUNT(DISTINCT f.asset_id)
                       FROM favorites f JOIN assets a ON a.id=f.asset_id
                      WHERE f.visitor_id=? AND a.gallery_id=? AND a.section_id=s.id
                        AND a.status='ready'{delivery_gate.clause("a")}) AS selected_count
              FROM sections s
              WHERE s.gallery_id=?
              ORDER BY s.position, s.id
              LIMIT ?""",
        (visitor_id, gallery_id, gallery_id, _MAX_SECTIONS),
    ).fetchall()
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


def _manifest_assets(
    request: Request,
    con: sqlite3.Connection,
    gallery_id: int,
    visitor_id: int,
) -> list[GalleryAsset]:
    rows = con.execute(
        f"""SELECT a.id, a.gallery_id, a.section_id, a.kind, a.filename, a.stored,
                    a.width, a.height, a.duration, a.bytes, a.position, a.created_at,
                    a.argus_alt_text, a.argus_keywords,
                    CASE WHEN f.asset_id IS NULL THEN 0 ELSE 1 END AS selected
               FROM assets a
               LEFT JOIN sections s ON s.id=a.section_id AND s.gallery_id=a.gallery_id
               LEFT JOIN favorites f ON f.asset_id=a.id AND f.visitor_id=?
              WHERE a.gallery_id=? AND a.status='ready'
                AND (a.section_id IS NULL OR s.id IS NOT NULL)
                {delivery_gate.clause("a")}
              ORDER BY a.section_id IS NULL, a.section_id, a.position, a.id
              LIMIT ?""",
        (visitor_id, gallery_id, _MAX_ASSETS),
    ).fetchall()
    result: list[GalleryAsset] = []
    for row in rows:
        selected = bool(row["selected"])
        duration = _finite_nonnegative(row["duration"])
        result.append(
            GalleryAsset(
                id=int(row["id"]),
                gallery_id=int(row["gallery_id"]),
                section_id=int(row["section_id"]) if row["section_id"] is not None else None,
                kind="video" if row["kind"] == "video" else "photo",
                status="ready",
                filename=_safe_filename(row["filename"], int(row["id"])),
                width=int(row["width"]) if row["width"] and int(row["width"]) > 0 else None,
                height=(int(row["height"]) if row["height"] and int(row["height"]) > 0 else None),
                duration_seconds=duration,
                byte_count=max(0, int(row["bytes"])) if row["bytes"] is not None else None,
                position=int(row["position"]),
                created_at=_sqlite_utc(row["created_at"]),
                is_favorite=selected,
                # Client delivery never exposes other visitors' aggregate activity.
                favorite_count=1 if selected else 0,
                links=_media_links(
                    request,
                    gallery_id,
                    int(row["id"]),
                    str(row["kind"]),
                    row["stored"],
                ),
                alt_text=_optional_text(row["argus_alt_text"], maximum=10_000),
                keywords=_keywords(row["argus_keywords"]),
                keeper_score=None,
                hero_potential=None,
                cull_state=None,
            )
        )
    return result


def _delivery_state(
    gallery: sqlite3.Row,
    sections: list[GallerySection],
) -> Literal["proofing", "expiring", "delivered"]:
    expiry = _expiry_date(gallery["expires_at"])
    if expiry is not None and expiry <= _studio_today() + dt.timedelta(days=7):
        return "expiring"
    if any(
        section.proof_target is not None and section.selected_count < section.proof_target
        for section in sections
    ):
        return "proofing"
    return "delivered"


def _private_headers(etag: str | None = None) -> dict[str, str]:
    headers = {"Cache-Control": _PRIVATE_REVALIDATE, "Vary": "Authorization"}
    if etag is not None:
        headers["ETag"] = etag
    return headers


def _media_headers(variant: str, etag: str) -> dict[str, str]:
    return {
        "Cache-Control": _PRIVATE_REVALIDATE if variant == "download" else _PRIVATE_DERIVATIVE,
        "Vary": "Authorization",
        "ETag": etag,
    }


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"


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


@router.get("/client/gallery", response_model=GalleryDetail, tags=["client galleries"])
def client_gallery(request: Request, response: Response) -> GalleryDetail | Response:
    principal = _gallery_principal(request, "read")
    con = db.connect()
    try:
        con.execute("BEGIN")
        gallery = _live_gallery(principal, con=con)
        gallery_id = int(gallery["id"])
        visitor_id = int(principal.gallery_visitor_id)
        sections = _manifest_sections(con, gallery_id, visitor_id)
        assets = _manifest_assets(request, con, gallery_id, visitor_id)
        downloads = con.execute(
            "SELECT COUNT(*) AS n FROM downloads WHERE gallery_id=? AND visitor_id=?",
            (gallery_id, visitor_id),
        ).fetchone()
    finally:
        con.close()

    asset_ids = {asset.id for asset in assets}
    hero_asset_ids = _hero_ids(gallery["argus_hero_asset_ids"], asset_ids)
    cover_asset_id = (
        int(gallery["cover_asset_id"])
        if gallery["cover_asset_id"] is not None and int(gallery["cover_asset_id"]) in asset_ids
        else None
    )
    detail = GalleryDetail(
        summary=GallerySummary(
            id=gallery_id,
            title=str(gallery["title"]).strip()[:500] or f"Gallery {gallery_id}",
            slug=str(gallery["slug"]),
            # Sequential internal CRM/project identifiers are not client capabilities.
            client_id=None,
            project_id=None,
            client_name=_optional_text(gallery["resolved_client_name"], maximum=500),
            type="drop" if gallery["type"] == "drop" else "gallery",
            published=True,
            requires_pin=bool(gallery["require_pin"]),
            content_revision=max(0, int(gallery["content_rev"] or 0)),
            cover_asset_id=cover_asset_id,
            expires_on=_expiry_date(gallery["expires_at"]),
            asset_count=len(assets),
            favorite_count=sum(1 for asset in assets if asset.is_favorite),
            download_count=max(0, int(downloads["n"])) if downloads is not None else 0,
            delivery_state=_delivery_state(gallery, sections),
            created_at=_sqlite_utc(gallery["created_at"]),
        ),
        sections=sections,
        assets=assets,
        hero_asset_ids=hero_asset_ids,
        # Vision run ids, scores, cull decisions, and provider state remain owner-only.
        vision=None,
    )
    digest = hashlib.sha256(detail.model_dump_json().encode()).hexdigest()
    etag = f'"client-gallery-{digest[:32]}"'
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=_private_headers(etag))
    for key, value in _private_headers(etag).items():
        response.headers[key] = value
    return detail


def _safe_media_path(gallery_id: int, stored: object, variant: str, kind: str) -> FilePath:
    stored_name = str(stored or "")
    stored_path = FilePath(stored_name)
    if (
        not stored_name
        or stored_name in {".", ".."}
        or stored_path.is_absolute()
        or stored_path.name != stored_name
    ):
        raise HTTPException(status_code=404, detail="Media not found.")

    stem = stored_path.stem
    if variant == "download":
        directory_name = "original"
        filename = stored_name
    elif variant == "thumbnail":
        directory_name = "thumb"
        filename = f"{stem}.jpg"
    elif variant == "preview":
        directory_name = "web"
        filename = f"{stem}.mp4" if kind == "video" else f"{stem}.jpg"
    elif variant == "poster" and kind == "video":
        directory_name = "web"
        filename = f"{stem}_poster.jpg"
    else:
        raise HTTPException(status_code=404, detail="Media not found.")

    try:
        media_root = FilePath(config.MEDIA_DIR).resolve(strict=True)
        gallery_root = media_root / str(gallery_id)
        directory = gallery_root / directory_name
        lexical_candidate = directory / filename
        # Reject symlinks at every resource-specific level. Merely checking the
        # resolved candidate against MEDIA_DIR would still permit one gallery to
        # point into another gallery beneath that shared tenant root.
        if gallery_root.resolve(strict=True) != gallery_root:
            raise ValueError("symlinked gallery root")
        if directory.resolve(strict=True) != directory:
            raise ValueError("symlinked media variant")
        candidate = lexical_candidate.resolve(strict=True)
        if candidate != lexical_candidate:
            raise ValueError("symlinked media file")
        candidate.relative_to(gallery_root)
        if not candidate.is_file():
            raise FileNotFoundError
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=404, detail="Media not found.")
    return candidate


def _serve_media(request: Request, asset_id: int, variant: str) -> Response:
    capability = "download" if variant == "download" else "read"
    principal = _gallery_principal(request, capability)
    con = db.connect()
    try:
        gallery = _live_gallery(principal, con=con)
        asset = _asset_row(
            con,
            int(gallery["id"]),
            asset_id,
            video_only=variant == "poster",
        )
    finally:
        con.close()

    path = _safe_media_path(int(gallery["id"]), asset["stored"], variant, str(asset["kind"]))
    try:
        stat_result = path.stat()
    except OSError:
        raise HTTPException(status_code=404, detail="Media not found.")
    etag_input = (
        f"{gallery['id']}:{asset_id}:{variant}:{gallery['content_rev']}:"
        f"{stat_result.st_size}:{stat_result.st_mtime_ns}"
    )
    etag = f'"media-{hashlib.sha256(etag_input.encode()).hexdigest()[:32]}"'
    headers = _media_headers(variant, etag)
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    if variant == "download":
        # A resumed/ranged transfer may create another analytics row. Download
        # rows are telemetry, never authorization or billing state.
        db.run(
            "INSERT INTO downloads (gallery_id, visitor_id, asset_id) VALUES (?,?,?)",
            (gallery["id"], principal.gallery_visitor_id, asset_id),
        )

    media_type = {
        "thumbnail": "image/jpeg",
        "poster": "image/jpeg",
        "preview": "video/mp4" if asset["kind"] == "video" else "image/jpeg",
        "download": "application/octet-stream",
    }[variant]
    filename = _safe_filename(asset["filename"], asset_id) if variant == "download" else None
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if filename else "inline",
        stat_result=stat_result,
        headers=headers,
    )


@router.get(
    "/client/gallery/assets/{asset_id}/thumbnail",
    response_class=FileResponse,
    tags=["client gallery media"],
)
def asset_thumbnail(request: Request, asset_id: Annotated[int, Path(ge=1)]) -> Response:
    return _serve_media(request, asset_id, "thumbnail")


@router.get(
    "/client/gallery/assets/{asset_id}/preview",
    response_class=FileResponse,
    tags=["client gallery media"],
)
def asset_preview(request: Request, asset_id: Annotated[int, Path(ge=1)]) -> Response:
    return _serve_media(request, asset_id, "preview")


@router.get(
    "/client/gallery/assets/{asset_id}/poster",
    response_class=FileResponse,
    tags=["client gallery media"],
)
def asset_poster(request: Request, asset_id: Annotated[int, Path(ge=1)]) -> Response:
    return _serve_media(request, asset_id, "poster")


@router.get(
    "/client/gallery/assets/{asset_id}/download",
    response_class=FileResponse,
    tags=["client gallery media"],
)
def asset_download(request: Request, asset_id: Annotated[int, Path(ge=1)]) -> Response:
    return _serve_media(request, asset_id, "download")


def _favorite_state(
    con: sqlite3.Connection,
    *,
    gallery_id: int,
    visitor_id: int,
    asset: sqlite3.Row,
    selected: bool,
) -> FavoriteState:
    section_id = asset["section_id"]
    if section_id is None:
        return FavoriteState(asset_id=int(asset["id"]), selected=selected)
    count = con.execute(
        f"""SELECT COUNT(DISTINCT f.asset_id) AS n
               FROM favorites f JOIN assets a ON a.id=f.asset_id
              WHERE f.visitor_id=? AND a.gallery_id=? AND a.section_id=?
                AND a.status='ready'{delivery_gate.clause("a")}""",
        (visitor_id, gallery_id, section_id),
    ).fetchone()
    target = asset["proof_target"]
    return FavoriteState(
        asset_id=int(asset["id"]),
        selected=selected,
        section_selected_count=max(0, int(count["n"])),
        section_proof_target=int(target) if target is not None and int(target) > 0 else None,
    )


@router.put(
    "/client/gallery/assets/{asset_id}/favorite",
    response_model=FavoriteState,
    tags=["client gallery favorites"],
)
def select_favorite(
    request: Request,
    response: Response,
    asset_id: Annotated[int, Path(ge=1)],
) -> FavoriteState:
    principal = _gallery_principal(request, "favorite")
    inserted = False
    with db.tx() as con:
        con.execute("BEGIN IMMEDIATE")
        gallery = _live_gallery(principal, con=con)
        gallery_id = int(gallery["id"])
        visitor_id = int(principal.gallery_visitor_id)
        asset = _asset_row(con, gallery_id, asset_id)
        existing = con.execute(
            "SELECT 1 FROM favorites WHERE visitor_id=? AND asset_id=?",
            (visitor_id, asset_id),
        ).fetchone()
        if existing is None:
            target = asset["proof_target"]
            if target is not None and int(target) > 0:
                current = _favorite_state(
                    con,
                    gallery_id=gallery_id,
                    visitor_id=visitor_id,
                    asset=asset,
                    selected=False,
                )
                if (current.section_selected_count or 0) >= int(target):
                    raise HTTPException(status_code=409, detail="Section proof target reached.")
            inserted = (
                con.execute(
                    "INSERT OR IGNORE INTO favorites (visitor_id,asset_id) VALUES (?,?)",
                    (visitor_id, asset_id),
                ).rowcount
                == 1
            )
        state = _favorite_state(
            con,
            gallery_id=gallery_id,
            visitor_id=visitor_id,
            asset=asset,
            selected=True,
        )
    if inserted and asset["kind"] == "photo":
        try:
            jobs.enqueue("social_crops", {"asset_id": asset_id})
        except Exception:  # noqa: BLE001 - derivative creation is best-effort after commit
            log.error("social crop enqueue failed for gallery asset %s", asset_id)
    _no_store(response)
    return state


@router.delete(
    "/client/gallery/assets/{asset_id}/favorite",
    response_model=FavoriteState,
    tags=["client gallery favorites"],
)
def remove_favorite(
    request: Request,
    response: Response,
    asset_id: Annotated[int, Path(ge=1)],
) -> FavoriteState:
    principal = _gallery_principal(request, "favorite")
    with db.tx() as con:
        con.execute("BEGIN IMMEDIATE")
        gallery = _live_gallery(principal, con=con)
        gallery_id = int(gallery["id"])
        visitor_id = int(principal.gallery_visitor_id)
        asset = _asset_row(con, gallery_id, asset_id)
        con.execute(
            "DELETE FROM favorites WHERE visitor_id=? AND asset_id=?",
            (visitor_id, asset_id),
        )
        state = _favorite_state(
            con,
            gallery_id=gallery_id,
            visitor_id=visitor_id,
            asset=asset,
            selected=False,
        )
    _no_store(response)
    return state


def _comment(row: sqlite3.Row) -> VideoComment:
    timecode = _finite_nonnegative(row["timecode"])
    if timecode is None or timecode > _MAX_TIMECODE_SECONDS:
        timecode = 0.0
    body = str(row["body"] or "").strip()[:_MAX_COMMENT_BODY] or "Comment unavailable."
    status = str(row["status"] or "open").strip()[:64] or "open"
    return VideoComment(
        id=int(row["id"]),
        asset_id=int(row["asset_id"]),
        parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
        timecode_seconds=timecode,
        body=body,
        author_role="admin" if row["author_role"] == "admin" else "client",
        status=status,
        created_at=_sqlite_utc(row["created_at"]),
    )


def _comment_rows(con: sqlite3.Connection, asset_id: int) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT id, asset_id, parent_id, timecode, body, author_role, status, created_at
             FROM video_comments
            WHERE asset_id=? AND deleted_at IS NULL
            ORDER BY timecode, created_at, id
            LIMIT ?""",
        (asset_id, _MAX_COMMENTS),
    ).fetchall()


@router.get(
    "/client/gallery/assets/{asset_id}/comments",
    response_model=VideoCommentList,
    tags=["client gallery comments"],
)
def list_video_comments(
    request: Request,
    response: Response,
    asset_id: Annotated[int, Path(ge=1)],
    limit: Annotated[int, Query(ge=1, le=_MAX_COMMENTS)] = _MAX_COMMENTS,
) -> VideoCommentList | Response:
    principal = _gallery_principal(request, "comment")
    con = db.connect()
    try:
        gallery = _live_gallery(principal, con=con)
        _asset_row(con, int(gallery["id"]), asset_id, video_only=True)
        rows = _comment_rows(con, asset_id)[:limit]
    finally:
        con.close()
    comments = [_comment(row) for row in rows]
    serialized = json.dumps(
        [comment.model_dump(mode="json") for comment in comments],
        sort_keys=True,
        separators=(",", ":"),
    )
    etag = f'"video-comments-{hashlib.sha256(serialized.encode()).hexdigest()[:32]}"'
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=_private_headers(etag))
    for key, value in _private_headers(etag).items():
        response.headers[key] = value
    return comments


@router.post(
    "/client/gallery/assets/{asset_id}/comments",
    response_model=VideoComment,
    status_code=201,
    tags=["client gallery comments"],
)
def add_video_comment(
    request: Request,
    response: Response,
    body: VideoCommentCreate,
    asset_id: Annotated[int, Path(ge=1)],
) -> VideoComment:
    principal = _gallery_principal(request, "comment")
    reopened = None
    with db.tx() as con:
        con.execute("BEGIN IMMEDIATE")
        gallery = _live_gallery(principal, con=con)
        gallery_id = int(gallery["id"])
        _asset_row(con, gallery_id, asset_id, video_only=True)
        inherited_timecode = None
        if body.parent_id is not None:
            parent = con.execute(
                """SELECT timecode FROM video_comments
                    WHERE id=? AND asset_id=? AND gallery_id=? AND deleted_at IS NULL""",
                (body.parent_id, asset_id, gallery_id),
            ).fetchone()
            if parent is None:
                raise HTTPException(status_code=400, detail="Reply target not found.")
            inherited_timecode = _finite_nonnegative(parent["timecode"])
            if inherited_timecode is None or inherited_timecode > _MAX_TIMECODE_SECONDS:
                raise HTTPException(status_code=400, detail="Reply target not found.")
        timecode = inherited_timecode if inherited_timecode is not None else body.timecode_seconds
        cursor = con.execute(
            """INSERT INTO video_comments
               (asset_id,gallery_id,parent_id,visitor_id,author_role,timecode,body)
               VALUES (?,?,?,?,?,?,?)""",
            (
                asset_id,
                gallery_id,
                body.parent_id,
                principal.gallery_visitor_id,
                "client",
                timecode,
                body.body,
            ),
        )
        comment_id = int(cursor.lastrowid)
        if body.parent_id is not None:
            reopened = public_gallery._maybe_reopen_on_reply(con, comment_id)
        row = con.execute(
            """SELECT id, asset_id, parent_id, timecode, body, author_role, status, created_at
                 FROM video_comments WHERE id=?""",
            (comment_id,),
        ).fetchone()
        assert row is not None

    if reopened is not None:
        try:
            reopen_notify.notify_reopen(
                {
                    "gallery_slug": gallery["slug"],
                    "gallery_title": gallery["title"],
                    **reopened,
                }
            )
        except Exception:  # noqa: BLE001 - push notification is post-commit and best-effort
            log.error("video comment reopen notification failed for asset %s", asset_id)
    _no_store(response)
    return _comment(row)
