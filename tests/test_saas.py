import asyncio
import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import config, db, features, passwords, saas, saas_demo, saas_preflight, security

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


def _request(path: str, host: str, *, cookie: str | None = None, method: str = "GET") -> Request:
    headers = [(b"host", host.encode()), (b"accept", b"text/html")]
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
    cookie = f"{security.ADMIN_COOKIE}={security.sign(f'operator:{security._pw_fp(config.ADMIN_PASSWORD)}')}"

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
    cookie = f"{security.ADMIN_COOKIE}={security.sign(f'operator:{security._pw_fp(config.ADMIN_PASSWORD)}')}"

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
    cookie = _tenant_cookie(tenant)

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


def _tenant_cookie(tenant: dict) -> str:
    fp = security._pw_fp(tenant.get("admin_password_hash") or "")
    principal = f"tenant:{tenant['id']}:{tenant['slug']}:{fp}"
    return f"{security.ADMIN_COOKIE}={security.sign(principal)}"


def test_admin_principal_is_context_bound(tmp_path, monkeypatch):
    # Each principal now carries a trailing credential fingerprint (ADR 0063) so a
    # password change evicts live sessions; identity is the stable prefix.
    monkeypatch.setattr(config, "SAAS_MODE", False)
    assert security.admin_principal(_request("/admin/home", "studio.example")).startswith("admin:")
    # hosted: "operator:<fp>" at the root host, "tenant:<id>:<slug>:<fp>" in a tenant runtime
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    assert security.admin_principal(_request("/admin/saas", "mise.test")).startswith("operator:")
    with saas.tenant_runtime(tenant):
        assert security.admin_principal(_request("/admin", "alpha.mise.test")).startswith(
            f"tenant:{tenant['id']}:alpha:"
        )


def test_tenant_cookie_rejected_on_another_tenant(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    alpha = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    beta = saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    # Alpha's own valid cookie, replayed against Beta's subdomain.
    cookie = _tenant_cookie(alpha)
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
    alpha = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    # A tenant cookie presented at the platform/root host must NOT reach the operator console.
    cookie = _tenant_cookie(alpha)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(saas.operator_console(_request("/admin/saas", "mise.test", cookie=cookie)))
    assert exc.value.status_code == 303


def test_operator_cookie_rejected_on_tenant(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    cookie = f"{security.ADMIN_COOKIE}={security.sign(f'operator:{security._pw_fp(config.ADMIN_PASSWORD)}')}"
    with saas.tenant_runtime(tenant):
        request = _request("/admin/billing", "alpha.mise.test", cookie=cookie)
        assert security.is_admin(request) is False


def test_matching_cookies_still_authenticate(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime(tenant):
        req = _request("/admin", "alpha.mise.test", cookie=_tenant_cookie(tenant))
        assert security.is_admin(req) is True
    op = _request(
        "/admin/saas",
        "mise.test",
        cookie=f"{security.ADMIN_COOKIE}={security.sign(f'operator:{security._pw_fp(config.ADMIN_PASSWORD)}')}",
    )
    assert security.is_admin(op) is True


def test_reused_slug_after_delete_does_not_authenticate_old_cookie(tmp_path, monkeypatch):
    # ADR 0051: slugs are reusable after delete, so the session principal carries the
    # tenant id — an old "alpha" cookie must not admin the NEW "alpha".
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    old = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    old_cookie = _tenant_cookie(old)
    saas.delete_tenant_studio(old)
    new = saas.create_tenant("alpha", "New Alpha", "new@example.com", "secret123")
    assert new["id"] != old["id"]
    with saas.tenant_runtime(new):
        req = _request("/admin", "alpha.mise.test", cookie=old_cookie)
        assert security.is_admin(req) is False  # old id ≠ new id
        assert security.is_admin(_request("/admin", "alpha.mise.test", cookie=_tenant_cookie(new)))


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


# ── Billing-lifecycle integrity (ADR 0050) — exactly-once webhooks, dunning, throttle ──


def _subscription_event(event_id: str, tenant: dict, status: str) -> dict:
    return {
        "id": event_id,
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_123",
                "status": status,
                "customer": "cus_123",
                "metadata": {"tenant_id": str(tenant["id"]), "slug": tenant["slug"]},
            }
        },
    }


def _saas_event_recorded(event_id: str) -> bool:
    with saas.control_connect() as con:
        row = con.execute("SELECT 1 FROM saas_events WHERE id=?", (event_id,)).fetchone()
    return row is not None


def test_saas_webhook_event_applies_exactly_once(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    event = _subscription_event("evt_1", tenant, "active")
    assert saas._process_saas_event(event) == {"ok": True, "type": event["type"]}
    assert saas.tenant_by_slug("alpha")["plan_status"] == "active"
    # Stripe retries the same event id → duplicate no-op, state untouched.
    saas.update_tenant_billing(tenant["id"], plan_status="past_due")
    assert saas._process_saas_event(event) == {"ok": True, "duplicate": True}
    assert saas.tenant_by_slug("alpha")["plan_status"] == "past_due"


def test_saas_webhook_failed_effect_stays_retryable(tmp_path, monkeypatch):
    # The exactly-once contract: if the billing effect dies mid-event, the idempotency
    # marker must roll back WITH it, so Stripe's retry reprocesses instead of deduping
    # against a marker whose effect never ran (the old ordering swallowed the event).
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    event = _subscription_event("evt_2", tenant, "canceled")

    def boom(*args, **kwargs):
        raise RuntimeError("crash between marker and effect")

    original = saas.update_tenant_billing
    monkeypatch.setattr(saas, "update_tenant_billing", boom)
    with pytest.raises(RuntimeError):
        saas._process_saas_event(event)
    assert not _saas_event_recorded("evt_2")  # marker rolled back with the effect
    assert saas.tenant_by_slug("alpha")["plan_status"] == "trialing"
    monkeypatch.setattr(saas, "update_tenant_billing", original)
    # The retry (same event id) now succeeds and applies the cancellation.
    assert saas._process_saas_event(event) == {"ok": True, "type": event["type"]}
    assert _saas_event_recorded("evt_2")
    assert saas.tenant_by_slug("alpha")["plan_status"] == "canceled"


def test_past_due_gets_dunning_grace_then_blocks(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    # Fresh past_due (updated_at = now) → access continues during the grace window.
    saas.update_tenant_billing(tenant["id"], plan_status="past_due")
    fresh = saas.tenant_by_slug("alpha")
    assert saas.tenant_has_access(fresh) is True
    banner = saas.tenant_billing_context(fresh)
    assert banner["tone"] == "warn"
    # Grace lapsed → blocked.
    stale = dict(fresh)
    stale["updated_at"] = saas._iso(
        saas._now() - timedelta(days=config.SAAS_PAST_DUE_GRACE_DAYS + 1)
    )
    assert saas.tenant_has_access(stale) is False
    assert saas.tenant_billing_context(stale)["tone"] == "block"
    # Terminal states never get grace; missing updated_at fails closed.
    for status in ("unpaid", "canceled"):
        terminal = dict(fresh)
        terminal["plan_status"] = status
        assert saas.tenant_has_access(terminal) is False
    no_stamp = dict(fresh)
    no_stamp["updated_at"] = None
    assert saas.tenant_has_access(no_stamp) is False


def test_signup_route_is_rate_limited(monkeypatch):
    from app import ratelimit

    assert ratelimit._bucket_for("/start-trial", "POST") == "signup"
    # Merely viewing the form never spends the tight signup budget.
    assert ratelimit._bucket_for("/start-trial", "GET") is None
    monkeypatch.setattr(config, "SAAS_MODE", False)  # keep is_admin on the legacy path
    monkeypatch.setitem(config.RATE_LIMITS, "signup", (3, 3600))
    monkeypatch.setattr(ratelimit, "_hits", type(ratelimit._hits)(ratelimit._hits.default_factory))
    request = _request("/start-trial", "mise.test", method="POST")
    for _ in range(3):
        assert ratelimit.check(request, "/start-trial") is None
    blocked = ratelimit.check(request, "/start-trial")
    assert blocked is not None and blocked.status_code == 429
    # The GET form stays reachable even while POSTs are throttled.
    assert ratelimit.check(_request("/start-trial", "mise.test"), "/start-trial") is None


# ── Recovery & ownership (ADR 0051) — password reset, export, delete ──────────


def test_password_reset_token_roundtrip_and_single_use(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    token = saas.make_password_reset_token(tenant)
    redeemed = saas.redeem_password_reset_token(token)
    assert redeemed is not None and redeemed["id"] == tenant["id"]
    # A session cookie can never act as a reset token (purpose scoping) …
    assert saas.redeem_password_reset_token(security.sign("tenant:alpha")) is None
    # … a tampered token dies …
    assert saas.redeem_password_reset_token(token[:-2] + "xx") is None
    # … and changing the password spends every outstanding token.
    saas.set_tenant_password(tenant["id"], "brand-new-password")
    assert saas.redeem_password_reset_token(token) is None
    assert passwords.verify_password(
        "brand-new-password", saas.tenant_by_slug("alpha")["admin_password_hash"]
    )


def test_forgot_password_emails_owner_only(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    sent: list[tuple] = []
    monkeypatch.setattr(saas.mailer, "configured", lambda: True)
    monkeypatch.setattr(saas.mailer, "send", lambda *a, **k: sent.append(a))
    with saas.tenant_runtime("alpha"):
        req = _request("/admin/forgot", "alpha.mise.test")
        # Wrong address: same outward response, and NO background send (no enumeration,
        # and identical latency because the send is deferred either way).
        miss = asyncio.run(saas.forgot_password(req, email="stranger@example.com"))
        assert miss.background is None
        # Match: the send is deferred to a background task, fired after the response.
        hit = asyncio.run(saas.forgot_password(req, email="Alpha@Example.com"))
        assert sent == []  # not sent synchronously
        assert hit.background is not None
        asyncio.run(hit.background())
    assert len(sent) == 1
    to, _subject, body = sent[0][0], sent[0][1], sent[0][2]
    assert to == "alpha@example.com"
    assert "/admin/reset?token=" in body


def test_reset_password_route_sets_new_password_once(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    token = saas.make_password_reset_token(tenant)
    with saas.tenant_runtime("alpha"):
        req = _request("/admin/reset", "alpha.mise.test")
        resp = asyncio.run(
            saas.reset_password(
                req, token=token, password="newpass123", password_confirm="newpass123"
            )
        )
        assert resp.status_code == 303 and "reset=1" in resp.headers["location"]
        # The same link is spent now.
        again = asyncio.run(
            saas.reset_password(
                req, token=token, password="another-pass", password_confirm="another-pass"
            )
        )
        assert again.status_code == 400
    assert passwords.verify_password(
        "newpass123", saas.tenant_by_slug("alpha")["admin_password_hash"]
    )


def test_reset_token_rejected_on_another_tenant(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    alpha = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.create_tenant("beta", "Beta Studio", "beta@example.com", "secret123")
    token = saas.make_password_reset_token(alpha)
    with saas.tenant_runtime("beta"):
        resp = asyncio.run(
            saas.reset_password(
                _request("/admin/reset", "beta.mise.test"),
                token=token,
                password="newpass123",
                password_confirm="newpass123",
            )
        )
        assert resp.status_code == 400
    # Alpha's password is untouched.
    assert passwords.verify_password(
        "secret123", saas.tenant_by_slug("alpha")["admin_password_hash"]
    )


def test_studio_export_zip_contains_db_and_media(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    media_dir = saas.tenant_data_path("alpha") / "galleries"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "photo.jpg").write_bytes(b"fake-jpeg-bytes")
    tmp_zip = saas.build_studio_export(tenant)
    try:
        import zipfile

        with zipfile.ZipFile(tmp_zip) as zf:
            names = set(zf.namelist())
            assert "mise.db" in names
            assert "galleries/photo.jpg" in names
            # The snapshot is a real SQLite database, not a torn copy.
            assert zf.read("mise.db")[:16] == b"SQLite format 3\x00"
    finally:
        tmp_zip.unlink(missing_ok=True)


def test_delete_studio_tombstones_and_parks_data(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    assert saas.tenant_data_path("alpha").exists()
    saas.delete_tenant_studio(tenant)
    # The address frees up; the row survives as a canceled tombstone.
    assert saas.tenant_by_slug("alpha") is None
    row = saas.tenant_by_id(tenant["id"])
    assert row["slug"].startswith("alpha-deleted-")
    assert row["plan_status"] == "canceled"
    assert row["deleted_at"]
    # The data moved to trash — recoverable, not destroyed.
    assert not saas.tenant_data_path("alpha").exists()
    trash = config.SAAS_TENANT_DATA_DIR / ".trash" / row["slug"]
    assert (trash / "mise.db").exists()


def test_delete_studio_route_requires_exact_confirmation(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    cookie = _tenant_cookie(tenant)
    with saas.tenant_runtime("alpha"):
        req = _request("/admin/delete-studio", "alpha.mise.test", cookie=cookie)
        wrong_slug = asyncio.run(
            saas.delete_studio(req, reason="", confirm_slug="beta", password="secret123")
        )
        assert "delete_error=slug" in wrong_slug.headers["location"]
        wrong_pw = asyncio.run(
            saas.delete_studio(req, reason="", confirm_slug="alpha", password="nope")
        )
        assert "delete_error=password" in wrong_pw.headers["location"]
        assert saas.tenant_by_slug("alpha") is not None  # still alive
        done = asyncio.run(
            saas.delete_studio(req, reason="", confirm_slug="alpha", password="secret123")
        )
        assert done.status_code == 303 and "deleted=1" in done.headers["location"]
    assert saas.tenant_by_slug("alpha") is None


def test_reset_routes_reachable_when_locked_out():
    # A past_due/expired owner must still reach forgot/reset (and billing) to recover.
    assert saas._billing_allowed_path("/admin/forgot")
    assert saas._billing_allowed_path("/admin/reset")


def test_forgot_route_uses_tight_rate_bucket():
    from app import ratelimit

    # Sending the email is throttled hard; viewing the form only costs the admin bucket.
    assert ratelimit._bucket_for("/admin/forgot", "POST") == "signup"
    assert ratelimit._bucket_for("/admin/forgot", "GET") == "admin"


# ── Tenant self-serve Stripe connection (ADR 0054) ────────────────────────────


def _fake_stripe(fail_auth: bool = False, fail_perm: bool = False):
    """Stand-in for the stripe module: records Account.retrieve calls."""
    import types

    class AuthError(Exception):
        pass

    class PermError(Exception):
        pass

    calls: list[str] = []

    class Account:
        @staticmethod
        def retrieve(api_key=None):
            calls.append(api_key)
            if fail_auth:
                raise AuthError("bad key")
            if fail_perm:
                raise PermError("missing scope")
            return {"id": "acct_1"}

    return types.SimpleNamespace(
        AuthenticationError=AuthError, PermissionError=PermError, Account=Account
    ), calls


def test_tenant_connects_own_stripe_and_payments_go_live(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    fake, calls = _fake_stripe()
    monkeypatch.setattr(saas, "_stripe", lambda: fake)
    with saas.tenant_runtime("alpha"):
        req = _request(
            "/admin/account/payments",
            "alpha.mise.test",
            cookie=_tenant_cookie(tenant),
            method="POST",
        )
        resp = asyncio.run(
            saas.update_account_payments(
                req,
                stripe_secret_key="sk_test_abc123def456",
                stripe_webhook_secret="whsec_xyz789",
            )
        )
    assert resp.status_code == 303 and "payments=1" in resp.headers["location"]
    assert calls == ["sk_test_abc123def456"]  # key was live-verified before saving
    # Fresh runtime -> fail-closed gate now resolves the tenant's own key.
    with saas.tenant_runtime("alpha"):
        assert features.stripe_enabled() is True
        assert features.client_stripe_secret_key() == "sk_test_abc123def456"
        page = asyncio.run(
            saas.account(
                _request("/admin/account", "alpha.mise.test", cookie=_tenant_cookie(tenant))
            )
        ).body.decode()
    assert "sk_test_abc123def456" not in page  # the raw secret never renders
    assert "sk_test…f456" in page  # only the mask does
    assert "(test mode)" in page


def test_bad_key_or_missing_webhook_secret_rejected(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    fake, calls = _fake_stripe()
    monkeypatch.setattr(saas, "_stripe", lambda: fake)
    cases = [
        {"stripe_secret_key": "pk_live_wrong_kind", "stripe_webhook_secret": "whsec_ok"},
        {"stripe_secret_key": "sk_test_abc123def456", "stripe_webhook_secret": ""},
        {"stripe_secret_key": "sk_test_abc123def456", "stripe_webhook_secret": "nope"},
    ]
    with saas.tenant_runtime("alpha"):
        req = _request(
            "/admin/account/payments",
            "alpha.mise.test",
            cookie=_tenant_cookie(tenant),
            method="POST",
        )
        for form in cases:
            resp = asyncio.run(saas.update_account_payments(req, **form))
            assert resp.status_code == 400
    assert calls == []  # format failures never reach the live verify
    with saas.tenant_runtime("alpha"):
        assert features.stripe_enabled() is False  # nothing was saved


def test_stripe_rejected_key_is_not_saved(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    fake, _calls = _fake_stripe(fail_auth=True)
    monkeypatch.setattr(saas, "_stripe", lambda: fake)
    with saas.tenant_runtime("alpha"):
        resp = asyncio.run(
            saas.update_account_payments(
                _request(
                    "/admin/account/payments",
                    "alpha.mise.test",
                    cookie=_tenant_cookie(tenant),
                    method="POST",
                ),
                stripe_secret_key="sk_live_stolen_or_typoed",
                stripe_webhook_secret="whsec_ok",
            )
        )
        assert resp.status_code == 400
        assert "rejected" in resp.body.decode()
    with saas.tenant_runtime("alpha"):
        assert features.stripe_enabled() is False


def test_disconnect_returns_payments_to_fail_closed(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.set_tenant_client_stripe(tenant["id"], "sk_test_abc123def456", "whsec_xyz789")
    with saas.tenant_runtime("alpha"):
        assert features.stripe_enabled() is True
        resp = asyncio.run(
            saas.disconnect_account_payments(
                _request(
                    "/admin/account/payments/disconnect",
                    "alpha.mise.test",
                    cookie=_tenant_cookie(tenant),
                    method="POST",
                )
            )
        )
        assert resp.status_code == 303 and "payments_off=1" in resp.headers["location"]
    with saas.tenant_runtime("alpha"):
        assert features.stripe_enabled() is False  # ADR 0049 off state restored: no new charges
        # …but the OLD webhook secret stays verifiable so an in-flight checkout
        # that the client already paid can still record (rotation grace).
        assert features.client_stripe_webhook_secrets() == ["whsec_xyz789"]


def test_permission_rejected_key_is_not_saved(tmp_path, monkeypatch):
    # A 403 is deterministic (restricted key without the needed scopes), not transient:
    # saving it would 500 on the client's pay click, so it must hard-reject.
    _configure_saas(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    fake, _calls = _fake_stripe(fail_perm=True)
    monkeypatch.setattr(saas, "_stripe", lambda: fake)
    with saas.tenant_runtime("alpha"):
        resp = asyncio.run(
            saas.update_account_payments(
                _request(
                    "/admin/account/payments",
                    "alpha.mise.test",
                    cookie=_tenant_cookie(tenant),
                    method="POST",
                ),
                stripe_secret_key="rk_live_minimal_scope_key",
                stripe_webhook_secret="whsec_ok",
            )
        )
        assert resp.status_code == 400
        assert "enough access" in resp.body.decode()
    with saas.tenant_runtime("alpha"):
        assert features.stripe_enabled() is False


def test_webhook_secret_rotation_keeps_previous_secret_verifiable(tmp_path, monkeypatch):
    # The mid-update hazard: a checkout created under secret A stays payable ~24h.
    # Rotating to B must not orphan A-signed deliveries — grace-verify via _prev.
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.set_tenant_client_stripe(tenant["id"], "sk_test_key_for_acct_a", "whsec_AAA")
    saas.set_tenant_client_stripe(tenant["id"], "sk_test_key_for_acct_b", "whsec_BBB")
    with saas.tenant_runtime("alpha"):
        assert features.client_stripe_webhook_secret() == "whsec_BBB"
        assert features.client_stripe_webhook_secrets() == ["whsec_BBB", "whsec_AAA"]
    # Re-saving the SAME webhook secret must not clobber the grace slot.
    saas.set_tenant_client_stripe(tenant["id"], "sk_test_key_for_acct_b2", "whsec_BBB")
    with saas.tenant_runtime("alpha"):
        assert features.client_stripe_webhook_secrets() == ["whsec_BBB", "whsec_AAA"]
    # A second rotation retires A: only the last two secrets ever verify.
    saas.set_tenant_client_stripe(tenant["id"], "sk_test_key_for_acct_c", "whsec_CCC")
    with saas.tenant_runtime("alpha"):
        assert features.client_stripe_webhook_secrets() == ["whsec_CCC", "whsec_BBB"]


def test_client_webhook_accepts_delivery_signed_with_previous_secret(tmp_path, monkeypatch):
    import types

    from app.public import pay

    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.set_tenant_client_stripe(tenant["id"], "sk_test_key_for_acct_a", "whsec_AAA")
    saas.set_tenant_client_stripe(tenant["id"], "sk_test_key_for_acct_b", "whsec_BBB")

    class SigError(Exception):
        pass

    class Webhook:
        @staticmethod
        def construct_event(payload, signature, secret):
            if secret != "whsec_AAA":  # the delivery was signed under the OLD secret
                raise SigError("mismatch")
            return {"type": "ping.ignored", "data": {"object": {}}}

    fake = types.SimpleNamespace(Webhook=Webhook, SignatureVerificationError=SigError)
    monkeypatch.setattr(pay, "_stripe", lambda: fake)

    async def _post():
        request = _request("/webhooks/stripe", "alpha.mise.test", method="POST")

        async def body():
            return b"{}"

        request.body = body
        return await pay.stripe_webhook(request)

    with saas.tenant_runtime("alpha"):
        result = asyncio.run(_post())
    assert result == {"ok": True, "ignored": "ping.ignored"}  # verified via the grace secret


# ── Checkout recovery (ADR 0056) ──────────────────────────────────────────────


def _fake_checkout_stripe():
    import types

    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(id="cs_1", url="https://checkout.stripe.test/cs_1")

    fake = types.SimpleNamespace(
        checkout=types.SimpleNamespace(Session=types.SimpleNamespace(create=create))
    )
    return fake, calls


def _configure_platform_stripe(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_platform")
    monkeypatch.setattr(config, "SAAS_STRIPE_PRICE_ID", "price_20")


def test_expired_trial_can_restart_checkout_and_pays_immediately(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    _configure_platform_stripe(monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.control_connect() as con:
        con.execute(
            "UPDATE tenants SET trial_ends_at=? WHERE id=?",
            (saas._iso(saas._now() - timedelta(days=2)), tenant["id"]),
        )
    fake, calls = _fake_checkout_stripe()
    monkeypatch.setattr(saas, "_stripe", lambda: fake)
    with saas.tenant_runtime("alpha"):
        resp = asyncio.run(
            saas.billing_checkout(
                _request(
                    "/admin/billing/checkout",
                    "alpha.mise.test",
                    cookie=_tenant_cookie(tenant),
                    method="POST",
                )
            )
        )
    assert resp.status_code == 303
    assert resp.headers["location"] == "https://checkout.stripe.test/cs_1"
    (kwargs,) = calls
    # Trial is spent -> Stripe bills immediately; no free-trial re-grant.
    assert "trial_period_days" not in kwargs["subscription_data"]
    assert kwargs["metadata"]["tenant_id"] == str(tenant["id"])
    assert kwargs["success_url"].endswith("/admin/billing?subscribed=1")


def test_mid_trial_checkout_carries_remaining_days_only(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    _configure_platform_stripe(monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.control_connect() as con:
        con.execute(
            "UPDATE tenants SET trial_ends_at=? WHERE id=?",
            (saas._iso(saas._now() + timedelta(days=7, hours=5)), tenant["id"]),
        )
    fake, calls = _fake_checkout_stripe()
    monkeypatch.setattr(saas, "_stripe", lambda: fake)
    with saas.tenant_runtime("alpha"):
        resp = asyncio.run(
            saas.billing_checkout(
                _request(
                    "/admin/billing/checkout",
                    "alpha.mise.test",
                    cookie=_tenant_cookie(tenant),
                    method="POST",
                )
            )
        )
    assert resp.status_code == 303
    assert calls[0]["subscription_data"]["trial_period_days"] == 7  # not another full 14


def test_checkout_recovery_refuses_when_subscription_is_live(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    _configure_platform_stripe(monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    saas.update_tenant_billing(tenant["id"], plan_status="active", stripe_subscription_id="sub_1")
    fake, calls = _fake_checkout_stripe()
    monkeypatch.setattr(saas, "_stripe", lambda: fake)
    with saas.tenant_runtime("alpha"):
        resp = asyncio.run(
            saas.billing_checkout(
                _request(
                    "/admin/billing/checkout",
                    "alpha.mise.test",
                    cookie=_tenant_cookie(tenant),
                    method="POST",
                )
            )
        )
    assert resp.status_code == 303 and "already=1" in resp.headers["location"]
    assert calls == []  # no session was created
    # …but a CANCELED subscription may restart via checkout.
    saas.update_tenant_billing(tenant["id"], plan_status="canceled")
    with saas.tenant_runtime("alpha"):
        resp = asyncio.run(
            saas.billing_checkout(
                _request(
                    "/admin/billing/checkout",
                    "alpha.mise.test",
                    cookie=_tenant_cookie(tenant),
                    method="POST",
                )
            )
        )
    assert resp.headers["location"] == "https://checkout.stripe.test/cs_1"


def test_billing_page_offers_checkout_only_when_recoverable(tmp_path, monkeypatch):
    _configure_saas(tmp_path, monkeypatch)
    _configure_platform_stripe(monkeypatch)
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    tenant = saas.create_tenant("alpha", "Alpha Studio", "alpha@example.com", "secret123")
    with saas.tenant_runtime("alpha"):
        page = asyncio.run(
            saas.billing(
                _request("/admin/billing", "alpha.mise.test", cookie=_tenant_cookie(tenant))
            )
        ).body.decode()
    assert "/admin/billing/checkout" in page  # abandoned-checkout trial can pay
    assert "Start subscription" in page
    saas.update_tenant_billing(tenant["id"], plan_status="active", stripe_subscription_id="sub_1")
    with saas.tenant_runtime("alpha"):
        page = asyncio.run(
            saas.billing(
                _request("/admin/billing", "alpha.mise.test", cookie=_tenant_cookie(tenant))
            )
        ).body.decode()
    assert "/admin/billing/checkout" not in page  # live sub manages via the portal
