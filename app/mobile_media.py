"""Bearer-authenticated media serving for the native API.

Milestone 2 emitted ``null`` for every gallery asset media link. This module is
the client-delivery slice that promised: a mounted ``/media`` router under
``/api/v1`` that resolves the same on-disk derivatives the browser app serves
from ``app/public/media.py``, but gated by an opaque bearer session instead of
a visitor cookie. It never accepts a tenant id, slug, or filesystem path as
caller-supplied authority -- only a gallery/asset id pair re-checked against
the authenticated principal's own scope on every request.
"""

from __future__ import annotations

import datetime as dt
import mimetypes
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from . import config, db, delivery_gate, mobile_auth, urls

router = APIRouter(prefix="/media", tags=["media"])

MediaVariant = Literal["thumbnail", "preview", "poster", "download"]


def build_media_links(
    request: Request, gallery_id: int, asset_id: int, kind: str
) -> dict[str, str | None]:
    """Absolute, bearer-protected URLs for one ready asset's derivatives."""
    base = f"{urls.request_origin(request)}/api/v1/media/galleries/{gallery_id}/assets/{asset_id}"
    return {
        "thumbnail_url": f"{base}/thumbnail",
        "preview_url": f"{base}/preview",
        "poster_url": f"{base}/poster" if kind == "video" else None,
        "download_url": f"{base}/download",
    }


def _gallery_row(gallery_id: int):
    return db.one("SELECT id, published, expires_at FROM galleries WHERE id=?", (gallery_id,))


def _gallery_is_expired(row) -> bool:
    return bool(row["expires_at"]) and row["expires_at"] < dt.date.today().isoformat()


def _asset_row(gallery_id: int, asset_id: int):
    gate = delivery_gate.clause("a")
    return db.one(
        f"""SELECT a.id, a.gallery_id, a.kind, a.stored, a.status
              FROM assets a WHERE a.id=? AND a.gallery_id=? AND a.status='ready'{gate}""",
        (asset_id, gallery_id),
    )


def _resolve_path(gallery_id: int, asset, variant: MediaVariant) -> Path:
    base = config.MEDIA_DIR / str(gallery_id)
    stem = Path(asset["stored"]).stem
    if variant == "download":
        return base / "original" / asset["stored"]
    if variant == "thumbnail":
        return base / "thumb" / f"{stem}.jpg"
    if variant == "poster":
        return base / "web" / f"{stem}_poster.jpg"
    return base / "web" / (f"{stem}.mp4" if asset["kind"] == "video" else f"{stem}.jpg")


def _insufficient_scope() -> mobile_auth.MobileAuthError:
    return mobile_auth.MobileAuthError(
        403, "auth.insufficient_scope", "The token lacks this scope."
    )


def _authorize(request: Request, gallery_id: int, *, require_download: bool) -> None:
    """Re-check gallery scope on every media request; scopes are never cached."""
    principal = mobile_auth.authenticate_request(request)
    if principal.kind == mobile_auth.STUDIO_OWNER:
        if not principal.has_scope("studio:read"):
            raise _insufficient_scope()
        return
    if principal.kind == mobile_auth.GALLERY_GUEST and principal.resource_id == gallery_id:
        if require_download and not principal.has_scope(f"gallery:{gallery_id}:download"):
            raise _insufficient_scope()
        return
    if principal.kind == mobile_auth.WORKSPACE_GUEST and not require_download:
        row = db.one("SELECT gallery_id FROM projects WHERE id=?", (principal.resource_id,))
        if row and row["gallery_id"] == gallery_id:
            return
        raise _insufficient_scope()
    if principal.kind == mobile_auth.PORTAL_GUEST and not require_download:
        client_row = db.one("SELECT client_id FROM portals WHERE id=?", (principal.resource_id,))
        if client_row:
            owns = db.one(
                "SELECT 1 AS x FROM galleries WHERE id=? AND client_id=? AND published=1",
                (gallery_id, client_row["client_id"]),
            )
            if owns:
                return
        raise _insufficient_scope()
    raise _insufficient_scope()


@router.get("/galleries/{gallery_id}/assets/{asset_id}/{variant}")
def serve_asset_media(
    request: Request,
    gallery_id: int,
    asset_id: int,
    variant: MediaVariant,
) -> FileResponse:
    _authorize(request, gallery_id, require_download=(variant == "download"))
    gallery = _gallery_row(gallery_id)
    if not gallery or not gallery["published"] or _gallery_is_expired(gallery):
        raise HTTPException(status_code=404, detail="Gallery not found.")
    asset = _asset_row(gallery_id, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found.")
    if variant == "poster" and asset["kind"] != "video":
        raise HTTPException(status_code=404, detail="Asset has no poster.")
    path = _resolve_path(gallery_id, asset, variant)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media not available.")
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(
        path, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"}
    )
