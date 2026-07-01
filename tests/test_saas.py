import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from starlette.requests import Request

from app import config, db, passwords, saas, saas_demo, security


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SAAS_TRIAL_DAYS", 14)
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path: str, host: str, *, cookie: str | None = None) -> Request:
    headers = [(b"host", host.encode()), (b"accept", b"text/html")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": headers,
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def test_hosted_price_is_locked_to_twenty_dollars():
    assert config.SAAS_PRICE_CENTS == 2000


def test_password_hash_verifies_and_rejects_wrong_password():
    encoded = passwords.hash_password("correct horse")
    assert passwords.verify_password("correct horse", encoded)
    assert not passwords.verify_password("wrong horse", encoded)


def test_tenant_slug_from_host(monkeypatch):
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")

    assert saas.tenant_slug_from_host("river.mise.test") == "river"
    assert saas.tenant_slug_from_host("mise.test") is None
    assert saas.tenant_slug_from_host("www.mise.test") is None
    assert saas.tenant_slug_from_host("too.deep.mise.test") is None
    assert saas._platform_path("/webhooks/stripe")
    assert saas._platform_path("/webhooks/stripe/saas")
    assert saas._platform_path("/demo")
    assert saas._platform_path("/admin/login")
    assert saas._platform_path("/admin/saas")


def test_tenant_databases_are_isolated(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    alpha = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    beta = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")

    with saas.tenant_runtime(alpha):
        db.run(
            "INSERT INTO clients (name, email, market) VALUES (?,?,?)",
            ("Alpha Client", "client@alpha.test", "demo"),
        )
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 1
        assert security.check_admin_password("secret123")
        assert not security.check_admin_password("wrong")

    with saas.tenant_runtime(beta):
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 0
        db.run(
            "INSERT INTO clients (name, email, market) VALUES (?,?,?)",
            ("Beta Client", "client@beta.test", "demo"),
        )
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 1

    with saas.tenant_runtime(alpha):
        assert db.one("SELECT name FROM clients")["name"] == "Alpha Client"


def test_platform_admin_password_is_separate_from_tenant_password(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    assert security.check_admin_password("operator-secret")
    assert not security.check_admin_password("secret123")

    with saas.tenant_runtime(tenant):
        assert security.check_admin_password("secret123")
        assert not security.check_admin_password("operator-secret")


def test_account_settings_update_custom_domain_and_branding(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    updated = saas.update_tenant_account(
        tenant["id"],
        studio_name="Alpha Weddings",
        owner_email="owner@alpha.test",
        custom_domain="https://clients.alpha.test/",
        brand_accent="#A1B2C3",
    )

    assert updated["studio_name"] == "Alpha Weddings"
    assert updated["owner_email"] == "owner@alpha.test"
    assert updated["custom_domain"] == "clients.alpha.test"
    assert updated["brand_accent"] == "#a1b2c3"
    assert updated["custom_domain_verified_at"] is None
    assert saas.tenant_slug_from_host("clients.alpha.test") == "alpha"
    assert saas.tenant_slug_from_host("alpha.mise.test") == "alpha"


def test_custom_domain_is_unique_per_tenant(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    alpha = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    beta = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    saas.update_tenant_account(
        alpha["id"],
        studio_name="Alpha Studio",
        owner_email="alpha@example.com",
        custom_domain="clients.alpha.test",
        brand_accent="#2f5c45",
    )

    with pytest.raises(ValueError, match="already connected"):
        saas.update_tenant_account(
            beta["id"],
            studio_name="Beta Studio",
            owner_email="beta@example.com",
            custom_domain="clients.alpha.test",
            brand_accent="#2f5c45",
        )


def test_custom_domain_verification_marks_seen_host(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    tenant = saas.update_tenant_account(
        tenant["id"],
        studio_name="Alpha Studio",
        owner_email="alpha@example.com",
        custom_domain="clients.alpha.test",
        brand_accent="#2f5c45",
    )

    verified = saas.mark_custom_domain_verified(tenant, "clients.alpha.test")

    assert verified["custom_domain_verified_at"]


def test_operator_overview_summarizes_tenants(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    alpha = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    beta = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    saas.update_tenant_billing(alpha["id"], plan_status="active")
    saas.update_tenant_billing(beta["id"], plan_status="past_due")
    saas.update_tenant_account(
        beta["id"],
        studio_name="Beta Studio",
        owner_email="beta@example.com",
        custom_domain="clients.beta.test",
        brand_accent="#2f5c45",
    )

    overview = saas.operator_tenant_overview()

    assert overview["counts"]["total"] == 2
    assert overview["counts"]["active"] == 1
    assert overview["counts"]["attention"] == 1
    assert overview["counts"]["custom_domains_pending"] == 1
    assert overview["counts"]["active_mrr_cents"] == 2000
    assert overview["counts"]["trial_pipeline_cents"] == 0
    assert overview["counts"]["support_queue"] == 2
    beta_row = next(r for r in overview["rows"] if r["tenant"]["slug"] == "beta")
    assert beta_row["domain_state"] == "pending"
    assert beta_row["tenant_url"] == "https://beta.mise.test/admin/login"


def test_operator_support_actions_update_billing_and_domain(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    tenant = saas.update_tenant_account(
        tenant["id"],
        studio_name="Alpha Studio",
        owner_email="alpha@example.com",
        custom_domain="clients.alpha.test",
        brand_accent="#2f5c45",
    )

    updated = saas.operator_update_tenant_status(tenant["id"], "active")
    assert updated["plan_status"] == "active"

    verified = saas.operator_set_domain_verified(tenant["id"], verified=True)
    assert verified["custom_domain_verified_at"]
    reset = saas.operator_set_domain_verified(tenant["id"], verified=False)
    assert reset["custom_domain_verified_at"] is None

    with pytest.raises(ValueError, match="Unsupported"):
        saas.operator_update_tenant_status(tenant["id"], "enterprise")


def test_operator_console_renders_for_platform_admin(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-secret")
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(config, "SAAS_STRIPE_PRICE_ID", "price_20")
    monkeypatch.setattr(config, "SAAS_STRIPE_WEBHOOK_SECRET", "whsec_test")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    cookie = f"{security.ADMIN_COOKIE}={security.sign('admin')}"

    response = asyncio.run(
        saas.operator_console(_request("/admin/saas", "mise.test", cookie=cookie))
    )

    assert response.status_code == 200
    assert response.context["overview"]["counts"]["total"] == 1
    assert response.context["price_cents"] == 2000


def test_billing_portal_uses_customer_and_return_url(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.update_tenant_billing(tenant["id"], stripe_customer_id="cus_123")
    tenant = saas.tenant_by_slug("alpha")
    seen = {}

    class FakeSession:
        @staticmethod
        def create(**kwargs):
            seen.update(kwargs)
            return type("Session", (), {"url": "https://billing.stripe.test/session"})()

    class FakeBillingPortal:
        Session = FakeSession

    class FakeStripe:
        billing_portal = FakeBillingPortal

    monkeypatch.setattr(saas, "_stripe", lambda: FakeStripe)

    url = saas.create_billing_portal_url(tenant, "https://alpha.mise.test/admin/billing")

    assert url == "https://billing.stripe.test/session"
    assert seen == {
        "api_key": "sk_test",
        "customer": "cus_123",
        "return_url": "https://alpha.mise.test/admin/billing",
    }


def test_trial_access_expires_and_billing_context_blocks(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    started = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(saas, "_now", lambda: started)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    assert saas.tenant_has_access(tenant)
    active_trial = saas.tenant_billing_context(tenant)
    assert active_trial["tone"] == "ok"
    assert active_trial["access_ok"] is True

    monkeypatch.setattr(saas, "_now", lambda: started + timedelta(days=15))
    expired = saas.tenant_by_slug("alpha")
    assert not saas.tenant_has_access(expired)
    blocked = saas.tenant_billing_context(expired)
    assert blocked["tone"] == "block"
    assert blocked["access_ok"] is False
    assert "Trial ended" in blocked["message"]


def test_billing_context_warns_near_trial_end_and_ok_for_active(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    started = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(saas, "_now", lambda: started)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    monkeypatch.setattr(saas, "_now", lambda: started + timedelta(days=12))
    warning = saas.tenant_billing_context(saas.tenant_by_slug("alpha"))
    assert warning["tone"] == "warn"
    assert warning["access_ok"] is True
    assert "Trial ends" in warning["message"]

    saas.update_tenant_billing(tenant["id"], plan_status="active")
    active = saas.tenant_billing_context(saas.tenant_by_slug("alpha"))
    assert active["tone"] == "ok"
    assert active["message"] == "Hosted plan active at $20/month."


def test_onboarding_demo_seeds_project_flow(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("demostudio", "Demo Studio", "demo@example.com", "secret123")

    with saas.tenant_runtime(tenant):
        result = saas_demo.seed_preset("wedding")
        assert result["created"] is True
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 1
        assert db.one("SELECT status FROM projects")["status"] == "contract_signed"
        assert db.one("SELECT status FROM proposals")["status"] == "accepted"
        assert db.one("SELECT status FROM contracts")["status"] == "signed"
        assert db.one("SELECT status FROM invoices")["status"] == "sent"
        assert db.one("SELECT published FROM galleries")["published"] == 1

        again = saas_demo.seed_preset("wedding")
        assert again["created"] is False
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 1
