from pathlib import Path

from app import config, saas_preflight


def _configure_ready_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control" / "saas.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SAAS_TRIAL_DAYS", 14)
    monkeypatch.setattr(config, "SAAS_PRICE_CENTS", 2000)
    monkeypatch.setattr(config, "SECRET_KEY", "not-a-default-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-password")
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_test")
    monkeypatch.setattr(config, "SAAS_STRIPE_PRICE_ID", "price_20_monthly")
    monkeypatch.setattr(config, "SAAS_STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(config, "GMAIL_USER", "")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "")


def test_preflight_passes_launch_critical_checks_with_email_warning(tmp_path, monkeypatch):
    _configure_ready_saas(tmp_path, monkeypatch)

    report = saas_preflight.check_readiness(
        project_root=Path.cwd(),
        write_probes=True,
    )

    assert report["ready"] is True
    assert report["failures"] == 0
    assert report["warnings"] == 1
    assert next(c for c in report["checks"] if c["key"] == "email")["status"] == "warn"
    assert "READY" in saas_preflight.format_text(report)


def test_preflight_fails_missing_hosted_contract(tmp_path, monkeypatch):
    _configure_ready_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "BASE_URL", "http://localhost:8400")
    monkeypatch.setattr(config, "SAAS_PRICE_CENTS", 1900)
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(config, "SAAS_STRIPE_PRICE_ID", "")

    report = saas_preflight.check_readiness(project_root=Path.cwd(), write_probes=False)
    failed = {check["key"] for check in report["checks"] if check["status"] == "fail"}

    assert report["ready"] is False
    assert {"saas_mode", "price", "cookie_secure", "stripe_checkout"} <= failed
