"""Identity-bound transactions for work that crosses an external wait.

Retired hosted slugs are now permanently reserved, but a database can still be
parked or replaced at an operator-controlled path. Any provider call that releases
its initial SQLite connection must capture the migration-owned database identity
and prove it again, with offboarding still false, in the same transaction as every
later write.
"""

from __future__ import annotations

import hmac
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class RuntimeUnavailable(Exception):
    """The original database is gone, offboarding, malformed, or replaced."""


def current(con: sqlite3.Connection) -> str | None:
    try:
        row = con.execute(
            """SELECT database_identity,offboarding
                 FROM mobile_runtime_state WHERE singleton=1"""
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row["offboarding"] != 0:
        return None
    identity = str(row["database_identity"] or "")
    if len(identity) != 32 or any(character not in "0123456789abcdef" for character in identity):
        return None
    return identity


@contextmanager
def bound_transaction(database_path: Path, identity: str):
    """Open an existing DB and match identity before yielding a write transaction."""

    try:
        con = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=rw",
            uri=True,
            timeout=30,
        )
    except sqlite3.Error as exc:
        raise RuntimeUnavailable from exc
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA secure_delete=ON")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("BEGIN IMMEDIATE")
        current_identity = current(con)
        if current_identity is None or not hmac.compare_digest(
            current_identity.encode(), identity.encode()
        ):
            raise RuntimeUnavailable
        yield con
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()
