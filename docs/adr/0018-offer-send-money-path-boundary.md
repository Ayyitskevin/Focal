# ADR 0018 — Offer send: deliver the link, never touch the money path

**Status:** Accepted (Track B — the first money-path-adjacent action)
**Date:** 2026-06-26
**Deciders:** Kevin (owner), principal engineer

## Context

Plutus proposes print/album offers; the operator can already approve or reject them
(ADR 0012). The approved ones then had to be acted on entirely outside Mise — there was no
way to actually get an approved offer in front of the client from the queue. That is the
first step of "Track B" (the money-path flows). It is also the most sensitive area in the
whole consolidation: AI proposed the offer, so anything that turns a proposal into client
contact or money must stay a deliberate, human action (audit §11.4, the project's standing
money guardrail).

## Decision

Add a **send** action to the offers queue that emails the client the offer **link**, and
nothing more — with a hard boundary at the money path.

- **Reuse the proven manual-send path.** Sending an offer works exactly like emailing a
  proposal/contract/invoice today: `mailer.send` (Gmail SMTP) + a row in `emails_log`. No
  new mail channel, no new identity.
- **The email is an editable draft.** `/admin/offers/{id}/send` pre-fills a warm note + the
  offer link (pricing stays on the offer page — the chosen content). The operator reviews,
  edits, and clicks Send. Nothing auto-sends; the button only appears for a **ready,
  operator-approved** offer that has a link *and* a client email.
- **Same guard on GET and POST.** `_send_block` refuses (status not ready / not approved / no
  link / no client email) identically on the compose and the send paths, so a stale page or
  a direct POST can't bypass the approval gate.
- **Record atomically.** After `mailer.send` succeeds, one `db.tx()` writes the `emails_log`
  row, marks the gallery sent (`plutus_offer_sent_at` / `…_sent_to`, migration 069), and
  appends an `audit_log` row (`offer_emailed`) — they commit together or not at all.
- **The boundary:** sending emails the link. It **never** charges the client and **never**
  creates an invoice. Acceptance continues to flow through the existing, human-initiated
  invoice workflow. No AI-proposed value ever reaches a charge automatically.

## Consequences

- **Positive:** the approved-offer pipeline is now actionable end to end inside Mise, using
  infrastructure operators already trust, without weakening the money guardrail. The send is
  logged twice (email log + audit) so there is a clear record of what went to whom.
- **Schema:** migration 069 adds two nullable columns to `galleries`; additive, forward-only,
  rollback drops them (the `emails_log` history is independent and survives). Red-light change
  — shipped as a reviewed PR a human merges.
- **Bounded by data, not a flag:** the action is inert unless an offer is approved, has a
  link, and the client has an email (and Gmail is configured). No feature flag is needed
  because there is nothing to arm — the preconditions are the gate.
- **Honest scope:** resending is allowed (it emails a fresh copy and updates the timestamp) —
  there is no idempotency key, by design, because an operator may legitimately re-send. The
  "Sent" badge and the confirm dialog make a repeat send a conscious choice.

## Alternatives considered

- **One-click send of a fixed template.** Rejected — client-facing, AI-originated content
  must be a human-reviewed draft (audit §11.4). The editable compose step is the gate.
- **Publish to the client portal instead of emailing.** Considered and deferred — email is
  how every other client document goes out today, so it is the consistent first channel.
  Portal surfacing remains a possible later addition.
- **Auto-create a draft invoice on send (or on acceptance).** Rejected — that crosses the
  money-path boundary this ADR exists to hold. Invoicing stays separate and human-initiated.
