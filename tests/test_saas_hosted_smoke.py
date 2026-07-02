import asyncio

import pytest
from starlette.requests import Request

from app import config, db, onboarding, saas
from app.admin import activity, auth

# Fast, hermetic (tmp-path DBs, no network): run in the CI unit gate.
pytestmark = pytest.mark.unit


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SAAS_TRIAL_DAYS", 14)
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path: str, host: str, *, method: str = "GET") -> Request:
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


def test_fresh_hosted_login_starts_in_onboarding(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    with saas.tenant_runtime(tenant):
        login = asyncio.run(auth.login(_request("/admin/login", "alpha.mise.test"), "secret123"))
        assert login.status_code == 303
        assert login.headers["location"] == onboarding.ADMIN_ONBOARDING_PATH

        home = asyncio.run(activity.home(_request("/admin/home", "alpha.mise.test")))
        assert home.status_code == 303
        assert home.headers["location"] == onboarding.ADMIN_ONBOARDING_PATH


def test_hosted_trial_route_creates_isolated_tenant_and_onboarding(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)

    trial = asyncio.run(
        saas.start_trial(
            _request("/start-trial", "mise.test", method="POST"),
            studio_name="Smoke Studio",
            owner_email="smoke@example.com",
            slug="smokestudio",
            password="secret123",
        )
    )
    assert trial.status_code == 303
    assert trial.headers["location"] == "https://smokestudio.mise.test/admin/login?trial=1"
    assert (tmp_path / "tenants" / "smokestudio" / "mise.db").exists()

    tenant = saas.tenant_by_slug("smokestudio")
    with saas.tenant_runtime(tenant):
        login = asyncio.run(
            auth.login(_request("/admin/login", "smokestudio.mise.test"), "secret123")
        )
        assert login.status_code == 303
        assert login.headers["location"] == onboarding.ADMIN_ONBOARDING_PATH


def test_hosted_trial_route_persists_signup_attribution(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)

    trial = asyncio.run(
        saas.start_trial(
            _request("/start-trial", "mise.test", method="POST"),
            studio_name="Referral Studio",
            owner_email="referral@example.com",
            slug="referralstudio",
            password="secret123",
            signup_source="newsletter",
            signup_campaign="beta",
            signup_referrer="https://mise.test/demo",
        )
    )

    assert trial.status_code == 303
    tenant = saas.tenant_by_slug("referralstudio")
    assert tenant["signup_source"] == "newsletter"
    assert tenant["signup_campaign"] == "beta"
    assert tenant["signup_referrer"] == "https://mise.test/demo"


def test_tenant_middleware_scopes_hosted_requests(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    seen = {}

    async def call_next(request):
        seen["tenant"] = request.state.tenant["slug"]
        seen["db"] = str(db.current_db_path())
        return saas.JSONResponse({"ok": True})

    response = asyncio.run(
        saas.tenant_middleware(_request("/admin/home", "alpha.mise.test"), call_next)
    )

    assert response.status_code == 200
    assert seen["tenant"] == "alpha"
    assert seen["db"].endswith("/tenants/alpha/mise.db")


def test_platform_demo_tour_renders(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)

    response = asyncio.run(saas.demo(_request("/demo", "mise.test")))

    assert response.status_code == 200
    assert "Restaurant content day" in response.body.decode()
    assert "Wedding story collection" in response.body.decode()


def test_platform_home_and_pricing_answer_buyer_objections(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)

    home = asyncio.run(saas.saas_home(_request("/", "mise.test")))
    pricing = asyncio.run(saas.pricing(_request("/pricing", "mise.test")))

    home_body = home.body.decode()
    pricing_body = pricing.body.decode()
    assert "Does Mise replace Pixieset and HoneyBook" in home_body
    assert "No paid tiers" in home_body
    assert "Trial-first setup" in pricing_body
    assert "Solo-founder supportable" in pricing_body


def test_platform_marketing_pages_render_share_metadata(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)

    home = asyncio.run(saas.saas_home(_request("/", "mise.test"))).body.decode()
    pricing = asyncio.run(saas.pricing(_request("/pricing", "mise.test"))).body.decode()
    demo = asyncio.run(saas.demo(_request("/demo", "mise.test"))).body.decode()

    assert '<link rel="canonical" href="https://mise.test/">' in home
    assert '"@type": "SoftwareApplication"' in home
    assert '"price": "20"' in home
    assert 'property="og:title" content="Mise Pricing - $20/month"' in pricing
    assert '<link rel="canonical" href="https://mise.test/pricing">' in pricing
    assert 'property="og:title" content="Mise Demo - F&B and Wedding Client Studio"' in demo
    assert '<link rel="canonical" href="https://mise.test/demo">' in demo


def test_legal_pages_render_and_are_platform_paths(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_SUPPORT_EMAIL", "help@mise.test")

    terms = asyncio.run(saas.legal_terms(_request("/terms", "mise.test")))
    privacy = asyncio.run(saas.legal_privacy(_request("/privacy", "mise.test")))
    support = asyncio.run(saas.legal_support(_request("/support", "mise.test")))

    assert terms.status_code == privacy.status_code == support.status_code == 200
    terms_body, privacy_body, support_body = (
        terms.body.decode(),
        privacy.body.decode(),
        support.body.decode(),
    )
    # Each doc renders its own distinct content, not a shared stub.
    assert "Terms of Service" in terms_body and "$20/month" in terms_body
    assert "Privacy Policy" in privacy_body
    assert "train AI models" in privacy_body  # the no-AI-training promise
    assert "help@mise.test" in support_body  # support email surfaces
    # The three routes must be servable at the root host (not redirected to /pricing).
    for path in ("/terms", "/privacy", "/support"):
        assert saas._platform_path(path)
    # Pricing carries the legal consent line + footer links.
    pricing_body = asyncio.run(saas.pricing(_request("/pricing", "mise.test"))).body.decode()
    assert 'href="/terms"' in pricing_body and 'href="/privacy"' in pricing_body


def test_legal_support_falls_back_without_configured_email(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_SUPPORT_EMAIL", "")
    support = asyncio.run(saas.legal_support(_request("/support", "mise.test"))).body.decode()
    # No dead-end: without a configured address it still tells the user how to reach support.
    assert "Reply to any email from your studio" in support


def test_invite_gate_blocks_signup_without_valid_code(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "beta-2026")

    def _signup(code):
        return asyncio.run(
            saas.start_trial(
                _request("/start-trial", "mise.test", method="POST"),
                studio_name="Gated Studio",
                owner_email="gated@example.com",
                slug="gated",
                password="secret123",
                invite_code=code,
            )
        )

    # Missing and wrong codes are rejected BEFORE any provisioning.
    for bad in (None, "", "wrong-code"):
        resp = _signup(bad)
        assert resp.status_code == 403
        assert "private beta" in resp.body.decode()
        assert saas.tenant_by_slug("gated") is None
        assert not (tmp_path / "tenants" / "gated").exists()
    # The right code (whitespace-tolerant) signs up normally.
    ok = _signup("  beta-2026 ")
    assert ok.status_code == 303
    assert saas.tenant_by_slug("gated") is not None


def test_signup_stays_open_when_no_invite_code_configured(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "")
    resp = asyncio.run(
        saas.start_trial(
            _request("/start-trial", "mise.test", method="POST"),
            studio_name="Open Studio",
            owner_email="open@example.com",
            slug="openstudio",
            password="secret123",
        )
    )
    assert resp.status_code == 303
    # And the pricing form doesn't ask for a code it doesn't need.
    pricing = asyncio.run(saas.pricing(_request("/pricing", "mise.test"))).body.decode()
    assert "invite_code" not in pricing


def test_pricing_form_shows_invite_field_when_gated(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SAAS_INVITE_CODE", "beta-2026")
    pricing = asyncio.run(saas.pricing(_request("/pricing", "mise.test"))).body.decode()
    assert 'name="invite_code"' in pricing


def test_welcome_email_carries_studio_url_on_signup(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(saas.mailer, "configured", lambda: True)
    monkeypatch.setattr(saas.mailer, "send", lambda *a, **k: sent.append(a))
    resp = asyncio.run(
        saas.start_trial(
            _request("/start-trial", "mise.test", method="POST"),
            studio_name="Welcome Studio",
            owner_email="welcome@example.com",
            slug="welcomestudio",
            password="secret123",
        )
    )
    assert resp.status_code == 303
    # Deferred, not synchronous — same non-blocking pattern as the reset email.
    assert sent == []
    assert resp.background is not None
    asyncio.run(resp.background())
    assert len(sent) == 1
    to, subject, body = sent[0][0], sent[0][1], sent[0][2]
    assert to == "welcome@example.com"
    assert "https://welcomestudio.mise.test/admin/login" in body
    assert "Welcome Studio" in subject


def test_login_confirms_trial_after_checkout_redirect(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/admin/login",
                "query_string": b"trial=1",
                "headers": [(b"host", b"alpha.mise.test"), (b"accept", b"text/html")],
                "scheme": "https",
                "server": ("alpha.mise.test", 443),
                "client": ("127.0.0.1", 50000),
            }
        )
        body = asyncio.run(auth.login_form(request)).body.decode()
    assert "your free trial is active" in body


def test_outbound_email_identity_is_tenant_scoped(tmp_path, monkeypatch):
    from app import mailer

    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SITE_NAME", "Operator Studio")
    monkeypatch.setattr(config, "GMAIL_USER", "operator@gmail.test")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime("alpha"):
        # Identity: the studio's name over the operator's SMTP login; replies reach
        # the studio owner, never the platform operator.
        assert mailer.sender_name() == "Alpha Studio"
        assert mailer.studio_inbox() == "alpha@example.com"
        msg = mailer._build_message("client@example.com", "Your gallery", "hi")
        assert msg["From"] == "Alpha Studio <operator@gmail.test>"
        assert msg["Reply-To"] == "alpha@example.com"
        # An explicit reply_to (e.g. a lead's own address) still wins.
        lead = mailer._build_message("x@example.com", "s", "b", reply_to="lead@example.com")
        assert lead["Reply-To"] == "lead@example.com"
    # Platform/root context: operator identity, no implicit Reply-To.
    msg = mailer._build_message("x@example.com", "s", "b")
    assert msg["From"] == "Operator Studio <operator@gmail.test>"
    assert msg["Reply-To"] is None


def test_single_tenant_email_identity_unchanged(monkeypatch):
    from app import mailer

    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SITE_NAME", "Kevin Lee Photography")
    monkeypatch.setattr(config, "GMAIL_USER", "kevin@gmail.test")
    assert mailer.sender_name() == "Kevin Lee Photography"
    assert mailer.studio_inbox() == "kevin@gmail.test"
    msg = mailer._build_message("x@example.com", "s", "b")
    assert msg["From"] == "Kevin Lee Photography <kevin@gmail.test>"
    assert msg["Reply-To"] is None


def test_booking_links_use_tenant_host(tmp_path, monkeypatch):
    from app import booking_notify, urls

    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime("alpha"):
        # A studio's client must land on the studio's origin, not the platform's.
        assert urls.public_base_url() == "https://alpha.mise.test"
        assert booking_notify._manage_url("tok123") == "https://alpha.mise.test/booking/tok123"


def test_operator_integrations_fail_closed_in_tenant_context(tmp_path, monkeypatch):
    from app import features, gcal

    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "QUO_API_KEY", "quo-key")
    monkeypatch.setattr(config, "QUO_NUMBER", "+15550001111")
    monkeypatch.setattr(config, "NOTION_TOKEN", "secret-notion")
    monkeypatch.setattr(config, "NOTION_BOOKINGS_DB", "db1")
    monkeypatch.setattr(config, "NOTION_SESSIONS_DB", "db2")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "gsecret")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    # Operator/root context: the operator's own integrations work as before.
    assert features.operator_context() is True
    assert features.sms_enabled() and features.notion_bookings_enabled()
    assert features.notion_sessions_enabled() and gcal.configured()
    # Tenant context: every operator-credential integration is OFF (fail-closed) —
    # a studio's bookings must never mirror into the operator's Notion/Calendar,
    # and a studio's texts must never send from the operator's number.
    with saas.tenant_runtime("alpha"):
        assert features.operator_context() is False
        assert not features.sms_enabled()
        assert not features.notion_enabled()
        assert not features.notion_bookings_enabled()
        assert not features.notion_sessions_enabled()
        assert not gcal.configured()


def test_lead_notifications_route_to_tenant_owner(tmp_path, monkeypatch):
    from app import mailer

    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "GMAIL_USER", "operator@gmail.test")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime("alpha"):
        assert mailer.studio_inbox() == "alpha@example.com"
    assert mailer.studio_inbox() == "operator@gmail.test"
