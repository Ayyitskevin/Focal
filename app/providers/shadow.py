"""Shadow-mode comparison for the providers facade (Phase 2).

Pure functions only — no I/O, no DB. Given a legacy result and a challenger result for
the same subject, produce a structured, log/ledger-friendly comparison: did both
succeed, do their statuses agree, and what are the latency / cost deltas. The shadow
runner (``app.vision_shadow``) records each result to the ai_runs ledger and stores this
comparison; a future evaluation reads the ledger to decide whether a challenger is ready
to be promoted (audit §9.5). The challenger output is NEVER written to authoritative
state — comparison is observation only.
"""

from __future__ import annotations

from typing import Any

from .contracts import ProviderResult


def _delta(a: float | int | None, b: float | int | None) -> float | int | None:
    """challenger minus legacy, or None if either side is missing."""
    if a is None or b is None:
        return None
    return b - a


def compare(legacy: ProviderResult, challenger: ProviderResult) -> dict[str, Any]:
    """Compare a challenger ``ProviderResult`` against the legacy baseline.

    Returns a flat dict safe to log or persist alongside the two provenance rows. It
    asserts nothing and mutates nothing — it only describes the difference.
    """
    return {
        "capability": legacy.capability.value,
        "legacy_provider": legacy.provider,
        "challenger_provider": challenger.provider,
        "legacy_status": legacy.status.value,
        "challenger_status": challenger.status.value,
        "both_ok": legacy.ok and challenger.ok,
        "status_agree": legacy.status is challenger.status,
        "legacy_model": legacy.model,
        "challenger_model": challenger.model,
        "latency_delta_ms": _delta(legacy.latency_ms, challenger.latency_ms),
        "cost_delta_usd": _delta(legacy.cost_usd, challenger.cost_usd),
    }
