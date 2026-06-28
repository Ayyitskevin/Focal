# ADR 0030 — AI-assisted culling: the cull-state spine (operator keep/cut, reversible)

**Status:** Accepted (foundation slice of local AI-assisted culling; PR-A of the scoped plan)
**Date:** 2026-06-28
**Deciders:** Kevin (owner), principal engineer

## Context

The vision sidecars already score every photo (`argus_keeper_score`, migration 064) — but **no UI
acts on it**: the score is computed and thrown away. For a solo high-volume F&B shooter, fast
culling over those scores is the single highest-value AI capability (it's the AI category
photographers actually pay for, and the operator chose local/on-device AI). A 16-agent design
workflow scoped the feature; the operator chose a **keyboard cull deck**, **cloud-Argus scoring
now** (source-agnostic), and **flag-only Cut** (delivery gate deferred). This ADR is the
foundation those build on: the durable cull-state spine + write routes. The deck UI (PR-B) and the
delivery gate (PR-C) are separate, sequenced PRs.

A key finding constrained the design: the existing "local AI preview" path is **verified broken at
scale** — it scores only ~4 photos per gallery (alphabetical, base64-inlined, 120s, filename-keyed).
So the cull ranking reads the persisted `argus_keeper_score`, which **both** the cloud and (future)
local scorer write to the same column — promoting local Qwen later needs zero cull-UI change.

## Decision

Add a per-asset **operator decision** state, reversible and audited, with no UI yet beyond the
write routes (inert until armed).

- **Schema (migration 077).** `assets.cull_state` (`CHECK IN ('keep','cut') OR NULL`; NULL =
  undecided, the §11.4-safe default — every existing asset reads NULL and nothing changes),
  `cull_decided_at`, `cull_source` ('manual' today; the *score* provenance stays in
  `argus_keeper_score`). Index `(gallery_id, cull_state)`. Net-new because the existing columns are
  taken: `status` is the derivative-pipeline enum, `favorites` is the *client* actor, `portfolio` is
  publication intent.
- **Write routes (`app/admin/cull.py`).** `POST …/assets/{id}/cull` (keep/cut/restore) and
  `POST …/assets/bulk-cull` (one action over many) — each writes the three `cull_*` columns and an
  `audit_log` row (`cull:keep|cut|restore`) in one `db.tx()` (the first audited per-asset operator
  act). Bulk is **server-side scoped to the gallery** — a posted id from another gallery is
  silently skipped, so a tampered form can't reach across galleries.
- **'Cut' is soft and reversible.** It sets a flag and audits it; it touches **no** original,
  derivative, asset row, or delivery path. The destructive `delete_asset` stays a separate,
  confirm-gated route. 'restore' clears the decision back to NULL.
- **Inert until armed.** Every cull route 404s unless `MISE_CULL_UI` is on (default off), so
  shipping this changes nothing on a host until the operator flips the flag.

## Consequences

- **Positive:** the foundation for a fast cull workflow exists, fully tested and money-/delivery-
  safe, with the score it ranks on already populated. The deck UI can be built and reverted behind
  one flag.
- **§11.4 holds:** AI only provides the score; every keep/cut is an explicit human click; nothing
  auto-deletes, auto-publishes, or auto-decides. The decision is reversible and audited.
- **Schema:** additive (3 columns + index); existing rows read NULL. Rollback is plain DROP COLUMN
  (SQLite 3.45+) — no rebuild of the FK-heavy `assets` table. Red-light change → reviewed draft PR.
- **Deferred (next PRs):** the keyboard deck + admin large-preview route + scored/paginated query
  (PR-B); the client-delivery gate so a 'cut' frame stops reaching clients (PR-C, red-light, edits
  the ~5 client serving queries with its own review); and any local-scorer rebuild (separate).

## Alternatives considered

- **Overload `status` or reuse `favorites`/`portfolio`.** Rejected — wrong actor / wrong meaning /
  entangled with the derivative pipeline; a dedicated cull state is the honest model.
- **Make 'cut' hide from clients immediately.** Deferred to PR-C — that's a red-light delivery-path
  change across five client query files and deserves its own review; v1 keeps Cut a pure, reversible
  record (the UI will say so loudly).
- **No CHECK on the added column.** Rejected — SQLite carries a CHECK on `ALTER ADD COLUMN`; it's a
  cheap domain backstop, so it's included.
