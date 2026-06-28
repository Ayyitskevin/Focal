"""Capability -> adapter resolution: the strangler switch point.

Phase 0 always resolves to the **legacy external adapter** — the current production
path. The consolidation roadmap (``docs/MISE-CONSOLIDATION-ROADMAP.md``) flips a
per-capability feature flag here to route to an internal implementation in shadow
mode, then cuts over one capability at a time with rollback to legacy. Defaulting to
legacy is the whole point: introducing this seam changes nothing about behavior.

Tests (and a future shadow-mode harness) inject a different adapter for one capability
via :func:`use` without mutating env or global state permanently.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from .adapters import (
    InternalAlbumBaselineAdapter,
    LegacyArgusVisionAdapter,
    LegacyOdysseusCaptionAdapter,
    LegacyPlutusOffersAdapter,
)
from .album_challenger import InternalAlbumChallengerAdapter
from .contracts import Capability
from .products_render import ProductsRenderAdapter
from .vision_challenger import InternalVisionChallengerAdapter

# The default (legacy) provider for each capability. CONTENT defaults to the Odysseus
# caption drafter; the Dionysus pack reader is addressed by name where needed. PRODUCTS
# resolves to the dormant Aphrodite render adapter (no production path yet — ADR 0021).
# ALBUMS resolves to the deterministic in-app baseline proposer (ADR 0011/0023) — so
# resolve(ALBUMS) is a real adapter and adopting Mnemosyne is a registration + flag, not a rewrite.
_DEFAULT_FACTORIES = {
    Capability.VISION: LegacyArgusVisionAdapter,
    Capability.OFFERS: LegacyPlutusOffersAdapter,
    Capability.CONTENT: LegacyOdysseusCaptionAdapter,
    Capability.PRODUCTS: ProductsRenderAdapter,
    Capability.ALBUMS: InternalAlbumBaselineAdapter,
}

# Per-capability overrides (test / shadow only). Empty in production.
_overrides: dict[Capability, Any] = {}

# Per-capability CHALLENGER adapter for shadow mode (Phase 2). A challenger runs
# alongside the legacy provider, is recorded to the ai_runs ledger for comparison, and
# NEVER writes authoritative state. Overrides (test/shadow) take precedence; otherwise a
# default challenger factory is consulted and returned only if it is configured/enabled,
# so shadow stays inert until a real challenger backend is deliberately armed by env.
_challengers: dict[Capability, Any] = {}

_DEFAULT_CHALLENGER_FACTORIES = {
    Capability.VISION: InternalVisionChallengerAdapter,
    Capability.ALBUMS: InternalAlbumChallengerAdapter,
}


def resolve(capability: Capability) -> Any:
    """Return the adapter for ``capability`` — an override if one is registered,
    otherwise a fresh legacy adapter (the production default)."""
    if capability in _overrides:
        return _overrides[capability]
    try:
        return _DEFAULT_FACTORIES[capability]()
    except KeyError:
        raise ValueError(f"no provider registered for capability {capability!r}") from None


def challenger(capability: Capability) -> Any | None:
    """Return the shadow challenger for ``capability`` — a registered override if present,
    else the default challenger backend if it is configured/enabled, else None. None means
    shadow mode no-ops for that capability."""
    if capability in _challengers:
        return _challengers[capability]
    factory = _DEFAULT_CHALLENGER_FACTORIES.get(capability)
    if factory is None:
        return None
    adapter = factory()
    return adapter if adapter.is_enabled() else None


@contextmanager
def use_challenger(capability: Capability, adapter: Any):
    """Temporarily register a shadow ``challenger`` for ``capability`` for the block."""
    sentinel = object()
    previous = _challengers.get(capability, sentinel)
    _challengers[capability] = adapter
    try:
        yield adapter
    finally:
        if previous is sentinel:
            _challengers.pop(capability, None)
        else:
            _challengers[capability] = previous


@contextmanager
def use(capability: Capability, adapter: Any):
    """Temporarily route ``capability`` to ``adapter`` (e.g. a mock) for the block."""
    sentinel = object()
    previous = _overrides.get(capability, sentinel)
    _overrides[capability] = adapter
    try:
        yield adapter
    finally:
        if previous is sentinel:
            _overrides.pop(capability, None)
        else:
            _overrides[capability] = previous


def reset() -> None:
    """Drop all overrides and challengers — restores the legacy default everywhere."""
    _overrides.clear()
    _challengers.clear()


# ── production provider selection (the vision cutover seam) ──────────────────────

# Names an operator may set MISE_VISION_PROVIDER to, mapped to a factory. Aliases let the
# config read naturally ('qwen'/'challenger' both mean the Qwen3-VL adapter).
_VISION_PROVIDER_FACTORIES = {
    "argus": LegacyArgusVisionAdapter,
    "qwen3-vl": InternalVisionChallengerAdapter,
    "qwen": InternalVisionChallengerAdapter,
    "challenger": InternalVisionChallengerAdapter,
}
_VISION_DEFAULT = "argus"


def active_vision_provider(requested: str | None = None) -> dict:
    """Resolve which provider serves PRODUCTION vision, with a hard interlock.

    ``requested`` defaults to ``config.VISION_PROVIDER``. A provider is honored only if it
    is known, declares ``serves_production``, AND is configured/enabled; otherwise this
    falls back to Argus and says why. Returns ``{requested, effective, eligible, reason}``.

    This is the seam the live vision trigger will consult once a production-capable
    challenger exists; today it enforces the interlock and surfaces the promotion status, so
    a flag pointing at an eval-only/unconfigured provider can NEVER quietly route production
    into a non-writeback path. The challenger is eval-only (``serves_production = False``)
    until it has an asset-writeback path.
    """
    from .. import config

    req = (requested if requested is not None else config.VISION_PROVIDER) or _VISION_DEFAULT
    req = req.strip().lower()

    def _fallback(reason: str) -> dict:
        return {"requested": req, "effective": _VISION_DEFAULT, "eligible": False, "reason": reason}

    factory = _VISION_PROVIDER_FACTORIES.get(req)
    if factory is None:
        return _fallback(f"unknown vision provider {req!r}; using {_VISION_DEFAULT}")
    adapter = factory()
    name = getattr(adapter, "name", req)
    if not getattr(adapter, "serves_production", False):
        return _fallback(
            f"{name} is eval-only (no production writeback yet); using {_VISION_DEFAULT}"
        )
    if not adapter.is_enabled():
        return _fallback(f"{name} is not configured; using {_VISION_DEFAULT}")
    return {
        "requested": req,
        "effective": name,
        "eligible": True,
        "reason": f"{name} serves production",
    }


# ── production album-proposer selection (the album adopt seam, ADR 0023) ──────────

# Named challengers an operator may set MISE_ALBUM_PROVIDER to. 'baseline'/'internal' mean the
# deterministic in-app proposer (always available); a challenger is honored only if production-capable.
_ALBUM_PROVIDER_FACTORIES = {
    "mnemosyne": InternalAlbumChallengerAdapter,
    "challenger": InternalAlbumChallengerAdapter,
}
_ALBUM_DEFAULT = "baseline"


def active_album_provider(requested: str | None = None) -> dict:
    """Resolve which provider proposes PRODUCTION album layouts, with a hard interlock — the
    album analog of :func:`active_vision_provider`.

    ``requested`` defaults to ``config.ALBUM_PROVIDER``. ``baseline`` (the default) is the
    deterministic in-app proposer and is always eligible. A named challenger (Mnemosyne) is
    honored only if it declares ``serves_production`` AND is configured; otherwise this falls
    back to the baseline and says why. So a flag pointing at an unproven/unconfigured proposer
    can NEVER silently replace the baseline — adoption stays a deliberate flip once the
    challenger's layouts beat the baseline (ADR 0011/0023). Returns
    ``{requested, effective, eligible, reason}``.
    """
    from .. import config

    req = (requested if requested is not None else config.ALBUM_PROVIDER) or _ALBUM_DEFAULT
    req = req.strip().lower()
    if req in ("baseline", "internal"):
        return {
            "requested": req,
            "effective": _ALBUM_DEFAULT,
            "eligible": True,
            "reason": "deterministic baseline proposer",
        }

    def _fallback(reason: str) -> dict:
        return {"requested": req, "effective": _ALBUM_DEFAULT, "eligible": False, "reason": reason}

    factory = _ALBUM_PROVIDER_FACTORIES.get(req)
    if factory is None:
        return _fallback(f"unknown album provider {req!r}; using {_ALBUM_DEFAULT}")
    adapter = factory()
    name = getattr(adapter, "name", req)
    if not getattr(adapter, "serves_production", False):
        return _fallback(f"{name} is eval-only (not proven vs baseline); using {_ALBUM_DEFAULT}")
    if not adapter.is_enabled():
        return _fallback(f"{name} is not configured; using {_ALBUM_DEFAULT}")
    return {
        "requested": req,
        "effective": name,
        "eligible": True,
        "reason": f"{name} serves production",
    }


def album_proposer_adapter():
    """The eligible production ALBUMS challenger adapter, or ``None`` to use the deterministic
    baseline — the consumer twin of :func:`active_album_provider` for
    ``app.albums._provider_placements``. A registered override (test/shadow) wins; otherwise the
    interlock decides. ``None`` means "use the in-app baseline proposer" (the default + every
    fallback), so the baseline path stays byte-identical until a deliberate promotion."""
    if Capability.ALBUMS in _overrides:
        return _overrides[Capability.ALBUMS]
    status = active_album_provider()
    if not status["eligible"] or status["effective"] == _ALBUM_DEFAULT:
        return None
    factory = _ALBUM_PROVIDER_FACTORIES.get(status["requested"])
    return factory() if factory else None
