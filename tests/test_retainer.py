"""Retainer deepening — PURE logic units (CI-run): quota parsing, the advisory overage math,
and the term-roll date arithmetic. No DB, no routes — the money-adjacent computations pinned in
isolation so a regression in what an operator is shown as "owed" fails loudly (R9)."""

import datetime as dt
import json

import pytest

from app.admin.recurring import _advance_term, _overage_lines, _quota_line, parse_quota

pytestmark = pytest.mark.unit


# --- parse_quota: units + overage rate (dollars in, cents stored) -----------


def test_parse_quota_captures_unit_and_overage_rate():
    form = {
        "quota_label_0": "Hero images",
        "quota_target_0": "20",
        "quota_unit_0": "images",
        "quota_rate_0": "15.00",
        "quota_label_1": "Reels",
        "quota_target_1": "3",
        "quota_unit_1": "reels",
        "quota_rate_1": "250",
    }
    q = json.loads(parse_quota(form))
    assert q[0] == {
        "label": "Hero images",
        "target": 20,
        "unit": "images",
        "overage_rate_cents": 1500,
    }
    assert q[1] == {"label": "Reels", "target": 3, "unit": "reels", "overage_rate_cents": 25000}


def test_parse_quota_defaults_unit_and_zero_rate():
    # blank rate -> 0 (advisory line, no dollar figure); unknown unit -> 'images'
    form = {
        "quota_label_0": "Stuff",
        "quota_target_0": "5",
        "quota_unit_0": "bogus",
        "quota_rate_0": "",
    }
    q = json.loads(parse_quota(form))
    assert q[0]["unit"] == "images" and q[0]["overage_rate_cents"] == 0


def test_parse_quota_clamps_negatives_and_rejects_bad_rate():
    assert json.loads(parse_quota({"quota_label_0": "X", "quota_target_0": "-4"}))[0]["target"] == 0
    with pytest.raises(Exception):
        parse_quota({"quota_label_0": "X", "quota_target_0": "1", "quota_rate_0": "abc"})


def test_quota_line_normalizes_legacy_rows():
    # a legacy {label, target}-only row gets the new keys with safe defaults
    assert _quota_line({"label": "Hero", "target": 10}) == {
        "label": "Hero",
        "target": 10,
        "unit": "images",
        "overage_rate_cents": 0,
    }


# --- _overage_lines: the advisory money math --------------------------------


def _q(label, target, rate_cents=0, unit="images"):
    return {"label": label, "target": target, "unit": unit, "overage_rate_cents": rate_cents}


def test_overage_amount_only_when_over_and_rate_set():
    quota = [_q("Hero", 20, 1500), _q("Reels", 3, 25000)]
    delivered = {"Hero": 22, "Reels": 3}  # 2 over on Hero; Reels exactly met
    out = _overage_lines(quota, delivered)
    hero = next(line for line in out["lines"] if line["label"] == "Hero")
    reels = next(line for line in out["lines"] if line["label"] == "Reels")
    assert hero["over"] == 2 and hero["amount_cents"] == 3000  # 2 * $15
    assert reels["over"] == 0 and reels["amount_cents"] == 0
    assert out["total_cents"] == 3000


def test_overage_with_no_rate_counts_units_but_no_dollars():
    out = _overage_lines([_q("Hero", 20, 0)], {"Hero": 25})
    line = out["lines"][0]
    assert line["over"] == 5 and line["amount_cents"] == 0
    assert out["total_cents"] == 0  # over, but no rate -> advisory only


def test_overage_extra_label_flagged_with_near_match_suggestion():
    # a typo'd label is un-targeted; it must surface (not silently dodge billing) with a hint
    out = _overage_lines([_q("Hero images", 20, 1500)], {"Hero image": 22})
    assert out["lines"][0]["over"] == 0  # nothing matched the real quota line
    assert len(out["extra"]) == 1
    assert out["extra"][0]["label"] == "Hero image"
    assert out["extra"][0]["suggestion"] == "Hero images"


def test_overage_unrelated_extra_has_no_suggestion():
    out = _overage_lines([_q("Hero images", 20)], {"Behind the scenes": 4})
    assert out["extra"][0]["suggestion"] is None


# --- _advance_term: the Renew date roll -------------------------------------


def test_advance_term_preserves_length_when_both_dates_known():
    # a 1-year term rolls to the next year, same length
    start, end = _advance_term("2026-01-01", "2027-01-01")
    assert start == "2027-01-01" and end == "2028-01-01"


def test_advance_term_quarterly_length_preserved():
    start, end = _advance_term("2026-01-01", "2026-04-01")  # 90-day term
    assert start == "2026-04-01"
    assert dt.date.fromisoformat(end) == dt.date(2026, 4, 1) + (
        dt.date(2026, 4, 1) - dt.date(2026, 1, 1)
    )


def test_advance_term_rolls_year_when_no_start():
    _start, end = _advance_term(None, "2026-06-30")
    assert end == "2027-06-30"
