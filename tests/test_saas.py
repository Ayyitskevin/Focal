import pytest

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
