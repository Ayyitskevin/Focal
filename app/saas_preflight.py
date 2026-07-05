"""Hosted SaaS readiness checks for launch and operator support."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from . import config

BAD_SECRET_VALUES = {
    "",
    "change-me",
    "changeme",
    "secret",
    "test",
    "test-secret",
    "saas-smoke-secret",
    "unused-in-saas-mode",
}


def _check(key: str, label: str, status: str, detail: str, fix: str = "") -> dict:
    tone = {"pass": "is-active", "warn": "is-sent", "fail": "is-declined"}[status]
    return {
        "key": key,
        "label": label,
        "status": status,
        "tone": tone,
        "detail": detail,
        "fix": fix,
    }


def _present_secret(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and value.lower() not in BAD_SECRET_VALUES


def _host(url: str) -> str:
    parsed = urlsplit(url)
    return (parsed.hostname or "").lower()


def _writable_dir(path: Path, *, create: bool, probe: bool) -> tuple[bool, str]:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            return False, f"{path} does not exist"
        if not path.is_dir():
            return False, f"{path} is not a directory"
        if not probe:
            return True, f"{path} exists"
        probe = path / ".mise-preflight"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        return True, f"{path} is writable"
    except OSError as exc:
        return False, f"{path} is not writable: {exc}"


def check_readiness(*, project_root: Path | None = None, write_probes: bool = False) -> dict:
    """Return a structured hosted-launch readiness report.

    ``write_probes`` creates missing data directories and writes short temp files.
    The admin UI keeps it off; the CLI turns it on by default for real launch
    checks.
    """
    project_root = project_root or Path.cwd()
    checks: list[dict] = []

    checks.append(
        _check(
            "saas_mode",
            "Hosted mode",
            "pass" if config.SAAS_MODE else "fail",
            "MISE_SAAS_MODE=true" if config.SAAS_MODE else "MISE_SAAS_MODE is not enabled",
            "Set MISE_SAAS_MODE=true on the hosted service.",
        )
    )

    parsed_base = urlsplit(config.BASE_URL)
    base_host = _host(config.BASE_URL)
    root_domain = (config.SAAS_ROOT_DOMAIN or base_host).strip().lower()
    if parsed_base.scheme == "https" and base_host:
        base_status = "pass"
        base_detail = f"public base URL is {config.BASE_URL}"
    elif base_host in {"localhost", "127.0.0.1"}:
        base_status = "warn"
        base_detail = "BASE_URL is local; acceptable only for local rehearsal"
    else:
        base_status = "fail"
        base_detail = "BASE_URL must be a public HTTPS URL"
    checks.append(
        _check(
            "base_url",
            "Public URL",
            base_status,
            base_detail,
            "Set MISE_BASE_URL=https://your-hosted-domain.",
        )
    )

    root_ok = bool(root_domain and "." in root_domain)
    checks.append(
        _check(
            "root_domain",
            "Root domain",
            "pass" if root_ok else "fail",
            f"tenant root is {root_domain}" if root_ok else "MISE_SAAS_ROOT_DOMAIN is missing",
            "Set MISE_SAAS_ROOT_DOMAIN to the wildcard tenant domain.",
        )
    )

    if config.SAAS_PRICE_CENTS == 2000:
        checks.append(_check("price", "Flat price", "pass", "hosted plan is locked at $20/month"))
    else:
        checks.append(
            _check(
                "price",
                "Flat price",
                "fail",
                f"hosted plan is {config.SAAS_PRICE_CENTS} cents",
                "Keep the SaaS plan locked to exactly 2000 cents.",
            )
        )

    checks.append(
        _check(
            "trial",
            "Trial length",
            "pass" if config.SAAS_TRIAL_DAYS == 14 else "fail",
            f"{config.SAAS_TRIAL_DAYS}-day trial configured",
            "Set MISE_SAAS_TRIAL_DAYS=14.",
        )
    )

    checks.append(
        _check(
            "secret_key",
            "App secret",
            "pass" if _present_secret(config.SECRET_KEY) else "fail",
            "MISE_SECRET_KEY is set"
            if _present_secret(config.SECRET_KEY)
            else "MISE_SECRET_KEY is weak or missing",
            "Set a long random MISE_SECRET_KEY.",
        )
    )
    checks.append(
        _check(
            "operator_password",
            "Operator password",
            "pass" if _present_secret(config.ADMIN_PASSWORD) else "fail",
            "operator admin password is set"
            if _present_secret(config.ADMIN_PASSWORD)
            else "MISE_ADMIN_PASSWORD is weak or missing",
            "Set a strong MISE_ADMIN_PASSWORD for root-host operator access.",
        )
    )
    checks.append(
        _check(
            "cookie_secure",
            "Secure cookies",
            "pass" if config.COOKIE_SECURE else "fail",
            "cookies require HTTPS" if config.COOKIE_SECURE else "MISE_COOKIE_SECURE is false",
            "Set MISE_COOKIE_SECURE=true behind HTTPS.",
        )
    )

    stripe_ready = bool(config.STRIPE_SECRET_KEY and config.SAAS_STRIPE_PRICE_ID)
    checks.append(
        _check(
            "stripe_checkout",
            "Stripe checkout",
            "pass" if stripe_ready else "fail",
            "Stripe secret and $20 Price ID are configured"
            if stripe_ready
            else "Stripe secret or SaaS Price ID is missing",
            "Set MISE_STRIPE_SECRET_KEY and MISE_SAAS_STRIPE_PRICE_ID.",
        )
    )
    checks.append(
        _check(
            "stripe_webhook",
            "Stripe SaaS webhook",
            "pass" if config.SAAS_STRIPE_WEBHOOK_SECRET else "fail",
            "SaaS webhook secret is configured"
            if config.SAAS_STRIPE_WEBHOOK_SECRET
            else "SaaS webhook secret is missing",
            "Set MISE_SAAS_STRIPE_WEBHOOK_SECRET for /webhooks/stripe/saas.",
        )
    )
    checks.append(
        _check(
            "stripe_api_version",
            "Stripe API version",
            "pass" if config.STRIPE_API_VERSION else "warn",
            f"pinned to {config.STRIPE_API_VERSION}"
            if config.STRIPE_API_VERSION
            else "unpinned — SDK bumps can shift the API contract",
            "Set MISE_STRIPE_API_VERSION to the tested version; bump it deliberately "
            "after a Stripe test-mode rehearsal.",
        )
    )

    if config.SAAS_MODE:
        from . import features

        # Client-invoice charges must resolve the *tenant's* Stripe key, never the
        # operator's platform key. At preflight time no tenant is in context, so a
        # correct build resolves to "" (fail-closed). A non-empty result means the
        # operator key would be used to charge a studio's client — the money-boundary
        # leak that ADR 0041 closes.
        leaks_operator_key = bool(features.client_stripe_secret_key())
        checks.append(
            _check(
                "client_payment_isolation",
                "Client payment isolation",
                "fail" if leaks_operator_key else "pass",
                "operator Stripe key would charge a studio's client (money-boundary leak)"
                if leaks_operator_key
                else (
                    "client-invoice charges resolve the tenant's own Stripe; "
                    "the operator key never charges a studio's client"
                ),
                "Keep client-invoice charges on features.client_stripe_secret_key() "
                "(fail-closed per tenant) in hosted mode.",
            )
        )

    control_ok, control_detail = _writable_dir(
        Path(config.SAAS_CONTROL_DB_PATH).parent, create=write_probes, probe=write_probes
    )
    checks.append(
        _check(
            "control_db",
            "Control DB path",
            "pass" if control_ok else "fail",
            control_detail,
            "Mount a writable directory for MISE_SAAS_CONTROL_DB_PATH.",
        )
    )
    tenant_ok, tenant_detail = _writable_dir(
        Path(config.SAAS_TENANT_DATA_DIR), create=write_probes, probe=write_probes
    )
    checks.append(
        _check(
            "tenant_data",
            "Tenant data path",
            "pass" if tenant_ok else "fail",
            tenant_detail,
            "Mount a writable MISE_SAAS_TENANT_DATA_DIR volume.",
        )
    )

    for filename in ("Dockerfile", "docker-compose.yml", "Caddyfile"):
        exists = (project_root / filename).exists()
        checks.append(
            _check(
                f"asset_{filename}",
                filename,
                "pass" if exists else "fail",
                "present" if exists else "missing",
                f"Keep {filename} in the deploy checkout.",
            )
        )

    checks.append(
        _check(
            "email",
            "Outbound email",
            "pass" if config.GMAIL_USER and config.GMAIL_APP_PASSWORD else "warn",
            "Gmail SMTP is configured"
            if config.GMAIL_USER and config.GMAIL_APP_PASSWORD
            else "Gmail SMTP is not configured; email sends will be manual/off",
            "Set MISE_GMAIL_USER and MISE_GMAIL_APP_PASSWORD when ready.",
        )
    )

    failures = [c for c in checks if c["status"] == "fail"]
    warnings = [c for c in checks if c["status"] == "warn"]
    return {
        "ready": not failures,
        "checks": checks,
        "failures": len(failures),
        "warnings": len(warnings),
        "passes": sum(1 for c in checks if c["status"] == "pass"),
    }


def format_text(report: dict) -> str:
    lines = ["Mise hosted SaaS preflight"]
    for check in report["checks"]:
        lines.append(f"{check['status'].upper():4}  {check['label']}: {check['detail']}")
        if check["status"] != "pass" and check.get("fix"):
            lines.append(f"      fix: {check['fix']}")
    verdict = "READY" if report["ready"] else "NOT READY"
    lines.append(
        f"{verdict}: {report['passes']} pass, {report['warnings']} warn, {report['failures']} fail"
    )
    return "\n".join(lines)


def format_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
