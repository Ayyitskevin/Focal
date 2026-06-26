# ADR 0008 — Notion API modernization (version-configurable + data-source create)

**Status:** Accepted (foundation shipped; cutover gated on staging validation)
**Date:** 2026-06-26

## Context

The Notion adapter (`app/notion_sync.py`) hard-coded `Notion-Version: 2022-06-28` in two
request helpers — the audit's **#1 compatibility risk** ([R-MISE-03], audit §6.1, §8.1,
§11.2). Notion's **2025-09-03** release introduced a *multi-source database* model: a
database now parents one or more **data sources**, and once any connected workspace adds a
second data source to a database, a bare `database_id` is no longer precise enough — page
**creation** must target a `data_source_id`, or Notion returns validation errors.

What Mise actually does with Notion (verified by reading the adapter): **PATCH a page by
`page_id`** (invoice/gallery/booking status) and **CREATE a page under a database**
(Bookings calendar, Sessions spine). It never queries a Notion database. Per Notion's
2025-09-03 docs, PATCH-by-`page_id` is **unaffected**; only **CREATE** must move to a
data-source parent. So the surface that needs changing is exactly `_create_page`.

## Decision

Modernize through config, defaulting to the legacy behavior so nothing changes until a
human validates the new version on staging and flips it:

1. **API version is config-driven** — `config.NOTION_VERSION` (env `MISE_NOTION_VERSION`,
   default `2022-06-28`), applied by a single `_headers()` helper. The upgrade becomes a
   validated config flip, not a code edit.
2. **`_create_page` supports a data-source parent** — when a `data_source_id` is supplied
   it sends `{"parent": {"type": "data_source_id", "data_source_id": …}}`; otherwise the
   legacy `{"parent": {"database_id": …}}` (byte-identical to before). The two create
   sites pass `config.NOTION_BOOKINGS_DS` / `config.NOTION_SESSIONS_DS` (env, default
   empty).
3. **PATCH is untouched** — addressed by `page_id`, unaffected by the data-source model.

Default config (`2022-06-28`, no `*_DS`) ⇒ the adapter behaves exactly as it did. This is
strangler step 4 ("implement the current API version behind a flag"); the live-Notion
steps below are the arming gate.

## What still needs Kevin's live Notion (the cutover gate — NOT done here)

Per audit §11.2, before flipping `MISE_NOTION_VERSION=2025-09-03` in production:
1. **Inventory** every Notion DB/property Mise & Odysseus reference; assign a field owner.
2. **Resolve & store `data_source_id`** for the Bookings and Sessions databases (Notion:
   database settings → Manage data sources → Copy data source ID) into `*_DS` env.
3. **Capture redacted schema fixtures** (property types/ids, content redacted).
4. **Validate on staging** — create/patch/relation/date/rich-text + 429/5xx behavior,
   honoring Retry-After and the ~3 req/s limit.
5. **Shadow** writes to a staging DB; compare normalized payloads.
6. **Flip the version flag**, with immediate rollback = set it back to `2022-06-28`.

See `docs/NOTION-MODERNIZATION.md` for the runbook.

## Consequences

- **Positive:** the hard-coded-version risk is removed; the upgrade is a reversible config
  flip; the data-source create path is implemented to the documented 2025-09-03 contract
  and unit-tested against fixtures; PATCH (the bulk of Mise's Notion traffic) is provably
  unaffected.
- **To validate:** the create payload is written from Notion's published docs, not yet run
  against a live 2025-09-03 workspace — the staging step above is the gate before arming.
- **Scope:** Mise issues no Notion database *queries*, so the `/v1/data_sources/{id}/query`
  migration does not apply here; if a future feature queries Notion, revisit.

## Alternatives considered

- **Auto-resolve `data_source_id`** from `GET /v1/databases/{id}` at runtime: deferred —
  adds live calls + caching surface; an env-configured id (copied once from Notion) is
  simpler and keeps the adapter dormant until armed.
- **Rewrite to the `/v1/data_sources` query API now:** unnecessary — Mise never queries
  Notion databases.
