"""Mise photography-AI provider facade (Phase 0 foundation slice).

A small, typed internal contract over the AI capabilities Mise consumes — vision
(Argus), content (Odysseus / Dionysus), and products (Aphrodite, dormant). It wraps
today's external sidecars as *legacy adapters* behind one normalized result type
(:class:`ProviderResult`) and resolves capability -> adapter through a registry that
defaults to the legacy path. (The consumer-upsell capabilities OFFERS/Plutus and
ALBUMS/Mnemosyne were decommissioned — migration 075.)

The facade is the stable seam the consolidation roadmap migrates callers onto, one
capability at a time, behind a feature flag and in shadow mode. See ``docs/PHASE-0-SLICE.md``.
"""

from __future__ import annotations

from .adapters import (
    LegacyArgusVisionAdapter,
    LegacyDionysusPackAdapter,
    LegacyOdysseusCaptionAdapter,
)
from .contracts import (
    Capability,
    ProviderResult,
    ResultStatus,
    ReviewRequirement,
)
from .products_render import ProductsRenderAdapter
from .registry import (
    challenger,
    reset,
    resolve,
    use,
    use_challenger,
)
from .shadow import compare
from .vision_challenger import InternalVisionChallengerAdapter

__all__ = [
    "Capability",
    "ProviderResult",
    "ResultStatus",
    "ReviewRequirement",
    "LegacyArgusVisionAdapter",
    "LegacyOdysseusCaptionAdapter",
    "LegacyDionysusPackAdapter",
    "InternalVisionChallengerAdapter",
    "ProductsRenderAdapter",
    "resolve",
    "use",
    "reset",
    "challenger",
    "use_challenger",
    "compare",
]
