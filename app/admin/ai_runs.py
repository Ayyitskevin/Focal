"""AI runs — read-only operator view over the append-only ai_runs ledger.

Every AI call routed through the providers facade (caption drafting, vision shadow, …)
records one ai_runs row via ai_runs.record(). This page renders those rows newest-first,
filterable by capability, with a status badge so non-OK runs (provider errors, invalid
responses) are visible at a glance rather than buried in logs (audit §8.3, §11.5). Plus
a CSV export. Nothing here writes — it only reads the ledger.
"""

import csv
import io
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .. import db, security
from ..render import _localtime, templates

log = logging.getLogger("mise.admin.ai_runs")
router = APIRouter(prefix="/admin/ai-runs", dependencies=[Depends(security.require_admin)])

_LIMIT = 500  # newest N runs — a ledger viewer, not a full dump

_FILTERS = ["all", "vision", "offers", "content"]
_CAP_LABEL = {"vision": "Vision", "offers": "Offers", "content": "Content"}

# Status -> badge styling. Non-OK statuses use a warm/alert palette so a failed or
# rejected run stands out in the feed.
_STATUS_META = {
    "ok": {"label": "OK", "bg": "#e1f2e9", "color": "#2f7d57"},
    "disabled": {"label": "Disabled", "bg": "#eceff1", "color": "#5b6b73"},
    "provider_error": {"label": "Provider error", "bg": "#f3e3e5", "color": "#7C2F38"},
    "invalid_response": {"label": "Invalid response", "bg": "#f7ecd2", "color": "#9a7a2c"},
}


def _status_meta(status: str) -> dict:
    return _STATUS_META.get(status, {"label": status, "bg": "#eceff1", "color": "#5b6b73"})


def _subject(subject_type: str | None, subject_id) -> str:
    if not subject_type:
        return ""
    return f"{subject_type.replace('_', ' ')} #{subject_id}" if subject_id else subject_type


def _metrics(latency_ms, cost_usd, tokens) -> str:
    """Compact 'latency · cost · tokens' string, omitting fields the provider didn't report."""
    parts = []
    if latency_ms is not None:
        parts.append(f"{latency_ms} ms")
    if cost_usd is not None:
        parts.append(f"${cost_usd:,.4f}")
    if tokens is not None:
        parts.append(f"{tokens} tok")
    return " · ".join(parts)


def _rows(capability: str) -> list[dict]:
    if capability != "all":
        raw = db.all_(
            """SELECT capability, provider, status, review, model, latency_ms, cost_usd,
                      tokens, error, subject_type, subject_id, correlation_id, created_at
               FROM ai_runs WHERE capability=? ORDER BY created_at DESC, id DESC LIMIT ?""",
            (capability, _LIMIT),
        )
    else:
        raw = db.all_(
            """SELECT capability, provider, status, review, model, latency_ms, cost_usd,
                      tokens, error, subject_type, subject_id, correlation_id, created_at
               FROM ai_runs ORDER BY created_at DESC, id DESC LIMIT ?""",
            (_LIMIT,),
        )
    out = []
    for r in raw:
        meta = _status_meta(r["status"])
        out.append(
            {
                "capability": r["capability"],
                "provider": r["provider"],
                "title": f"{_CAP_LABEL.get(r['capability'], r['capability'])} · {r['provider']}",
                "status": r["status"],
                "status_label": meta["label"],
                "status_bg": meta["bg"],
                "status_color": meta["color"],
                "model": r["model"] or "",
                "review": r["review"],
                "metrics": _metrics(r["latency_ms"], r["cost_usd"], r["tokens"]),
                "subject": _subject(r["subject_type"], r["subject_id"]),
                "error": r["error"] or "",
                "correlation_id": r["correlation_id"],
                "created_at": r["created_at"],
            }
        )
    return out


def _group(rows: list[dict]) -> list[dict]:
    """Fold runs sharing a correlation_id into one comparison item (e.g. a vision shadow
    pair: legacy Argus + the challenger), preserving newest-first order. Runs with no
    correlation_id — or a lone correlated run — render as singles."""
    items: list[dict] = []
    index: dict[str, int] = {}
    for r in rows:
        cid = r.get("correlation_id")
        if cid:
            if cid in index:
                items[index[cid]]["runs"].append(r)
            else:
                index[cid] = len(items)
                items.append({"kind": "group", "correlation_id": cid, "runs": [r]})
        else:
            items.append({"kind": "single", "run": r})
    for i, it in enumerate(items):
        if it["kind"] == "group" and len(it["runs"]) == 1:
            items[i] = {"kind": "single", "run": it["runs"][0]}
    return items


def _counts() -> dict:
    raw = db.all_(
        "SELECT capability FROM ai_runs ORDER BY created_at DESC, id DESC LIMIT ?", (_LIMIT,)
    )
    counts = {"all": len(raw), "vision": 0, "offers": 0, "content": 0}
    for r in raw:
        if r["capability"] in counts:
            counts[r["capability"]] += 1
    return counts


@router.get("", response_class=HTMLResponse)
async def ai_runs_view(request: Request, cap: str = "all"):
    if cap not in _FILTERS:
        cap = "all"
    counts = _counts()
    filters = [
        {
            "key": k,
            "label": "All" if k == "all" else _CAP_LABEL[k],
            "n": counts[k],
            "active": k == cap,
        }
        for k in _FILTERS
    ]
    return templates.TemplateResponse(
        request,
        "admin/ai_runs.html",
        {"items": _group(_rows(cap)), "filters": filters, "cap": cap, "total": counts["all"]},
    )


@router.get(".csv", response_class=PlainTextResponse)
async def ai_runs_csv():
    """Full window as CSV — provenance evidence / cost ledger for an AI evaluation."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Time", "Capability", "Provider", "Status", "Model", "Metrics", "Subject", "Error"])
    for e in _rows("all"):
        w.writerow(
            [
                _localtime(e["created_at"]),
                e["capability"],
                e["provider"],
                e["status"],
                e["model"],
                e["metrics"],
                e["subject"],
                e["error"],
            ]
        )
    return PlainTextResponse(
        buf.getvalue(),
        headers={"Content-Disposition": 'attachment; filename="kleephotography_ai_runs.csv"'},
    )
