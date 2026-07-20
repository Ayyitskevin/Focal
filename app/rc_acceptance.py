"""Release-candidate acceptance readiness report (operator-facing).

Distinct from hosted SaaS env preflight (``saas_preflight``): this module
validates **product behavioral** RC gates via structural probes and an
invoked pytest acceptance suite. Status vocabulary:

- **pass** — check ran and succeeded
- **fail** — check ran and found a product defect
- **blocked** — environment cannot run the check (missing toolchain, etc.)
- **not_applicable** — check intentionally out of scope for this surface/host

Never prints secrets, tenant paths with live data, or credentials.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

STATUSES = ("pass", "fail", "blocked", "not_applicable")


def _check(
    key: str,
    label: str,
    status: str,
    detail: str,
    *,
    fix: str = "",
) -> dict[str, str]:
    if status not in STATUSES:
        raise ValueError(f"invalid status {status!r}")
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "fix": fix,
    }


def _project_root(explicit: Path | None = None) -> Path:
    return (explicit or Path(__file__).resolve().parents[1]).resolve()


def check_python_deps(root: Path) -> dict[str, str]:
    try:
        import fastapi  # noqa: F401
        import pytest  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as exc:
        return _check(
            "python_deps",
            "Python runtime dependencies",
            "fail",
            f"import failed: {exc}",
            fix="Create .venv and pip install -r requirements.txt requirements-dev.txt",
        )
    return _check(
        "python_deps",
        "Python runtime dependencies",
        "pass",
        f"fastapi/pytest importable under {sys.executable}",
    )


def check_migrations(root: Path) -> dict[str, str]:
    mig = root / "migrations"
    if not mig.is_dir():
        return _check(
            "migrations",
            "Migration tree present",
            "fail",
            f"missing {mig}",
            fix="Restore migrations/ from the Focal repository.",
        )
    files = sorted(p.name for p in mig.glob("*.sql"))
    if not files:
        return _check(
            "migrations",
            "Migration tree present",
            "fail",
            "migrations/ has no .sql files",
        )
    return _check(
        "migrations",
        "Migration tree present",
        "pass",
        f"{len(files)} SQL migrations (latest {files[-1]})",
    )


def check_seed_demo_tombstone(root: Path) -> dict[str, str]:
    script = root / "scripts" / "seed_demo_tenant.py"
    if not script.is_file():
        return _check(
            "seed_demo_tombstone",
            "Reviewer seeder fail-closed",
            "fail",
            "scripts/seed_demo_tenant.py missing",
        )
    text = script.read_text(encoding="utf-8")
    if "DISABLED_MESSAGE" not in text or "SystemExit" not in text:
        return _check(
            "seed_demo_tombstone",
            "Reviewer seeder fail-closed",
            "fail",
            "seeder is not a fail-closed tombstone",
            fix="Restore the #188/#195 containment tombstone; see issue #185.",
        )
    if "from app" in text or "import app" in text:
        return _check(
            "seed_demo_tombstone",
            "Reviewer seeder fail-closed",
            "fail",
            "seeder imports application modules (unsafe)",
        )
    return _check(
        "seed_demo_tombstone",
        "Reviewer seeder fail-closed",
        "pass",
        "seed_demo_tenant.py refuses before config/DB (issue #185 hold)",
    )


def check_invoice_preview_isolation_source(root: Path) -> dict[str, str]:
    pay = (root / "app" / "public" / "pay.py").read_text(encoding="utf-8")
    commercial = (
        root / "ios" / "Mise" / "Features" / "Commercial" / "CommercialView.swift"
    ).read_text(encoding="utf-8")
    ok_pay = "_record_client_first_view" in pay and "is_admin" in pay
    ok_ios = (
        'Link("Open invoice"' not in commercial and "destination: inv.publicURL" not in commercial
    )
    if ok_pay and ok_ios:
        return _check(
            "invoice_preview_isolation",
            "Owner invoice preview isolation (source)",
            "pass",
            "admin-session skip + native AR does not open publicURL",
        )
    return _check(
        "invoice_preview_isolation",
        "Owner invoice preview isolation (source)",
        "fail",
        "missing isolation markers in pay.py or CommercialView.swift",
        fix="Restore #184 isolation: _record_client_first_view + in-app Preview invoice.",
    )


def check_owner_draft_media_source(root: Path) -> dict[str, str]:
    media = (root / "app" / "mobile_media.py").read_text(encoding="utf-8")
    if "_delivery_eligible_for_principal" in media and "STUDIO_OWNER" in media:
        return _check(
            "owner_draft_media",
            "Owner draft media authorization (source)",
            "pass",
            "studio owners bypass publish/expiry guest delivery gate",
        )
    return _check(
        "owner_draft_media",
        "Owner draft media authorization (source)",
        "fail",
        "owner delivery eligibility helper missing",
        fix="Restore mobile_media owner draft bypass from launch-integrity.",
    )


def check_ios_toolchain() -> dict[str, str]:
    if platform.system() != "Darwin":
        return _check(
            "ios_xcode",
            "iOS Xcode / device QA",
            "not_applicable",
            f"host is {platform.system()} — xcodebuild not expected",
        )
    # Darwin without xcode still blocked
    from shutil import which

    if which("xcodebuild") is None:
        return _check(
            "ios_xcode",
            "iOS Xcode / device QA",
            "blocked",
            "Darwin host but xcodebuild not on PATH",
            fix="Install Xcode CLT and re-run native MiseTests.",
        )
    return _check(
        "ios_xcode",
        "iOS Xcode / device QA",
        "blocked",
        "xcodebuild present but RC suite does not auto-run UI tests here",
        fix="Run: xcodebuild test -scheme Mise -destination 'platform=iOS Simulator,name=iPhone 16'",
    )


def check_hosted_preflight_surface(root: Path) -> dict[str, str]:
    """Hosted env preflight is a separate operator tool — N/A unless SAAS_MODE."""
    try:
        from app import config, saas_preflight
    except Exception as exc:  # pragma: no cover - import environment
        return _check(
            "hosted_preflight",
            "Hosted SaaS env preflight",
            "blocked",
            f"cannot import saas_preflight: {exc}",
        )
    if not config.SAAS_MODE:
        return _check(
            "hosted_preflight",
            "Hosted SaaS env preflight",
            "not_applicable",
            "MISE_SAAS_MODE is false — use scripts/hosted-preflight.py on hosted hosts",
        )
    report = saas_preflight.check_readiness(project_root=root, write_probes=False)
    if report.get("ready"):
        return _check(
            "hosted_preflight",
            "Hosted SaaS env preflight",
            "pass",
            f"{report.get('passes', 0)} pass, {report.get('warnings', 0)} warn",
        )
    return _check(
        "hosted_preflight",
        "Hosted SaaS env preflight",
        "fail",
        f"{report.get('failures', 0)} fail, {report.get('warnings', 0)} warn",
        fix="Run scripts/hosted-preflight.py and resolve FAIL lines before hosted launch.",
    )


def run_pytest_suite(
    root: Path,
    *,
    args: list[str],
    key: str,
    label: str,
    timeout: int = 600,
) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    cmd = [sys.executable, "-m", "pytest", *args, "-q", "--tb=line"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _check(
            key,
            label,
            "blocked",
            f"pytest timed out after {timeout}s",
            fix="Re-run the suite directly and inspect hung tests.",
        )
    except FileNotFoundError:
        return _check(
            key,
            label,
            "blocked",
            "python/pytest not executable",
            fix="Activate the project .venv.",
        )
    tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-400:]
    detail_tail = " ".join(tail.split())[-400:]
    if proc.returncode == 0:
        return _check(key, label, "pass", f"pytest exit 0 — {detail_tail or 'ok'}")
    if proc.returncode == 5:
        # pytest: no tests collected
        return _check(
            key,
            label,
            "fail",
            "pytest collected no tests",
            fix="Ensure tests/test_rc_acceptance.py is present.",
        )
    return _check(
        key,
        label,
        "fail",
        f"pytest exit {proc.returncode} — {detail_tail}",
        fix="Run the same pytest command locally and fix failures.",
    )


def build_report(
    *,
    project_root: Path | None = None,
    run_tests: bool = True,
    include_integrity: bool = True,
) -> dict[str, Any]:
    root = _project_root(project_root)
    checks: list[dict[str, str]] = [
        check_python_deps(root),
        check_migrations(root),
        check_seed_demo_tombstone(root),
        check_invoice_preview_isolation_source(root),
        check_owner_draft_media_source(root),
        check_ios_toolchain(),
        check_hosted_preflight_surface(root),
    ]
    if run_tests:
        checks.append(
            run_pytest_suite(
                root,
                args=["tests/test_rc_acceptance.py", "-m", "unit"],
                key="rc_acceptance_tests",
                label="RC owner→client acceptance suite",
            )
        )
        if include_integrity:
            checks.append(
                run_pytest_suite(
                    root,
                    args=[
                        "tests/test_tenant_storage_integrity.py",
                        "tests/test_seed_demo_tenant.py",
                        "tests/test_mobile_gallery_calendar_api.py",
                        "-m",
                        "unit",
                    ],
                    key="integrity_regressions",
                    label="Storage / seeder / gallery paging regressions",
                    timeout=900,
                )
            )
    else:
        checks.append(
            _check(
                "rc_acceptance_tests",
                "RC owner→client acceptance suite",
                "not_applicable",
                "skipped (--no-tests)",
            )
        )

    counts = {s: 0 for s in STATUSES}
    for c in checks:
        counts[c["status"]] += 1

    # Product readiness for merge/RC evidence: no fail; blocked items are env limits.
    product_ready = counts["fail"] == 0
    # Store ship still requires owner decisions — never claimed here.
    return {
        "ready": product_ready,
        "checks": checks,
        "counts": counts,
        "passes": counts["pass"],
        "failures": counts["fail"],
        "blocked": counts["blocked"],
        "not_applicable": counts["not_applicable"],
        "verdict": "READY" if product_ready else "NOT READY",
        "store_ship": "do-not-ship",
        "store_ship_reason": (
            "App Store / production blocked on owner decisions #179 #180 and "
            "reviewer-demo replacement #185 — see docs/RC-ACCEPTANCE.md"
        ),
    }


def format_text(report: dict[str, Any]) -> str:
    lines = ["Focal release-candidate acceptance readiness", ""]
    width = max(len(s) for s in STATUSES)
    for check in report["checks"]:
        status = check["status"].upper().replace("_", "-")
        lines.append(f"{status:{width + 2}} {check['label']}: {check['detail']}")
        if check["status"] not in ("pass", "not_applicable") and check.get("fix"):
            lines.append(f"{'':{width + 2}} fix: {check['fix']}")
    lines.append("")
    lines.append(
        f"{report['verdict']}: "
        f"{report['passes']} pass, {report['failures']} fail, "
        f"{report['blocked']} blocked, {report['not_applicable']} n/a"
    )
    lines.append(f"STORE SHIP: {report['store_ship']} — {report['store_ship_reason']}")
    return "\n".join(lines)


def format_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
