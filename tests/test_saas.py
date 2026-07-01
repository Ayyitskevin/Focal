import asyncio
import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import config, db, features, passwords, saas, saas_demo, saas_preflight, security


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


def _request(path: str, host: str, *, cookie: str | None = None) -> Request:
    headers = [(b"host", host.encode()), (b"accept", b"text/html")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": headers,
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def test_hosted_price_is_locked_to_twenty_dollars():
    assert config.SAAS_PRICE_CENTS == 2000


def test_password_hash_verifies_and_rejects_wrong_password():
    encoded = passwords.hash_password("correct horse")
    assert passwords.verify_password("correct horse", encoded)
    assert not passwords.verify_password("wrong horse", encoded)


def test_tenant_slug_from_host(monkeypatch):
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")

    assert saas.tenant_slug_from_host("river.mise.test") == "river"
    assert saas.tenant_slug_from_host("mise.test") is None
    assert saas.tenant_slug_from_host("www.mise.test") is None
    assert saas.tenant_slug_from_host("too.deep.mise.test") is None
    assert saas._platform_path("/webhooks/stripe")
    assert saas._platform_path("/webhooks/stripe/saas")
    assert saas._platform_path("/demo")
    assert saas._platform_path("/admin/login")
    assert saas._platform_path("/admin/saas")


def test_tenant_databases_are_isolated(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    alpha = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    beta = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")

    with saas.tenant_runtime(alpha):
        db.run(
            "INSERT INTO clients (name, email, market) VALUES (?,?,?)",
            ("Alpha Client", "client@alpha.test", "demo"),
        )
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 1
        assert security.check_admin_password("secret123")
        assert not security.check_admin_password("wrong")

    with saas.tenant_runtime(beta):
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 0
        db.run(
            "INSERT INTO clients (name, email, market) VALUES (?,?,?)",
            ("Beta Client", "client@beta.test", "demo"),
        )
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 1

    with saas.tenant_runtime(alpha):
        assert db.one("SELECT name FROM clients")["name"] == "Alpha Client"


def test_signup_attribution_is_sanitized_and_stored(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)

    tenant = saas.create_tenant(
        "source-test",
        "Source Studio",
        "source@example.com",
        "secret123",
        signup_source="x / twitter <script>",
        signup_campaign="beta launch!",
        signup_referrer="https://example.com/post?x=<bad>",
    )

    assert tenant["signup_source"] == "x / twitter script"
    assert tenant["signup_campaign"] == "beta launch"
    assert tenant["signup_referrer"] == "https://example.com/post?x=bad"


def test_platform_admin_password_is_separate_from_tenant_password(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    assert security.check_admin_password("operator-secret")
    assert not security.check_admin_password("secret123")

    with saas.tenant_runtime(tenant):
        assert security.check_admin_password("secret123")
        assert not security.check_admin_password("operator-secret")


def test_account_settings_update_custom_domain_and_branding(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    updated = saas.update_tenant_account(
        tenant["id"],
        studio_name="Alpha Weddings",
        owner_email="owner@alpha.test",
        custom_domain="https://clients.alpha.test/",
        brand_accent="#A1B2C3",
    )

    assert updated["studio_name"] == "Alpha Weddings"
    assert updated["owner_email"] == "owner@alpha.test"
    assert updated["custom_domain"] == "clients.alpha.test"
    assert updated["brand_accent"] == "#a1b2c3"
    assert updated["custom_domain_verified_at"] is None
    assert saas.tenant_slug_from_host("clients.alpha.test") == "alpha"
    assert saas.tenant_slug_from_host("alpha.mise.test") == "alpha"


def test_custom_domain_is_unique_per_tenant(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    alpha = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    beta = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    saas.update_tenant_account(
        alpha["id"],
        studio_name="Alpha Studio",
        owner_email="alpha@example.com",
        custom_domain="clients.alpha.test",
        brand_accent="#2f5c45",
    )

    with pytest.raises(ValueError, match="already connected"):
        saas.update_tenant_account(
            beta["id"],
            studio_name="Beta Studio",
            owner_email="beta@example.com",
            custom_domain="clients.alpha.test",
            brand_accent="#2f5c45",
        )


def test_custom_domain_verification_marks_seen_host(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    tenant = saas.update_tenant_account(
        tenant["id"],
        studio_name="Alpha Studio",
        owner_email="alpha@example.com",
        custom_domain="clients.alpha.test",
        brand_accent="#2f5c45",
    )

    verified = saas.mark_custom_domain_verified(tenant, "clients.alpha.test")

    assert verified["custom_domain_verified_at"]


def test_operator_overview_summarizes_tenants(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    alpha = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    beta = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    saas.update_tenant_billing(alpha["id"], plan_status="active")
    saas.update_tenant_billing(beta["id"], plan_status="past_due")
    saas.update_tenant_account(
        beta["id"],
        studio_name="Beta Studio",
        owner_email="beta@example.com",
        custom_domain="clients.beta.test",
        brand_accent="#2f5c45",
    )

    overview = saas.operator_tenant_overview()

    assert overview["counts"]["total"] == 2
    assert overview["counts"]["active"] == 1
    assert overview["counts"]["attention"] == 1
    assert overview["counts"]["custom_domains_pending"] == 1
    assert overview["counts"]["active_mrr_cents"] == 2000
    assert overview["counts"]["trial_pipeline_cents"] == 0
    assert overview["counts"]["support_queue"] == 2
    assert overview["counts"]["launch_ready"] == 0
    assert overview["counts"]["trials_at_risk"] == 0
    assert overview["growth"]["active_rate"] == 50
    assert overview["growth"]["top_source"] == "direct"
    assert overview["growth"]["source_rows"] == [{"source": "direct", "count": 2}]
    beta_row = next(r for r in overview["rows"] if r["tenant"]["slug"] == "beta")
    assert beta_row["domain_state"] == "pending"
    assert beta_row["tenant_url"] == "https://beta.mise.test/admin/login"
    assert beta_row["launch"]["score"] == 25


def test_operator_launch_checklist_tracks_launch_blockers(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(config, "SAAS_STRIPE_PRICE_ID", "price_20")
    monkeypatch.setattr(config, "SAAS_STRIPE_WEBHOOK_SECRET", "whsec_test")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.update_tenant_billing(tenant["id"], plan_status="active")
    overview = saas.operator_tenant_overview()

    checklist = saas.operator_launch_checklist(overview, {"ready": True})

    assert checklist["headline"] == "Launch room is clear"
    assert checklist["done"] == checklist["total"]
    assert [item["label"] for item in checklist["items"]] == [
        "Hosted preflight is ready",
        "Stripe billing is configured",
        "Public demo and pricing are linked",
        "At least one test studio exists",
        "Support queue is clear",
    ]


def test_operator_overview_reports_launch_health_and_trial_risk(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    ready = saas.create_tenant("ready", "Ready Studio", "ready@example.com", "secret123")
    risk = saas.create_tenant("risk", "Risk Studio", "risk@example.com", "secret123")
    saas.update_tenant_billing(ready["id"], plan_status="active")
    with saas.control_connect() as con:
        con.execute(
            "UPDATE tenants SET trial_ends_at=? WHERE id=?",
            (saas._iso(datetime.now(UTC) + timedelta(days=2)), risk["id"]),
        )

    with saas.tenant_runtime(ready):
        db.run(
            "INSERT INTO packages (slug, name, price_cents) VALUES (?,?,?)",
            ("starter", "Starter", 20000),
        )
        db.run(
            """INSERT INTO workflow_rules
               (name, trigger_key, action_key, task_title, delay_days)
               VALUES (?,?,?,?,?)""",
            ("Delivery follow-up", "gallery_published", "task", "Follow up", 1),
        )
        saas_demo.seed_preset("fnb")

    overview = saas.operator_tenant_overview()

    assert overview["counts"]["launch_ready"] == 1
    assert overview["counts"]["trials_at_risk"] == 1
    assert overview["counts"]["average_launch_score"] == 62
    ready_row = next(r for r in overview["rows"] if r["tenant"]["slug"] == "ready")
    risk_row = next(r for r in overview["rows"] if r["tenant"]["slug"] == "risk")
    assert ready_row["launch"]["complete"] is True
    assert ready_row["launch"]["score"] == 100
    assert risk_row["launch"]["complete"] is False
    assert risk_row["launch"]["score"] == 25


def test_operator_growth_metrics_track_sources_and_activation(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    saas.create_tenant(
        "newsletter-one",
        "Newsletter One",
        "one@example.com",
        "secret123",
        signup_source="newsletter",
    )
    two = saas.create_tenant(
        "newsletter-two",
        "Newsletter Two",
        "two@example.com",
        "secret123",
        signup_source="newsletter",
    )
    saas.create_tenant("direct-one", "Direct One", "direct@example.com", "secret123")
    saas.update_tenant_billing(two["id"], plan_status="active")

    overview = saas.operator_tenant_overview()

    assert overview["growth"]["active_rate"] == 33
    assert overview["growth"]["activation_rate"] == 0
    assert overview["growth"]["top_source"] == "newsletter"
    assert overview["growth"]["source_rows"] == [
        {"source": "newsletter", "count": 2},
        {"source": "direct", "count": 1},
    ]


def test_operator_trial_nudges_draft_high_leverage_followups(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    started = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(saas, "_now", lambda: started)
    risk = saas.create_tenant("risk", "Risk Studio", "risk@example.com", "secret123")
    saas.create_tenant("early", "Early Studio", "early@example.com", "secret123")
    past_due = saas.create_tenant("due", "Due Studio", "due@example.com", "secret123")
    saas.update_tenant_billing(past_due["id"], plan_status="past_due")
    with saas.control_connect() as con:
        con.execute(
            "UPDATE tenants SET trial_ends_at=? WHERE id=?",
            (saas._iso(started + timedelta(days=2)), risk["id"]),
        )

    nudges = saas.operator_trial_nudges()

    labels = [n["label"] for n in nudges]
    assert labels[:2] == ["Trial rescue", "Billing recovery"]
    assert "Setup nudge" in labels
    rescue = next(n for n in nudges if n["tenant"]["slug"] == "risk")
    assert rescue["days_left"] == 2
    assert rescue["mailto"].startswith("mailto:risk@example.com?subject=")
    assert "Studio%20login" in rescue["mailto"]
    assert next(n for n in nudges if n["tenant"]["slug"] == "early")["reason"] == (
        "setup is still early in the trial"
    )


def test_operator_tenant_export_csv_tracks_growth_and_revenue(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant(
        "source-test",
        "Source Studio",
        "source@example.com",
        "secret123",
        signup_source="x",
        signup_campaign="launch-thread",
        signup_referrer="https://x.test/post",
    )
    saas.update_tenant_billing(tenant["id"], plan_status="active")
    csv_text = saas.operator_tenant_export_csv()

    rows = list(csv.DictReader(io.StringIO(csv_text)))

    assert rows[0]["slug"] == "source-test"
    assert rows[0]["plan_status"] == "active"
    assert rows[0]["signup_source"] == "x"
    assert rows[0]["signup_campaign"] == "launch-thread"
    assert rows[0]["active_mrr_cents"] == "2000"
    assert rows[0]["trial_pipeline_cents"] == "0"
    assert rows[0]["tenant_url"] == "https://source-test.mise.test/admin/login"


def test_operator_support_actions_update_billing_and_domain(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    tenant = saas.update_tenant_account(
        tenant["id"],
        studio_name="Alpha Studio",
        owner_email="alpha@example.com",
        custom_domain="clients.alpha.test",
        brand_accent="#2f5c45",
    )

    updated = saas.operator_update_tenant_status(tenant["id"], "active")
    assert updated["plan_status"] == "active"

    verified = saas.operator_set_domain_verified(tenant["id"], verified=True)
    assert verified["custom_domain_verified_at"]
    reset = saas.operator_set_domain_verified(tenant["id"], verified=False)
    assert reset["custom_domain_verified_at"] is None

    with pytest.raises(ValueError, match="Unsupported"):
        saas.operator_update_tenant_status(tenant["id"], "enterprise")


def test_operator_console_renders_for_platform_admin(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-secret")
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(config, "SAAS_STRIPE_PRICE_ID", "price_20")
    monkeypatch.setattr(config, "SAAS_STRIPE_WEBHOOK_SECRET", "whsec_test")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    cookie = f"{security.ADMIN_COOKIE}={security.sign('operator')}"

    response = asyncio.run(
        saas.operator_console(_request("/admin/saas", "mise.test", cookie=cookie))
    )

    assert response.status_code == 200
    assert response.context["overview"]["counts"]["total"] == 1
    assert response.context["price_cents"] == 2000
    assert response.context["trial_nudges"][0]["label"] == "Setup nudge"


def test_operator_csv_export_route_is_platform_admin_only(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-secret")
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.update_tenant_billing(tenant["id"], plan_status="active")
    cookie = f"{security.ADMIN_COOKIE}={security.sign('operator')}"

    response = asyncio.run(
        saas.operator_tenants_export(_request("/admin/saas/export.csv", "mise.test", cookie=cookie))
    )

    assert response.status_code == 200
    assert response.media_type == "text/csv; charset=utf-8"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="mise_hosted_tenants.csv"'
    )
    assert "alpha,Alpha Studio,alpha@example.com,active" in response.body.decode()


def test_tenant_admin_shows_env_announcement_banner(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(config, "SAAS_ANNOUNCEMENT", "New wedding starter pack is live.")
    monkeypatch.setattr(config, "SAAS_ANNOUNCEMENT_URL", "/admin/onboarding")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    cookie = f"{security.ADMIN_COOKIE}={security.sign('tenant:alpha')}"

    with saas.tenant_runtime(tenant):
        request = _request("/admin/billing", "alpha.mise.test", cookie=cookie)
        request.state.tenant = tenant
        request.state.saas_billing = saas.tenant_billing_context(tenant)
        response = asyncio.run(saas.billing(request))

    body = response.body.decode()
    assert "New wedding starter pack is live." in body
    assert 'href="/admin/onboarding"' in body


def test_billing_portal_uses_customer_and_return_url(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.update_tenant_billing(tenant["id"], stripe_customer_id="cus_123")
    tenant = saas.tenant_by_slug("alpha")
    seen = {}

    class FakeSession:
        @staticmethod
        def create(**kwargs):
            seen.update(kwargs)
            return type("Session", (), {"url": "https://billing.stripe.test/session"})()

    class FakeBillingPortal:
        Session = FakeSession

    class FakeStripe:
        billing_portal = FakeBillingPortal

    monkeypatch.setattr(saas, "_stripe", lambda: FakeStripe)

    url = saas.create_billing_portal_url(tenant, "https://alpha.mise.test/admin/billing")

    assert url == "https://billing.stripe.test/session"
    assert seen == {
        "api_key": "sk_test",
        "customer": "cus_123",
        "return_url": "https://alpha.mise.test/admin/billing",
    }


def test_trial_access_expires_and_billing_context_blocks(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    started = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(saas, "_now", lambda: started)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    assert saas.tenant_has_access(tenant)
    active_trial = saas.tenant_billing_context(tenant)
    assert active_trial["tone"] == "ok"
    assert active_trial["access_ok"] is True

    monkeypatch.setattr(saas, "_now", lambda: started + timedelta(days=15))
    expired = saas.tenant_by_slug("alpha")
    assert not saas.tenant_has_access(expired)
    blocked = saas.tenant_billing_context(expired)
    assert blocked["tone"] == "block"
    assert blocked["access_ok"] is False
    assert "Trial ended" in blocked["message"]


def test_billing_context_warns_near_trial_end_and_ok_for_active(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    started = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(saas, "_now", lambda: started)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")

    monkeypatch.setattr(saas, "_now", lambda: started + timedelta(days=12))
    warning = saas.tenant_billing_context(saas.tenant_by_slug("alpha"))
    assert warning["tone"] == "warn"
    assert warning["access_ok"] is True
    assert "Trial ends" in warning["message"]

    saas.update_tenant_billing(tenant["id"], plan_status="active")
    active = saas.tenant_billing_context(saas.tenant_by_slug("alpha"))
    assert active["tone"] == "ok"
    assert active["message"] == "Hosted plan active at $20/month."


def test_onboarding_demo_seeds_project_flow(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("demostudio", "Demo Studio", "demo@example.com", "secret123")

    with saas.tenant_runtime(tenant):
        result = saas_demo.seed_preset("wedding")
        assert result["created"] is True
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 1
        assert db.one("SELECT status FROM projects")["status"] == "contract_signed"
        assert db.one("SELECT status FROM proposals")["status"] == "accepted"
        assert db.one("SELECT status FROM contracts")["status"] == "signed"
        assert db.one("SELECT status FROM invoices")["status"] == "sent"
        assert db.one("SELECT published FROM galleries")["published"] == 1

        again = saas_demo.seed_preset("wedding")
        assert again["created"] is False
        assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 1


# ── Tenant-bound admin sessions (ADR 0048) — cross-tenant isolation ──────────


def test_admin_principal_is_context_bound(tmp_path, monkeypatch):
    # single-tenant: legacy "admin" (unchanged so existing self-hosted sessions survive)
    monkeypatch.setattr(config, "SAAS_MODE", False)
    assert security.admin_principal(_request("/admin/home", "studio.example")) == "admin"
    # hosted: "operator" at the root host, "tenant:<slug>" inside a tenant runtime
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    assert security.admin_principal(_request("/admin/saas", "mise.test")) == "operator"
    with saas.tenant_runtime(tenant):
        assert security.admin_principal(_request("/admin", "alpha.mise.test")) == "tenant:alpha"


def test_tenant_cookie_rejected_on_another_tenant(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    beta = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    # Alpha's own valid cookie, replayed against Beta's subdomain.
    cookie = f"{security.ADMIN_COOKIE}={security.sign('tenant:alpha')}"
    with saas.tenant_runtime(beta):
        request = _request("/admin/billing", "beta.mise.test", cookie=cookie)
        assert security.is_admin(request) is False
        with pytest.raises(HTTPException) as exc:
            security.require_admin(request)
        assert exc.value.status_code == 303


def test_tenant_cookie_rejected_at_operator_console(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-secret")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    # A tenant cookie presented at the platform/root host must NOT reach the operator console.
    cookie = f"{security.ADMIN_COOKIE}={security.sign('tenant:alpha')}"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(saas.operator_console(_request("/admin/saas", "mise.test", cookie=cookie)))
    assert exc.value.status_code == 303


def test_operator_cookie_rejected_on_tenant(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    cookie = f"{security.ADMIN_COOKIE}={security.sign('operator')}"
    with saas.tenant_runtime(tenant):
        request = _request("/admin/billing", "alpha.mise.test", cookie=cookie)
        assert security.is_admin(request) is False


def test_matching_cookies_still_authenticate(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        req = _request(
            "/admin",
            "alpha.mise.test",
            cookie=f"{security.ADMIN_COOKIE}={security.sign('tenant:alpha')}",
        )
        assert security.is_admin(req) is True
    op = _request(
        "/admin/saas", "mise.test", cookie=f"{security.ADMIN_COOKIE}={security.sign('operator')}"
    )
    assert security.is_admin(op) is True


# ── Hosted client-payment isolation (ADR 0049) — fail-closed, per-tenant Stripe ──


def _set_tenant_stripe(slug: str, *, secret: str = "", webhook: str = "") -> None:
    with saas.control_connect() as con:
        con.execute(
            "UPDATE tenants SET client_stripe_secret_key=?, client_stripe_webhook_secret=? "
            "WHERE slug=?",
            (secret, webhook, slug),
        )


def test_client_stripe_key_is_operator_key_in_single_tenant(monkeypatch):
    # Single-tenant: unchanged — the operator's own key charges the operator's own clients.
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_operator")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_operator")
    assert features.client_stripe_secret_key() == "sk_live_operator"
    assert features.client_stripe_webhook_secret() == "whsec_operator"
    assert features.stripe_enabled() is True
    assert features.stripe_webhook_enabled() is True


def test_hosted_client_payments_fail_closed_without_tenant_key(tmp_path, monkeypatch):
    # The money-boundary invariant: even with the operator key present, a tenant with no
    # Stripe of its own can charge NOTHING — the operator key is never used for a client.
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_operator")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_operator")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime("alpha"):
        assert features.client_stripe_secret_key() == ""
        assert features.client_stripe_webhook_secret() == ""
        assert features.stripe_enabled() is False
        assert features.stripe_webhook_enabled() is False


def test_hosted_client_payments_use_tenant_own_key(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_operator")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    _set_tenant_stripe("alpha", secret="sk_live_alpha", webhook="whsec_alpha")
    with saas.tenant_runtime("alpha"):
        # The tenant's OWN key, never the operator's.
        assert features.client_stripe_secret_key() == "sk_live_alpha"
        assert features.client_stripe_webhook_secret() == "whsec_alpha"
        assert features.stripe_enabled() is True


def test_hosted_no_tenant_context_never_leaks_operator_key(tmp_path, monkeypatch):
    # At the platform/root host (no tenant) the client-charge path resolves to nothing,
    # so the operator key can never charge a "client" of the platform itself.
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_operator")
    assert saas.current_tenant() is None
    assert features.client_stripe_secret_key() == ""
    assert features.stripe_enabled() is False


def test_preflight_passes_client_payment_isolation_when_failclosed(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_operator")
    report = saas_preflight.check_readiness(project_root=tmp_path, write_probes=False)
    check = next(c for c in report["checks"] if c["key"] == "client_payment_isolation")
    assert check["status"] == "pass"


def test_preflight_fails_when_client_charge_would_use_operator_key(tmp_path, monkeypatch):
    # Regression tripwire: if a refactor makes the client-charge path resolve a key with no
    # tenant in context (i.e. the operator key), preflight must fail the launch.
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(features, "client_stripe_secret_key", lambda: "sk_live_operator")
    report = saas_preflight.check_readiness(project_root=tmp_path, write_probes=False)
    check = next(c for c in report["checks"] if c["key"] == "client_payment_isolation")
    assert check["status"] == "fail"
    assert report["ready"] is False
