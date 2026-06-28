"""Client-delivery cull gate — the enforcement half of AI-assisted culling (ADRs 0030 / 0031).

A frame the operator CUT in the cull deck (assets.cull_state='cut', migration 077) must not reach
a client: not listed in the gallery, not served as a file, not zipped, not shown on the portal.
This module is the single place that decides "is the gate on, and what SQL drops a cut frame".

Enforcement is gated on MISE_CULL_UI so the *whole* feature — authoring AND delivery — is one env
var: flip it off and client delivery returns to the pre-cull path exactly (the strangler-rollback
invariant). The deck only ever sets 'cut' while the flag is on, so in practice the behaviour is
identical; the flag just keeps rollback clean and the feature dormant-until-armed like its siblings.

NULL (undecided) and 'keep' always deliver — so an un-culled gallery, and every existing row, is
unaffected (§11.4-safe). The public marketing site is intentionally NOT gated here: it shows only
portfolio=1 assets, a separate, explicit publication intent (see ADR 0032).
"""

from . import config


def on() -> bool:
    """True when the delivery gate is active (same switch as the authoring deck)."""
    return bool(config.CULL_UI)


def clause(alias: str = "") -> str:
    """SQL AND-fragment that drops cut frames from a client query — or '' when the gate is off.

    `alias` is the table alias used for `assets` in the query ('a' -> 'a.cull_state'); pass '' when
    the table is unaliased. SQLite's `IS NOT` is NULL-safe, so NULL (undecided) and 'keep' both
    pass in one expression while 'cut' is excluded. The fragment carries no bind parameters, so it
    is safe to interpolate and never shifts a query's `?` placeholders.
    """
    if not on():
        return ""
    col = f"{alias}.cull_state" if alias else "cull_state"
    return f" AND {col} IS NOT 'cut'"
