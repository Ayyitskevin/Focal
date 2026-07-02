"""Batch C / Slice C2: platform dunning email.

Before this slice a card decline was silent from Mise's side — Stripe's own
retry emails plus an in-admin banner the owner only sees by visiting. Two
one-shot emails per decline EPISODE: the notice when past_due lands, the final
warning as the ADR 0050 grace window runs out. Stamps clear on recovery so a
future decline notifies again.
"""

import pytest

from app import config, mailer, saas

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "c2-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SAAS_PAST_DUE_GRACE_DAYS", 10)
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


def _ago(days):
    return saas._iso(saas._now() - saas.timedelta(days=days))


def test_decline_notice_fires_once(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _set(t["id"], plan_status="past_due", updated_at=_ago(0))

    assert saas.dunning_sweep() == 1
    to, subject, body = outbox[0]
    assert to == "alpha@example.com" and "declined" in subject.lower()
    assert "alpha.mise.test/admin/billing" in body
    # Fresh episode, grace barely started: no final warning yet; notice is one-shot.
    assert saas.dunning_sweep() == 0 and len(outbox) == 1


def test_final_warning_fires_as_grace_runs_out(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    # Flipped 9 days ago on a 10-day grace: 1 day left (<= 2-day threshold).
    _set(t["id"], plan_status="past_due", updated_at=_ago(9), dunning_notice_sent_at=_ago(9))

    assert saas.dunning_sweep() == 1
    to, subject, body = outbox[0]
    assert "pauses" in subject
    assert "export" in body.lower()  # the ownership promise rides along
    assert saas.dunning_sweep() == 0 and len(outbox) == 1  # final is one-shot too


def test_recovery_resets_the_episode(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _set(
        t["id"],
        plan_status="active",
        dunning_notice_sent_at=_ago(30),
        dunning_final_sent_at=_ago(25),
    )
    assert saas.dunning_sweep() == 0  # recovered: nothing sent, stamps cleared
    row = saas.tenant_by_slug("alpha")
    assert row["dunning_notice_sent_at"] is None and row["dunning_final_sent_at"] is None
    # A NEW decline months later notifies again.
    _set(t["id"], plan_status="past_due", updated_at=_ago(0))
    assert saas.dunning_sweep() == 1 and len(outbox) == 1


def test_healthy_and_deleted_tenants_are_never_emailed(tmp_path, monkeypatch, outbox):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")  # trialing
    b = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    _set(b["id"], plan_status="past_due", updated_at=_ago(1), deleted_at=_ago(0))
    assert saas.dunning_sweep() == 0 and outbox == []


def test_failed_send_is_not_stamped(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    t = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _set(t["id"], plan_status="past_due", updated_at=_ago(0))
    monkeypatch.setattr(mailer, "configured", lambda: True)

    def boom(*a, **kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(mailer, "send", boom)
    assert saas.dunning_sweep() == 0
    assert saas.tenant_by_slug("alpha").get("dunning_notice_sent_at") is None
