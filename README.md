# Focal

[![Python CI](https://github.com/Ayyitskevin/Focal/actions/workflows/ci.yml/badge.svg)](https://github.com/Ayyitskevin/Focal/actions/workflows/ci.yml)
[![iOS](https://github.com/Ayyitskevin/Focal/actions/workflows/ios.yml/badge.svg)](https://github.com/Ayyitskevin/Focal/actions/workflows/ios.yml)
[![License: AGPL-3.0-only](https://img.shields.io/badge/license-AGPL--3.0--only-2f634d.svg)](LICENSE)

> **Your studio in focus — from first inquiry to final frame.**

Focal is a self-hostable, local-first studio operating system for solo photographers
and small creative studios. It brings client relationships, inquiry and booking,
contracts, galleries, invoicing, payments, workflow, and optional human-reviewed AI
into one modular web application with a focused native iOS companion.

> **Project status:** active development and private-beta preparation. Self-hosted mode
> is the safest review path. Hosted mode is implemented in parts but is not a public
> service or launch claim. There is no App Store release, TestFlight release, or
> generally available hosted signup.

Focal is a standalone product repository. It is separate from the public
`kleephotography` website and from Hestia's separate greenfield commercial SaaS track.
Sibling repositories may inform capability design, but they are not runtime dependencies
or alternate sources of truth.

## What Focal is becoming

| Surface | Current reality |
| --- | --- |
| Web studio | FastAPI + HTMX modular monolith with CRM, inquiry intake, projects, booking, proposals, contracts, invoices, galleries, proofing, delivery, and operator workflows. |
| Self-hosted mode | Canonical review path: one studio, one SQLite database, local media, optional integrations, and disposable demo data. |
| Hosted mode | Tenant routing, per-studio SQLite/media isolation, billing state, and lifecycle tooling exist in the codebase; the hosted service is not publicly launched. |
| iOS companion | SwiftUI owner/client companion for tenant discovery, scoped auth, studio views, galleries, documents, bookings, and a narrow set of guarded commands. It is not full web parity. |
| AI assistance | Optional provider-backed vision, copy, and product capabilities. Models propose; deterministic code validates; a human approves before consequential persistence or publication. |

## Core workflow

Focal is organized around the photographer's operating loop:

**inquiry → client → project → booking → contract → gallery → invoice/payment → delivery → follow-up**

The revenue and rights path must work without an AI provider, Notion, or any sibling
repository being available.

## Review Focal safely

The reviewer launcher exposes a disposable product tour using a temporary data
directory. It binds only to loopback, ignores local `.env` state, loads no production
credentials, and removes its temporary state when stopped.

~~~bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python scripts/reviewer_demo.py
~~~

Open `http://localhost:8400/demo`, then visit `/healthz`. The tour is static and
does not create a tenant, exercise Stripe, contact external services, or provision
App Store credentials. Do not run `scripts/seed_demo_tenant.py`; [issue #185](https://github.com/Ayyitskevin/Focal/issues/185)
documents why that separate hosted-state provisioner remains held.

For a structured code tour and review prompts, use the [reviewer guide](docs/REVIEWER-GUIDE.md).
The current product boundaries are recorded in [Focal product identity](docs/FOCAL-IDENTITY.md).

## Architecture at a glance

Focal is a modular monolith with two explicit delivery boundaries:

- browser routes use signed, tenant-bound cookies and server-rendered HTMX;
- `/api/v1` uses scoped opaque sessions for the native app and does not reuse the browser
  or machine-token boundary;
- hosted requests resolve the tenant from the host before entering a tenant runtime;
- optional AI/media workers sit behind narrow adapters and remain dormant without
  explicit configuration.

SQLite is the transaction authority for client, booking, gallery, contract, invoice,
payment, and workflow state. Notion is a bounded human-facing mirror, not the
transactional database. AI output is a draft until an operator approves it.

See [Architecture](docs/ARCHITECTURE.md), the [iOS architecture](docs/IOS-ARCHITECTURE.md),
the [mobile API contract](docs/IOS-API-V1.md), and the checked-in [architecture decisions](docs/adr/).

## Run the self-hosted web app

~~~bash
pip install -r requirements.txt
cp .env.example .env
# Set MISE_SECRET_KEY and MISE_ADMIN_PASSWORD in .env.
python -m uvicorn app.main:app --port 8400
~~~

Open `http://localhost:8400/admin/login`. Self-hosted mode uses one SQLite database
under `MISE_DATA_DIR`. The application still exposes `MISE_*` environment variables
and legacy filesystem/service identifiers during this branding transition; those
compatibility names will be migrated separately with explicit tests and rollback.

For a containerized local deployment, use `docker compose up --build`. Hosted
deployment design remains reference material until its launch gates are closed.

## Development

Python 3.12 is the supported runtime. Run the complete Python gate:

~~~bash
source .venv/bin/activate
python -m pytest tests/ -m unit
MISE_DATA_DIR=$(mktemp -d) MISE_SECRET_KEY=test MISE_ADMIN_PASSWORD=pw \
  MISE_ENV_FILE=/nonexistent python -m pytest tests/ -m "not unit"
ruff check .
ruff format --check .
~~~

The non-unit partition needs `ffmpeg`. iOS changes run through the macOS `build-test`
workflow; local setup is in [ios/README.md](ios/README.md).

## Repository map

| Path | Purpose |
| --- | --- |
| `app/` | FastAPI composition, domain modules, hosted tenancy, jobs, API boundaries |
| `app/admin/` | Owner/operator HTML routes |
| `app/public/` | Client galleries, portals, documents, payments, booking, and marketing |
| `ios/` | SwiftUI application, repositories, networking, session security, and tests |
| `migrations/` | Forward and rollback SQL history for tenant databases |
| `templates/`, `static/` | Server-rendered interface and review assets |
| `tests/` | Unit, contract, authorization, tenancy, billing, and end-to-end coverage |
| `docs/` | Architecture, security, operations, product identity, and historical decisions |

## Engineering constraints worth reviewing

- Tenant choice comes from the request host, never a caller-supplied tenant ID.
- Existing tenant storage opens fail-loud and can never become an empty replacement studio.
- Money webhooks are signature-verified, replay-guarded, and amount-reconciled.
- Native retryable commands use idempotency keys and explicit workflow state.
- Secrets and optional integrations fail dormant rather than open.
- AI adapters require human review and do not persist directly.
- Schema, money, auth, legal, and deploy changes are human-gated by [AGENTS.md](AGENTS.md).

The [security policy](SECURITY.md) explains private reporting; the operational
[security playbook](docs/SECURITY.md) documents the implemented model.

## Known holds

Focal keeps unresolved risk visible rather than presenting a green CI badge as launch
approval. The most material open decisions and defects are tracked in:

- [#182 — native companion versus pocket studio OS scope](https://github.com/Ayyitskevin/Focal/issues/182)
- [#180 — App Store purchase/IAP strategy](https://github.com/Ayyitskevin/Focal/issues/180)
- [#179 — privacy manifest and label accuracy](https://github.com/Ayyitskevin/Focal/issues/179)
- [#185 — safe reviewer-account replacement](https://github.com/Ayyitskevin/Focal/issues/185)

These are evidence of active product and correctness work, not hidden completion claims.

## Contributing and AI transparency

Start with [CONTRIBUTING.md](CONTRIBUTING.md). This repository is openly AI-assisted;
[docs/AI-DEVELOPMENT.md](docs/AI-DEVELOPMENT.md) describes the authorship, evidence,
disclosure, privacy, and human-approval standard used for agent contributions.

## License

Focal is licensed under the [GNU Affero General Public License v3.0 only](LICENSE)
(`AGPL-3.0-only`). Network operators who modify Focal are responsible for the
corresponding-source obligations in the license, including section 13.
