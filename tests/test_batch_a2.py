"""Batch A / Slice A2: tenant pulse.

Before this slice the operator had NO usage signal per studio: updated_at only
moves on billing/domain writes and the launch score measures setup, not
presence. last_login_at is stamped on every successful tenant admin login and
feeds the console ("seen Nd ago" / "quiet" / "never signed in"), the CSV
export, and the at-risk trial count — a silent trial is at-risk even when its
setup checklist is complete.
"""

import asyncio

import pytest
from starlette.requests import Request

from app import config, saas
from app.admin import auth

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "a2-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path, host, method="POST"):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [(b"host", host.encode()), (b"accept", b"text/html")],
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def _set_last_login(tenant_id, stamp):
    with saas.control_connect() as con:
        con.execute("UPDATE tenants SET last_login_at=? WHERE id=?", (stamp, tenant_id))


def test_successful_login_stamps_last_login(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    assert saas.tenant_by_slug("alpha").get("last_login_at") is None

    with saas.tenant_runtime(tenant):
        resp = asyncio.run(auth.login(_request("/admin/login", "alpha.mise.test"), "secret123"))
    assert resp.status_code == 303
    assert saas.tenant_by_slug("alpha")["last_login_at"] is not None


def test_failed_login_stamps_nothing(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        resp = asyncio.run(auth.login(_request("/admin/login", "alpha.mise.test"), "wrong-pw"))
    assert resp.status_code == 401
    assert saas.tenant_by_slug("alpha").get("last_login_at") is None


def test_silent_trial_is_at_risk_even_when_launch_ready(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    # Force the silence condition: last login 9 days ago; make launch look complete
    # so ONLY the silence path can trip at-risk.
    _set_last_login(tenant["id"], "2026-06-23 10:00:00")
    monkeypatch.setattr(
        saas,
        "tenant_launch_status",
        lambda t: {"score": 100, "complete": True, "headline": "", "detail": ""},
    )
    overview = saas.operator_tenant_overview()
    assert overview["counts"]["trials_at_risk"] == 1
    row = overview["rows"][0]
    assert row["silent_days"] is not None and row["silent_days"] >= saas.SILENT_TRIAL_DAYS


def test_fresh_active_tenant_is_not_at_risk(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.touch_tenant_login(tenant["id"])  # seen today
    monkeypatch.setattr(
        saas,
        "tenant_launch_status",
        lambda t: {"score": 100, "complete": True, "headline": "", "detail": ""},
    )
    overview = saas.operator_tenant_overview()
    assert overview["counts"]["trials_at_risk"] == 0
    assert overview["rows"][0]["silent_days"] == 0


def test_never_logged_in_counts_silence_from_signup(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    # Backdate signup, never log in: silence measured from created_at.
    with saas.control_connect() as con:
        con.execute(
            "UPDATE tenants SET created_at='2026-06-20 09:00:00' WHERE id=?", (tenant["id"],)
        )
    monkeypatch.setattr(
        saas,
        "tenant_launch_status",
        lambda t: {"score": 100, "complete": True, "headline": "", "detail": ""},
    )
    overview = saas.operator_tenant_overview()
    assert overview["counts"]["trials_at_risk"] == 1
    assert overview["rows"][0]["last_login_at"] is None


def test_csv_export_carries_last_login(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _set_last_login(tenant["id"], "2026-07-01 08:00:00")
    monkeypatch.setattr(
        saas,
        "tenant_launch_status",
        lambda t: {"score": 0, "complete": False, "headline": "", "detail": ""},
    )
    csv_text = saas.operator_tenant_export_csv()
    assert "last_login_at" in csv_text.splitlines()[0]
    assert "2026-07-01 08:00:00" in csv_text
