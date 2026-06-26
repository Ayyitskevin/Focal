# Mise Solo Studio OS

> Single-tenant, modular **operating system** for one photography business and one
> technically capable owner. This document defines the product, the current state,
> the target architecture, and the boundaries that keep the live business safe while
> the sibling sidecars are consolidated into Mise.

**Status:** design + Phase 0 foundation (this branch).
**Audience:** Kevin (owner/operator) and any agent or engineer working on Mise.
**Companion docs:** [`MISE-SOLO-STUDIO-OS-RUNBOOK.md`](MISE-SOLO-STUDIO-OS-RUNBOOK.md) (how to operate it) ·
[`REPO-CONSOLIDATION-MATRIX.md`](REPO-CONSOLIDATION-MATRIX.md) ·
[`MISE-CONSOLIDATION-ROADMAP.md`](MISE-CONSOLIDATION-ROADMAP.md) ·
[`adr/`](adr/) · [`PHASE-0-SLICE.md`](PHASE-0-SLICE.md)

## Evidence labels

Every consequential claim below is tagged so a reader can tell deployed fact from
plan. (Same scheme as the 2026-06-25 Technical Audit.)

| Tag | Meaning |
| --- | --- |
| **[DEPLOYED]** | Operating evidence or explicit production documentation. |
| **[CODE]** | Implemented in the current default branch (verified by reading it). |
| **[DOC]** | Documented; deployment unverified. |
| **[PLAN]** | Design intent / roadmap. Not a current capability claim. |
| **[INFER]** | Strongly suggested, not directly proven. |
| **[UNKNOWN]** | Material fact not supplied; needs a read-only check or benchmark. |

A README does not prove deployment, and an `.env.example` key does not prove a feature
is armed.

---

## 1. Product definition

Mise Solo Studio OS is a **single-tenant modular monolith with optional stateless
compute workers**. One FastAPI + HTMX + SQLite application **[CODE]** is the command
center and the single authority for the business; heavy or experimental AI runs on
replaceable workers that own no business records.

It absorbs the *proven photography capabilities* of the sibling repositories — Argus
(vision), Plutus (offers), Mnemosyne (albums), Dionysus (content), and later parts of
Aphrodite (product images) — **as modules inside Mise**, conforming to Mise's existing
conventions and shared infrastructure. It does **not** concatenate repositories, import
sibling apps at runtime, reproduce their SaaS infrastructure, or rewrite Mise.

The outcome we are optimizing for:

- **Fewer services.** One coherent operator experience instead of a fleet of sidecars
  each with its own auth, queue, deploy, and backup.
- **One business data spine.** Mise SQLite is the system of record for everything that
  touches a client, money, a contract, or a deliverable.
- **Clear ownership of state.** Every record has exactly one writer; everything else is
  a projection, a mirror, or a draft awaiting human approval.

### The two non-negotiables (audit §1A)

1. **Keep the client-to-cash path boring and recoverable.** Inquiry → booking →
   shoot → gallery → contract → invoice → payment must never depend on an AI service,
   a sidecar, or Notion being up.
2. **Make computationally heavy or experimental work disposable.** Vision, album, and
   content generation run on workers that can be rebuilt from authoritative inputs.

### Operating principle

> Protect the revenue path, make experiments disposable, measure before buying, and
> require proof before migrating authority. (audit §21)

---

## 2. Current-state architecture

Mise today is already a modular monolith **[CODE]**, not a greenfield. It is a
FastAPI app (`app/main.py`) with two route packages (`app/admin/`, `app/public/`), a
flat set of domain/service modules, a SQLite database in WAL mode (`app/db.py`), and an
in-process thread-pool job queue that survives restarts (`app/jobs.py`).

### What Mise already owns (verified)

| Domain | Where | Evidence |
| --- | --- | --- |
| Public studio site + inquiry intake | `app/public/site.py`, `app/public/forms.py` | [CODE] |
| Clients & projects | `app/clients.py`, `app/admin/studio.py`, `migrations/001,002,017` | [CODE] |
| Bookings & scheduling | `app/scheduling.py`, `app/scheduler.py`, `app/gcal.py`, `migrations/007,033-038` | [CODE] |
| Shot lists & prep | `app/admin/shotlist.py`, `migrations/027` | [CODE] |
| Galleries, proofing, delivery, media | `app/public/gallery.py`, `app/admin/galleries.py`, `app/jobs.py`, `migrations/010` | [CODE] |
| Contracts, invoices, payment state | `app/admin/contracts.py`, `app/admin/invoices.py`, `app/public/pay.py` (Stripe) | [CODE] |
| Operator dashboard & review queues | `app/admin/studio.py`, `app/admin/inbox.py` | [CODE] |
| Audit trail (append-only) | `app/audit.py` → `audit_log` table | [CODE] |
| Background jobs | `app/jobs.py` (SQLite `jobs` table + `ThreadPoolExecutor`) | [CODE] |
| Storage abstraction / media namespace | `config.MEDIA_DIR / <gallery_id> / {original,web,thumb,crops}` | [CODE] |
| One shared admin auth | `app/admin/auth.py`, `app/security.py`, signed-cookie sessions | [CODE] |

### AI / sibling integrations already wired into Mise (verified)

These are the proven photography capabilities the audit says have narrowed to
"studio-sidecar" roles. **All four already exist inside Mise as outbound integrations**
— the consolidation is therefore largely about putting them behind one clean internal
contract, not building integration from scratch.

| Capability | Mise module | Shape | Armed by |
| --- | --- | --- | --- |
| **Vision** (Argus) | `app/argus_analyze.py`, `app/argus_writeback.py` | POST `mise_gallery_id` → `/analyze-folder`; queued/sync; callback updates `argus_last_*`; writeback pulls run export → asset scores/alt-text/keywords + hero pick | `MISE_ARGUS_URL` + `MISE_ARGUS_TOKEN` [CODE] |
| **Offers** (Plutus) | `app/plutus_recommend.py` | POST `mise_gallery_id` → `/recommend/mise-gallery`; records `plutus_last_*` | `MISE_PLUTUS_URL` + `MISE_PLUTUS_TOKEN` [CODE] |
| **Content / caption** (Odysseus) | `app/caption_ai.py` | POST context → `{caption, model}`; Odysseus owns model routing | `MISE_ODYSSEUS_CAPTION_URL` + `MISE_ODYSSEUS_CAPTION_TOKEN` [CODE] |
| **Content packs** (Dionysus) | `app/platekit.py` | GET approved packs; `notify_argus_complete` drafts a caption pack after vision | `MISE_PLATEKIT_API_BASE` + `MISE_PLATEKIT_API_TOKEN` (legacy `MISE_DIONYSUS_*` fallback) [CODE] |
| Inbound read API for siblings | `app/service_api.py` | `GET /api/shots`, `/api/galleries`, `/api/galleries/expiring`, `/api/press/recent`; `POST /api/{argus,plutus}/callback` | bearer-gated [CODE] |

**Shared integration pattern [CODE]** — every module is **dormant-by-env**
(`is_enabled()` true only when *both* URL and token are set) and **non-mutating on the
provider call** (persistence lives in the background/caller path, never in the
trigger/read call). The full shape differs by module, and the facade is designed around
exactly these differences:

- `argus_analyze` and `plutus_recommend` follow the full pattern: typed error
  (`ArgusAnalyzeError` / `PlutusRecommendError`), a non-mutating synchronous `trigger_*`
  that raises, and a background `run_for_gallery` that swallows and records last status.
- `caption_ai` exposes `is_enabled()` + `draft_caption()` (raising `CaptionDraftError`)
  with **no** `run_for_gallery` — the caller writes the accepted draft.
- `platekit` (Dionysus) exposes `is_enabled()` + `packs_for_client()` — a read that
  **never raises** and returns a status dict — with **no** typed error class and **no**
  `run_for_gallery`.

### Notion and Odysseus boundaries (verified)

- **Notion** is a bounded, one-way human mirror **[CODE]** (`app/notion_sync.py`): Mise
  creates/patches selected booking/session/invoice/delivery fields; it is never read
  back into a Mise flow. *(Carries the known `Notion-Version: 2022-06-28` debt — see
  roadmap. Red-light.)*
- **Odysseus** is an external reasoning/model-routing adapter **[CODE]**: caption
  drafting delegates model choice entirely to Odysseus (`app/caption_ai.py`), and the
  shot-list read API exists so Odysseus can read Mise's locally-authored shot list.
  Odysseus never mutates money/contract/gallery state directly.

### Backup safety net (deployed)

Nightly integrity-checked SQLite snapshot on flow (02:30) → mickey pull (03:30) →
**proven restore** (04:00) → Telegram alert on failure. **[DEPLOYED]** (`ops/BACKUP.md`,
`AGENTS.md`). Material gap **[UNKNOWN]**: geographic / immutable (ransomware-resistant)
copy and UPS coverage are not verified.

### Current-state limitations the OS framing addresses

- **No uniform AI result contract [CODE].** Each capability records provenance ad hoc
  (`galleries.argus_last_*`, `plutus_last_*`, `retainer_captions.ai_model`,
  `assets.argus_*`). There is no single normalized result with provider, model,
  latency, cost, and review requirement — so there is no shared review queue, no shadow
  comparison seam, and no place to swap an external provider for an internal worker.
  **Phase 0 closes this** (see §6 and `PHASE-0-SLICE.md`).
- **Sidecar sprawl [DEPLOYED/INFER].** Each sibling duplicates auth, queue, deploy, and
  backup; recent Plutus auth drift caused a 401 (audit §3.2). Consolidation removes the
  duplication, not the capability.

---

## 3. Target architecture

A **single-tenant modular monolith** (Mise) + **optional stateless compute workers**.

```
                 ┌─────────────────────────── flow (production zone) ───────────────────────────┐
   Internet ───▶ │  Mise (FastAPI + HTMX)                                                        │
   (Cloudflare/  │   studio · crm · scheduling · galleries · documents · billing                 │
    tunnel)      │   vision · offers · albums · content_ai · products(*)  ← capability modules   │
                 │   providers (AI facade) · jobs · audit · automation · operations              │
   Operator ───▶ │   SQLite (WAL) = the ONE business data spine    media/ = storage namespace    │
   (secure       │   one auth · one audit trail · one job queue · one storage abstraction        │
    overlay)     └───────────────┬───────────────────────────────────────────────┬──────────────┘
                                 │ bounded events / read APIs / job refs           │ one-way mirror
                                 ▼                                                  ▼
                 Replaceable stateless workers (mickey / strix / cloud)        Notion (human surface)
                  Argus vision · Plutus offers · Mnemosyne albums ·            planning, dashboards,
                  Aphrodite product images · model execution                   review views — NOT a
                  — own NO authoritative business records                       transaction DB/queue
                                 │
                                 ▼
                 Odysseus / model gateway — reasoning & model routing only; proposes
                 structured outputs, never mutates money/contract/gallery/delivery state.
```

`(*) products` (Aphrodite) is a later, optional module — not in the first consolidation
phase.

### Design tenets (from Hestia *as guidance only* — no Hestia runtime dependency)

- One product, one data spine. Modules, not microservices.
- Provider **seams**, durable jobs, idempotency, human-reviewed AI.
- Shared identity, storage, audit, and job foundations — built once, reused by every
  module.

### Divergence from the audit's *selected* topology — recorded honestly

The 2026-06-25 audit's selected Pattern E puts a **PostgreSQL + pgvector control plane
on mickey** and keeps the siblings as separate worker services. This document follows
**Kevin's master-prompt directive**: a *Mise-owned* modular monolith that keeps SQLite
and owns the job/review lifecycle itself, with mickey/strix/cloud as **stateless
workers** rather than a stateful control plane. The two are reconcilable — both keep
Mise authoritative, both make workers disposable, both centralize the AI substrate. The
difference is *where durable job/provenance state lives* (Mise SQLite vs a separate
Postgres). This is a deliberate decision for solo-operator simplicity and is recorded
in [`adr/0001`](adr/0001-modular-monolith-plus-workers.md) and
[`adr/0005`](adr/0005-sqlite-retention.md), with the measured triggers that would
reopen it.

---

## 4. System-of-record boundaries

Mise SQLite is the single transaction authority. Everything else is a projection,
mirror, draft, or disposable cache. (Derived from audit §3.1; **Proposed authority** =
the target for Mise Solo Studio OS.)

| Domain | Authority | Writers | Mirrors / projections | Conflict policy |
| --- | --- | --- | --- | --- |
| Leads / inquiries | **Mise** | Mise UI/API | Notion summary | Mise wins |
| Clients | **Mise** | Mise; controlled migration tools | Notion reference | Mise wins |
| Projects / shoots | **Mise** | Mise; Odysseus via bounded read API only | Notion Sessions | explicit field ownership |
| Bookings / schedule | **Mise** | Mise scheduler; Google webhook/OAuth adapter | Google Calendar, Notion | Mise wins |
| Shot lists | **Mise** | Mise; Odysseus read-only (`/api/shots`) | Notion fallback | Mise wins |
| Galleries / delivery | **Mise** | Mise | Notion delivery status | Mise wins |
| Media assets | **Mise metadata + filesystem** | ingest + approved editors | Notion references (no binaries) | content hash / manifest |
| Contracts | **Mise** | human-reviewed Mise path | Notion status | Mise wins (red-light) |
| Invoices / payments | **Mise + Stripe events** | Mise pay code + verified webhook | Notion summary | idempotent provider event; Mise wins (red-light) |
| **AI job state / provenance / approval** | **Mise** | Mise job worker; validated worker callbacks | Notion AI-review queue (summary) | idempotency key; Mise owns accepted status |
| Offers / client links | **Mise** | Mise (Plutus *proposes*) | Notion summary | one idempotent offer per gallery |
| Album drafts | **Mise** | Mise (Mnemosyne *proposes*) | — | human-approved version wins |
| Content drafts | **Mise (client-linked) / Notion (planning)** | human; AI draft only | review queue | human-approved wins |
| Tasks | **Notion** (today) | humans, scoped Odysseus | — | Notion is master until a gated migration |
| Knowledge / planning | **Notion** (today); Git for tech docs | humans, scoped agents | — | no live dual-master |
| Source code | **GitHub** | PR/branch rules | local clones | default branch |
| Secrets | **per-host `.env` / systemd creds** | named operator + service accounts | none | never via Notion/Git |
| Backups | **independent backup identity** | backup service | logs | newest verified restore wins |

**Notion must never become** the transaction DB, the job queue, a binary-media store,
or a second authority for a Mise-owned record. **Odysseus / any provider must never
mutate** money, contract, gallery-publication, or client-delivery state; it may propose
structured output that deterministic Mise code validates and a human approves.

---

## 5. Module boundaries (target shape)

These are **bounded capabilities inside one Mise process**, not independent apps. Names
conform to the existing flat-module + `admin/`/`public/` layout; we introduce a new
boundary only where it removes real duplication or establishes a migration seam (Mise
rule R2 — no abstraction for single-use code).

| Module | Capability | Today → target |
| --- | --- | --- |
| `studio` (public) | Public site + inquiry intake | `app/public/*` — keep |
| `crm` | Clients, projects | `app/clients.py`, `app/admin/studio.py` — keep |
| `scheduling` | Bookings, calendar, reminders | `app/scheduling.py`, `app/scheduler.py`, `app/gcal.py` — keep |
| `galleries` | Galleries, proofing, delivery, media | `app/public/gallery.py`, `app/admin/galleries.py`, `app/jobs.py` — keep |
| `documents` | Contracts, proposals, e-sign | `app/admin/{contracts,proposals,doc_templates}.py` — keep (red-light) |
| `billing` | Invoices, payments, Stripe | `app/admin/invoices.py`, `app/public/pay.py` — keep (red-light) |
| **`providers`** | **AI provider facade + adapters + registry** | **new — `app/providers/` (Phase 0, shipped on this branch)** |
| `vision` | Argus capability | `app/argus_analyze.py`, `app/argus_writeback.py` → behind `providers` |
| `offers` | Plutus capability | `app/plutus_recommend.py` → behind `providers` |
| `content_ai` | Odysseus caption + Dionysus packs | `app/caption_ai.py`, `app/platekit.py` → behind `providers` |
| `albums` | Mnemosyne capability | **[PLAN]** new module; shadow pilot first |
| `products` | Aphrodite capability | **[PLAN]** optional, later, budget-capped |
| `automation` | Reminders, nudges, arming | `app/*_reminders.py`, `app/hermes_arm.py`, `app/postshoot_reminders.py` — keep |
| `jobs` | One shared background-job mechanism | `app/jobs.py` — keep |
| `audit` | One shared audit trail | `app/audit.py` — keep |
| `operations` | Health, ops monitor, alerts, backup evidence | `app/ops_monitor.py`, `app/alerts.py`, `/healthz` — extend |

---

## 6. Worker boundaries

Workers are **replaceable stateless compute**. They run heavy model execution on
`mickey`, `strix-halo-a9-mega`, or a cloud provider, and **own no authoritative business
records** (audit §7.7, §5.2).

- **Mise owns the job and review lifecycle.** A worker receives a job reference +
  bounded inputs, returns a validated result via callback, and updates *run* state — not
  *transaction* state.
- **Worker failure ≠ business-state failure.** A timeout or outage leaves Mise records
  untouched (proven for the existing path; enforced by the Phase 0 contract — only an
  `OK` `ProviderResult` may drive a write).
- **Local worker copies are disposable.** Media travels by durable reference + content
  hash; a worker's cache is never the only authoritative copy.
- **Replaceable behind the `providers` seam.** External Argus, direct cloud vision, or a
  local worker can back the `vision` capability without changing the gallery workflow.

The Phase 0 `providers` facade is exactly this seam: `providers.resolve(Capability.X)`
returns the legacy external adapter today and can return an internal-worker adapter
later, behind a feature flag, in shadow mode, with rollback.

---

## 7. Non-goals (explicit)

Mise Solo Studio OS will **not**:

- Become **multi-tenant** or add public SaaS signup / studio-subscription billing.
- Take a **Hestia runtime dependency** or perform a Hestia database migration. Hestia is
  design guidance only; it stays an independent product behind its own migration gates.
- Integrate **Athena** (Notion stays the planning surface) or **Midas** (unrelated).
- Turn **Notion** into the transaction DB, job queue, or media store.
- Let **Odysseus / any model** decide prices, payment state, contract state,
  publication, deletion, or sending.
- Introduce **PostgreSQL, Redis, Kubernetes, a JavaScript SPA, or new microservices**
  for architectural fashion. SQLite stays unless measured contention, durability, or
  operational need justifies a change ([`adr/0005`](adr/0005-sqlite-retention.md)).
- Merge Git histories, copy entire sibling applications, or import sibling apps at
  runtime.
- Auto-publish AI client communication, image selection, album design, pricing, or
  offers. All remain **drafts requiring human approval**.

---

## 8. How this stays safe

- Every red-light area (money path, schema/migrations, deploy, security/auth/CSRF,
  contracts) goes through a human-merged PR — never self-merged (`AGENTS.md`).
- The consolidation is **strangler-style**: stable interface → wrap legacy as adapter →
  internal impl behind a flag → shadow → switch one workflow → keep rollback →
  decommission only after parity + restore test + observation. Never a flag-day swap.
- The Phase 0 slice on this branch is **additive and dormant**: nothing in the running
  app imports `app/providers/` yet, so it changes no behavior, route, env var, or
  schema. It is the seam; the cutover is later, gated work.
