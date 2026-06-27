# Mise Worker Contract — how a sibling becomes a useful OS worker

> The single contract every photography-AI sibling (Argus, Plutus, Odysseus, Dionysus,
> Mnemosyne, Aphrodite) conforms to so Mise can **drive it, compare it, meter it, and retire
> it**. This is the "consolidate the chassis, not the engines" rule (audit §3.3, §19.3) made
> concrete: don't merge codebases — make each engine a clean, stateless, contract-true worker
> behind the Mise provider facade (`app/providers/`).

Every claim here is pinned to Mise-side code so a worker author knows exactly what Mise
expects. Machine-checkable output shapes live in [`../schemas/`](../schemas/).

---

## 1. The result shape

Every worker call resolves to one normalized `ProviderResult`
(`app/providers/contracts.py`): `capability, provider, status, review, output, model,
latency_ms, cost_usd, tokens, error`.

- **`status`** ∈ `ok | disabled | provider_error | invalid_response`. **Only `ok` may carry a
  usable `output` and drive a write.** Every other status is non-mutating by contract — a
  provider failure must never become a business-state failure.
- **`review`** ∈ `none | human_review | explicit_commit`. Vision/content/albums are
  `human_review` (A1 draft); offers and products are `explicit_commit` (money / client-facing).
- **`output`** is the already-normalized, capability-specific payload — never raw provider
  JSON, never authoritative state. Its shape is the matching `schemas/*.schema.json`.
- **provenance** (`model`, `latency_ms`, `cost_usd`, `tokens`) is what Mise persists to the
  `ai_runs` ledger (`app/ai_runs.py`, migration 065) and surfaces in the AI cost report and
  ops dashboard. **Report `cost_usd` per call** — it is the COGS signal the retire-gate uses.

## 2. Stateless by design

A worker is a **stateless, reproducible worker**, not a second authority.

- No business state: no clients, galleries, invoices, offers, or "last status" of its own as a
  source of truth — Mise owns those. Keep only a run cache + reproducible outputs.
- Mise is the sole transaction authority (ADR 0002). A worker proposes; Mise's deterministic
  caller performs every authoritative write on an `ok` result.
- This is what lets a sibling's DB / UI / auth be **retired** at its decommission gate without
  data loss (the matrix "infrastructure to discard" column).

## 3. Idempotency

- **One stable result per (capability, subject, idempotency key).** A retry must not duplicate
  — e.g. Plutus emits *one* offer per gallery; re-running does not create a second
  (`tests/test_smoke_plutus.py`). Argus enqueues exactly one analyze per publish.
- Echo the `correlation_id` Mise sends so paired shadow runs link in the ledger
  (`app/vision_shadow.py` links the legacy + challenger rows by correlation id).

## 4. Callbacks & auth

- Async workers call back to Mise's service API (`app/service_api.py`):
  `POST /api/argus/callback`, `POST /api/plutus/callback`, with `gallery_id` and a result body
  matching the capability schema.
- Authenticate with the Mise **service token** (bearer). Token drift is a real outage class —
  the Plutus 401 the audit flagged — so treat the service-token register as part of the
  contract, not an afterthought.
- A callback for an unknown/again subject is a no-op, never an error (callbacks are
  best-effort; Mise records last status and never crashes the publish path).

## 5. Structured output

- Emit **strict JSON** matching the capability schema in [`../schemas/`](../schemas/):
  `vision`, `offers`, `albums`, `products`. Tolerate nothing fuzzy on Mise's side — Mise
  validates deterministically (`app/qwen_writeback.parse_structured`,
  `app/albums.validate_core`, `app/products.create_render`) and **rejects** a malformed or
  out-of-range payload rather than guessing.
- For **vision specifically**: Argus and the Qwen3-VL challenger must emit the *same*
  `vision.schema.json` so the validation gate compares like with like — this is the single
  change that unblocks the cutover (ADR 0016/0017).

## 6. Privacy & spend posture

- **Local-only vision** (audit §13.4): the vision challenger points at a trusted *local*
  endpoint; it sends downsized **web derivatives**, never originals, capped by
  `MISE_VISION_CHALLENGER_MAX_IMAGES`. Cloud vision by default is intentionally unsupported.
- **Spend guards** (products, audit §13.5): report real `cost_usd`; Mise hard-caps cumulative
  spend (`MISE_PRODUCTS_BUDGET_USD`) and refuses a render that would exceed it. No automatic
  client publication; export is human-approved + consent-confirmed.

## 7. Health & CI

- Expose `/healthz` (readiness) so Mise's ops evidence (`/healthz`, `app/ops_monitor.py`) can
  see the worker.
- CI is **mock-only / reproducible** — no live model calls in tests (the Argus dogfood
  standard). Outputs must be deterministic enough to validate against the schemas.

---

## The shared-SDK opportunity

Sections 1–7 are identical for every worker. That repetition is the argument for a small
**shared worker SDK** — one library that implements the `ProviderResult` emission, idempotency
keys, signed callbacks, provenance, schema validation, and `/healthz` once, so each sibling
adopts it instead of re-deriving it. Build it once; "consolidate the chassis" then falls out
as a side effect. See [`SIBLING-IMPROVEMENT-PLAN.md`](SIBLING-IMPROVEMENT-PLAN.md) for the
per-repo, prioritized rollout.
