#!/usr/bin/env python3
"""Hosted SaaS smoke rehearsal for local/staging launch checks.

This script imports the app after forcing SaaS mode into temp SQLite paths. It
does not touch flow:/opt/mise, production data, Stripe, or kleephotography.com.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"smoke failed: {message}")


root = Path(tempfile.mkdtemp(prefix="mise-saas-smoke-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(
    {
        "MISE_ENV_FILE": "/nonexistent",
        "MISE_SECRET_KEY": "saas-smoke-secret",
        "MISE_ADMIN_PASSWORD": "unused-in-saas-mode",
        "MISE_DATA_DIR": str(root / "single"),
        "MISE_BASE_URL": "https://mise.test",
        "MISE_SAAS_MODE": "true",
        "MISE_SAAS_ROOT_DOMAIN": "mise.test",
        "MISE_SAAS_MARKETING_HOST": "mise.test",
        "MISE_SAAS_CONTROL_DB_PATH": str(root / "control.db"),
        "MISE_SAAS_TENANT_DATA_DIR": str(root / "tenants"),
        "MISE_SHOWCASE_SEED": "false",
    }
)

import asyncio  # noqa: E402

from starlette.requests import Request  # noqa: E402

from app import config, saas  # noqa: E402
from app import onboarding as onboarding_state  # noqa: E402
from app.admin import activity, auth  # noqa: E402


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


def main() -> None:
    saas.migrate_control()
    platform = asyncio.run(saas.saas_home(_request("/", "mise.test")))
    _assert(platform.status_code == 200, "platform homepage should render")
    _assert(platform.context["price_cents"] == 2000, "platform homepage should use $20 pricing")

    pricing = asyncio.run(saas.pricing(_request("/pricing", "mise.test")))
    _assert(pricing.status_code == 200, "pricing page should render")
    _assert(pricing.context["trial_days"] == 14, "pricing page should show the free trial")

    trial = asyncio.run(
        saas.start_trial(
            _request("/start-trial", "mise.test", method="POST"),
            studio_name="Smoke Studio",
            owner_email="smoke@example.com",
            slug="smokestudio",
            password="secret123",
        )
    )
    _assert(trial.status_code == 303, "trial signup should redirect")
    _assert(
        trial.headers["location"] == "https://smokestudio.mise.test/admin/login?trial=1",
        "trial signup should land on the tenant login",
    )

    tenant = saas.tenant_by_slug("smokestudio")
    _assert(bool(tenant), "trial signup should create the tenant")
    with saas.tenant_runtime(tenant):
        login = asyncio.run(
            auth.login(_request("/admin/login", "smokestudio.mise.test"), "secret123")
        )
        _assert(login.status_code == 303, "tenant login should redirect")
        _assert(
            login.headers["location"] == onboarding_state.ADMIN_ONBOARDING_PATH,
            "fresh hosted tenants should start in onboarding",
        )

        home = asyncio.run(activity.home(_request("/admin/home", "smokestudio.mise.test")))
        _assert(home.status_code == 303, "home should redirect before rendering a blank dashboard")
        _assert(
            home.headers["location"] == onboarding_state.ADMIN_ONBOARDING_PATH,
            "home should send fresh tenants to onboarding",
        )

    tenant_db = root / "tenants" / "smokestudio" / "mise.db"
    _assert(tenant_db.exists(), "tenant SQLite database should be created")
    _assert(config.SAAS_PRICE_CENTS == 2000, "hosted plan must remain exactly $20/month")

    print(f"ok hosted SaaS smoke passed with temp data at {root}")


if __name__ == "__main__":
    main()
