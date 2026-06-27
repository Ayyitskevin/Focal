# Argus brief — vision worker

**Role:** the VISION worker (keywords, alt text, IPTC, culling/hero signals). Goal: a
stateless, contract-true worker that emits the structured vision schema, supports a local Qwen
model alongside cloud Grok (reversibly), and that Mise can compare against Qwen and eventually
retire. Conforms to [`../WORKER-CONTRACT.md`](../WORKER-CONTRACT.md) +
[`../../schemas/vision.schema.json`](../../schemas/vision.schema.json).

Cutover note: **after discrete #2 merges you can already run the vision cutover** (point Mise
at local Qwen, eyeball `/admin/vision-cutover`, flip `MISE_VISION_PROVIDER=qwen`). #3–#6 harden
Argus but aren't blockers for going live.

---

## Mode A — umbrella (self-sequencing)

````
You are improving the **Argus** repo to fit the vision of "Mise Solo Studio OS." Do a
review → gap-analysis → prioritized plan → incremental draft PRs. Deliver the audit and plan
first; wait for go before large changes.

Mise is a single-tenant modular monolith consolidating its photography-AI sidecars behind one
provider facade ("consolidate the chassis, not the engines"). Mise owns ALL business state;
Argus is the VISION worker and must become a stateless, reproducible, contract-true worker —
never a second source of truth. Near-term direction: vision moves to a local Qwen3-VL (32B) on
an OpenAI-compatible endpoint (Ollama), replacing cloud Grok, as a reversible, measured cutover
(not a blind swap). So make the model provider-configurable, defaulting to current behavior.

Bring Argus into full alignment with this contract:
1. Structured output: strict JSON per photo
   {"photos":[{"basename","keywords":[],"alt_text","keeper_score":0-1,"hero_potential":0-1}]}.
   basename required; scores in [0,1]. Both Grok and Qwen emit the IDENTICAL shape.
2. Provenance + cost: report model, latency_ms, cost_usd (0 for local) per analysis.
3. Callback: POST results to Mise at /api/argus/callback?gallery_id=<id> with a bearer service
   token; echo any correlation_id; unknown subject = no-op.
4. Idempotency: one stable result per (gallery, run); retries never duplicate.
5. Stateless / retire-ready: run cache + reproducible outputs only; no authoritative state.
6. Privacy & spend: send downsized web derivatives (not originals), capped count; local Qwen
   endpoint is trusted/local-only; cloud is opt-in.
7. Resilience & CI: failures swallowed + recorded (never crash Mise's publish path); /healthz;
   mock-only / reproducible CI (no live model calls).

Process: develop on claude/... branches; each change a draft PR a human merges;
backward-compatible defaults; independently green; no secrets; no live calls in CI.

First response: deliver a gap analysis vs the 7 points, a ranked plan of small PRs, and the
Grok↔Qwen config design — then wait for go before implementing beyond the first PR.
````

---

## Mode B — discrete sequence

### #1 — Structured output + cost + CLAUDE.md  *(the cutover unblocker)*
````
Add a structured-output mode to Argus: every analysis emits strict JSON per photo —
{"photos":[{"basename":"<exact file name>","keywords":["..."],"alt_text":"one line or null",
"keeper_score":0.0-1.0 or null,"hero_potential":0.0-1.0 or null}]} — and the callback to Mise
(/api/argus/callback) includes cost_usd + latency_ms. basename required; scores in [0,1].
Keep the existing output path working; add this alongside it. Mise validates this shape
deterministically and rejects malformed/out-of-range replies, so be strict. Propose a plan
first, then implement on a claude/ branch as a draft PR; mock-only CI; wait for go.

Also include a CLAUDE.md at the repo root in the same PR. It should capture: Argus's role as
Mise's VISION worker (keywords, alt text, culling/hero signals); the 7-point worker contract in
brief (structured output, provenance+cost, signed callback, idempotency, statelessness,
privacy/spend, health/CI); the branch/PR convention (claude/ branches, draft PRs, never push to
main); mock-only CI rule; and any Argus-specific gotchas (e.g. never send originals — web
derivatives only; local Qwen endpoint is trusted-only). This becomes the bootstrap for every
future Claude Code session in this repo.
````

### #2 — Configurable Grok|Qwen provider  *(reversible)*
````
Make Argus's vision model a configurable provider, fully reversible: `grok` (current cloud
path, DEFAULT) or `qwen` (local Qwen3-VL:32b on an OpenAI-compatible endpoint, e.g.
http://mickeybot:11434/v1), selected by config/env (follow the repo's config conventions, e.g.
ARGUS_VISION_PROVIDER=grok|qwen plus endpoint/model/token/timeout). Do NOT remove Grok — it
stays default and rollback. Both providers emit the IDENTICAL structured output from #1 +
cost_usd (real for Grok, 0 for local). For Qwen, POST to {base}/chat/completions with photos as
base64 web-derivatives + the structured prompt, temperature 0, strict JSON; parse strictly.
Privacy: web derivatives only, capped; local endpoint trusted-only. A provider failure is
recorded, never crashes the flow. Acceptance: default=grok is byte-identical to today; qwen
runs against the local endpoint and emits the same schema; switching is one env change;
CI exercises BOTH paths with MOCKED endpoints. Plan first; one focused draft PR; wait for go.
````

### #3 — Idempotency + signed callbacks + auth robustness  *(the 401 fix)*
````
Harden Argus's callback contract. (1) Idempotency: one stable result per (gallery_id, run);
include a stable run_id + idempotency key so a re-run/retry/re-delivery never creates a second
result or double-write. (2) Correlation: echo any correlation_id Mise sent. (3) Auth: bearer
service token; on 401 attempt one re-auth/refresh + retry, and on hard failure record + surface
it (log/alert) — NEVER silently drop a completed run. (4) Delivery: retry transient failures
with exponential backoff, then dead-letter locally (persist, re-deliverable) rather than lose
it; unknown subject = no-op. (5) Status: report queued/done/error consistently; failures never
crash Mise's path. Acceptance: re-delivery doesn't duplicate; correlation round-trips; a
rotated token re-auths or is surfaced (no data loss); transient failures retried then
dead-lettered. CI (mock-only) covers idempotency, correlation, the 401 path, retry/dead-letter.
Plan first; draft PR; wait for go.
````

### #4 — Statelessness / retire-readiness + RETIRE.md
````
Make Argus retire-ready (Mise owns the authority now). Audit for any business state Argus
treats as a source of truth (run store, status, client/gallery data) and reduce it to a run
cache + reproducible outputs only — Mise holds the signals and the review surface. Strip any
duplicated auth/UI/deploy that exists only because Argus was standalone. Add a RETIRE.md
documenting: what Mise now owns, what is safe to turn off, and the exact rollback (Mise's
MISE_VISION_PROVIDER=argus flag + the Argus URL/token still valid). Nothing here should change
runtime behavior — it's a statelessness audit + cleanup + docs. Plan first; draft PR; wait for
go.
````

### #5 — Privacy hardening + /healthz + mock-only CI + tests
````
Finalize the operational contract. (1) Privacy: confirm Argus only ever sends downsized WEB
derivatives (never originals), capped by a configurable max-images, and never routes client
media to an unapproved cloud endpoint. (2) Expose /healthz (readiness). (3) CI: ensure it is
mock-only / reproducible — no live model calls — and add tests that validate Argus's output
against the structured vision schema (keys present, scores in [0,1], basename required) for
BOTH provider paths. Plan first; draft PR; wait for go.
````

### #6 — Self-review pass *(optional, before trusting it in the cutover)*
````
Re-audit everything changed in #1–#5 against the 7-point worker contract (structured output,
provenance+cost, callback, idempotency, statelessness, privacy/spend, resilience/CI). List any
remaining gaps or regressions, propose fixes as small draft PRs, and confirm the default Grok
path is still byte-identical to the original. Report findings first; wait for go before fixing.
````
