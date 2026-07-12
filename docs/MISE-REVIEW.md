# Mise — full-app review (2026-07-12)

Reviewed against `origin/main` @ `b641388` (includes iOS Milestone 3,
PR #144). This checkout's working tree is clean: the in-flight reskin's
uncommitted changes live on the operator's machine and are **not** reviewed
or judged here. Review and plan only — nothing was changed.

## 1. Architecture & code health — **fine**

The modular monolith holds its shape: one FastAPI app (`app/main.py`)
composing ~40 domain routers (one file = one domain in `app/`, admin-only
handlers under `app/admin/`), sequential SQL migrations with matching
rollbacks (`migrations/` — 081 is current, next is 082), JSON schemas for
worker structured output (`schemas/`), and the provider facade at
`app/providers/`. The native `/api/v1` boundary (ADR 0066/0067) is a
separately mounted sub-app with its own problem-response contract and does
not leak browser-cookie semantics. Middleware ordering in `app/main.py` is
documented as load-bearing and reads correctly (tenant context outside
CSRF/rate-limit, inside common headers).

Notable debts, none urgent:

- `app/mobile_owner_api.py` and `app/mobile_gallery_calendar_api.py` carry
  two independently implemented cursor schemes and two ETag helpers;
  `mobile_client_api.py` reuses the latter. Consolidation is cosmetic.
- CLAUDE.md still lists five AI capabilities; two are gone (see §2). Stale
  onboarding docs cost every future session a re-derivation.

## 2. AI capability topology — **worth fixing** (factual finding)

Read from the actual adapter code (`app/providers/adapters.py`,
`registry.py`, `vision_challenger.py`, `products_render.py` and the modules
they delegate to). Taxonomy: (a) in-process, (b) direct hosted API,
(c) separate self-hosted service.

| Capability | Status | Topology | Evidence |
|---|---|---|---|
| VISION — Argus (production) | live | **(c)** separate self-hosted service | `app/argus_analyze.py` HTTP+bearer → `MISE_ARGUS_URL`; Argus internally uses cloud Grok (runbook §vision) |
| VISION challenger — Qwen3-VL (eval-only) | dormant by env | **(c)**, explicitly the operator's homelab | `vision_challenger.py:75` POSTs to `MISE_VISION_CHALLENGER_URL`, documented as `http://mickeybot:11434/v1` (Ollama); `serves_production=False` with a hard interlock in `registry.py:150` |
| CONTENT — caption (Odysseus) | live | **(c)** separate self-hosted service | `app/caption_ai.py:45` `urlopen` → `MISE_ODYSSEUS_CAPTION_URL`; Odysseus owns model routing |
| CONTENT — packs (Dionysus/Platekit) | live | **(c)** separate self-hosted service | `app/platekit.py` HTTP → `MISE_PLATEKIT_API_BASE` |
| PRODUCTS — Aphrodite | **correctly dormant** | no outbound call ever | `products_render.py`: `serves_production=False`; `render` returns non-OK without calling out even when the URL is set |
| OFFERS — Plutus / ALBUMS — Mnemosyne | **decommissioned** | — | migration `075_decommission_albums_offers.sql` dropped schema + surfaces; CLAUDE.md not yet updated |

Plainly: **no live capability is in-process, and none calls a hosted model
API directly from Mise — every one depends on a separate self-hosted
sidecar**, none of which is co-deployed in `docker-compose.yml`, and the
challenger endpoints are documented homelab hosts. The recommendation and
consolidation plan are deliberately deferred to `docs/HANDOFF-QUEUE.md`
(Opus lane); `docs/MISE-CONSOLIDATION-ROADMAP.md` already sketches the
strangler path and `registry.py` is the built-in seam.

Invariant checks, all good:

- **§11.4** — every adapter returns `ReviewRequirement.HUMAN_REVIEW`, and
  adapters never touch the DB (module docstring + code confirm: persistence
  stays in the legacy trigger/callback paths).
- **Spend caps / consent** — Aphrodite's budget/consent guards are real code
  in `app/products.py` (deterministic, per ADR 0021), not convention;
  Plutus's caps are moot (decommissioned).
- The `active_vision_provider` interlock means a mis-set
  `MISE_VISION_PROVIDER` can never route production onto an eval-only or
  unconfigured provider.

## 3. Reskin consistency — **fine** (in the committed tree)

The "candlelit cream" skin is layered coherently: `base.html` → root;
`base_cream.html` extends it; `templates/admin/base_admin.html` extends
cream and **all 66 admin templates** extend `base_admin` — fully uniform.
16 of 19 `templates/public/` pages are on cream; the 3 exceptions
(`book_event`, `book_index`, `booking_manage`) deliberately extend
`site/base_site.html` (public booking is marketing-adjacent). `saas/*` and
`site/*` stay on the base/marketing skin. One `static/mise.css` carries the
tokens; no duplicated stylesheets found. Two notes:

- CLAUDE.md's directory map says `templates/ (admin/* + client/*)` — the
  actual split is `admin/ public/ saas/ site/`. Docs drift, not code drift.
- The uncommitted reskin on the operator's machine presumably continues
  this; nothing in the committed tree looks half-migrated.

## 4. Tests & lint — **fine**

Run tonight against this tree (Python 3.12 venv):

| Check | Result |
|---|---|
| `python -m pytest -m unit -q` | **pass** — 758 unit tests, 0 failures |
| `ruff check .` | **pass** ("All checks passed!") |
| `ruff format --check .` | **pass** (232 files already formatted) |

No test performs a live model/API call: every `urlopen`/network reference in
`tests/` is a fake or monkeypatch (`test_providers.py`, `test_platekit_hook.py`,
`test_notion_modernization.py` construct fakes explicitly). House rule holds.
(The `-m smoke` tier needs `ffmpeg` and is environment-dependent; not part of
the unit gate and not run here.)

## 5. Commercial spine (ADRs 0034–0046) ↔ iOS — **worth fixing** (gap, not defect)

Per the ADR index, the F&B commercial spine (repeat-client cadence,
shot-list/deliverable templates, closeout-readiness, company next-action
ranking, Studio Activity commercial queue, AR chase assist, AR cadence,
company comms history, billing readiness) is deterministic, operator-only
admin HTML. The iOS owner companion (M2–M3) covers dashboard KPIs, clients,
projects, galleries, calendar, and client delivery — **none of the
commercial-spine surfaces exist natively**. The owner's highest-value daily
loop (AR chase, closeout readiness, next actions) is desktop-only today.
That's the single biggest product gap for the iOS app → picked up in
`docs/IOS-UPGRADE.md`.

## 6. Security-adjacent flags — route to cybersecurity review (flag only)

1. Sidecar auth uses long-lived static bearer tokens in env
   (`MISE_ARGUS_TOKEN`, `MISE_ODYSSEUS_CAPTION_TOKEN`,
   `MISE_PLATEKIT_API_TOKEN`) with no rotation story in the runbook.
2. The vision/album challenger URLs are plain `http://` LAN endpoints;
   client-media derivatives would transit unencrypted if armed.
3. `.env.example:138` still documents `MISE_ALBUM_CHALLENGER_URL` pointing
   at a homelab port for a capability deleted in migration 075.

No production secrets, keys, client media, or the production DB were read.

## Lane ratings

| Lane | Rating |
|---|---|
| Architecture & code health | fine |
| AI topology vs. architecture directive | **worth fixing** (consolidation → Opus queue) |
| Reskin consistency (committed tree) | fine |
| Tests / lint / no-live-calls rule | fine |
| Commercial spine on iOS | **worth fixing** (product gap) |
| Security-adjacent flags | route to security review |
| Docs accuracy (CLAUDE.md capability list, template map) | worth fixing (cheap) |

Nothing rated **urgent**.
