"""Lifecycle helpers for native-command idempotency receipts.

Receipts are useful only through their authenticated session's absolute lifetime.
Consequential routes prune opportunistically in their writer transaction, and the
recurring scheduler supplies the time-based cleanup path even when no later command
arrives. Callers that already own a transaction use ``prune_expired_in_transaction``;
the standalone wrapper owns its commit.
"""

import sqlite3
import time

from . import db


def now_ts() -> int:
    return int(time.time())


def prune_expired_in_transaction(
    con: sqlite3.Connection,
    *,
    cutoff: int | None = None,
) -> int:
    threshold = now_ts() if cutoff is None else cutoff
    cursor = con.execute(
        "DELETE FROM api_idempotency_replays WHERE expires_at <= ?",
        (threshold,),
    )
    return cursor.rowcount


def prune_expired(*, cutoff: int | None = None) -> int:
    con = db.connect()
    try:
        count = prune_expired_in_transaction(con, cutoff=cutoff)
        con.commit()
        return count
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
