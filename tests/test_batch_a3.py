"""Batch A / Slice A3: waitlist on the invite gate.

Before this slice a launch-buzz visitor without an invite code was a DISCARDED
email: the 403 told them to reply to an invite they don't have, and the address
they'd already typed went nowhere. Now the rejection offers a one-field waitlist
join (idempotent, membership-non-leaking), and the operator gets the queue in
the console plus a CSV — the invite list for when the beta widens.
"""

import asyncio

import pytest
from starlette.requests import Request

from app import config, saas, security

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "a3-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path, host="mise.test", method="POST"):
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


def test_join_waitlist_stores_lowercased_and_dedupes(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    assert saas.join_waitlist("  Ana@Example.COM ", "x", "launch-thread") == "new"
    assert saas.join_waitlist("ana@example.com", "email", "beta") == "repeat"
    rows = saas.waitlist_entries()
    assert len(rows) == 1
    assert rows[0]["email"] == "ana@example.com"
    assert rows[0]["source"] == "x" and rows[0]["campaign"] == "launch-thread"


def test_join_waitlist_rejects_garbage(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    assert saas.join_waitlist("not-an-email") == "invalid"
    assert saas.join_waitlist("a@b") == "invalid"
    assert saas.join_waitlist("") == "invalid"
    assert saas.waitlist_entries() == []


def test_invite_rejection_offers_the_waitlist(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "sesame")
    resp = asyncio.run(
        saas.start_trial(
            _request("/start-trial"),
            studio_name="Gate Studio",
            owner_email="gate@example.com",
            slug="gatestudio",
            password="gatepw99",
            invite_code="wrong",
        )
    )
    assert resp.status_code == 403
    body = resp.body.decode()
    assert 'action="/waitlist"' in body
    assert 'value="gate@example.com"' in body  # their email, pre-filled
    # And nothing was provisioned.
    assert saas.tenant_by_slug("gatestudio") is None


def test_waitlist_route_confirms_without_leaking_membership(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)

    def _join(email):
        return asyncio.run(
            saas.waitlist_join(
                _request("/waitlist"), email=email, signup_source=None, signup_campaign=None
            )
        )

    first = _join("ana@example.com")
    again = _join("ana@example.com")
    assert first.status_code == 200 and again.status_code == 200
    assert b"on the list" in first.body and b"on the list" in again.body  # identical outcome
    assert len(saas.waitlist_entries()) == 1
    assert _join("nope").status_code == 400


def test_waitlist_route_404_in_single_tenant(tmp_path, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(config, "SAAS_MODE", False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            saas.waitlist_join(
                _request("/waitlist"),
                email="a@example.com",
                signup_source=None,
                signup_campaign=None,
            )
        )
    assert exc.value.status_code == 404


def test_operator_console_and_csv_carry_the_queue(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "op-pw")
    saas.join_waitlist("ana@example.com", "x", "launch-thread")
    cookie = f"{security.ADMIN_COOKIE}={security.sign(f'operator:{security._pw_fp(config.ADMIN_PASSWORD)}')}"
    req = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/saas",
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
    resp = asyncio.run(saas.operator_console(req))
    assert resp.status_code == 200 and "ana@example.com" in resp.body.decode()
    csv_text = saas.waitlist_export_csv()
    assert "ana@example.com" in csv_text and "launch-thread" in csv_text
