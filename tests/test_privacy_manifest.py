"""The iOS privacy manifest is valid, uses only keys Apple documents, and matches its docs (#179).

Apple's documented vocabularies below were read from developer.apple.com on 2026-11-08:
NSPrivacyCollectedDataType, NSPrivacyAccessedAPIType (and its reason codes), and the
App Store privacy-details data types. This does not replace the archive privacy report.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "ios" / "Mise" / "PrivacyInfo.xcprivacy"
SUBMISSION = ROOT / "docs" / "APP-STORE-SUBMISSION.md"

TOP_KEYS = {
    "NSPrivacyTracking",
    "NSPrivacyTrackingDomains",
    "NSPrivacyCollectedDataTypes",
    "NSPrivacyAccessedAPITypes",
}
COLLECTED_KEYS = {
    "NSPrivacyCollectedDataType",
    "NSPrivacyCollectedDataTypeLinked",
    "NSPrivacyCollectedDataTypeTracking",
    "NSPrivacyCollectedDataTypePurposes",
}
DATA_TYPES = {
    f"NSPrivacyCollectedDataType{n}"
    for n in (
        "Name EmailAddress PhoneNumber PhysicalAddress OtherUserContactInfo Health Fitness "
        "PaymentInfo CreditInfo OtherFinancialInfo PreciseLocation CoarseLocation "
        "SensitiveInfo Contacts EmailsOrTextMessages PhotosorVideos AudioData GameplayContent "
        "CustomerSupport OtherUserContent BrowsingHistory SearchHistory UserID DeviceID "
        "PurchaseHistory ProductInteraction AdvertisingData OtherUsageData CrashData "
        "PerformanceData OtherDiagnosticData EnvironmentScanning Hands Head OtherDataTypes"
    ).split()
}
PURPOSES = {
    f"NSPrivacyCollectedDataTypePurpose{n}"
    for n in (
        "ThirdPartyAdvertising DeveloperAdvertising Analytics ProductPersonalization "
        "AppFunctionality Other"
    ).split()
}
API_TYPES = {
    f"NSPrivacyAccessedAPICategory{n}"
    for n in "FileTimestamp SystemBootTime DiskSpace ActiveKeyboards UserDefaults".split()
}
# the reason codes Apple documents per category (only the ones this app could declare are pinned)
USER_DEFAULTS_REASONS = {"CA92.1", "1C8F.1", "C56D.1", "AC6B.1"}


@pytest.fixture(scope="module")
def manifest() -> dict:
    with MANIFEST.open("rb") as f:
        return plistlib.load(f)


def test_the_manifest_parses_and_uses_only_documented_keys(manifest):
    assert set(manifest) == TOP_KEYS
    assert manifest["NSPrivacyTracking"] is False
    assert manifest["NSPrivacyTrackingDomains"] == []
    for entry in manifest["NSPrivacyCollectedDataTypes"]:
        assert set(entry) == COLLECTED_KEYS
        assert entry["NSPrivacyCollectedDataType"] in DATA_TYPES
        assert isinstance(entry["NSPrivacyCollectedDataTypeLinked"], bool)
        assert entry["NSPrivacyCollectedDataTypeTracking"] is False
        assert entry["NSPrivacyCollectedDataTypePurposes"]
        assert set(entry["NSPrivacyCollectedDataTypePurposes"]) <= PURPOSES
    for entry in manifest["NSPrivacyAccessedAPITypes"]:
        assert entry["NSPrivacyAccessedAPIType"] in API_TYPES
        assert entry["NSPrivacyAccessedAPITypeReasons"]


def test_no_duplicate_collected_types(manifest):
    kinds = [e["NSPrivacyCollectedDataType"] for e in manifest["NSPrivacyCollectedDataTypes"]]
    assert len(kinds) == len(set(kinds))


def test_the_inventory_includes_retained_product_interaction_linked_for_app_functionality(manifest):
    by_kind = {e["NSPrivacyCollectedDataType"]: e for e in manifest["NSPrivacyCollectedDataTypes"]}
    entry = by_kind["NSPrivacyCollectedDataTypeProductInteraction"]
    assert entry["NSPrivacyCollectedDataTypeLinked"] is True
    assert entry["NSPrivacyCollectedDataTypeTracking"] is False
    assert entry["NSPrivacyCollectedDataTypePurposes"] == [
        "NSPrivacyCollectedDataTypePurposeAppFunctionality"
    ]


def test_every_declared_type_is_named_in_the_submission_doc(manifest):
    doc = SUBMISSION.read_text(encoding="utf-8")
    for entry in manifest["NSPrivacyCollectedDataTypes"]:
        key = entry["NSPrivacyCollectedDataType"].removeprefix("NSPrivacyCollectedDataType")
        assert f"`{key}`" in doc, (
            f"{key} is in the manifest but not in docs/APP-STORE-SUBMISSION.md"
        )


def test_the_file_timestamp_reason_is_declared_only_with_a_real_covered_call(manifest):
    declared = any(
        e["NSPrivacyAccessedAPIType"] == "NSPrivacyAccessedAPICategoryFileTimestamp"
        for e in manifest["NSPrivacyAccessedAPITypes"]
    )
    covered = re.compile(
        r"\b(creationDate|modificationDate|fileModificationDate|contentModificationDateKey|"
        r"creationDateKey|attributesOfItem|attributesOfFileSystem|fstat|lstat|stat)\b"
    )
    swift = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in (ROOT / "ios" / "Mise").rglob("*.swift")
    )
    used = bool(covered.search(swift))
    assert declared == used, f"FileTimestamp declared={declared} but covered-API use={used}"


def test_user_defaults_is_declared_with_a_documented_reason_when_used(manifest):
    swift = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in (ROOT / "ios" / "Mise").rglob("*.swift")
    )
    assert "UserDefaults" in swift  # positive control: the scan reaches the sources
    entries = [
        e
        for e in manifest["NSPrivacyAccessedAPITypes"]
        if e["NSPrivacyAccessedAPIType"] == "NSPrivacyAccessedAPICategoryUserDefaults"
    ]
    assert len(entries) == 1
    assert set(entries[0]["NSPrivacyAccessedAPITypeReasons"]) <= USER_DEFAULTS_REASONS


def test_the_manifest_is_inside_the_app_target_sources():
    """xcodegen bundles ``sources: path: Mise`` (so PrivacyInfo.xcprivacy ships in the app)."""
    project = (ROOT / "ios" / "project.yml").read_text(encoding="utf-8")
    assert re.search(r"sources:\s*\n\s*- path: Mise\s*\n", project)
    assert MANIFEST.parent.name == "Mise"
