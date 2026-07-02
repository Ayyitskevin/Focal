"""Security Slice 5 (ADR 0065): logging, audit trail, final defenses.

Three properties locked here:
  1. The CSP's load-bearing directives can't silently regress (they're the
     backstop for the documented 'unsafe-inline' tradeoff).
  2. Credentials never reach the process log: a failed login doesn't log the
     attempted password; a failed webhook doesn't log the signing secret.
  3. Hosted auth-audit lines are tenant-attributable — gallery/portal ids
     restart per tenant, so without the label a "bad PIN" line can't be tied
     to a studio during incident forensics.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app import alerts, config, db, ratelimit, saas, security
from app.main import CSP_POLICY, app

pytestmark = pytest.mark.unit


# --- CSP: the directives doing the real work ---------------------------------


def test_csp_locks_the_load_bearing_directives():
    directives = {d.split(" ", 1)[0]: d for d in CSP_POLICY.split("; ")}
    # These four are the hardening-in-depth backstop for 'unsafe-inline':
    # no plugin/applet execution, no clickjacking frame, no form exfil, no
    # <base> hijack of every relative URL on the page.
    assert directives["object-src"] == "object-src 'none'"
    assert directives["frame-ancestors"] == "frame-ancestors 'none'"
    assert directives["form-action"] == "form-action 'self'"
    assert directives["base-uri"] == "base-uri 'self'"
    assert directives["default-src"] == "default-src 'self'"


def test_csp_never_allows_unsafe_eval():
    assert "unsafe-eval" not in CSP_POLICY


# --- no credentials in logs ---------------------------------------------------


def _clean_slate():
    ratelimit._hits.clear()
    db.run("DELETE FROM pin_attempts")


def test_failed_login_never_logs_the_attempted_password(caplog):
    _clean_slate()
    attempted = "hunter2-attempted-password"
    with caplog.at_level(logging.DEBUG), TestClient(app) as client:
        r = client.post("/admin/login", data={"password": attempted})
    assert r.status_code == 401
    # The failure IS logged (audit trail) — but only IP + bucket, never the value.
    assert "bad PIN" in caplog.text
    assert attempted not in caplog.text
    assert config.ADMIN_PASSWORD not in caplog.text
    _clean_slate()


def test_failed_webhook_never_logs_the_signing_secret(monkeypatch, caplog):
    _clean_slate()
    secret = "whsec_slice5_do_not_log_me"
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", secret)
    with caplog.at_level(logging.DEBUG), TestClient(app) as client:
        r = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=forged"},
        )
    assert r.status_code == 400
    # The rejection is logged for the operator — the secret value never is.
    assert "signature failed" in caplog.text
    assert secret not in caplog.text
    _clean_slate()


# --- hosted auth-audit lines carry the tenant --------------------------------


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "slice5-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def test_single_tenant_log_label_is_empty(monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    assert security.tenant_log_label() == ""


def test_hosted_pin_failure_log_and_lockout_alert_name_the_tenant(tmp_path, monkeypatch, caplog):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    fired = []
    monkeypatch.setattr(alerts, "security_alert", lambda text: fired.append(text))

    with caplog.at_level(logging.WARNING), saas.tenant_runtime("alpha"):
        for _ in range(config.PIN_MAX_FAILS):
            security.pin_fail("203.0.113.9", 3)

    assert "[tenant:alpha]" in caplog.text  # every failure line is attributable
    assert len(fired) == 1  # alert fires exactly at the threshold
    assert "[tenant:alpha]" in fired[0]


def test_operator_context_log_label_says_platform(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    # No tenant in context (root-host/operator request) — still attributable.
    assert security.tenant_log_label() == " [platform]"
