"""Client-facing licence summary — PURE shaping units (CI-run). _friendly_license turns a stored
licence row into the client view: JSON channel/territory lists humanized, tier labeled, term
rendered, and the fee never present. No DB, no routes."""

import pytest

from app.public.portal import _friendly_license

pytestmark = pytest.mark.unit


def _row(**over):
    base = {
        "id": 1,
        "title": "Spring menu — social",
        "scope": "All plated dishes",
        "usage_tier": "extended",
        "exclusivity": "non_exclusive",
        "territory": '["US", "north_america"]',
        "channels": '["organic_social", "paid_social"]',
        "starts_on": "2026-01-01",
        "ends_on": "2026-12-31",
        "perpetual": 0,
        "fee_cents": 120000,
    }
    base.update(over)
    return base


def test_friendly_license_humanizes_and_omits_fee():
    out = _friendly_license(_row())
    assert out["title"] == "Spring menu — social"
    assert out["tier"] == "Extended"
    assert out["exclusive"] is False
    assert out["channels"] == ["Organic Social", "Paid Social"]
    assert out["territory"] == ["Us", "North America"]
    assert out["term"] == "2026-01-01 → 2026-12-31"
    assert "fee" not in out and "fee_cents" not in out  # client never sees the fee


def test_friendly_license_exclusive_flag_and_tier_label():
    out = _friendly_license(_row(exclusivity="exclusive", usage_tier="unpublished_commercial"))
    assert out["exclusive"] is True
    assert out["tier"] == "Unpublished / commercial"


def test_friendly_license_perpetual_term():
    assert _friendly_license(_row(perpetual=1, ends_on=None))["term"] == "Perpetual"


def test_friendly_license_open_ended_and_undated_terms():
    assert (
        _friendly_license(_row(starts_on=None, ends_on="2027-06-30"))["term"]
        == "through 2027-06-30"
    )
    assert _friendly_license(_row(starts_on=None, ends_on=None, perpetual=0))["term"] == "—"


def test_friendly_license_tolerates_empty_or_bad_json():
    out = _friendly_license(_row(territory="[]", channels=None))
    assert out["channels"] == [] and out["territory"] == []
