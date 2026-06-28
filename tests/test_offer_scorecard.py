"""Offer scorecard — pure unit tests for the SKU attribution sum (CI-run via `unit`).

Covers ``offer_scorecard._sum_attributed`` (ADR 0022 piece 3): summing the value of
offer-SKU-tagged invoice lines, counting ONLY lines whose sku Plutus actually offered — never
the base fee on the same invoice and never a stray/manual sku. The DB wrapper
(``_attributed_upsell``) + the rendered tile are exercised by tests/test_smoke_offer_scorecard.py.
"""

import pytest

from app.admin.offer_scorecard import _sum_attributed

pytestmark = pytest.mark.unit


def test_sums_only_offered_sku_lines_not_the_base_fee():
    offered = {"ALBUM", "WALL"}
    invoices = [
        [
            {"label": "Shoot fee", "qty": 1, "unit_cents": 90000},  # untagged -> excluded
            {"label": "10x10 album", "qty": 1, "unit_cents": 30000, "sku": "ALBUM"},
            {"label": "Wall", "qty": 2, "unit_cents": 6000, "sku": "WALL"},
        ]
    ]
    assert _sum_attributed(offered, invoices) == {
        "cents": 30000 + 2 * 6000,
        "invoices": 1,
        "skus": 2,
    }


def test_ignores_sku_that_matches_no_offer():
    agg = _sum_attributed(
        {"ALBUM"}, [[{"label": "x", "qty": 1, "unit_cents": 5000, "sku": "MYSTERY"}]]
    )
    assert agg == {"cents": 0, "invoices": 0, "skus": 0}


def test_untagged_lines_count_nothing():
    agg = _sum_attributed({"ALBUM"}, [[{"label": "Shoot", "qty": 1, "unit_cents": 90000}]])
    assert agg == {"cents": 0, "invoices": 0, "skus": 0}


def test_counts_invoices_with_at_least_one_attributed_line():
    invoices = [
        [{"label": "a", "qty": 1, "unit_cents": 10000, "sku": "ALBUM"}],
        [{"label": "b", "qty": 1, "unit_cents": 99999}],  # no tagged line -> not counted
        [{"label": "c", "qty": 3, "unit_cents": 1000, "sku": "ALBUM"}],
    ]
    assert _sum_attributed({"ALBUM"}, invoices) == {
        "cents": 10000 + 3000,
        "invoices": 2,
        "skus": 1,
    }


def test_empty_offered_or_no_invoices():
    assert _sum_attributed(set(), [[{"sku": "ALBUM", "qty": 1, "unit_cents": 100}]]) == {
        "cents": 0,
        "invoices": 0,
        "skus": 0,
    }
    assert _sum_attributed({"ALBUM"}, []) == {"cents": 0, "invoices": 0, "skus": 0}
