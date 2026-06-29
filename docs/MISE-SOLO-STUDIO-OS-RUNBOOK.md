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
  project closeout gaps, and repeat-shoot cadence; each item links back to its owning surface;
- the Studio Activity `Needs attention` panel rolls up the top commercial action per company so
  the morning triage view shows which relationship to open first;
- AR chase assist opens from past-due company/action/invoice links, gathers the statement and
  payable invoice links, and sends only after review. It logs the manual email and never changes
  invoice or payment state.
- AR follow-up cadence is derived from those manual send-log rows: company and Activity surfaces
  show never chased, recently chased, or follow-up due after `MISE_AR_CHASE_FOLLOWUP_DAYS`
  (default seven). It does not schedule or
  send anything by itself.

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

### Content / captions (Odysseus, Dionysus) — ADR 0006

- **What it does:** caption / copy drafts (Odysseus) and pack-draft provenance (Dionysus).
- **Arm:** `MISE_PROVIDER_FACADE_CONTENT=true` routes caption drafting through the facade and
  records Dionysus pack-draft outcomes to the ledger (additive, ledger-only). Off = legacy
  behavior, no ledger rows.
- **Bounded:** every caption is a draft requiring your approval where it's used.

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

All consolidation migrations are **additive and forward-only** — applying them with the
feature dormant changes nothing. Run via the app's normal `db.migrate()` on boot.

| Migration | Adds | Rollback |
| --- | --- | --- |
| `065_ai_runs.sql` | `ai_runs` provenance ledger | `rollback/065_ai_runs.sql` (drops the table) |
| `067_validation_set.sql` | `validation_items` + `validation_scores` | `rollback/067_validation_set.sql` |
| `075_decommission_albums_offers.sql` | **drops** `album_drafts`/`album_placements` + 13 `plutus_*` columns (decommission, ADR 0026) | `rollback/075…` (recreates the dropped schema; the features stay gone in code) |
| `077_asset_cull_state.sql` | `assets.cull_state` + `cull_decided_at/cull_source` + index | `rollback/077…` (DROP COLUMN; SQLite ≥3.45) |

Each rollback is safe because the tables/columns are dormant and referenced by no money,
invoice, or business record. Rolling a feature back is normally a **flag**, not a migration —
reach for a rollback script only to remove the schema itself. (The album/offer migrations
066/068/069/070 that 075 reverses are historical — their schema no longer exists.)

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
