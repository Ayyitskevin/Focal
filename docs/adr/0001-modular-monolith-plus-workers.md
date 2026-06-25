# ADR 0001 — Modular monolith plus optional stateless workers

**Status:** Accepted (Phase 0)
**Date:** 2026-06-25
**Deciders:** Kevin (owner), principal engineer

## Context

Mise is a live, single-owner photography production system: FastAPI + HTMX + SQLite,
two route packages, an in-process job queue, one admin auth, one audit trail **[CODE]**.
The sibling repos (Argus, Plutus, Dionysus, Mnemosyne, Aphrodite) are separate services
that duplicate auth, queue, deploy, and backup, causing "sidecar sprawl" and auth drift
(e.g. a Plutus 401, audit §3.2).

Two architectures were on the table:
- **Audit Pattern E (selected in the audit):** keep Mise on flow, move orchestration +
  durable jobs + provenance to a **PostgreSQL/pgvector control plane on mickey**, keep
  siblings as worker services.
- **Master-prompt directive (this project):** evolve Mise itself into a **modular
  monolith** that owns the job/review lifecycle, with mickey/strix/cloud as **stateless
  workers** owning no business records.

## Decision

Adopt the **single-tenant modular monolith + optional stateless compute workers**.
Capabilities (vision, offers, content, albums, products) become **modules inside the
Mise process**, sharing one auth, one audit trail, one job queue, one storage
abstraction. Heavy/experimental model execution runs on **replaceable workers** that own
no authoritative state. Mise owns the job and review lifecycle.

## Why this over the audit's control-plane-on-mickey

Both keep Mise authoritative and workers disposable; they differ in **where durable
job/provenance state lives**. For a solo operator, a second stateful service (Postgres
on mickey) adds a recovery layer, a second backup chain, and a second failure domain for
no proven concurrency need. Mise's SQLite + thread-pool queue already survive restarts
and are restore-tested nightly. Keeping job/provenance state in Mise means **one spine,
one backup, one operator view**. See [ADR 0005](0005-sqlite-retention.md).

## Consequences

- **Positive:** fewer deploy/auth/backup surfaces; one coherent operator experience;
  workers are rebuildable from authoritative inputs; the `providers` seam lets a
  capability's backend change without touching the workflow.
- **Negative / risk:** the monolith is a single host blast radius (mitigated by the
  restore-tested backup chain + clean-rebuild runbook); a runaway worker could still load
  a shared box (mitigated by worker concurrency limits, audit §5.3).
- **Constraints:** no new microservice for fashion; no Kubernetes/Redis; workers stay
  stateless.

## Reopen triggers (measured)

Revisit a separate control plane only if: SQLite shows measured write contention under
real load, OR durable cross-service job volume exceeds what the in-process queue handles
within SLO, OR a second always-on consumer genuinely needs shared state. (audit §15.4)

## Alternatives considered

- **Postgres control plane on mickey (audit Pattern E):** deferred, not rejected —
  recorded as the fallback if a reopen trigger fires.
- **Eventual Hestia migration (Pattern F):** rejected for KLP now; Hestia stays
  independent behind its migration gates (audit §19.1).
- **Status quo (Pattern A, separate sidecars):** rejected as target; stabilize only.
