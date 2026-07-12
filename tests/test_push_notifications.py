"""Tenant-local APNs registration, outbox, delivery, and retry guarantees."""

from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from starlette.requests import Request

from app import apns, config, db, mobile_auth, push_notifications, saas, scheduler

pytestmark = pytest.mark.unit

_INSTALLATION_UPPER = "A8A06DC2-2034-4E3B-B07D-0CBFD2455B98"
_INSTALLATION_LOWER = _INSTALLATION_UPPER.lower()
_TOKEN = "ab" * 32


@dataclass(frozen=True)
class PushContext:
    request: Request
    pair: mobile_auth.TokenPair


def _request(host: str = "studio.test") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/devices",
            "query_string": b"",
            "headers": [(b"host", host.encode()), (b"accept", b"application/json")],
            "scheme": "https",
            "server": (host, 443),
            "client": ("203.0.113.9", 50000),
        }
    )


@pytest.fixture
def push_context(tmp_path, monkeypatch) -> PushContext:
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "push-test-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "BASE_URL", "https://canonical.studio.test")
    monkeypatch.setattr(config, "SITE_NAME", "North Star Studio")
    monkeypatch.setattr(config, "APNS_TOPIC", "com.ayyitskevin.mise")
    monkeypatch.setattr(config, "APNS_ENVIRONMENT", "sandbox")
    monkeypatch.setattr(
        config,
        "APNS_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(b"k" * 32).decode(),
    )
    db.migrate()
    request = _request()
    pair = mobile_auth.issue_studio_owner_session(
        request,
        "owner-password",
        installation_id=_INSTALLATION_UPPER,
        device_name="Kevin's iPhone",
        device_platform="ios",
        device_app_version="1.0 (42)",
    )
    return PushContext(request=request, pair=pair)


def _register(context: PushContext, *, token: str = _TOKEN):
    return push_notifications.upsert_owner_device(
        context.request,
        context.pair.principal,
        installation_id=_INSTALLATION_LOWER,
        token=token,
        environment="sandbox",
        locale="en_US",
        app_version="1.0 (42)",
    )


def _enqueue(context: PushContext, suffix: str, route: str = "/app/bookings/41") -> int:
    title, body = push_notifications.alert_copy("new_bookings")
    with db.tx() as con:
        job_ids = push_notifications.enqueue_owner_event_tx(
            con,
            dedupe_key=f"booking.confirmed:{suffix}",
            category="new_bookings",
            route=route,
            title=title,
            body=body,
        )
    assert len(job_ids) == 1
    row = db.one(
        """SELECT id FROM mobile_notification_deliveries
             WHERE event_id=(SELECT id FROM mobile_notification_events WHERE dedupe_key=?)""",
        (f"booking.confirmed:{suffix}",),
    )
    assert row is not None
    return int(row["id"])


def test_registration_encrypts_token_canonicalizes_uuid_and_uses_revision_etags(
    push_context,
):
    registration = _register(push_context)
    row = db.one("SELECT * FROM mobile_push_devices")
    assert row is not None
    assert row["active"] == 1
    assert row["token_hash"] != _TOKEN
    assert len(row["token_hash"]) == 64
    assert row["token_ciphertext"].startswith("v1.")
    assert _TOKEN not in row["token_ciphertext"]
    assert row["origin"] == "https://canonical.studio.test"
    assert row["workspace_cache_namespace"].startswith("workspace_")
    assert _TOKEN not in repr(registration)

    first_etag = push_notifications.device_etag(registration)
    updated = push_notifications.update_current_preferences(
        push_context.pair.principal,
        {"payments": False},
        if_match=first_etag,
    )
    assert updated.preferences["payments"] is False
    assert updated.revision == registration.revision + 1
    assert push_notifications.device_etag(updated) != first_etag

    restored = push_notifications.update_current_preferences(
        push_context.pair.principal,
        {"payments": True},
        if_match=push_notifications.device_etag(updated),
    )
    assert restored.preferences == registration.preferences
    assert push_notifications.device_etag(restored) != first_etag

    with pytest.raises(push_notifications.PushNotificationError) as caught:
        push_notifications.update_current_preferences(
            push_context.pair.principal,
            {"payments": True},
            if_match=first_etag,
        )
    assert caught.value.code == "resource.version_conflict"

    assert mobile_auth.logout(push_context.request, push_context.pair.access_token)
    erased = db.one("SELECT active,token_ciphertext,revision FROM mobile_push_devices")
    assert dict(erased) == {
        "active": 0,
        "token_ciphertext": None,
        "revision": restored.revision + 1,
    }


def test_event_delivery_and_job_snapshot_commit_or_roll_back_together(push_context):
    _register(push_context)
    title, body = push_notifications.alert_copy("new_bookings")

    with pytest.raises(RuntimeError, match="source transition failed"):
        with db.tx() as con:
            push_notifications.enqueue_owner_event_tx(
                con,
                dedupe_key="booking.confirmed:rollback",
                category="new_bookings",
                route="/app/bookings/40",
                title=title,
                body=body,
            )
            raise RuntimeError("source transition failed")

    assert db.one("SELECT COUNT(*) AS n FROM mobile_notification_events")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM mobile_notification_deliveries")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='apns_delivery'")["n"] == 0

    delivery_id = _enqueue(push_context, "commit", "/app/bookings/40")
    assert delivery_id > 0
    with db.tx() as con:
        duplicate = push_notifications.enqueue_owner_event_tx(
            con,
            dedupe_key="booking.confirmed:commit",
            category="new_bookings",
            route="/app/bookings/40",
            title=title,
            body=body,
        )
    assert duplicate == []
    assert db.one("SELECT COUNT(*) AS n FROM mobile_notification_events")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM mobile_notification_deliveries")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='apns_delivery'")["n"] == 1


def test_delivery_rechecks_session_and_sends_only_generic_typed_payload(push_context, monkeypatch):
    _register(push_context)
    delivery_id = _enqueue(push_context, "deliver")
    captured = {}

    monkeypatch.setattr(apns, "configured", lambda: True)

    def send(**kwargs):
        captured.update(kwargs)
        return apns.APNsResponse(
            status_code=200,
            reason=None,
            apns_id=kwargs["apns_id"],
        )

    monkeypatch.setattr(apns, "send", send)
    push_notifications.deliver(delivery_id)

    delivery = db.one(
        "SELECT status,attempts,delivered_at,apns_id FROM mobile_notification_deliveries WHERE id=?",
        (delivery_id,),
    )
    assert delivery["status"] == "delivered"
    assert delivery["attempts"] == 1
    assert delivery["delivered_at"] is not None
    assert captured["device_token"] == _TOKEN
    assert captured["environment"] == "sandbox"
    assert captured["apns_id"] == delivery["apns_id"]
    assert captured["collapse_id"] == captured["payload"]["mise"]["event_id"]
    assert captured["payload"]["mise"] == {
        "version": 1,
        "event_id": captured["collapse_id"],
        "workspace_origin": "https://canonical.studio.test",
        "workspace_cache_namespace": captured["payload"]["mise"]["workspace_cache_namespace"],
        "principal_kind": "studio_owner",
        "principal_id": "studio_owner",
        "route": "/app/bookings/41",
    }
    payload_text = json.dumps(captured["payload"])
    assert "client" not in payload_text.casefold()
    assert "$" not in payload_text


def test_unavailable_provider_preserves_attempt_and_invalid_token_erases_secret(
    push_context, monkeypatch
):
    _register(push_context)
    unavailable_id = _enqueue(push_context, "provider-unavailable")
    monkeypatch.setattr(apns, "configured", lambda: False)
    push_notifications.deliver(unavailable_id)
    unavailable = db.one(
        "SELECT status,attempts,reason FROM mobile_notification_deliveries WHERE id=?",
        (unavailable_id,),
    )
    assert dict(unavailable) == {
        "status": "retry",
        "attempts": 0,
        "reason": "provider_unavailable",
    }

    invalid_id = _enqueue(push_context, "invalid-token")
    monkeypatch.setattr(apns, "configured", lambda: True)
    monkeypatch.setattr(
        apns,
        "send",
        lambda **kwargs: apns.APNsResponse(
            status_code=410,
            reason="Unregistered",
            apns_id=kwargs["apns_id"],
        ),
    )
    push_notifications.deliver(invalid_id)
    device = db.one(
        "SELECT active,token_ciphertext,token_version,revision FROM mobile_push_devices"
    )
    assert dict(device) == {
        "active": 0,
        "token_ciphertext": None,
        "token_version": 2,
        "revision": 2,
    }
    invalid = db.one(
        "SELECT status,reason,http_status FROM mobile_notification_deliveries WHERE id=?",
        (invalid_id,),
    )
    assert dict(invalid) == {
        "status": "failed",
        "reason": "Unregistered",
        "http_status": 410,
    }


def test_delivery_rechecks_preference_after_claim_before_sending(push_context, monkeypatch):
    _register(push_context)
    delivery_id = _enqueue(push_context, "preference-race")
    sent = []
    original_session_check = mobile_auth.session_is_current

    def disable_after_claim(con, session_id):
        current = original_session_check(con, session_id)
        con.execute(
            """UPDATE mobile_push_devices SET pref_new_bookings=0,revision=revision+1
                 WHERE session_id=?""",
            (session_id,),
        )
        return current

    monkeypatch.setattr(mobile_auth, "session_is_current", disable_after_claim)
    monkeypatch.setattr(apns, "configured", lambda: True)
    monkeypatch.setattr(apns, "send", lambda **kwargs: sent.append(kwargs))

    push_notifications.deliver(delivery_id)

    assert sent == []
    delivery = db.one(
        "SELECT status,reason FROM mobile_notification_deliveries WHERE id=?",
        (delivery_id,),
    )
    assert delivery["status"] == "skipped"


def test_stale_invalid_response_cannot_erase_same_token_reregistration(push_context, monkeypatch):
    first = _register(push_context)
    delivery_id = _enqueue(push_context, "reregister-race")
    monkeypatch.setattr(apns, "configured", lambda: True)

    def stale_response(**kwargs):
        refreshed = _register(push_context)
        assert refreshed.revision == first.revision + 1
        return apns.APNsResponse(
            status_code=410,
            reason="Unregistered",
            apns_id=kwargs["apns_id"],
            invalidated_at=0,
        )

    monkeypatch.setattr(apns, "send", stale_response)
    push_notifications.deliver(delivery_id)

    device = db.one(
        "SELECT active,token_ciphertext,token_version,revision FROM mobile_push_devices"
    )
    assert device["active"] == 1
    assert device["token_ciphertext"] is not None
    assert device["token_version"] == 2
    assert device["revision"] == 2


@pytest.mark.parametrize("cleanup_path", ["access", "sweep", "cleanup_only"])
def test_absolute_session_expiry_erases_active_device_without_an_event(push_context, cleanup_path):
    _register(push_context)
    expired_at = int(push_notifications._now().timestamp()) - 1
    db.run(
        """UPDATE api_sessions
              SET created_at=?,last_seen_at=?,absolute_expires_at=? WHERE id=?""",
        (expired_at - 1, expired_at, expired_at, push_context.pair.session_id),
    )

    if cleanup_path == "access":
        with pytest.raises(mobile_auth.MobileAuthError):
            mobile_auth.authenticate_access(
                push_context.request,
                push_context.pair.access_token,
            )
    elif cleanup_path == "sweep":
        assert push_notifications.sweep() == 0
    else:
        assert push_notifications.sweep(dispatch=False) == 0

    device = db.one("SELECT active,token_ciphertext FROM mobile_push_devices")
    session = db.one("SELECT revoked_at,revoke_reason FROM api_sessions")
    assert dict(device) == {"active": 0, "token_ciphertext": None}
    assert session["revoked_at"] is not None
    assert session["revoke_reason"] == "session_expired"


def test_push_sweep_persists_one_dispatch_job_under_repeated_ticks(push_context):
    _register(push_context)
    delivery_id = _enqueue(push_context, "dispatch-dedupe")
    initial = db.one(
        "SELECT queued_job_id FROM mobile_notification_deliveries WHERE id=?",
        (delivery_id,),
    )
    assert initial["queued_job_id"] is not None
    assert push_notifications.sweep() == 0

    db.run("UPDATE jobs SET status='done' WHERE id=?", (initial["queued_job_id"],))
    db.run(
        """UPDATE mobile_notification_deliveries
              SET status='retry',next_attempt_at=datetime('now')
            WHERE id=?""",
        (delivery_id,),
    )
    assert push_notifications.sweep() == 1
    assert push_notifications.sweep() == 0
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='apns_delivery'")["n"] == 2


def test_cleanup_only_sweep_recovers_locked_running_job_and_reactivation_kicks_it(
    push_context,
    monkeypatch,
):
    _register(push_context)
    delivery_id = _enqueue(push_context, "locked-recovery")
    row = db.one(
        "SELECT queued_job_id FROM mobile_notification_deliveries WHERE id=?",
        (delivery_id,),
    )
    job_id = int(row["queued_job_id"])
    db.run(
        """UPDATE jobs SET status='running',
                  updated_at=datetime('now', ?)
             WHERE id=?""",
        (f"-{int(config.APNS_LEASE_SECONDS) + 1} seconds", job_id),
    )
    kicked: list[int] = []
    monkeypatch.setattr(
        push_notifications,
        "kick",
        lambda job_ids: kicked.extend(job_ids),
    )

    assert push_notifications.sweep(dispatch=False) == 0
    assert db.one("SELECT status FROM jobs WHERE id=?", (job_id,))["status"] == "queued"
    assert kicked == []

    assert push_notifications.sweep(dispatch=True) == 0
    assert kicked == [job_id]
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='apns_delivery'")["n"] == 1


def test_sweep_purges_expired_audit_and_inactive_device_after_retention(
    push_context,
    monkeypatch,
):
    monkeypatch.setattr(config, "APNS_RETENTION_DAYS", 30)
    _register(push_context)
    _enqueue(push_context, "retention")
    assert mobile_auth.logout(push_context.request, push_context.pair.access_token)
    db.run(
        """UPDATE mobile_notification_events
              SET expires_at=datetime('now','-31 days')"""
    )
    db.run(
        """UPDATE mobile_push_devices
              SET disabled_at=datetime('now','-31 days'),
                  updated_at=datetime('now','-31 days')"""
    )
    db.run(
        """UPDATE jobs SET status='done',updated_at=datetime('now','-31 days')
             WHERE kind='apns_delivery'"""
    )
    db.run(
        """INSERT INTO jobs (kind,payload,status,created_at)
             VALUES ('apns_delivery','{"delivery_id":999999}','queued',
                     datetime('now','-31 days'))"""
    )

    assert push_notifications.sweep(dispatch=False) == 0

    assert db.one("SELECT COUNT(*) AS n FROM mobile_notification_events")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM mobile_notification_deliveries")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM mobile_push_devices")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='apns_delivery'")["n"] == 0


def test_hosted_sweep_cleans_locked_tenants_without_dispatching(monkeypatch):
    tenants = [
        {"slug": "active", "plan_status": "active", "deleted_at": None},
        {"slug": "unpaid", "plan_status": "unpaid", "deleted_at": None},
        {
            "slug": "expired-trial",
            "plan_status": "trialing",
            "trial_ends_at": "2000-01-01 00:00:00",
            "deleted_at": None,
        },
        {"slug": "deleted", "plan_status": "active", "deleted_at": "2026-07-11"},
    ]
    calls: list[tuple[str, bool]] = []
    cleanup_calls: list[str] = []
    current: list[str] = []

    @contextmanager
    def tenant_runtime(tenant):
        current.append(tenant["slug"])
        try:
            yield
        finally:
            current.pop()

    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(saas, "list_tenants", lambda: tenants)
    monkeypatch.setattr(saas, "tenant_runtime", tenant_runtime)
    monkeypatch.setattr(
        push_notifications,
        "sweep",
        lambda *, dispatch: calls.append((current[-1], dispatch)),
    )
    monkeypatch.setattr(
        scheduler,
        "_mobile_content_cleanup",
        lambda: cleanup_calls.append(current[-1]),
    )

    scheduler._push_sweep_all()

    assert calls == [("active", True), ("unpaid", False), ("expired-trial", False)]
    assert cleanup_calls == ["active", "unpaid", "expired-trial"]


def test_hosted_recurring_monitor_checks_platform_backup_once_outside_tenants(
    tmp_path,
    monkeypatch,
):
    platform_dir = tmp_path / "platform"
    tenant_root = tmp_path / "tenants"
    tenants = [{"slug": "alpha"}, {"slug": "beta"}]
    backup_paths: list[str] = []
    tenant_paths: list[str] = []

    @contextmanager
    def tenant_runtime(tenant):
        tokens = config.set_runtime_dirs(tenant_root / tenant["slug"])
        try:
            yield
        finally:
            config.reset_runtime_dirs(tokens)

    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(
        saas,
        "list_tenants",
        lambda *, billable_only=False: tenants,
    )
    monkeypatch.setattr(saas, "tenant_runtime", tenant_runtime)
    for name in (
        "pending_subscription_cancel_sweep",
        "pending_tenant_offboarding_sweep",
        "purge_retired_tenant_data",
        "trial_reminder_sweep",
        "winback_sweep",
        "dunning_sweep",
        "weekly_digest_sweep",
    ):
        monkeypatch.setattr(saas, name, lambda: None)

    monkeypatch.setattr(scheduler.ops_monitor.alerts, "is_enabled", lambda: True)
    monkeypatch.setattr(scheduler.ops_monitor, "_check_disk", lambda: None)
    monkeypatch.setattr(
        scheduler.ops_monitor,
        "_check_backup",
        lambda: backup_paths.append(str(config.DATA_DIR)),
    )
    monkeypatch.setattr(
        scheduler.recurring,
        "run_due_plans",
        lambda: tenant_paths.append(str(config.DATA_DIR)),
    )
    for module in (
        scheduler.booking_reminders,
        scheduler.gallery_reminders,
        scheduler.contract_reminders,
        scheduler.retainer_reminders,
        scheduler.postshoot_reminders,
    ):
        monkeypatch.setattr(module, "sweep", lambda: None)

    tokens = config.set_runtime_dirs(platform_dir)
    try:
        scheduler._recurring_sweep_all()
    finally:
        config.reset_runtime_dirs(tokens)

    assert backup_paths == [str(platform_dir)]
    assert tenant_paths == [
        str(tenant_root / "alpha"),
        str(tenant_root / "beta"),
    ]


def test_retry_floors_honor_apns_operational_guidance(monkeypatch):
    monkeypatch.setattr(config, "APNS_RETRY_BASE_SECONDS", 1)
    monkeypatch.setattr(config, "APNS_RETRY_MAX_SECONDS", 120)
    monkeypatch.setattr(push_notifications.secrets, "randbelow", lambda _: 0)

    assert (
        push_notifications._retry_delay(
            1,
            apns.APNsResponse(503, "ServiceUnavailable", "event"),
        )
        >= 15 * 60
    )
    assert (
        push_notifications._retry_delay(
            1,
            apns.APNsResponse(429, "TooManyRequests", "event", retry_after_seconds=1800),
        )
        >= 1800
    )
    assert (
        push_notifications._retry_delay(
            1,
            apns.APNsResponse(403, "TooManyProviderTokenUpdates", "event"),
        )
        >= 20 * 60
    )
