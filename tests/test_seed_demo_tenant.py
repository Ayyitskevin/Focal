"""Reviewer demo-studio seed script (Conductor plan T3).

Pins the safety and correctness properties the Stage-1 review of #178 required:
the seed grants non-expiring access WITHOUT fabricating paid MRR, refuses to touch
a real tenant that happens to share the slug, keeps a future booking convergently
(so the demo never decays), and validates the niche preset instead of silently
defaulting.
"""

import importlib.util
from pathlib import Path

import pytest

from app import config, saas

pytestmark = pytest.mark.unit


def _load_seeder():
    path = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo_tenant.py"
    spec = importlib.util.spec_from_file_location("seed_demo_tenant_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "demo-seed-secret")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _run(preset="wedding", slug="demo-tour", password="review-me-please"):
    return _load_seeder().seed_demo_tenant(
        slug=slug,
        studio_name="Mise Demo Studio",
        owner_email="reviewer@demo.mise.local",
        password=password,
        preset=preset,
    )


def _no_active_tenants() -> bool:
    with saas.control_connect() as con:
        return (
            con.execute("SELECT COUNT(*) FROM tenants WHERE plan_status='active'").fetchone()[0]
            == 0
        )


def test_grants_nonexpiring_access_without_fabricating_paid_mrr(hosted):
    _run()
    tenant = saas.tenant_by_slug("demo-tour")
    # Non-expiring: a trialing tenant with a far-future end has full access forever.
    assert tenant["plan_status"] == "trialing"
    assert tenant["trial_ends_at"].startswith("2099")
    assert saas.tenant_has_access(tenant) is True
    # Revenue truth: MRR counts only 'active' tenants — the demo must not be one.
    assert _no_active_tenants()

    with saas.tenant_runtime(tenant):
        from app import db

        client = db.one("SELECT id FROM clients WHERE email=?", ("demo+wedding@mise.local",))
        assert client is not None
        assert db.one("SELECT COUNT(*) AS n FROM invoices")["n"] >= 1
        assert db.one("SELECT COUNT(*) AS n FROM tasks WHERE done=0")["n"] >= 1
        booking = db.one("SELECT status, start_utc FROM bookings ORDER BY id LIMIT 1")
        assert booking is not None and booking["status"] == "confirmed"
        assert booking["start_utc"] > "2026"


def test_refuses_a_slug_owned_by_a_real_tenant(hosted):
    # A genuine studio already lives at this slug.
    real = saas.create_tenant(
        "demo-tour",
        "Real Studio",
        "real-owner@example.test",
        "realpassword",
        signup_source="direct",
    )
    saas.update_tenant_billing(real["id"], plan_status="canceled")

    with pytest.raises(SystemExit):
        _run()

    # It must be untouched: not reactivated, not renamed, creds not overwritten.
    after = saas.tenant_by_slug("demo-tour")
    assert after["owner_email"] == "real-owner@example.test"
    assert after["plan_status"] == "canceled"
    assert (after.get("signup_source") or "") != "reviewer-demo"


def test_booking_is_refreshed_convergently(hosted):
    _run()
    tenant = saas.tenant_by_slug("demo-tour")
    with saas.tenant_runtime(tenant):
        from app import db

        # Simulate the demo having aged: its booking drifted into the past.
        db.run("UPDATE bookings SET start_utc='2020-01-01 15:00:00'")

    _run()  # rerun must restore a future booking, not leave the agenda empty
    with saas.tenant_runtime(tenant):
        from app import db

        rows = db.all_("SELECT start_utc FROM bookings")
        assert len(rows) == 1  # convergent, not piled up
        assert rows[0]["start_utc"] > "2026"


def test_rerun_rotates_credentials_and_stays_active_trial(hosted):
    first = _run(password="review-me-please")
    second = _run(password="a-new-review-password")
    assert first["tenant_created"] is True
    assert second["tenant_created"] is False
    tenant = saas.tenant_by_slug("demo-tour")
    # The most recently advertised password must verify.
    assert saas.passwords.verify_password("a-new-review-password", tenant["admin_password_hash"])
    assert tenant["plan_status"] == "trialing"


@pytest.mark.parametrize("bad", ["neutral", "", "WEDDING", "unknown"])
def test_rejects_unsupported_preset(hosted, bad):
    with pytest.raises(SystemExit):
        _run(preset=bad)


def test_refuses_single_tenant_mode(hosted, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    with pytest.raises(SystemExit):
        _run()


def test_rejects_short_password(hosted):
    with pytest.raises(SystemExit):
        _run(password="short")
