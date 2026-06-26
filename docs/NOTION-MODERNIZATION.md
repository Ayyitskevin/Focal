# Notion API modernization runbook

Mise's Notion adapter shipped against `Notion-Version: 2022-06-28`. This is the controlled
path to a current version (**2025-09-03**, the multi-source-database model). The code
foundation is in place and **defaults to the legacy behavior** — these steps arm the
upgrade. See [`adr/0008`](adr/0008-notion-api-modernization.md) for the decision.

## What's already shipped (default-off)

- `config.NOTION_VERSION` (env `MISE_NOTION_VERSION`, default `2022-06-28`) drives the
  `Notion-Version` header via one `_headers()` helper.
- `_create_page` sends a `data_source_id` parent when one is configured, else the legacy
  `database_id` parent. Two create sites read `MISE_NOTION_BOOKINGS_DS` /
  `MISE_NOTION_SESSIONS_DS` (default empty).
- PATCH-by-`page_id` (the bulk of Mise's Notion traffic) is unchanged and unaffected.
- Unit contract tests cover both header versions and both create parents.

## What Mise touches in Notion (the surface to validate)

| Call | Where | 2025-09-03 impact |
| --- | --- | --- |
| PATCH page status | `sync_invoice`, `sync_gallery`, `sync_booking` (update) | **None** — addressed by `page_id` |
| CREATE page (Bookings) | `sync_booking` (first mirror) | parent → `data_source_id` |
| CREATE page (Sessions) | `sync_session_for_booking` | parent → `data_source_id` |
| Query a database | — | **N/A** — Mise never queries Notion |

## Arming steps (require a live/staging Notion — NOT done in code)

1. **Inventory** every Notion database + property Mise (and Odysseus) reads/writes; assign
   each a single field owner. Confirm property names still match (`sync_*` use literal
   names like `Invoice Amount`, `Status`, `Session Name`).
2. **Resolve data-source ids** for the Bookings and Sessions databases — in Notion: open
   the database → settings → **Manage data sources** → ⋯ → **Copy data source ID**. (Or
   `GET /v1/databases/{id}` on 2025-09-03 returns a `data_sources` array.)
3. **Stage**: point a staging Mise at a staging Notion workspace (separate token/dbs). Set
   `MISE_NOTION_VERSION=2025-09-03` + the two `*_DS` ids there.
4. **Validate** create + patch + date/rich-text/select, and 429/5xx handling (honor
   `Retry-After`, stay under ~3 req/s). Capture redacted fixtures.
5. **Shadow** (optional): mirror to a staging DB and diff normalized payloads vs. the
   legacy path.
6. **Cut over** production: set `MISE_NOTION_VERSION=2025-09-03` and the `*_DS` ids in
   flow's `.env`. **Rollback = set `MISE_NOTION_VERSION` back to `2022-06-28`** (the
   `database_id` parent path resumes; no code change).

## Rollback

A single env var. With `MISE_NOTION_VERSION=2022-06-28` (or the `*_DS` ids unset), the
adapter sends the legacy header and `database_id` parent — exactly the pre-modernization
behavior.
