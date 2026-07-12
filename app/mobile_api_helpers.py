"""Shared primitives for the native `/api/v1` routers.

`mobile_owner_api`, `mobile_gallery_calendar_api`, and `mobile_client_api` had
independently grown copies of the same signed-cursor problem, ETag matcher, and
private-cache headers. This module is the single home for those, so the three
routers agree by construction (MISE-REVIEW §1 cleanup).

Wire formats are preserved exactly — this is a behavior-neutral extraction:

- The **keyset cursor codec** (`encode_keyset_cursor` / `decode_keyset_cursor`)
  is the JSON, domain-separated, full-SHA256-signature format used by the
  gallery/calendar and client collection routes.
- The owner router keeps its own distinct single-integer cursor codec (a
  different wire format); only its non-codec helpers are shared from here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Sequence

from fastapi import Response

from . import config, mobile_auth

MAX_CURSOR_LENGTH = 1024

_PRIVATE_REVALIDATE = "private, no-cache"
_CURSOR_DOMAIN = b"mise-mobile-pagination\0"


def require_secret_key() -> bytes:
    """The HMAC key for signing cursors. Raises if the deploy has no secret."""
    if not config.SECRET_KEY:
        raise RuntimeError("MISE_SECRET_KEY is not set")
    return config.SECRET_KEY.encode()


def cursor_problem() -> mobile_auth.MobileAuthError:
    return mobile_auth.MobileAuthError(
        422,
        "pagination.invalid_cursor",
        "The pagination cursor is invalid.",
    )


def encode_keyset_cursor(kind: str, values: Sequence[str | int]) -> str:
    """Sign an opaque keyset cursor carrying `values` for the `kind` namespace."""
    payload = json.dumps(
        {"v": 1, "kind": kind, "values": list(values)},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    signature = hmac.new(require_secret_key(), _CURSOR_DOMAIN + payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def decode_keyset_cursor(
    cursor: str | None, kind: str, types: Sequence[type]
) -> list[str | int] | None:
    """Validate + decode a keyset cursor; raise `cursor_problem` on any mismatch."""
    if cursor is None:
        return None
    if not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        raise cursor_problem()
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        if len(raw) <= hashlib.sha256().digest_size or not config.SECRET_KEY:
            raise ValueError("invalid signed cursor")
        payload_bytes = raw[: -hashlib.sha256().digest_size]
        supplied_signature = raw[-hashlib.sha256().digest_size :]
        expected_signature = hmac.new(
            require_secret_key(), _CURSOR_DOMAIN + payload_bytes, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("invalid cursor signature")
        payload = json.loads(payload_bytes)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise cursor_problem() from exc
    if not isinstance(payload, dict) or set(payload) != {"v", "kind", "values"}:
        raise cursor_problem()
    values = payload["values"]
    if payload["v"] != 1 or payload["kind"] != kind or not isinstance(values, list):
        raise cursor_problem()
    if len(values) != len(types):
        raise cursor_problem()
    for value, expected in zip(values, types, strict=True):
        if expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise cursor_problem()
        elif not isinstance(value, expected):
            raise cursor_problem()
    return values


def _weak_value(value: str) -> str:
    value = value.strip()
    return value[2:].strip() if value.startswith("W/") else value


def etag_matches(header: str | None, etag: str) -> bool:
    """True when an `If-None-Match` header matches `etag`.

    Handles `*` and normalizes the weak (`W/`) prefix on BOTH the incoming
    header and the target etag — the owner routes issue weak etags
    (`W/"..."`), the gallery/client routes issue strong ones, and this matcher
    serves both.
    """
    if not header:
        return False
    target = _weak_value(etag)
    for candidate in header.split(","):
        stripped = candidate.strip()
        if stripped == "*" or _weak_value(stripped) == target:
            return True
    return False


def private_headers(etag: str | None = None) -> dict[str, str]:
    headers = {"Cache-Control": _PRIVATE_REVALIDATE, "Vary": "Authorization"}
    if etag is not None:
        headers["ETag"] = etag
    return headers


def set_private_headers(response: Response, etag: str | None = None) -> None:
    for key, value in private_headers(etag).items():
        response.headers[key] = value
