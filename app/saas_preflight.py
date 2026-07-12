"""Hosted SaaS readiness checks for launch and operator support."""

from __future__ import annotations

import json
import re
import time
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
_BACKUP_GENERATION_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9]{6}$")


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


def _safe_inventory_names(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    names: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > 160
            or item in {".", ".."}
            or "/" in item
            or "\\" in item
            or "\x00" in item
            or Path(item).name != item
        ):
            return None
        names.append(item)
    if len(names) != len(set(names)):
        return None
    return names


def _runtime_generation_evidence(
    backups_dir: Path,
    generation: str,
) -> tuple[bool, str]:
    if not _BACKUP_GENERATION_RE.fullmatch(generation):
        return False, "off-site success marker has an invalid generation name"
    generation_dir = backups_dir / generation
    manifest_path = generation_dir / "manifest.json"
    if (
        generation_dir.is_symlink()
        or not generation_dir.is_dir()
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
    ):
        return False, "committed generation or manifest is missing or unsafe"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        return False, "committed generation manifest is unreadable"
    expected_live = _safe_inventory_names(manifest.get("expected_live"))
    expected_parked = _safe_inventory_names(manifest.get("expected_parked"))
    captured_live = manifest.get("captured_live")
    captured_parked = manifest.get("captured_parked")
    if (
        manifest.get("format_version") != 1
        or manifest.get("complete") is not True
        or manifest.get("stamp") != generation
        or manifest.get("control") != "saas-control.db.gz"
        or manifest.get("failures") != []
        or expected_live is None
        or expected_parked is None
        or type(captured_live) is not int
        or type(captured_parked) is not int
        or captured_live != len(expected_live)
        or captured_parked != len(expected_parked)
    ):
        return False, "manifest inventory is incomplete or count-mismatched"
    required = [generation_dir / "saas-control.db.gz"]
    required.extend(generation_dir / "tenants" / f"{name}.db.gz" for name in expected_live)
    required.extend(generation_dir / "trash" / f"{name}.db.gz" for name in expected_parked)
    if any(path.is_symlink() or not path.is_file() for path in required):
        return False, "manifest inventory is missing one or more database payloads"
    return True, "manifest inventory and payload counts match"


def check_readiness(
    *,
    project_root: Path | None = None,
    write_probes: bool = False,
    require_runtime_evidence: bool = True,
) -> dict:
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

    if require_runtime_evidence:
        control_ok, control_detail = _writable_dir(
            Path(config.SAAS_CONTROL_DB_PATH).parent,
            create=write_probes,
            probe=write_probes,
        )
    else:
        control_ok, control_detail = True, "deferred to the data-mounted runtime gate"
    checks.append(
        _check(
            "control_db",
            "Control DB path",
            "pass" if control_ok else "fail",
            control_detail,
            "Mount a writable directory for MISE_SAAS_CONTROL_DB_PATH.",
        )
    )
    if require_runtime_evidence:
        tenant_ok, tenant_detail = _writable_dir(
            Path(config.SAAS_TENANT_DATA_DIR),
            create=write_probes,
            probe=write_probes,
        )
    else:
        tenant_ok, tenant_detail = True, "deferred to the data-mounted runtime gate"
    checks.append(
        _check(
            "tenant_data",
            "Tenant data path",
            "pass" if tenant_ok else "fail",
            tenant_detail,
            "Mount a writable MISE_SAAS_TENANT_DATA_DIR volume.",
        )
    )

    if config.SAAS_MODE:
        from .hosted_backup import (
            FAILURE_MARKER_NAME,
            OFFSITE_FAILURE_MARKER_NAME,
            OFFSITE_SUCCESS_MARKER_NAME,
        )

        backups_dir = Path(config.DATA_DIR) / "backups"
        success_marker = backups_dir / OFFSITE_SUCCESS_MARKER_NAME
        failure_marker = backups_dir / OFFSITE_FAILURE_MARKER_NAME
        tenant_failure_marker = backups_dir / FAILURE_MARKER_NAME
        static_ready = bool(config.BACKUP_RCLONE_REMOTE and config.BACKUP_RCLONE_REMOTE_ENCRYPTED)
        evidence_ready = False
        evidence_detail = ""
        age_hours: float | None = None
        generation = ""
        if static_ready and success_marker.is_file() and not success_marker.is_symlink():
            generation = success_marker.read_text().strip()
            age_hours = (time.time() - success_marker.stat().st_mtime) / 3600
            generation_ready, evidence_detail = _runtime_generation_evidence(
                backups_dir,
                generation,
            )
            evidence_ready = bool(
                0 <= age_hours <= config.BACKUP_STALE_HOURS
                and not (failure_marker.exists() or failure_marker.is_symlink())
                and not (tenant_failure_marker.exists() or tenant_failure_marker.is_symlink())
                and generation_ready
            )
        remote_ready = static_ready and (evidence_ready or not require_runtime_evidence)
        if evidence_ready and age_hours is not None:
            remote_detail = (
                f"encrypted off-site sync to {config.BACKUP_RCLONE_REMOTE} "
                f"committed complete generation {generation} {age_hours:.1f}h ago"
            )
        elif static_ready and not require_runtime_evidence:
            remote_detail = (
                "encrypted remote is configured; runtime sync proof is deferred "
                "to the post-start container gate"
            )
        elif not config.BACKUP_RCLONE_REMOTE:
            remote_detail = "MISE_BACKUP_RCLONE_REMOTE is missing"
        elif not config.BACKUP_RCLONE_REMOTE_ENCRYPTED:
            remote_detail = "encrypted-remote acknowledgement is missing"
        elif failure_marker.exists() or failure_marker.is_symlink():
            remote_detail = "latest off-site sync is pending or failed"
        elif tenant_failure_marker.exists() or tenant_failure_marker.is_symlink():
            remote_detail = "latest generation skipped one or more tenant databases"
        elif not success_marker.is_file() or success_marker.is_symlink():
            remote_detail = "successful off-site sync evidence is missing or unsafe"
        elif age_hours is None or not 0 <= age_hours <= config.BACKUP_STALE_HOURS:
            remote_detail = "successful off-site sync evidence is stale"
        elif evidence_detail:
            remote_detail = evidence_detail
        else:
            remote_detail = "successful off-site sync evidence is stale"
        checks.append(
            _check(
                "offsite_backup",
                "Encrypted off-site backup",
                "pass" if remote_ready else "fail",
                remote_detail,
                "Mount a backup-only rclone crypt config, set the remote + encrypted "
                "acknowledgement, force a pass, and restore-check one complete "
                "manifest generation before launch.",
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
