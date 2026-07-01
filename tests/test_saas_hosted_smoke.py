import asyncio

from starlette.requests import Request

from app import config, db, onboarding, saas
from app.admin import activity, auth


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
