#!/usr/bin/env python3
"""Fail-closed tombstone for the unsafe reviewer-demo provisioner.

The implementation merged in PR #178 is not safe to run against hosted state.
Issue #185 records the proven adoption, billing, booking, and session-continuity
failures and the replacement contract. Keep this module importable so an old
operator command fails with an actionable message instead of disappearing or,
worse, continuing to mutate tenant state.

This file intentionally imports no Mise application modules and reads no
environment variables. Both the Python entry point and the callable API stop
before a control database, tenant database, credential, or billing field can be
opened or changed.
"""

from __future__ import annotations

BLOCKER_URL = "https://github.com/Ayyitskevin/mise/issues/185"
DISABLED_MESSAGE = (
    "Reviewer demo provisioning is disabled: the former seeder is unsafe for "
    "hosted state. Do not run it on local, staging, or production control data. "
    f"Follow the replacement design and status at {BLOCKER_URL}."
)


def seed_demo_tenant(
    *,
    slug: str,
    studio_name: str,
    owner_email: str,
    password: str,
    preset: str,
) -> dict:
    """Refuse the legacy callable contract before touching hosted state."""
    del slug, studio_name, owner_email, password, preset
    raise SystemExit(DISABLED_MESSAGE)


def main() -> None:
    """Refuse the legacy CLI contract before reading configuration."""
    raise SystemExit(DISABLED_MESSAGE)


if __name__ == "__main__":
    main()
