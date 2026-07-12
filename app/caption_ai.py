"""Mesh client for Odysseus's caption-drafting brain (Domain G slice 6b).

Mise does NOT pick models or route — it hands context to Odysseus and takes back
one caption plus the model name Odysseus used. The call crosses the tailnet mesh,
so failure is EXPECTED, not exceptional: every failure mode (feature off, timeout,
connection refused, bad/empty response) raises CaptionDraftError, and the caller
leaves body/status untouched and writes nothing. There are no partial drafts.

The Odysseus-side endpoint (POST returning {"caption","model"}) is a separate,
independently-deployed change to the Odysseus CRM; until it exists this degrades
cleanly (MISE_ODYSSEUS_CAPTION_URL unset -> CaptionDraftError "not configured").
"""

import json
import logging
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterable
from typing import NoReturn

from . import config, features

log = logging.getLogger("mise.caption_ai")

_MAX_REQUEST_BYTES = 32 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_CAPTION_SCALARS = 10_000
_MAX_CAPTION_BYTES = 20 * 1024
_MAX_MODEL_SCALARS = 200
_CONTEXT_LIMITS = {
    "label": 500,
    "note": 4_000,
    "client": 500,
    "period": 32,
    "plan_title": 1_000,
    "instruction": 4_000,
}
_BIDI_CONTROLS = {
    "\u061c",  # Arabic letter mark
    "\u200e",  # left-to-right mark
    "\u200f",  # right-to-left mark
    "\u202a",  # left-to-right embedding
    "\u202b",  # right-to-left embedding
    "\u202c",  # pop directional formatting
    "\u202d",  # left-to-right override
    "\u202e",  # right-to-left override
    "\u2066",  # left-to-right isolate
    "\u2067",  # right-to-left isolate
    "\u2068",  # first-strong isolate
    "\u2069",  # pop directional isolate
}
_ALLOWED_FORMAT_CHARACTERS = {"\u200c", "\u200d"}  # ZWNJ/ZWJ, including emoji sequences

_INVALID_REQUEST = "AI drafting request is invalid"
_PROVIDER_UNAVAILABLE = "AI drafting provider is unavailable"
_INVALID_RESPONSE = "AI drafting provider returned an invalid response"


class CaptionDraftError(Exception):
    """Any reason a draft could not be produced. Carries a human-readable message
    safe to surface in the admin UI (no secrets, no stack)."""

    def __init__(self, message: str, *, provider_attempted: bool | None = None):
        super().__init__(message)
        self.invalid_response = message == _INVALID_RESPONSE
        # Callers use this only to decide whether a durable at-most-once claim may
        # be released. A transport error or malformed provider response is
        # outcome-ambiguous: the provider may already have spent work even though
        # Mise did not receive a usable response. Configuration/request failures
        # happen before the socket is opened and are safe to retry with a new claim.
        if provider_attempted is None:
            provider_attempted = message in {_PROVIDER_UNAVAILABLE, _INVALID_RESPONSE}
        self.provider_attempted = provider_attempted


def is_enabled() -> bool:
    """AI drafting is armed only when BOTH the endpoint URL and the bearer token are
    configured. Either unset -> the "Draft with AI" button stays cleanly dormant
    (the route greys it out; a direct call raises CaptionDraftError, never crashes)."""
    return features.odysseus_caption_enabled() and _provider_url() is not None


def _fail(message: str) -> NoReturn:
    raise CaptionDraftError(message)


def _provider_url() -> str | None:
    raw = str(config.ODYSSEUS_CAPTION_URL or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        _ = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        not raw
        or parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return None
    return raw


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Treat every redirect as a provider failure before bearer forwarding."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_provider(request: urllib.request.Request, *, timeout: float):
    return urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _normalize_text(value: str, *, error_message: str) -> str:
    value = unicodedata.normalize("NFC", value)
    for character in value:
        if character in _BIDI_CONTROLS:
            _fail(error_message)
        category = unicodedata.category(character)
        if category in {"Cc", "Cs"} and character not in {"\n", "\t"}:
            _fail(error_message)
        if category == "Cf" and character not in _ALLOWED_FORMAT_CHARACTERS:
            _fail(error_message)
    return value


def _request_body(ctx: dict) -> bytes:
    if not isinstance(ctx, dict):
        _fail(_INVALID_REQUEST)
    if any(not isinstance(key, str) for key in ctx):
        _fail(_INVALID_REQUEST)
    if any(key not in _CONTEXT_LIMITS for key in ctx):
        _fail(_INVALID_REQUEST)

    clean: dict[str, str] = {}
    for key, value in ctx.items():
        if not isinstance(value, str):
            _fail(_INVALID_REQUEST)
        value = _normalize_text(value, error_message=_INVALID_REQUEST)
        if len(value) > _CONTEXT_LIMITS[key]:
            _fail(_INVALID_REQUEST)
        clean[key] = value

    try:
        body = json.dumps(
            clean,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail(_INVALID_REQUEST)
    if len(body) > _MAX_REQUEST_BYTES:
        _fail(_INVALID_REQUEST)
    return body


def _canonical_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(_INVALID_REQUEST)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        _fail(_INVALID_REQUEST)
    if value != str(parsed):
        _fail(_INVALID_REQUEST)
    return value


def _object_without_duplicate_keys(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response key")
        result[key] = value
    return result


def _response_payload(raw: bytes) -> dict[str, str]:
    if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
        _fail(_INVALID_RESPONSE)
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except Exception:
        _fail(_INVALID_RESPONSE)
    if not isinstance(payload, dict) or set(payload) != {"caption", "model"}:
        _fail(_INVALID_RESPONSE)

    caption_value = payload["caption"]
    model_value = payload["model"]
    if not isinstance(caption_value, str) or not isinstance(model_value, str):
        _fail(_INVALID_RESPONSE)

    caption = _normalize_text(caption_value, error_message=_INVALID_RESPONSE)
    model = _normalize_text(model_value, error_message=_INVALID_RESPONSE)
    if len(caption) > _MAX_CAPTION_SCALARS or len(caption.encode("utf-8")) > _MAX_CAPTION_BYTES:
        _fail(_INVALID_RESPONSE)
    if len(model) > _MAX_MODEL_SCALARS:
        _fail(_INVALID_RESPONSE)

    caption = caption.strip()
    if not caption:
        _fail(_INVALID_RESPONSE)
    return {"caption": caption, "model": model.strip() or "unknown"}


def draft_caption(ctx: dict, *, idempotency_key: str | None = None) -> dict:
    """Ask Odysseus to draft ONE caption from `ctx`. Returns {"caption", "model"}.

    Raises CaptionDraftError on every failure path so the route can leave the
    caption untouched. `ctx` is the drafting context (label, note, client, period
    …) — Odysseus shapes the prompt and selects the model."""
    if not is_enabled():
        raise CaptionDraftError("AI drafting is not configured")
    provider_url = _provider_url()
    if provider_url is None:
        raise CaptionDraftError("AI drafting is not configured")
    body = _request_body(ctx)
    idempotency_key = _canonical_idempotency_key(idempotency_key)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {config.ODYSSEUS_CAPTION_TOKEN}",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    try:
        req = urllib.request.Request(
            provider_url,
            method="POST",
            data=body,
            headers=headers,
        )
        with _open_provider(req, timeout=config.ODYSSEUS_TIMEOUT) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        _fail(_PROVIDER_UNAVAILABLE)
    except CaptionDraftError:
        raise
    except Exception:
        _fail(_PROVIDER_UNAVAILABLE)

    result = _response_payload(raw)
    log.info(
        "caption drafted via Odysseus (%d chars)",
        len(result["caption"]),
    )
    return result
