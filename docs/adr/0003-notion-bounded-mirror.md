# ADR 0003 — Notion is a bounded human mirror, never an authority

**Status:** Accepted (Phase 0)
**Date:** 2026-06-25

## Context

Notion is Kevin's human-facing planning and dashboard surface. It is tempting to let it
become the database of record because it is where humans look. Mise already syncs to it
**one-way** today **[CODE]** (`app/notion_sync.py`: creates/patches selected
booking/session/invoice/delivery fields; never read back into a Mise flow). Notion also
carries a known compatibility risk: Mise sends `Notion-Version: 2022-06-28`, predating
the data-source model (audit §11.2).

## Decision

Notion remains a **bounded, mostly one-way mirror and planning surface**. It must
**never** become: the transaction database, the job queue, a binary-media store, or a
second authority for any Mise-owned record. Mise projects selected fields outward to
Notion asynchronously; Notion may own a small set of **explicitly-owned human-workflow
fields** (e.g. task status, planning notes), reconciled by field ownership — never a
generic merge. Tasks and knowledge stay Notion-authoritative **today**, migrating only
through gates (ADR scope: not now; audit §19.2 — Athena has not earned replacement).

A Notion outage never blocks a Mise transaction; sync lag surfaces on the dashboard and
replays from the local event/job ledger (audit §17.10).

## Consequences

- **Positive:** humans keep their familiar surface; Mise stays authoritative and
  recoverable; payment/delivery never roll back because Notion failed.
- **Negative / debt:** the `2022-06-28` API version is a **red-light** modernization
  (store `data_source_id`, upgrade version, contract tests, staging+shadow, replayable
  sync, rollback). Tracked in the roadmap as a parallel red-light track; **not** done in
  Phase 0.
- **Boundary check:** any feature proposing to *read authoritative state back from
  Notion* is rejected by this ADR.

## Alternatives considered

- **Two-way sync / Notion as workflow DB:** rejected — dual-master drift, rate limits
  (avg 3 req/s), and binary-media unsuitability (audit §11.1, §11.3).
- **Replace Notion with Athena now:** rejected — modernize the adapter and quantify the
  business case first (audit §19.2).
