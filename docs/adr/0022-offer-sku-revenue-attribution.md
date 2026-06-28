# ADR 0022 — Offer SKU revenue attribution: proxy → real attributed upsell

**Status:** Proposed (build-ready; gated on Plutus emitting `offers.schema.json` with stable
SKUs — sibling brief PLUTUS #1)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

The offer scorecard (ADR 0020) reports a deliberately honest **revenue proxy**: all payment
revenue on a project recorded *after* its offer was sent. ADR 0020 named its own limitation
and the fix, and deferred it:

> Build the product/line-item attribution model now. **Deferred** — much larger; the funnel +
> proxy answer the retire-gate question today.

This ADR is that deferred follow-up. It becomes buildable once Plutus emits the
`offers.schema.json` shape with a **stable `sku` per bundle** (sibling brief PLUTUS #1): the
SKU is the key that links an accepted offer to the invoice line a human later builds, turning
the proxy into **real attributed upsell revenue**.

What exists today (the seam this plugs into):

- **Offer storage is summary-only.** `galleries.plutus_last_*` keeps `run_id`, `status`,
  `estimated_cents`, `bundle_count`, `offer_url`, `pitch_url`, plus the operator
  `decision`/`sent` columns (migrations 055/059/062/068/069). The **bundles themselves — and
  any SKUs — are not persisted**; they live at `offer_url` / in Plutus's response.
- **Invoice line items carry no SKU.** `invoices.line_items` is JSON `[{label, qty,
  unit_cents}]` (migration 002). There is nowhere to tag a line as "this is offer SKU X."
- **The scorecard proxy** joins offered project → `invoices` → `payments` recorded after the
  send date. No offer→sale key, so it attributes *all* post-send project revenue.

## Decision

Add an **offer→invoice-line linkage keyed by SKU**, kept strictly within the money/rights
boundary (§11.4): the model proposes SKUs, deterministic code records them, **a human still
builds every invoice by hand** — nothing here auto-invoices, auto-charges, or auto-sends. The
SKU is carried through as a tag the operator opts into; attribution is read-only aggregation.

Three pieces, each independently shippable and inert until the one before it carries data:

### 1. Persist the proposed bundles with SKUs (migration + writeback)

When Plutus returns the `offers.schema.json` shape, persist the full bundle list (incl. each
`sku` and optional `line_items`) so Mise owns the proposed catalogue, not just a total.

- **Migration `NNN_plutus_offer_bundles.sql`** (+ rollback): add
  `galleries.plutus_last_bundles TEXT` (JSON: the validated `bundles` array). Additive,
  forward-only, existing rows read NULL — behaviour unchanged until an offer with bundles
  lands. (A child `offer_bundles` table is the alternative; rejected below — JSON on the
  gallery matches the existing one-offer-per-gallery `plutus_last_*` model and the dormant
  rows stay trivially nullable.)
- **Writeback:** the path that records a Plutus offer validates against `offers.schema.json`
  (the deterministic gate already used elsewhere) and stores the `bundles` JSON alongside the
  existing summary columns. Malformed/over-range → reject, store nothing (the offer summary is
  unaffected). One offer per gallery, idempotent (mirrors PLUTUS #2).

### 2. An optional `sku` on invoice line items (no new table)

Extend the `invoices.line_items` JSON item shape from `{label, qty, unit_cents}` to
`{label, qty, unit_cents, sku?}` — `sku` optional, omitted on every existing/non-offer line.
No migration (it's a JSON shape, `DEFAULT '[]'`), no change to invoice math (totals ignore
`sku`). When the operator builds an invoice from an accepted offer, the offer's bundle/line
SKUs are **pre-filled into the line items they choose to add** — opt-in, editable, never
auto-applied.

### 3. Real attribution on the scorecard (replace the proxy half)

Add a **third scorecard tile: "Attributed upsell"** — payment revenue on invoices that carry
an offer SKU in a `line_items[].sku`, summed against the offers whose SKUs match. This is the
causal link the proxy lacked: it counts only revenue on lines a human tagged with an offered
SKU, not all post-send revenue. Keep the existing proxy tile alongside it, relabelled
"directional proxy," so the two are comparable during rollout and the proxy still covers
offers whose invoices predate SKU tagging.

## Consequences

- **Positive:** the scorecard graduates from "directional proxy" to **real attributed upsell
  revenue** — the evidence the retire-gate (audit §19.4) actually wants for the offers
  capability. Mise also gains a persisted offer catalogue (bundles + SKUs), useful beyond the
  scorecard.
- **Money/rights boundary intact:** SKUs are proposals carried as tags; the human builds every
  invoice; attribution is read-only. No auto-invoice/charge/send is introduced (§11.4,
  consistent with ADRs 0018/0020).
- **Strangler-safe:** each piece is additive and dormant until the upstream carries data —
  persist bundles (inert with no SKUs) → SKU on lines (omitted until used) → attribution tile
  (reads $0 until a SKU-tagged invoice is paid). The proxy stays as the fallback/comparison.
- **Red-light (money path + migration):** ships as reviewed draft PRs a human merges; never
  self-applied.

## Dependencies / sequencing

- **Upstream gate:** PLUTUS #1 must merge first (Plutus emits stable `sku` per bundle). Until
  then, piece 1 persists empty/absent SKUs and the attribution tile reads $0 — safe but inert.
- **Build order:** (1) persist bundles → (2) SKU on invoice lines + pre-fill from an accepted
  offer → (3) attribution tile. Each is its own draft PR with tests; (3) is the payoff.
- **Tests (mock-only):** schema-validated bundle writeback (reject malformed); SKU round-trips
  onto an invoice line and totals are unchanged; attribution query counts only SKU-tagged
  revenue and excludes untagged post-send payments; money-guardrail test — no path
  auto-creates/charges an invoice.

## Alternatives considered

- **Child `offer_bundles` / `offer_skus` tables.** Rejected for now — a normalized table is
  heavier than the one-offer-per-gallery model needs; `plutus_last_bundles` JSON matches the
  existing `plutus_last_*` summary pattern and stays nullable/inert. Revisit if offers ever
  become many-per-gallery or need cross-gallery SKU analytics.
- **A first-class `sku` column on a separate invoice-line table.** Rejected — `invoices`
  stores `line_items` as JSON today; a relational line table is a much larger migration of the
  whole invoicing model, out of scope for closing the attribution gap.
- **Auto-build the invoice from an accepted offer.** Rejected — violates the money/rights
  boundary (§11.4). The operator must build and send every invoice; SKUs are pre-fill, not
  automation.
- **Keep only the proxy.** Rejected — ADR 0020 already shipped the proxy and named this as the
  honest upgrade; with SKUs available upstream, the causal link is finally cheap to add.
