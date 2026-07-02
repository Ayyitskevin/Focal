"""The money rehearsal, as code (ADR 0060).

One narrative test walking the ENTIRE hosted customer lifecycle in-process —
the same journey docs/LAUNCH-PLAYBOOK.md walks manually against the real box in
Stripe test mode. If this passes, the state machine behind the rehearsal is
sound; the manual pass then only has to prove the *wiring* (DNS, TLS, real
Stripe keys, real webhooks).
"""

import asyncio
import types
from datetime import timedelta

import pytest

from app import config, features, passwords, saas, security

pytestmark = pytest.mark.unit


def _configure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SAAS_TRIAL_DAYS", 14)
    monkeypatch.setattr(config, "SECRET_KEY", "rehearsal-secret")
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "beta-rehearsal")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_platform")
    monkeypatch.setattr(config, "SAAS_STRIPE_PRICE_ID", "price_20")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path, host, *, cookie=None, method="GET"):
    from starlette.requests import Request

    headers = [(b"host", host.encode()), (b"accept", b"text/html")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": headers,
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def _fake_platform_stripe(cancelled: list):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(id=f"cs_{len(calls)}", url="https://checkout.stripe.test/cs")

    class AuthError(Exception):
        pass

    class PermError(Exception):
        pass

    class Subscription:
        @staticmethod
        def cancel(sub_id, api_key=None):
            cancelled.append(sub_id)

    class Account:
        @staticmethod
        def retrieve(api_key=None):
            return {"id": "acct_1"}

    return types.SimpleNamespace(
        checkout=types.SimpleNamespace(Session=types.SimpleNamespace(create=create)),
        Subscription=Subscription,
        Account=Account,
        AuthenticationError=AuthError,
        PermissionError=PermError,
    ), calls


def test_full_hosted_lifecycle_rehearsal(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    cancelled: list = []
    fake, checkout_calls = _fake_platform_stripe(cancelled)
    monkeypatch.setattr(saas, "_stripe", lambda: fake)
    emails = []
    monkeypatch.setattr(saas.mailer, "configured", lambda: True)
    monkeypatch.setattr(saas.mailer, "send", lambda *a, **k: emails.append(a))

    # 1. Gated signup: wrong code bounces before provisioning; right code provisions
    #    and redirects into the platform checkout.
    bad = asyncio.run(
        saas.start_trial(
            _request("/start-trial", "mise.test", method="POST"),
            studio_name="Rehearsal Studio",
            owner_email="owner@example.com",
            slug="rehearsal",
            password="secret123",
            invite_code="wrong",
        )
    )
    assert bad.status_code == 403 and saas.tenant_by_slug("rehearsal") is None
    ok = asyncio.run(
        saas.start_trial(
            _request("/start-trial", "mise.test", method="POST"),
            studio_name="Rehearsal Studio",
            owner_email="owner@example.com",
            slug="rehearsal",
            password="secret123",
            invite_code="beta-rehearsal",
        )
    )
    assert ok.status_code == 303 and ok.headers["location"].startswith("https://checkout")
    tenant = saas.tenant_by_slug("rehearsal")
    assert tenant["plan_status"] == "trialing"

    # 2. The welcome email carries the studio URL (the abandoned-checkout lifeline).
    assert ok.background is not None
    asyncio.run(ok.background())
    assert any("https://rehearsal.mise.test" in e[2] for e in emails)

    # 3. Platform webhook activates the subscription — exactly once.
    event = {
        "id": "evt_activate",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_r1",
                "status": "active",
                "customer": "cus_r1",
                "metadata": {"tenant_id": str(tenant["id"]), "slug": "rehearsal"},
            }
        },
    }
    assert saas._process_saas_event(event)["type"] == "customer.subscription.updated"
    assert saas._process_saas_event(event) == {"ok": True, "duplicate": True}
    tenant = saas.tenant_by_slug("rehearsal")
    assert tenant["plan_status"] == "active" and tenant["stripe_customer_id"] == "cus_r1"

    # 4. The tenant connects their OWN Stripe: client payments flip from
    #    fail-closed off to live (ADR 0049/0054).
    fp = security._pw_fp(saas.tenant_by_slug("rehearsal")["admin_password_hash"])
    principal = f"tenant:{tenant['id']}:rehearsal:{fp}"
    cookie = f"{security.ADMIN_COOKIE}={security.sign(principal)}"
    with saas.tenant_runtime("rehearsal"):
        assert features.stripe_enabled() is False
        resp = asyncio.run(
            saas.update_account_payments(
                _request(
                    "/admin/account/payments", "rehearsal.mise.test", cookie=cookie, method="POST"
                ),
                stripe_secret_key="sk_test_tenant_own_key",
                stripe_webhook_secret="whsec_tenant_own",
            )
        )
        assert resp.status_code == 303
    with saas.tenant_runtime("rehearsal"):
        assert features.stripe_enabled() is True

    # 5. A card decline is a grace window, not a lockout (ADR 0050)...
    saas.update_tenant_billing(tenant["id"], plan_status="past_due")
    fresh = saas.tenant_by_slug("rehearsal")
    assert saas.tenant_has_access(fresh) is True
    assert saas.tenant_billing_context(fresh)["tone"] == "warn"

    # 6. ...and a canceled subscription can restart from the day-14 paywall —
    #    the spent trial bills immediately, no free re-grant (ADR 0056).
    saas.update_tenant_billing(tenant["id"], plan_status="canceled")
    with saas.control_connect() as con:
        con.execute(
            "UPDATE tenants SET trial_ends_at=? WHERE id=?",
            (saas._iso(saas._now() - timedelta(days=1)), tenant["id"]),
        )
    fresh = saas.tenant_by_slug("rehearsal")
    assert saas.tenant_has_access(fresh) is False
    checkout_calls.clear()
    with saas.tenant_runtime("rehearsal"):
        resp = asyncio.run(
            saas.billing_checkout(
                _request(
                    "/admin/billing/checkout", "rehearsal.mise.test", cookie=cookie, method="POST"
                )
            )
        )
    assert resp.status_code == 303 and len(checkout_calls) == 1
    assert "trial_period_days" not in checkout_calls[0]["subscription_data"]

    # 7. Ownership promises: export the whole studio, then delete it —
    #    delete cancels billing and frees the address (ADR 0051).
    saas.ensure_tenant_database(tenant)
    zip_path = saas.build_studio_export(saas.tenant_by_slug("rehearsal"))
    try:
        assert zip_path.exists() and zip_path.stat().st_size > 0
    finally:
        zip_path.unlink(missing_ok=True)
    saas.update_tenant_billing(tenant["id"], plan_status="active", stripe_subscription_id="sub_r1")
    saas.delete_tenant_studio(saas.tenant_by_slug("rehearsal"))
    assert cancelled == ["sub_r1"]
    assert saas.tenant_by_slug("rehearsal") is None
    tombstone = saas.tenant_by_id(tenant["id"])
    assert tombstone["deleted_at"] and tombstone["plan_status"] == "canceled"

    # 8. The address is genuinely reusable: a new signup can claim the freed slug,
    #    and the old admin cookie does NOT work against the new studio (ADR 0048/0051).
    again = asyncio.run(
        saas.start_trial(
            _request("/start-trial", "mise.test", method="POST"),
            studio_name="Second Life Studio",
            owner_email="second@example.com",
            slug="rehearsal",
            password="secret456",
            invite_code="beta-rehearsal",
        )
    )
    assert again.status_code == 303
    reborn = saas.tenant_by_slug("rehearsal")
    assert reborn["id"] != tenant["id"]
    with saas.tenant_runtime("rehearsal"):
        stale = _request("/admin/account", "rehearsal.mise.test", cookie=cookie)
        assert security.is_admin(stale) is False  # id-bound principal rejects the reclaim
        assert passwords.verify_password("secret456", reborn["admin_password_hash"])
