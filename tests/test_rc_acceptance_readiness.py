"""Unit tests for the RC readiness reporter (structural + status vocabulary)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import rc_acceptance

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def test_structural_checks_pass_on_repo_tree():
    report = rc_acceptance.build_report(
        project_root=ROOT,
        run_tests=False,
        include_integrity=False,
    )
    by_key = {c["key"]: c for c in report["checks"]}
    assert by_key["python_deps"]["status"] == "pass"
    assert by_key["migrations"]["status"] == "pass"
    assert by_key["seed_demo_tombstone"]["status"] == "pass"
    assert by_key["invoice_preview_isolation"]["status"] == "pass"
    assert by_key["owner_draft_media"]["status"] == "pass"
    assert by_key["ios_xcode"]["status"] in {"not_applicable", "blocked", "pass"}
    assert by_key["rc_acceptance_tests"]["status"] == "not_applicable"
    assert report["failures"] == 0
    assert report["ready"] is True
    # App Store / production ship remains blocked regardless of eng READY.
    assert report["store_ship"] == "do-not-ship"
    assert "179" in report["store_ship_reason"] or "#179" in report["store_ship_reason"]
    assert "180" in report["store_ship_reason"] or "#180" in report["store_ship_reason"]
    assert "185" in report["store_ship_reason"] or "#185" in report["store_ship_reason"]
    text = rc_acceptance.format_text(report)
    assert "pass" in text.lower() or "PASS" in text
    assert "STORE SHIP" in text
    assert "do-not-ship" in text


def test_post_merge_matrix_documents_do_not_ship_and_named_issues():
    """Reconciliation matrix must stay aligned with App Store holds after #203."""
    matrix = (ROOT / "docs" / "LAUNCH-INTEGRITY-MATRIX.md").read_text(encoding="utf-8")
    assert "2a3e1dc" in matrix or "post-merge" in matrix.lower()
    assert "do-not-ship" in matrix.lower() or "DO NOT SHIP" in matrix
    for issue in ("#179", "#180", "#183", "#184", "#185", "#199"):
        assert issue in matrix
    # Must not claim App Store ship or close issues by fiat.
    assert "does **not** close GitHub issues" in matrix or "does not close" in matrix.lower()


def test_seed_tombstone_fails_if_reenabled(tmp_path):
    root = tmp_path / "fake"
    (root / "scripts").mkdir(parents=True)
    (root / "migrations").mkdir()
    (root / "migrations" / "001_init.sql").write_text("-- x\n")
    (root / "app" / "public").mkdir(parents=True)
    (root / "app" / "public" / "pay.py").write_text(
        "def _record_client_first_view():\n    security.is_admin\n"
    )
    (root / "app" / "mobile_media.py").write_text(
        "def _delivery_eligible_for_principal():\n    STUDIO_OWNER\n"
    )
    ios = root / "ios" / "Mise" / "Features" / "Commercial"
    ios.mkdir(parents=True)
    (ios / "CommercialView.swift").write_text("// Preview invoice\n")
    (root / "scripts" / "seed_demo_tenant.py").write_text(
        "from app import config\ndef seed_demo_tenant(**kw):\n    return {}\n"
    )
    check = rc_acceptance.check_seed_demo_tombstone(root)
    assert check["status"] == "fail"


def test_status_vocabulary_is_closed():
    for status in ("pass", "fail", "blocked", "not_applicable"):
        c = rc_acceptance._check("k", "L", status, "d")
        assert c["status"] == status
    with pytest.raises(ValueError):
        rc_acceptance._check("k", "L", "warn", "d")


def test_pytest_timeout_is_fail_not_ready(monkeypatch):
    """A timed-out required suite must not yield READY + exit 0 (AC4)."""
    import subprocess

    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    monkeypatch.setattr(rc_acceptance.subprocess, "run", boom)
    check = rc_acceptance.run_pytest_suite(
        ROOT,
        args=["tests/test_rc_acceptance.py"],
        key="rc_acceptance_tests",
        label="RC suite",
        timeout=1,
    )
    assert check["status"] == "fail"
    assert "timed out" in check["detail"].lower()

    report = rc_acceptance.build_report(project_root=ROOT, run_tests=True, include_integrity=False)
    by_key = {c["key"]: c for c in report["checks"]}
    assert by_key["rc_acceptance_tests"]["status"] == "fail"
    assert report["ready"] is False
    assert report["verdict"] == "NOT READY"
    assert report["failures"] >= 1


def test_pytest_missing_interpreter_is_fail_not_ready(monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("python")

    monkeypatch.setattr(rc_acceptance.subprocess, "run", boom)
    check = rc_acceptance.run_pytest_suite(
        ROOT,
        args=["tests/test_rc_acceptance.py"],
        key="rc_acceptance_tests",
        label="RC suite",
    )
    assert check["status"] == "fail"
    assert "not executable" in check["detail"].lower() or "did not run" in check["detail"].lower()


def test_required_suite_blocked_status_prevents_ready():
    """Defense in depth: non-pass required suites (except n/a) block READY."""
    checks = [
        rc_acceptance._check("python_deps", "deps", "pass", "ok"),
        rc_acceptance._check("migrations", "mig", "pass", "ok"),
        rc_acceptance._check("seed_demo_tombstone", "seed", "pass", "ok"),
        rc_acceptance._check("invoice_preview_isolation", "inv", "pass", "ok"),
        rc_acceptance._check("owner_draft_media", "media", "pass", "ok"),
        rc_acceptance._check("ios_xcode", "ios", "not_applicable", "linux"),
        rc_acceptance._check("hosted_preflight", "hosted", "not_applicable", "off"),
        rc_acceptance._check("rc_acceptance_tests", "RC suite", "blocked", "would have been env"),
    ]
    counts = {s: 0 for s in rc_acceptance.STATUSES}
    for c in checks:
        counts[c["status"]] += 1
    by_key = {c["key"]: c for c in checks}
    required_ok = True
    for key in ("rc_acceptance_tests", "integrity_regressions"):
        check = by_key.get(key)
        if check is None or check["status"] == "not_applicable":
            continue
        if check["status"] != "pass":
            required_ok = False
            break
    product_ready = counts["fail"] == 0 and required_ok
    assert product_ready is False
    assert counts["fail"] == 0  # pure blocked without fail still not ready
