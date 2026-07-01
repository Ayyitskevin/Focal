from app import config, db, passwords, saas, saas_demo, security


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SAAS_TRIAL_DAYS", 14)
    monkeypatch.setattr(config, "SAAS_PRICE_CENTS", 2000)
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


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
