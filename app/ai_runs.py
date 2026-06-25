"""Append-only AI provenance ledger (the ``ai_runs`` table, migration 065).

One row per provider call routed through ``app.providers`` — provider, model,
normalized status, review class, latency, cost/tokens when reported, and the subject it
relates to. From the app's perspective this is write-only (a future operator dashboard
reads it); a failed / disabled / invalid provider call still records a row so a
non-mutating failure stays visible instead of disappearing into logs.

It carries METADATA ONLY — never the AI output payload, never a secret — exactly the
flat dict ``providers.ProviderResult.provenance()`` returns (audit §8.3, §11.3, ADR
0006). Recording here is deliberately separate from any business-state write: the
caller decides whether to act on an ``OK`` result; this just logs that the call happened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import db

if TYPE_CHECKING:
    from .providers import ProviderResult


def record(
    result: ProviderResult,
    *,
    subject_type: str | None = None,
    subject_id: int | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
) -> int:
    """Append one provenance row for ``result``; returns the new row id.

    ``subject_type``/``subject_id`` link the run to the record it informed (e.g.
    ``("retainer_caption", 42)``). All values are bound parameters.
    """
    p = result.provenance()
    return db.run(
        """INSERT INTO ai_runs
               (capability, provider, status, review, model, latency_ms, cost_usd,
                tokens, error, subject_type, subject_id, correlation_id, idempotency_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["capability"],
            p["provider"],
            p["status"],
            p["review"],
            p["model"],
            p["latency_ms"],
            p["cost_usd"],
            p["tokens"],
            p["error"],
            subject_type,
            subject_id,
            correlation_id,
            idempotency_key,
        ),
    )
