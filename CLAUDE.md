# Mise — Claude Code bootstrap

**Mise Solo Studio OS** is a single-tenant modular monolith for a solo photography studio. Stack:
FastAPI (async) + HTMX/Alpine.js + SQLite/WAL. One product, one data spine, one operator.

## Architecture in one paragraph

Mise is the **transaction authority**. AI sidecars (Argus/vision, Plutus/offers,
Mnemosyne/albums, Aphrodite/products, Dionysus/content) are **stateless workers** driven
through a **provider facade** (`app/providers/`). The facade owns capability routing, cost
ledgering (`ai_runs`), and the structured-output contracts (`schemas/*.schema.json`). Every AI
output is a **draft a human approves** — the model proposes, deterministic code validates, a
human clicks. Nothing auto-sends, charges, publishes, or prints.

## Key invariants (never break these)

- **§11.4 — Model proposes, human approves.** AI results enter a review surface; operator
  action is required before any write to a client-visible record.
- **Money/rights boundary.** Plutus/Aphrodite outputs are proposals; Mise enforces spend caps
  and consent gates in code, not convention. No auto-invoice, auto-charge, or auto-publish.
- **Strangler migration.** Live paths stay green; the new path is flag-gated and can be rolled
  back to the old path by toggling one env var. Decommission only after parity + observation.

## Directory map

```
app/               FastAPI modules (one file = one domain)
app/admin/         Admin-only route handlers (CSRF-gated)
app/providers/     Provider facade: contracts.py, registry.py, adapters
migrations/        Sequential SQL migrations (NNN_name.sql) + rollback/
schemas/           JSON schemas for worker structured output
templates/         Jinja2 templates (admin/* + client/*)
tests/             Pytest suite (no live model/API calls — mock only)
docs/              ADRs, runbook, worker contract, sibling briefs
docs/sibling-briefs/  Ready-to-paste prompts for each sibling repo
```

## Development rules

- **Branch:** always `claude/<short-topic>`; NEVER push to `main`.
- **PRs:** every change is a **draft PR**; a human merges — never self-merge.
- **Red-light changes** (migrations, money path, auth/CSRF, contracts, deploy) ship as
  reviewed draft PRs; do not self-apply.
- **Migrations:** sequential `NNN_name.sql` + matching `rollback/NNN_name.sql`; never edit a
  merged migration.
- **Tests:** `pytest` — all passing, no live model/API calls. Use fixtures and mock adapters.
- **No secrets in code.** Use `.env.example`; never read production `.env`, private keys, or
  client media.

## Session safety (hard stops)

Do NOT:
- Edit the live flow tree, restart services, push to main, or self-merge PRs.
- Read/copy real secrets, production `.env`, private keys, client media, or production DB.
- Auto-send offers/emails, auto-export orders, auto-invoice, or auto-publish anything.
- Write the model identifier into commits, PR bodies, code comments, or docs.

## Key entry points by task

| Task | Start here |
|------|-----------|
| New capability | `app/providers/contracts.py` (add `Capability`), then adapter in `app/providers/` |
| New migration | `migrations/NNN_name.sql` + `migrations/rollback/NNN_name.sql` |
| New admin route | `app/admin/<domain>.py`, register in `app/main.py` |
| Provider swap / cutover | `app/config.py` flag → `app/providers/registry.py` → `/admin/vision-cutover` |
| Worker contract | `docs/WORKER-CONTRACT.md` + `schemas/*.schema.json` |
| Runbook / operations | `docs/MISE-SOLO-STUDIO-OS-RUNBOOK.md` |
| Sibling repo prompts | `docs/sibling-briefs/` |
| ADR index | `docs/adr/README.md` |

## Current commercial spine handoff

Recent F&B/commercial work is documented in ADRs 0034–0043. That spine now includes
derived repeat-client cadence, canned shot-list and deliverable templates, a project closeout-readiness
panel, read-only company next-action ranking, a Studio Activity commercial action queue, and a
manual AR chase assist for past-due invoices. These are deterministic/operator surfaces only: no
auto-send, auto-charge, auto-publish, or auto-close.

## Provider facade quick reference

```python
# app/providers/contracts.py
class Capability(str, Enum):
    VISION = "vision"
    OFFERS = "offers"
    ALBUMS = "albums"
    PRODUCTS = "products"   # dormant — budget cap + consent gate
    CONTENT = "content"

# ProviderResult fields: provider, model, latency_ms, cost_usd, raw, structured
```

Adapters live in `app/providers/<name>_adapter.py`. Register in `app/providers/registry.py`.
`serves_production=False` marks a dormant adapter (dormant = wired but not armed).

## Dormant capabilities

Products (Aphrodite) is intentionally dormant until the operator sets three gates:
1. `PRODUCTS_BUDGET_USD` — the spend cap
2. Written consent/licensing policy
3. Render backend choice (Aphrodite URL)

Do not arm it without operator sign-off on all three.
