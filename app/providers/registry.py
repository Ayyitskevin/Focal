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
    LegacyArgusVisionAdapter,
    LegacyOdysseusCaptionAdapter,
    LegacyPlutusOffersAdapter,
)
from .contracts import Capability

# The default (legacy) provider for each capability. CONTENT defaults to the Odysseus
# caption drafter; the Dionysus pack reader is addressed by name where needed.
_DEFAULT_FACTORIES = {
    Capability.VISION: LegacyArgusVisionAdapter,
    Capability.OFFERS: LegacyPlutusOffersAdapter,
    Capability.CONTENT: LegacyOdysseusCaptionAdapter,
}

# Per-capability overrides (test / shadow only). Empty in production.
_overrides: dict[Capability, Any] = {}

# Per-capability CHALLENGER adapter for shadow mode (Phase 2). A challenger runs
# alongside the legacy provider, is recorded to the ai_runs ledger for comparison, and
# NEVER writes authoritative state. Default empty: with no challenger registered, shadow
# mode is inert even when its feature flag is armed — production stays safe until a real
# challenger backend is deliberately wired in.
_challengers: dict[Capability, Any] = {}


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
    """Return the registered shadow challenger for ``capability``, or None if none is
    wired. None means shadow mode no-ops for that capability."""
    return _challengers.get(capability)


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
