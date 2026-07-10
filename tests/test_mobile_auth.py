"""Native API auth foundation: opaque tokens, rotation, and tenant/resource scope."""

import datetime as dt
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.requests import Request

from app import config, db, mobile_auth, saas

pytestmark = pytest.mark.unit


def _request(
    host: str = "studio.test",
    *,
    authorization: str | None = None,
    cookie: str | None = None,
) -> Request:
    headers = [(b"host", host.encode()), (b"accept", b"application/json")]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth",
            "query_string": b"",
            "headers": headers,
            "scheme": "https",
            "server": (host, 443),
            "client": ("203.0.113.9", 50000),
        }
    )


@pytest.fixture
def self_hosted(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "mobile-auth-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    db.migrate()
    return _request("studio.test")


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "mobile-saas-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _seed_project() -> tuple[int, int]:
    client_id = db.run(
        "INSERT INTO clients (name, email) VALUES (?,?)", ("Client", "client@example.test")
    )
    project_id = db.run(
        "INSERT INTO projects (client_id, title) VALUES (?,?)", (client_id, "Mobile project")
    )
    return client_id, project_id


def _assert_error(code: str, call):
    with pytest.raises(mobile_auth.MobileAuthError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def test_owner_tokens_are_hash_only_fixed_lifetime_and_never_use_cookies(self_hosted, monkeypatch):
    now = dt.datetime(2026, 7, 10, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(mobile_auth, "_now", lambda: now)
    pair = mobile_auth.issue_studio_owner_session(
        self_hosted,
        "owner-password",
        email="ignored@self-host.test",
        installation_id="INSTALLATION-SECRET",
        device_name=" Kevin's\n iPhone\x00 " + "x" * 200,
        device_platform=" iOS\n ",
        device_app_version=" 1.0 (42)\x00 ",
    )

    assert pair.access_expires_at == now + dt.timedelta(minutes=15)
    assert pair.refresh_expires_at == now + dt.timedelta(days=30)
    assert pair.principal.absolute_expires_at == now + dt.timedelta(days=90)
    assert pair.principal.kind == mobile_auth.STUDIO_OWNER
    assert pair.principal.scopes == {"studio:read", "studio:write"}
    assert pair.principal.device_name.startswith("Kevin's iPhone")
    assert len(pair.principal.device_name) == 120
    assert pair.principal.device_platform == "ios"
    assert pair.principal.device_app_version == "1.0 (42)"

    token_rows = db.all_("SELECT token_hash FROM api_tokens ORDER BY id")
    assert {row["token_hash"] for row in token_rows} == {
        hashlib.sha256(pair.access_token.encode()).hexdigest(),
        hashlib.sha256(pair.refresh_token.encode()).hexdigest(),
    }
    assert all(len(row["token_hash"]) == 64 for row in token_rows)
    assert pair.access_token not in repr(pair)
    assert pair.refresh_token not in repr(pair)
    session = db.one(
        """SELECT installation_id_hash, device_name, device_platform,
                  device_app_version FROM api_sessions WHERE id=?""",
        (pair.session_id,),
    )
    assert session["installation_id_hash"] != "INSTALLATION-SECRET"
    assert len(session["installation_id_hash"]) == 64
    assert session["device_platform"] == "ios"
    assert session["device_app_version"] == "1.0 (42)"

    # A browser cookie, even one named like the real admin cookie, is never a
    # fallback credential for the mobile dependency.
    cookie_request = _request("studio.test", cookie="mise_admin=legacy-cookie")
    _assert_error("auth.invalid_token", lambda: mobile_auth.authenticate_request(cookie_request))
    bearer_request = _request(
        "studio.test", authorization=f"Bearer {pair.access_token}", cookie="mise_admin=anything"
    )
    assert mobile_auth.authenticate_request(bearer_request).session_id == pair.session_id


def test_self_hosted_session_is_bound_to_normalized_request_origin(self_hosted):
    pair = mobile_auth.issue_studio_owner_session(self_hosted, "owner-password")
    assert mobile_auth.authenticate_access(self_hosted, pair.access_token).kind == "studio_owner"
    alias = _request("alias.studio.test")
    _assert_error(
        "auth.invalid_token", lambda: mobile_auth.authenticate_access(alias, pair.access_token)
    )
    # A failed cross-alias replay does not revoke the valid origin's family.
    assert (
        mobile_auth.authenticate_access(self_hosted, pair.access_token).session_id
        == pair.session_id
    )


def test_hosted_owner_email_is_generic_and_tokens_do_not_cross_tenants(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    alpha = saas.create_tenant("alpha", "Alpha", "owner@alpha.test", "alpha-password")
    beta = saas.create_tenant("beta", "Beta", "owner@beta.test", "beta-password")
    alpha_request = _request("alpha.mise.test")

    with saas.tenant_runtime(alpha):
        wrong_email = _assert_error(
            "auth.invalid_credentials",
            lambda: mobile_auth.issue_studio_owner_session(
                alpha_request, "alpha-password", email="other@alpha.test"
            ),
        )
        wrong_password = _assert_error(
            "auth.invalid_credentials",
            lambda: mobile_auth.issue_studio_owner_session(
                alpha_request, "not-the-password", email="owner@alpha.test"
            ),
        )
        assert wrong_email.status_code == wrong_password.status_code == 401
        pair = mobile_auth.issue_studio_owner_session(
            alpha_request, "alpha-password", email=" OWNER@ALPHA.TEST "
        )
        assert pair.principal.tenant_key == f"tenant:{alpha['id']}:https://alpha.mise.test"

    with saas.tenant_runtime(beta):
        _assert_error(
            "auth.invalid_token",
            lambda: mobile_auth.authenticate_access(_request("beta.mise.test"), pair.access_token),
        )
    with saas.tenant_runtime(alpha):
        assert (
            mobile_auth.authenticate_access(alpha_request, pair.access_token).session_id
            == pair.session_id
        )
        _assert_error(
            "auth.invalid_token",
            lambda: mobile_auth.authenticate_access(
                _request("custom.alpha.test"), pair.access_token
            ),
        )
        # Origin mismatch is a replay rejection, not a revocation of the valid
        # tenant-host session.
        assert (
            mobile_auth.authenticate_access(alpha_request, pair.access_token).session_id
            == pair.session_id
        )


def test_hosted_platform_context_cannot_issue_any_mobile_principal(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    request = _request("mise.test")
    for issue in (
        lambda: mobile_auth.issue_studio_owner_session(request, "operator-password"),
        lambda: mobile_auth.issue_gallery_session(request, "anything", "1234"),
        lambda: mobile_auth.issue_portal_session(request, "anything", "1234"),
        lambda: mobile_auth.issue_workspace_session(request, "anything", "1234"),
        lambda: mobile_auth.issue_document_session(request, "proposal", "anything"),
    ):
        error = _assert_error("auth.tenant_not_found", issue)
        assert error.status_code == 404


def test_hosted_password_reset_invalidates_owner_family(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha", "owner@alpha.test", "old-password")
    request = _request("alpha.mise.test")
    with saas.tenant_runtime(tenant):
        pair = mobile_auth.issue_studio_owner_session(request, "old-password")

    saas.set_tenant_password(tenant["id"], "new-password")
    with saas.tenant_runtime("alpha"):
        _assert_error(
            "auth.invalid_token",
            lambda: mobile_auth.authenticate_access(request, pair.access_token),
        )
        row = db.one("SELECT revoke_reason FROM api_sessions WHERE id=?", (pair.session_id,))
        assert row["revoke_reason"] == "credential_changed"


def test_password_change_invalidates_and_revokes_owner_family(self_hosted, monkeypatch):
    pair = mobile_auth.issue_studio_owner_session(self_hosted, "owner-password")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "rotated-password")
    _assert_error(
        "auth.invalid_token",
        lambda: mobile_auth.authenticate_access(self_hosted, pair.access_token),
    )
    row = db.one(
        "SELECT revoked_at, revoke_reason FROM api_sessions WHERE id=?", (pair.session_id,)
    )
    assert row["revoked_at"] is not None
    assert row["revoke_reason"] == "credential_changed"
    _assert_error(
        "auth.invalid_token", lambda: mobile_auth.rotate_refresh(self_hosted, pair.refresh_token)
    )


def test_refresh_rotates_rolls_for_30_days_and_stops_at_absolute_cap(self_hosted, monkeypatch):
    clock = [dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)]
    monkeypatch.setattr(mobile_auth, "_now", lambda: clock[0])
    pair = mobile_auth.issue_studio_owner_session(self_hosted, "owner-password")
    absolute = clock[0] + dt.timedelta(days=90)

    clock[0] += dt.timedelta(days=29)
    pair = mobile_auth.rotate_refresh(self_hosted, pair.refresh_token)
    assert pair.refresh_expires_at == dt.datetime(2026, 3, 1, tzinfo=dt.UTC)

    clock[0] += dt.timedelta(days=29)
    pair = mobile_auth.rotate_refresh(self_hosted, pair.refresh_token)
    assert pair.refresh_expires_at == dt.datetime(2026, 3, 30, tzinfo=dt.UTC)

    clock[0] += dt.timedelta(days=29)
    pair = mobile_auth.rotate_refresh(self_hosted, pair.refresh_token)
    assert pair.refresh_expires_at == absolute
    assert pair.principal.absolute_expires_at == absolute

    clock[0] = absolute
    _assert_error(
        "auth.invalid_token", lambda: mobile_auth.rotate_refresh(self_hosted, pair.refresh_token)
    )


def test_refresh_reuse_atomically_revokes_the_whole_rotated_family(self_hosted, monkeypatch):
    clock = [dt.datetime(2026, 7, 10, 12, 0, tzinfo=dt.UTC)]
    monkeypatch.setattr(mobile_auth, "_now", lambda: clock[0])
    first = mobile_auth.issue_studio_owner_session(self_hosted, "owner-password")
    clock[0] += dt.timedelta(seconds=1)
    rotated = mobile_auth.rotate_refresh(self_hosted, first.refresh_token)

    error = _assert_error(
        "auth.refresh_reused",
        lambda: mobile_auth.rotate_refresh(self_hosted, first.refresh_token),
    )
    assert error.status_code == 401
    _assert_error(
        "auth.invalid_token",
        lambda: mobile_auth.authenticate_access(self_hosted, rotated.access_token),
    )
    _assert_error(
        "auth.invalid_token",
        lambda: mobile_auth.rotate_refresh(self_hosted, rotated.refresh_token),
    )
    session = db.one(
        "SELECT revoked_at, revoke_reason FROM api_sessions WHERE id=?", (first.session_id,)
    )
    assert session["revoked_at"] is not None
    assert session["revoke_reason"] == "refresh_reuse"
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM api_tokens WHERE session_id=? AND revoked_at IS NOT NULL",
            (first.session_id,),
        )["n"]
        == 4
    )


def test_concurrent_refresh_has_one_rotation_and_one_family_revocation(self_hosted):
    first = mobile_auth.issue_studio_owner_session(self_hosted, "owner-password")
    barrier = threading.Barrier(2)

    def rotate_once():
        barrier.wait()
        try:
            return mobile_auth.rotate_refresh(self_hosted, first.refresh_token)
        except mobile_auth.MobileAuthError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: rotate_once(), range(2)))

    pairs = [result for result in results if isinstance(result, mobile_auth.TokenPair)]
    errors = [result for result in results if isinstance(result, mobile_auth.MobileAuthError)]
    assert len(pairs) == len(errors) == 1
    assert errors[0].code == "auth.refresh_reused"
    _assert_error(
        "auth.invalid_token",
        lambda: mobile_auth.authenticate_access(self_hosted, pairs[0].access_token),
    )


def test_gallery_guest_is_visitor_scoped_and_pin_rotation_invalidates(self_hosted):
    gallery_id = db.run(
        """INSERT INTO galleries (slug, title, pin, published, type, require_pin)
           VALUES (?,?,?,?,?,?)""",
        ("gallery-link", "Gallery", "2468", 1, "gallery", 1),
    )
    pair = mobile_auth.issue_gallery_session(self_hosted, "gallery-link", "2468")
    principal = mobile_auth.authenticate_access(
        self_hosted,
        pair.access_token,
        required_scopes={f"gallery:{gallery_id}:read"},
    )
    assert principal.kind == mobile_auth.GALLERY_GUEST
    assert principal.resource_id == gallery_id
    assert principal.gallery_visitor_id is not None
    assert db.one(
        "SELECT 1 AS x FROM visitors WHERE id=? AND gallery_id=?",
        (principal.gallery_visitor_id, gallery_id),
    )
    insufficient = _assert_error(
        "auth.insufficient_scope",
        lambda: mobile_auth.authenticate_access(
            self_hosted, pair.access_token, required_scopes={"studio:read"}
        ),
    )
    assert insufficient.status_code == 403

    db.run("UPDATE galleries SET pin='8642' WHERE id=?", (gallery_id,))
    _assert_error(
        "auth.invalid_token",
        lambda: mobile_auth.authenticate_access(self_hosted, pair.access_token),
    )
    assert (
        db.one("SELECT revoke_reason FROM api_sessions WHERE id=?", (pair.session_id,))[
            "revoke_reason"
        ]
        == "credential_changed"
    )


def test_gallery_expiry_uses_studio_day_for_issue_and_session_revalidation(
    self_hosted, monkeypatch
):
    gallery_id = db.run(
        """INSERT INTO galleries (slug, title, pin, published, type, require_pin, expires_at)
           VALUES (?,?,?,?,?,?,?)""",
        ("studio-clock-gallery", "Gallery", "2468", 1, "gallery", 1, "2026-07-10"),
    )
    # UTC has crossed midnight, but the studio's local delivery day has not.
    monkeypatch.setattr(
        mobile_auth,
        "_now",
        lambda: dt.datetime(2026, 7, 11, 0, 30, tzinfo=dt.UTC),
    )
    monkeypatch.setattr(mobile_auth, "_studio_today", lambda: dt.date(2026, 7, 10))
    pair = mobile_auth.issue_gallery_session(self_hosted, "studio-clock-gallery", "2468")
    assert pair.principal.resource_id == gallery_id
    assert (
        mobile_auth.authenticate_access(self_hosted, pair.access_token).session_id
        == pair.session_id
    )

    monkeypatch.setattr(mobile_auth, "_studio_today", lambda: dt.date(2026, 7, 11))
    _assert_error(
        "auth.invalid_token",
        lambda: mobile_auth.authenticate_access(self_hosted, pair.access_token),
    )


def test_shared_access_principals_keep_exact_resource_scopes(self_hosted):
    client_id, project_id = _seed_project()
    portal_id = db.run(
        "INSERT INTO portals (client_id, slug, pin, published) VALUES (?,?,?,1)",
        (client_id, "client-portal", "1111"),
    )
    db.run(
        """UPDATE projects SET workspace_slug=?, workspace_pin=?, workspace_published=1
           WHERE id=?""",
        ("project-space", "2222", project_id),
    )
    proposal_id = db.run(
        """INSERT INTO proposals (project_id, slug, title, status)
           VALUES (?,?,?,'sent')""",
        (project_id, "proposal-capability", "Proposal"),
    )
    contract_id = db.run(
        """INSERT INTO contracts (project_id, slug, title, body, status)
           VALUES (?,?,?,?, 'sent')""",
        (project_id, "contract-capability", "Contract", "Terms"),
    )
    invoice_id = db.run(
        """INSERT INTO invoices (project_id, slug, title, status)
           VALUES (?,?,?,'sent')""",
        (project_id, "invoice-capability", "Invoice"),
    )

    portal = mobile_auth.issue_portal_session(self_hosted, "client-portal", "1111")
    workspace = mobile_auth.issue_workspace_session(self_hosted, "project-space", "2222")
    proposal = mobile_auth.issue_document_session(self_hosted, "proposal", "proposal-capability")
    contract = mobile_auth.issue_document_session(self_hosted, "contract", "contract-capability")
    invoice = mobile_auth.issue_document_session(self_hosted, "invoice", "invoice-capability")

    assert portal.principal.kind == mobile_auth.PORTAL_GUEST
    assert portal.principal.scopes == {
        f"portal:{portal_id}:read",
        f"portal:{portal_id}:download",
    }
    assert workspace.principal.kind == mobile_auth.WORKSPACE_GUEST
    assert workspace.principal.scopes == {f"workspace:{project_id}:read"}
    assert proposal.principal.scopes == {
        f"document:proposal:{proposal_id}:read",
        f"document:proposal:{proposal_id}:respond",
    }
    assert contract.principal.scopes == {
        f"document:contract:{contract_id}:read",
        f"document:contract:{contract_id}:sign",
    }
    assert invoice.principal.scopes == {
        f"document:invoice:{invoice_id}:read",
        f"document:invoice:{invoice_id}:checkout",
    }
    assert all(
        pair.principal.kind == mobile_auth.DOCUMENT_GUEST for pair in (proposal, contract, invoice)
    )
    assert portal.principal.scopes.isdisjoint(workspace.principal.scopes)
    assert portal.principal.scopes.isdisjoint(proposal.principal.scopes)

    # Rotating either shared PIN invalidates that exact family without widening
    # or disturbing an unrelated principal.
    db.run("UPDATE portals SET pin='9999' WHERE id=?", (portal_id,))
    _assert_error(
        "auth.invalid_token",
        lambda: mobile_auth.authenticate_access(self_hosted, portal.access_token),
    )
    assert (
        mobile_auth.authenticate_access(self_hosted, workspace.access_token).resource_id
        == project_id
    )


def test_link_only_drop_can_exchange_without_pin_but_still_obeys_live_state(self_hosted):
    gallery_id = db.run(
        """INSERT INTO galleries (slug, title, pin, published, type, require_pin)
           VALUES (?,?,?,?,?,?)""",
        ("drop-link", "Transfer", "unused", 1, "drop", 0),
    )
    pair = mobile_auth.issue_gallery_session(self_hosted, "drop-link")
    assert pair.principal.resource_id == gallery_id
    db.run("UPDATE galleries SET published=0 WHERE id=?", (gallery_id,))
    _assert_error(
        "auth.invalid_token",
        lambda: mobile_auth.authenticate_access(self_hosted, pair.access_token),
    )


def test_owner_can_list_revoke_and_logout_device_families(self_hosted):
    first = mobile_auth.issue_studio_owner_session(
        self_hosted, "owner-password", installation_id="one", device_name="First iPhone"
    )
    second = mobile_auth.issue_studio_owner_session(
        self_hosted, "owner-password", installation_id="two", device_name="iPad"
    )
    owner = mobile_auth.authenticate_access(self_hosted, first.access_token)
    sessions = mobile_auth.list_sessions(self_hosted, owner)
    assert {session.session_id for session in sessions} == {first.session_id, second.session_id}
    assert sum(session.is_current for session in sessions) == 1

    assert mobile_auth.revoke_session(self_hosted, owner, second.session_id)
    _assert_error(
        "auth.invalid_token",
        lambda: mobile_auth.authenticate_access(self_hosted, second.access_token),
    )
    assert not mobile_auth.revoke_session(self_hosted, owner, "missing-session")

    assert mobile_auth.logout(self_hosted, first.refresh_token)
    assert mobile_auth.logout(self_hosted, first.refresh_token)  # idempotent family lookup
    assert not mobile_auth.logout(self_hosted, "unknown-token")
    _assert_error(
        "auth.invalid_token",
        lambda: mobile_auth.authenticate_access(self_hosted, first.access_token),
    )
