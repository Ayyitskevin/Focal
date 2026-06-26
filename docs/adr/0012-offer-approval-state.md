# ADR 0012 — Plutus offers: persisted operator approve/reject state

**Status:** Accepted (offers review workflow)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

The offers review queue (`/admin/offers`) consolidates every gallery's Plutus offer
proposal into one triage surface, but it was **read-only**: the operator could see an
offer and click through to Plutus, yet had nowhere to record *"I've decided to pursue
this one / drop that one."* Triage was ephemeral — re-opening the page showed the same
undifferentiated list every time. The offers slice (#16) explicitly deferred this:
"persisting an 'approved' state would need a schema column and is a separate red-light
follow-up."

This mirrors the album review workflow (ADR 0011): a model proposes, and the human's
**decision** on that proposal should be a first-class, persisted fact — without the
decision ever crossing into auto-acting on the client's behalf.

## Decision

Persist a per-gallery operator decision on the Plutus offer and make the queue actionable.

- **Schema (migration 068, additive).** Two nullable columns on `galleries`:
  `plutus_offer_decision` (`'approved'` | `'rejected'` | NULL = undecided) and
  `plutus_offer_decided_at`. Values are enforced in app code (`admin/offers._set_decision`
  raises on anything else), matching the plain ADD COLUMN style of the other `plutus_*`
  migrations. Existing rows read NULL → undecided; behavior is unchanged until the operator
  acts.
- **Actions.** `POST /admin/offers/{id}/approve|reject|reset` set or clear the decision
  (admin-gated; same-origin via the global CSRF middleware; bound-param SQL). A decision is
  allowed only for a gallery that actually has an offer (`plutus_last_status` present).
- **Surface.** The queue shows a decision badge per row, approve/reject/reset buttons, a
  decision filter (any / undecided / approved / rejected) orthogonal to the status filter,
  and an **approved-pipeline value** beside the proposed-pipeline total — the committed vs.
  proposed upsell pipeline. CSV gains a Decision column.
- **Bounded authority — unchanged.** Approving an offer records the human's call and
  *nothing else*. It does **not** send the offer, charge a card, or create an invoice; the
  offer is still edited in Plutus and shared deliberately. AI-proposed pricing stays a
  human-approved draft (audit §11.4, the project's money-path red line).

## Consequences

- **Positive:** triage is now durable and the queue is a real workflow (review → decide →
  the approved set is what you act on). The approved-pipeline figure separates committed
  from speculative revenue. Rollback drops two nullable, unreferenced columns (SQLite ≥3.35
  `DROP COLUMN`; target is 3.45+).
- **Negative / deferred:** the decision does not yet *drive* anything downstream — it does
  not trigger a send, an invoice, or a Plutus state change. Wiring an approved offer into an
  actual send/checkout flow is a separate, money-path slice (red-light) and intentionally
  out of scope here. The decision is also independent of Plutus's own offer state; Mise
  records the operator's local call, not a round-trip to Plutus.
- **Schema:** the additive migration is what makes this a red-light, human-merged PR.

## Alternatives considered

- **Keep the queue read-only; track decisions in Plutus.** Rejected — Mise is the operator's
  daily surface and the sole transaction authority (ADR 0002); the decision belongs where
  the triage happens, and round-tripping to Plutus for a local "yes/no" adds coupling for no
  gain.
- **A separate `offer_decisions` table.** Rejected as overkill — the offer summary is
  already one-per-gallery on the `galleries` row (`plutus_last_*`); the decision is 1:1 with
  it, so two columns alongside that summary is the simplest faithful model. A table would be
  warranted only if we needed decision *history*, which the audit doesn't ask for here.
- **Auto-approve high-value offers / auto-send on approve.** Rejected — both cross the
  money-path red line. Every offer decision and every send stays an explicit human act.
