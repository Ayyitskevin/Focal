# ADR 0002 — Mise is the sole transaction authority

**Status:** Accepted (Phase 0)
**Date:** 2026-06-25

## Context

The business spans several systems (Mise, Notion, Stripe, Google Calendar, sibling
sidecars, Odysseus). Without one authority, records drift: which system is right about a
booking, an invoice, a delivered gallery, an accepted offer? The audit's system-of-record
matrix (§3.1) and the master prompt both require a single transaction authority for
everything that touches a client, money, a contract, or a deliverable.

## Decision

**Mise's SQLite database is the single authoritative system of record** for: leads,
clients, projects, bookings, shot lists, galleries/delivery, media metadata, contracts,
invoices/payments, AI job state + provenance + approval, offers/client links, album
drafts, and client-linked content drafts. Every other system is a **projection, mirror,
draft, or disposable cache**:

- **Stripe** is the payment *event* source; Mise reconciles idempotent webhooks and is
  authoritative for invoice/payment state **[CODE]** (`app/public/pay.py`).
- **Google Calendar / Notion** are one-way mirrors of Mise-owned bookings/sessions.
- **Sibling workers** own *run* state only; Mise owns *accepted* status + references.

Every authoritative record has **exactly one writer**. Cross-system writes are
idempotent and carry actor + timestamp + source; conflicts resolve "Mise wins" except
where a field is explicitly owned elsewhere (e.g. Notion-owned human workflow fields).

## Consequences

- **Positive:** no split-brain; recovery is "restore the Mise DB" (one chain, nightly
  restore-tested); the audit trail (`app/audit.py`) and per-field provenance have one
  home.
- **Negative:** Mise must stay available for the revenue path → mitigated by emergency
  operating mode (Mise runs core booking/gallery/contract/invoice/payment without any
  AI/Notion/sidecar, audit §17.11) and the clean-rebuild runbook.
- **Rule:** a worker/Odysseus/Notion outage or a provider failure must **never** mutate
  or partially update an authoritative record. Enforced structurally by the Phase 0
  contract — only an `OK` `ProviderResult` may drive a write.

## Alternatives considered

- **Active-active Mise / second writable copy:** rejected — SQLite + media + payment
  state make split-brain risk exceed the availability gain (audit §6.4).
- **Postgres as a cross-service authority:** rejected for now (see ADR 0001/0005);
  workers' run state is not promoted to a business authority.
