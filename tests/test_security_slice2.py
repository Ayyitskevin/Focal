"""Security Slice 2 (ADR 0062): tenant/client data isolation.

Locks the isolation properties so a future refactor that drops a scoping clause
is caught by CI: client-session cookies are bound to the serving studio, and the
gallery visitor token is a server-side secret that can't cross tenants.
"""

import pytest

from app import config, security

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch, tenant_id=7):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "slice2-secret")

    class _T:
        def __init__(self, tid):
            self._t = {"id": tid, "slug": f"studio{tid}"}

        def __getitem__(self, k):
            return self._t[k]

    from app import saas

    monkeypatch.setattr(saas, "current_tenant", lambda: _T(tenant_id))


# ── client_session_payload: single-tenant unchanged, hosted tenant-bound ──────


def test_single_tenant_payload_is_unchanged(monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    # Byte-for-byte the legacy claim, so self-hosted client cookies survive the deploy.
    assert security.client_session_payload("portal", 5) == "portal:5"
    assert security.client_session_payload("workspace", 9) == "workspace:9"


def test_hosted_payload_binds_tenant_id(monkeypatch, tmp_path):
    _configure_saas(tmp_path, monkeypatch, tenant_id=7)
    assert security.client_session_payload("portal", 5) == "portal:7:5"


def test_portal_cookie_does_not_replay_across_tenants(monkeypatch, tmp_path):
    # Portal 5 exists in BOTH studios (ids restart per tenant). A cookie minted on
    # studio 7 must not authenticate portal 5 on studio 8.
    _configure_saas(tmp_path, monkeypatch, tenant_id=7)
    cookie_from_7 = security.sign(security.client_session_payload("portal", 5))
    assert security.unsign(cookie_from_7) == "portal:7:5"  # valid signature (global key)

    _configure_saas(tmp_path, monkeypatch, tenant_id=8)
    expected_on_8 = security.client_session_payload("portal", 5)
    assert expected_on_8 == "portal:8:5"
    # The copied cookie's payload (portal:7:5) != studio 8's expected (portal:8:5).
    assert security.unsign(cookie_from_7) != expected_on_8


def test_workspace_cookie_does_not_replay_across_tenants(monkeypatch, tmp_path):
    _configure_saas(tmp_path, monkeypatch, tenant_id=7)
    cookie_from_7 = security.sign(security.client_session_payload("workspace", 3))
    _configure_saas(tmp_path, monkeypatch, tenant_id=8)
    assert security.unsign(cookie_from_7) != security.client_session_payload("workspace", 3)


def test_tenant_id_binding_rejects_legacy_conflicting_slug_context(monkeypatch, tmp_path):
    # Permanent retirement prevents normal slug reuse. The tenant id remains in
    # the payload as defense-in-depth for a legacy/corrupt control plane that
    # presents the same slug under a different immutable tenant identity.
    _configure_saas(tmp_path, monkeypatch, tenant_id=7)
    old = security.sign(security.client_session_payload("portal", 5))
    _configure_saas(tmp_path, monkeypatch, tenant_id=99)
    assert security.unsign(old) != security.client_session_payload("portal", 5)


def test_same_tenant_cookie_still_authenticates(monkeypatch, tmp_path):
    _configure_saas(tmp_path, monkeypatch, tenant_id=7)
    cookie = security.sign(security.client_session_payload("portal", 5))
    assert security.unsign(cookie) == security.client_session_payload("portal", 5)
