"""Fixture-driven tests for the App Store readiness auditor.

Drives real `app.app_store_readiness` functions with temp trees — no App Store
Connect, no production, no reimplementation of scanners in assertions beyond
calling the shipped APIs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import app_store_readiness as asr

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _minimal_ios_tree(
    root: Path,
    *,
    privacy: str | None = None,
    project_yml: str | None = None,
    swift_extra: str = "",
    seeder: str | None = None,
) -> Path:
    if privacy is None:
        privacy = """<?xml version="1.0"?>
<plist><dict>
<key>NSPrivacyTracking</key><false/>
<key>NSPrivacyCollectedDataTypes</key><array/>
<key>NSPrivacyAccessedAPITypes</key><array>
<dict>
<key>NSPrivacyAccessedAPIType</key>
<string>NSPrivacyAccessedAPICategoryUserDefaults</string>
<key>NSPrivacyAccessedAPITypeReasons</key>
<array><string>CA92.1</string></array>
</dict>
</array>
</dict></plist>
"""
    if project_yml is None:
        project_yml = "name: Mise\n"
    _write(root / "ios/Mise/PrivacyInfo.xcprivacy", privacy)
    _write(root / "ios/project.yml", project_yml)
    _write(
        root / "ios/Mise/Core/Dummy.swift",
        "import Foundation\nlet _ = UserDefaults.standard\n" + swift_extra,
    )
    if seeder is None:
        seeder = (
            'DISABLED_MESSAGE = "disabled"\n'
            "def seed_demo_tenant(**kw):\n"
            "    raise SystemExit(DISABLED_MESSAGE)\n"
        )
    _write(root / "scripts/seed_demo_tenant.py", seeder)
    _write(
        root / "ios/Mise/Features/Shared/GalleryViewer.swift",
        "enum GalleryMediaPresentation {}\n",
    )
    _write(
        root / "ios/Mise/Features/Shared/AuthenticatedRemoteVideo.swift",
        "import AVKit\nstruct X { let p = AVPlayer.self }\n",
    )
    return root


def test_live_repo_auditor_runs_and_never_ships_app_store():
    report = asr.build_report(project_root=ROOT)
    assert report["app_store_ship"] == "do-not-ship"
    assert "179" in report["app_store_ship_reason"]
    assert "180" in report["app_store_ship_reason"]
    assert "185" in report["app_store_ship_reason"]
    keys = {c["key"] for c in report["checks"]}
    assert {
        "privacy_manifest",
        "privacy_labels",
        "info_plist_permissions",
        "storekit_ctas",
        "reviewer_seeder",
        "gallery_video_qa",
    } <= keys
    # C617.1 without file-timestamp APIs is a known fail on current main.
    by = {c["key"]: c for c in report["checks"]}
    assert by["privacy_manifest"]["status"] in {"pass", "fail"}
    assert by["reviewer_seeder"]["status"] == "pass"
    assert by["gallery_video_qa"]["status"] == "blocked"  # no device QA artifact
    assert by["storekit_ctas"]["status"] == "blocked"  # web CTAs + #180
    text = asr.format_text(report)
    assert "APP STORE SHIP" in text
    assert "do-not-ship" in text


def test_missing_manifest_fails(tmp_path):
    root = tmp_path / "app"
    _minimal_ios_tree(root)
    (root / "ios/Mise/PrivacyInfo.xcprivacy").unlink()
    c = asr.check_privacy_manifest(root)
    assert c["status"] == "fail"
    assert "missing" in c["detail"].lower()


def test_c617_without_timestamp_api_fails(tmp_path):
    privacy = """<?xml version="1.0"?>
<plist><dict>
<key>NSPrivacyAccessedAPITypes</key><array>
<dict>
<key>NSPrivacyAccessedAPIType</key>
<string>NSPrivacyAccessedAPICategoryFileTimestamp</string>
<key>NSPrivacyAccessedAPITypeReasons</key>
<array><string>C617.1</string></array>
</dict>
</array>
</dict></plist>
"""
    root = tmp_path / "app"
    _minimal_ios_tree(root, privacy=privacy)
    c = asr.check_privacy_manifest(root)
    assert c["status"] == "fail"
    assert "C617.1" in c["detail"]


def test_stale_faceid_permission_fails(tmp_path):
    root = tmp_path / "app"
    _minimal_ios_tree(
        root,
        project_yml='NSFaceIDUsageDescription: "Use Face ID"\n',
        swift_extra="// no biometry APIs\n",
    )
    c = asr.check_info_plist_permissions(root)
    assert c["status"] == "fail"
    assert "NSFaceIDUsageDescription" in c["detail"]


def test_new_purchase_cta_is_detected(tmp_path):
    root = tmp_path / "app"
    _minimal_ios_tree(
        root,
        swift_extra='let s = "Start a studio"\nlet b = "Manage billing"\n',
    )
    c = asr.check_storekit_and_purchase_ctas(root)
    assert c["status"] == "blocked"
    assert (
        "Start a studio" in c["detail"] or "signup" in c["detail"].lower() or "CTA" in c["detail"]
    )


def test_storekit_import_fails(tmp_path):
    root = tmp_path / "app"
    _minimal_ios_tree(root, swift_extra="import StoreKit\n")
    c = asr.check_storekit_and_purchase_ctas(root)
    assert c["status"] == "fail"
    assert "StoreKit" in c["detail"]


def test_enabled_seeder_fails(tmp_path):
    root = tmp_path / "app"
    _minimal_ios_tree(
        root,
        seeder="from app import config\ndef seed_demo_tenant(**kw):\n    return {}\n",
    )
    c = asr.check_reviewer_seeder(root)
    assert c["status"] == "fail"


def test_tombstone_seeder_passes(tmp_path):
    root = tmp_path / "app"
    _minimal_ios_tree(root)
    c = asr.check_reviewer_seeder(root)
    assert c["status"] == "pass"


def test_video_structure_without_device_doc_is_blocked(tmp_path):
    root = tmp_path / "app"
    _minimal_ios_tree(root)
    c = asr.check_gallery_video_device_qa(root)
    assert c["status"] == "blocked"


def test_format_json_is_deterministic_keys(tmp_path):
    root = tmp_path / "app"
    _minimal_ios_tree(root)
    r1 = asr.format_json(asr.build_report(project_root=root))
    r2 = asr.format_json(asr.build_report(project_root=root))
    assert r1 == r2
