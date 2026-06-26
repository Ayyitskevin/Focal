"""Validation — the operator surface for the AI promotion gate.

Phase 2 shadows a challenger against the legacy provider into ai_runs; this page turns that
into a DECISION surface. It renders the fixed validation set, the per-model quality means
(human-scored), the ai_runs cost/latency, and the deterministic readiness verdict from
``app.validation.promotion_report`` — for vision, Argus (baseline) vs the configured
challenger model.

It also lets the operator CURATE the set and ENTER scores — the human-judgement half of the
gate (the model proposes, the human scores, deterministic code decides). The writes here
are operator data entry only: a curated subject, a quality score in [0, 1], or dropping a
case. Nothing here promotes a provider — that stays a deliberate, separate human action.
Same-origin is enforced by the global CSRF middleware; the router is admin-gated.
"""

import csv
import io
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .. import config, db, security, validation
from ..render import templates

log = logging.getLogger("mise.admin.validation")
router = APIRouter(prefix="/admin/validation", dependencies=[Depends(security.require_admin)])

# Today only VISION has a registered challenger (Qwen3-VL vs Argus). The page is built so
# more capabilities slot in here as they earn a challenger. provider labels group the
# models by family in the ai_runs ledger and the scores table.
_VISION = {
    "capability": "vision",
    "label": "Vision",
    "baseline": "argus",
    "baseline_provider": "argus",
    "challenger": config.VISION_CHALLENGER_MODEL,
    "challenger_provider": "qwen",
}


def _fmt_score(v) -> str:
    return f"{v:.3f}" if v is not None else "—"


def _report_view(spec: dict) -> dict:
    rep = validation.promotion_report(spec["capability"], spec["baseline"], spec["challenger"])
    return {
        "label": spec["label"],
        "capability": spec["capability"],
        "ready": rep.ready,
        "verdict": "Ready to promote" if rep.ready else "Not ready",
        "total_items": rep.total_items,
        "paired": rep.paired,
        "min_paired": rep.min_paired,
        "mean_delta": (f"{rep.mean_delta:+.3f}" if rep.mean_delta is not None else "—"),
        "reasons": list(rep.reasons),
        "baseline": {
            "model": rep.baseline.model,
            "scored": rep.baseline.scored,
            "mean_score": _fmt_score(rep.baseline.mean_score),
            "runs": rep.baseline.runs,
        },
        "challenger": {
            "model": rep.challenger.model,
            "scored": rep.challenger.scored,
            "mean_score": _fmt_score(rep.challenger.mean_score),
            "runs": rep.challenger.runs,
        },
    }


def _item_rows(spec: dict) -> list[dict]:
    """The active set, each row carrying its current baseline/challenger scores so the
    inline scoring form pre-fills."""
    smap = validation.scores_map(spec["capability"])
    rows = []
    for it in validation.list_items(spec["capability"]):
        scored = smap.get(it["id"], {})
        rows.append(
            {
                "id": it["id"],
                "label": it["label"] or f"{it['subject_type']} #{it['subject_id']}",
                "subject": f"{it['subject_type']} #{it['subject_id']}",
                "expected": it["expected"] or "",
                "baseline_score": scored.get(spec["baseline"]),
                "challenger_score": scored.get(spec["challenger"]),
            }
        )
    return rows


@router.get("", response_class=HTMLResponse)
async def validation_view(request: Request, msg: str = "", err: str = ""):
    items = _item_rows(_VISION)
    return templates.TemplateResponse(
        request,
        "admin/validation.html",
        {
            "report": _report_view(_VISION),
            "items": items,
            "item_count": len(items),
            "baseline_model": _VISION["baseline"],
            "challenger_model": _VISION["challenger"],
            "msg": msg,
            "err": err,
        },
    )


def _redirect(msg: str = "", err: str = "") -> RedirectResponse:
    q = []
    if msg:
        q.append(f"msg={msg}")
    if err:
        q.append(f"err={err}")
    suffix = ("?" + "&".join(q)) if q else ""
    return RedirectResponse(f"/admin/validation{suffix}", status_code=303)


@router.post("/items")
async def add_item(request: Request):
    """Curate a subject into the fixed validation set (vision)."""
    form = await request.form()
    subject_type = (form.get("subject_type") or "gallery").strip()
    try:
        subject_id = int(form.get("subject_id") or "")
    except (TypeError, ValueError):
        return _redirect(err="A numeric subject id is required.")
    label = (form.get("label") or "").strip() or None
    expected = (form.get("expected") or "").strip() or None
    validation.add_item(
        _VISION["capability"], subject_type, subject_id, label=label, expected=expected
    )
    log.info("validation item added: %s %s #%s", _VISION["capability"], subject_type, subject_id)
    return _redirect(msg="Added to the validation set.")


def _maybe_score(item_id: int, raw, *, provider: str, model: str) -> bool:
    """Record one score if a value was entered; returns True on a successful write. Raises
    ValueError (incl. validation.ScoreError) on a malformed/out-of-range value."""
    raw = (raw or "").strip()
    if raw == "":
        return False
    score = float(raw)  # ValueError -> caller surfaces it
    validation.record_score(item_id, provider, model, score)
    return True


@router.post("/items/{item_id}/scores")
async def record_scores(request: Request, item_id: int):
    """Record the human quality score for the baseline and/or challenger on one item.

    Either field may be left blank (score only one side now, the other later). An
    out-of-range or non-numeric value is rejected with a message — never silently stored.
    """
    db.get_or_404("SELECT id FROM validation_items WHERE id=?", (item_id,), detail="No such item")
    form = await request.form()
    try:
        wrote = _maybe_score(
            item_id,
            form.get("baseline_score"),
            provider=_VISION["baseline_provider"],
            model=_VISION["baseline"],
        )
        wrote = (
            _maybe_score(
                item_id,
                form.get("challenger_score"),
                provider=_VISION["challenger_provider"],
                model=_VISION["challenger"],
            )
            or wrote
        )
    except (ValueError, validation.ScoreError):
        return _redirect(err="Scores must be numbers between 0.00 and 1.00.")
    return _redirect(msg="Score saved." if wrote else "No score entered.")


@router.post("/items/{item_id}/deactivate")
async def deactivate(item_id: int):
    """Drop a subject from the fixed set (soft — its scores are kept for the record)."""
    validation.deactivate_item(item_id)
    log.info("validation item %s deactivated", item_id)
    return _redirect(msg="Removed from the validation set.")


@router.get(".csv", response_class=PlainTextResponse)
async def validation_csv():
    """Current vision validation scores as CSV — the evidence behind the gate verdict."""
    rows = db.all_(
        """SELECT i.label, i.subject_type, i.subject_id, s.provider, s.model, s.score, s.scored_by
           FROM validation_scores s JOIN validation_items i ON i.id = s.item_id
           WHERE i.capability=? AND i.active=1
           ORDER BY i.id, s.model""",
        (_VISION["capability"],),
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Item", "Subject", "Provider", "Model", "Score", "Scored by"])
    for r in rows:
        w.writerow(
            [
                r["label"] or "",
                f"{r['subject_type']} #{r['subject_id']}",
                r["provider"],
                r["model"],
                r["score"],
                r["scored_by"] or "",
            ]
        )
    return PlainTextResponse(
        buf.getvalue(),
        headers={"Content-Disposition": 'attachment; filename="kleephotography_validation.csv"'},
    )
