"""Validation-scoring harness — the deterministic promotion gate (pure unit).

No DB, no network: drives ``build_report`` directly and exercises the score range guard
(which raises before any DB access). Pins the gate logic the audit (§9.5) requires:
parity is judged on PAIRED human scores, with explicit coverage + margin thresholds, and
cost/latency are informational — never an auto-gate.
"""

import pytest

from app import validation
from app.validation import ScoreError, build_report

pytestmark = pytest.mark.unit

BASE = "argus"
CHAL = "qwen3-vl:32b"


def _scores(base: dict, chal: dict) -> list[dict]:
    """base/chal: {item_id: score} -> the flat score rows build_report consumes."""
    rows = [{"item_id": i, "model": BASE, "provider": "argus", "score": s} for i, s in base.items()]
    rows += [{"item_id": i, "model": CHAL, "provider": "qwen", "score": s} for i, s in chal.items()]
    return rows


def _report(base, chal, *, total=None, run_metrics=None, min_paired=2, margin=0.0):
    return build_report(
        capability="vision",
        baseline_model=BASE,
        challenger_model=CHAL,
        total_items=total if total is not None else max(len(base), len(chal)),
        scores=_scores(base, chal),
        run_metrics=run_metrics or {},
        min_paired=min_paired,
        margin=margin,
    )


# ── readiness verdict ───────────────────────────────────────────────────────────


def test_ready_when_paired_and_challenger_at_parity_or_better():
    r = _report({1: 0.6, 2: 0.7, 3: 0.8}, {1: 0.7, 2: 0.7, 3: 0.9}, min_paired=3)
    assert r.paired == 3
    assert r.mean_delta == pytest.approx((0.1 + 0.0 + 0.1) / 3)
    assert r.challenger_better == 2 and r.ties == 1 and r.challenger_worse == 0
    assert r.ready is True


def test_not_ready_when_challenger_worse_on_average():
    r = _report({1: 0.9, 2: 0.9}, {1: 0.5, 2: 0.6}, min_paired=2)
    assert r.mean_delta < 0
    assert r.ready is False


def test_not_ready_when_insufficient_paired_coverage_even_if_better():
    # challenger is better, but only 1 paired item < min_paired=3 -> not enough evidence.
    r = _report({1: 0.5}, {1: 0.9}, min_paired=3)
    assert r.paired == 1 and r.challenger_better == 1
    assert r.ready is False
    assert any("paired coverage 1/3" in x for x in r.reasons)


def test_margin_requires_beating_baseline_by_more_than_parity():
    # parity (delta 0) passes at margin 0.0 but fails when a positive margin is required.
    at_parity = _report({1: 0.7, 2: 0.7}, {1: 0.7, 2: 0.7}, min_paired=2, margin=0.0)
    assert at_parity.ready is True
    strict = _report({1: 0.7, 2: 0.7}, {1: 0.7, 2: 0.7}, min_paired=2, margin=0.05)
    assert strict.ready is False


def test_unpaired_scores_do_not_count_toward_parity():
    # baseline scored items 1,2; challenger scored items 3,4 -> ZERO paired -> not ready.
    r = _report({1: 0.9, 2: 0.9}, {3: 0.95, 4: 0.95}, min_paired=1)
    assert r.paired == 0
    assert r.mean_delta is None
    assert r.ready is False
    assert r.baseline.scored == 2 and r.challenger.scored == 2  # absolute means still tracked


def test_empty_set_is_not_ready():
    r = _report({}, {}, min_paired=1)
    assert r.paired == 0 and r.mean_delta is None and r.ready is False


def test_scores_under_other_model_strings_are_excluded_not_bucketed():
    # A stray score from a THIRD model (typo'd / renamed / different provider) must be
    # DROPPED, never folded into baseline or challenger. If the `m in by_model` filter ever
    # loosened, stray scores would inflate paired count + mean_delta and manufacture a false
    # 'ready' — exactly what the head-to-head gate exists to prevent.
    scores = [
        {"item_id": 1, "model": BASE, "provider": "argus", "score": 0.6},
        {"item_id": 1, "model": CHAL, "provider": "qwen", "score": 0.7},
        {"item_id": 1, "model": "gpt-4o", "provider": "openai", "score": 0.99},
        {"item_id": 2, "model": CHAL, "provider": "qwen", "score": 0.5},
    ]
    r = build_report(
        capability="vision",
        baseline_model=BASE,
        challenger_model=CHAL,
        total_items=2,
        scores=scores,
        run_metrics={},
        min_paired=1,
        margin=0.0,
    )
    # only item 1 is paired between the two NAMED models; the gpt-4o row is dropped
    assert r.paired == 1
    assert r.baseline.scored == 1 and r.challenger.scored == 2
    assert r.mean_delta == pytest.approx(0.1)  # 0.7 - 0.6, the stray 0.99 ignored


# ── per-model stats + informational cost/latency ─────────────────────────────────


def test_mean_scores_and_run_metrics_surface():
    rm = {
        BASE: {"provider": "argus", "avg_latency_ms": 800.0, "avg_cost_usd": 0.01, "runs": 5},
        CHAL: {"provider": "qwen", "avg_latency_ms": 1500.0, "avg_cost_usd": 0.0, "runs": 5},
    }
    r = _report({1: 0.6, 2: 0.8}, {1: 0.7, 2: 0.9}, min_paired=2, run_metrics=rm)
    assert r.baseline.mean_score == pytest.approx(0.7)
    assert r.challenger.mean_score == pytest.approx(0.8)
    assert r.challenger.avg_latency_ms == 1500.0 and r.challenger.runs == 5
    # latency/cost appear as an informational reason, not a gate
    assert any("informational" in x for x in r.reasons)
    assert r.ready is True  # slower + free challenger still ready on quality alone


def test_cost_latency_never_flips_ready():
    # challenger is much slower and we still mark ready: cost/latency must not auto-fail.
    rm = {
        BASE: {"provider": "argus", "avg_latency_ms": 100.0, "avg_cost_usd": 0.0, "runs": 3},
        CHAL: {"provider": "qwen", "avg_latency_ms": 9000.0, "avg_cost_usd": 0.0, "runs": 3},
    }
    r = _report({1: 0.5, 2: 0.5}, {1: 0.9, 2: 0.9}, min_paired=2, run_metrics=rm)
    assert r.ready is True


# ── score range guard (raises before any DB access) ──────────────────────────────


def test_record_score_rejects_out_of_range():
    with pytest.raises(ScoreError):
        validation.record_score(1, "qwen", CHAL, 1.5)
    with pytest.raises(ScoreError):
        validation.record_score(1, "qwen", CHAL, -0.1)


def test_record_score_rejects_non_numeric_and_bool():
    with pytest.raises(ScoreError):
        validation.record_score(1, "qwen", CHAL, "great")
    with pytest.raises(ScoreError):
        validation.record_score(1, "qwen", CHAL, True)
