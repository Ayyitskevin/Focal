# ADR 0020 — Offer scorecard: a funnel + an honest revenue proxy

**Status:** Accepted (roadmap 3.3 — the "does it earn its keep?" gate for offers)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

The audit (§19.4) says a consolidated capability is a **retire candidate** unless it shows
measured value, and the roadmap (3.3) asks for a 30–60 day offer-acceptance/revenue
scorecard. Offers are now actionable end to end (propose → approve → send, ADRs 0012/0018),
so we can finally measure them. The hard part is *revenue attribution*: an offer is an
upsell, but there is **no offer→sale link** in the data — an accepted offer surfaces only as
an invoice/payment in the existing money path, which also contains the original shoot fee.

## Decision

Add a read-only `/admin/offers-scorecard` with two halves, and be explicit about what each
can and cannot claim.

- **The funnel (exact).** Proposed → approved → sent counts, proposed/approved pipeline
  value, and the operator approval rate (approved ÷ proposed) and send rate (sent ÷
  approved), over all-time / last 60d / last 30d (windowed by proposal date). All of this is
  exact, straight from the `plutus_*` columns on `galleries`.
- **Revenue (attribution proxy).** Payment revenue recorded on a project *after* its offer
  was sent. Two deliberate choices:
  - **Project-level aggregation.** A project can own several galleries, so summing per
    gallery would double-count a shared payment. The query takes `MIN(sent_at)` per project
    and sums payments once per project.
  - **Labelled a proxy, not causal.** With no offer→sale key, it attributes *all* post-send
    project revenue, not just incremental upsell. The UI says so plainly and points to the AI
    cost report for the COGS side. Galleries with no linked project can't be attributed and
    are excluded from this half (but counted in "offers sent").
- **Reports, never decides.** The keep/retire judgement (audit §19.4) stays human: a healthy
  approval rate + real pipeline value argue keep; a long run of proposed-but-never-approved
  offers argues retire.

## Consequences

- **Positive:** the offers capability now has the measurement the audit requires to justify
  keeping or retiring it, without inventing a precise revenue-lift number the data can't
  support. The funnel alone is a strong operator-engagement signal.
- **No schema, no migration, no flag.** Pure aggregation over existing columns; additive
  surface, inert until offers exist.
- **Honest limitation:** the revenue figure is directional. A precise incremental-upsell
  number would need a product/line-item model tying an invoice line to the offer — a larger,
  separate piece of work, explicitly out of scope here.

## Alternatives considered

- **Claim a precise revenue-lift number.** Rejected — it would require an offer→sale link the
  data doesn't have; presenting attribution as causal would be dishonest.
- **Per-gallery revenue sums.** Rejected — double-counts payments on multi-gallery projects.
- **Skip revenue, show only the funnel.** Rejected — the audit explicitly wants a revenue
  signal; a clearly-labelled proxy is more useful than none, as long as it doesn't overclaim.
- **Build the product/line-item attribution model now.** Deferred — much larger; the funnel +
  proxy answer the retire-gate question today.
