"""Reviewer demo-studio seed script (Conductor plan T3).

The App Store / TestFlight reviewer needs a studio that stays populated and never
lapses. These tests pin that the seed script is hosted-only, marks the tenant
'active' (so the trial sweep never 402s it mid-review), seeds a realistic studio
including an upcoming booking, and is idempotent.
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


def _run(preset="wedding", slug="demo-tour"):
    return _load_seeder().seed_demo_tenant(
        slug=slug,
        studio_name="Mise Demo Studio",
        owner_email="reviewer@demo.mise.local",
        password="review-me-please",
        preset=preset,
    )


def test_seed_creates_active_tenant_with_populated_studio(hosted):
    summary = _run()
    assert summary["tenant_created"] is True
    assert summary["plan_status"] == "active"

    tenant = saas.tenant_by_slug("demo-tour")
    assert tenant is not None
    # 'active' is the exemption that keeps the trial sweep from 402-ing the demo.
    assert tenant["plan_status"] == "active"

    with saas.tenant_runtime(tenant):
        from app import db

        client = db.one("SELECT id FROM clients WHERE email=?", ("demo+wedding@mise.local",))
        assert client is not None
        assert db.one("SELECT id FROM projects WHERE client_id=?", (client["id"],)) is not None
        assert db.one("SELECT COUNT(*) AS n FROM invoices")["n"] >= 1
        assert db.one("SELECT id FROM event_types WHERE slug='demo-consult'") is not None
        booking = db.one("SELECT status, start_utc FROM bookings ORDER BY id LIMIT 1")
        assert booking is not None
        assert booking["status"] == "confirmed"
        assert booking["start_utc"] > "2026"  # a real future timestamp, not empty


def test_seed_is_idempotent(hosted):
    first = _run()
    second = _run()
    assert first["tenant_created"] is True
    assert second["tenant_created"] is False  # reused, not duplicated
    assert second["booking_seeded"] is False  # no second booking piled on

    # Exactly one tenant, one demo client, one booking after two runs.
    tenant = saas.tenant_by_slug("demo-tour")
    with saas.tenant_runtime(tenant):
        from app import db

        assert db.one("SELECT COUNT(*) AS n FROM bookings")["n"] == 1
        assert (
            db.one("SELECT COUNT(*) AS n FROM clients WHERE email=?", ("demo+wedding@mise.local",))[
                "n"
            ]
            == 1
        )


def test_seed_repairs_a_lapsed_demo_to_active(hosted):
    _run()
    tenant = saas.tenant_by_slug("demo-tour")
    # Simulate the demo having drifted into a terminal billing state.
    saas.update_tenant_billing(tenant["id"], plan_status="canceled")
    assert saas.tenant_by_slug("demo-tour")["plan_status"] == "canceled"

    _run()  # re-running the seed must restore 'active'
    assert saas.tenant_by_slug("demo-tour")["plan_status"] == "active"


def test_seed_refuses_single_tenant_mode(hosted, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    with pytest.raises(SystemExit):
        _run()


def test_seed_rejects_short_password(hosted):
    with pytest.raises(SystemExit):
        _load_seeder().seed_demo_tenant(
            slug="demo-tour",
            studio_name="Mise Demo Studio",
            owner_email="reviewer@demo.mise.local",
            password="short",
            preset="wedding",
        )
