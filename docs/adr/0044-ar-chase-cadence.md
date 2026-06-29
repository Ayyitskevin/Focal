# ADR 0044 - AR chase follow-up cadence

**Status:** Accepted (F&B/commercial spine; builds on ADR 0043)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal engineer

## Context

ADR 0043 added a manual company AR chase assist. Once that button exists, the next operational
risk is repeat nudging: the operator should know whether AP was already chased recently before
sending another reminder.

## Decision

Add a derived follow-up cadence over the existing manual send log.

- Treat AR chase sends as `emails_log` rows whose `doc_kind='other'`, `doc_id` is the company id,
  and whose subject uses the AR assist default prefix.
- Show "never chased", "last chased Xd ago", and the next follow-up date on company AR surfaces.
- Rank overdue AR as "chased recently" for the configured follow-up window, default seven days,
  after a logged send, then return it to the normal chase action once follow-up is due.
- Keep the cadence read-only. It creates no task, sends nothing, changes no invoice/payment state,
  and remains editable by changing the next manual email draft.

## Consequences

- The commercial action queue now separates first-time AR chases, recent nudges, and due follow-ups.
- The operator can reopen the chase assist with context instead of guessing from memory.
- No schema migration is needed; the tradeoff is that cadence is tied to the default AR assist
  subject marker in the existing send log.
