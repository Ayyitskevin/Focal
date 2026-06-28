# ADR 0024 — Money operations: one read-only pane over the money path

**Status:** Accepted (the money-path analog of the AI operations pane, ADR 0013)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

Mise's money signals are spread across pages: outstanding AR on financials, collected revenue on
financials/income, the approved-offer pipeline on the offers queue and scorecard. The most
actionable gap had **no home at all**: offers the operator *approved but never sent* — the offer
scorecard computes a send-rate (sent ÷ approved) but never surfaces *which* approved offers are
still sitting unsent. The AI side already has a "one pane of glass" (`/admin/ai-ops`, ADR 0013);
the money side did not.

## Decision

Add a read-only `/admin/money-ops` — the money-path analog of `/admin/ai-ops`. Pure aggregation
over the real `invoices` / `payments` / offer columns; it writes nothing and every tile links to
the page that owns the action.

- **Needs attention** (chase order): **approved offers not sent** (count + committed value —
  the actionable send-rate gap) and **invoices past due** (count + owed). Each highlights when > 0.
- **Money at a glance:** collected (last 30 days), outstanding AR (`common.open_invoice_balance`),
  and the approved-offer pipeline awaiting send.
- Jump-to links to financials, Client P&L, offers, and the offer scorecard.

## Consequences

- **Positive:** the operator gets a single morning glance at what to chase — the approved-unsent
  gap is now visible and one click from the offers queue, which directly lifts the scorecard's
  send-rate (and therefore attributed upsell, ADR 0022). AR past due is surfaced, not buried.
- **Read-only, money-safe:** no charge/send/decision happens here (§11.4); it reads the same
  helpers the financials and offers pages already trust. Additive surface, inert until there are
  invoices/offers to summarize.
- **No schema, no migration, trivial rollback** (drop the route + nav entry).

## Alternatives considered

- **Add the approved-unsent count to the existing offer scorecard.** Rejected — the scorecard is a
  keep/retire measurement view; the operator's daily chase list is a different job, and the AR +
  collected numbers belong with it, not on the scorecard.
- **Auto-send approved offers.** Rejected — violates the money/rights boundary (§11.4). The pane
  surfaces the gap; the human still sends each offer from the queue.
