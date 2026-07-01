# ADR 0047 — MicroSaaS positioning: instance-per-customer, one flat price

**Status:** Accepted (strategic frame for the $20/mo transformation)
**Date:** 2026-06-29
**Deciders:** Kevin (owner), principal architect

## Context

Mise began as a single-tenant studio OS (ADRs 0002/0005: one SQLite spine, privacy-first,
self-hosted). A hosted mode has since grown (`app/saas.py`, `SAAS_MODE`) to let non-technical solo
photographers pay for a managed instance rather than run their own server. The transformation goal
is to make Mise the standout $20/month MicroSaaS in the portfolio **without discarding the identity
that makes it different** — a photographer's own private studio, not a seat in a shared database.

The market research that set our direction found the durable wedge is **data ownership + no
subscription-stacking**, and the trap is the solo-operator support/ops burden of a true
multi-tenant SaaS. Both point the same way.

## Decision

Mise is a **modular monolith deployed instance-per-customer**, offered two ways from one codebase:

- **Managed — $20/month flat**, 14-day trial, no tiers (`SAAS_PRICE_CENTS = 2000`, locked in code,
  asserted by preflight). The host runs the hosted control plane; each studio gets its **own
  SQLite database and file-storage root** under `SAAS_TENANT_DATA_DIR/<slug>/`, resolved by
  subdomain. Isolation is physical (separate DB files), not row-level — the privacy story is real.
- **Self-hosted — free**, the default posture (`SAAS_MODE=false`, byte-for-byte the original
  single-tenant deploy). This is the "own your data, no rent" escape hatch that de-risks the
  purchase and is itself marketing.

Explicitly **rejected: a row-level multi-tenant rewrite** (shared tables keyed by tenant_id). It
would destroy the physical-isolation privacy moat, add a whole class of cross-tenant query bugs,
and make "export/delete my studio" hard — the opposite of the positioning.

Pricing is one flat line because a solo founder cannot support a pricing matrix, and buyers in this
niche are fatigued by tiered SaaS. The moat is *positioning + data ownership + focus*, not features
gated behind tiers.

## Consequences

- **Isolation is a feature, not just an implementation detail** — "your studio, your database,
  export or leave anytime" is the sales line and the architecture simultaneously.
- **The single codebase serves both models** — every feature built for self-host is the managed
  product; there is no SaaS/OSS fork to maintain. `SAAS_MODE` gates only the control plane +
  routing, never the studio features.
- **Hard gates before charging (this transformation's job):** instance isolation must be *correct*
  — tenant-bound sessions (ADR 0048), no shared money/integration credentials leaking across
  studios, provisioning after payment, recovery + offboarding. These are tracked as sequenced
  red-light PRs; the managed plan does not open to the public until they land.
- **Per-instance cost scales with customers** (disk, a DB file, media). Acceptable at $20/mo for a
  solo-run host at small scale; a density/limits story is a later concern, logged not solved.

## Alternatives considered

- **Row-level multi-tenant SaaS.** Rejected — kills the privacy moat and the clean export/delete
  story; adds cross-tenant leak surface to every query.
- **Self-host only (no managed plan).** Rejected — excludes the majority of solo photographers who
  won't run a server; the $20 managed plan is the actual business.
- **Tiered pricing (Starter/Pro).** Rejected — unsupportable by a solo founder and against the
  anti-stacking positioning; one price is the product.
