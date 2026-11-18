"""Deliberate owner email replies with session-bound delivery replay receipts."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Response
from pydantic import Field

from . import audit, db, mailer, mobile_auth, mobile_idempotency
from .mobile_api_helpers import require_secret_key
from .mobile_owner_api import (
    InquiryDetail,
    OwnerAPIModel,
    _inquiry_detail_payload,
    _inquiry_record,
    _require_studio_write,
    require_studio_owner,
)

router = APIRouter(dependencies=[Depends(require_studio_owner)], tags=["owner inquiries"])

_REPLY_COMMAND = "inquiry.reply"
_REPLY_SUBJECT_MAX = 200
_REPLY_BODY_MAX = 100_000


class InquiryReplyRequest(OwnerAPIModel):
    channel: Literal["email"] = "email"
    subject: str = Field(min_length=1, max_length=_REPLY_SUBJECT_MAX)
    body: str = Field(min_length=1, max_length=_REPLY_BODY_MAX)


class InquiryReplyResult(OwnerAPIModel):
    code: Literal["inquiry.reply_sent", "inquiry.delivery_unknown"]
    delivery_status: Literal["sent", "unknown"]
    message_id: int | None = None
    detail: InquiryDetail


def _reply_key_hash(key: str) -> str:
    return hmac.new(
        require_secret_key(),
        b"mise-inquiry-reply-idempotency\0" + key.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _reply_request_hash(inquiry_id: int, subject: str, body: str) -> str:
    canonical = json.dumps(
        {"inquiry_id": inquiry_id, "channel": "email", "subject": subject, "body": body},
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hmac.new(
        require_secret_key(),
        b"mise-inquiry-reply-request\0" + canonical.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _reply_message_id(session_id: str, inquiry_id: int, key: str) -> str:
    digest = hmac.new(
        require_secret_key(),
        f"mise-inquiry-reply-message\0{session_id}\0{inquiry_id}\0{key}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"<inquiry-reply-{digest}@focal.local>"


def _result(
    inquiry_id: int,
    *,
    code: str,
    delivery_status: str,
    message_id: int | None,
) -> InquiryReplyResult:
    con = db.connect()
    try:
        con.execute("BEGIN")
        detail = _inquiry_detail_payload(_inquiry_record(inquiry_id, con=con), con=con)
    finally:
        con.close()
    return InquiryReplyResult(
        code=code,
        delivery_status=delivery_status,
        message_id=message_id,
        detail=detail,
    )


def _unknown(inquiry_id: int) -> InquiryReplyResult:
    return _result(
        inquiry_id, code="inquiry.delivery_unknown", delivery_status="unknown", message_id=None
    )


def _send_reply(
    inquiry_id: int,
    *,
    principal: mobile_auth.Principal,
    idempotency_key: str,
    request: InquiryReplyRequest,
) -> tuple[InquiryReplyResult, int]:
    subject = request.subject.strip()
    body = request.body.strip()
    if not subject or not body:
        raise mobile_auth.MobileAuthError(
            422,
            "request.validation_failed",
            "Subject and body are required.",
        )
    key_hash = _reply_key_hash(idempotency_key)
    request_hash = _reply_request_hash(inquiry_id, subject, body)
    message_id = _reply_message_id(principal.session_id, inquiry_id, idempotency_key)

    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        now_ts = mobile_idempotency.now_ts()
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
                 FROM api_idempotency_replays WHERE session_id=? AND key_hash=?""",
            (principal.session_id, key_hash),
        ).fetchone()
        if replay is not None:
            if replay["command_kind"] != _REPLY_COMMAND or replay["request_hash"] != request_hash:
                raise mobile_auth.MobileAuthError(
                    409,
                    "idempotency.key_conflict",
                    "This idempotency key was already used for another reply.",
                )
            status = int(replay["response_status"])
            if status not in (201, 202):
                raise RuntimeError("unsupported inquiry reply replay status")
            metadata = json.loads(replay["response_json"])
            con.commit()
            result = _result(
                inquiry_id,
                code=metadata["code"],
                delivery_status=metadata["delivery_status"],
                message_id=metadata.get("message_id"),
            )
            return result, status

        inquiry = con.execute(
            "SELECT id, email, converted_project_id FROM inquiries WHERE id=?",
            (inquiry_id,),
        ).fetchone()
        if inquiry is None:
            raise mobile_auth.MobileAuthError(404, "inquiry.not_found", "Inquiry not found.")
        recipient = str(inquiry["email"] or "").strip()
        if not recipient:
            raise mobile_auth.MobileAuthError(
                422,
                "inquiry.recipient_missing",
                "This inquiry has no email address.",
            )
        if not mailer.configured():
            raise mobile_auth.MobileAuthError(
                503,
                "inquiry.delivery_unavailable",
                "Email delivery is not configured.",
            )
        con.execute(
            """INSERT INTO api_idempotency_replays
               (session_id, key_hash, command_kind, request_hash,
                response_status, response_json, created_at, expires_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                principal.session_id,
                key_hash,
                _REPLY_COMMAND,
                request_hash,
                202,
                json.dumps(
                    {
                        "code": "inquiry.delivery_unknown",
                        "delivery_status": "unknown",
                        "message_id": None,
                    }
                ),
                now_ts,
                int(session["absolute_expires_at"]),
            ),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    try:
        mailer.send(recipient, subject, body, message_id=message_id)
    except Exception:
        return _unknown(inquiry_id), 202

    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        replay = con.execute(
            """SELECT response_status FROM api_idempotency_replays
                 WHERE session_id=? AND key_hash=? AND command_kind=? AND request_hash=?""",
            (principal.session_id, key_hash, _REPLY_COMMAND, request_hash),
        ).fetchone()
        if replay is None:
            con.rollback()
            return _unknown(inquiry_id), 202
        if int(replay["response_status"]) == 201:
            metadata = con.execute(
                "SELECT response_json FROM api_idempotency_replays WHERE session_id=? AND key_hash=?",
                (principal.session_id, key_hash),
            ).fetchone()
            payload = json.loads(metadata["response_json"])
            con.commit()
            result = _result(
                inquiry_id,
                code=payload["code"],
                delivery_status=payload["delivery_status"],
                message_id=payload.get("message_id"),
            )
            return result, 201
        if int(replay["response_status"]) != 202:
            raise RuntimeError("unsupported inquiry reply replay status")

        con.execute(
            """INSERT INTO emails_log (project_id, doc_kind, doc_id, to_email, subject)
               VALUES (?, 'other', ?, ?, ?)""",
            (inquiry["converted_project_id"], inquiry_id, recipient, subject),
        )
        message_db_id = int(
            con.execute(
                """INSERT INTO messages (inquiry_id, direction, channel, body, provider_msg_id)
                   VALUES (?, 'out', 'email', ?, ?)""",
                (inquiry_id, body, message_id),
            ).lastrowid
        )
        con.execute("UPDATE inquiries SET emailed=1 WHERE id=?", (inquiry_id,))
        audit.log(
            con,
            "inquiry",
            inquiry_id,
            "reply",
            diff={"channel": "email", "message_id": message_db_id},
            actor="owner",
        )
        metadata = json.dumps(
            {
                "code": "inquiry.reply_sent",
                "delivery_status": "sent",
                "message_id": message_db_id,
            }
        )
        con.execute(
            """UPDATE api_idempotency_replays
                  SET response_status=201, response_json=?
                WHERE session_id=? AND key_hash=?""",
            (metadata, principal.session_id, key_hash),
        )
        detail = _inquiry_detail_payload(_inquiry_record(inquiry_id, con=con), con=con)
        con.commit()
        result = InquiryReplyResult(
            code="inquiry.reply_sent",
            delivery_status="sent",
            message_id=message_db_id,
            detail=detail,
        )
        return result, 201
    except Exception:
        con.rollback()
        return _unknown(inquiry_id), 202
    finally:
        con.close()


@router.post(
    "/inquiries/{inquiry_id}/reply",
    response_model=InquiryReplyResult,
    status_code=201,
    responses={
        201: {"model": InquiryReplyResult, "description": "SMTP accepted and the reply committed"},
        202: {"model": InquiryReplyResult, "description": "Delivery outcome is unknown"},
    },
)
def reply_to_inquiry(
    inquiry_id: Annotated[int, Path(ge=1, le=2**63 - 1)],
    request: InquiryReplyRequest,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    principal: Annotated[mobile_auth.Principal, Depends(require_studio_owner)],
) -> InquiryReplyResult:
    _require_studio_write(principal)
    result, status = _send_reply(
        inquiry_id,
        principal=principal,
        idempotency_key=str(idempotency_key),
        request=request,
    )
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return result
