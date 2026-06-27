# Aphrodite brief — products render worker

**Role:** the PRODUCTS worker (product-image variant renders from source photos). Goal: a
stateless render worker that reports REAL per-render cost, never auto-publishes, and treats
every render as an explicit-commit draft gated on human approval + consent. Conforms to
[`../WORKER-CONTRACT.md`](../WORKER-CONTRACT.md) +
[`../../schemas/products.schema.json`](../../schemas/products.schema.json).

**Money + rights guardrail (every step):** report real `cost_usd` (Mise hard-caps total spend
and refuses anything over); NEVER publish to a client automatically; every render is a draft a
human approves AND confirms rights/consent on before export (audit §13.5).

**Activation gates (owner decisions, NOT code — flag them, don't assume):** the budget number,
the written consent/licensing policy, and the render backend choice. This brief aligns the
WORKER; Mise keeps products dormant until those three are set. **Lowest urgency** — do after the
products business calls.

---

## Mode A — umbrella (self-sequencing)

````
You are improving the **Aphrodite** repo to fit the vision of "Mise Solo Studio OS." Do a
review → gap-analysis → prioritized plan → incremental draft PRs. Deliver the audit and plan
first; wait for go before large changes.

Mise owns assets and a budget-capped, human-approved, export-gated product-render workflow
(already built as a dormant foundation). Aphrodite is the PRODUCTS worker: it renders
product-image variants from source photos. It must be a stateless, contract-true render worker
— Mise owns the spend cap, the approval/consent gate, and the export step.

CRITICAL — money + rights: report REAL per-render cost (Mise hard-caps total spend and refuses
a render that would exceed it); NEVER publish to a client automatically; every render is an
explicit-commit draft a human approves AND confirms rights/consent on before export. This
capability is intentionally dormant in Mise until the owner sets a budget number, a
consent/licensing policy, and a render backend — flag those three gates; don't assume them.

Bring Aphrodite into alignment:
1. Structured output: {"renders":[{"source_asset_id":<int>,"kind":"...","spec":{...} or "...",
   "output_path":"... or null","cost_usd":<number >= 0>}]}.
2. Real cost: cost_usd is the ACTUAL per-render spend; never under-report.
3. Provenance: model, latency_ms, cost_usd.
4. Idempotency: stable result per (source asset, spec, request); retries don't double-charge.
5. Stateless / retire-ready: render cache + outputs only; budget controls live in Mise;
   RETIRE.md.
6. No auto-publish; consent + export gate (explicit-commit review state).
7. Resilience, spend safety & CI: honor a spend ceiling; fail safe (no partial charge); /healthz;
   mock-only / reproducible CI — NO live generation in tests.

Process: claude/... branches; draft PRs a human merges; backward-compatible; independently
green; no secrets; no live generation in CI.

First response: gap analysis vs the 7 points (flag any auto-publish/uncapped-spend surfaces); a
ranked plan; the cost-reporting + idempotency design; the 3 activation gates — then wait for go.
````

---

## Mode B — discrete sequence

### #1 — Conform: renders schema + REAL cost + spend-safe failure
````
Make Aphrodite emit strict JSON: {"renders":[{"source_asset_id":<int>,"kind":"...",
"spec":{...} or "...","output_path":"... or null","cost_usd":<number >= 0>}]}, where cost_usd
is the ACTUAL per-render spend (never under-report — Mise sums it against a hard cap and refuses
anything over). Add provenance (model/latency_ms/cost_usd). Fail SAFE: on any error, do not
charge / do not emit a partial result. Keep the existing path working. Plan first; draft PR on a
claude/ branch; mock-only CI (no live generation); wait for go.
````

### #2 — Idempotency (no double-charge) + no-auto-publish/consent posture
````
Harden spend + rights safety. (1) Idempotency: stable result per (source_asset_id, spec,
request) — a retry/re-delivery must not duplicate a render or double-charge; include an
idempotency key. (2) Confirm there is NO path that publishes a render to a client automatically;
output is explicit-commit review state, and rights/consent must be confirmed before any export
(Mise enforces this — Aphrodite must not bypass it). Add tests proving no double-charge on
retry and no auto-publish path. Plan first; draft PR; wait for go.
````

### #3 — Statelessness / retire + RETIRE.md + /healthz + budget-guard tests + activation gates
````
Make Aphrodite stateless and retire-ready: render cache + outputs only; the budget cap lives in
Mise, not here. Add RETIRE.md (what Mise owns, what's safe to turn off, rollback). Expose
/healthz. Ensure CI is mock-only / reproducible (NO live generation) and add tests validating
output against the products schema + spend-safe failure (no charge on error). Finally, document
the 3 ACTIVATION GATES as owner decisions, not code: the budget number, the written
consent/licensing policy, and the render backend choice. Plan first; draft PR; wait for go.
````
