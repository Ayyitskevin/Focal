# Focal — Claude Code bootstrap

**Focal Solo Studio OS** is a single-tenant modular monolith for a solo photography studio. Stack:
FastAPI (async) + HTMX/Alpine.js + SQLite/WAL. One product, one data spine, one operator.

## Architecture in one paragraph

Focal is a self-hostable, local-first studio operating system for solo photographers
and small creative studios. The stack is FastAPI + HTMX/Alpine.js + SQLite/WAL for the
web application and SwiftUI for the focused iOS companion. Focal is the transaction
authority. Optional AI/media workers are stateless and operate through a provider
facade. Every AI output is a draft a human approves: the model proposes, deterministic
code validates, and an operator decides before anything becomes client-visible,
financial, contractual, or published state.


## Key invariants (never break these)

- **§11.4 — Model proposes, human approves.** AI results enter a review surface; operator
  action is required before any write to a client-visible record.
- **Money/rights boundary.** Aphrodite (products) outputs are proposals; Focal enforces spend
  caps and consent gates in code, not convention. No auto-invoice, auto-charge, or auto-publish.
- **Strangler migration.** Live paths stay green; the new path is flag-gated and can be rolled
  back to the old path by toggling one env var. Decommission only after parity + observation.

## Directory map

```
app/               FastAPI modules (one file = one domain)
app/admin/         Admin-only route handlers (CSRF-gated)
app/providers/     Provider facade: contracts.py, registry.py, adapters
migrations/        Sequential SQL migrations (NNN_name.sql) + rollback/
schemas/           JSON schemas for worker structured output
templates/         Jinja2 templates (admin/, public/, saas/, site/)
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

## Current product spine

Current work is centered on the photographer's operating loop: inquiry, client and
project records, booking, shot preparation, gallery proofing and delivery, contracts,
invoices and payment state, follow-up, and owner activity. These surfaces are
deterministic and operator-controlled. No AI path may auto-send, auto-charge,
auto-publish, or silently mutate a consequential business record.

The iOS companion is a focused native surface for the highest-value owner and client
journeys. It must remain honest about capability boundaries instead of pretending to
have full web parity.

## Provider facade quick reference

```python
# app/providers/contracts.py
class Capability(enum.Enum):
    VISION = "vision"       # Argus: keywords, alt text, culling / hero signals
    CONTENT = "content"     # Odysseus caption / Dionysus packs: captions, copy drafts
    PRODUCTS = "products"   # Aphrodite: renders — dormant, budget cap + consent gate

# ProviderResult fields: capability, provider, status, review, output,
#   model, latency_ms, cost_usd, tokens, error
```

Adapters live in `app/providers/adapters.py` (legacy) / `<name>_*.py`. Register in
`app/providers/registry.py`. `serves_production=False` marks a dormant/eval-only adapter
(dormant = wired but not armed). See ADR 0068 for the consolidation target (in-process or
direct hosted-API — never a separate self-hosted sidecar).

## Dormant capabilities

Products (Aphrodite) is intentionally dormant until the operator sets three gates:
1. `PRODUCTS_BUDGET_USD` — the spend cap
2. Written consent/licensing policy
3. Render backend choice (Aphrodite URL)

Do not arm it without operator sign-off on all three.
