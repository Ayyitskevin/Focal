"""Read-only App Store readiness auditor for Focal (iOS companion).

Inventories local evidence only — never contacts App Store Connect, production
tenants, or paid endpoints. Status vocabulary matches RC readiness:

- pass / fail / blocked / not_applicable

Unknown or missing evidence is **blocked** (never pass). Owner policy decisions
(#179/#180) and incomplete reviewer replacement (#185) keep App Store ship as
do-not-ship; this auditor does not invent privacy labels or IAP strategy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

STATUSES = ("pass", "fail", "blocked", "not_applicable")

# Required-reason API reason codes we understand how to inventory.
KNOWN_REASONS = {
    "CA92.1": "UserDefaults",  # app-scoped defaults
    "C617.1": "FileTimestamp",  # file timestamps
    "0A2A.1": "DiskSpace",
    "E174.1": "FileTimestamp",
    "35F9.1": "SystemBootTime",
    "8FFB.1": "SystemBootTime",
    "3B52.1": "DiskSpace",
}

# Heuristic patterns for required-reason APIs (Swift).
FILE_TIMESTAMP_PATTERNS = (
    r"\bcreationDate\b",
    r"\bmodificationDate\b",
    r"\bfileModificationDate\b",
    r"\bcontentModificationDateKey\b",
    r"\bcreationDateKey\b",
    r"URLResourceKey\.contentModificationDateKey",
    r"URLResourceKey\.creationDateKey",
    r"\bgetattrlist\b",
    r"\bfstat\b",
    r"\blstat\b",
)
USERDEFAULTS_PATTERNS = (r"\bUserDefaults\b",)

# Permission usage description keys we can cross-check.
PERMISSION_KEYS = (
    "NSCameraUsageDescription",
    "NSPhotoLibraryUsageDescription",
    "NSPhotoLibraryAddUsageDescription",
    "NSLocationWhenInUseUsageDescription",
    "NSLocationAlwaysAndWhenInUseUsageDescription",
    "NSMicrophoneUsageDescription",
    "NSFaceIDUsageDescription",
    "NSContactsUsageDescription",
    "NSBluetoothAlwaysUsageDescription",
)

# Code patterns suggesting a permission is actually needed.
PERMISSION_CODE_HINTS: dict[str, tuple[str, ...]] = {
    "NSCameraUsageDescription": (r"\bAVCapture\b", r"\bUIImagePickerController\b", r"\.camera\b"),
    "NSPhotoLibraryUsageDescription": (
        r"\bPHPhotoLibrary\b",
        r"\bPHPicker\b",
        r"\bUIImagePickerController\b",
    ),
    "NSLocationWhenInUseUsageDescription": (r"\bCLLocationManager\b", r"\bCoreLocation\b"),
    "NSFaceIDUsageDescription": (
        r"\bLAContext\b",
        r"\bLocalAuthentication\b",
        r"\bbiometryType\b",
        r"\bevaluatePolicy\b",
    ),
    "NSMicrophoneUsageDescription": (r"\bAVAudioRecorder\b", r"\bAVCaptureDevice\.default\(\.mic"),
}

STOREKIT_PATTERNS = (
    r"\bimport StoreKit\b",
    r"\bSKPayment\b",
    r"\bSKProduct\b",
    r"\bProduct\.Subscription\b",
    r"\bStoreKit2\b",
)

PURCHASE_CTA_PATTERNS = (
    (r"Start a studio", "authentication signup CTA"),
    (r"Manage billing", "manage billing CTA"),
    (r"appending\(path:\s*\"pricing\"\)", "pricing path builder"),
    (r"manageBillingURL", "manage billing URL field"),
    (r"signupURL", "signup URL field"),
)


def _check(
    key: str,
    label: str,
    status: str,
    detail: str,
    *,
    sources: list[str] | None = None,
    fix: str = "",
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"invalid status {status!r}")
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "sources": sources or [],
        "fix": fix,
    }


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _iter_swift_files(root: Path) -> list[Path]:
    ios = root / "ios"
    if not ios.is_dir():
        return []
    return sorted(p for p in ios.rglob("*.swift") if ".build" not in p.parts)


def _concat_sources(paths: list[Path], *, limit: int = 200) -> tuple[str, list[str]]:
    chunks: list[str] = []
    used: list[str] = []
    for p in paths[:limit]:
        text = _read_text(p)
        if text is None:
            continue
        chunks.append(text)
        used.append(str(p))
    return "\n".join(chunks), used


def parse_privacy_manifest(text: str) -> dict[str, Any]:
    """Best-effort parse of PrivacyInfo.xcprivacy (plist XML as text)."""
    collected = re.findall(
        r"<key>NSPrivacyCollectedDataType</key>\s*<string>([^<]+)</string>",
        text,
    )
    api_types = re.findall(
        r"<key>NSPrivacyAccessedAPIType</key>\s*<string>([^<]+)</string>",
        text,
    )
    reasons = re.findall(
        r"<string>([A-Z0-9]{4}\.\d)</string>",
        text,
    )
    tracking = (
        "true"
        in (
            re.search(
                r"<key>NSPrivacyTracking</key>\s*<(true|false)/>",
                text,
            )
            or [None, "false"]
        )[1]
    )
    return {
        "collected_types": collected,
        "api_types": api_types,
        "reasons": reasons,
        "tracking": tracking,
    }


def scan_required_reason_usage(swift_blob: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {"UserDefaults": [], "FileTimestamp": []}
    for pat in USERDEFAULTS_PATTERNS:
        if re.search(pat, swift_blob):
            hits["UserDefaults"].append(pat)
    for pat in FILE_TIMESTAMP_PATTERNS:
        if re.search(pat, swift_blob):
            hits["FileTimestamp"].append(pat)
    return hits


def check_privacy_manifest(root: Path) -> dict[str, Any]:
    path = root / "ios" / "Mise" / "PrivacyInfo.xcprivacy"
    text = _read_text(path)
    if text is None:
        return _check(
            "privacy_manifest",
            "PrivacyInfo.xcprivacy present",
            "fail",
            "missing ios/Mise/PrivacyInfo.xcprivacy",
            sources=[],
            fix="Restore PrivacyInfo.xcprivacy from main.",
        )
    parsed = parse_privacy_manifest(text)
    swift_files = _iter_swift_files(root)
    if not swift_files:
        return _check(
            "privacy_manifest",
            "PrivacyInfo.xcprivacy vs code",
            "blocked",
            "no ios/**/*.swift sources to cross-check",
            sources=[str(path)],
        )
    blob, used = _concat_sources(swift_files)
    usage = scan_required_reason_usage(blob)
    issues: list[str] = []
    # C617.1 declared but no file-timestamp API usage → fail (known #179 finding)
    if "C617.1" in parsed["reasons"] and not usage["FileTimestamp"]:
        issues.append(
            "C617.1 (FileTimestamp) declared but no file-timestamp API usage found in Swift sources"
        )
    # UserDefaults used but CA92.1 missing → fail
    if usage["UserDefaults"] and "CA92.1" not in parsed["reasons"]:
        issues.append("UserDefaults used in code but CA92.1 not declared")
    # File timestamp used but reason missing
    if usage["FileTimestamp"] and not any(
        r.startswith("C617") or r.startswith("E174") for r in parsed["reasons"]
    ):
        issues.append("file-timestamp APIs used but no timestamp reason declared")

    detail_parts = [
        f"collected={len(parsed['collected_types'])}",
        f"reasons={parsed['reasons']}",
        f"UserDefaults_hits={bool(usage['UserDefaults'])}",
        f"FileTimestamp_hits={bool(usage['FileTimestamp'])}",
    ]
    if issues:
        return _check(
            "privacy_manifest",
            "Privacy manifest vs required-reason APIs",
            "fail",
            "; ".join(issues) + " | " + ", ".join(detail_parts),
            sources=[str(path)] + used[:5],
            fix="Reconcile PrivacyInfo.xcprivacy with actual API use (#179).",
        )
    return _check(
        "privacy_manifest",
        "Privacy manifest vs required-reason APIs",
        "pass",
        ", ".join(detail_parts),
        sources=[str(path)] + used[:5],
    )


def check_privacy_label_inventory(root: Path) -> dict[str, Any]:
    """Code-cited data categories — does not invent Connect answers."""
    sources: list[str] = []
    evidence: list[str] = []
    paths = [
        root / "ios" / "Mise" / "PrivacyInfo.xcprivacy",
        root / "docs" / "APP-STORE-SUBMISSION.md",
        root / "docs" / "APP-STORE-PRIVACY-AND-IAP-DECISIONS.md",
        root / "app" / "mobile_auth.py",
    ]
    for p in paths:
        t = _read_text(p)
        if t is None:
            continue
        sources.append(str(p))
        if "EmailAddress" in t or "email" in t.lower():
            evidence.append(f"email refs in {p.name}")
        if "DeviceID" in t or "installation_id" in t:
            evidence.append(f"device/install id refs in {p.name}")
        if "ProductInteraction" in t:
            evidence.append(f"ProductInteraction mentioned in {p.name}")
    # Prefer blocked when decision memo still lists open Kevin choices
    memo = _read_text(root / "docs" / "APP-STORE-PRIVACY-AND-IAP-DECISIONS.md") or ""
    if (
        "Kevin decision checklist" in memo
        or "No policy was chosen" in memo
        or "do not invent" in memo.lower()
    ):
        return _check(
            "privacy_labels",
            "Privacy label inventory (code-cited)",
            "blocked",
            "evidence inventoried but #179 owner decisions remain open; not asserting Connect answers",
            sources=sources,
            fix="Complete docs/APP-STORE-PRIVACY-AND-IAP-DECISIONS.md checklist with Kevin.",
        )
    if not evidence:
        return _check(
            "privacy_labels",
            "Privacy label inventory (code-cited)",
            "blocked",
            "insufficient local evidence for privacy-label inventory",
            sources=sources,
        )
    return _check(
        "privacy_labels",
        "Privacy label inventory (code-cited)",
        "pass",
        "; ".join(evidence[:8]),
        sources=sources,
    )


def check_info_plist_permissions(root: Path) -> dict[str, Any]:
    """Cross-check usage description keys in project.yml / Info.plist vs code."""
    candidates = [
        root / "ios" / "project.yml",
        root / "ios" / "Mise" / "Supporting" / "Info.plist",
        root / "ios" / "Mise" / "Info.plist",
    ]
    plist_blob = ""
    sources: list[str] = []
    for p in candidates:
        t = _read_text(p)
        if t:
            plist_blob += "\n" + t
            sources.append(str(p))
    if not plist_blob:
        return _check(
            "info_plist_permissions",
            "Info.plist permission strings",
            "blocked",
            "no project.yml/Info.plist found under ios/",
            sources=[],
        )
    declared = [k for k in PERMISSION_KEYS if k in plist_blob]
    swift_blob, used = _concat_sources(_iter_swift_files(root))
    sources.extend(used[:3])
    stale: list[str] = []
    missing: list[str] = []
    face_id_code = bool(
        re.search(
            r"\bLAContext\b|\bLocalAuthentication\b|\bbiometryType\b|\bevaluatePolicy\b",
            swift_blob,
        )
    )
    for key in PERMISSION_KEYS:
        hints = PERMISSION_CODE_HINTS.get(key, ())
        code_needs = any(re.search(h, swift_blob) for h in hints)
        has_decl = key in declared
        if has_decl and not code_needs and key != "NSFaceIDUsageDescription":
            stale.append(key)
        if code_needs and not has_decl:
            missing.append(key)
    if missing:
        return _check(
            "info_plist_permissions",
            "Info.plist permission strings vs code",
            "fail",
            f"code suggests permission needed but string missing: {missing}",
            sources=sources,
            fix="Add usage description or remove API use.",
        )
    # Declared FaceID without LocalAuthentication path → fail (stale permission text)
    if "NSFaceIDUsageDescription" in declared and not face_id_code:
        return _check(
            "info_plist_permissions",
            "Info.plist permission strings vs code",
            "fail",
            "NSFaceIDUsageDescription declared but no LocalAuthentication/LAContext usage found",
            sources=sources,
            fix="Wire Face ID unlock or remove NSFaceIDUsageDescription from project.yml.",
        )
    if stale:
        return _check(
            "info_plist_permissions",
            "Info.plist permission strings vs code",
            "fail",
            f"stale permission strings without code use: {stale}",
            sources=sources,
            fix="Remove unused usage description keys.",
        )
    return _check(
        "info_plist_permissions",
        "Info.plist permission strings vs code",
        "pass",
        f"declared={declared or ['(none of catalog)']}",
        sources=sources,
    )


def check_storekit_and_purchase_ctas(root: Path) -> dict[str, Any]:
    swift_files = _iter_swift_files(root)
    if not swift_files:
        return _check(
            "storekit_ctas",
            "StoreKit / web-purchase CTAs",
            "blocked",
            "no Swift sources",
            sources=[],
        )
    blob, used = _concat_sources(swift_files)
    storekit_hits = [p for p in STOREKIT_PATTERNS if re.search(p, blob)]
    ctas: list[str] = []
    for pat, label in PURCHASE_CTA_PATTERNS:
        if re.search(pat, blob):
            ctas.append(label)
    if storekit_hits:
        return _check(
            "storekit_ctas",
            "StoreKit / web-purchase CTAs",
            "fail",
            f"StoreKit symbols present: {storekit_hits}; CTAs={ctas}",
            sources=used[:8],
            fix="Unexpected StoreKit — reconcile with #180 or remove.",
        )
    if ctas:
        # Presence of web purchase CTAs is expected but #180 undecided → blocked for ship
        return _check(
            "storekit_ctas",
            "StoreKit / web-purchase CTAs",
            "blocked",
            f"no StoreKit; web purchase/manage CTAs present: {ctas} — #180 owner decision required",
            sources=used[:8],
            fix="Kevin decide storefront/IAP/free-companion (#180).",
        )
    return _check(
        "storekit_ctas",
        "StoreKit / web-purchase CTAs",
        "pass",
        "no StoreKit and no known web-purchase CTA patterns",
        sources=used[:5],
    )


def check_reviewer_seeder(root: Path) -> dict[str, Any]:
    path = root / "scripts" / "seed_demo_tenant.py"
    text = _read_text(path)
    if text is None:
        return _check(
            "reviewer_seeder",
            "Reviewer demo seeder fail-closed",
            "fail",
            "scripts/seed_demo_tenant.py missing",
            sources=[],
        )
    if "DISABLED_MESSAGE" not in text or "SystemExit" not in text:
        return _check(
            "reviewer_seeder",
            "Reviewer demo seeder fail-closed",
            "fail",
            "seeder is not a fail-closed tombstone",
            sources=[str(path)],
            fix="Restore #188/#195 containment; see issue #185.",
        )
    if "from app" in text or "import app" in text:
        return _check(
            "reviewer_seeder",
            "Reviewer demo seeder fail-closed",
            "fail",
            "seeder imports application modules (unsafe)",
            sources=[str(path)],
        )
    return _check(
        "reviewer_seeder",
        "Reviewer demo seeder fail-closed",
        "pass",
        "tombstone exits before config/DB; full App Review demo still held (#185)",
        sources=[str(path), "https://github.com/Ayyitskevin/Focal/issues/185"],
    )


def check_gallery_video_device_qa(root: Path) -> dict[str, Any]:
    viewer = root / "ios" / "Mise" / "Features" / "Shared" / "GalleryViewer.swift"
    video = root / "ios" / "Mise" / "Features" / "Shared" / "AuthenticatedRemoteVideo.swift"
    v_text = _read_text(viewer) or ""
    vid_text = _read_text(video) or ""
    sources = [str(p) for p in (viewer, video) if p.is_file()]
    if not sources:
        return _check(
            "gallery_video_qa",
            "Gallery video device QA evidence",
            "blocked",
            "video presentation sources missing",
            sources=[],
        )
    has_structure = "GalleryMediaPresentation" in v_text and (
        "VideoPlayer" in vid_text or "AVPlayer" in vid_text
    )
    # Device evidence artifact?
    evidence_paths = [
        root / "docs" / "DEVICE-QA-GALLERY-VIDEO.md",
        root / "docs" / "IOS-GALLERY-DEVICE-QA.md",
    ]
    has_device_doc = any(p.is_file() for p in evidence_paths)
    if not has_structure:
        return _check(
            "gallery_video_qa",
            "Gallery video device QA evidence",
            "fail",
            "poster/AVPlayer presentation structure not found",
            sources=sources,
        )
    if not has_device_doc:
        return _check(
            "gallery_video_qa",
            "Gallery video device QA evidence",
            "blocked",
            "code structure present; no device/simulator QA evidence artifact (Linux CI cannot run xcodebuild)",
            sources=sources,
            fix="Run Xcode gallery video QA on device/sim and file evidence under docs/ (#183 residual).",
        )
    return _check(
        "gallery_video_qa",
        "Gallery video device QA evidence",
        "pass",
        "structure + device QA doc present",
        sources=sources + [str(p) for p in evidence_paths if p.is_file()],
    )


def build_report(project_root: Path | None = None) -> dict[str, Any]:
    root = (project_root or Path.cwd()).resolve()
    checks = [
        check_privacy_manifest(root),
        check_privacy_label_inventory(root),
        check_info_plist_permissions(root),
        check_storekit_and_purchase_ctas(root),
        check_reviewer_seeder(root),
        check_gallery_video_device_qa(root),
    ]
    counts = {s: 0 for s in STATUSES}
    for c in checks:
        counts[c["status"]] += 1
    # App Store ship is never pass while owner issues open — always do-not-ship here.
    app_store_ship = "do-not-ship"
    ship_reasons = [
        "owner decisions #179 (privacy) and #180 (IAP/storefront)",
        "reviewer-demo replacement #185",
        "device gallery video QA residual (#183)",
    ]
    # Engineering-style "clean" only if no fails (blocked/n/a allowed)
    clean = counts["fail"] == 0
    return {
        "ready": clean,  # local audit ran without product defects; NOT App Store approval
        "app_store_ship": app_store_ship,
        "app_store_ship_reason": "; ".join(ship_reasons),
        "checks": checks,
        "counts": counts,
        "passes": counts["pass"],
        "failures": counts["fail"],
        "blocked": counts["blocked"],
        "not_applicable": counts["not_applicable"],
        "verdict": "AUDIT_CLEAN" if clean else "AUDIT_FAILED",
    }


def format_text(report: dict[str, Any]) -> str:
    lines = ["Focal App Store readiness auditor (read-only)", ""]
    width = max(len(s) for s in STATUSES)
    for check in report["checks"]:
        status = check["status"].upper().replace("_", "-")
        lines.append(f"{status:{width + 2}} {check['label']}: {check['detail']}")
        if check.get("sources"):
            lines.append(f"{'':{width + 2}} sources: {', '.join(check['sources'][:4])}")
        if check["status"] not in ("pass", "not_applicable") and check.get("fix"):
            lines.append(f"{'':{width + 2}} fix: {check['fix']}")
    lines.append("")
    lines.append(
        f"{report['verdict']}: {report['passes']} pass, {report['failures']} fail, "
        f"{report['blocked']} blocked, {report['not_applicable']} n/a"
    )
    lines.append(f"APP STORE SHIP: {report['app_store_ship']} — {report['app_store_ship_reason']}")
    lines.append(
        "Note: AUDIT_CLEAN means local evidence inventory ran without hard defects; "
        "it is not App Store Connect approval."
    )
    return "\n".join(lines)


def format_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
