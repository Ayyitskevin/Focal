"""Mise photography-AI provider facade (Phase 0 foundation slice).

A small, typed internal contract over the AI capabilities Mise consumes — vision
(Argus), offers (Plutus), and content (Odysseus / Dionysus). It wraps today's external
sidecars as *legacy adapters* behind one normalized result type (:class:`ProviderResult`)
and resolves capability -> adapter through a registry that defaults to the legacy path.

This package is **additive and dormant**: nothing in the running app imports it yet, so
shipping it changes no production behavior, route, env var, or schema. It exists to be
the stable seam the consolidation roadmap migrates callers onto, one capability at a
time, behind a feature flag and in shadow mode. See ``docs/PHASE-0-SLICE.md``.
"""

from __future__ import annotations

from .adapters import (
    LegacyArgusVisionAdapter,
    LegacyDionysusPackAdapter,
    LegacyOdysseusCaptionAdapter,
    LegacyPlutusOffersAdapter,
)
from .contracts import (
    Capability,
    ProviderResult,
    ResultStatus,
    ReviewRequirement,
)
from .registry import challenger, reset, resolve, use, use_challenger
from .shadow import compare

__all__ = [
    "Capability",
    "ProviderResult",
    "ResultStatus",
    "ReviewRequirement",
    "LegacyArgusVisionAdapter",
    "LegacyPlutusOffersAdapter",
    "LegacyOdysseusCaptionAdapter",
    "LegacyDionysusPackAdapter",
    "resolve",
    "use",
    "reset",
    "challenger",
    "use_challenger",
    "compare",
]
