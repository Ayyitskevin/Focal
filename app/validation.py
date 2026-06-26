"""Validation-scoring harness — the deterministic promotion gate for a shadowed provider.

Phase 2 shadows a challenger (e.g. Qwen3-VL) against the legacy provider and records both
to ``ai_runs``. That accumulates *comparison rows* but never a *decision*. This module is
the decision layer the audit (§9.5) requires before any cutover:

    the model proposes  ->  a human scores  ->  deterministic code decides readiness.

A **fixed validation set** (``validation_items``) pins the subjects to evaluate on. A human
records one **quality score in [0, 1]** per (item, model) (``validation_scores``). From
those scores plus the ``ai_runs`` cost/latency ledger, :func:`build_report` computes a
deterministic verdict: is the challenger at parity-or-better on enough paired evidence?

Nothing here promotes anything or writes business state. ``build_report`` is pure (no I/O)
so the gate logic is unit-testable; :func:`promotion_report` is the thin DB wrapper. The
verdict is advisory — flipping a provider over to the challenger stays a deliberate human
action. See ADR 0010.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config, db

SCORE_MIN = 0.0
SCORE_MAX = 1.0


class ScoreError(ValueError):
    """A score outside the allowed [0, 1] range, or otherwise malformed."""


@dataclass(frozen=True)
class ModelStats:
    """Per-model rollup over the validation set. ``mean_score`` is the human-quality
    signal; latency/cost come from the ai_runs ledger and are informational (audit §9.5)."""

    model: str
    provider: str | None
    scored: int  # validation items with a human score for this model
    mean_score: float | None
    avg_latency_ms: float | None
    avg_cost_usd: float | None
    runs: int  # ok ai_runs rows observed for this model (latency/cost basis)


@dataclass(frozen=True)
class PromotionReport:
    """Deterministic readiness verdict for promoting ``challenger`` over ``baseline``.

    ``ready`` is True only when there is enough paired human evidence AND the challenger is
    at parity-or-better by the configured margin. ``reasons`` spells out each criterion so
    the verdict is auditable rather than a bare boolean.
    """

    capability: str
    baseline: ModelStats
    challenger: ModelStats
    total_items: int
    paired: int  # items scored for BOTH models — the only fair comparison basis
    challenger_better: int
    ties: int
    challenger_worse: int
    mean_delta: float | None  # mean(challenger - baseline) over paired items
    min_paired: int
    margin: float
    ready: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_report(
    *,
    capability: str,
    baseline_model: str,
    challenger_model: str,
    total_items: int,
    scores: list[dict],
    run_metrics: dict[str, dict],
    min_paired: int,
    margin: float,
) -> PromotionReport:
    """Pure gate computation. ``scores`` is a list of ``{item_id, model, provider, score}``
    over the capability's validation set; ``run_metrics`` maps a model to
    ``{provider, avg_latency_ms, avg_cost_usd, runs}``. No I/O — tests drive this directly.
    """
    by_model: dict[str, dict[int, float]] = {baseline_model: {}, challenger_model: {}}
    provider_of: dict[str, str | None] = {baseline_model: None, challenger_model: None}
    for s in scores:
        m = s["model"]
        if m in by_model:
            by_model[m][s["item_id"]] = s["score"]
            provider_of[m] = provider_of[m] or s.get("provider")

    def _stats(model: str) -> ModelStats:
        item_scores = by_model[model]
        rm = run_metrics.get(model, {})
        return ModelStats(
            model=model,
            provider=provider_of[model] or rm.get("provider"),
            scored=len(item_scores),
            mean_score=_mean(list(item_scores.values())),
            avg_latency_ms=rm.get("avg_latency_ms"),
            avg_cost_usd=rm.get("avg_cost_usd"),
            runs=rm.get("runs", 0),
        )

    baseline = _stats(baseline_model)
    challenger = _stats(challenger_model)

    base_scores, chal_scores = by_model[baseline_model], by_model[challenger_model]
    paired_ids = sorted(set(base_scores) & set(chal_scores))
    deltas = [chal_scores[i] - base_scores[i] for i in paired_ids]
    better = sum(1 for d in deltas if d > 0)
    worse = sum(1 for d in deltas if d < 0)
    ties = sum(1 for d in deltas if d == 0)
    mean_delta = _mean(deltas)

    paired = len(paired_ids)
    paired_ok = paired >= min_paired
    parity_ok = mean_delta is not None and mean_delta >= margin
    ready = paired_ok and parity_ok

    reasons = (
        f"paired coverage {paired}/{min_paired} {'✓' if paired_ok else '✗'}",
        (
            f"quality delta {mean_delta:+.3f} (need ≥ {margin:+.3f}) {'✓' if parity_ok else '✗'}"
            if mean_delta is not None
            else f"quality delta — no paired scores yet (need ≥ {margin:+.3f}) ✗"
        ),
        f"head-to-head on paired: challenger {better}W / {ties}T / {worse}L",
        _cost_latency_note(baseline, challenger),
    )
    return PromotionReport(
        capability=capability,
        baseline=baseline,
        challenger=challenger,
        total_items=total_items,
        paired=paired,
        challenger_better=better,
        ties=ties,
        challenger_worse=worse,
        mean_delta=mean_delta,
        min_paired=min_paired,
        margin=margin,
        ready=ready,
        reasons=reasons,
    )


def _cost_latency_note(baseline: ModelStats, challenger: ModelStats) -> str:
    """Informational only — the audit lists cost/latency as inputs to the HUMAN decision,
    not auto-gates, so they never flip ``ready`` on their own."""
    parts = []
    if baseline.avg_latency_ms is not None and challenger.avg_latency_ms is not None:
        parts.append(f"latency {challenger.avg_latency_ms:.0f} vs {baseline.avg_latency_ms:.0f} ms")
    if baseline.avg_cost_usd is not None and challenger.avg_cost_usd is not None:
        parts.append(f"cost ${challenger.avg_cost_usd:.4f} vs ${baseline.avg_cost_usd:.4f}")
    return ("informational · " + " · ".join(parts)) if parts else "cost/latency — not yet observed"


# ── curation + scoring (human-driven data entry; no business state) ──────────────


def add_item(
    capability: str,
    subject_type: str,
    subject_id: int,
    *,
    label: str | None = None,
    expected: str | None = None,
    notes: str | None = None,
) -> int:
    """Add a subject to the fixed validation set; returns the row id (or the existing id if
    the subject is already in the set for this capability). Curation, not a money/state write."""
    db.run(
        """INSERT INTO validation_items (capability, subject_type, subject_id, label, expected, notes)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(capability, subject_type, subject_id) DO NOTHING""",
        (capability, subject_type, subject_id, label, expected, notes),
    )
    row = db.one(
        "SELECT id FROM validation_items WHERE capability=? AND subject_type=? AND subject_id=?",
        (capability, subject_type, subject_id),
    )
    return row["id"]


def record_score(
    item_id: int,
    provider: str,
    model: str,
    score: float,
    *,
    ai_run_id: int | None = None,
    scored_by: str | None = None,
    notes: str | None = None,
) -> int:
    """Record (or update) a human quality score for ``model`` on validation ``item_id``.

    ``score`` must be in [0, 1]; anything else raises :class:`ScoreError` rather than
    silently storing a value the gate would mis-aggregate. Upserts on (item_id, model) so
    re-scoring overwrites instead of double-counting. Returns the score row id.
    """
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise ScoreError(f"score must be a number in [{SCORE_MIN}, {SCORE_MAX}], got {score!r}")
    if not (SCORE_MIN <= score <= SCORE_MAX):
        raise ScoreError(f"score {score} out of range [{SCORE_MIN}, {SCORE_MAX}]")
    db.run(
        """INSERT INTO validation_scores (item_id, provider, model, score, ai_run_id, scored_by, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(item_id, model) DO UPDATE SET
               provider=excluded.provider, score=excluded.score, ai_run_id=excluded.ai_run_id,
               scored_by=excluded.scored_by, notes=excluded.notes, updated_at=datetime('now')""",
        (item_id, provider, model, float(score), ai_run_id, scored_by, notes),
    )
    row = db.one("SELECT id FROM validation_scores WHERE item_id=? AND model=?", (item_id, model))
    return row["id"]


def list_items(capability: str | None = None, *, active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM validation_items WHERE 1=1"
    params: list = []
    if capability:
        sql += " AND capability=?"
        params.append(capability)
    if active_only:
        sql += " AND active=1"
    sql += " ORDER BY capability, id"
    return [dict(r) for r in db.all_(sql, tuple(params))]


def _scores_for(capability: str) -> list[dict]:
    rows = db.all_(
        """SELECT s.item_id, s.provider, s.model, s.score
           FROM validation_scores s JOIN validation_items i ON i.id = s.item_id
           WHERE i.capability=? AND i.active=1""",
        (capability,),
    )
    return [dict(r) for r in rows]


def scores_map(capability: str) -> dict[int, dict[str, float]]:
    """``{item_id: {model: score}}`` for the active set — lets the operator UI pre-fill the
    current score for each (item, model) without N queries. Read-only."""
    out: dict[int, dict[str, float]] = {}
    for s in _scores_for(capability):
        out.setdefault(s["item_id"], {})[s["model"]] = s["score"]
    return out


def deactivate_item(item_id: int) -> None:
    """Drop a subject from the fixed set (soft — ``active=0``). The gate excludes inactive
    items; its scores are kept for the record. Reversible by flipping ``active`` back."""
    db.run("UPDATE validation_items SET active=0 WHERE id=?", (item_id,))


def shadow_candidates(capability: str = "vision") -> list[dict]:
    """Galleries that have shadow runs in the ai_runs ledger but are NOT yet enrolled in the
    validation set — the natural candidates to add and score next.

    Bridges the shadow ledger to the gate: shadow mode already recorded which galleries were
    compared (correlation_id ``shadow:gallery:…``), so 'what should I score?' is
    discoverable instead of re-typed. Read-only; one click enrolls a candidate via the
    existing add-item route, after which it drops off this list.
    """
    rows = db.all_(
        """SELECT r.subject_id AS gallery_id, g.slug, g.title,
                  COUNT(*) AS runs, MAX(r.created_at) AS last_shadow
           FROM ai_runs r JOIN galleries g ON g.id = r.subject_id
           WHERE r.capability = ? AND r.subject_type = 'gallery'
                 AND r.correlation_id LIKE 'shadow:%'
                 AND NOT EXISTS (
                     SELECT 1 FROM validation_items vi
                     WHERE vi.capability = r.capability AND vi.subject_type = 'gallery'
                           AND vi.subject_id = r.subject_id AND vi.active = 1)
           GROUP BY r.subject_id, g.slug, g.title
           ORDER BY last_shadow DESC""",
        (capability,),
    )
    return [dict(r) for r in rows]


def _run_metrics(capability: str, model: str) -> dict:
    """Average ok-run latency/cost for ``model`` from the ai_runs ledger (the cost/latency
    half of the audit's evaluation; quality comes from human scores)."""
    row = db.one(
        """SELECT COUNT(*) AS n, AVG(latency_ms) AS lat, AVG(cost_usd) AS cost,
                  MAX(provider) AS provider
           FROM ai_runs WHERE capability=? AND model=? AND status='ok'""",
        (capability, model),
    )
    if not row or not row["n"]:
        return {"runs": 0, "avg_latency_ms": None, "avg_cost_usd": None, "provider": None}
    return {
        "runs": row["n"],
        "avg_latency_ms": row["lat"],
        "avg_cost_usd": row["cost"],
        "provider": row["provider"],
    }


def promotion_report(
    capability: str,
    baseline_model: str,
    challenger_model: str,
    *,
    min_paired: int | None = None,
    margin: float | None = None,
) -> PromotionReport:
    """Read the validation set, human scores, and ai_runs metrics, then compute the verdict.

    Thresholds default to ``config.VALIDATION_MIN_PAIRED`` / ``VALIDATION_PARITY_MARGIN``.
    Non-mutating — only reads.
    """
    total = db.one(
        "SELECT COUNT(*) AS n FROM validation_items WHERE capability=? AND active=1",
        (capability,),
    )
    return build_report(
        capability=capability,
        baseline_model=baseline_model,
        challenger_model=challenger_model,
        total_items=total["n"] if total else 0,
        scores=_scores_for(capability),
        run_metrics={
            baseline_model: _run_metrics(capability, baseline_model),
            challenger_model: _run_metrics(capability, challenger_model),
        },
        min_paired=config.VALIDATION_MIN_PAIRED if min_paired is None else min_paired,
        margin=config.VALIDATION_PARITY_MARGIN if margin is None else margin,
    )
