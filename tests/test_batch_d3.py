"""Batch D / Slice D3: the public-launch flip, proven.

Going public is unsetting MISE_SAAS_INVITE_CODE (ADR 0053) — but until now
that flip was asserted only in docs, and the console never said which mode
production was actually in. These tests pin both directions of the gate and
the operator-console badge that makes the current state impossible to misread.
"""

import asyncio

import pytest
from starlette.requests import Request

from app import config, saas, security

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "d3-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "op-pw")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path, method="POST", cookie=None):
    headers = [(b"host", b"mise.test"), (b"accept", b"text/html")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": headers,
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def _signup(slug="open-studio"):
    return asyncio.run(
        saas.start_trial(
            _request("/start-trial"),
            studio_name="Open Studio",
            owner_email="open@example.com",
            slug=slug,
            password="openpw99",
            signup_source=None,
            signup_campaign=None,
            signup_referrer=None,
            invite_code=None,
        )
    )


def _console_body():
    cookie = (
        f"{security.ADMIN_COOKIE}="
        f"{security.sign(f'operator:{security._pw_fp(config.ADMIN_PASSWORD)}')}"
    )
    resp = asyncio.run(saas.operator_console(_request("/admin/saas", "GET", cookie)))
    assert resp.status_code == 200
    return resp.body.decode()


def test_the_flip_gate_armed_then_open(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    # Armed: the same code-less signup is refused, nothing provisioned...
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "sesame")
    resp = _signup()
    assert resp.status_code == 403
    assert saas.tenant_by_slug("open-studio") is None
    # ...then the one-variable flip, and the identical request goes straight in.
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "")
    resp = _signup()
    assert resp.status_code == 303
    assert "open-studio." in resp.headers["location"]  # the new studio's own URL
    assert saas.tenant_by_slug("open-studio") is not None


def test_pricing_page_drops_the_invite_field_when_open(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "sesame")
    body = asyncio.run(saas.pricing(_request("/pricing", "GET"))).body.decode()
    assert 'name="invite_code"' in body
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "")
    body = asyncio.run(saas.pricing(_request("/pricing", "GET"))).body.decode()
    assert 'name="invite_code"' not in body


def test_console_badge_says_which_mode_production_is_in(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "sesame")
    assert "Private beta — invite gate armed" in _console_body()
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "")
    body = _console_body()
    assert "Public — open signup live" in body
    assert "invite gate armed" not in body
