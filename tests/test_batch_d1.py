"""Batch D / Slice D1: weekly operator digest.

The operator console only helps if the operator remembers to open it. One
platform email per ISO week now delivers its headline — signups, at-risk
trials, fresh feedback, waitlist growth, lifecycle-mail activity — the only
sweep addressed to the OPERATOR rather than a tenant. Stamped in control_meta
only after a successful send, so failures retry and restarts never double-send.
"""

import pytest

from app import config, mailer, saas

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "d1-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SAAS_SUPPORT_EMAIL", "operator@example.com")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(
        mailer, "send", lambda to, subject, body, **kw: sent.append((to, subject, body))
    )
    return sent


def _set(tenant_id, **cols):
    sets = ", ".join(f"{k}=?" for k in cols)
    with saas.control_connect() as con:
        con.execute(f"UPDATE tenants SET {sets} WHERE id=?", (*cols.values(), tenant_id))


def test_digest_delivers_the_console_headline_once_per_week(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    a = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    b = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    _set(b["id"], plan_status="active")
    _set(a["id"], trial_reminder_sent_at=saas._iso(saas._now() - saas.timedelta(days=2)))
    saas.record_tenant_feedback(a["id"], "help", "Where do galleries live?")
    saas.join_waitlist("hopeful@example.com")

    assert saas.weekly_digest_sweep() == 1
    to, subject, body = outbox[0]
    assert to == "operator@example.com"  # the operator, not a tenant
    assert subject.startswith("Mise weekly — 2 studios")
    assert "1 paying ($20/mo), 1 trialing" in body
    assert "New studios: 2" in body
    assert "Waitlist joins: 1 (total 1)" in body
    assert "Feedback notes: 1" in body
    assert "1 trial reminder, 0 win-backs" in body
    assert "Alpha Studio (help): Where do galleries live?" in body
    assert "mise.test/admin/saas" in body

    # Same ISO week: one-shot. A NEW week (stale stamp) sends again.
    assert saas.weekly_digest_sweep() == 0 and len(outbox) == 1
    saas._meta_set("digest_last_week", "2020-W01")
    assert saas.weekly_digest_sweep() == 1 and len(outbox) == 2


def test_at_risk_trials_and_billing_recovery_need_a_human(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    a = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    b = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    # Alpha's trial is nearly over with setup unfinished; Beta's card declined.
    _set(a["id"], trial_ends_at=saas._iso(saas._now() + saas.timedelta(days=2)))
    _set(b["id"], plan_status="past_due")

    assert saas.weekly_digest_sweep() == 1
    body = outbox[0][2]
    assert "Needs a human:" in body
    assert "Trial rescue: Alpha Studio" in body
    assert "Billing recovery: Beta Studio" in body


def test_failed_send_is_not_stamped_and_retries(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    monkeypatch.setattr(mailer, "configured", lambda: True)

    def boom(*a, **kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(mailer, "send", boom)
    assert saas.weekly_digest_sweep() == 0
    assert saas._meta_get("digest_last_week") is None  # unstamped: next tick retries

    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body, **kw: sent.append(to))
    assert saas.weekly_digest_sweep() == 1 and sent == ["operator@example.com"]


def test_digest_requires_mailer_and_operator_address(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    monkeypatch.setattr(config, "SAAS_SUPPORT_EMAIL", "")
    assert saas.weekly_digest_sweep() == 0 and outbox == []
