"""Native owner caption workspace and immutable AI suggestion operations.

Caption reads and manual saves are first-class mobile studio operations. AI
generation is separately flag-gated and asynchronous: a worker may create a
short-lived suggestion, but it can never mutate the caption. Only a later,
version-checked owner save can copy a reviewed suggestion into an editable draft.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import sqlite3
import unicodedata
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import Field, field_validator, model_validator

from . import ai_runs, audit, caption_ai, config, db, jobs, mobile_auth, providers, runtime_identity
from . import mobile_gallery_calendar_api as gallery_reads
from . import mobile_owner_mutation_api as mutations
from .mobile_api_schemas import APIProblem, MobileAPIModel

router = APIRouter()
log = logging.getLogger("mise.mobile_content")

_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100
_MAX_CURSOR_LENGTH = 1_024
_MAX_BODY_CHARACTERS = 100_000
_MAX_NOTE_CHARACTERS = 20_000
_MAX_INSTRUCTION_CHARACTERS = 1_000
_MAX_CANDIDATE_CHARACTERS = 10_000
_INT64_MAX = 2**63 - 1
_PRIVATE_REVALIDATE = "private, no-cache"
_REPRESENTATION_VERSION = 1
_BIDI_CONTROLS = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)

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
    "description": "Strong ETag from the current caption detail.",
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
    value: dict[str, object] = {
        "model": APIProblem,
        "description": description,
        "content": {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/APIProblem"},
            }
        },
    }
    if retry_after:
        value["headers"] = {
            "Retry-After": {
                "description": "Seconds before another request should be attempted.",
                "schema": {"type": "integer", "minimum": 1},
            }
        }
    return value


_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": {
        "description": "Private response that must be revalidated before reuse.",
        "schema": {"type": "string", "const": _PRIVATE_REVALIDATE},
    },
    "ETag": {
        "description": "Strong validator for this normalized caption representation.",
        "schema": {"type": "string"},
    },
    "Vary": {
        "description": "Prevents reuse across owner bearer sessions.",
        "schema": {"type": "string", "const": "Authorization"},
    },
}
_NO_STORE_RESPONSE_HEADERS = {
    "Cache-Control": {
        "description": "Sensitive response that must never be persisted by a cache.",
        "schema": {"type": "string", "const": "no-store"},
    },
    "Vary": {
        "description": "Prevents reuse across owner bearer sessions.",
        "schema": {"type": "string", "const": "Authorization"},
    },
}
_REPLAY_RESPONSE_HEADER = {
    "Idempotency-Replayed": {
        "description": "Present with value true when a prior command result is replayed.",
        "schema": {"type": "string", "const": "true"},
    }
}

_READ_RESPONSES = {
    200: {
        "description": "Normalized owner caption representation",
        "headers": _PRIVATE_RESPONSE_HEADERS,
    },
    304: {
        "description": "The private caption representation is unchanged",
        "headers": _PRIVATE_RESPONSE_HEADERS,
    },
    401: _problem_response("Authentication failed"),
    403: _problem_response("Exact studio owner scope required"),
    404: _problem_response("Caption not found"),
    422: _problem_response("Invalid path, limit, or cursor"),
    429: _problem_response("Rate limit exceeded", retry_after=True),
    500: _problem_response("A stored caption could not be projected safely"),
}
_SUGGESTION_ERRORS = {
    401: _problem_response("Authentication failed"),
    403: _problem_response("Studio owner write scope required"),
    404: _problem_response("Caption, suggestion, or feature not found"),
    409: _problem_response("Caption version or suggestion state conflict"),
    422: _problem_response("Header, body, or path validation failed"),
    429: _problem_response("Tenant generation limit reached", retry_after=True),
    500: _problem_response("Suggestion operation failed safely"),
}
_WRITE_RESPONSES = {
    200: {
        "description": "Updated draft caption",
        "headers": {
            **_NO_STORE_RESPONSE_HEADERS,
            "ETag": {
                "description": "Strong validator for the updated caption detail.",
                "schema": {"type": "string"},
            },
            **_REPLAY_RESPONSE_HEADER,
        },
    },
    401: _problem_response("Authentication failed"),
    403: _problem_response("Studio owner write scope required"),
    404: _problem_response("Caption or suggestion not found"),
    409: _problem_response("Caption version, approval, or suggestion conflict"),
    422: _problem_response("Header, body, or path validation failed"),
    429: _problem_response("Rate limit exceeded", retry_after=True),
    500: _problem_response("Caption update failed safely"),
}
_SUGGESTION_CREATE_RESPONSES = {
    202: {
        "description": "Accepted immutable caption suggestion operation",
        "headers": {
            **_NO_STORE_RESPONSE_HEADERS,
            "Location": {
                "description": "Session-bound URL used to poll the operation.",
                "schema": {"type": "string"},
            },
            **_REPLAY_RESPONSE_HEADER,
        },
    },
    **_SUGGESTION_ERRORS,
}
_SUGGESTION_READ_RESPONSES = {
    200: {
        "description": "Current immutable caption suggestion state",
        "headers": _NO_STORE_RESPONSE_HEADERS,
    },
    **_SUGGESTION_ERRORS,
}


class ContentCaptionSummary(MobileAPIModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    version_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=0, le=_INT64_MAX)
    client_display_name: str = Field(min_length=1, max_length=500)
    plan_title: str = Field(min_length=1, max_length=1_000)
    period: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=500)
    body_preview: str = Field(max_length=320)
    status: Literal["draft", "approved"]
    ai_assisted: bool
    updated_at: dt.datetime


class ContentCaptionPage(MobileAPIModel):
    items: list[ContentCaptionSummary] = Field(default_factory=list, max_length=_MAX_PAGE_SIZE)
    next_cursor: str | None = Field(default=None, max_length=_MAX_CURSOR_LENGTH)
    has_more: bool
    suggestions_enabled: bool

    @model_validator(mode="after")
    def continuation_matches_flag(self) -> ContentCaptionPage:
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("caption continuation is inconsistent")
        return self


class ContentCaptionDetail(mutations.MobileWriteModel):
    id: int = Field(gt=0, le=_INT64_MAX)
    version_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=0, le=_INT64_MAX)
    client_display_name: str = Field(min_length=1, max_length=500)
    plan_id: int = Field(gt=0, le=_INT64_MAX)
    plan_title: str = Field(min_length=1, max_length=1_000)
    period: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=500)
    body: str = Field(max_length=_MAX_BODY_CHARACTERS)
    note: str | None = Field(default=None, max_length=_MAX_NOTE_CHARACTERS)
    status: Literal["draft", "approved"]
    ai_assisted: bool
    ai_drafted_at: dt.datetime | None = None
    suggestions_enabled: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class CaptionSuggestionRequest(mutations.MobileWriteRequest):
    instruction: str | None = Field(default=None, max_length=_MAX_INSTRUCTION_CHARACTERS)

    @field_validator("instruction")
    @classmethod
    def valid_instruction(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _validated_plain_text(
            value,
            maximum=_MAX_INSTRUCTION_CHARACTERS,
            allow_empty=True,
        )
        return cleaned or None


class CaptionBodyUpdate(mutations.MobileWriteRequest):
    body: str = Field(min_length=1, max_length=_MAX_BODY_CHARACTERS)
    suggestion_id: uuid.UUID | None = None

    @field_validator("body")
    @classmethod
    def valid_body(cls, value: str) -> str:
        return _validated_plain_text(
            value,
            maximum=_MAX_BODY_CHARACTERS,
            allow_empty=False,
        )


SuggestionState = Literal["queued", "running", "ready", "failed", "applied", "expired"]
SuggestionFailure = Literal[
    "disabled",
    "provider_error",
    "invalid_response",
    "session_ended",
    "unknown_outcome",
    "internal",
]


class CaptionSuggestion(mutations.MobileWriteModel):
    id: uuid.UUID
    caption_id: int = Field(gt=0, le=_INT64_MAX)
    state: SuggestionState
    review: Literal["human_review"] = "human_review"
    candidate_text: str | None = Field(default=None, max_length=_MAX_CANDIDATE_CHARACTERS)
    failure_reason: SuggestionFailure | None = None
    base_revision: int = Field(ge=0, le=_INT64_MAX)
    stale: bool
    created_at: dt.datetime
    expires_at: dt.datetime
    completed_at: dt.datetime | None = None

    @model_validator(mode="after")
    def payload_matches_state(self) -> CaptionSuggestion:
        if self.state == "ready":
            if self.candidate_text is None or self.failure_reason is not None:
                raise ValueError("ready suggestions require candidate text")
        elif self.candidate_text is not None:
            raise ValueError("only ready suggestions expose candidate text")
        if self.state == "failed":
            if self.failure_reason is None:
                raise ValueError("failed suggestions require a safe failure reason")
        elif self.failure_reason is not None:
            raise ValueError("only failed suggestions expose a failure reason")
        return self


StudioReader = Annotated[
    mobile_auth.Principal,
    Depends(gallery_reads.require_studio_owner),
]
StudioWriter = Annotated[
    mobile_auth.Principal,
    Depends(mutations.require_studio_writer),
]


def _validated_plain_text(value: str, *, maximum: int, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise ValueError("text must be a string")
    cleaned = unicodedata.normalize("NFC", value).strip()
    if not allow_empty and not cleaned:
        raise ValueError("text is required")
    if len(cleaned) > maximum:
        raise ValueError("text is too long")
    for character in cleaned:
        if character in _BIDI_CONTROLS:
            raise ValueError("text contains unsupported direction controls")
        category = unicodedata.category(character)
        if category in {"Cc", "Cs"} and character not in {"\n", "\t"}:
            raise ValueError("text contains unsupported control characters")
    return cleaned


def _stored_text(value: object, *, maximum: int) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    if len(text) > maximum:
        raise ValueError("stored caption text exceeds the mobile contract")
    return "".join(
        "\ufffd"
        if (
            character in _BIDI_CONTROLS
            or (unicodedata.category(character) in {"Cc", "Cs"} and character not in {"\n", "\t"})
        )
        else character
        for character in text
    )


def _required_stored(value: object, *, maximum: int, fallback: str) -> str:
    cleaned = _stored_text(value, maximum=maximum).strip()
    return cleaned or fallback


def _timestamp(value: object) -> dt.datetime:
    parsed = mutations._sqlite_utc(value)
    if parsed is None:
        raise ValueError("stored caption timestamp is invalid")
    return parsed


def _reject_unknown_or_duplicate_query(request: Request, allowed: set[str]) -> None:
    seen: set[str] = set()
    for name, _value in request.query_params.multi_items():
        if name not in allowed or name in seen:
            raise mobile_auth.MobileAuthError(
                422,
                "request.validation_failed",
                "The query parameters are invalid.",
            )
        seen.add(name)


def _cursor_kind(tenant_key: str) -> str:
    if not config.SECRET_KEY:
        raise RuntimeError("MISE_SECRET_KEY is not set")
    binding = hmac.new(
        config.SECRET_KEY.encode(),
        b"mise-mobile-content-captions\0" + tenant_key.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"content-captions:{binding}"


def _decode_after(cursor: str | None, tenant_key: str) -> int | None:
    decoded = gallery_reads._decode_cursor(cursor, _cursor_kind(tenant_key), (int,))
    if decoded is None:
        return None
    after = int(decoded[0])
    if not 1 <= after <= _INT64_MAX:
        raise gallery_reads._cursor_problem()
    return after


def _daily_limit() -> int:
    return max(0, min(int(config.MOBILE_CONTENT_DAILY_LIMIT), 10_000))


def _concurrent_limit() -> int:
    return max(0, min(int(config.MOBILE_CONTENT_CONCURRENT_LIMIT), 100))


def _ttl_hours() -> int:
    return max(1, min(int(config.MOBILE_CONTENT_SUGGESTION_TTL_HOURS), 168))


def _suggestions_enabled() -> bool:
    return bool(
        config.MOBILE_CONTENT_SUGGESTIONS
        and caption_ai.is_enabled()
        and _daily_limit() > 0
        and _concurrent_limit() > 0
    )


_CAPTION_SELECT = """SELECT rc.id, rc.plan_id, rc.period, rc.label, rc.body,
                            rc.status, rc.note, rc.ai_drafted, rc.ai_drafted_at,
                            rc.created_at, rc.updated_at, rc.revision,
                            rc.identity_token,
                            rp.title AS plan_title,
                            COALESCE(NULLIF(c.company,''), c.name) AS client_display_name
                       FROM retainer_captions rc
                       JOIN recurring_plans rp ON rp.id=rc.plan_id
                       JOIN projects p ON p.id=rp.project_id
                       JOIN clients c ON c.id=p.client_id"""


def _caption_row(con: sqlite3.Connection, caption_id: int) -> sqlite3.Row:
    row = con.execute(_CAPTION_SELECT + " WHERE rc.id=?", (caption_id,)).fetchone()
    if row is None:
        raise mobile_auth.MobileAuthError(404, "content.caption_not_found", "Caption not found.")
    return row


def _summary(row: sqlite3.Row) -> ContentCaptionSummary:
    body = _stored_text(row["body"], maximum=_MAX_BODY_CHARACTERS)
    preview = " ".join(body.split())
    if len(preview) > 320:
        preview = preview[:319].rstrip() + "…"
    updated = row["updated_at"] or row["created_at"]
    return ContentCaptionSummary(
        id=int(row["id"]),
        version_id=str(row["identity_token"]),
        revision=max(0, int(row["revision"])),
        client_display_name=_required_stored(
            row["client_display_name"],
            maximum=500,
            fallback="Client",
        ),
        plan_title=_required_stored(row["plan_title"], maximum=1_000, fallback="Retainer"),
        period=_required_stored(row["period"], maximum=32, fallback="Unscheduled"),
        label=_required_stored(row["label"], maximum=500, fallback="Caption"),
        body_preview=preview,
        status="approved" if row["status"] == "approved" else "draft",
        ai_assisted=bool(row["ai_drafted"]),
        updated_at=_timestamp(updated),
    )


def _detail(row: sqlite3.Row) -> ContentCaptionDetail:
    status: Literal["draft", "approved"] = "approved" if row["status"] == "approved" else "draft"
    updated = row["updated_at"] or row["created_at"]
    note = _stored_text(row["note"], maximum=_MAX_NOTE_CHARACTERS).strip()
    return ContentCaptionDetail(
        id=int(row["id"]),
        version_id=str(row["identity_token"]),
        revision=max(0, int(row["revision"])),
        client_display_name=_required_stored(
            row["client_display_name"],
            maximum=500,
            fallback="Client",
        ),
        plan_id=int(row["plan_id"]),
        plan_title=_required_stored(row["plan_title"], maximum=1_000, fallback="Retainer"),
        period=_required_stored(row["period"], maximum=32, fallback="Unscheduled"),
        label=_required_stored(row["label"], maximum=500, fallback="Caption"),
        body=_stored_text(row["body"], maximum=_MAX_BODY_CHARACTERS),
        note=note or None,
        status=status,
        ai_assisted=bool(row["ai_drafted"]),
        ai_drafted_at=(
            _timestamp(row["ai_drafted_at"]) if row["ai_drafted_at"] is not None else None
        ),
        suggestions_enabled=_suggestions_enabled() and status == "draft",
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(updated),
    )


def _caption_etag(value: ContentCaptionDetail) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "representation_version": _REPRESENTATION_VERSION,
                "caption": value.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return f'"content-caption-v{_REPRESENTATION_VERSION}-{value.revision}-{digest[:24]}"'


def _private_read_headers(etag: str) -> dict[str, str]:
    return {
        "Cache-Control": _PRIVATE_REVALIDATE,
        "ETag": etag,
        "Vary": "Authorization",
    }


def _no_store(response: Response, *, etag: str | None = None, replayed: bool = False) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    if etag is not None:
        response.headers["ETag"] = etag
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"


def _collection_stamp(con: sqlite3.Connection) -> dict[str, object]:
    row = con.execute(
        """SELECT COUNT(*) AS count,
                  COALESCE(MAX(id),0) AS max_id,
                  COALESCE(SUM(revision),0) AS revision_sum,
                  COALESCE(MAX(COALESCE(updated_at,created_at)),'') AS last_change
             FROM retainer_captions"""
    ).fetchone()
    audit_row = con.execute("SELECT COALESCE(MAX(id),0) AS max_id FROM audit_log").fetchone()
    return {
        "count": int(row["count"]),
        "max_id": int(row["max_id"]),
        "revision_sum": int(row["revision_sum"]),
        "last_change": str(row["last_change"]),
        "audit_revision": int(audit_row["max_id"]),
    }


def _page_etag(
    principal: mobile_auth.Principal,
    page: ContentCaptionPage,
    stamp: dict[str, object],
) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "representation_version": _REPRESENTATION_VERSION,
                "tenant_binding": hashlib.sha256(principal.tenant_key.encode()).hexdigest(),
                "collection": stamp,
                "page": page.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return f'"content-captions-v{_REPRESENTATION_VERSION}-{digest[:32]}"'


def _expire_suggestions(con: sqlite3.Connection) -> bool:
    usage_finished = con.execute(
        """UPDATE mobile_caption_usage
              SET state='finished',finished_at=COALESCE(finished_at,datetime('now'))
            WHERE state='active' AND expires_at <= datetime('now')"""
    ).rowcount
    usage_deleted = con.execute(
        """DELETE FROM mobile_caption_usage
            WHERE state='finished' AND accepted_at < datetime('now','-8 days')"""
    ).rowcount
    scrubbed = con.execute(
        """UPDATE mobile_caption_suggestions
              SET status='expired', context_json=NULL, candidate_text=NULL,
                  provider=NULL, model=NULL, failure_code=NULL,
                  completed_at=COALESCE(completed_at, datetime('now'))
            WHERE status IN ('queued','running','ready','failed')
              AND expires_at <= datetime('now')"""
    ).rowcount
    if scrubbed:
        log.info("mobile caption suggestions expired and scrubbed (count=%d)", scrubbed)
    deleted = con.execute(
        """DELETE FROM mobile_caption_suggestions
            WHERE status IN ('expired','applied','failed')
              AND expires_at < datetime('now','-7 days')"""
    ).rowcount
    return bool(usage_finished or usage_deleted or scrubbed or deleted)


def sweep_expired_suggestions() -> None:
    """Scrub expired provider context/output under the active tenant runtime."""
    with mutations._immediate_transaction() as con:
        changed = _expire_suggestions(con)
    if changed and not db.checkpoint_truncate():
        log.warning("mobile caption cleanup WAL checkpoint remains busy")


def _fail_suggestion(
    con: sqlite3.Connection,
    suggestion_id: str,
    reason: SuggestionFailure,
    *,
    clear_session: bool = False,
) -> None:
    _finish_usage(con, suggestion_id)
    changed = con.execute(
        """UPDATE mobile_caption_suggestions
              SET session_id=CASE WHEN ? THEN NULL ELSE session_id END,
                  status='failed', context_json=NULL, candidate_text=NULL,
                  provider=NULL, model=NULL, failure_code=?,
                  completed_at=COALESCE(completed_at, datetime('now'))
            WHERE id=? AND status IN ('queued','running','ready','failed')""",
        (int(clear_session), reason, suggestion_id),
    ).rowcount
    if changed:
        log.info("mobile caption suggestion reached safe terminal state (reason=%s)", reason)


def _finish_usage(con: sqlite3.Connection, suggestion_id: str) -> None:
    con.execute(
        """UPDATE mobile_caption_usage
              SET state='finished',finished_at=COALESCE(finished_at,datetime('now'))
            WHERE id=? AND state='active'""",
        (suggestion_id,),
    )


def _safe_provenance_result(
    result: providers.ProviderResult,
) -> providers.ProviderResult:
    """Strip untrusted raw provider errors before append-only provenance."""

    return providers.ProviderResult(
        capability=result.capability,
        provider=str(result.provider)[:200],
        status=result.status,
        review=result.review,
        output=None,
        model=(str(result.model)[:200] if result.model is not None else None),
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        tokens=result.tokens,
        error=(None if result.ok else result.status.value),
    )


def _suggestion_row(
    con: sqlite3.Connection,
    caption_id: int,
    suggestion_id: str,
    session_id: str,
) -> sqlite3.Row:
    _expire_suggestions(con)
    row = con.execute(
        """SELECT s.*, j.status AS job_status,
                  rc.revision AS current_revision, rc.status AS current_caption_status
             FROM mobile_caption_suggestions s
             JOIN retainer_captions rc ON rc.id=s.caption_id
             LEFT JOIN jobs j ON j.id=s.job_id
            WHERE s.id=? AND s.caption_id=? AND s.session_id=?""",
        (suggestion_id, caption_id, session_id),
    ).fetchone()
    if row is None:
        raise mobile_auth.MobileAuthError(
            404,
            "content.suggestion_not_found",
            "Suggestion not found.",
        )
    if row["status"] in {"queued", "running"} and row["job_status"] in {"failed", "done"}:
        reason: SuggestionFailure = (
            "unknown_outcome" if row["provider_attempted_at"] is not None else "internal"
        )
        _fail_suggestion(con, suggestion_id, reason)
        row = con.execute(
            """SELECT s.*, j.status AS job_status,
                      rc.revision AS current_revision,
                      rc.status AS current_caption_status
                 FROM mobile_caption_suggestions s
                 JOIN retainer_captions rc ON rc.id=s.caption_id
                 LEFT JOIN jobs j ON j.id=s.job_id
                WHERE s.id=? AND s.caption_id=? AND s.session_id=?""",
            (suggestion_id, caption_id, session_id),
        ).fetchone()
    return row


def _suggestion_value(row: sqlite3.Row) -> CaptionSuggestion:
    state: SuggestionState = str(row["status"])
    stale = bool(
        state == "ready"
        and (
            int(row["current_revision"]) != int(row["base_revision"])
            or row["current_caption_status"] != "draft"
        )
    )
    return CaptionSuggestion(
        id=uuid.UUID(str(row["id"])),
        caption_id=int(row["caption_id"]),
        state=state,
        candidate_text=(
            _stored_text(row["candidate_text"], maximum=_MAX_CANDIDATE_CHARACTERS)
            if state == "ready"
            else None
        ),
        failure_reason=str(row["failure_code"]) if state == "failed" else None,
        base_revision=int(row["base_revision"]),
        stale=stale,
        created_at=_timestamp(row["created_at"]),
        expires_at=_timestamp(row["expires_at"]),
        completed_at=(_timestamp(row["completed_at"]) if row["completed_at"] is not None else None),
    )


def _provider_failure(result: providers.ProviderResult) -> SuggestionFailure:
    if result.status is providers.ResultStatus.DISABLED:
        return "disabled"
    if result.status is providers.ResultStatus.INVALID_RESPONSE:
        return "invalid_response"
    return "provider_error"


_RuntimeUnavailable = runtime_identity.RuntimeUnavailable
_runtime_identity = runtime_identity.current
_bound_runtime_transaction = runtime_identity.bound_transaction


def _require_current_mobile_session(
    con: sqlite3.Connection,
    principal: mobile_auth.Principal,
) -> None:
    if not mobile_auth.session_is_current(con, principal.session_id):
        raise mobile_auth.MobileAuthError(
            401,
            "auth.invalid_token",
            "The token is invalid or expired.",
        )


def run_caption_suggestion(suggestion_id: str) -> None:
    """Run one provider attempt under the tenant context restored by jobs.py.

    A queued-to-running compare-and-set is the paid-call claim. If a process dies
    after that claim, startup may requeue the generic job, but this handler turns
    the ambiguous running operation into unknown_outcome without calling the
    provider twice.
    """

    database_path = db.current_db_path().resolve()
    with mutations._immediate_transaction() as con:
        runtime_identity = _runtime_identity(con)
        if runtime_identity is None:
            return
        _expire_suggestions(con)
        row = con.execute(
            "SELECT * FROM mobile_caption_suggestions WHERE id=?",
            (suggestion_id,),
        ).fetchone()
        if row is None:
            _finish_usage(con, suggestion_id)
            return
        if row["status"] in {"ready", "failed", "applied", "expired"}:
            _finish_usage(con, suggestion_id)
            return
        if row["status"] == "running":
            _fail_suggestion(con, suggestion_id, "unknown_outcome")
            return
        session_id = str(row["session_id"] or "")
        if not session_id or not mobile_auth.session_is_current(con, session_id):
            _fail_suggestion(con, suggestion_id, "session_ended", clear_session=True)
            return
        if not _suggestions_enabled():
            _fail_suggestion(con, suggestion_id, "disabled")
            return
        try:
            context = json.loads(str(row["context_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            _fail_suggestion(con, suggestion_id, "internal")
            return
        if not isinstance(context, dict):
            _fail_suggestion(con, suggestion_id, "internal")
            return
        claimed = con.execute(
            """UPDATE mobile_caption_suggestions
                  SET status='running', provider_attempted_at=datetime('now')
                WHERE id=? AND status='queued'""",
            (suggestion_id,),
        ).rowcount
        if claimed != 1:
            return
        caption_id = int(row["caption_id"])

    # Recheck the exact database, session, row state, and kill switch immediately
    # before resolving/calling the provider, without holding a write lock across
    # the outbound request. An offboarding race can still let the paid call finish,
    # but the identity-bound final transaction below can never persist its result.
    try:
        with _bound_runtime_transaction(database_path, runtime_identity) as con:
            row = con.execute(
                "SELECT * FROM mobile_caption_suggestions WHERE id=?",
                (suggestion_id,),
            ).fetchone()
            if row is None or row["status"] != "running":
                _finish_usage(con, suggestion_id)
                return
            session_id = str(row["session_id"] or "")
            if not session_id or not mobile_auth.session_is_current(con, session_id):
                _fail_suggestion(con, suggestion_id, "session_ended", clear_session=True)
                return
            if not _suggestions_enabled():
                _fail_suggestion(con, suggestion_id, "disabled")
                return
    except _RuntimeUnavailable:
        return

    try:
        result = providers.resolve(providers.Capability.CONTENT).draft(
            context,
            idempotency_key=suggestion_id,
        )
    except Exception:
        result = providers.ProviderResult.failure(
            providers.Capability.CONTENT,
            "odysseus",
            providers.ResultStatus.PROVIDER_ERROR,
            "caption provider invocation failed",
        )

    candidate: str | None = None
    if result.ok:
        try:
            output = result.output or {}
            if not isinstance(output, dict):
                raise ValueError("caption provider output must be an object")
            candidate = _validated_plain_text(
                output.get("caption"),
                maximum=_MAX_CANDIDATE_CHARACTERS,
                allow_empty=False,
            )
        except (TypeError, ValueError):
            result = providers.ProviderResult.failure(
                providers.Capability.CONTENT,
                result.provider,
                providers.ResultStatus.INVALID_RESPONSE,
                "caption provider returned an invalid suggestion",
                latency_ms=result.latency_ms,
            )
            candidate = None

    try:
        with _bound_runtime_transaction(database_path, runtime_identity) as con:
            _expire_suggestions(con)
            row = con.execute(
                "SELECT * FROM mobile_caption_suggestions WHERE id=?",
                (suggestion_id,),
            ).fetchone()
            if row is None or row["status"] != "running" or int(row["caption_id"]) != caption_id:
                _finish_usage(con, suggestion_id)
                return
            session_id = str(row["session_id"] or "")
            if not session_id or not mobile_auth.session_is_current(con, session_id):
                _fail_suggestion(con, suggestion_id, "session_ended", clear_session=True)
                return
            if not _suggestions_enabled():
                _fail_suggestion(con, suggestion_id, "disabled")
                return
            try:
                ai_runs.record(
                    _safe_provenance_result(result),
                    subject_type="retainer_caption",
                    subject_id=caption_id,
                    idempotency_key=suggestion_id,
                    connection=con,
                )
            except Exception:
                _fail_suggestion(con, suggestion_id, "internal")
                return
            if result.ok and candidate is not None:
                con.execute(
                    """UPDATE mobile_caption_suggestions
                          SET status='ready', context_json=NULL, candidate_text=?,
                              provider=?, model=?, failure_code=NULL,
                              completed_at=datetime('now')
                        WHERE id=? AND status='running'""",
                    (
                        candidate,
                        str(result.provider)[:200],
                        str(result.model or "unknown")[:200],
                        suggestion_id,
                    ),
                )
                _finish_usage(con, suggestion_id)
            else:
                _fail_suggestion(con, suggestion_id, _provider_failure(result))
    except _RuntimeUnavailable:
        return


@router.get(
    "/content/captions",
    response_model=ContentCaptionPage,
    responses=_READ_RESPONSES,
    openapi_extra={"parameters": [_AUTH_PARAMETER, _IF_NONE_MATCH_PARAMETER]},
    tags=["owner content"],
)
def captions(
    request: Request,
    response: Response,
    principal: StudioReader,
    cursor: Annotated[str | None, Query(max_length=_MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_SIZE)] = _DEFAULT_PAGE_SIZE,
) -> ContentCaptionPage | Response:
    _reject_unknown_or_duplicate_query(request, {"cursor", "limit"})
    after = _decode_after(cursor, principal.tenant_key)
    predicate = "WHERE rc.id < ?" if after is not None else ""
    params = (after, limit + 1) if after is not None else (limit + 1,)
    con = db.connect()
    try:
        rows = con.execute(
            _CAPTION_SELECT + f" {predicate} ORDER BY rc.id DESC LIMIT ?",
            params,
        ).fetchall()
        stamp = _collection_stamp(con)
    finally:
        con.close()
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = None
    if has_more and visible:
        next_cursor = gallery_reads._encode_cursor(
            _cursor_kind(principal.tenant_key),
            (int(visible[-1]["id"]),),
        )
    page = ContentCaptionPage(
        items=[_summary(row) for row in visible],
        next_cursor=next_cursor,
        has_more=has_more,
        suggestions_enabled=_suggestions_enabled(),
    )
    etag = _page_etag(principal, page, stamp)
    headers = _private_read_headers(etag)
    if gallery_reads._etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    for name, value in headers.items():
        response.headers[name] = value
    return page


@router.get(
    "/content/captions/{caption_id}",
    response_model=ContentCaptionDetail,
    responses=_READ_RESPONSES,
    openapi_extra={"parameters": [_AUTH_PARAMETER, _IF_NONE_MATCH_PARAMETER]},
    tags=["owner content"],
)
def caption_detail(
    request: Request,
    response: Response,
    _principal: StudioReader,
    caption_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
) -> ContentCaptionDetail | Response:
    _reject_unknown_or_duplicate_query(request, set())
    con = db.connect()
    try:
        value = _detail(_caption_row(con, caption_id))
    finally:
        con.close()
    etag = _caption_etag(value)
    headers = _private_read_headers(etag)
    if gallery_reads._etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    for name, header_value in headers.items():
        response.headers[name] = header_value
    return value


@router.post(
    "/content/captions/{caption_id}/suggestions",
    response_model=CaptionSuggestion,
    status_code=202,
    responses=_SUGGESTION_CREATE_RESPONSES,
    openapi_extra={"parameters": [_AUTH_PARAMETER, _IF_MATCH_PARAMETER, _IDEMPOTENCY_PARAMETER]},
    tags=["owner content"],
)
def create_caption_suggestion(
    request: Request,
    response: Response,
    body: CaptionSuggestionRequest,
    principal: StudioWriter,
    caption_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
) -> CaptionSuggestion:
    _reject_unknown_or_duplicate_query(request, set())
    job_id_to_kick: int | None = None
    with mutations._immediate_transaction() as con:
        _require_current_mobile_session(con, principal)
        claim = mutations._claim_command(
            con,
            request,
            principal,
            f"content.caption.suggest:{caption_id}",
            mutations._request_payload(body, request, include_match=True),
        )
        if claim.replayed:
            value = mutations._replay(claim, CaptionSuggestion)
        else:
            if not _suggestions_enabled():
                raise mobile_auth.MobileAuthError(
                    404,
                    "content.suggestions_disabled",
                    "Caption suggestions are not available.",
                )
            caption_row = _caption_row(con, caption_id)
            detail = _detail(caption_row)
            if detail.status != "draft":
                raise mobile_auth.MobileAuthError(
                    409,
                    "content.caption_not_editable",
                    "Approved captions must be reopened before generating a suggestion.",
                )
            mutations._require_current(request, _caption_etag(detail))
            _expire_suggestions(con)
            active_for_caption = con.execute(
                """SELECT 1 FROM mobile_caption_suggestions
                    WHERE caption_id=? AND status IN ('queued','running')""",
                (caption_id,),
            ).fetchone()
            if active_for_caption is not None:
                raise mobile_auth.MobileAuthError(
                    409,
                    "content.suggestion_in_progress",
                    "A suggestion is already being generated for this caption.",
                )
            active_count = con.execute(
                """SELECT COUNT(*) AS n FROM mobile_caption_usage
                    WHERE state='active' AND expires_at > datetime('now')"""
            ).fetchone()["n"]
            if int(active_count) >= _concurrent_limit():
                log.info("mobile caption suggestion quota denied (kind=concurrent)")
                raise mobile_auth.MobileAuthError(
                    429,
                    "content.concurrent_limit",
                    "The studio generation queue is full.",
                    retry_after=30,
                )
            daily_count = con.execute(
                """SELECT COUNT(*) AS n FROM mobile_caption_usage
                    WHERE accepted_at >= datetime('now','-1 day')"""
            ).fetchone()["n"]
            if int(daily_count) >= _daily_limit():
                log.info("mobile caption suggestion quota denied (kind=daily)")
                raise mobile_auth.MobileAuthError(
                    429,
                    "content.daily_limit",
                    "The studio caption-generation limit has been reached.",
                    retry_after=3_600,
                )

            suggestion_id = str(uuid.uuid4())
            expires_at = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=_ttl_hours())).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            context = {
                "label": detail.label[:500],
                "period": detail.period[:32],
            }
            if body.instruction is not None:
                context["instruction"] = body.instruction
            con.execute(
                """INSERT INTO mobile_caption_usage (id,state,expires_at)
                   VALUES (?,'active',?)""",
                (suggestion_id, expires_at),
            )
            con.execute(
                """INSERT INTO mobile_caption_suggestions
                   (id,session_id,caption_id,base_revision,status,context_json,expires_at)
                   VALUES (?,?,?,?, 'queued', ?,?)""",
                (
                    suggestion_id,
                    principal.session_id,
                    caption_id,
                    detail.revision,
                    json.dumps(context, sort_keys=True, separators=(",", ":")),
                    expires_at,
                ),
            )
            job_id_to_kick = jobs.enqueue_in_transaction(
                con,
                "mobile_caption_suggestion",
                {"suggestion_id": suggestion_id},
            )
            con.execute(
                "UPDATE mobile_caption_suggestions SET job_id=? WHERE id=?",
                (job_id_to_kick, suggestion_id),
            )
            row = _suggestion_row(
                con,
                caption_id,
                suggestion_id,
                principal.session_id,
            )
            value = _suggestion_value(row)
            audit.log(
                con,
                "retainer_caption",
                caption_id,
                "suggestion_requested",
                actor="mobile_owner",
                diff={"suggestion_id": suggestion_id},
            )
            mutations._finish_command(
                con,
                principal,
                claim,
                value,
                status_code=202,
            )
    if job_id_to_kick is not None:
        jobs.kick(job_id_to_kick)
    _no_store(response, replayed=claim.replayed)
    response.headers["Location"] = f"/api/v1/content/captions/{caption_id}/suggestions/{value.id}"
    return value


@router.get(
    "/content/captions/{caption_id}/suggestions/{suggestion_id}",
    response_model=CaptionSuggestion,
    responses=_SUGGESTION_READ_RESPONSES,
    openapi_extra={"parameters": [_AUTH_PARAMETER]},
    tags=["owner content"],
)
def caption_suggestion(
    request: Request,
    response: Response,
    principal: StudioReader,
    caption_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
    suggestion_id: uuid.UUID,
) -> CaptionSuggestion:
    _reject_unknown_or_duplicate_query(request, set())
    with mutations._immediate_transaction() as con:
        row = _suggestion_row(
            con,
            caption_id,
            str(suggestion_id),
            principal.session_id,
        )
        value = _suggestion_value(row)
    _no_store(response)
    return value


@router.patch(
    "/content/captions/{caption_id}",
    response_model=ContentCaptionDetail,
    responses=_WRITE_RESPONSES,
    openapi_extra={"parameters": [_AUTH_PARAMETER, _IF_MATCH_PARAMETER, _IDEMPOTENCY_PARAMETER]},
    tags=["owner content"],
)
def update_caption(
    request: Request,
    response: Response,
    body: CaptionBodyUpdate,
    principal: StudioWriter,
    caption_id: Annotated[int, Path(ge=1, le=_INT64_MAX)],
) -> ContentCaptionDetail:
    _reject_unknown_or_duplicate_query(request, set())
    with mutations._immediate_transaction() as con:
        _require_current_mobile_session(con, principal)
        claim = mutations._claim_command(
            con,
            request,
            principal,
            f"content.caption.update:{caption_id}",
            mutations._request_payload(body, request, include_match=True),
        )
        if claim.replayed:
            value = mutations._replay(claim, ContentCaptionDetail)
        else:
            before_row = _caption_row(con, caption_id)
            before = _detail(before_row)
            if before.status != "draft":
                raise mobile_auth.MobileAuthError(
                    409,
                    "content.caption_not_editable",
                    "Approved captions must be reopened before editing.",
                )
            mutations._require_current(request, _caption_etag(before))
            suggestion_row: sqlite3.Row | None = None
            suggestion_original: str | None = None
            if body.suggestion_id is not None:
                suggestion_row = _suggestion_row(
                    con,
                    caption_id,
                    str(body.suggestion_id),
                    principal.session_id,
                )
                if suggestion_row["status"] != "ready":
                    raise mobile_auth.MobileAuthError(
                        409,
                        "content.suggestion_not_ready",
                        "This suggestion is no longer ready to use.",
                    )
                if (
                    int(suggestion_row["base_revision"]) != before.revision
                    or suggestion_row["current_caption_status"] != "draft"
                ):
                    raise mobile_auth.MobileAuthError(
                        409,
                        "content.suggestion_stale",
                        "The caption changed after this suggestion was generated.",
                    )
                suggestion_original = _stored_text(
                    suggestion_row["candidate_text"],
                    maximum=_MAX_CANDIDATE_CHARACTERS,
                )

            if suggestion_row is None:
                changed = con.execute(
                    """UPDATE retainer_captions
                          SET body=?, revision=revision+1, updated_at=datetime('now')
                        WHERE id=? AND status='draft' AND revision=?""",
                    (body.body, caption_id, before.revision),
                ).rowcount
            else:
                changed = con.execute(
                    """UPDATE retainer_captions
                          SET body=?, ai_drafted=1, ai_model=?,
                              ai_drafted_at=COALESCE(?,datetime('now')),
                              ai_draft_original=?, revision=revision+1,
                              updated_at=datetime('now')
                        WHERE id=? AND status='draft' AND revision=?""",
                    (
                        body.body,
                        str(suggestion_row["model"] or "unknown")[:200],
                        suggestion_row["completed_at"],
                        suggestion_original,
                        caption_id,
                        before.revision,
                    ),
                ).rowcount
            if changed != 1:
                raise mobile_auth.MobileAuthError(
                    409,
                    "resource.version_conflict",
                    "This caption changed on another device.",
                )
            if suggestion_row is not None:
                applied = con.execute(
                    """UPDATE mobile_caption_suggestions
                          SET status='applied', candidate_text=NULL, context_json=NULL,
                              provider=NULL, model=NULL, failure_code=NULL,
                              applied_at=datetime('now')
                        WHERE id=? AND caption_id=? AND session_id=? AND status='ready'""",
                    (
                        str(body.suggestion_id),
                        caption_id,
                        principal.session_id,
                    ),
                ).rowcount
                if applied != 1:
                    raise mobile_auth.MobileAuthError(
                        409,
                        "content.suggestion_not_ready",
                        "This suggestion is no longer ready to use.",
                    )
            value = _detail(_caption_row(con, caption_id))
            audit.log(
                con,
                "retainer_caption",
                caption_id,
                "body_updated",
                actor="mobile_owner",
                diff={
                    "field": "body",
                    "suggestion_id": (
                        str(body.suggestion_id) if body.suggestion_id is not None else None
                    ),
                },
            )
            mutations._finish_command(
                con,
                principal,
                claim,
                value,
                status_code=200,
            )
    _no_store(
        response,
        etag=_caption_etag(value),
        replayed=claim.replayed,
    )
    return value
