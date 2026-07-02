"""Batch C / Slice C1: post-expiry win-back sweep.

Before this slice a lapsed trial got no follow-up EVER (the trial reminder is
pre-expiry and one-shot) and canceled subscribers got none at all. The sweep
sends exactly one come-back email per tenant — a door held open, not a drip
campaign — stamped only after a successful send so failures retry.
"""

import pytest

from app import config, mailer, saas

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "c1-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
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


def test_lapsed_trial_gets_exactly_one_winback(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _set(t["id"], trial_ends_at="2026-06-20 12:00:00")  # lapsed 12 days ago

    assert saas.winback_sweep() == 1
    to, subject, body = outbox[0]
    assert to == "alpha@example.com"
    assert "still here" in subject
    assert "alpha.mise.test/admin/billing" in body
    assert "export" in body.lower()  # the ownership promise rides along
    # One-shot: the second sweep sends nothing.
    assert saas.winback_sweep() == 0
    assert len(outbox) == 1


def test_freshly_lapsed_trial_waits_out_the_delay(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    ends = saas._iso(saas._now() - saas.timedelta(days=saas.WINBACK_DELAY_DAYS - 1))
    _set(t["id"], trial_ends_at=ends)  # lapsed, but inside the quiet window
    assert saas.winback_sweep() == 0
    # An ACTIVE trial (future end) is never touched either.
    _set(t["id"], trial_ends_at=saas._iso(saas._now() + saas.timedelta(days=5)))
    assert saas.winback_sweep() == 0


def test_canceled_tenant_gets_the_winback_after_the_delay(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _set(t["id"], plan_status="canceled", updated_at="2026-06-20 12:00:00")
    assert saas.winback_sweep() == 1
    assert "subscription ended" in outbox[0][2]


def test_active_and_deleted_tenants_are_never_emailed(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    a = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _set(a["id"], plan_status="active")
    b = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    _set(
        b["id"],
        trial_ends_at="2026-06-01 12:00:00",
        deleted_at="2026-06-15 12:00:00",
    )
    assert saas.winback_sweep() == 0 and outbox == []


def test_failed_send_is_not_stamped_and_retries(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _set(t["id"], trial_ends_at="2026-06-20 12:00:00")
    monkeypatch.setattr(mailer, "configured", lambda: True)

    def boom(*a, **kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(mailer, "send", boom)
    assert saas.winback_sweep() == 0
    assert saas.tenant_by_slug("alpha").get("winback_sent_at") is None  # retries next sweep

    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, s, b, **kw: sent.append(to))
    assert saas.winback_sweep() == 1
    assert saas.tenant_by_slug("alpha")["winback_sent_at"] is not None
