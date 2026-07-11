"""Tenant-local APNs registration, event snapshot, and durable delivery state.

Device identifiers and tokens never become authority. The current request host,
opaque owner session, stored installation hash, and live credential fingerprint
are rechecked at each boundary. Jobs contain only tenant-local delivery ids; the
worker re-enters the tenant runtime before reading any token material.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request

from . import apns, audit, config, db, jobs, mobile_auth, mobile_workspace

log = logging.getLogger("mise.push")

NotificationCategory = Literal[
    "new_bookings",
    "booking_changes",
    "proposal_responses",
    "payments",
]

_PREFERENCE_COLUMNS = {
    "new_bookings": "pref_new_bookings",
    "booking_changes": "pref_booking_changes",
    "proposal_responses": "pref_proposal_responses",
    "payments": "pref_payments",
}
_ALERTS = {
    "new_bookings": ("New booking", "A new booking is ready to review in Mise."),
    "booking_changes": ("Booking updated", "A booking schedule changed. Open Mise to review."),
    "proposal_responses": (
        "Proposal response",
        "A client responded to a proposal. Open Mise to review.",
    ),
    "payments": ("Payment update", "A payment was recorded. Open Mise to review."),
}
_ROUTE_RE = re.compile(r"^/app/(?:home|projects/[1-9][0-9]*|bookings/[1-9][0-9]*)$")
_TOKEN_RE = re.compile(r"^[0-9a-f]+$")
_TOPIC_RE = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_INVALID_DEVICE_REASONS = {
    "BadDeviceToken",
    "DeviceTokenNotForTopic",
    "Unregistered",
}


class PushNotificationError(RuntimeError):
    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class DeviceRegistration:
    environment: str
    locale: str
    app_version: str
    preferences: dict[str, bool]
    active: bool
    registered_at: str
    updated_at: str
    revision: int


@contextmanager
def _immediate_transaction():
    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _sqlite_now(value: dt.datetime | None = None) -> str:
    return (value or _now()).astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M:%S")


def _storage_key() -> bytes:
    raw = config.APNS_TOKEN_ENCRYPTION_KEY
    try:
        key = base64.b64decode(raw, validate=True)
    except (TypeError, ValueError):
        key = b""
    if len(key) != 32:
        raise PushNotificationError(
            503,
            "notifications.unavailable",
            "Push notification storage is not configured.",
        )
    return key


def _topic() -> str:
    value = config.APNS_TOPIC.strip()
    if not 1 <= len(value) <= 255 or not _TOPIC_RE.fullmatch(value):
        raise PushNotificationError(
            503,
            "notifications.unavailable",
            "Push notifications are not configured.",
        )
    return value


def normalize_token(value: str) -> str:
    token = value.strip().lower()
    if not 32 <= len(token) <= 512 or len(token) % 2 or not _TOKEN_RE.fullmatch(token):
        raise PushNotificationError(
            422,
            "device.invalid_token",
            "The APNs device token is invalid.",
        )
    return token


def _token_hash(token: str, key: bytes) -> str:
    return hmac.new(key, b"mise-apns-token\0" + token.encode(), hashlib.sha256).hexdigest()


def _subkey(master: bytes, purpose: bytes) -> bytes:
    return hmac.new(master, b"mise-apns-key-v1\0" + purpose, hashlib.sha256).digest()


def _associated_data(token_hash: str, environment: str, topic: str) -> bytes:
    return f"mise-apns-token-v1\0{token_hash}\0{environment}\0{topic}".encode()


def _encrypt_token(token: str, token_hash: str, environment: str, topic: str, key: bytes) -> str:
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key).encrypt(
        nonce,
        token.encode(),
        _associated_data(token_hash, environment, topic),
    )
    return "v1." + base64.urlsafe_b64encode(nonce + encrypted).decode()


def _decrypt_token(row: sqlite3.Row) -> str:
    value = row["token_ciphertext"] or ""
    prefix, separator, encoded = value.partition(".")
    if prefix != "v1" or separator != ".":
        raise PushNotificationError(503, "notifications.token_unavailable", "Token unavailable.")
    try:
        raw = base64.urlsafe_b64decode(encoded.encode())
        token = AESGCM(_subkey(_storage_key(), b"encryption")).decrypt(
            raw[:12],
            raw[12:],
            _associated_data(row["device_token_hash"], row["environment"], row["topic"]),
        )
        result = token.decode()
    except Exception:
        raise PushNotificationError(
            503,
            "notifications.token_unavailable",
            "Token unavailable.",
        ) from None
    return normalize_token(result)


def _preferences(row: sqlite3.Row) -> dict[str, bool]:
    return {name: bool(row[column]) for name, column in _PREFERENCE_COLUMNS.items()}


def _registration(row: sqlite3.Row) -> DeviceRegistration:
    return DeviceRegistration(
        environment=str(row["environment"]),
        locale=str(row["locale"]),
        app_version=str(row["app_version"]),
        preferences=_preferences(row),
        active=bool(row["active"]),
        registered_at=str(row["registered_at"]) + "Z",
        updated_at=str(row["updated_at"]) + "Z",
        revision=int(row["revision"]),
    )


def device_etag(value: DeviceRegistration) -> str:
    payload = json.dumps(
        {
            "environment": value.environment,
            "locale": value.locale,
            "app_version": value.app_version,
            "preferences": value.preferences,
            "active": value.active,
            "registered_at": value.registered_at,
            "updated_at": value.updated_at,
            "revision": value.revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f'"device-{hashlib.sha256(payload.encode()).hexdigest()[:32]}"'


def _validate_owner(principal: mobile_auth.Principal) -> None:
    if principal.kind != mobile_auth.STUDIO_OWNER or not principal.has_scope("studio:read"):
        raise PushNotificationError(403, "auth.insufficient_scope", "Owner access is required.")


def _installation_hash(value: str) -> str:
    try:
        normalized = str(uuid.UUID(value)).lower()
    except (ValueError, AttributeError):
        raise PushNotificationError(
            422,
            "device.invalid_installation",
            "The installation identifier is invalid.",
        ) from None
    result = mobile_auth._installation_hash(normalized)
    assert result is not None
    return result


def upsert_owner_device(
    request: Request,
    principal: mobile_auth.Principal,
    *,
    installation_id: str,
    token: str,
    environment: str,
    locale: str | None,
    app_version: str | None,
    preferences: Mapping[str, bool] | None = None,
) -> DeviceRegistration:
    _validate_owner(principal)
    token = normalize_token(token)
    install_hash = _installation_hash(installation_id)
    if environment not in {"sandbox", "production"} or environment != config.APNS_ENVIRONMENT:
        raise PushNotificationError(
            422,
            "device.environment_mismatch",
            "The app and server APNs environments do not match.",
        )
    if not locale or not app_version:
        raise PushNotificationError(422, "device.invalid_metadata", "Device metadata is required.")
    if not 1 <= len(locale) <= 35 or not 1 <= len(app_version) <= 64:
        raise PushNotificationError(422, "device.invalid_metadata", "Device metadata is invalid.")
    metadata = mobile_workspace.tenant_metadata(request, canonical=True)
    workspace_cache_namespace = str(metadata["cache_namespace"])
    if not 1 <= len(workspace_cache_namespace) <= 255:
        raise PushNotificationError(422, "device.invalid_workspace", "Workspace is invalid.")
    origin = str(metadata["origin"])
    if not origin.startswith("https://"):
        raise PushNotificationError(
            422,
            "device.secure_origin_required",
            "Push notifications require the studio's HTTPS origin.",
        )
    topic = _topic()
    master_key = _storage_key()
    token_hash = _token_hash(token, _subkey(master_key, b"fingerprint"))
    ciphertext = _encrypt_token(
        token,
        token_hash,
        environment,
        topic,
        _subkey(master_key, b"encryption"),
    )
    requested_preferences = dict(preferences) if preferences is not None else None
    if requested_preferences is not None and not set(requested_preferences).issubset(
        _PREFERENCE_COLUMNS
    ):
        raise PushNotificationError(422, "device.invalid_preferences", "Preferences are invalid.")

    with _immediate_transaction() as con:
        if not mobile_auth.session_is_current(con, principal.session_id):
            raise PushNotificationError(404, "device.not_found", "Device not found.")
        session = con.execute(
            """SELECT installation_id_hash FROM api_sessions
                 WHERE id=? AND tenant_key=? AND principal_kind='studio_owner'
                   AND revoked_at IS NULL""",
            (principal.session_id, principal.tenant_key),
        ).fetchone()
        if (
            session is None
            or session["installation_id_hash"] is None
            or not hmac.compare_digest(install_hash, session["installation_id_hash"])
        ):
            raise PushNotificationError(404, "device.not_found", "Device not found.")

        existing = con.execute(
            "SELECT * FROM mobile_push_devices WHERE installation_id_hash=?",
            (install_hash,),
        ).fetchone()
        conflicting = con.execute(
            """SELECT * FROM mobile_push_devices
                 WHERE environment=? AND topic=? AND token_hash=? AND active=1""",
            (environment, topic, token_hash),
        ).fetchone()
        if conflicting is not None and (existing is None or conflicting["id"] != existing["id"]):
            con.execute(
                """UPDATE mobile_push_devices
                      SET active=0, token_ciphertext=NULL, token_version=token_version+1,
                          revision=revision+1,
                          disabled_reason='token_rebound', disabled_at=datetime('now'),
                          updated_at=datetime('now') WHERE id=?""",
                (conflicting["id"],),
            )

        if existing is None:
            values = {name: True for name in _PREFERENCE_COLUMNS}
            if requested_preferences is not None:
                values.update(requested_preferences)
            device_id = con.execute(
                """INSERT INTO mobile_push_devices
                   (session_id,installation_id_hash,token_hash,token_ciphertext,
                    environment,topic,origin,workspace_cache_namespace,locale,app_version,
                    pref_new_bookings,pref_booking_changes,pref_proposal_responses,pref_payments)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    principal.session_id,
                    install_hash,
                    token_hash,
                    ciphertext,
                    environment,
                    topic,
                    origin,
                    workspace_cache_namespace,
                    locale,
                    app_version,
                    int(values["new_bookings"]),
                    int(values["booking_changes"]),
                    int(values["proposal_responses"]),
                    int(values["payments"]),
                ),
            ).lastrowid
            action = "register"
        else:
            values = _preferences(existing)
            if requested_preferences is not None:
                values.update(requested_preferences)
            changed_token = (
                not existing["active"]
                or existing["token_hash"] != token_hash
                or existing["environment"] != environment
                or existing["topic"] != topic
            )
            con.execute(
                """UPDATE mobile_push_devices
                      SET session_id=?, token_hash=?, token_ciphertext=?,
                          token_version=token_version+1, revision=revision+1,
                          environment=?, topic=?, origin=?,
                          workspace_cache_namespace=?, locale=?, app_version=?,
                          pref_new_bookings=?, pref_booking_changes=?,
                          pref_proposal_responses=?, pref_payments=?, active=1,
                          disabled_reason=NULL, disabled_at=NULL,
                          last_registered_at=datetime('now'), updated_at=datetime('now')
                    WHERE id=?""",
                (
                    principal.session_id,
                    token_hash,
                    ciphertext,
                    environment,
                    topic,
                    origin,
                    workspace_cache_namespace,
                    locale,
                    app_version,
                    int(values["new_bookings"]),
                    int(values["booking_changes"]),
                    int(values["proposal_responses"]),
                    int(values["payments"]),
                    existing["id"],
                ),
            )
            device_id = existing["id"]
            action = "rotate" if changed_token else "refresh"
        audit.log(
            con,
            "mobile_push_device",
            int(device_id),
            action,
            actor="mobile_owner",
            diff={
                "session_id": principal.session_id,
                "environment": environment,
                "preferences": values,
            },
        )
        row = con.execute("SELECT * FROM mobile_push_devices WHERE id=?", (device_id,)).fetchone()
    assert row is not None
    return _registration(row)


def current_device(principal: mobile_auth.Principal) -> DeviceRegistration | None:
    _validate_owner(principal)
    row = db.one(
        "SELECT * FROM mobile_push_devices WHERE session_id=? AND active=1",
        (principal.session_id,),
    )
    return _registration(row) if row is not None else None


def update_current_preferences(
    principal: mobile_auth.Principal,
    preferences: Mapping[str, bool],
    *,
    if_match: str,
) -> DeviceRegistration:
    _validate_owner(principal)
    values = dict(preferences)
    if not values or not set(values).issubset(_PREFERENCE_COLUMNS):
        raise PushNotificationError(422, "device.invalid_preferences", "Preferences are invalid.")
    with _immediate_transaction() as con:
        row = con.execute(
            "SELECT * FROM mobile_push_devices WHERE session_id=? AND active=1",
            (principal.session_id,),
        ).fetchone()
        if row is None:
            raise PushNotificationError(404, "device.not_found", "Device not found.")
        before = _registration(row)
        if not hmac.compare_digest(device_etag(before), if_match):
            raise PushNotificationError(
                409,
                "resource.version_conflict",
                "Notification preferences changed. Reload before saving.",
            )
        merged = before.preferences.copy()
        merged.update(values)
        con.execute(
            """UPDATE mobile_push_devices
                  SET pref_new_bookings=?, pref_booking_changes=?,
                      pref_proposal_responses=?, pref_payments=?, revision=revision+1,
                      updated_at=datetime('now')
                WHERE id=?""",
            (
                int(merged["new_bookings"]),
                int(merged["booking_changes"]),
                int(merged["proposal_responses"]),
                int(merged["payments"]),
                row["id"],
            ),
        )
        audit.log(
            con,
            "mobile_push_device",
            int(row["id"]),
            "preferences",
            actor="mobile_owner",
            diff={"preferences": [before.preferences, merged]},
        )
        updated = con.execute(
            "SELECT * FROM mobile_push_devices WHERE id=?", (row["id"],)
        ).fetchone()
    assert updated is not None
    return _registration(updated)


def deactivate_current(
    principal: mobile_auth.Principal,
    reason: str = "unregistered",
) -> bool:
    _validate_owner(principal)
    with _immediate_transaction() as con:
        row = con.execute(
            "SELECT id FROM mobile_push_devices WHERE session_id=? AND active=1",
            (principal.session_id,),
        ).fetchone()
        if row is None:
            return False
        con.execute(
            """UPDATE mobile_push_devices
                  SET active=0, token_ciphertext=NULL, token_version=token_version+1,
                      revision=revision+1,
                      disabled_reason=?, disabled_at=datetime('now'), updated_at=datetime('now')
                WHERE id=?""",
            (reason[:120], row["id"]),
        )
        audit.log(
            con,
            "mobile_push_device",
            int(row["id"]),
            "deactivate",
            actor="mobile_owner",
            diff={"reason": reason[:120]},
        )
    return True


def deactivate_session_tx(con: sqlite3.Connection, session_id: str, reason: str) -> None:
    con.execute(
        """UPDATE mobile_push_devices
              SET active=0, token_ciphertext=NULL, token_version=token_version+1,
                  revision=revision+1,
                  disabled_reason=?, disabled_at=COALESCE(disabled_at,datetime('now')),
                  updated_at=datetime('now')
            WHERE session_id=? AND active=1""",
        (reason[:120], session_id),
    )


def enqueue_owner_event_tx(
    con: sqlite3.Connection,
    *,
    dedupe_key: str,
    category: NotificationCategory,
    route: str,
    title: str,
    body: str,
) -> list[int]:
    if category not in _PREFERENCE_COLUMNS:
        raise ValueError("unsupported notification category")
    if (title, body) != _ALERTS[category]:
        raise ValueError("notification alert copy must use the privacy-safe category template")
    if not 1 <= len(dedupe_key) <= 255 or not _ROUTE_RE.fullmatch(route):
        raise ValueError("invalid notification event identity or route")
    public_id = str(uuid.uuid4())
    inserted = con.execute(
        """INSERT INTO mobile_notification_events
           (public_id,dedupe_key,category,route,title,body)
           VALUES (?,?,?,?,?,?) ON CONFLICT(dedupe_key) DO NOTHING""",
        (public_id, dedupe_key, category, route, title, body),
    )
    if inserted.rowcount != 1:
        return []
    event_id = int(inserted.lastrowid)
    preference_column = db.ident(category, _PREFERENCE_COLUMNS.keys())
    preference_column = _PREFERENCE_COLUMNS[preference_column]
    rows = con.execute(
        f"""SELECT d.* FROM mobile_push_devices d
             JOIN api_sessions s ON s.id=d.session_id
            WHERE d.active=1 AND d.{preference_column}=1
              AND s.principal_kind='studio_owner' AND s.revoked_at IS NULL"""
    ).fetchall()
    job_ids: list[int] = []
    for device in rows:
        if not mobile_auth.session_is_current(con, str(device["session_id"])):
            continue
        delivery_id = con.execute(
            """INSERT INTO mobile_notification_deliveries
               (event_id,device_id,token_hash,token_version,apns_id)
               VALUES (?,?,?,?,?)""",
            (
                event_id,
                device["id"],
                device["token_hash"],
                device["token_version"],
                str(uuid.uuid4()),
            ),
        ).lastrowid
        assert delivery_id is not None
        job_id = jobs.enqueue_in_transaction(con, "apns_delivery", {"delivery_id": delivery_id})
        con.execute(
            "UPDATE mobile_notification_deliveries SET queued_job_id=? WHERE id=?",
            (job_id, delivery_id),
        )
        job_ids.append(job_id)
    return job_ids


def alert_copy(category: NotificationCategory) -> tuple[str, str]:
    return _ALERTS[category]


def kick(job_ids: list[int] | tuple[int, ...]) -> None:
    for job_id in job_ids:
        jobs.kick(int(job_id))


def _claim(delivery_id: int) -> tuple[str, sqlite3.Row] | None:
    claim = str(uuid.uuid4())
    lease = max(30, int(config.APNS_LEASE_SECONDS))
    with _immediate_transaction() as con:
        claimed = con.execute(
            """UPDATE mobile_notification_deliveries
                  SET status='sending', claim_token=?, claimed_at=datetime('now'),
                      queued_job_id=NULL,
                      attempts=attempts+1, updated_at=datetime('now')
                WHERE id=? AND (
                    (status IN ('queued','retry') AND next_attempt_at <= datetime('now'))
                    OR
                    (status='sending' AND claimed_at < datetime('now', ?))
                )""",
            (claim, delivery_id, f"-{lease} seconds"),
        )
        if claimed.rowcount != 1:
            return None
        row = con.execute(
            """SELECT dl.id,dl.claim_token,dl.attempts,dl.token_hash AS delivery_token_hash,
                      dl.token_version AS delivery_token_version,dl.apns_id,
                      e.public_id,e.category,e.route,e.title,e.body,e.expires_at,
                      d.id AS device_id,d.session_id,d.token_hash AS device_token_hash,
                      d.token_version AS device_token_version,d.token_ciphertext,
                      d.environment,d.topic,d.origin,d.workspace_cache_namespace,
                      d.pref_new_bookings,d.pref_booking_changes,
                      d.pref_proposal_responses,d.pref_payments,d.active
                 FROM mobile_notification_deliveries dl
                 JOIN mobile_notification_events e ON e.id=dl.event_id
                 JOIN mobile_push_devices d ON d.id=dl.device_id
                WHERE dl.id=? AND dl.claim_token=?""",
            (delivery_id, claim),
        ).fetchone()
    return (claim, row) if row is not None else None


def _finish(
    delivery_id: int,
    claim: str,
    *,
    status: str,
    reason: str,
    http_status: int | None = None,
    delivered: bool = False,
    next_attempt_at: str | None = None,
    preserve_attempt: bool = False,
) -> bool:
    with _immediate_transaction() as con:
        if preserve_attempt:
            attempt_sql = ", attempts=MAX(attempts-1,0)"
        else:
            attempt_sql = ""
        values: list[object] = [status, reason[:120], http_status]
        next_sql = ""
        if next_attempt_at is not None:
            next_sql = ", next_attempt_at=?"
            values.append(next_attempt_at)
        values.extend([delivery_id, claim])
        delivered_sql = "datetime('now')" if delivered else "delivered_at"
        changed = con.execute(
            f"""UPDATE mobile_notification_deliveries
                    SET status=?, reason=?, http_status=?, claim_token=NULL,
                        claimed_at=NULL, updated_at=datetime('now')
                        {attempt_sql}{next_sql},
                        delivered_at={delivered_sql}
                  WHERE id=? AND status='sending' AND claim_token=?""",
            tuple(values),
        )
        return changed.rowcount == 1


def _retry_delay(attempts: int, response: apns.APNsResponse | None = None) -> int:
    base = max(1, int(config.APNS_RETRY_BASE_SECONDS))
    maximum = max(base, int(config.APNS_RETRY_MAX_SECONDS))
    computed = min(maximum, base * (2 ** max(0, min(attempts - 1, 16))))
    minimum = 0
    if response is not None and response.retry_after_seconds is not None:
        minimum = max(minimum, response.retry_after_seconds)
    if response is not None and 500 <= response.status_code <= 599:
        minimum = max(minimum, 15 * 60)
    if response is not None and response.status_code == 403:
        minimum = max(minimum, 15 * 60)
    if response is not None and response.reason == "TooManyProviderTokenUpdates":
        minimum = max(minimum, 20 * 60)
    computed = max(computed, minimum)
    jitter = secrets.randbelow(max(1, min(computed // 5, 60) + 1))
    return computed + jitter


def _schedule_retry(
    row: sqlite3.Row,
    claim: str,
    reason: str,
    *,
    response: apns.APNsResponse | None = None,
) -> None:
    attempts = int(row["attempts"])
    if attempts >= max(1, int(config.APNS_MAX_ATTEMPTS)):
        _finish(
            row["id"],
            claim,
            status="failed",
            reason=reason,
            http_status=(response.status_code if response else None),
        )
        return
    delay = _retry_delay(attempts, response)
    when = _sqlite_now(_now() + dt.timedelta(seconds=delay))
    _finish(
        row["id"],
        claim,
        status="retry",
        reason=reason,
        http_status=(response.status_code if response else None),
        next_attempt_at=when,
    )


def _payload(row: sqlite3.Row) -> dict:
    return {
        "aps": {
            "alert": {"title": row["title"], "body": row["body"]},
            "sound": "default",
        },
        "mise": {
            "version": 1,
            "event_id": row["public_id"],
            "workspace_origin": row["origin"],
            "workspace_cache_namespace": row["workspace_cache_namespace"],
            "principal_kind": mobile_auth.STUDIO_OWNER,
            "principal_id": mobile_auth.STUDIO_OWNER,
            "route": row["route"],
        },
    }


def _registration_is_newer(registered_at: str, invalidated_at_ms: int) -> bool:
    """Compare SQLite's second-resolution registration to APNs' millisecond epoch.

    Treat the complete stored second as the registration window. Token version CAS
    remains the primary race guard; this timestamp check implements Apple's 410
    guidance when the app registered again after APNs invalidated that token.
    """

    try:
        registered = dt.datetime.fromisoformat(str(registered_at) + "+00:00")
    except ValueError:
        return False
    end_of_second_ms = int(registered.timestamp()) * 1000 + 999
    return end_of_second_ms >= int(invalidated_at_ms)


def deliver(delivery_id: int) -> None:
    claimed = _claim(int(delivery_id))
    if claimed is None:
        return
    claim, row = claimed
    assert _UUID_RE.fullmatch(str(row["public_id"]))
    if str(row["expires_at"]) <= _sqlite_now():
        _finish(row["id"], claim, status="skipped", reason="event_expired")
        return

    preference_column = _PREFERENCE_COLUMNS[str(row["category"])]
    if (
        not row["active"]
        or not row[preference_column]
        or row["delivery_token_hash"] != row["device_token_hash"]
        or row["delivery_token_version"] != row["device_token_version"]
    ):
        _finish(row["id"], claim, status="skipped", reason="device_changed")
        return

    with _immediate_transaction() as con:
        session_current = mobile_auth.session_is_current(con, str(row["session_id"]))
        if session_current:
            fresh = con.execute(
                f"""SELECT active,token_hash,token_version,
                           {preference_column} AS preference_enabled
                      FROM mobile_push_devices WHERE id=?""",
                (row["device_id"],),
            ).fetchone()
            device_current = bool(
                fresh
                and fresh["active"]
                and fresh["preference_enabled"]
                and fresh["token_hash"] == row["delivery_token_hash"]
                and fresh["token_version"] == row["delivery_token_version"]
            )
        else:
            device_current = False
    if not session_current or not device_current:
        reason = "session_inactive" if not session_current else "device_changed"
        _finish(row["id"], claim, status="skipped", reason=reason)
        return

    if not apns.configured():
        when = _sqlite_now(_now() + dt.timedelta(minutes=15))
        _finish(
            row["id"],
            claim,
            status="retry",
            reason="provider_unavailable",
            next_attempt_at=when,
            preserve_attempt=True,
        )
        return

    try:
        token = _decrypt_token(row)
        expiration = int(dt.datetime.fromisoformat(str(row["expires_at"]) + "+00:00").timestamp())
        result = apns.send(
            device_token=token,
            environment=str(row["environment"]),
            payload=_payload(row),
            apns_id=str(row["apns_id"]),
            collapse_id=str(row["public_id"]),
            expiration=expiration,
        )
    except apns.APNsTransportError:
        _schedule_retry(row, claim, "transport_error")
        return
    except apns.APNsConfigurationError:
        when = _sqlite_now(_now() + dt.timedelta(minutes=15))
        _finish(
            row["id"],
            claim,
            status="retry",
            reason="provider_configuration",
            next_attempt_at=when,
            preserve_attempt=True,
        )
        return
    except PushNotificationError:
        when = _sqlite_now(_now() + dt.timedelta(minutes=15))
        _finish(
            row["id"],
            claim,
            status="retry",
            reason="token_unavailable",
            next_attempt_at=when,
            preserve_attempt=True,
        )
        return
    except apns.APNsPayloadError:
        _finish(row["id"], claim, status="failed", reason="payload_invalid")
        return

    if result.delivered:
        _finish(
            row["id"],
            claim,
            status="delivered",
            reason="delivered",
            http_status=result.status_code,
            delivered=True,
        )
        return
    if result.status_code == 410 or result.reason in _INVALID_DEVICE_REASONS:
        with _immediate_transaction() as con:
            fresh = con.execute(
                """SELECT last_registered_at FROM mobile_push_devices
                     WHERE id=? AND active=1 AND token_hash=? AND token_version=?""",
                (
                    row["device_id"],
                    row["delivery_token_hash"],
                    row["delivery_token_version"],
                ),
            ).fetchone()
            registered_after_invalidation = bool(
                fresh
                and result.status_code == 410
                and result.invalidated_at is not None
                and _registration_is_newer(fresh["last_registered_at"], result.invalidated_at)
            )
            if fresh is not None and not registered_after_invalidation:
                con.execute(
                    """UPDATE mobile_push_devices
                      SET active=0,token_ciphertext=NULL,token_version=token_version+1,
                          revision=revision+1,
                          disabled_reason='apns_invalid',disabled_at=datetime('now'),
                          updated_at=datetime('now')
                      WHERE id=? AND active=1 AND token_hash=? AND token_version=?""",
                    (
                        row["device_id"],
                        row["delivery_token_hash"],
                        row["delivery_token_version"],
                    ),
                )
        _finish(
            row["id"],
            claim,
            status="failed",
            reason=result.reason or "apns_unregistered",
            http_status=result.status_code,
        )
        return
    if result.status_code == 403 and result.reason == "Forbidden":
        _finish(
            row["id"],
            claim,
            status="failed",
            reason="Forbidden",
            http_status=result.status_code,
        )
        return
    if result.status_code == 429 or result.status_code >= 500 or result.status_code == 403:
        _schedule_retry(row, claim, result.reason or "apns_retry", response=result)
        return
    _finish(
        row["id"],
        claim,
        status="failed",
        reason=result.reason or "apns_rejected",
        http_status=result.status_code,
    )


def sweep(limit: int = 100, *, dispatch: bool = True) -> int:
    lease = max(30, int(config.APNS_LEASE_SECONDS))
    retention_days = max(1, min(int(config.APNS_RETENTION_DAYS), 3650))
    retention_window = f"-{retention_days} days"
    stale_job_window = f"-{lease} seconds"
    kick_ids: list[int] = []
    created_count = 0
    with _immediate_transaction() as con:
        active_sessions = con.execute(
            """SELECT DISTINCT session_id FROM mobile_push_devices
                 WHERE active=1 AND session_id IS NOT NULL"""
        ).fetchall()
        for session in active_sessions:
            session_id = str(session["session_id"])
            if not mobile_auth.session_is_current(con, session_id):
                deactivate_session_tx(con, session_id, "session_inactive")
        # A billing-locked tenant may be skipped by the general worker bootstrap.
        # Normalize stale APNs job claims during every cleanup-only sweep so later
        # reactivation can kick the same durable job instead of duplicating it.
        con.execute(
            """UPDATE jobs SET status='queued',error=NULL,updated_at=datetime('now')
                WHERE kind='apns_delivery' AND status='running'
                  AND COALESCE(updated_at,created_at) < datetime('now', ?)
                  AND EXISTS (
                      SELECT 1 FROM mobile_notification_deliveries dl
                       WHERE dl.queued_job_id=jobs.id
                         AND dl.status IN ('queued','retry')
                  )""",
            (stale_job_window,),
        )
        con.execute(
            """UPDATE jobs SET status='failed',error='stale APNs worker',
                  updated_at=datetime('now')
                WHERE kind='apns_delivery' AND status='running'
                  AND COALESCE(updated_at,created_at) < datetime('now', ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM mobile_notification_deliveries dl
                       WHERE dl.queued_job_id=jobs.id
                  )""",
            (stale_job_window,),
        )
        # Expired events remain available for bounded incident diagnosis, then
        # cascade-delete deliveries. Raw tokens/ciphertext are never audit data.
        con.execute(
            """DELETE FROM mobile_notification_events
                WHERE expires_at < datetime('now', ?)""",
            (retention_window,),
        )
        con.execute(
            """DELETE FROM jobs
                WHERE kind='apns_delivery' AND status='queued'
                  AND created_at < datetime('now', ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM mobile_notification_deliveries dl
                       WHERE dl.queued_job_id=jobs.id
                  )""",
            (retention_window,),
        )
        con.execute(
            """DELETE FROM jobs
                WHERE kind='apns_delivery' AND status IN ('done','failed')
                  AND COALESCE(updated_at,created_at) < datetime('now', ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM mobile_notification_deliveries dl
                       WHERE dl.queued_job_id=jobs.id
                  )""",
            (retention_window,),
        )
        con.execute(
            """DELETE FROM mobile_push_devices
                WHERE active=0
                  AND COALESCE(disabled_at,updated_at) < datetime('now', ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM mobile_notification_deliveries dl
                       WHERE dl.device_id=mobile_push_devices.id
                  )""",
            (retention_window,),
        )
        if not dispatch:
            # Billing-locked tenants still receive session-expiry cleanup so
            # encrypted token material can never outlive its authority, but no
            # delivery work is scheduled until the tenant is billable again.
            return 0
        con.execute(
            """UPDATE mobile_notification_deliveries
              SET status='retry',claim_token=NULL,claimed_at=NULL,
                  queued_job_id=NULL,
                  next_attempt_at=datetime('now'),reason='stale_lease',
                  updated_at=datetime('now')
            WHERE status='sending' AND claimed_at < datetime('now', ?)""",
            (f"-{lease} seconds",),
        )
        con.execute(
            """UPDATE mobile_notification_deliveries
                  SET queued_job_id=NULL,updated_at=datetime('now')
                WHERE status IN ('queued','retry') AND queued_job_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM jobs j
                       WHERE j.id=mobile_notification_deliveries.queued_job_id
                        AND j.status IN ('queued','running')
                  )"""
        )
        maximum = max(1, min(int(limit), 500))
        existing = con.execute(
            """SELECT DISTINCT j.id
                 FROM mobile_notification_deliveries dl
                 JOIN jobs j ON j.id=dl.queued_job_id
                WHERE dl.status IN ('queued','retry')
                  AND dl.next_attempt_at <= datetime('now')
                  AND j.kind='apns_delivery' AND j.status='queued'
                ORDER BY dl.next_attempt_at,dl.id LIMIT ?""",
            (maximum,),
        ).fetchall()
        kick_ids.extend(int(row["id"]) for row in existing)
        remaining = maximum - len(existing)
        if remaining > 0:
            due = con.execute(
                """SELECT id FROM mobile_notification_deliveries
                WHERE status IN ('queued','retry') AND queued_job_id IS NULL
                  AND next_attempt_at <= datetime('now')
                ORDER BY next_attempt_at,id LIMIT ?""",
                (remaining,),
            ).fetchall()
            for row in due:
                delivery_id = int(row["id"])
                job_id = jobs.enqueue_in_transaction(
                    con,
                    "apns_delivery",
                    {"delivery_id": delivery_id},
                )
                con.execute(
                    """UPDATE mobile_notification_deliveries SET queued_job_id=?
                         WHERE id=? AND queued_job_id IS NULL""",
                    (job_id, delivery_id),
                )
                kick_ids.append(job_id)
                created_count += 1
    kick(kick_ids)
    return created_count
