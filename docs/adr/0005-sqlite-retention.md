# ADR 0005 — Retain SQLite; do not adopt PostgreSQL on spec

**Status:** Accepted (Phase 0)
**Date:** 2026-06-25

## Context

The audit's selected pattern proposes PostgreSQL + pgvector as a control-plane DB on
mickey. Mise today runs **SQLite in WAL mode with short-lived connections** safe across
job threads **[CODE]** (`app/db.py`: `journal_mode=WAL`, `busy_timeout=30000`,
`foreign_keys=ON`), plus a SQLite-backed job queue that re-queues crashed jobs on
startup (`app/jobs.py`). It is restore-tested nightly **[DEPLOYED]** (`ops/BACKUP.md`).
For one operator and low write concurrency, this is fit for purpose.

## Decision

**Keep SQLite as Mise's single business database.** Do not introduce PostgreSQL, Redis,
or a separate vector store **unless measured contention, durability, or operational
requirements justify it.** AI provenance and album/product job state will be **additive
SQLite tables** (e.g. `ai_runs`), not a new datastore (roadmap Phase 1.1, 5.1, 6).

## Reopen criteria (must be measured, not assumed)

Adopt Postgres (or another store) only when a named threshold is crossed:

- **Write contention:** sustained `SQLITE_BUSY`/lock waits under real load that WAL +
  `busy_timeout` + queue serialization cannot absorb.
- **Concurrency:** a genuine second always-on writer/consumer needs shared transactional
  state beyond what the in-process queue provides.
- **Durability/scale:** DB or index growth, or a queue volume, that exceeds SQLite's
  comfortable envelope for the SLOs in audit §5.1.
- **Vector retrieval at scale:** an embeddings workload that a SQLite-side approach
  cannot serve within SLO (then pgvector becomes justified — audit §8).

Each is a benchmark gate (audit §15.4), not a default.

## Consequences

- **Positive:** one backup chain, one restore drill, one failure domain, zero new infra;
  matches the modular-monolith decision (ADR 0001) and sole-authority decision (ADR
  0002).
- **Negative:** SQLite single-writer semantics require disciplined transactions
  (`db.tx()`) and bounded job concurrency; heavy vector search would need a future
  decision.
- **Guardrail:** any new migration is **red-light** — forward-only, additive, PR'd, and
  human-merged (`AGENTS.md`). No schema change ships in Phase 0.

## Alternatives considered

- **Postgres now (audit Pattern E):** deferred; recorded as the fallback if a reopen
  criterion is met.
- **SQLite + external Redis/Celery queue:** rejected — the existing durable jobs table +
  thread pool is sufficient at current scale (audit §6.4 "Redis/Celery: defer").
