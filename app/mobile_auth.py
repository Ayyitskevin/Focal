"""Opaque, tenant-bound sessions for the native/mobile JSON API.

This module deliberately does not know about FastAPI routers, response schemas,
cookies, or the existing browser session.  Callers pass an already host-scoped
``Request`` plus explicit credentials; the helpers return plain dataclasses or a
typed ``MobileAuthError`` that the API layer can translate to a problem response.

Access and refresh credentials contain 256 random bits.  Only SHA-256 hashes are
stored.  Refresh credentials rotate once, and replaying a consumed credential
revokes the complete session family in the same immediate SQLite transaction.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from fastapi import Request

from . import config, db, security, urls
from .admin import studio as admin_studio

ACCESS_TTL = dt.timedelta(minutes=15)
REFRESH_TTL = dt.timedelta(days=30)
SESSION_ABSOLUTE_TTL = dt.timedelta(days=90)

STUDIO_OWNER = "studio_owner"
GALLERY_GUEST = "gallery_guest"
PORTAL_GUEST = "portal_guest"
WORKSPACE_GUEST = "workspace_guest"
DOCUMENT_GUEST = "document_guest"

_PRINCIPAL_KINDS = frozenset(
    {STUDIO_OWNER, GALLERY_GUEST, PORTAL_GUEST, WORKSPACE_GUEST, DOCUMENT_GUEST}
)
_DOCUMENT_VARIANTS = frozenset({"proposal", "contract", "invoice"})
_DOCUMENT_TABLES = {
    "proposal": "proposals",
    "contract": "contracts",
    "invoice": "invoices",
}
_LAST_SEEN_WRITE_INTERVAL = 300
_DEVICE_NAME_MAX = 120
_DEVICE_PLATFORM_MAX = 32
_DEVICE_APP_VERSION_MAX = 64
_TOKEN_BYTES = 32


def _studio_today() -> dt.date:
    """Use Mise's canonical studio-day boundary for gallery expiry."""

    return admin_studio._today()


class MobileAuthError(Exception):
    """Router-neutral authentication failure with stable API problem fields."""

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        *,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.retry_after = retry_after


@dataclass(frozen=True)
class Principal:
    session_id: str
    tenant_key: str
    kind: str
    resource_id: int | None
    resource_variant: str | None
    gallery_visitor_id: int | None
    scopes: frozenset[str]
    device_name: str | None
    device_platform: str | None
    device_app_version: str | None
    created_at: dt.datetime
    absolute_expires_at: dt.datetime

    @property
    def id(self) -> str:
        if self.kind == STUDIO_OWNER:
            return STUDIO_OWNER
        if self.kind == DOCUMENT_GUEST:
            return f"{self.kind}:{self.resource_variant}:{self.resource_id}"
        return f"{self.kind}:{self.resource_id}"

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def has_any_scope(self, scopes: Iterable[str]) -> bool:
        return not self.scopes.isdisjoint(scopes)


@dataclass(frozen=True)
class TokenPair:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires_at: dt.datetime
    refresh_expires_at: dt.datetime
    session_id: str
    principal: Principal


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    device_name: str | None
    device_platform: str | None
    device_app_version: str | None
    created_at: dt.datetime
    last_seen_at: dt.datetime
    absolute_expires_at: dt.datetime
    revoked_at: dt.datetime | None
    is_current: bool


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _now_ts() -> int:
    return int(_now().timestamp())


def _as_datetime(value: int | None) -> dt.datetime | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value, tz=dt.UTC)


def _ts_after(now: dt.datetime, delta: dt.timedelta) -> int:
    return int((now + delta).timestamp())


def _tenant_key(request: Request) -> str:
    """Immutable hosted tenant id plus origin, or a self-host request origin.

    Hosted callers must already be inside ``saas.tenant_middleware``.  In
    particular, the platform/root host is never an implicit operator tenant.
    Self-hosted installs can answer multiple aliases from one DB, so the origin
    remains part of the binding and an alias cannot replay another alias's token.
    """
    origin = urls.origin_from_url(urls.request_origin(request))
    if origin is None:
        raise MobileAuthError(400, "auth.invalid_origin", "A valid request host is required.")

    if config.SAAS_MODE:
        from . import saas

        tenant = saas.current_tenant()
        if not tenant or tenant.get("deleted_at"):
            raise MobileAuthError(404, "auth.tenant_not_found", "Tenant context is required.")
        return f"tenant:{int(tenant['id'])}:{origin}"
    return f"self:{origin}"


def _token(prefix: str) -> str:
    return f"mise_{prefix}_{secrets.token_urlsafe(_TOKEN_BYTES)}"


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _installation_hash(installation_id: str | None) -> str | None:
    value = (installation_id or "").strip()
    if not value:
        return None
    try:
        value = str(uuid.UUID(value))
    except ValueError:
        # Older clients used bounded opaque installation identifiers. Preserve
        # their exact lexical identity while canonicalizing native UUIDs.
        pass
    return hashlib.sha256(f"mise-installation\0{value}".encode()).hexdigest()


def _sanitize_device_name(device_name: str | None) -> str | None:
    if not device_name:
        return None
    printable = "".join(ch if ch.isprintable() else " " for ch in device_name)
    value = re.sub(r"\s+", " ", printable).strip()
    return value[:_DEVICE_NAME_MAX] or None


def _sanitize_device_platform(device_platform: str | None) -> str | None:
    value = _sanitize_device_text(device_platform, _DEVICE_PLATFORM_MAX)
    return value.casefold() if value else None


def _sanitize_device_app_version(device_app_version: str | None) -> str | None:
    return _sanitize_device_text(device_app_version, _DEVICE_APP_VERSION_MAX)


def _sanitize_device_text(value: str | None, max_length: int) -> str | None:
    if not value:
        return None
    printable = "".join(ch if ch.isprintable() else " " for ch in value)
    cleaned = re.sub(r"\s+", " ", printable).strip()
    return cleaned[:max_length] or None


def _credential_fingerprint(
    tenant_key: str,
    principal_kind: str,
    resource_id: int | None,
    resource_variant: str | None,
    source: str,
) -> str:
    if not config.SECRET_KEY:
        raise RuntimeError("MISE_SECRET_KEY is not set")
    message = json.dumps(
        [tenant_key, principal_kind, resource_id, resource_variant, source],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hmac.new(
        config.SECRET_KEY.encode(), b"mise-mobile-credential\0" + message, hashlib.sha256
    ).hexdigest()


def _scope_json(scopes: frozenset[str]) -> str:
    return json.dumps(sorted(scopes), separators=(",", ":"))


def _load_scopes(raw: str) -> frozenset[str]:
    try:
        values = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise MobileAuthError(401, "auth.invalid_token", "The access token is invalid.") from exc
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise MobileAuthError(401, "auth.invalid_token", "The access token is invalid.")
    return frozenset(values)


def _principal_from_row(row: sqlite3.Row) -> Principal:
    created_at = _as_datetime(row["created_at"])
    absolute_expires_at = _as_datetime(row["absolute_expires_at"])
    if created_at is None or absolute_expires_at is None:
        raise MobileAuthError(401, "auth.invalid_token", "The access token is invalid.")
    return Principal(
        session_id=row["session_id"],
        tenant_key=row["tenant_key"],
        kind=row["principal_kind"],
        resource_id=row["resource_id"],
        resource_variant=row["resource_variant"],
        gallery_visitor_id=row["gallery_visitor_id"],
        scopes=_load_scopes(row["scopes_json"]),
        device_name=row["device_name"],
        device_platform=row["device_platform"],
        device_app_version=row["device_app_version"],
        created_at=created_at,
        absolute_expires_at=absolute_expires_at,
    )


def mobile_runtime_accepting(con: sqlite3.Connection) -> bool:
    """Return whether this database may admit or continue mobile work.

    Migration 085 adds a durable, tenant-local offboarding barrier. Older
    databases remain compatible while old application code is being restored,
    but once the table exists a missing/malformed singleton fails closed.
    """

    table = con.execute(
        """SELECT 1 FROM sqlite_master
              WHERE type='table' AND name='mobile_runtime_state'"""
    ).fetchone()
    if table is None:
        return True
    row = con.execute(
        """SELECT database_identity,offboarding
             FROM mobile_runtime_state WHERE singleton=1"""
    ).fetchone()
    return bool(
        row is not None
        and isinstance(row["database_identity"], str)
        and re.fullmatch(r"[0-9a-f]{32}", row["database_identity"])
        and row["offboarding"] == 0
    )


def _issue_session(
    request: Request,
    *,
    principal_kind: str,
    credential_source: str,
    scopes: frozenset[str],
    resource_id: int | None = None,
    resource_variant: str | None = None,
    gallery_visitor_id: int | None = None,
    installation_id: str | None = None,
    device_name: str | None = None,
    device_platform: str | None = None,
    device_app_version: str | None = None,
) -> TokenPair:
    if principal_kind not in _PRINCIPAL_KINDS:
        raise ValueError("unsupported mobile principal")
    if principal_kind == DOCUMENT_GUEST and resource_variant not in _DOCUMENT_VARIANTS:
        raise ValueError("unsupported document principal")
    tenant_key = _tenant_key(request)
    now = _now()
    now_ts = int(now.timestamp())
    absolute_expires_at = _ts_after(now, SESSION_ABSOLUTE_TTL)
    access_expires_at = min(_ts_after(now, ACCESS_TTL), absolute_expires_at)
    refresh_expires_at = min(_ts_after(now, REFRESH_TTL), absolute_expires_at)
    session_id = _token("session")
    access_token = _token("access")
    refresh_token = _token("refresh")
    fingerprint = _credential_fingerprint(
        tenant_key, principal_kind, resource_id, resource_variant, credential_source
    )

    # An immediate transaction serializes issuance with tenant offboarding. A
    # deferred read followed by an insert would allow deletion to set its marker
    # between those two statements and then mint a fresh session after the scrub.
    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        if not mobile_runtime_accepting(con):
            raise MobileAuthError(401, "auth.invalid_credentials", "The credentials are invalid.")
        con.execute(
            """INSERT INTO api_sessions
               (id, tenant_key, principal_kind, resource_id, resource_variant,
                gallery_visitor_id, scopes_json, credential_fingerprint,
                installation_id_hash, device_name, device_platform,
                device_app_version, created_at, last_seen_at, absolute_expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                tenant_key,
                principal_kind,
                resource_id,
                resource_variant,
                gallery_visitor_id,
                _scope_json(scopes),
                fingerprint,
                _installation_hash(installation_id),
                _sanitize_device_name(device_name),
                _sanitize_device_platform(device_platform),
                _sanitize_device_app_version(device_app_version),
                now_ts,
                now_ts,
                absolute_expires_at,
            ),
        )
        con.executemany(
            """INSERT INTO api_tokens
               (session_id, kind, token_hash, created_at, expires_at)
               VALUES (?,?,?,?,?)""",
            (
                (session_id, "access", _token_hash(access_token), now_ts, access_expires_at),
                (session_id, "refresh", _token_hash(refresh_token), now_ts, refresh_expires_at),
            ),
        )
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()

    row = db.one("SELECT *, id AS session_id FROM api_sessions WHERE id=?", (session_id,))
    assert row is not None
    principal = _principal_from_row(row)
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=_as_datetime(access_expires_at),
        refresh_expires_at=_as_datetime(refresh_expires_at),
        session_id=session_id,
        principal=principal,
    )


def _invalid_credentials() -> MobileAuthError:
    return MobileAuthError(401, "auth.invalid_credentials", "The credentials are invalid.")


def _locked() -> MobileAuthError:
    return MobileAuthError(
        429,
        "auth.locked",
        "Too many attempts. Try again later.",
        retry_after=config.PIN_LOCKOUT_MIN * 60,
    )


def issue_studio_owner_session(
    request: Request,
    password: str,
    *,
    email: str | None = None,
    installation_id: str | None = None,
    device_name: str | None = None,
    device_platform: str | None = None,
    device_app_version: str | None = None,
) -> TokenPair:
    """Verify the existing owner credential and mint a native owner session.

    Hosted email is optional for compatibility with today's password-only login.
    When supplied, it is checked without skipping the password hash work, so a
    mismatched address and a wrong password share one response and timing class.
    """
    _tenant_key(request)  # Reject the hosted platform/root context before credential work.
    ip = security.client_ip(request)
    if security.pin_locked(ip, 0):
        raise _locked()

    password_ok = security.check_admin_password(password or "")
    email_ok = True
    if config.SAAS_MODE and email is not None:
        from . import saas

        tenant = saas.current_tenant()
        assert tenant is not None
        supplied = hashlib.sha256(email.strip().casefold().encode()).digest()
        expected = hashlib.sha256(
            (tenant.get("owner_email") or "").strip().casefold().encode()
        ).digest()
        email_ok = hmac.compare_digest(supplied, expected)

    if not (password_ok & email_ok):
        security.pin_fail(ip, 0)
        raise _invalid_credentials()
    security.pin_clear(ip, 0)

    if config.SAAS_MODE:
        from . import saas

        tenant = saas.current_tenant()
        assert tenant is not None
        credential_source = tenant.get("admin_password_hash") or ""
    else:
        credential_source = config.ADMIN_PASSWORD
    return _issue_session(
        request,
        principal_kind=STUDIO_OWNER,
        credential_source=credential_source,
        scopes=frozenset({"studio:read", "studio:write"}),
        installation_id=installation_id,
        device_name=device_name,
        device_platform=device_platform,
        device_app_version=device_app_version,
    )


def _verify_pin(request: Request, bucket: int, supplied: str | None, actual: str | None) -> None:
    ip = security.client_ip(request)
    if security.pin_locked(ip, bucket):
        raise _locked()
    if not security.pin_matches(supplied or "", actual):
        security.pin_fail(ip, bucket)
        raise _invalid_credentials()
    security.pin_clear(ip, bucket)


def issue_gallery_session(
    request: Request,
    slug: str,
    pin: str | None = None,
    *,
    installation_id: str | None = None,
    device_name: str | None = None,
    device_platform: str | None = None,
    device_app_version: str | None = None,
) -> TokenPair:
    _tenant_key(request)
    gallery = db.one("SELECT * FROM galleries WHERE slug=?", ((slug or "").strip(),))
    if not gallery or not gallery["published"]:
        raise MobileAuthError(404, "gallery.not_found", "Gallery not found.")
    if gallery["expires_at"] and gallery["expires_at"] < _studio_today().isoformat():
        raise MobileAuthError(410, "gallery.expired", "The gallery has expired.")
    link_only_drop = gallery["type"] == "drop" and not gallery["require_pin"]
    if not link_only_drop:
        _verify_pin(request, gallery["id"], pin, gallery["pin"])

    visitor_token = secrets.token_urlsafe(24)
    visitor_id = db.run(
        "INSERT INTO visitors (gallery_id, token) VALUES (?,?)",
        (gallery["id"], visitor_token),
    )
    try:
        return _issue_session(
            request,
            principal_kind=GALLERY_GUEST,
            credential_source=gallery["pin"] or "",
            scopes=frozenset(
                {
                    f"gallery:{gallery['id']}:read",
                    f"gallery:{gallery['id']}:favorite",
                    f"gallery:{gallery['id']}:comment",
                    f"gallery:{gallery['id']}:download",
                }
            ),
            resource_id=gallery["id"],
            gallery_visitor_id=visitor_id,
            installation_id=installation_id,
            device_name=device_name,
            device_platform=device_platform,
            device_app_version=device_app_version,
        )
    except Exception:
        db.run("DELETE FROM visitors WHERE id=?", (visitor_id,))
        raise


def issue_portal_session(
    request: Request,
    slug: str,
    pin: str,
    *,
    installation_id: str | None = None,
    device_name: str | None = None,
    device_platform: str | None = None,
    device_app_version: str | None = None,
) -> TokenPair:
    _tenant_key(request)
    portal = db.one("SELECT * FROM portals WHERE slug=?", ((slug or "").strip(),))
    if not portal or not portal["published"]:
        raise MobileAuthError(404, "portal.not_found", "Portal not found.")
    from .public.portal import PIN_OFFSET

    _verify_pin(request, PIN_OFFSET + portal["id"], pin, portal["pin"])
    return _issue_session(
        request,
        principal_kind=PORTAL_GUEST,
        credential_source=portal["pin"] or "",
        scopes=frozenset({f"portal:{portal['id']}:read", f"portal:{portal['id']}:download"}),
        resource_id=portal["id"],
        installation_id=installation_id,
        device_name=device_name,
        device_platform=device_platform,
        device_app_version=device_app_version,
    )


def issue_workspace_session(
    request: Request,
    slug: str,
    pin: str,
    *,
    installation_id: str | None = None,
    device_name: str | None = None,
    device_platform: str | None = None,
    device_app_version: str | None = None,
) -> TokenPair:
    _tenant_key(request)
    workspace = db.one("SELECT * FROM projects WHERE workspace_slug=?", ((slug or "").strip(),))
    if not workspace or not workspace["workspace_published"]:
        raise MobileAuthError(404, "workspace.not_found", "Workspace not found.")
    from .public.workspace import PIN_OFFSET

    _verify_pin(request, PIN_OFFSET + workspace["id"], pin, workspace["workspace_pin"])
    return _issue_session(
        request,
        principal_kind=WORKSPACE_GUEST,
        credential_source=workspace["workspace_pin"] or "",
        scopes=frozenset({f"workspace:{workspace['id']}:read"}),
        resource_id=workspace["id"],
        installation_id=installation_id,
        device_name=device_name,
        device_platform=device_platform,
        device_app_version=device_app_version,
    )


def issue_document_session(
    request: Request,
    variant: str,
    slug: str,
    *,
    installation_id: str | None = None,
    device_name: str | None = None,
    device_platform: str | None = None,
    device_app_version: str | None = None,
) -> TokenPair:
    _tenant_key(request)
    if variant not in _DOCUMENT_VARIANTS:
        raise MobileAuthError(404, "document.not_found", "Document not found.")
    table = _DOCUMENT_TABLES[variant]
    document = db.one(f"SELECT id, slug, status FROM {table} WHERE slug=?", ((slug or "").strip(),))
    if not document or document["status"] == "draft":
        raise MobileAuthError(404, "document.not_found", "Document not found.")
    scopes = {f"document:{variant}:{document['id']}:read"}
    action = {"proposal": "respond", "contract": "sign", "invoice": "checkout"}[variant]
    scopes.add(f"document:{variant}:{document['id']}:{action}")
    return _issue_session(
        request,
        principal_kind=DOCUMENT_GUEST,
        credential_source=document["slug"],
        scopes=frozenset(scopes),
        resource_id=document["id"],
        resource_variant=variant,
        installation_id=installation_id,
        device_name=device_name,
        device_platform=device_platform,
        device_app_version=device_app_version,
    )


_SESSION_SELECT = """SELECT
    s.id AS session_id, s.tenant_key, s.principal_kind, s.resource_id,
    s.resource_variant, s.gallery_visitor_id, s.scopes_json,
    s.credential_fingerprint, s.device_name, s.device_platform,
    s.device_app_version, s.created_at, s.last_seen_at,
    s.absolute_expires_at, s.revoked_at AS session_revoked_at,
    t.id AS token_id, t.kind AS token_kind, t.token_hash, t.expires_at AS token_expires_at,
    t.consumed_at, t.revoked_at AS token_revoked_at
FROM api_tokens t JOIN api_sessions s ON s.id=t.session_id"""


def _current_credential_source(con: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    kind = row["principal_kind"]
    if kind == STUDIO_OWNER:
        if config.SAAS_MODE:
            from . import saas

            tenant = saas.current_tenant()
            return (tenant.get("admin_password_hash") or "") if tenant else None
        return config.ADMIN_PASSWORD or None

    if kind == GALLERY_GUEST:
        resource = con.execute(
            """SELECT g.pin, g.published, g.expires_at
               FROM galleries g JOIN visitors v ON v.gallery_id=g.id
               WHERE g.id=? AND v.id=?""",
            (row["resource_id"], row["gallery_visitor_id"]),
        ).fetchone()
        if not resource or not resource["published"]:
            return None
        if resource["expires_at"] and resource["expires_at"] < _studio_today().isoformat():
            return None
        return resource["pin"] or ""

    if kind == PORTAL_GUEST:
        resource = con.execute(
            "SELECT pin, published FROM portals WHERE id=?", (row["resource_id"],)
        ).fetchone()
        return (resource["pin"] or "") if resource and resource["published"] else None

    if kind == WORKSPACE_GUEST:
        resource = con.execute(
            "SELECT workspace_pin, workspace_published FROM projects WHERE id=?",
            (row["resource_id"],),
        ).fetchone()
        return (
            (resource["workspace_pin"] or "")
            if resource and resource["workspace_published"]
            else None
        )

    if kind == DOCUMENT_GUEST:
        variant = row["resource_variant"]
        if variant not in _DOCUMENT_VARIANTS:
            return None
        table = _DOCUMENT_TABLES[variant]
        resource = con.execute(
            f"SELECT slug, status FROM {table} WHERE id=?", (row["resource_id"],)
        ).fetchone()
        return resource["slug"] if resource and resource["status"] != "draft" else None
    return None


def _credential_is_current(con: sqlite3.Connection, row: sqlite3.Row) -> bool:
    source = _current_credential_source(con, row)
    if source is None:
        return False
    expected = _credential_fingerprint(
        row["tenant_key"],
        row["principal_kind"],
        row["resource_id"],
        row["resource_variant"],
        source,
    )
    return hmac.compare_digest(expected.encode(), row["credential_fingerprint"].encode())


def _binding_matches(request: Request, stored: str) -> bool:
    expected = _tenant_key(request)
    return hmac.compare_digest(expected.encode(), stored.encode())


def _revoke_session_tx(con: sqlite3.Connection, session_id: str, now_ts: int, reason: str) -> None:
    con.execute(
        """UPDATE api_sessions SET revoked_at=COALESCE(revoked_at, ?),
                                  revoke_reason=COALESCE(revoke_reason, ?)
           WHERE id=?""",
        (now_ts, reason, session_id),
    )
    con.execute(
        "UPDATE api_tokens SET revoked_at=COALESCE(revoked_at, ?) WHERE session_id=?",
        (now_ts, session_id),
    )
    # Generated caption suggestions are session-private user content. Preserve
    # only a scrubbed tenant-local usage row for quota/audit accounting; provider
    # context and candidate text must disappear in the revocation transaction.
    con.execute(
        """UPDATE mobile_caption_usage
              SET state='finished',finished_at=COALESCE(finished_at,datetime('now'))
            WHERE state='active' AND id IN (
                SELECT id FROM mobile_caption_suggestions
                 WHERE session_id=? AND status<>'running'
            )""",
        (session_id,),
    )
    con.execute(
        """UPDATE mobile_caption_suggestions
              SET session_id=NULL,
                  status=CASE
                      WHEN status IN ('queued','running','ready','failed')
                      THEN 'failed'
                      ELSE status
                  END,
                  context_json=NULL,
                  candidate_text=NULL,
                  provider=NULL,
                  model=NULL,
                  failure_code=CASE
                      WHEN status IN ('queued','running','ready','failed')
                      THEN 'session_ended'
                      ELSE NULL
                  END,
                  completed_at=CASE
                      WHEN status IN ('queued','running','ready','failed')
                      THEN COALESCE(completed_at, datetime('now'))
                      ELSE completed_at
                  END
            WHERE session_id=?""",
        (session_id,),
    )
    # Lazy import avoids a mobile_auth -> push_notifications -> mobile_auth cycle.
    # Token material is erased in the same transaction as family revocation.
    from . import push_notifications

    push_notifications.deactivate_session_tx(con, session_id, reason)


def session_is_current(con: sqlite3.Connection, session_id: str) -> bool:
    """Re-check a session immediately before an asynchronous privileged effect.

    Push delivery must not rely on registration-time authentication: the owner
    may have logged out, revoked the device, rotated their password, or reached
    the absolute session cap while an event was queued.
    """

    row = con.execute(
        """SELECT id AS session_id, tenant_key, principal_kind, resource_id,
                  resource_variant, gallery_visitor_id, scopes_json,
                  credential_fingerprint, device_name, device_platform,
                  device_app_version, created_at, last_seen_at,
                  absolute_expires_at, revoked_at AS session_revoked_at
             FROM api_sessions WHERE id=?""",
        (session_id,),
    ).fetchone()
    if row is None or row["session_revoked_at"] is not None:
        return False

    now_ts = _now_ts()
    if not mobile_runtime_accepting(con):
        _revoke_session_tx(con, session_id, now_ts, "studio_offboarding")
        return False
    if row["absolute_expires_at"] <= now_ts:
        _revoke_session_tx(con, session_id, now_ts, "session_expired")
        return False
    if not _credential_is_current(con, row):
        _revoke_session_tx(con, session_id, now_ts, "credential_changed")
        return False
    return True


def _invalid_token() -> MobileAuthError:
    return MobileAuthError(401, "auth.invalid_token", "The token is invalid or expired.")


def authenticate_access(
    request: Request,
    access_token: str,
    *,
    required_scopes: Iterable[str] = (),
) -> Principal:
    """Authenticate one explicit bearer value; browser cookies are never read."""
    token_hash = _token_hash(access_token)
    now_ts = _now_ts()
    con = db.connect()
    try:
        row = con.execute(
            _SESSION_SELECT + " WHERE t.token_hash=? AND t.kind='access'", (token_hash,)
        ).fetchone()
        if row is None or not hmac.compare_digest(token_hash, row["token_hash"]):
            raise _invalid_token()
        if not _binding_matches(request, row["tenant_key"]):
            raise _invalid_token()
        if not mobile_runtime_accepting(con):
            _revoke_session_tx(con, row["session_id"], now_ts, "studio_offboarding")
            con.commit()
            raise _invalid_token()
        if row["absolute_expires_at"] <= now_ts:
            _revoke_session_tx(con, row["session_id"], now_ts, "session_expired")
            con.commit()
            raise _invalid_token()
        if (
            row["session_revoked_at"] is not None
            or row["token_revoked_at"] is not None
            or row["token_expires_at"] <= now_ts
        ):
            raise _invalid_token()
        if not _credential_is_current(con, row):
            _revoke_session_tx(con, row["session_id"], now_ts, "credential_changed")
            con.commit()
            raise _invalid_token()

        principal = _principal_from_row(row)
        needed = frozenset(required_scopes)
        if not needed.issubset(principal.scopes):
            raise MobileAuthError(403, "auth.insufficient_scope", "The token lacks this scope.")
        if now_ts - row["last_seen_at"] >= _LAST_SEEN_WRITE_INTERVAL:
            con.execute(
                "UPDATE api_sessions SET last_seen_at=? WHERE id=?",
                (now_ts, row["session_id"]),
            )
            con.commit()
        return principal
    finally:
        con.close()


def authenticate_request(request: Request, *, required_scopes: Iterable[str] = ()) -> Principal:
    """Extract a Bearer header and authenticate it without any cookie fallback."""
    header = request.headers.get("authorization", "")
    scheme, separator, token = header.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token.strip():
        raise _invalid_token()
    return authenticate_access(request, token.strip(), required_scopes=required_scopes)


def rotate_refresh(request: Request, refresh_token: str) -> TokenPair:
    """Rotate once; replay of a consumed token atomically revokes its family."""
    token_hash = _token_hash(refresh_token)
    now = _now()
    now_ts = int(now.timestamp())
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            _SESSION_SELECT + " WHERE t.token_hash=? AND t.kind='refresh'", (token_hash,)
        ).fetchone()
        if row is None or not hmac.compare_digest(token_hash, row["token_hash"]):
            con.rollback()
            raise _invalid_token()
        if not _binding_matches(request, row["tenant_key"]):
            con.rollback()
            raise _invalid_token()
        if not mobile_runtime_accepting(con):
            _revoke_session_tx(con, row["session_id"], now_ts, "studio_offboarding")
            con.commit()
            raise _invalid_token()
        if row["consumed_at"] is not None:
            _revoke_session_tx(con, row["session_id"], now_ts, "refresh_reuse")
            con.commit()
            raise MobileAuthError(
                401,
                "auth.refresh_reused",
                "Refresh token reuse revoked this session.",
            )
        if (
            row["session_revoked_at"] is not None
            or row["token_revoked_at"] is not None
            or row["token_expires_at"] <= now_ts
            or row["absolute_expires_at"] <= now_ts
        ):
            _revoke_session_tx(con, row["session_id"], now_ts, "refresh_expired")
            con.commit()
            raise _invalid_token()
        if not _credential_is_current(con, row):
            _revoke_session_tx(con, row["session_id"], now_ts, "credential_changed")
            con.commit()
            raise _invalid_token()
        principal = _principal_from_row(row)

        absolute_expires_at = row["absolute_expires_at"]
        access_expires_at = min(_ts_after(now, ACCESS_TTL), absolute_expires_at)
        refresh_expires_at = min(_ts_after(now, REFRESH_TTL), absolute_expires_at)
        if access_expires_at <= now_ts or refresh_expires_at <= now_ts:
            _revoke_session_tx(con, row["session_id"], now_ts, "session_expired")
            con.commit()
            raise _invalid_token()

        updated = con.execute(
            """UPDATE api_tokens SET consumed_at=?
               WHERE id=? AND consumed_at IS NULL AND revoked_at IS NULL""",
            (now_ts, row["token_id"]),
        )
        if updated.rowcount != 1:
            _revoke_session_tx(con, row["session_id"], now_ts, "refresh_reuse")
            con.commit()
            raise MobileAuthError(
                401,
                "auth.refresh_reused",
                "Refresh token reuse revoked this session.",
            )

        access_token = _token("access")
        new_refresh_token = _token("refresh")
        access_id = con.execute(
            """INSERT INTO api_tokens
               (session_id, kind, token_hash, created_at, expires_at)
               VALUES (?,?,?,?,?)""",
            (
                row["session_id"],
                "access",
                _token_hash(access_token),
                now_ts,
                access_expires_at,
            ),
        ).lastrowid
        assert access_id is not None
        refresh_id = con.execute(
            """INSERT INTO api_tokens
               (session_id, kind, token_hash, created_at, expires_at)
               VALUES (?,?,?,?,?)""",
            (
                row["session_id"],
                "refresh",
                _token_hash(new_refresh_token),
                now_ts,
                refresh_expires_at,
            ),
        ).lastrowid
        con.execute(
            "UPDATE api_tokens SET replaced_by_id=? WHERE id=?",
            (refresh_id, row["token_id"]),
        )
        con.execute(
            "UPDATE api_sessions SET last_seen_at=? WHERE id=?",
            (now_ts, row["session_id"]),
        )
        con.commit()

        return TokenPair(
            access_token=access_token,
            refresh_token=new_refresh_token,
            access_expires_at=_as_datetime(access_expires_at),
            refresh_expires_at=_as_datetime(refresh_expires_at),
            session_id=row["session_id"],
            principal=principal,
        )
    except sqlite3.Error:
        con.rollback()
        raise
    finally:
        con.close()


def logout(request: Request, token: str) -> bool:
    """Idempotently revoke the family containing an access or refresh token."""
    token_hash = _token_hash(token)
    now_ts = _now_ts()
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            """SELECT s.id AS session_id, s.tenant_key, t.token_hash
               FROM api_tokens t JOIN api_sessions s ON s.id=t.session_id
               WHERE t.token_hash=?""",
            (token_hash,),
        ).fetchone()
        if (
            row is None
            or not hmac.compare_digest(token_hash, row["token_hash"])
            or not _binding_matches(request, row["tenant_key"])
        ):
            con.rollback()
            return False
        _revoke_session_tx(con, row["session_id"], now_ts, "logout")
        con.commit()
        return True
    except sqlite3.Error:
        con.rollback()
        raise
    finally:
        con.close()


def list_sessions(request: Request, owner: Principal) -> tuple[SessionSummary, ...]:
    tenant_key = _tenant_key(request)
    if owner.kind != STUDIO_OWNER or not hmac.compare_digest(
        tenant_key.encode(), owner.tenant_key.encode()
    ):
        raise MobileAuthError(403, "auth.insufficient_scope", "Owner access is required.")
    rows = db.all_(
        """SELECT id, device_name, device_platform, device_app_version,
                  created_at, last_seen_at,
                  absolute_expires_at, revoked_at
           FROM api_sessions
           WHERE tenant_key=? AND principal_kind='studio_owner'
           ORDER BY created_at DESC
           LIMIT 500""",
        (tenant_key,),
    )
    return tuple(
        SessionSummary(
            session_id=row["id"],
            device_name=row["device_name"],
            device_platform=row["device_platform"],
            device_app_version=row["device_app_version"],
            created_at=_as_datetime(row["created_at"]),
            last_seen_at=_as_datetime(row["last_seen_at"]),
            absolute_expires_at=_as_datetime(row["absolute_expires_at"]),
            revoked_at=_as_datetime(row["revoked_at"]),
            is_current=row["id"] == owner.session_id,
        )
        for row in rows
    )


def revoke_session(request: Request, owner: Principal, session_id: str) -> bool:
    """Revoke one owner device session; guest families are not enumerable here."""
    tenant_key = _tenant_key(request)
    if owner.kind != STUDIO_OWNER or not hmac.compare_digest(
        tenant_key.encode(), owner.tenant_key.encode()
    ):
        raise MobileAuthError(403, "auth.insufficient_scope", "Owner access is required.")
    now_ts = _now_ts()
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            """SELECT id FROM api_sessions
               WHERE id=? AND tenant_key=? AND principal_kind='studio_owner'""",
            (session_id, tenant_key),
        ).fetchone()
        if not row:
            con.rollback()
            return False
        _revoke_session_tx(con, session_id, now_ts, "owner_revoked")
        con.commit()
        return True
    except sqlite3.Error:
        con.rollback()
        raise
    finally:
        con.close()
