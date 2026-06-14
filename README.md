# Mise

**Self-hosted delivery + business platform for a Food & Beverage photographer/videographer.**
A single-operator "Pixieset + HoneyBook hybrid" — client galleries, content delivery,
proposals/contracts/invoices, recurring social retainers, and a public marketing site —
built as one FastAPI app with no JS build chain.

Live: <https://kleephotography.com> · Runs on a single always-on node behind a Cloudflare Tunnel.

---

## What it does

- **Client galleries** — PIN-gated delivery, favorites/proofing, video comments, single-asset
  and full-gallery ZIP downloads, iOS-friendly Range-streamed media.
- **F&B content portal** — per-client hub with auto social crops (1:1, 4:5, 9:16), brand kits,
  caption packs, usage/licensing rights, and a content calendar.
- **Studio (the money side)** — proposals → contracts (typed-name e-sign) → Stripe invoices,
  plus recurring-retainer plans that auto-*draft* monthly deliverables (never auto-send/charge).
- **Public marketing site** — home, portfolio, services, work case studies, testimonials,
  press, about, contact, and an inquiry/booking form.

## Stack

FastAPI · Jinja2 · HTMX (no front-end build) · SQLite (WAL) · Pillow + pillow-heif (imaging) ·
ffmpeg (video transcode/poster) · Stripe (payments) · itsdangerous (signed cookies).
Python deps pinned in `requirements.txt`. ~17.5K LOC.

## Architecture

Three surfaces, one process:

| Surface | Code | Audience | Auth |
|---|---|---|---|
| Marketing site | `app/public/site.py` | Public / indexable | none |
| Client delivery | `app/public/{gallery,portal,downloads,media,pay}.py` | Clients | 14-char slug + 4-digit PIN, per-IP lockout |
| Admin back office | `app/admin/*` | The photographer | password + signed cookie |
| Machine API | `app/service_api.py` | Internal automation | bearer token (`/api/shots`) |

**Spine:** `main.py` (app factory + middleware), `config.py` (env-driven), `db.py`
(SQLite, short-lived connections, 27 forward-only migrations in `migrations/`),
`security.py` (slugs/PINs/lockout/cookies), `jobs.py` (in-process queue for image
derivatives + video transcodes), `scheduler.py` (retainer thread — drafts only).

**Integration doctrine — one-way, by design.** Mise *owns* money and media truth. It pushes
status **outward only**: to Notion (`notion_sync.py`) and an external Odysseus CRM
(`caption_ai.py`, `reopen_notify.py`). There is **no bidirectional sync anywhere** — that is a
deliberate constraint, not a missing feature.

## Repo layout

```
app/
  main.py config.py db.py security.py render.py jobs.py scheduler.py audit.py
  admin/      back-office routers (galleries, studio, invoices, contracts,
              proposals, licenses, presets, press, recurring, shotlist, uploads, activity)
  public/     client + marketing routers (gallery, portal, downloads, media, pay, docs, site)
  service_api.py            bearer-gated /api/shots
  imaging.py video.py       media pipeline
  notion_sync.py caption_ai.py reopen_notify.py   one-way outbound integrations
migrations/   001..027 forward-only (+ rollback/ mirror)
templates/    admin/ · public/ · site/   (Jinja + HTMX)
static/       mise.css + 4 vanilla JS (htmx, lightbox, copy-link, details-persist)
ops/          systemd units + nightly backup
tests/        test_smoke.py (end-to-end smoke suite)
```

## Running it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in real values (mode 600, never committed)
uvicorn app.main:app --host 127.0.0.1 --port 8400
```

Migrations run automatically on startup (`db.migrate()`). Production runs under systemd
(`mise.service`) with a nightly backup timer (`ops/mise-backup.timer`).

### Configuration

All config is env-driven via `app/config.py` (loads `/opt/mise/.env`). Keys cover the secret
key, admin password, Stripe keys, Gmail app password (manual-send only), Notion token, the
Odysseus caption/reopen URLs, and the `/api/shots` bearer token. **No secrets live in the
repo** — `.env` is git-ignored; `.env.example` holds placeholders only.

## Design system

The visual layer is a single hand-written `static/mise.css` plus Jinja partials under
`templates/site/` (marketing) and `templates/public/` (client-facing). Brand colors, spacing,
and type scale are defined as CSS custom properties at the top of `mise.css`.

## Security posture

- Tiered auth (see table); client PINs have per-IP brute-force lockout (5 fails → 15 min).
- Gallery slugs are 14-char base62 (unguessable); all non-marketing routes send
  `X-Robots-Tag: noindex`; `X-Frame-Options: DENY` everywhere.
- `CF-Connecting-IP` is trusted only when the peer is localhost (tunnel-correct rate limiting).
- Secrets in `.env` (mode 600), never in code, logs, or history. Stripe webhooks are
  signature-verified.

## Status & scope

Single-operator (not multi-tenant). Self-hosted on one node; designed for later VPS
lift-out. Money and media truth live here; everything else reads from Mise one-way.
