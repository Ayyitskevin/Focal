"""Unit tests for the ai-runs view's shadow-pair grouping (pure, no DB)."""

import pytest

from app.admin.ai_runs import _group

pytestmark = pytest.mark.unit


def test_group_folds_correlated_runs_into_one_comparison():
    rows = [
        {"correlation_id": "shadow:gallery:5:42", "provider": "qwen3-vl"},
        {"correlation_id": "shadow:gallery:5:42", "provider": "argus"},
        {"correlation_id": None, "provider": "odysseus"},
    ]
    items = _group(rows)
    assert len(items) == 2
    assert items[0]["kind"] == "group" and len(items[0]["runs"]) == 2
    assert {r["provider"] for r in items[0]["runs"]} == {"qwen3-vl", "argus"}
    assert items[1]["kind"] == "single" and items[1]["run"]["provider"] == "odysseus"


def test_group_lone_correlated_run_renders_single():
    items = _group([{"correlation_id": "c1", "provider": "argus"}])
    assert len(items) == 1 and items[0]["kind"] == "single"


def test_group_uncorrelated_runs_all_single_in_order():
    rows = [{"correlation_id": None, "provider": "a"}, {"correlation_id": None, "provider": "b"}]
    items = _group(rows)
    assert [i["kind"] for i in items] == ["single", "single"]
    assert [i["run"]["provider"] for i in items] == ["a", "b"]


def test_group_preserves_newest_first_position():
    # a correlated pair interleaved with a single keeps the pair at its first (newest) slot
    rows = [
        {"correlation_id": "c1", "provider": "qwen3-vl"},
        {"correlation_id": None, "provider": "odysseus"},
        {"correlation_id": "c1", "provider": "argus"},
    ]
    items = _group(rows)
    assert items[0]["kind"] == "group" and len(items[0]["runs"]) == 2
    assert items[1]["kind"] == "single" and items[1]["run"]["provider"] == "odysseus"
