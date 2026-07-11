"""Minimal persistent HTTP/2 APNs provider with token authentication.

The module deliberately owns only transport/authentication. Durable delivery
leases, preference checks, token deactivation, and retry scheduling live in
``push_notifications``. No device token, payload, provider JWT, or private key is
ever included in a log or exception message created here.
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt

from . import config

_SANDBOX_HOST = "https://api.sandbox.push.apple.com"
_PRODUCTION_HOST = "https://api.push.apple.com"
_JWT_LIFETIME_SECONDS = 50 * 60
_REJECTION_REFRESH_FLOOR_SECONDS = 20 * 60
_MAX_PAYLOAD_BYTES = 4096
_APPLE_ID_RE = re.compile(r"^[A-Z0-9]{10}$")
_TOPIC_RE = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


class APNsConfigurationError(RuntimeError):
    """Safe configuration failure whose message never contains secret values."""


class APNsPayloadError(ValueError):
    pass


class APNsTransportError(RuntimeError):
    """Retryable transport failure with no token-bearing URL in its message."""


@dataclass(frozen=True)
class APNsResponse:
    status_code: int
    reason: str | None
    apns_id: str
    retry_after_seconds: int | None = None
    invalidated_at: int | None = None

    @property
    def delivered(self) -> bool:
        return self.status_code == 200


@dataclass(frozen=True)
class _ProviderConfig:
    team_id: str
    key_id: str
    topic: str
    environment: str
    private_key: bytes


_lock = threading.RLock()
_cached_jwt: tuple[str, float, tuple[str, ...]] | None = None
_client: httpx.Client | None = None


def _read_private_key() -> bytes:
    encoded = config.APNS_PRIVATE_KEY_B64
    path = config.APNS_PRIVATE_KEY_PATH
    if bool(encoded) == bool(path):
        raise APNsConfigurationError("configure exactly one APNs private-key source")
    if encoded:
        try:
            value = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise APNsConfigurationError("APNs private-key base64 is invalid") from exc
    else:
        try:
            value = Path(path).read_bytes()
        except OSError as exc:
            raise APNsConfigurationError("APNs private-key file is unavailable") from exc
    if not value.startswith(b"-----BEGIN PRIVATE KEY-----"):
        raise APNsConfigurationError("APNs private key is not a PKCS#8 PEM key")
    return value


def provider_config() -> _ProviderConfig:
    if not _APPLE_ID_RE.fullmatch(config.APNS_TEAM_ID):
        raise APNsConfigurationError("APNs team id is missing or invalid")
    if not _APPLE_ID_RE.fullmatch(config.APNS_KEY_ID):
        raise APNsConfigurationError("APNs key id is missing or invalid")
    if not 1 <= len(config.APNS_TOPIC) <= 255 or not _TOPIC_RE.fullmatch(config.APNS_TOPIC):
        raise APNsConfigurationError("APNs topic is missing or invalid")
    if config.APNS_ENVIRONMENT not in {"sandbox", "production"}:
        raise APNsConfigurationError("APNs environment must be sandbox or production")
    if config.APNS_TIMEOUT_SECONDS <= 0:
        raise APNsConfigurationError("APNs timeout must be positive")
    return _ProviderConfig(
        team_id=config.APNS_TEAM_ID,
        key_id=config.APNS_KEY_ID,
        topic=config.APNS_TOPIC,
        environment=config.APNS_ENVIRONMENT,
        private_key=_read_private_key(),
    )


def configured() -> bool:
    try:
        provider_config()
    except APNsConfigurationError:
        return False
    return True


def _jwt_identity(value: _ProviderConfig) -> tuple[str, ...]:
    # The key bytes are intentionally not copied into cache metadata.
    return (value.team_id, value.key_id, value.topic, value.environment)


def _provider_token(value: _ProviderConfig, *, rejected_token: str | None = None) -> str:
    global _cached_jwt
    now = time.time()
    identity = _jwt_identity(value)
    with _lock:
        if _cached_jwt is not None and _cached_jwt[2] == identity:
            cached_token, created_at, _ = _cached_jwt
            if rejected_token is not None:
                # Another worker may already have replaced the rejected token.
                # Otherwise enforce Apple's provider-token update floor rather
                # than letting concurrent/persistent 403s create a JWT stampede.
                if cached_token != rejected_token:
                    return cached_token
                if now - created_at < _REJECTION_REFRESH_FLOOR_SECONDS:
                    return cached_token
            elif now - created_at < _JWT_LIFETIME_SECONDS:
                return cached_token
        try:
            encoded = jwt.encode(
                {"iss": value.team_id, "iat": int(now)},
                value.private_key,
                algorithm="ES256",
                headers={"kid": value.key_id},
            )
        except Exception as exc:  # PyJWT/cryptography expose several key errors.
            raise APNsConfigurationError("APNs provider token could not be signed") from exc
        _cached_jwt = (encoded, now, identity)
        return encoded


def _http_client() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            timeout = httpx.Timeout(config.APNS_TIMEOUT_SECONDS)
            _client = httpx.Client(
                http2=True,
                timeout=timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return _client


def close() -> None:
    global _client, _cached_jwt
    with _lock:
        client = _client
        _client = None
        _cached_jwt = None
    if client is not None:
        client.close()


def _retry_after(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(0, int(value.strip()))
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.UTC)
        return max(0, int((when - dt.datetime.now(dt.UTC)).total_seconds()))


def _decode_error(response: httpx.Response) -> tuple[str | None, int | None]:
    try:
        value: Any = response.json()
    except ValueError:
        return None, None
    if not isinstance(value, dict):
        return None, None
    reason = value.get("reason")
    timestamp = value.get("timestamp")
    return (
        reason if isinstance(reason, str) and len(reason) <= 120 else None,
        timestamp if isinstance(timestamp, int) and timestamp >= 0 else None,
    )


def _payload_bytes(payload: dict[str, Any]) -> bytes:
    if set(payload) != {"aps", "mise"}:
        raise APNsPayloadError("APNs payload must contain only aps and mise")
    try:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    except (TypeError, ValueError) as exc:
        raise APNsPayloadError("APNs payload is not JSON serializable") from exc
    if len(body) > _MAX_PAYLOAD_BYTES:
        raise APNsPayloadError("APNs payload exceeds 4096 bytes")
    return body


def _send_once(
    value: _ProviderConfig,
    *,
    device_token: str,
    payload: dict[str, Any],
    apns_id: str,
    collapse_id: str,
    expiration: int,
    provider_token: str | None = None,
) -> APNsResponse:
    if value.environment not in {"sandbox", "production"}:
        raise APNsConfigurationError("APNs environment is invalid")
    host = _SANDBOX_HOST if value.environment == "sandbox" else _PRODUCTION_HOST
    headers = {
        "authorization": f"bearer {provider_token or _provider_token(value)}",
        "apns-topic": value.topic,
        "apns-push-type": "alert",
        "apns-priority": "10",
        "apns-expiration": str(max(0, expiration)),
        "apns-collapse-id": collapse_id,
        "apns-id": apns_id,
        "content-type": "application/json",
    }
    try:
        response = _http_client().post(
            f"{host}/3/device/{device_token}",
            headers=headers,
            content=_payload_bytes(payload),
        )
    except httpx.HTTPError:
        # HTTPX exceptions commonly include the token-bearing request URL.
        raise APNsTransportError("APNs transport failed") from None
    reason, invalidated_at = _decode_error(response)
    return APNsResponse(
        status_code=response.status_code,
        reason=reason,
        apns_id=response.headers.get("apns-id", apns_id),
        retry_after_seconds=_retry_after(response.headers.get("retry-after")),
        invalidated_at=invalidated_at,
    )


def send(
    *,
    device_token: str,
    environment: str,
    payload: dict[str, Any],
    apns_id: str,
    collapse_id: str,
    expiration: int,
) -> APNsResponse:
    """Send one alert request, coalescing an eligible rejected-token refresh."""

    value = provider_config()
    if environment != value.environment:
        raise APNsConfigurationError("device and provider APNs environments differ")
    if (
        not device_token
        or len(device_token) % 2
        or any(c not in "0123456789abcdef" for c in device_token)
    ):
        raise APNsPayloadError("APNs device token is invalid")
    if not collapse_id or len(collapse_id.encode()) > 64:
        raise APNsPayloadError("APNs collapse id is invalid")

    provider_token = _provider_token(value)
    result = _send_once(
        value,
        device_token=device_token,
        payload=payload,
        apns_id=apns_id,
        collapse_id=collapse_id,
        expiration=expiration,
        provider_token=provider_token,
    )
    if result.status_code == 403 and result.reason in {
        "ExpiredProviderToken",
        "InvalidProviderToken",
    }:
        replacement = _provider_token(value, rejected_token=provider_token)
        if replacement != provider_token:
            result = _send_once(
                value,
                device_token=device_token,
                payload=payload,
                apns_id=apns_id,
                collapse_id=collapse_id,
                expiration=expiration,
                provider_token=replacement,
            )
    return result
