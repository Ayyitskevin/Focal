"""Offer scorecard — does the Plutus offers capability earn its keep? (admin, read-only).

The audit (§19.4) says a consolidated capability is a **retire candidate** unless it shows
measured value; the roadmap (3.3) asks for a 30–60 day offer-acceptance/revenue scorecard.
This page is that scorecard — pure aggregation over the offer columns on `galleries` and the
existing money path (`invoices` / `payments`). It writes nothing and decides nothing; the
retire call stays a human judgement informed by these numbers.

Two halves:

* **The funnel** — proposed → approved → sent, with counts, pipeline value, and the operator
  approval/send rates, over all-time / last 60d / last 30d (windowed by when each offer was
  proposed). This is exact, from the `plutus_*` columns.
* **Revenue (attribution proxy)** — for projects whose gallery had an offer SENT, the payment
  revenue recorded on that project *after* the send date. It is aggregated at the PROJECT
  level (a project can own several galleries, so per-gallery sums would double-count a shared
  payment) and is an honest **proxy, not a causal link**: there is no offer→sale foreign key,
  so this attributes all post-send project revenue, not just incremental upsell. Labelled as
  such in the UI; the AI cost report carries the COGS side.
"""

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .. import db, security
from ..render import templates

log = logging.getLogger("mise.admin.offer_scorecard")
router = APIRouter(prefix="/admin/offers-scorecard", dependencies=[Depends(security.require_admin)])

_WINDOWS = [("All time", None), ("Last 60 days", "-60 days"), ("Last 30 days", "-30 days")]


def _dollars(cents) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def _sum_attributed(offered_skus: set, invoice_items: list) -> dict:
    """Pure: sum the COLLECTED value of offer-SKU-tagged line items.

    ``offered_skus`` is the set of SKUs Plutus actually proposed; ``invoice_items`` is one
    ``(line_items, collected_fraction)`` pair per invoice that has collected money —
    ``collected_fraction`` is paid_cents / total_cents (1.0 for a fully-paid invoice, <1 for a
    deposit). Counts only ``qty*unit_cents`` for lines whose ``sku`` is in ``offered_skus`` — the
    real upsell a human tagged, NOT the whole invoice (the base shoot fee is excluded) and not a
    stray sku that matches no offer — pro-rated by the fraction collected, so a deposit attributes
    its collected share and an unpaid line attributes nothing. Returns ``{cents, invoices, skus}``.
    No I/O — unit-testable (ADR 0022 piece 3, deposit pro-rate per the 0022 update)."""
    total, invoices_n, skus = 0, 0, set()
    for items, fraction in invoice_items:
        inv = 0
        inv_skus = set()
        for it in items or []:
            sku = it.get("sku")
            if sku and sku in offered_skus:
                inv += int(it.get("qty") or 0) * int(it.get("unit_cents") or 0)
                inv_skus.add(sku)
        collected = round(inv * fraction)
        if collected:  # an invoice converts a SKU only when it actually collected money
            total += collected
            invoices_n += 1
            skus |= inv_skus
    return {"cents": total, "invoices": invoices_n, "skus": len(skus)}


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def _funnel(window: str | None) -> dict:
    """The proposed→approved→sent funnel over 'done' offers, optionally within a window
    (by plutus_last_at). Errors are excluded — they aren't real proposals."""
    where = "WHERE plutus_last_status = 'done'"
    params: list = []
    if window:
        where += " AND plutus_last_at >= datetime('now', ?)"
        params.append(window)
    row = db.one(
        f"""SELECT COUNT(*) AS proposed,
                   COALESCE(SUM(plutus_last_estimated_cents), 0) AS proposed_cents,
                   SUM(CASE WHEN plutus_offer_decision='approved' THEN 1 ELSE 0 END) AS approved,
                   COALESCE(SUM(CASE WHEN plutus_offer_decision='approved'
                                     THEN plutus_last_estimated_cents END), 0) AS approved_cents,
                   SUM(CASE WHEN plutus_offer_decision='rejected' THEN 1 ELSE 0 END) AS rejected,
                   SUM(CASE WHEN plutus_offer_sent_at IS NOT NULL THEN 1 ELSE 0 END) AS sent
            FROM galleries {where}""",
        tuple(params),
    )
    proposed = (row["proposed"] or 0) if row else 0
    approved = (row["approved"] or 0) if row else 0
    sent = (row["sent"] or 0) if row else 0
    return {
        "proposed": proposed,
        "proposed_value": _dollars(row["proposed_cents"] if row else 0),
        "approved": approved,
        "approved_value": _dollars(row["approved_cents"] if row else 0),
        "rejected": (row["rejected"] or 0) if row else 0,
        "sent": sent,
        "approval_rate": _pct(approved, proposed),
        "send_rate": _pct(sent, approved),
    }


def _revenue_proxy() -> dict:
    """Payment revenue on projects whose gallery had an offer sent, recorded after the send.

    Aggregated at the PROJECT level (MIN send date per project) so a payment on a project
    with several offered galleries is counted once, not once per gallery. Galleries without a
    project can't be attributed and are excluded from this half (but counted in sent_total)."""
    row = db.one(
        """WITH offered AS (
               SELECT project_id, MIN(plutus_offer_sent_at) AS first_sent
               FROM galleries
               WHERE plutus_offer_sent_at IS NOT NULL AND project_id IS NOT NULL
               GROUP BY project_id
           )
           SELECT COUNT(DISTINCT o.project_id) AS projects,
                  COUNT(DISTINCT CASE WHEN p.id IS NOT NULL THEN o.project_id END) AS converted,
                  COALESCE(SUM(p.amount_cents), 0) AS revenue_cents
           FROM offered o
           LEFT JOIN invoices i ON i.project_id = o.project_id
           LEFT JOIN payments p ON p.invoice_id = i.id AND p.created_at >= o.first_sent"""
    )
    sent_total = db.one(
        "SELECT COUNT(*) AS n FROM galleries WHERE plutus_offer_sent_at IS NOT NULL"
    )["n"]
    projects = (row["projects"] or 0) if row else 0
    converted = (row["converted"] or 0) if row else 0
    return {
        "sent_total": sent_total or 0,
        "projects": projects,
        "converted": converted,
        "revenue": _dollars(row["revenue_cents"] if row else 0),
        "conversion_rate": _pct(converted, projects),
    }


def _attributed_upsell() -> dict:
    """Real attributed upsell (ADR 0022 piece 3): the COLLECTED value of offer-SKU-tagged invoice
    lines, matched against the SKUs Plutus actually offered. Unlike the proxy, this is a causal
    link — it counts only the tagged lines a human added (via the offer pre-fill), not all
    post-send project revenue.

    Counts fully-paid invoices in full and **deposit-paid invoices pro-rated** by the fraction
    actually collected (paid_cents / total_cents) — deposit-first is the studio's real billing
    pattern, so a paid deposit attributes its share now rather than waiting for the balance.
    Unpaid (draft/sent/viewed) invoices attribute nothing. Reads only; decides nothing."""
    offered: set = set()
    for r in db.all_(
        "SELECT plutus_last_bundles FROM galleries WHERE plutus_last_bundles IS NOT NULL"
    ):
        try:
            for b in json.loads(r["plutus_last_bundles"]) or []:
                sku = b.get("sku")
                if sku:
                    offered.add(sku)
        except (ValueError, TypeError):
            continue
    pairs: list = []
    if offered:
        for inv in db.all_(
            "SELECT id, total_cents, status, line_items FROM invoices "
            "WHERE status IN ('paid', 'deposit_paid')"
        ):
            try:
                items = json.loads(inv["line_items"]) or []
            except (ValueError, TypeError):
                continue
            if inv["status"] == "paid":
                fraction = 1.0  # the app's state machine: 'paid' == fully collected
            else:  # deposit_paid: pro-rate by money actually collected on this invoice
                total = inv["total_cents"] or 0
                collected = db.one(
                    "SELECT COALESCE(SUM(amount_cents), 0) AS c FROM payments WHERE invoice_id=?",
                    (inv["id"],),
                )["c"]
                fraction = min(1.0, collected / total) if total else 0.0
            if fraction > 0:
                pairs.append((items, fraction))
    agg = _sum_attributed(offered, pairs)
    return {
        "revenue": _dollars(agg["cents"]),
        "invoices": agg["invoices"],
        "skus": agg["skus"],
        "offered_skus": len(offered),
        "has_data": agg["cents"] > 0,
    }


@router.get("", response_class=HTMLResponse)
async def scorecard_view(request: Request):
    windows = [{"label": label, **_funnel(mod)} for label, mod in _WINDOWS]
    return templates.TemplateResponse(
        request,
        "admin/offer_scorecard.html",
        {"windows": windows, "revenue": _revenue_proxy(), "attributed": _attributed_upsell()},
    )
