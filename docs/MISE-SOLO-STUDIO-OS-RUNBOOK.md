# Mise Solo Studio OS — operator runbook

How to run the consolidated photography-AI stack day to day: the surfaces you open, the
flags that arm each capability, the review workflows, and how to roll anything back. This is
the operator-facing companion to the architecture docs — see
[`MISE-SOLO-STUDIO-OS.md`](MISE-SOLO-STUDIO-OS.md) for the why,
[`MISE-CONSOLIDATION-ROADMAP.md`](MISE-CONSOLIDATION-ROADMAP.md) for the plan, and
[`adr/`](adr/README.md) for the decisions (ADRs 0001–0014).

> **Single rule that governs everything here:** the model proposes, deterministic code
> validates, **a human approves**. Nothing on these surfaces sends a client message, charges
> a card, creates an invoice, hides a photo from a client, or promotes a model on its own.
> Every such act is an explicit human action, and every AI output is at least a reviewable
> draft (audit §11.4, ADR 0006).

---

## Commercial project closeout — `/admin/studio/projects/{id}`

The project page has a **Closeout readiness** panel (ADR 0039) that reconciles the commercial
spine before you close a shoot: shot list, deliverable spec progress, usage licence, invoice,
open AR, linked gallery, and client workspace. It is read-only. Each row links to the surface that
owns the fix, and it never sends, charges, publishes, or changes project status.

The related intake shortcuts are also local/operator-controlled:

- company and client-list cadence cues are derived from past `shoot_date`s and suppress themselves
  once a future shoot is scheduled;
- shot-list templates clone normal audited rows into the project, then you edit them like any other
  shot list;
- the company command view ranks the next few derived actions across money, retainers, active
  project closeout gaps, billing readiness, and repeat-shoot cadence; each item links back to
  its owning surface;
- company billing readiness rolls up AP email, billing address, tax ID, and recipient fallback
  gaps across the root company and venues before drafts or AR chases leave the studio;
- the Studio Activity `Needs attention` panel rolls up the top commercial action per company so
  the morning triage view shows which relationship to open first;
- AR chase assist opens from past-due company/action/invoice links, gathers the statement and
  payable invoice links, and sends only after review. It logs the manual email and never changes
  invoice or payment state.
- AR follow-up cadence is derived from those manual send-log rows: company and Activity surfaces
  show never chased, recently chased, or follow-up due after `MISE_AR_CHASE_FOLLOWUP_DAYS`
  (default seven). It does not schedule or send anything by itself.
- company communication history rolls up recent proposal, contract, invoice, and AR chase sends
  across the company group. It is a scoped read-only view over the existing sent-email log, not a
  new mailbox or task queue.

## 1. Start here every morning — `/admin/ai-ops`

`/admin/ai-ops` is the one pane over every AI capability (ADR 0013). It shows, with links
straight to the queue that owns each action:

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

### Culling (AI-assisted keep/cut) — ADRs 0030, 0031, 0032, 0033

- **What it does:** a fast, keyboard-first cull over the keeper scores Vision already writes
  (`argus_keeper_score`). You step through the gallery best-first and keep/cut each frame; a
  **cut** is hidden from the client (not the studio) and is fully reversible. Nothing is ever
  deleted — `cut` is a flag, the original stays on disk, and `restore` brings the frame back.
- **Surface:** the **Cull** button on a gallery page → `/admin/galleries/{id}/cull`. One big
  card at a time: **K** keep · **X** cut · **H / →** skip · **←** back · **R** undecide · **U**
  undo. A **score-threshold slider** pre-selects the low-scoring tail for one bulk cut, and a
  **triage grid** shows every frame's decision (cut dimmed) — click to jump.
- **Precondition — check which world you're in.** The ranking + threshold are only useful if
  scores exist. Open the deck on a real gallery: if frames show a score badge, Vision is
  scoring you and the AI-assist works. If most read **"unscored"**, Vision isn't analysing your
  galleries (Argus not armed, or the local challenger not promoted) — the deck still works as a
  *manual* keyboard cull, but ranking/threshold do nothing until scores flow. Two ways to get
  scores: arm Vision/Argus (above), or use the **local keeper-scorer** below.
- **Local keeper-scorer (score on your own model).** Set `MISE_CULL_SCORER=true` **and**
  `MISE_VISION_CHALLENGER_URL` to your local Qwen3-VL endpoint, then click **Re-score with local
  AI** on the deck. It scores every photo per-asset in the background (writes `argus_keeper_score`
  only — never keywords/alt-text), so ranking + threshold light up without the cloud. Independent
  of the Argus→Qwen production cutover (§3): scoring to cull does not promote Qwen to serve
  production vision. Off by default; the button 503s until both are set. ADR 0033.
- **Arm:** `MISE_CULL_UI=true`. **One switch** arms both halves — the authoring deck *and* the
  client-delivery gate. Off by default; every cull route 404s and delivery is unchanged until set.
- **What a cut does once armed:** the frame drops out of the client gallery listing, 404s on the
  media + download routes (even a guessed/cached URL), is excluded from the favourites / section /
  full-gallery ZIPs, and disappears from the client portal. The full-gallery ZIP rebuilds on next
  download (a cull decision bumps `content_rev`). The studio side (gallery manage page, the deck
  itself) always shows every frame so you can see and reverse decisions.
- **What it never touches:** the **public marketing site** (governed by the `portfolio` flag, a
  separate publication intent — a cut can't make a portfolio piece vanish) and **transfers/drops**
  (a literal send; no deck). Originals, `delete`, and the money path are all untouched.
- **Rollback:** `MISE_CULL_UI=false` returns every client path to pre-cull delivery exactly, and
  hides the deck — one env var, no code change. Caveat: the *full-gallery ZIP* reflects the cull
  state at its last build (the live listing/media/per-file paths honour the flag immediately); a
  fresh download after a decision rebuilds it. To permanently un-cut a frame, use **restore**, not
  the flag.

> **Decommissioned (ADR 0026, migration 075).** The **Offers (Plutus)** and **Albums
> (Mnemosyne)** consumer-upsell capabilities were removed — print/album bundles and lay-flat
> album layout don't fit a solo B2B food-and-beverage workflow (companies receive licensed
> digital files, not coffee-table books). Their code, admin surfaces, provider-facade
> capabilities, and schema are gone; the historical ADRs (0009/0011/0012/0018–0020/0022/0023)
> are marked Superseded.

### Content / captions (Odysseus, Dionysus) — ADRs 0006, 0068

- **What it does:** caption / copy drafts (Odysseus) and pack-draft provenance (Dionysus).
- **Arm:** `MISE_PROVIDER_FACADE_CONTENT=true` routes caption drafting through the facade and
  records Dionysus pack-draft outcomes to the ledger (additive, ledger-only). Off = legacy
  behavior, no ledger rows.
- **Bounded:** every caption is a draft requiring your approval where it's used.
- **Native Content:** owner list/detail and manual draft saves work without AI. Optional
  asynchronous suggestions require both `MISE_MOBILE_CONTENT_SUGGESTIONS=true` and the iOS
  build flag; both default off. Provider output is immutable, session-bound, no-store, and
  memory-only on iOS until an explicit version-checked save. It cannot approve, publish,
  deliver, invoice, or charge. See
  [`IOS-CONTENT-SUGGESTIONS-OPERATIONS.md`](IOS-CONTENT-SUGGESTIONS-OPERATIONS.md).
- **Transport gate:** Odysseus caption calls now require direct HTTPS and reject redirects;
  HTTP tailnet/LAN endpoints disable the existing web button. Do not arm web or native
  drafting until runtime certificate/no-redirect checks pass staging. Web/native paid-call
  claims with ambiguous outcomes are never automatically replayed; the web claim remains
  blocked until an operator reconciles provider/billing state and clears the exact
  identity/claim-bound row using the Content operations runbook. Any future replay remains
  blocked until provider-side durable `Idempotency-Key` handling is proven.
- **Cost boundary:** Odysseus currently reports no token/cost values to Mise. Per-tenant
  daily quota limits requests, not total spend; configure processor/provider-account budget
  alerts and a hard cutoff before enablement.

### Products (Aphrodite) — ADR 0021 — **foundation only, dormant**

- **What it does (when armed):** renders product-image variants from source photos. Today
  only the **foundation** ships — the deterministic guards, not a render backend.
- **Dormant by default:** with no `MISE_PRODUCTS_RENDER_URL` and a 0 budget, the capability
  is disabled and `products.create_render` refuses everything; nothing in the app calls it.
- **The guards (enforced in code, migration 071):** total spend is **hard-capped** by
  `MISE_PRODUCTS_BUDGET_USD` (a render that would exceed it is refused); every render is a
  **draft** (no automatic client publication); export is refused unless the render is
  **approved AND** rights/**consent confirmed** — the single, human-gated outbound step.
- **Deferred to activation (your calls):** the render backend/worker, what a "product"
  variant is, the budget number, and the written consent/licensing policy. The foundation
  enforces *that* consent is confirmed; *which* policy is yours to set.
- **Rollback:** migration 071 is additive and dormant; see §6.

---

## 3. Promoting the vision challenger over Argus (the gate workflow)

This is the one workflow that decides a provider cutover. It is deliberately
human-scored and never automatic (ADR 0010).

1. **Arm shadow** (§2, Vision; full startup in §3.0) so comparisons accumulate in the ledger
   as galleries are analyzed. Check they're landing at `/admin/ai-runs` (filter: Vision; look
   for ⇄ shadow pairs).
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

### 3.0 Starting shadow mode and accumulating paired data (the precondition phase)

Before the gate (§3) can read **Ready** or the cutover (§3.1) can even be attempted, the
challenger has to run alongside Argus long enough to produce paired, human-scored evidence.
This is its own phase. Shadow recording is **asset-safe** — it writes only `ai_runs` ledger
rows, never assets/galleries, never re-calls Argus, and cannot crash the analyze/publish path.

**What is automatic.** There is **no admin button** for shadow — it auto-enqueues a one-shot
job after **each *completed* Argus analysis** (sync `done` result, or the async completion
webhook `POST /api/argus/callback`). The job snapshots the finished Argus run (no second cloud
call, no extra cost), calls the challenger once with up to `MISE_VISION_CHALLENGER_MAX_IMAGES`
(default 4) downsized **web derivatives**, and records **exactly two `ai_runs` rows per
gallery** (legacy + challenger) linked by `correlation_id` `shadow:gallery:{id}:{run_id}` —
metadata only, **per gallery, not per image**. A *queued* (async) Argus run shadows **later**,
when its callback lands; a run that never completes is never shadowed.

**Arming it needs BOTH (a silent no-op otherwise):**
1. `MISE_VISION_CHALLENGER_URL` — a **trusted local** endpoint (e.g. `http://mickeybot:11434/v1`).
2. `MISE_VISION_SHADOW=true`.

Setting the flag **alone** changes nothing — with no URL the registry resolves no challenger
and the runner no-ops. This is the #1 "I turned it on but nothing happens" trap.

**Apply it (systemd):** edit the production env file, then **restart the service** so systemd
re-reads `EnvironmentFile`. Editing `/opt/mise/.env` does **not** hot-reload — the in-process
read is a deliberate no-op under systemd. No code change; rollback is `MISE_VISION_SHADOW=false`
(or unset the URL), and Argus is unaffected.

**Confirm pairs are landing:** `/admin/ai-runs`, filter **Vision**, look for ⇄ shadow pairs
(`shadow:gallery:…`). Expect **two** rows per gallery. A failed/empty challenger call still
records a **non-OK** second row — failure stays visible, it is not silently dropped.

**How much to accumulate:** pairs accrue only as galleries get a *completed* Argus analysis, so
volume is bounded by analysis throughput and by your manual scoring effort — not elapsed time.
Aim for at least `MISE_VALIDATION_MIN_PAIRED` (default 20) galleries you are willing to **fully
score on both sides**, plus a little headroom. Keep the set *representative*, not exhaustive.

**The bridge is two manual steps (this is the part operators miss):** a shadow run **never**
auto-becomes a scorable item (ADR 0014).
- **Enrol** each gallery — one click **"Add to set"** in the *From vision shadow runs* section
  of `/admin/validation`. This creates an item with **zero** scores, and the gallery then
  **drops off** the candidate list — track which enrolled items still need scoring.
- **Score both sides** — enter a 0.00–1.00 quality number for **`argus` AND the challenger** on
  each item. The gate counts an item as **paired** only when **both** are scored; a one-sided
  or blank score advances nothing. So "shadow ran on N galleries" yields **0** gate-countable
  items until **N enrolments + 2N scores** are entered.

The challenger model string you score must match `MISE_VISION_CHALLENGER_MODEL` (default
`qwen3-vl:32b`) exactly, or those scores are ignored by the gate. Then proceed to §3 step 4
(read the verdict).

**Troubleshooting — flag set but no pairs landing:** (a) URL also set? (b) service restarted
after the env change? (c) a challenger registered? (d) does the gallery have a **completed**
(`done`) Argus run with a non-null run id? (e) does it have web derivatives? Any one missing
makes shadow inert for that gallery.

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

### 3.2 Decommissioning Argus (only after the cutover proves out)

Promotion (§3.1) does **not** mean Argus is gone — it stays the **default and the rollback**
until it has earned retirement. Retiring it is a separate, deliberate step gated on evidence.
Do **not** stop the Argus service until every box below is checked (decommission gate, audit
§16.7 / roadmap Phase 7).

**Gate — all must hold before you stop Argus:**
- [ ] **Parity proven.** The validation gate (§3) read **Ready** — Qwen met or beat Argus on a
  representative, human-scored set — and stayed ready, not a one-off.
- [ ] **Promoted and stable.** `MISE_VISION_PROVIDER=qwen` has served production for an
  **observation period** (≥ ~30 days) with no quality regression you'd act on.
- [ ] **Cost confirmed.** `/admin/ai-cost` shows the expected vision COGS under Qwen (≈0, local)
  vs the Argus baseline — no surprise spend.
- [ ] **Writeback verified.** Spot-check several galleries: `assets.argus_*` (keywords, alt
  text, keeper/hero) are populated by Qwen and look right; the gallery hero set is sane.
- [ ] **Rollback rehearsed.** You've flipped `MISE_VISION_PROVIDER=argus` once on staging/a test
  gallery and confirmed Argus re-takes the path and re-writes from its last run. Confirm the
  Argus URL/token are still valid so the fallback actually works.

**Then retire, in this order (reversible at every step):**
1. **Stop *new* Argus work, keep the service reachable.** Leave `MISE_ARGUS_URL`/`_TOKEN` set so
   the legacy adapter still works as rollback; just rely on Qwen for live analysis.
2. **Observe** for the period above. If anything regresses, `MISE_VISION_PROVIDER=argus` is the
   instant revert — nothing else to undo.
3. **Decommission the Argus *service*** (its own deploy/systemd, its run-store-as-authority, its
   separate UI/auth). Mise already owns the signals and the review surface, so this removes
   infrastructure, not data.
4. **Keep the legacy adapter in Mise** (`LegacyArgusVisionAdapter`) and the Grok path **until**
   you're past the observation period and confident — it is the last rollback. Removing it is a
   final, separate cleanup, not part of the cutover.

**What NOT to do:** don't delete the Argus adapter or unset `MISE_ARGUS_*` in the same change
as the cutover; don't retire the service before the observation period; don't remove the
rollback path until parity has held for the full window. Argus stays the safety net until it's
provably unneeded.

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
| `MISE_MOBILE_CONTENT_SUGGESTIONS` | `false` | Server capability + creation/worker kill switch for native caption suggestions |
| `MISE_MOBILE_CONTENT_DAILY_LIMIT` | `10` | Per-tenant rolling-day request cap; not a global spend ceiling |
| `MISE_MOBILE_CONTENT_CONCURRENT_LIMIT` | `1` | Per-tenant queued/running cap |
| `MISE_MOBILE_CONTENT_SUGGESTION_TTL_HOURS` | `24` | Transient input/output retention, clamped to 1–168 hours |
| `MISE_MOBILE_CONTENT_WORKERS` | `1` | Dedicated content-provider pool, clamped to 1–4; separate from generic jobs |
| `MISE_CULL_UI` | `false` | Arm AI-assisted culling — the keep/cut deck **and** the client-delivery gate (one switch; see §2) |
| `MISE_CULL_SCORER` | `false` | Arm the local keeper-scorer (deck "Re-score with local AI"); needs `MISE_VISION_CHALLENGER_URL`. Writes `argus_keeper_score` only; independent of the cutover |
| `MISE_VISION_SHADOW` | `false` | Shadow-compare a challenger vs Argus into the ledger (needs a challenger URL) |
| `MISE_VISION_CHALLENGER_URL` | — | Trusted **local** OpenAI-compatible endpoint for the challenger |
| `MISE_VISION_CHALLENGER_MODEL` | `qwen3-vl:32b` | Challenger model id |
| `MISE_VISION_CHALLENGER_TOKEN` | — | Auth token for the endpoint, if needed |
| `MISE_VISION_CHALLENGER_TIMEOUT` | `120` | Per-call timeout (s) |
| `MISE_VISION_CHALLENGER_MAX_IMAGES` | `4` | Cap on downsized web derivatives sent per gallery (data minimization) |
| `MISE_VISION_PROVIDER` | `argus` | Which provider serves **production** vision. `qwen` is honored only once the interlock is satisfied (see §3.1); otherwise it falls back to Argus and logs why |
| `MISE_VALIDATION_MIN_PAIRED` | `20` | Paired scores required before the gate evaluates parity |
| `MISE_VALIDATION_PARITY_MARGIN` | `0.0` | How far the challenger must clear the baseline (0 = parity) |
| `MISE_PRODUCTS_RENDER_URL` | — | Aphrodite render backend; unset = products dormant |
| `MISE_PRODUCTS_BUDGET_USD` | `0` | Hard cap on total product-render spend (0 = disabled) |
| `MISE_NOTION_VERSION` | `2022-06-28` | Notion API version (set `2025-09-03` only after staging validation) |
| `MISE_NOTION_BOOKINGS_DS` / `…_SESSIONS_DS` | — | Data-source ids for the 2025-09-03 create path |

Provider arming (Argus / Odysseus URLs + tokens) and all other settings are in
[`.env.example`](../.env.example).

---

## 6. Migrations & rollback

Run migrations through the app's normal `db.migrate()` on boot. Most are additive and
dormant behind flags; the table below calls out destructive decommissioning and the red-light
085 rollback instead of assuming every migration is forward-only.

| Migration | Adds | Rollback |
| --- | --- | --- |
| `065_ai_runs.sql` | `ai_runs` provenance ledger | `rollback/065_ai_runs.sql` (drops the table) |
| `067_validation_set.sql` | `validation_items` + `validation_scores` | `rollback/067_validation_set.sql` |
| `075_decommission_albums_offers.sql` | **drops** `album_drafts`/`album_placements` + 13 `plutus_*` columns (decommission, ADR 0026) | `rollback/075…` (recreates the dropped schema; the features stay gone in code) |
| `077_asset_cull_state.sql` | `assets.cull_state` + `cull_decided_at/cull_source` + index | `rollback/077…` (DROP COLUMN; SQLite ≥3.45) |
| `085_mobile_caption_suggestions.sql` | Caption revisions/identities + web generation claim + immutable DB identity/offboarding barrier + session-bound suggestion operations | `rollback/085…` (atomic; removes its marker so `db.migrate()` can reapply the complete shape) |

Rolling a feature back is normally a **flag**, not a migration. Migration 085 is a red-light
auth/privacy boundary: stop creation, settle jobs, take and restore-check a backup, restore
compatible application code, then run its rollback. Its schema drops and migration-marker
delete share one `BEGIN IMMEDIATE`; failure leaves the applied shape intact, while success
makes the migration deliberately re-applicable. Never delete only the marker. (The
album/offer migrations 066/068/069/070 that 075 reverses are historical — their schema no
longer exists.)

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
- **Human approval gate.** Captions, cull rankings, and any model output are drafts until you
  accept them. Provider promotion is manual even when the gate is green.
- **Provider failure ≠ business-state failure.** A disabled/errored/invalid provider call
  records a ledger row and writes nothing else (ADR 0006).
- **The ledger carries metadata only** — provider, model, status, latency, cost — never the
  AI output payload and never a secret.
- **Local-only challenger posture.** The vision challenger URL must be a trusted local
  endpoint; it sends downsized web derivatives, never originals, capped by `…_MAX_IMAGES`.
- **Culling is reversible and never deletes.** A `cut` is a flag that hides a frame from the
  *client*; the original is untouched and `restore` brings it back. AI only ranks — every keep/cut
  is your keystroke. `MISE_CULL_UI=false` rolls the whole feature back to pre-cull delivery.
- **Red-light changes** (schema, money path, auth/CSRF, deploy, contracts) ship as a reviewed
  PR a human merges — never self-applied.

---

## 9. Operating checklist

**Daily:** open `/admin/ai-ops`; glance at provider errors; cull fresh galleries in the deck
when scores are in.

**While evaluating vision:** confirm shadow pairs are landing in the ledger; enrol a
representative set; score steadily; watch the gate verdict and the cost/latency line.

**Operational (yours, not in code):** arm Qwen on `mickeybot`; validate the Notion
`2025-09-03` cutover on a staging workspace before production; keep an eye on the ledger's
reported cost as a COGS signal; ensure the geo/immutable backup + UPS posture the audit calls
for.

---

*This runbook is living — when a capability is armed, promoted, or a new one is integrated,
update the relevant section and add/observe the matching ADR.*

## 10. Hosted backups & restore (ADR 0057)

The compose `backup` sidecar runs `scripts/hosted-backup.py --loop`: every
`MISE_BACKUP_INTERVAL_HOURS` (default 24) it uses SQLite's backup API to make a consistent
copy of the control DB, then uses that exact control snapshot as the inventory for every
live and parked tenant DB. Raw work stays in a whole-generation
`/data/backups/.generation-<stamp>/` directory; per-file `.mise-staging-*` directories and
partial gzip files never enter the remote allowlist. Verified tenant archives are written as
`tenants/<slug>.db.gz`, parked archives as `trash/<storage-key>.db.gz`, and the control
archive as `saas-control.db.gz`.

Destination tenant copies revoke native API sessions/tokens, disable and clear push
registrations, fail pending native push/content jobs, finish active content-usage claims,
and scrub transient suggestion context/candidates/provider metadata. They are committed and
`VACUUM`ed before schema, `PRAGMA quick_check`, foreign-key, and gzip validation. The live
database is never scrubbed by the backup job.

The pass writes `manifest.json` with the generation stamp, expected live/parked identities,
captured counts, and `failures`, then atomically renames the whole staging directory to
`/data/backups/<stamp>/`. Here `complete=true` means the generation structure was published;
it does **not** override a non-empty `failures` list. A restore candidate is clean only when
`failures=[]`, `captured_live == len(expected_live)`, and
`captured_parked == len(expected_parked)`.

Each hosted pass holds a non-blocking exclusive `.hosted-backup.lock`; an overlapping
timer/manual pass fails closed instead of snapshotting, pruning, or syncing concurrently.
The single-tenant shell path uses its own `.backup.lock` with the same rule. A lock-contention
exit is not a fresh backup and must remain visible to monitoring.

Live tenant snapshots are under `tenants/`; sanitized parked-studio snapshots are under
`trash/`. The pass prunes local snapshots older than `MISE_BACKUP_RETENTION_DAYS` (default
14), stamps `/data/backups/.last-hosted-backup` only after the complete evidence update, and
writes separate tenant/off-site failure markers. A successful off-site pass writes the
committed stamp into `.last-hosted-backup-offsite-success`. The hourly ops sweep alerts on
missing, stale, partial, or failed evidence. Silence is not evidence.

### Encrypted off-site configuration and commit order

**Local snapshots share the data volume** — they protect against corruption and mistakes,
not disk loss. Create a least-privilege object-store remote plus a named rclone `crypt`
remote. Store its config outside the repo (for example
`/opt/mise/secrets/rclone.conf`), set host ownership to UID/GID `10001:10001` and mode
`0400`, point `MISE_RCLONE_CONFIG_PATH` at that regular file, set
`MISE_BACKUP_RCLONE_REMOTE=<crypt-name>:` and
`MISE_BACKUP_RCLONE_REMOTE_ENCRYPTED=true`, and escrow/test the crypt password/salt
separately off-host. The compose mount is read-only and backup-only; never put config or
crypt-key contents in `.env` or expose them to the web container.

One off-site pass is deliberately ordered:

1. `rclone sync` only the exact control-derived tenant roots and only their durable
   `media/`, `brand/`, and `receipts/` children. Live SQLite files and companions,
   `tmp/`, generated `zips/`, and orphan roots are denied. Replaced/deleted media moves to
   `tenants-history/<stamp>/`.
2. Copy only the current generation payload to `backups/<stamp>/`, explicitly excluding
   `manifest.json`.
3. Copy that generation's `manifest.json` to its final remote path last. Its presence is
   the remote commit record; a remote generation without it is incomplete and must never
   be restored.

An unset remote reports `offsite: off`; a missing config or broken sync reports `failed:*`
and exits non-zero. Remote DB generations, media history, and objects uploaded by older
versions are **not automatically pruned**. Local retention and optional local deleted-studio
purge do not claim remote erasure. Configure a reviewed remote lifecycle, inventory current
and history for raw DB/WAL/SHM/journal files, export ZIPs, generated `zips/`, partial work,
and unsanitized archives, and obtain human approval before deleting any of it.

### The DB/media time boundary

Each SQLite archive is point-in-time consistent, but the media mirror runs after DB capture
and is not part of the same transaction. Remote `tenants/` is the latest successful media
mirror, while `tenants-history/<stamp>/` contains objects displaced during that sync—not a
self-contained snapshot. Prefer the newest clean generation and reconcile current/history
against project records when files changed around the backup window. Never describe a
generation plus media as an atomic point-in-time image.

### Select a restore generation (never “the newest directory”)

Before any drill or recovery, copy candidate material into a quarantine path, never directly
into `/data/tenants`. Accept exactly one stamp only after all of these checks pass:

- `format_version == 1`, `complete == true`, and manifest `stamp` equals the directory;
- `control == "saas-control.db.gz"`, `failures == []`, and expected identities are safe,
  unique direct path names;
- captured live/parked counts equal their expected-list lengths, with exactly the matching
  regular, non-symlink archives under `tenants/` and `trash/`;
- every decompressed DB passes schema/migration checks, `PRAGMA quick_check`, and
  `PRAGMA foreign_key_check`; and
- the control snapshot maps each live slug and parked storage key to the intended immutable
  tenant id. Do not combine a control DB from one stamp with tenant DBs from another.

### Non-destructive restore drill

1. Select one clean local or remote manifest generation using the checks above.
2. Restore its control DB plus at least one listed live DB and one listed parked DB into a
   throwaway quarantine directory. Never overwrite a running database for a drill.
3. Run the full SQLite/schema/identity checks and verify the restored live tenant has no
   unrevoked native API session/token, no active push device or token ciphertext, and no
   queued/running native push/content job. In-flight suggestions must be content-free
   `failed/session_ended` and never resume.
4. Exercise the crypt-key escrow on a clean machine periodically; a config file without its
   separately escrowed key is not a restorable backup.

### Full disk-loss recovery

1. Provision the new host and restore the reviewed rclone crypt config/key. Keep Caddy,
   Mise, and the backup loop stopped.
2. Download only one validated `backups/<stamp>/` generation into quarantine. Restore its
   `saas-control.db.gz`, every `expected_live` archive, and every `expected_parked` archive
   from that same stamp. Preserve the failed host/volume separately if it still exists.
3. Download remote `tenants/` and any needed `tenants-history/` material into a separate
   media quarantine. From the validated control inventory, install only
   `<live-slug>/{media,brand,receipts}` and
   `.trash/<parked-key>/{media,brand,receipts}`. Reject raw databases, scratch/export ZIPs,
   orphan roots, symlinks, and anything not named by that control snapshot.
4. Install live DBs as `/data/tenants/<slug>/mise.db`, parked DBs as
   `/data/tenants/.trash/<storage-key>/mise.db`, and the same-generation control DB as
   `/data/saas-control.db`. With services stopped, recreate and verify each deleted
   tenant's read-only `.mise-retired-path` marker and original-slug symlink guard. Permanent
   `retired_tenant_slugs` reservations remain intact.
5. Re-run all DB/schema/foreign-key and database-identity checks. Reconcile each platform
   subscription and cancellation-outbox row against Stripe before granting access; restored
   billing state is not newer than Stripe. Re-verify custom domains.
6. Finish through `scripts/launch-hosted-production.sh`. It starts Mise privately, runs
   migrations/health, forces a new encrypted manifest commit, passes runtime preflight, and
   only then opens Caddy. Owners must log in again and re-register for push. For compromise
   recovery, also rotate `MISE_SECRET_KEY`/passwords as directed in `docs/SECURITY.md`;
   database sanitization does not invalidate every signed browser cookie.

Manual one-off pass on a healthy stack:

```sh
docker compose stop backup
docker compose run --rm --no-deps --entrypoint python backup scripts/hosted-backup.py
docker compose start backup
```

Restart the backup service even when the one-off reports failure, then investigate the
failure marker/log before treating freshness as restored. Never `exec` a second pass
inside the running loop; the shared lock correctly rejects that overlap.

Deletion permanently records the original slug in `retired_tenant_slugs` before parking;
routine trash/backup retention never removes that reservation. Secure scrub/`VACUUM` runs
off the async request loop, but can hold SQLite locks, take time, and need roughly another
database-sized working copy plus temp/filesystem headroom. Check capacity first; failure must
abort rather than treat a partial park as success.

Restoring one deleted studio requires the same-generation control and parked archive plus
its quarantined durable media. Stop app/backup, preserve current state, identify the
immutable tenant id, and choose a new human-approved slug/path that is neither active nor
retired. The original slug remains permanently retired—do not delete, transfer, or bypass
that reservation. Reconcile Stripe first, restore the matching parked identity, rebuild the
retired-path guard as appropriate for the retained tombstone, and verify
`PRAGMA quick_check`, foreign keys, and `mobile_runtime_state` identity before a final
`BEGIN IMMEDIATE` clears `offboarding`. Native API sessions/tokens stay revoked, push stays
disabled until re-registration, custom domains require re-verification, and the owner must
sign in again. If any assertion fails, keep the app stopped and admission closed. The
complete fail-closed sequence is in the native Content operations runbook.

## 11. Hosted operations — the /admin/saas cockpit

Day-to-day beta running happens in one place: `/admin/saas` on the root host
(operator password, never a tenant login). What each surface is for:

- **Gate badge** (page header): *invite gate armed* vs *public — open signup
  live*. The flip is one env var (`MISE_SAAS_INVITE_CODE`); the badge is the
  truth of what production is doing. Going-public checklist: BETA-LAUNCH.md.
- **Studio feedback queue**: notes from each studio's in-app Help & feedback
  page, plus exit reasons from deleted studios. Mark **Done** once a note
  became copy, onboarding, or an issue — done notes stay in the record, out
  of the queue. New notes also ping Telegram.
- **Trial nudges**: ranked mailto drafts (trial rescue, conversion, setup,
  billing recovery). Deliberately manual — nothing sends itself.
- **Pulse badges** on each row: `never signed in` / `quiet Nd` — a silent
  trial counts as at-risk even when launch-ready.
- **Row actions**: billing-status override, domain verification, per-studio
  notes (the home for feedback that arrives by email/DM), and **extend
  trial** (1–30 days; re-arms the reminder/win-back emails, writes an audit
  line into the notes).
- **CSVs**: tenants (`/admin/saas/export.csv`) and waitlist
  (`/admin/saas/waitlist.csv` — the announcement list for going public).

Mail the platform sends on its own (all owner-facing, all one-shot, nothing
client-facing): trial reminder (~3 days before a card-less trial ends),
win-back (once, ~3 days after a lapse or cancel), dunning decline notice +
grace-ending warning (per decline episode, reset on recovery), and the
**weekly operator digest** to `MISE_SAAS_SUPPORT_EMAIL` — signups, at-risk
trials, fresh feedback, waitlist growth, lifecycle-mail counts — on the
first scheduler tick of each ISO week. A failed send retries next tick; a
restart never double-sends (stamps in `control_meta` / tenant rows).
