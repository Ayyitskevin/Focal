"""Security Slice 3 (ADR 0063): auth / session / rate-limit hardening.

The load-bearing property: a credential change (hosted tenant password reset, or
operator/self-host ADMIN_PASSWORD rotation) invalidates every existing admin
session — a stolen live cookie cannot outlive the reset meant to evict it.
"""

import pytest
from starlette.requests import Request

from app import config, saas, security

pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "slice3-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path, host, cookie):
    return Request(
        {
            "type": "http",
            "method": "GET",
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


def _admin_cookie(principal: str) -> str:
    return f"{security.ADMIN_COOKIE}={security.sign(principal)}"


def test_password_reset_invalidates_existing_admin_session(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    # A live admin session, minted under the current credential.
    with saas.tenant_runtime("alpha"):
        principal = security.admin_principal(_request("/admin", "alpha.mise.test", ""))
    cookie = _admin_cookie(principal)
    with saas.tenant_runtime("alpha"):
        assert security.is_admin(_request("/admin", "alpha.mise.test", cookie)) is True

    # The owner resets the password (the "evict whoever's in my account" action).
    saas.set_tenant_password(tenant["id"], "brand-new-pw-9999")

    # The pre-reset cookie no longer authenticates — the fingerprint moved.
    with saas.tenant_runtime("alpha"):
        assert security.is_admin(_request("/admin", "alpha.mise.test", cookie)) is False
        fresh = security.admin_principal(_request("/admin", "alpha.mise.test", ""))
        assert security.is_admin(_request("/admin", "alpha.mise.test", _admin_cookie(fresh)))


def test_single_tenant_admin_session_bound_to_admin_password(monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "slice3-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "old-operator-pw")
    req = _request("/admin", "localhost", "")
    principal = security.admin_principal(req)
    cookie = _admin_cookie(principal)
    assert security.is_admin(_request("/admin", "localhost", cookie))
    # Rotating ADMIN_PASSWORD invalidates the old session.
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "new-operator-pw")
    assert security.is_admin(_request("/admin", "localhost", cookie)) is False


def test_operator_and_tenant_sessions_do_not_interchange(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-pw")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime("alpha"):
        tenant_principal = security.admin_principal(_request("/admin", "alpha.mise.test", ""))
    operator_principal = security.admin_principal(_request("/admin/saas", "mise.test", ""))
    assert tenant_principal.startswith(f"tenant:{tenant['id']}:alpha:")
    assert operator_principal.startswith("operator:")
    # A tenant admin cookie is not an operator cookie and vice versa.
    assert tenant_principal != operator_principal


def test_fingerprint_is_not_reversible_and_short():
    fp = security._pw_fp("some-password-hash-value")
    assert len(fp) == 12 and fp.isalnum()
    assert "password" not in fp  # digest, not the source
    assert security._pw_fp("") == security._pw_fp("")  # deterministic
    assert security._pw_fp("a") != security._pw_fp("b")
