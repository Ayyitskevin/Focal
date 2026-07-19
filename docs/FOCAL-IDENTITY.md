# Focal product identity

Focal is a self-hostable, local-first studio operating system for solo photographers
and small creative studios.

Its promise is simple: **keep the whole studio in focus** — from first inquiry to
final frame.

## Product surface

Focal brings the core studio workflow into one modular application:

- inquiry intake, client records, and projects;
- booking, scheduling, shot lists, and preparation;
- proposals, contracts, invoices, deposits, and payment state;
- private galleries, proofing, favorites, comments, downloads, and delivery;
- follow-up, retention, and operator activity views;
- a native iOS companion for owner and client journeys;
- optional AI-assisted vision, copy, and product workflows.

The web application is the primary business surface. The iOS application is a focused
companion, not a promise of web parity.

## Current status

Focal is in active development and private-beta preparation.

- Self-hosted mode is the safest review path.
- Hosted mode is implemented in parts but is not a public service or launch claim.
- There is no App Store release or generally available hosted signup.
- Demo data and reviewer flows must remain disposable and must never touch real
  customer data or production credentials.

A green test suite is evidence about code behavior, not evidence of commercial launch,
App Store readiness, or external-user trust.

## Architecture posture

Focal remains a deliberately boring modular monolith:

- FastAPI + server-rendered HTMX for the web surface;
- SQLite/WAL and local media in self-hosted mode;
- a native SwiftUI iOS companion;
- optional stateless AI/media workers behind narrow provider contracts;
- deterministic validation and human approval before AI output becomes client-visible,
  financial, contractual, or published state.

The revenue path must work without an AI provider, Notion, or a sibling repository.

## Boundaries

Focal is a standalone product repository.

- kleephotography is the separate public photography website and business front door.
  Focal is not its hidden backend and this repository must not be described as that
  site's deployment source of truth.
- Hestia is a separate greenfield commercial SaaS track. Focal does not depend on
  Hestia at runtime and does not merge its history.
- Argus, Plutus, Mnemosyne, Dionysus, Aphrodite, Odysseus, and other sibling projects
  may inform capability design, but Focal owns its own data, tests, and runtime
  boundaries.

## Naming migration

The public product name is now **Focal**.

The repository currently retains legacy Mise/MISE_* identifiers in internal
configuration, filesystem paths, service names, Swift project names, and historical
documents. Those identifiers are compatibility-sensitive and should be migrated in a
separate, explicitly tested change. Do not rename environment variables, database
keys, deployment units, or API contracts as part of a copy-only branding change.
