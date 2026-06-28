"""Offer scorecard — pure unit tests for the SKU attribution sum (CI-run via `unit`).

Covers ``offer_scorecard._sum_attributed`` (ADR 0022 piece 3): summing the COLLECTED value of
offer-SKU-tagged invoice lines, counting ONLY lines whose sku Plutus actually offered — never
the base fee on the same invoice and never a stray/manual sku — pro-rated by the fraction
collected (1.0 for fully-paid, <1 for a deposit). The DB wrapper (``_attributed_upsell``) + the
rendered tile are exercised by tests/test_smoke_offer_scorecard.py.
"""

import pytest

from app.admin.offer_scorecard import _sum_attributed

pytestmark = pytest.mark.unit


def _paid(*invoice_item_lists):
    """Wrap fully-paid invoices (collected_fraction = 1.0) for _sum_attributed."""
    return [(items, 1.0) for items in invoice_item_lists]


def test_sums_only_offered_sku_lines_not_the_base_fee():
    offered = {"ALBUM", "WALL"}
    invoices = _paid(
        [
            {"label": "Shoot fee", "qty": 1, "unit_cents": 90000},  # untagged -> excluded
            {"label": "10x10 album", "qty": 1, "unit_cents": 30000, "sku": "ALBUM"},
            {"label": "Wall", "qty": 2, "unit_cents": 6000, "sku": "WALL"},
        ]
    )
    assert _sum_attributed(offered, invoices) == {
        "cents": 30000 + 2 * 6000,
        "invoices": 1,
        "skus": 2,
    }


def test_ignores_sku_that_matches_no_offer():
    agg = _sum_attributed(
        {"ALBUM"}, _paid([{"label": "x", "qty": 1, "unit_cents": 5000, "sku": "MYSTERY"}])
    )
    assert agg == {"cents": 0, "invoices": 0, "skus": 0}


def test_untagged_lines_count_nothing():
    agg = _sum_attributed({"ALBUM"}, _paid([{"label": "Shoot", "qty": 1, "unit_cents": 90000}]))
    assert agg == {"cents": 0, "invoices": 0, "skus": 0}


def test_counts_invoices_with_at_least_one_attributed_line():
    invoices = _paid(
        [{"label": "a", "qty": 1, "unit_cents": 10000, "sku": "ALBUM"}],
        [{"label": "b", "qty": 1, "unit_cents": 99999}],  # no tagged line -> not counted
        [{"label": "c", "qty": 3, "unit_cents": 1000, "sku": "ALBUM"}],
    )
    assert _sum_attributed({"ALBUM"}, invoices) == {
        "cents": 10000 + 3000,
        "invoices": 2,
        "skus": 1,
    }


def test_deposit_is_prorated_by_collected_fraction():
    # a 40%-collected deposit attributes 40% of the tagged line's value, rounded to cents
    invoices = [([{"label": "Album", "qty": 1, "unit_cents": 30000, "sku": "ALBUM"}], 0.4)]
    assert _sum_attributed({"ALBUM"}, invoices) == {"cents": 12000, "invoices": 1, "skus": 1}


def test_zero_fraction_attributes_nothing_and_is_not_counted():
    invoices = [([{"label": "Album", "qty": 1, "unit_cents": 30000, "sku": "ALBUM"}], 0.0)]
    assert _sum_attributed({"ALBUM"}, invoices) == {"cents": 0, "invoices": 0, "skus": 0}


def test_empty_offered_or_no_invoices():
    assert _sum_attributed(set(), _paid([{"sku": "ALBUM", "qty": 1, "unit_cents": 100}])) == {
        "cents": 0,
        "invoices": 0,
        "skus": 0,
    }
    assert _sum_attributed({"ALBUM"}, []) == {"cents": 0, "invoices": 0, "skus": 0}
