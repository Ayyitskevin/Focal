"""Batch A / Slice A4: operator tenant notes.

Feedback that arrives by email or DM had nowhere in-product to live against the
studio it came from (launch-gap audit). A free-text notes field per tenant in
the operator console closes the batch: submit → stored → rendered in the row →
rides the tenant CSV export.
"""

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import config, saas, security

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "a4-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "op-pw")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _operator_request(path, method="POST"):
    cookie = (
        f"{security.ADMIN_COOKIE}="
        f"{security.sign(f'operator:{security._pw_fp(config.ADMIN_PASSWORD)}')}"
    )
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [
                (b"host", b"mise.test"),
                (b"accept", b"text/html"),
                (b"cookie", cookie.encode()),
            ],
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def test_note_saves_renders_and_exports(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    resp = asyncio.run(
        saas.operator_tenant_notes(
            _operator_request(f"/admin/saas/{tenant['id']}/notes"),
            tenant["id"],
            notes="Emailed 7/2: confused by licence terms — promised a walkthrough",
        )
    )
    assert resp.status_code == 303
    assert "promised a walkthrough" in saas.tenant_by_slug("alpha")["notes"]

    monkeypatch.setattr(
        saas,
        "tenant_launch_status",
        lambda t: {"score": 0, "complete": False, "headline": "", "detail": ""},
    )
    console = asyncio.run(saas.operator_console(_operator_request("/admin/saas", "GET")))
    assert "promised a walkthrough" in console.body.decode()
    assert "promised a walkthrough" in saas.operator_tenant_export_csv()


def test_empty_note_clears_and_long_note_is_capped(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    req = _operator_request(f"/admin/saas/{tenant['id']}/notes")

    asyncio.run(saas.operator_tenant_notes(req, tenant["id"], notes="x" * 9000))
    assert len(saas.tenant_by_slug("alpha")["notes"]) == saas.NOTES_MAX_CHARS
    asyncio.run(saas.operator_tenant_notes(req, tenant["id"], notes="   "))
    assert saas.tenant_by_slug("alpha")["notes"] is None


def test_unknown_tenant_404s_and_tenant_context_cannot_write(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            saas.operator_tenant_notes(
                _operator_request("/admin/saas/999/notes"), 999, notes="ghost"
            )
        )
    assert exc.value.status_code == 404

    # A tenant-context request (even authenticated) is not the operator: 404 via
    # require_platform_admin, and the note is untouched.
    fp = security._pw_fp(tenant.get("admin_password_hash") or "")
    principal = f"tenant:{tenant['id']}:alpha:{fp}"
    cookie = f"{security.ADMIN_COOKIE}={security.sign(principal)}"
    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/admin/saas/{tenant['id']}/notes",
            "query_string": b"",
            "headers": [
                (b"host", b"alpha.mise.test"),
                (b"accept", b"text/html"),
                (b"cookie", cookie.encode()),
            ],
            "scheme": "https",
            "server": ("alpha.mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )
    with saas.tenant_runtime(tenant):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(saas.operator_tenant_notes(req, tenant["id"], notes="sneaky"))
    assert exc.value.status_code == 404
    assert saas.tenant_by_slug("alpha")["notes"] is None
