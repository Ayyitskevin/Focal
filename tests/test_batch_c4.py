"""Batch C / Slice C4: cancellation-reason capture on delete-studio.

Why someone left is the single most valuable feedback a beta produces — and it
used to evaporate with the studio. An optional exit note on the delete
confirmation now lands in tenant_feedback BEFORE the tombstone (the tenants
row survives deletion, so the operator panel's join holds) and pings the
operator's Telegram.
"""

import asyncio

import pytest
from starlette.requests import Request

from app import alerts, config, saas, security

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "c4-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _tenant_request(tenant, path="/admin/delete-studio"):
    fp = security._pw_fp(tenant.get("admin_password_hash") or "")
    principal = f"tenant:{tenant['id']}:{tenant['slug']}:{fp}"
    cookie = f"{security.ADMIN_COOKIE}={security.sign(principal)}"
    host = f"{tenant['slug']}.mise.test"
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": [
                (b"host", host.encode()),
                (b"accept", b"text/html"),
                (b"cookie", cookie.encode()),
            ],
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def _delete(tenant, reason):
    with saas.tenant_runtime(tenant):
        return asyncio.run(
            saas.delete_studio(
                _tenant_request(tenant),
                confirm_slug=tenant["slug"],
                password="secret123",
                reason=reason,
            )
        )


def test_exit_reason_survives_the_tombstone_and_pings(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    pings = []
    monkeypatch.setattr(alerts, "notify", lambda text: pings.append(text))

    resp = _delete(tenant, "Loved it but my clients are all on Pixieset already")
    assert resp.status_code == 303
    assert saas.tenant_by_slug("alpha") is None  # tombstoned: slug freed

    rows = saas.recent_tenant_feedback()
    assert len(rows) == 1
    assert rows[0]["page"] == "studio-delete"
    assert "Pixieset" in rows[0]["message"]
    assert rows[0]["studio_name"] == "Alpha Studio"  # join survives deletion
    assert len(pings) == 1 and "Studio deleted" in pings[0] and "Pixieset" in pings[0]


def test_silent_deletion_stores_nothing_and_still_deletes(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    pings = []
    monkeypatch.setattr(alerts, "notify", lambda text: pings.append(text))

    resp = _delete(tenant, "   ")
    assert resp.status_code == 303
    assert saas.tenant_by_slug("alpha") is None
    assert saas.recent_tenant_feedback() == [] and pings == []


def test_failed_confirmation_records_no_reason(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        resp = asyncio.run(
            saas.delete_studio(
                _tenant_request(tenant),
                confirm_slug="wrong-slug",
                password="secret123",
                reason="should not be stored",
            )
        )
    assert resp.status_code == 303 and "delete_error=slug" in resp.headers["location"]
    assert saas.tenant_by_slug("alpha") is not None  # nothing deleted
    assert saas.recent_tenant_feedback() == []  # nothing recorded
