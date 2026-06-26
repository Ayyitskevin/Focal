# Mise Solo Studio OS — operator runbook

How to run the consolidated photography-AI stack day to day: the surfaces you open, the
flags that arm each capability, the review workflows, and how to roll anything back. This is
the operator-facing companion to the architecture docs — see
[`MISE-SOLO-STUDIO-OS.md`](MISE-SOLO-STUDIO-OS.md) for the why,
[`MISE-CONSOLIDATION-ROADMAP.md`](MISE-CONSOLIDATION-ROADMAP.md) for the plan, and
[`adr/`](adr/README.md) for the decisions (ADRs 0001–0014).

> **Single rule that governs everything here:** the model proposes, deterministic code
> validates, **a human approves**. Nothing on these surfaces sends a client message, charges
> a card, creates an invoice, prints an album, or promotes a model on its own. Every such act
> is an explicit human action, and every AI output is at least a reviewable draft (audit
> §11.4, ADR 0006).

---

## 1. Start here every morning — `/admin/ai-ops`

`/admin/ai-ops` is the one pane over every AI capability (ADR 0013). It shows, with links
straight to the queue that owns each action:

- **Offers awaiting a decision** — count + proposed value → `/admin/offers?decision=undecided`
- **Album drafts to review** → `/admin/albums?status=draft`
- **Vision promotion gate** — ready / not-ready + paired coverage → `/admin/validation`
- **Provider errors in the ledger** → `/admin/ai-runs`
- **Ledger summary** — runs, last-7-day volume, reported cost, per-capability breakdown

It is read-only: act in the linked queue, where the human-review gate lives. (Everything is
reachable from the ⌘K command palette too.)

---

## 2. The capabilities, and how to arm each

Every capability is **dormant by default** — applying the migrations and deploying changes
nothing until you set the relevant flag. Flags live in flow's `.env`
(see [`.env.example`](../.env.example) for the full annotated list).

### Vision (Argus today; Qwen3-VL challenger) — ADRs 0004, 0007, 0010, 0014

- **What it does:** keywords, alt text, IPTC, culling/hero signals on a gallery's photos.
  Argus (cloud Grok) is the production provider; Qwen3-VL on `mickeybot` is the local
  challenger being evaluated to replace it.
- **Surfaces:** `/admin/ai-runs` (provenance ledger), `/admin/validation` (promotion gate).
- **Arm shadow evaluation** (records a challenger-vs-Argus comparison to the ledger, never
  touches assets):
  1. `MISE_VISION_CHALLENGER_URL` → a **trusted local** OpenAI-compatible endpoint
     (e.g. `http://mickeybot:11434/v1`). Never point this at an unapproved cloud vision API.
  2. `MISE_VISION_CHALLENGER_MODEL` (default `qwen3-vl:32b`), and `…_TOKEN` if your endpoint
     needs one.
  3. `MISE_VISION_SHADOW=true`.
  - With both the URL and `VISION_SHADOW` set, each completed Argus analysis queues a
    ledger-only shadow compare. With either unset it is inert.
- **Rollback:** set `MISE_VISION_SHADOW=false` (or unset the URL). No code change; the
  challenger stops running, Argus is unaffected throughout.
- **The promotion workflow** is in §3.

### Offers (Plutus) — ADRs 0006, 0012

- **What it does:** print/album bundle recommendations after a gallery is analyzed; the
  summary lands on the gallery (`plutus_last_*`).
- **Surface:** `/admin/offers` — a triage queue. Approve the offers worth pursuing, reject
  the rest (persisted; migration 068). Filter by status (ready/error) and by decision
  (undecided/approved/rejected). The header shows **proposed** vs **approved** pipeline value.
- **Send (approved offers):** an approved offer with a client email on file shows **Send to
  client** → a compose page with an editable draft (a warm note + the offer link only;
  pricing stays on the offer page). You review/edit and click Send; it goes out through the
  same Gmail path as proposals/invoices, is recorded in the email log, and the offer is
  marked **Sent** (migration 069). Nothing auto-sends — the button only appears for a ready,
  approved offer, and the email is a draft until you send it. ADR 0018.
- **Arm:** `MISE_PLUTUS_URL` + `MISE_PLUTUS_TOKEN` (proposals). Sending also needs Gmail
  configured (`MISE_GMAIL_USER` / `MISE_GMAIL_APP_PASSWORD`). Optional Phase-3 facade
  provenance into the ledger: `MISE_PROVIDER_FACADE_OFFERS=true` (legacy path runs unchanged
  when off).
- **Bounded:** approving records your call; sending emails the **link** only. Neither charges
  the client nor creates an invoice — acceptance flows through your existing, human-initiated
  invoice workflow. The money path is never touched by an AI proposal.
- **Rollback:** `MISE_PROVIDER_FACADE_OFFERS=false` reverts to the legacy path; clearing the
  Plutus URL/token makes offers dormant. Migration 069 is additive (see §6).

### Albums (Mnemosyne) — ADRs 0009, 0011

- **What it does:** proposes a curated, ordered subset of a gallery's photos laid into
  spreads. A deterministic baseline proposer ships today; a Mnemosyne model can register on
  the same seam later.
- **Surface:** `/admin/albums` — propose a baseline for a gallery, review the spreads (with
  thumbnails) and the **omitted** photos, then approve or reject.
- **Arm:** nothing to arm — the baseline proposer is always available. The deterministic
  validator guarantees a draft never silently omits, duplicates, or misassigns a photo, and
  refuses to store one that would.
- **Bounded:** approval records your decision; it does **not** print or order an album.
- **Rollback:** the tables (migration 066) are additive and dormant; see §6.

### Content / captions (Odysseus, Dionysus) — ADR 0006

- **What it does:** caption / copy drafts (Odysseus) and pack-draft provenance (Dionysus).
- **Arm:** `MISE_PROVIDER_FACADE_CONTENT=true` routes caption drafting through the facade and
  records Dionysus pack-draft outcomes to the ledger (additive, ledger-only). Off = legacy
  behavior, no ledger rows.
- **Bounded:** every caption is a draft requiring your approval where it's used.

---

## 3. Promoting the vision challenger over Argus (the gate workflow)

This is the one workflow that decides a provider cutover. It is deliberately
human-scored and never automatic (ADR 0010).

1. **Arm shadow** (§2, Vision) so comparisons accumulate in the ledger as galleries are
   analyzed. Check they're landing at `/admin/ai-runs` (filter: Vision; look for ⇄ shadow
   pairs).
2. **Enrol** the galleries to evaluate. On `/admin/validation`, the **"From vision shadow
   runs"** section lists shadowed galleries not yet in the set — click **Add to set** on a
   representative spread of them (ADR 0014). Keep the set *representative*, not exhaustive.
3. **Score** each enrolled item 0.00–1.00 for both `argus` and the challenger, by your own
   judgement of output quality. Either side can be scored now and the other later.
4. **Read the verdict.** The gate is **Ready** only when paired coverage ≥
   `MISE_VALIDATION_MIN_PAIRED` (default 20) **and** the challenger's mean quality clears
   Argus's by ≥ `MISE_VALIDATION_PARITY_MARGIN` (default 0.0 = parity). Cost/latency are shown
   but never auto-decide.
5. **Promote — manually.** Even a green verdict flips nothing. A vision cutover is a separate,
   deliberately-reviewed change; the gate only tells you the evidence supports it.

Tune the bar without code via `MISE_VALIDATION_MIN_PAIRED` / `MISE_VALIDATION_PARITY_MARGIN`.

### 3.1 Executing the cutover — `/admin/vision-cutover` (ADRs 0016, 0017)

When the gate is green, `/admin/vision-cutover` is the cockpit that turns promotion from
"remember four steps" into a checklist. It shows a **readiness checklist** (each remaining
condition + the exact next step), an asset-safe **dry-run preview** (Qwen's parsed per-photo
signals for a gallery, *written nowhere* — the prompt-tuning loop), and a manual **writeback**
the interlock refuses until Qwen is promoted. It flips nothing itself. The remaining steps it
tracks, in order:

1. **Endpoint** — `MISE_VISION_CHALLENGER_URL` points at the trusted local Qwen endpoint.
2. **Tune + writeback** — dry-run a few galleries on the page, adjust `STRUCTURED_PROMPT`
   until the parsed signals look right, then (a reviewed code change) set
   `InternalVisionChallengerAdapter.serves_production = True`.
3. **Flag** — set `MISE_VISION_PROVIDER=qwen`.
4. **Gate** — the validation gate (§3) is green.

When all four hold, the interlock makes Qwen the eligible production provider and the writeback
runs when triggered. **Rollback is the flag** — set `MISE_VISION_PROVIDER=argus` (or revert
`serves_production`); Argus assets are re-writeable from its last run.

---

## 4. Notion API modernization

The Notion adapter defaults to the legacy `2022-06-28` behavior and is unaffected until you
arm it. The controlled cutover to `2025-09-03` (data-source model) is its own runbook:
[`NOTION-MODERNIZATION.md`](NOTION-MODERNIZATION.md). Rollback is a single env var
(`MISE_NOTION_VERSION` back to `2022-06-28`). ADR 0008.

---

## 5. Flag reference

| Env var | Default | Effect |
| --- | --- | --- |
| `MISE_PROVIDER_FACADE_CONTENT` | `false` | Route captions through the facade + Dionysus pack provenance to the ledger |
| `MISE_VISION_SHADOW` | `false` | Shadow-compare a challenger vs Argus into the ledger (needs a challenger URL) |
| `MISE_VISION_CHALLENGER_URL` | — | Trusted **local** OpenAI-compatible endpoint for the challenger |
| `MISE_VISION_CHALLENGER_MODEL` | `qwen3-vl:32b` | Challenger model id |
| `MISE_VISION_CHALLENGER_TOKEN` | — | Auth token for the endpoint, if needed |
| `MISE_VISION_CHALLENGER_TIMEOUT` | `120` | Per-call timeout (s) |
| `MISE_VISION_CHALLENGER_MAX_IMAGES` | `4` | Cap on downsized web derivatives sent per gallery (data minimization) |
| `MISE_VISION_PROVIDER` | `argus` | Which provider serves **production** vision. `qwen` is honored only once the interlock is satisfied (see §3.1); otherwise it falls back to Argus and logs why |
| `MISE_VALIDATION_MIN_PAIRED` | `20` | Paired scores required before the gate evaluates parity |
| `MISE_VALIDATION_PARITY_MARGIN` | `0.0` | How far the challenger must clear the baseline (0 = parity) |
| `MISE_PROVIDER_FACADE_OFFERS` | `false` | Route Plutus offers through the facade + ledger provenance |
| `MISE_NOTION_VERSION` | `2022-06-28` | Notion API version (set `2025-09-03` only after staging validation) |
| `MISE_NOTION_BOOKINGS_DS` / `…_SESSIONS_DS` | — | Data-source ids for the 2025-09-03 create path |

Provider arming (Argus / Plutus / Odysseus URLs + tokens) and all other settings are in
[`.env.example`](../.env.example).

---

## 6. Migrations & rollback

All consolidation migrations are **additive and forward-only** — applying them with the
feature dormant changes nothing. Run via the app's normal `db.migrate()` on boot.

| Migration | Adds | Rollback |
| --- | --- | --- |
| `065_ai_runs.sql` | `ai_runs` provenance ledger | `rollback/065_ai_runs.sql` (drops the table) |
| `066_album_drafts.sql` | `album_drafts` + `album_placements` | `rollback/066_album_drafts.sql` |
| `067_validation_set.sql` | `validation_items` + `validation_scores` | `rollback/067_validation_set.sql` |
| `068_plutus_offer_decision.sql` | `galleries.plutus_offer_decision` + `…_decided_at` | `rollback/068…` (DROP COLUMN; SQLite ≥3.35) |
| `069_plutus_offer_sent.sql` | `galleries.plutus_offer_sent_at` + `…_sent_to` | `rollback/069…` (DROP COLUMN; SQLite ≥3.35) |

Each rollback is safe because the tables/columns are dormant and referenced by no money,
invoice, or business record. Rolling a feature back is normally a **flag**, not a migration —
reach for a rollback script only to remove the schema itself.

---

## 7. Verifying changes (the gates)

From the repo root (see `AGENTS.md`):

```sh
# unit (fast, no DB/network)
python -m pytest tests/ --ignore=tests/test_smoke.py -q -m unit
# smoke (the CI gate; uses a throwaway DB)
MISE_DATA_DIR=$(mktemp -d) MISE_SECRET_KEY=test MISE_ADMIN_PASSWORD=pw python -m pytest tests/test_smoke.py -q
# lint + format
ruff check . && ruff format --check .
```

CI runs the `-m unit` gate and `tests/test_smoke.py`. Topic smoke files
(`tests/test_smoke_*.py`) are DB-backed and run locally with the same env-var prefix.

---

## 8. Safety invariants (do not cross without a deliberate, reviewed change)

- **Money path is sacred.** No AI output sends a message, charges, invoices, or checks out.
  Offer approval and album approval record a decision only.
- **Human approval gate.** Captions, offers, album layouts, and any model output are drafts
  until you accept them. Provider promotion is manual even when the gate is green.
- **Provider failure ≠ business-state failure.** A disabled/errored/invalid provider call
  records a ledger row and writes nothing else (ADR 0006).
- **The ledger carries metadata only** — provider, model, status, latency, cost — never the
  AI output payload and never a secret.
- **Local-only challenger posture.** The vision challenger URL must be a trusted local
  endpoint; it sends downsized web derivatives, never originals, capped by `…_MAX_IMAGES`.
- **Red-light changes** (schema, money path, auth/CSRF, deploy, contracts) ship as a reviewed
  PR a human merges — never self-applied.

---

## 9. Operating checklist

**Daily:** open `/admin/ai-ops`; clear undecided offers; review pending album drafts; glance
at provider errors.

**While evaluating vision:** confirm shadow pairs are landing in the ledger; enrol a
representative set; score steadily; watch the gate verdict and the cost/latency line.

**Operational (yours, not in code):** arm Qwen on `mickeybot`; validate the Notion
`2025-09-03` cutover on a staging workspace before production; keep an eye on the ledger's
reported cost as a COGS signal; ensure the geo/immutable backup + UPS posture the audit calls
for.

---

*This runbook is living — when a capability is armed, promoted, or a new one is integrated,
update the relevant section and add/observe the matching ADR.*
