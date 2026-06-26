"""Typed contracts for Mise's photography-AI capabilities (Phase 0 foundation).

This is the stable internal seam the consolidation roadmap (see
``docs/MISE-CONSOLIDATION-ROADMAP.md``) strangles the sibling sidecars onto. It
defines ONE normalized result shape for every AI capability so that — later, behind a
per-capability feature flag — a legacy external adapter (Argus / Plutus / Odysseus
today) and a future internal implementation can be compared in shadow mode and cut
over one capability at a time, with rollback to the legacy adapter.

Design rules this file encodes (audit §8.3, §11.3, §11.4, §13.4):

* Nothing here performs I/O, touches the DB, or decides money / contract /
  publication state. A ``ProviderResult`` is a *suggestion plus provenance*; the
  deterministic caller still owns every authoritative write and human-approval gate.
* Only an ``OK`` result may drive a business-state write. Every other status
  (disabled, provider error, invalid response) MUST leave records untouched —
  provider failure is separated from business-state failure.
* The result carries provenance (provider, model, latency, cost when available) so a
  future ``ai_runs`` table can persist it. Phase 0 returns it in memory only; no
  schema change ships in this slice.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class Capability(enum.Enum):
    """The photography-AI capabilities Mise consumes. Each maps to one sibling.

    PRODUCTS (Aphrodite) is a roadmap capability not yet integrated in Mise and is
    intentionally absent until it earns a slice. ALBUMS (Mnemosyne) has a dormant
    foundation — schema + deterministic layout validator — but no production path yet.
    """

    VISION = "vision"  # Argus: keywords, alt text, IPTC, culling / hero signals
    OFFERS = "offers"  # Plutus: print / album bundle recommendations
    CONTENT = "content"  # Odysseus caption / Dionysus packs: captions, copy drafts
    ALBUMS = "albums"  # Mnemosyne: curated album-spread layout proposals (human-approved)


class ResultStatus(enum.Enum):
    """Outcome of one provider call.

    Only ``OK`` may carry a usable output and drive a downstream write. Every other
    status is terminal-for-this-call and non-mutating by contract.
    """

    OK = "ok"
    DISABLED = "disabled"  # feature not configured -> dormant, no outbound call made
    PROVIDER_ERROR = "provider_error"  # timeout / unreachable / HTTP / upstream failure
    INVALID_RESPONSE = "invalid_response"  # empty / unparseable / schema-invalid body

    @property
    def is_ok(self) -> bool:
        return self is ResultStatus.OK


class ReviewRequirement(enum.Enum):
    """Approval class required before acting on an AI output (audit §11.4).

    Mise never auto-commits client-facing, money, or contract state from a model.
    Every photography-AI output in Phase 0 is at least ``HUMAN_REVIEW``.
    """

    NONE = "none"  # A0/A2: assistive or bounded reversible auto-write
    HUMAN_REVIEW = "human_review"  # A1: reversible draft; a human must accept it
    EXPLICIT_COMMIT = "explicit_commit"  # A3/A4: client-facing / money; explicit human commit


@dataclass(frozen=True)
class ProviderResult:
    """One normalized AI result plus its provenance.

    ``output`` is the capability-specific, already-normalized payload (never raw
    provider JSON, never authoritative state). ``cost_usd`` / ``tokens`` stay ``None``
    when the provider does not report them — the field exists for the internal
    adapters the roadmap adds, not because every provider supplies it today.
    """

    capability: Capability
    provider: str
    status: ResultStatus
    review: ReviewRequirement
    output: dict[str, Any] | None = None
    model: str | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    tokens: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status.is_ok

    def provenance(self) -> dict[str, Any]:
        """Flat, log/DB-friendly provenance record (carries no secrets, no stack).

        The roadmap's unified ``ai_runs`` table persists exactly these fields; Phase 0
        only returns them in memory so callers / dashboards can surface provenance
        without a schema change.
        """
        return {
            "capability": self.capability.value,
            "provider": self.provider,
            "status": self.status.value,
            "review": self.review.value,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "tokens": self.tokens,
            "error": self.error,
        }

    @classmethod
    def disabled(
        cls,
        capability: Capability,
        provider: str,
        *,
        review: ReviewRequirement = ReviewRequirement.HUMAN_REVIEW,
    ) -> ProviderResult:
        """The feature is not configured. No call was made; nothing must be written."""
        return cls(
            capability=capability,
            provider=provider,
            status=ResultStatus.DISABLED,
            review=review,
            error="not configured",
        )

    @classmethod
    def failure(
        cls,
        capability: Capability,
        provider: str,
        status: ResultStatus,
        error: str,
        *,
        review: ReviewRequirement = ReviewRequirement.HUMAN_REVIEW,
        latency_ms: int | None = None,
    ) -> ProviderResult:
        """A call was attempted but did not yield a usable output. Non-mutating."""
        if status.is_ok:
            raise ValueError("failure() requires a non-OK status")
        return cls(
            capability=capability,
            provider=provider,
            status=status,
            review=review,
            latency_ms=latency_ms,
            error=error,
        )
