"""Native owner cull review and reversible decision commands.

AI keeper scores are suggestions only. This boundary exposes a bounded owner
review queue and derivative media, then records only an explicit human
keep/cut/restore decision. It never deletes an asset, serves an original, or
reuses the client delivery gate: owners must be able to see a cut frame in order
to restore it.

The entire surface is inert until ``MISE_CULL_UI`` is enabled. Reads require an
exact ``studio_owner`` bearer with ``studio:read``; decisions additionally
require ``studio:write``, a session-bound UUID idempotency key, and the strong
per-asset representation ETag returned in each queue item.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import AnyHttpUrl, Field, model_validator

from . import audit, config, db, mobile_auth
from . import mobile_gallery_calendar_api as gallery_reads
from . import mobile_gallery_delivery_api as gallery_delivery
from . import mobile_owner_mutation_api as mutations
from .mobile_api_schemas import APIProblem

router = APIRouter()

_PRIVATE_REVALIDATE = "private, no-cache"
_PRIVATE_DERIVATIVE = "private, max-age=86400"
_MAX_CURSOR_LENGTH = 1024
_INT64_MAX = 2**63 - 1
_ACTIONS: dict[str, str | None] = {"keep": "keep", "cut": "cut", "restore": None}
_AUTH_PARAMETER = {
    "name": "Authorization",
    "in": "header",
    "required": True,
    "schema": {"type": "string"},
    "description": "Bearer token for the exact studio owner session.",
}
_IF_NONE_MATCH_PARAMETER = {
    "name": "If-None-Match",
    "in": "header",
    "required": False,
    "schema": {"type": "string"},
}
_IF_MATCH_PARAMETER = {
    "name": "If-Match",
    "in": "header",
    "required": True,
    "schema": {"type": "string"},
    "description": "Strong ETag from the cull item being reviewed.",
}
_IDEMPOTENCY_PARAMETER = {
    "name": "Idempotency-Key",
    "in": "header",
    "required": True,
    "schema": {"type": "string", "format": "uuid"},
}


def _problem_response(
    description: str,
    *,
    retry_after: bool = False,
) -> dict[str, object]:
    response: dict[str, object] = {
        "model": APIProblem,
        "description": description,
        "content": {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/APIProblem"},
            }
        },
    }
    if retry_after:
        response["headers"] = {
            "Retry-After": {
                "description": "Seconds before the client should retry.",
                "schema": {"type": "integer", "minimum": 0},
            }
        }
    return response


_READ_RESPONSES = {
    304: {"description": "The private representation is unchanged"},
    401: _problem_response("Authentication failed"),
    403: _problem_response("Exact studio owner scope required"),
    404: _problem_response("Gallery/cull feature not found"),
    409: _problem_response("The score-ranked collection changed while paging"),
    422: _problem_response("Invalid path, limit, or cursor"),
    429: _problem_response("Rate limit exceeded", retry_after=True),
}
_WRITE_RESPONSES = {
    401: _problem_response("Authentication failed"),
    403: _problem_response("Studio owner write scope required"),
    404: _problem_response("Cull asset/feature not found"),
    409: _problem_response("Version or idempotency conflict"),
    422: _problem_response("Required header/body/path validation failed"),
    429: _problem_response("Rate limit exceeded", retry_after=True),
}
_MEDIA_RESPONSES = {
    200: {
        "description": "Private JPEG derivative",
        "content": {
            "image/jpeg": {
                "schema": {"type": "string", "format": "binary"},
            }
        },
    },
    304: {"description": "The private derivative is unchanged"},
    401: _problem_response("Authentication failed"),
    403: _problem_response("Exact studio owner scope required"),
    404: _problem_response("Derivative, asset, gallery, or feature not found"),
    422: _problem_response("Invalid path parameter"),
    429: _problem_response("Rate limit exceeded", retry_after=True),
}


class CullDecision(mutations.MobileWriteRequest):
    action: Literal["keep", "cut", "restore"]


class CullItem(mutations.MobileWriteModel):
    asset_id: int = Field(gt=0)
    gallery_id: int = Field(gt=0)
    filename: str = Field(min_length=1, max_length=1000)
    position: int
    keeper_score: float | None = Field(default=None, ge=0, le=1)
    hero_potential: float | None = Field(default=None, ge=0, le=1)
    state: Literal["keep", "cut"] | None = None
    thumbnail_url: AnyHttpUrl | None = None
    preview_url: AnyHttpUrl | None = None
    media_revision: int = Field(ge=0, le=_INT64_MAX)
    etag: str = Field(min_length=3, max_length=128)


class CullCounts(mutations.MobileWriteModel):
    total: int = Field(ge=0)
    keep: int = Field(ge=0)
    cut: int = Field(ge=0)
    undecided: int = Field(ge=0)
    scored: int = Field(ge=0)

    @model_validator(mode="after")
    def totals_are_consistent(self) -> CullCounts:
        if self.keep + self.cut + self.undecided != self.total or self.scored > self.total:
            raise ValueError("cull counts are inconsistent")
        return self


class CullPage(mutations.MobileWriteModel):
    items: list[CullItem] = Field(default_factory=list, max_length=100)
    next_cursor: str | None = Field(default=None, max_length=_MAX_CURSOR_LENGTH)
    has_more: bool
    counts: CullCounts


StudioReader = Annotated[
    mobile_auth.Principal,
    Depends(gallery_reads.require_studio_owner),
]


def _require_enabled() -> None:
    if not config.CULL_UI:
        raise HTTPException(status_code=404, detail="Culling is not enabled.")


def _not_found() -> mobile_auth.MobileAuthError:
    return mobile_auth.MobileAuthError(404, "cull.asset_not_found", "Cull asset not found.")


def _gallery_exists(con: sqlite3.Connection, gallery_id: int) -> None:
    if (
        con.execute(
            "SELECT 1 FROM galleries WHERE id=? AND type='gallery'",
            (gallery_id,),
        ).fetchone()
        is None
    ):
        raise mobile_auth.MobileAuthError(404, "cull.gallery_not_found", "Gallery not found.")


def _cull_asset_row(
    con: sqlite3.Connection,
    gallery_id: int,
    asset_id: int,
) -> sqlite3.Row:
    row = con.execute(
        """SELECT a.id, a.gallery_id, a.filename, a.stored, a.position,
                  a.argus_keeper_score, a.argus_hero_potential, a.cull_state,
                  a.cull_decided_at, a.created_at,
                  (SELECT COALESCE(MAX(al.id), 0)
                     FROM audit_log al
                    WHERE al.entity_type='asset' AND al.entity_id=a.id
                      AND al.action LIKE 'cull:%') AS cull_revision
             FROM assets a JOIN galleries g ON g.id=a.gallery_id AND g.type='gallery'
            WHERE a.id=? AND a.gallery_id=? AND a.kind='photo' AND a.status='ready'""",
        (asset_id, gallery_id),
    ).fetchone()
    if row is None:
        raise _not_found()
    return row


def _asset_etag(
    row: sqlite3.Row,
    derivatives: dict[str, list[int] | None],
) -> str:
    # The latest cull audit id prevents keep -> cut -> keep ABA from reviving an
    # old validator. Scores are included because they are the evidence the human
    # reviewed; a new scoring pass therefore requires a fresh decision view.
    representation = {
        "asset_id": int(row["id"]),
        "gallery_id": int(row["gallery_id"]),
        "filename": gallery_reads._safe_filename(row["filename"], int(row["id"])),
        "position": int(row["position"]),
        "keeper_score": gallery_reads._unit_score(row["argus_keeper_score"]),
        "hero_potential": gallery_reads._unit_score(row["argus_hero_potential"]),
        "state": row["cull_state"] if row["cull_state"] in {"keep", "cut"} else None,
        "cull_revision": max(0, int(row["cull_revision"] or 0)),
        # These values never cross the wire. They prevent a deleted/reinserted
        # integer id or a replaced review derivative from accepting a stale card.
        "stored_identity": str(row["stored"]),
        "created_at": str(row["created_at"] or ""),
        "derivatives": derivatives,
    }
    digest = hashlib.sha256(
        json.dumps(representation, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f'"cull-asset-{digest[:32]}"'


def _media_revision(
    row: sqlite3.Row,
    derivatives: dict[str, list[int] | None],
) -> int:
    """Return an opaque, stable cache revision for the protected derivatives."""

    identity = {
        "stored_identity": str(row["stored"]),
        "created_at": str(row["created_at"] or ""),
        "derivatives": derivatives,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    # Fifteen hex digits stay below signed Int64 while retaining 60 bits of
    # collision resistance for an in-memory UI cache key.
    return int(digest[:15], 16)


def _derivative_fingerprint(row: sqlite3.Row) -> dict[str, list[int] | None]:
    result: dict[str, list[int] | None] = {}
    for variant in ("thumbnail", "preview"):
        try:
            path = gallery_delivery._safe_media_path(
                int(row["gallery_id"]),
                row["stored"],
                variant,
                "photo",
            )
            stat_result = path.stat()
            result[variant] = [
                int(stat_result.st_size),
                int(stat_result.st_mtime_ns),
                int(stat_result.st_ctime_ns),
                int(stat_result.st_ino),
            ]
        except (HTTPException, OSError):
            result[variant] = None
    return result


def _media_url(
    request: Request,
    row: sqlite3.Row,
    variant: Literal["thumbnail", "preview"],
    derivatives: dict[str, list[int] | None],
) -> str | None:
    if derivatives[variant] is None:
        return None
    origin = gallery_delivery._origin(request)
    return (
        f"{origin}/api/v1/galleries/{int(row['gallery_id'])}/cull/assets/{int(row['id'])}/{variant}"
    )


def _cull_item(request: Request, row: sqlite3.Row) -> CullItem:
    state = row["cull_state"] if row["cull_state"] in {"keep", "cut"} else None
    derivatives = _derivative_fingerprint(row)
    return CullItem(
        asset_id=int(row["id"]),
        gallery_id=int(row["gallery_id"]),
        filename=gallery_reads._safe_filename(row["filename"], int(row["id"])),
        position=int(row["position"]),
        keeper_score=gallery_reads._unit_score(row["argus_keeper_score"]),
        hero_potential=gallery_reads._unit_score(row["argus_hero_potential"]),
        state=state,
        thumbnail_url=_media_url(request, row, "thumbnail", derivatives),
        preview_url=_media_url(request, row, "preview", derivatives),
        media_revision=_media_revision(row, derivatives),
        etag=_asset_etag(row, derivatives),
    )


def _decode_after(
    cursor: str | None,
    gallery_id: int,
    tenant_key: str,
) -> tuple[str, int, float, int, int] | None:
    decoded = gallery_reads._decode_cursor(
        cursor,
        _cursor_kind(gallery_id, tenant_key),
        (str, int, str, int, int),
    )
    if decoded is None:
        return None
    revision, missing, raw_score, position, asset_id = decoded
    try:
        score = float(str(raw_score))
    except ValueError as exc:
        raise gallery_reads._cursor_problem() from exc
    if (
        not str(revision)
        or len(str(revision)) != 64
        or int(missing) not in {0, 1}
        or not math.isfinite(score)
        or not 0 <= score <= 1
        or (int(missing) == 1 and score != 0)
        or int(asset_id) < 1
    ):
        raise gallery_reads._cursor_problem()
    return str(revision), int(missing), score, int(position), int(asset_id)


def _cursor_kind(gallery_id: int, tenant_key: str) -> str:
    """Bind opaque pagination state to one hosted tenant without exposing its key."""

    if not config.SECRET_KEY:
        raise RuntimeError("MISE_SECRET_KEY is not set")
    tenant_binding = hmac.new(
        config.SECRET_KEY.encode(),
        b"mise-mobile-cull-tenant\0" + tenant_key.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"cull:{gallery_id}:{tenant_binding}"


def _queue_revision(con: sqlite3.Connection, gallery_id: int) -> str:
    rows = con.execute(
        """SELECT id, position, argus_keeper_score
             FROM assets
            WHERE gallery_id=? AND kind='photo' AND status='ready'
            ORDER BY id""",
        (gallery_id,),
    ).fetchall()
    digest = hashlib.sha256()
    for row in rows:
        score = gallery_reads._unit_score(row["argus_keeper_score"])
        digest.update(
            f"{int(row['id'])}:{int(row['position'])}:"
            f"{format(score, '.17g') if score is not None else 'none'}\n".encode()
        )
    return digest.hexdigest()


def _page_rows(
    con: sqlite3.Connection,
    gallery_id: int,
    after: tuple[int, float, int, int] | None,
    limit: int,
) -> list[sqlite3.Row]:
    predicate = ""
    params: list[object] = [gallery_id]
    if after is not None:
        missing, score, position, asset_id = after
        predicate = """WHERE score_missing > ?
                       OR (score_missing = ? AND (
                            score_key < ?
                            OR (score_key = ? AND (
                                 position > ? OR (position = ? AND id > ?)
                            ))
                       ))"""
        params.extend((missing, missing, score, score, position, position, asset_id))
    params.append(limit + 1)
    return con.execute(
        f"""WITH queue AS (
               SELECT a.id, a.gallery_id, a.filename, a.stored, a.position,
                      a.argus_keeper_score, a.argus_hero_potential, a.cull_state,
                      a.cull_decided_at, a.created_at,
                      CASE WHEN a.argus_keeper_score BETWEEN 0.0 AND 1.0
                           THEN 0 ELSE 1 END AS score_missing,
                      CASE WHEN a.argus_keeper_score BETWEEN 0.0 AND 1.0
                           THEN a.argus_keeper_score ELSE 0.0 END AS score_key,
                      (SELECT COALESCE(MAX(al.id), 0)
                         FROM audit_log al
                        WHERE al.entity_type='asset' AND al.entity_id=a.id
                          AND al.action LIKE 'cull:%') AS cull_revision
                 FROM assets a
                WHERE a.gallery_id=? AND a.kind='photo' AND a.status='ready'
           )
           SELECT * FROM queue
           {predicate}
           ORDER BY score_missing, score_key DESC, position, id
           LIMIT ?""",
        tuple(params),
    ).fetchall()


def _counts(con: sqlite3.Connection, gallery_id: int) -> CullCounts:
    row = con.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN cull_state='keep' THEN 1 ELSE 0 END) AS kept,
                  SUM(CASE WHEN cull_state='cut' THEN 1 ELSE 0 END) AS cut,
                  SUM(CASE WHEN cull_state IS NULL THEN 1 ELSE 0 END) AS undecided,
                  SUM(CASE WHEN argus_keeper_score BETWEEN 0.0 AND 1.0
                           THEN 1 ELSE 0 END) AS scored
             FROM assets
            WHERE gallery_id=? AND kind='photo' AND status='ready'""",
        (gallery_id,),
    ).fetchone()
    return CullCounts(
        total=max(0, int(row["total"] or 0)),
        keep=max(0, int(row["kept"] or 0)),
        cut=max(0, int(row["cut"] or 0)),
        undecided=max(0, int(row["undecided"] or 0)),
        scored=max(0, int(row["scored"] or 0)),
    )


def _private_headers(etag: str | None = None, *, derivative: bool = False) -> dict[str, str]:
    headers = {
        "Cache-Control": _PRIVATE_DERIVATIVE if derivative else _PRIVATE_REVALIDATE,
        "Vary": "Authorization",
    }
    if etag is not None:
        headers["ETag"] = etag
    return headers


def _set_private(response: Response, etag: str) -> None:
    for key, value in _private_headers(etag).items():
        response.headers[key] = value


@router.get(
    "/galleries/{gallery_id}/cull",
    response_model=CullPage,
    responses=_READ_RESPONSES,
    openapi_extra={"parameters": [_AUTH_PARAMETER, _IF_NONE_MATCH_PARAMETER]},
    tags=["owner culling"],
)
def cull_page(
    request: Request,
    response: Response,
    principal: StudioReader,
    gallery_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
    cursor: Annotated[str | None, Query(max_length=_MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> CullPage | Response:
    _require_enabled()
    cursor_kind = _cursor_kind(gallery_id, principal.tenant_key)
    decoded_after = _decode_after(cursor, gallery_id, principal.tenant_key)
    con = db.connect()
    try:
        con.execute("BEGIN")
        _gallery_exists(con, gallery_id)
        queue_revision = _queue_revision(con, gallery_id)
        if decoded_after is not None and decoded_after[0] != queue_revision:
            raise mobile_auth.MobileAuthError(
                409,
                "pagination.collection_changed",
                "Cull scores changed while paging. Reload the review.",
            )
        after = decoded_after[1:] if decoded_after is not None else None
        rows = _page_rows(con, gallery_id, after, limit)
        counts = _counts(con, gallery_id)
    finally:
        con.close()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = gallery_reads._encode_cursor(
            cursor_kind,
            (
                queue_revision,
                int(last["score_missing"]),
                format(float(last["score_key"]), ".17g"),
                int(last["position"]),
                int(last["id"]),
            ),
        )
    page = CullPage(
        items=[_cull_item(request, row) for row in page_rows],
        next_cursor=next_cursor,
        has_more=has_more,
        counts=counts,
    )
    digest = hashlib.sha256(page.model_dump_json().encode()).hexdigest()
    etag = f'"cull-page-{digest[:32]}"'
    if gallery_reads._etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=_private_headers(etag))
    _set_private(response, etag)
    return page


def _serve_derivative(
    request: Request,
    gallery_id: int,
    asset_id: int,
    variant: Literal["thumbnail", "preview"],
) -> Response:
    _require_enabled()
    con = db.connect()
    try:
        row = _cull_asset_row(con, gallery_id, asset_id)
    finally:
        con.close()
    path = gallery_delivery._safe_media_path(gallery_id, row["stored"], variant, "photo")
    try:
        stat_result = path.stat()
    except OSError:
        raise _not_found()
    etag_input = json.dumps(
        {
            "gallery_id": gallery_id,
            "asset_id": asset_id,
            "variant": variant,
            "stored": str(row["stored"]),
            "created_at": str(row["created_at"] or ""),
            "size": int(stat_result.st_size),
            "mtime_ns": int(stat_result.st_mtime_ns),
            "ctime_ns": int(stat_result.st_ctime_ns),
            "inode": int(stat_result.st_ino),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    etag = f'"cull-media-{hashlib.sha256(etag_input.encode()).hexdigest()[:32]}"'
    headers = _private_headers(etag, derivative=True)
    if gallery_reads._etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return FileResponse(
        path,
        media_type="image/jpeg",
        content_disposition_type="inline",
        stat_result=stat_result,
        headers=headers,
    )


@router.get(
    "/galleries/{gallery_id}/cull/assets/{asset_id}/thumbnail",
    response_class=FileResponse,
    responses=_MEDIA_RESPONSES,
    openapi_extra={"parameters": [_AUTH_PARAMETER, _IF_NONE_MATCH_PARAMETER]},
    tags=["owner culling media"],
)
def cull_thumbnail(
    request: Request,
    _principal: StudioReader,
    gallery_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
    asset_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
) -> Response:
    return _serve_derivative(request, gallery_id, asset_id, "thumbnail")


@router.get(
    "/galleries/{gallery_id}/cull/assets/{asset_id}/preview",
    response_class=FileResponse,
    responses=_MEDIA_RESPONSES,
    openapi_extra={"parameters": [_AUTH_PARAMETER, _IF_NONE_MATCH_PARAMETER]},
    tags=["owner culling media"],
)
def cull_preview(
    request: Request,
    _principal: StudioReader,
    gallery_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
    asset_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
) -> Response:
    return _serve_derivative(request, gallery_id, asset_id, "preview")


@router.patch(
    "/galleries/{gallery_id}/assets/{asset_id}/cull",
    response_model=CullItem,
    responses=_WRITE_RESPONSES,
    openapi_extra={"parameters": [_AUTH_PARAMETER, _IDEMPOTENCY_PARAMETER, _IF_MATCH_PARAMETER]},
    tags=["owner culling"],
)
def decide_cull(
    request: Request,
    response: Response,
    body: CullDecision,
    principal: mutations.StudioWriter,
    gallery_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
    asset_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
) -> CullItem:
    _require_enabled()
    with mutations._immediate_transaction() as con:
        claim = mutations._claim_command(
            con,
            request,
            principal,
            f"cull.decide:{gallery_id}:{asset_id}",
            mutations._request_payload(body, request, include_match=True),
        )
        if claim.replayed:
            value = mutations._replay(claim, CullItem)
        else:
            before_row = _cull_asset_row(con, gallery_id, asset_id)
            before = _cull_item(request, before_row)
            mutations._require_current(request, before.etag)
            previous = before.state
            new_state = _ACTIONS[body.action]
            if new_state is None:
                con.execute(
                    """UPDATE assets
                          SET cull_state=NULL, cull_decided_at=NULL, cull_source=NULL
                        WHERE id=? AND gallery_id=?""",
                    (asset_id, gallery_id),
                )
            else:
                con.execute(
                    """UPDATE assets
                          SET cull_state=?, cull_decided_at=datetime('now'),
                              cull_source='manual'
                        WHERE id=? AND gallery_id=?""",
                    (new_state, asset_id, gallery_id),
                )
            audit.log(
                con,
                "asset",
                asset_id,
                f"cull:{body.action}",
                actor="mobile_owner",
                diff={"cull_state": [previous, new_state]},
            )
            if previous != new_state:
                con.execute(
                    "UPDATE galleries SET content_rev=content_rev+1 WHERE id=?",
                    (gallery_id,),
                )
            value = _cull_item(request, _cull_asset_row(con, gallery_id, asset_id))
            if value.media_revision != before.media_revision:
                raise mobile_auth.MobileAuthError(
                    409,
                    "cull.media_changed",
                    "The review media changed. Reload the frame before deciding.",
                )
            mutations._finish_command(con, principal, claim, value, status_code=200)
    mutations._private(
        response,
        etag=value.etag,
        replayed=claim.replayed,
    )
    return value
