# Plutus brief — offers worker

> **Superseded (2026-06-28, ADR 0026):** the OFFERS capability was decommissioned — print/album
> upsell bundles assume a consumer who buys prints, which doesn't fit Mise's solo B2B
> food-and-beverage workflow. Kept as a historical record; do not build against it without
> re-opening ADR 0026.

**Role:** the OFFERS worker (print/album bundle recommendations). Goal: a stateless,
contract-true recommendation worker — never a SaaS, never touching money. The headline change
is **SKU/line-item linkage**, which turns Mise's offer scorecard from a revenue *proxy* into
real attributed upsell. Conforms to [`../WORKER-CONTRACT.md`](../WORKER-CONTRACT.md) +
[`../../schemas/offers.schema.json`](../../schemas/offers.schema.json).

**Money guardrail (every step):** offers are PROPOSALS ONLY. Plutus must NEVER charge, send to
a client, or create an invoice. Pricing/SKU rules are deterministic proposals a human applies
in Mise.

---

## Mode A — umbrella (self-sequencing)

````
You are improving the **Plutus** repo to fit the vision of "Mise Solo Studio OS." Do a
review → gap-analysis → prioritized plan → incremental draft PRs. Deliver the audit and plan
first; wait for go before large changes.

Mise is a single-tenant modular monolith that already owns clients, galleries, invoices,
payments, operator identity, and the offer review/approve/send workflow. Plutus is the OFFERS
worker: it RECOMMENDS print/album bundles — nothing else. It must be a stateless, contract-true
recommendation worker.

CRITICAL — do NOT reproduce or keep: signup, tenants, subscriptions, Stripe/checkout, a
storefront, or a separate billing/identity layer (Mise owns all of that). CRITICAL — money
guardrail: offers are proposals only; never charge, send, or invoice.

Bring Plutus into alignment:
1. Structured output: {"run_id","estimated_total_cents","offer_url","pitch_url",
   "bundles":[{"sku","label","estimated_cents","line_items":[{"label","qty","unit_cents"}]}]}.
   The stable `sku` + `line_items` are the headline — they let an accepted offer link to an
   invoice line so Mise can attribute REAL upsell revenue (treat as P1).
2. Provenance + cost: model, latency_ms, cost_usd per run.
3. Callback: POST to Mise at /api/plutus/callback?gallery_id=<id> with a bearer service token;
   echo correlation_id; unknown subject = no-op.
4. Idempotency: ONE stable offer per gallery; a re-run never duplicates.
5. Stateless / retire-ready: recommendation run cache only; strip SaaS surfaces; RETIRE.md.
6. Auth robustness: fix any service-token drift / 401 fragility.
7. Resilience & CI: failures swallowed + recorded; /healthz; mock-only / reproducible CI.

Process: claude/... branches; draft PRs a human merges; backward-compatible; independently
green; no secrets; no live calls in CI.

First response: gap analysis vs the 7 points (explicitly flag any charge/send/invoice/SaaS
surfaces), a ranked plan, and the SKU + line_item data model and how it links to a Mise invoice
line — then wait for go.
````

---

## Mode B — discrete sequence

### #1 — Structured output + SKU/line-item linkage + cost + CLAUDE.md  *(the scorecard unlock)*
````
Make Plutus emit strict JSON: {"run_id":<id>,"estimated_total_cents":<int>,"offer_url":"...",
"pitch_url":"...","bundles":[{"sku":"<stable id>","label":"...","estimated_cents":<int>,
"line_items":[{"label":"...","qty":<int>,"unit_cents":<int>}]}]}, plus cost_usd + latency_ms.
The stable per-bundle `sku` and the line_items are the point: they must map 1:1 to the invoice
lines a human later creates in Mise, so an accepted offer can be attributed to real revenue.
Offers stay proposal-only — never charge/send/invoice. Keep the existing recommend path working.
Mise validates this shape and records it. Plan first; draft PR on a claude/ branch; mock-only
CI; wait for go.

Also include a CLAUDE.md at the repo root in the same PR. It should capture: Plutus's role as
Mise's OFFERS worker (print/album bundle recommendations — NEVER charge, send, or invoice);
the 7-point worker contract in brief; the branch/PR convention (claude/ branches, draft PRs,
never push to main); mock-only CI rule; and Plutus-specific money guardrails (proposals only,
no Stripe/checkout/billing surface, stable SKU maps to Mise invoice line). This becomes the
bootstrap for every future Claude Code session in this repo.
````

### #2 — Idempotency + auth robustness  *(one offer per gallery; the 401 fix)*
````
Harden Plutus's Mise integration. (1) Idempotency: exactly ONE stable offer per gallery — a
re-run/retry/re-delivery must not create a second offer or duplicate bundles; include a stable
run_id + idempotency key. (2) Correlation: echo any correlation_id Mise sent. (3) Auth: bearer
service token on the /api/plutus/callback path; on 401 attempt one re-auth/refresh + retry, and
on hard failure record + surface (never silently drop a completed recommendation). (4) Delivery:
retry transient failures with backoff, then dead-letter (re-deliverable); unknown subject =
no-op. Acceptance: re-delivery never duplicates the offer; correlation round-trips; rotated
token re-auths or is surfaced; CI (mock-only) covers idempotency, the 401 path, retry. Plan
first; draft PR; wait for go.
````

### #3 — Statelessness / strip SaaS + RETIRE.md
````
Make Plutus a stateless recommendation worker and retire-ready. Identify and REMOVE/disable any
SaaS surface Mise already owns: signup, tenants, subscriptions, Stripe/checkout, storefront,
separate billing/identity. Reduce persistence to a recommendation run cache + reproducible
outputs — no authoritative client/offer/money state. Add RETIRE.md documenting what Mise owns,
what is safe to turn off, and the rollback. Explicitly confirm NOTHING in Plutus can charge,
send, or invoice. Plan first; draft PR; wait for go.
````

### #4 — /healthz + mock-only CI + money-guardrail tests
````
Finalize the operational + safety contract. Expose /healthz. Ensure CI is mock-only /
reproducible (no live model/API calls). Add tests that: validate Plutus output against the
offers schema (run_id, bundles with stable sku, estimated_total_cents); assert idempotency (one
offer per gallery on re-run); and assert the money guardrail — there is NO code path that
charges, sends to a client, or creates an invoice. Plan first; draft PR; wait for go.
````
