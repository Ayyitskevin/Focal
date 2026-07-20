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
    assert report["store_ship"] == "do-not-ship"
    text = rc_acceptance.format_text(report)
    assert "pass" in text.lower() or "PASS" in text
    assert "STORE SHIP" in text
    assert "do-not-ship" in text


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
