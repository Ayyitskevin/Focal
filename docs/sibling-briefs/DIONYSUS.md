# Dionysus brief — content worker

**Role:** the CONTENT worker (campaign packs, gallery descriptions, captions/alt text,
print-pitch enrichment, blog/email/social drafts). Goal: a stateless content worker whose
generation calls a **local** model endpoint and whose every output is a reversible draft a
human accepts. Conforms to [`../WORKER-CONTRACT.md`](../WORKER-CONTRACT.md).

**Content guardrail (every step):** every output is a reversible DRAFT — never auto-publish,
never overwrite human-edited content, and on failure leave prior content untouched.

**Open question to settle in #1's audit:** is Dionysus still needed, or could its drafts be
produced by the same local content endpoint Mise already calls for captions? If redundant,
propose a **retirement path** instead of more features.

---

## Mode A — umbrella (self-sequencing)

````
You are improving the **Dionysus** repo to fit the vision of "Mise Solo Studio OS." Do a
review → gap-analysis → prioritized plan → incremental draft PRs. Deliver the audit and plan
first; wait for go before large changes.

Mise is a single-tenant modular monolith that owns business state and the human review/accept
workflow. Dionysus is the CONTENT worker: campaign packs, gallery descriptions, captions/alt
text, pitch enrichment, blog/email/social drafts. It must be a stateless, contract-true content
worker — not a second source of truth and not a SaaS. Generation should call a LOCAL model
endpoint (the same local AI already self-hosted for captions); make the model/endpoint
configurable; don't hard-depend on a cloud model.

CRITICAL — content guardrail: every output is a reversible DRAFT a human must accept; never
auto-publish; never overwrite content a human has edited (honor a human-edited guard); on
failure leave prior content untouched.

Bring Dionysus into alignment:
1. Structured output: {"drafts":[{"kind":"caption|gallery_description|campaign_pack|email|
   social","title":"optional","body":"draft text","alt_text":"optional"}],"model","cost_usd"}.
2. Provenance + cost: model, latency_ms, cost_usd (0 for local).
3. Mise integration: conform to the content API Mise uses (today a Platekit-style base+token;
   Mise records platekit_last_*); echo correlation_id; unknown subject = no-op.
4. Idempotency: stable output per (subject, request); retries don't duplicate.
5. Stateless / retire-ready: run cache only; strip signup/tenant/billing/workspace; RETIRE.md.
6. Draft-only + human-edit guard enforced in code; failure writes nothing.
7. Resilience & CI: failures swallowed + recorded; /healthz; mock-only / reproducible CI.

Also assess HONESTLY whether Dionysus is still needed or whether the local caption endpoint
already covers its output — if redundant, propose a retirement path.

Process: claude/... branches; draft PRs a human merges; backward-compatible; independently
green; no secrets; no live model calls in CI.

First response: gap analysis vs the 7 points; a ranked plan; a clear keep-vs-retire
recommendation with evidence; the local model/endpoint config — then wait for go.
````

---

## Mode B — discrete sequence

### #1 — Audit + keep-or-retire + structured drafts on a local model + CLAUDE.md
````
Audit Dionysus against the Mise content contract and decide keep-vs-retire FIRST: could its
drafts be produced by the same local content/caption endpoint Mise already calls? If yes,
deliver a retirement path. If keep: emit strict JSON drafts —
{"drafts":[{"kind":"caption|gallery_description|campaign_pack|email|social","title":"optional",
"body":"draft text","alt_text":"optional"}],"model":"...","cost_usd":0.0} — generated via a
CONFIGURABLE LOCAL model endpoint (no hard cloud dependency), with cost/latency. Keep the
existing path working. Deliver the audit + keep/retire recommendation + plan FIRST; wait for go.

Also include a CLAUDE.md at the repo root in the same PR. It should capture: Dionysus's role as
Mise's CONTENT worker (drafts only — never auto-publish, never overwrite human-edited content);
the keep-vs-retire context (alignment is done; Dionysus is retained only if it adds value over
the local caption endpoint); the 7-point worker contract in brief; the branch/PR convention
(claude/ branches, draft PRs, never push to main); and the content guardrail (reversible draft,
human-edit guard in code, failure writes nothing). This becomes the bootstrap for every future
Claude Code session in this repo.
````

### #2 — Draft-only + human-edit guard (the safety core)
````
Enforce the content guardrail in code: every Dionysus output is a reversible DRAFT — it must
never auto-publish, must never overwrite content a human has already edited (honor a
"human-edited" flag/guard), and on any failure must leave prior content untouched (write
nothing). Add tests proving: no publish path exists; a human-edited body is never clobbered; a
generation failure leaves existing content intact. Plan first; draft PR; wait for go.
````

### #3 — Statelessness / strip SaaS + RETIRE.md + /healthz + CI
````
Make Dionysus stateless and retire-ready: reduce persistence to a run cache + reproducible
outputs; strip any signup/tenant/billing/workspace layer Mise already owns. Add RETIRE.md (what
Mise owns, what's safe to turn off, rollback). Expose /healthz. Ensure CI is mock-only /
reproducible (no live model calls) and add tests validating draft output shape + idempotency
(stable output per subject/request). Plan first; draft PR; wait for go.
````
