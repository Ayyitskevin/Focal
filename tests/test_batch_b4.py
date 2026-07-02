"""Batch B / Slice B4: Plausible funnel goals.

The analytics integration was pageview-only (launch-gap audit): trial-start
conversions were visible only in the control DB, not in Plausible. Three
custom events now cover the funnel's decision points — form submit, gate
rejection, waitlist join — all gated on plausible_domain so a deployment
without analytics renders none of it (and tenant/client surfaces stay
untracked per ADR 0060).
"""

import asyncio

import pytest
from starlette.requests import Request

from app import config, render, saas

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch, *, plausible="mise.test"):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "b4-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "sesame")
    # plausible_domain is a frozen template global (set at import) — patch it there.
    monkeypatch.setitem(render.templates.env.globals, "plausible_domain", plausible)
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path, method="GET"):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [(b"host", b"mise.test"), (b"accept", b"text/html")],
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def test_pricing_page_carries_stub_and_trial_submit_event(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    body = asyncio.run(saas.pricing(_request("/pricing"))).body.decode()
    assert "window.plausible = window.plausible ||" in body  # queue stub
    assert "plausible('Trial Submit')" in body  # form submit goal


def test_gate_rejection_fires_the_rejected_event(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    resp = asyncio.run(
        saas.start_trial(
            _request("/start-trial", "POST"),
            studio_name="X",
            owner_email="x@example.com",
            slug="xstudio",
            password="xpass9999",
            invite_code="wrong",
        )
    )
    assert resp.status_code == 403
    assert "plausible('Invite Gate Rejected')" in resp.body.decode()


def test_waitlist_join_fires_the_joined_event(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    resp = asyncio.run(
        saas.waitlist_join(
            _request("/waitlist", "POST"),
            email="ana@example.com",
            signup_source=None,
            signup_campaign=None,
        )
    )
    assert resp.status_code == 200
    assert "plausible('Waitlist Joined')" in resp.body.decode()


def test_no_analytics_means_no_event_scripts(tmp_path, monkeypatch):
    # A deployment without MISE_PLAUSIBLE_DOMAIN renders zero plausible artifacts.
    _configure_saas(tmp_path, monkeypatch, plausible="")
    body = asyncio.run(saas.pricing(_request("/pricing"))).body.decode()
    assert "plausible" not in body
