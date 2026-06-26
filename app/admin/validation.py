"""Validation — read-only operator view over the AI promotion gate.

Phase 2 shadows a challenger against the legacy provider into ai_runs; this page turns that
into a DECISION surface. It renders the fixed validation set, the per-model quality means
(human-scored), the ai_runs cost/latency, and the deterministic readiness verdict from
``app.validation.promotion_report`` — for vision, Argus (baseline) vs the configured
challenger model. Plus a CSV of the current scores.

Read-only: nothing here promotes a provider or writes a score (scoring data entry is a
separate, deliberate follow-up). Promotion stays a human action — this only reports whether
the audit's parity criteria (§9.5) are met.
"""

import csv
import io
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .. import config, db, security, validation
from ..render import templates

log = logging.getLogger("mise.admin.validation")
router = APIRouter(prefix="/admin/validation", dependencies=[Depends(security.require_admin)])

# Today only VISION has a registered challenger (Qwen3-VL vs Argus). The page is built so
# more capabilities slot in here as they earn a challenger.
_VISION = {
    "capability": "vision",
    "label": "Vision",
    "baseline": "argus",
    "challenger": config.VISION_CHALLENGER_MODEL,
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


@router.get("", response_class=HTMLResponse)
async def validation_view(request: Request):
    report = _report_view(_VISION)
    items = validation.list_items(_VISION["capability"])
    return templates.TemplateResponse(
        request,
        "admin/validation.html",
        {"report": report, "items": items, "item_count": len(items)},
    )


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
