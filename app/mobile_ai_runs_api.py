"""Privacy-bounded native owner view over the append-only AI provenance ledger.

This surface is deliberately read-only. It exposes normalized operational metadata
for a studio owner, never provider output, prompts, raw error strings, correlation
or idempotency identifiers, credentials, or caller-selected tenant state.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field, model_validator

from . import config, db, mobile_auth
from . import mobile_gallery_calendar_api as gallery_reads
from .mobile_api_schemas import APIProblem, MobileAPIModel

router = APIRouter()

_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100
_MAX_CURSOR_LENGTH = 1024
_INT64_MAX = 2**63 - 1
_PRIVATE_REVALIDATE = "private, no-cache"
# Bump whenever the projection/normalization changes so a page-one 304 cannot
# preserve older pages assembled under different representation semantics.
_REPRESENTATION_VERSION = 1

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


_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": {
        "description": "Private response that must be revalidated before reuse.",
        "schema": {"type": "string", "const": _PRIVATE_REVALIDATE},
    },
    "ETag": {
        "description": "Strong validator for this normalized page.",
        "schema": {"type": "string"},
    },
    "Vary": {
        "description": "Prevents reuse across owner bearer sessions.",
        "schema": {"type": "string", "const": "Authorization"},
    },
}

_RESPONSES = {
    200: {
        "description": "Newest-first normalized AI activity page",
        "headers": _PRIVATE_RESPONSE_HEADERS,
    },
    304: {
        "description": "The private representation is unchanged",
        "headers": _PRIVATE_RESPONSE_HEADERS,
    },
    401: _problem_response("Authentication failed"),
    403: _problem_response("Exact studio owner scope required"),
    422: _problem_response("Invalid limit or cursor"),
    429: _problem_response("Rate limit exceeded", retry_after=True),
    500: _problem_response("A stored row could not be projected safely"),
}


class AIActivitySubject(MobileAPIModel):
    kind: Literal["gallery", "caption", "other"]


class AIRunItem(MobileAPIModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    capability: Literal["vision", "content", "products", "other"]
    provider: Literal["argus", "qwen", "odysseus", "dionysus", "aphrodite", "other"]
    status: Literal[
        "ok",
        "disabled",
        "provider_error",
        "invalid_response",
        "unknown",
    ]
    review: Literal["none", "human_review", "explicit_commit", "unknown"]
    latency_ms: int | None = Field(default=None, ge=0, le=_INT64_MAX)
    cost_micro_usd: int | None = Field(default=None, ge=0, le=_INT64_MAX)
    tokens: int | None = Field(default=None, ge=0, le=_INT64_MAX)
    subject: AIActivitySubject | None = None
    created_at: dt.datetime

    @model_validator(mode="after")
    def created_at_is_aware(self) -> AIRunItem:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return self


class AIRunPage(MobileAPIModel):
    items: list[AIRunItem] = Field(default_factory=list, max_length=_MAX_PAGE_SIZE)
    next_cursor: str | None = Field(default=None, max_length=_MAX_CURSOR_LENGTH)
    has_more: bool

    @model_validator(mode="after")
    def continuation_matches_flag(self) -> AIRunPage:
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("AI run continuation is inconsistent")
        return self


def _require_studio_owner(request: Request) -> mobile_auth.Principal:
    principal = mobile_auth.authenticate_request(request, required_scopes=("studio:read",))
    if principal.kind != mobile_auth.STUDIO_OWNER:
        raise mobile_auth.MobileAuthError(
            403,
            "auth.insufficient_scope",
            "This resource requires a studio owner.",
        )
    return principal


StudioReader = Annotated[
    mobile_auth.Principal,
    Depends(_require_studio_owner),
]


def _cursor_kind(tenant_key: str) -> str:
    if not config.SECRET_KEY:
        raise RuntimeError("MISE_SECRET_KEY is not set")
    tenant_binding = hmac.new(
        config.SECRET_KEY.encode(),
        b"mise-mobile-ai-runs-tenant\0" + tenant_key.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"ai-runs:{tenant_binding}"


def _decode_after(cursor: str | None, tenant_key: str) -> int | None:
    decoded = gallery_reads._decode_cursor(
        cursor,
        _cursor_kind(tenant_key),
        (int,),
    )
    if decoded is None:
        return None
    after = int(decoded[0])
    if not 1 <= after <= _INT64_MAX:
        raise gallery_reads._cursor_problem()
    return after


def _reject_unknown_or_duplicate_query(request: Request) -> None:
    allowed = {"cursor", "limit"}
    seen: set[str] = set()
    for name, _value in request.query_params.multi_items():
        if name not in allowed or name in seen:
            raise mobile_auth.MobileAuthError(
                422,
                "request.validation_failed",
                "The query parameters are invalid.",
            )
        seen.add(name)


def _bounded_nonnegative(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    if isinstance(value, Decimal) and value != value.to_integral_value():
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if 0 <= result <= _INT64_MAX else None


def _cost_micro_usd(value: object) -> int | None:
    if value is None:
        return None
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not cost.is_finite() or cost < 0:
        return None
    try:
        micro = (cost * Decimal(1_000_000)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
        result = int(micro)
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None
    return result if 0 <= result <= _INT64_MAX else None


def _utc_timestamp(value: object) -> dt.datetime:
    normalized = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("stored AI run timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _capability(value: object) -> str:
    raw = str(value or "")
    return raw if raw in {"vision", "content", "products"} else "other"


def _provider(value: object) -> str:
    raw = str(value or "")
    if raw == "qwen3-vl":
        return "qwen"
    return raw if raw in {"argus", "odysseus", "dionysus", "aphrodite"} else "other"


def _status(value: object) -> str:
    raw = str(value or "")
    return raw if raw in {"ok", "disabled", "provider_error", "invalid_response"} else "unknown"


def _review(value: object) -> str:
    raw = str(value or "")
    return raw if raw in {"none", "human_review", "explicit_commit"} else "unknown"


def _subject(row) -> AIActivitySubject | None:
    subject_type = str(row["subject_type"] or "")
    if subject_type == "gallery":
        return AIActivitySubject(kind="gallery")
    if subject_type == "retainer_caption":
        return AIActivitySubject(kind="caption")
    if subject_type:
        return AIActivitySubject(kind="other")
    return None


def _item(row) -> AIRunItem:
    return AIRunItem(
        id=int(row["id"]),
        capability=_capability(row["capability"]),
        provider=_provider(row["provider"]),
        status=_status(row["status"]),
        review=_review(row["review"]),
        latency_ms=_bounded_nonnegative(row["latency_ms"]),
        cost_micro_usd=_cost_micro_usd(row["cost_usd"]),
        tokens=_bounded_nonnegative(row["tokens"]),
        subject=_subject(row),
        created_at=_utc_timestamp(row["created_at"]),
    )


def _private_headers(etag: str) -> dict[str, str]:
    return {
        "Cache-Control": _PRIVATE_REVALIDATE,
        "ETag": etag,
        "Vary": "Authorization",
    }


@router.get(
    "/ai/runs",
    response_model=AIRunPage,
    responses=_RESPONSES,
    openapi_extra={"parameters": [_AUTH_PARAMETER, _IF_NONE_MATCH_PARAMETER]},
    tags=["owner AI activity"],
)
def ai_runs(
    request: Request,
    response: Response,
    principal: StudioReader,
    cursor: Annotated[str | None, Query(max_length=_MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_SIZE)] = _DEFAULT_PAGE_SIZE,
) -> AIRunPage | Response:
    _reject_unknown_or_duplicate_query(request)
    after = _decode_after(cursor, principal.tenant_key)
    predicate = "WHERE r.id < ?" if after is not None else ""
    params = (after, limit + 1) if after is not None else (limit + 1,)
    rows = db.all_(
        f"""SELECT r.id, r.capability, r.provider, r.status, r.review,
                   r.latency_ms, r.cost_usd, r.tokens, r.subject_type,
                   r.created_at
              FROM ai_runs r
              {predicate}
              ORDER BY r.id DESC
              LIMIT ?""",
        params,
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = None
    if has_more and visible:
        next_cursor = gallery_reads._encode_cursor(
            _cursor_kind(principal.tenant_key),
            (int(visible[-1]["id"]),),
        )
    page = AIRunPage(
        items=[_item(row) for row in visible],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    digest = hashlib.sha256(
        json.dumps(
            {
                "representation_version": _REPRESENTATION_VERSION,
                "page": page.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    etag = f'"ai-runs-v{_REPRESENTATION_VERSION}-{digest[:32]}"'
    headers = _private_headers(etag)
    if gallery_reads._etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    for key, value in headers.items():
        response.headers[key] = value
    return page
