"""Plutus offers — pure unit tests (CI-run via the `unit` marker).

Covers the deterministic, pure offer helpers:
* ``parse_bundles`` — the offers.schema.json validator gating what gets persisted to
  ``galleries.plutus_last_bundles`` (ADR 0022 piece 1), the analog of
  ``qwen_writeback.parse_structured``;
* ``bundles_to_line_items`` — flattening persisted bundles into invoice line items that carry
  the offer sku (ADR 0022 piece 2).

No DB/network, so this runs in the CI unit step. The DB-backed persistence + invoice pre-fill
paths are exercised by tests/test_smoke_plutus.py.
"""

import pytest

from app import plutus_recommend

pytestmark = pytest.mark.unit


def test_parse_bundles_normalizes_valid_with_sku_and_line_items():
    payload = {
        "bundles": [
            {
                "sku": " WALL-HERO ",
                "label": " Wall hero ",
                "estimated_cents": 12000,
                "line_items": [{"label": " 16x24 print ", "qty": 1, "unit_cents": 12000}],
            },
            {"sku": "ALBUM", "label": "Album", "estimated_cents": 30000},
        ]
    }
    assert plutus_recommend.parse_bundles(payload) == [
        {
            "sku": "WALL-HERO",
            "label": "Wall hero",
            "estimated_cents": 12000,
            "line_items": [{"label": "16x24 print", "qty": 1, "unit_cents": 12000}],
        },
        {"sku": "ALBUM", "label": "Album", "estimated_cents": 30000},
    ]


def test_parse_bundles_keeps_sku_none_when_absent():
    # pre-PLUTUS-#1: bundles arrive in the offers shape but without a SKU yet -> persisted with
    # sku=None (dormant linkage), never rejected for the missing key.
    out = plutus_recommend.parse_bundles(
        {"bundles": [{"label": "Album", "estimated_cents": 30000}]}
    )
    assert out == [{"sku": None, "label": "Album", "estimated_cents": 30000}]


def test_parse_bundles_returns_none_on_malformed_or_empty():
    assert plutus_recommend.parse_bundles({}) is None  # no bundles key
    assert plutus_recommend.parse_bundles(None) is None  # not a dict
    assert plutus_recommend.parse_bundles({"bundles": []}) is None  # empty
    assert plutus_recommend.parse_bundles({"bundles": "nope"}) is None  # not a list
    # legacy-shaped bundles (no label / estimated_cents) -> nothing valid to store
    assert plutus_recommend.parse_bundles({"bundles": [{"id": "wall-hero"}]}) is None
    # any malformed bundle rejects the WHOLE list (all-or-nothing gate)
    assert (
        plutus_recommend.parse_bundles(
            {
                "bundles": [
                    {"label": "ok", "estimated_cents": 100},
                    {"label": "", "estimated_cents": 5},
                ]
            }
        )
        is None
    )
    # estimated_cents must be a non-negative int (bool rejected)
    assert (
        plutus_recommend.parse_bundles({"bundles": [{"label": "x", "estimated_cents": -1}]}) is None
    )
    assert (
        plutus_recommend.parse_bundles({"bundles": [{"label": "x", "estimated_cents": True}]})
        is None
    )
    # a malformed line item (qty < 1) rejects the bundle
    assert (
        plutus_recommend.parse_bundles(
            {
                "bundles": [
                    {
                        "label": "x",
                        "estimated_cents": 100,
                        "line_items": [{"label": "li", "qty": 0, "unit_cents": 5}],
                    }
                ]
            }
        )
        is None
    )


def test_bundles_to_line_items_expands_line_items_with_sku():
    bundles = [
        {
            "sku": "ALBUM",
            "label": "Album",
            "estimated_cents": 30000,
            "line_items": [
                {"label": "10x10 album", "qty": 1, "unit_cents": 25000},
                {"label": "Extra spread", "qty": 2, "unit_cents": 2500},
            ],
        }
    ]
    assert plutus_recommend.bundles_to_line_items(bundles) == [
        {"label": "10x10 album", "qty": 1, "unit_cents": 25000, "sku": "ALBUM"},
        {"label": "Extra spread", "qty": 2, "unit_cents": 2500, "sku": "ALBUM"},
    ]


def test_bundles_to_line_items_falls_back_to_bundle_total_without_line_items():
    bundles = [{"sku": "WALL", "label": "Wall piece", "estimated_cents": 12000}]
    assert plutus_recommend.bundles_to_line_items(bundles) == [
        {"label": "Wall piece", "qty": 1, "unit_cents": 12000, "sku": "WALL"}
    ]


def test_bundles_to_line_items_skips_skuless_bundles_and_empty():
    # no sku -> no linkage key -> nothing to pre-fill (inert before Plutus emits SKUs)
    assert (
        plutus_recommend.bundles_to_line_items(
            [{"sku": None, "label": "x", "estimated_cents": 100}]
        )
        == []
    )
    assert plutus_recommend.bundles_to_line_items([]) == []
    assert plutus_recommend.bundles_to_line_items(None) == []
