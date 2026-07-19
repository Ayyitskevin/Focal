"""Transactional, per-effect dispatch for booking reschedule workflows.

The tenant database is the queue boundary. A caller inserts the six effect rows
inside the booking command transaction; dispatch later claims one row under a
short SQLite writer lock and performs provider I/O only after releasing it.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from uuid import uuid4

from . import config, db, mailer

log = logging.getLogger("mise.booking_workflow")

EFFECT_KINDS = (
    ("client_cancel_ics", 10),
    ("client_request_ics", 20),
    ("studio_reschedule_notice", 30),
    ("notion_booking_patch", 40),
    ("notion_session_link", 50),
    ("google_calendar_move", 60),
)

TERMINAL_STATUSES = frozenset({"succeeded", "skipped", "blocked"})
_SAFE_WORKFLOW_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_SAFE_ERROR_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,96}\Z")
_BACKOFF_BASE_SECONDS = 5
_BACKOFF_CAP_SECONDS = 3600


class NotApplicable(Exception):
    """An effect has no work for this tenant/booking and may be skipped."""

    def __init__(self, code: str = "not_applicable") -> None:
        self.code = code if _SAFE_ERROR_CODE.fullmatch(code) else "not_applicable"
        super().__init__(code)


class WorkflowBusy(Exception):
    """A conflicting provider effect is already running for this replacement."""


def now_ts() -> int:
    return int(time.time())


def available() -> bool:
    """Whether the public command and its dispatcher are deliberately armed."""
    return bool(getattr(config, "BOOKING_WORKFLOW_ENABLED", False) and mailer.configured())


def _validated_workflow_id(workflow_id: str) -> str:
    if not isinstance(workflow_id, str) or not _SAFE_WORKFLOW_ID.fullmatch(workflow_id):
        raise ValueError("workflow_id must be a safe 1-128 character identifier")
    return workflow_id


def _validated_booking_id(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def enqueue_reschedule(
    con: sqlite3.Connection,
    source_booking_id: int,
    replacement_booking_id: int,
    workflow_id: str,
) -> None:
    """Insert one immutable effect set without committing the caller's transaction."""
    workflow_id = _validated_workflow_id(workflow_id)
    source_booking_id = _validated_booking_id(source_booking_id, "source_booking_id")
    replacement_booking_id = _validated_booking_id(replacement_booking_id, "replacement_booking_id")
    if source_booking_id == replacement_booking_id:
        raise ValueError("source and replacement bookings must differ")

    existing = con.execute(
        """SELECT DISTINCT workflow_id, source_booking_id, replacement_booking_id
             FROM booking_workflow_effects
            WHERE workflow_id=?
               OR (source_booking_id=? AND replacement_booking_id=?)""",
        (workflow_id, source_booking_id, replacement_booking_id),
    ).fetchall()
    for row in existing:
        if (
            row["workflow_id"] != workflow_id
            or int(row["source_booking_id"]) != source_booking_id
            or int(row["replacement_booking_id"]) != replacement_booking_id
        ):
            raise ValueError("workflow_id or booking pair is already assigned")

    created_at = now_ts()
    for effect_kind, sequence_no in EFFECT_KINDS:
        con.execute(
            """INSERT INTO booking_workflow_effects
               (workflow_id, source_booking_id, replacement_booking_id,
                effect_kind, sequence_no, next_attempt_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT DO NOTHING""",
            (
                workflow_id,
                source_booking_id,
                replacement_booking_id,
                effect_kind,
                sequence_no,
                created_at,
                created_at,
                created_at,
            ),
        )


def _lease_seconds() -> int:
    return max(1, int(getattr(config, "BOOKING_WORKFLOW_LEASE_SECONDS", 120)))


def _max_attempts() -> int:
    return max(1, int(getattr(config, "BOOKING_WORKFLOW_MAX_ATTEMPTS", 8)))


def _claim(*, workflow_id: str | None = None) -> dict | None:
    now = now_ts()
    token = uuid4().hex
    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        exhausted = con.execute(
            """UPDATE booking_workflow_effects
                  SET status='blocked', next_attempt_at=NULL,
                      lease_token=NULL, lease_expires_at=NULL,
                      provider_ref=NULL, error_class='LeaseExpired',
                      error_code='lease_expired', completed_at=?, updated_at=?
                WHERE status='running' AND lease_expires_at <= ?
                  AND attempts >= ?
                  AND (? IS NULL OR workflow_id=?)""",
            (now, now, now, _max_attempts(), workflow_id, workflow_id),
        )
        if exhausted.rowcount:
            log.warning(
                "blocked %s booking workflow effect(s) after repeated lease expiry",
                exhausted.rowcount,
            )
        row = con.execute(
            """SELECT e.id
                 FROM booking_workflow_effects AS e
                WHERE (? IS NULL OR e.workflow_id=?)
                  AND (
                      (e.status IN ('pending','retry') AND e.next_attempt_at <= ?)
                      OR
                      (e.status='running' AND e.lease_expires_at <= ?)
                  )
                  AND (
                      e.effect_kind <> 'client_request_ics'
                      OR EXISTS (
                          SELECT 1
                            FROM booking_workflow_effects AS dependency
                           WHERE dependency.workflow_id=e.workflow_id
                             AND dependency.effect_kind='client_cancel_ics'
                             AND dependency.status IN ('succeeded','skipped')
                      )
                  )
                ORDER BY COALESCE(e.next_attempt_at, e.lease_expires_at, e.created_at),
                         e.sequence_no, e.id
                LIMIT 1""",
            (workflow_id, workflow_id, now, now),
        ).fetchone()
        if row is None:
            con.execute("COMMIT")
            return None
        updated = con.execute(
            """UPDATE booking_workflow_effects
                  SET status='running', attempts=attempts+1,
                      next_attempt_at=NULL, lease_token=?, lease_expires_at=?,
                      completed_at=NULL, updated_at=?
                WHERE id=?
                  AND (
                      (status IN ('pending','retry') AND next_attempt_at <= ?)
                      OR
                      (status='running' AND lease_expires_at <= ?)
                  )""",
            (token, now + _lease_seconds(), now, row["id"], now, now),
        )
        if updated.rowcount != 1:
            con.execute("ROLLBACK")
            return None
        claimed = con.execute(
            "SELECT * FROM booking_workflow_effects WHERE id=?", (row["id"],)
        ).fetchone()
        con.execute("COMMIT")
        return dict(claimed) if claimed is not None else None
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()


def _safe_error_class(exc: Exception) -> str:
    name = type(exc).__name__
    return name[:96] if name else "Exception"


def _safe_error_code(exc: Exception) -> str:
    candidate = getattr(exc, "code", None)
    if candidate is None:
        candidate = getattr(exc, "status_code", None)
    candidate = str(candidate) if candidate is not None else "exception"
    return candidate if _SAFE_ERROR_CODE.fullmatch(candidate) else "exception"


def _provider_ref(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int)):
        raise ValueError("invalid_provider_ref")
    result = str(value)
    if not result or len(result) > 255 or any(ord(char) < 32 for char in result):
        raise ValueError("invalid_provider_ref")
    return result


def _finish(
    claimed: dict,
    *,
    status: str,
    provider_ref: str | None = None,
    error_class: str | None = None,
    error_code: str | None = None,
) -> bool:
    completed_at = now_ts()
    con = db.connect()
    try:
        updated = con.execute(
            """UPDATE booking_workflow_effects
                  SET status=?, next_attempt_at=NULL,
                      lease_token=NULL, lease_expires_at=NULL,
                      provider_ref=?, error_class=?, error_code=?,
                      completed_at=?, updated_at=?
                WHERE id=? AND status='running' AND lease_token=?""",
            (
                status,
                provider_ref,
                error_class,
                error_code,
                completed_at,
                completed_at,
                claimed["id"],
                claimed["lease_token"],
            ),
        )
        con.commit()
        return updated.rowcount == 1
    finally:
        con.close()


def _backoff_seconds(attempts: int) -> int:
    exponent = min(max(attempts - 1, 0), 20)
    return min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2**exponent))


def _fail(claimed: dict, exc: Exception) -> bool:
    now = now_ts()
    blocked = int(claimed["attempts"]) >= _max_attempts()
    status = "blocked" if blocked else "retry"
    next_attempt_at = None if blocked else now + _backoff_seconds(int(claimed["attempts"]))
    completed_at = now if blocked else None
    error_class = _safe_error_class(exc)
    error_code = _safe_error_code(exc)
    con = db.connect()
    try:
        updated = con.execute(
            """UPDATE booking_workflow_effects
                  SET status=?, next_attempt_at=?,
                      lease_token=NULL, lease_expires_at=NULL,
                      provider_ref=NULL, error_class=?, error_code=?,
                      completed_at=?, updated_at=?
                WHERE id=? AND status='running' AND lease_token=?""",
            (
                status,
                next_attempt_at,
                error_class,
                error_code,
                completed_at,
                now,
                claimed["id"],
                claimed["lease_token"],
            ),
        )
        con.commit()
        if updated.rowcount == 1:
            log.warning(
                "booking workflow %s effect %s attempt %s -> %s (%s/%s)",
                claimed["workflow_id"],
                claimed["effect_kind"],
                claimed["attempts"],
                status,
                error_class,
                error_code,
            )
        return updated.rowcount == 1
    finally:
        con.close()


def _execute(claimed: dict) -> None:
    # Lazy import lets booking_notify import and raise this module's
    # NotApplicable without creating a module-import cycle.
    from . import booking_notify

    try:
        provider_ref = _provider_ref(
            booking_notify.run_reschedule_effect(
                claimed["effect_kind"],
                int(claimed["source_booking_id"]),
                int(claimed["replacement_booking_id"]),
            )
        )
    except NotApplicable as exc:
        if not _finish(
            claimed,
            status="skipped",
            error_class=_safe_error_class(exc),
            error_code=_safe_error_code(exc),
        ):
            log.warning(
                "booking workflow %s effect %s lost its lease before skip finalization",
                claimed["workflow_id"],
                claimed["effect_kind"],
            )
    except db.ExistingDatabaseUnavailable:
        raise
    except Exception as exc:
        if not _fail(claimed, exc):
            log.warning(
                "booking workflow %s effect %s lost its lease before failure finalization",
                claimed["workflow_id"],
                claimed["effect_kind"],
            )
    else:
        if not _finish(claimed, status="succeeded", provider_ref=provider_ref):
            log.warning(
                "booking workflow %s effect %s lost its lease before success finalization",
                claimed["workflow_id"],
                claimed["effect_kind"],
            )


def dispatch_workflow(workflow_id: str) -> int:
    """Attempt every currently eligible effect for one workflow."""
    workflow_id = _validated_workflow_id(workflow_id)
    if not available():
        return 0
    attempted = 0
    while claimed := _claim(workflow_id=workflow_id):
        attempted += 1
        _execute(claimed)
    return attempted


def sweep(limit: int | None = None) -> int:
    """Attempt at most limit due effects in the current tenant database."""
    if not available():
        return 0
    if limit is None:
        limit = int(getattr(config, "BOOKING_WORKFLOW_BATCH_SIZE", 20))
    limit = max(0, int(limit))
    attempted = 0
    while attempted < limit:
        claimed = _claim()
        if claimed is None:
            break
        attempted += 1
        _execute(claimed)
    return attempted


def retry_in_transaction(con: sqlite3.Connection, workflow_id: str) -> int:
    """Make blocked effects due again without committing the caller's transaction."""
    workflow_id = _validated_workflow_id(workflow_id)
    now = now_ts()
    updated = con.execute(
        """UPDATE booking_workflow_effects
              SET status='retry', attempts=0, next_attempt_at=?,
                  lease_token=NULL, lease_expires_at=NULL,
                  provider_ref=NULL, error_class=NULL, error_code=NULL,
                  completed_at=NULL, updated_at=?
            WHERE workflow_id=? AND status='blocked'""",
        (now, now, workflow_id),
    )
    return updated.rowcount


def retry(workflow_id: str) -> bool:
    """Compatibility wrapper that owns the blocked-effect retry transaction."""
    con = db.connect()
    try:
        updated = retry_in_transaction(con, workflow_id)
        con.commit()
        return updated > 0
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def supersede_replacement(con: sqlite3.Connection, replacement_booking_id: int) -> int:
    """Skip stale effects before changing a replacement booking again.

    The old source-UID CANCEL remains valid and is deliberately preserved. Any
    other effect with an active provider lease makes the lifecycle transition
    wait rather than racing provider I/O and later resurrecting stale replacement
    state. An expired lease is safe to supersede under the caller's writer lock,
    even when the workflow worker is unavailable to reclaim it.
    """
    replacement_booking_id = _validated_booking_id(
        replacement_booking_id,
        "replacement_booking_id",
    )
    now = now_ts()
    running = con.execute(
        """SELECT workflow_id, effect_kind
             FROM booking_workflow_effects
            WHERE replacement_booking_id=?
              AND effect_kind <> 'client_cancel_ics'
              AND status='running'
              AND (lease_expires_at IS NULL OR lease_expires_at > ?)
            LIMIT 1""",
        (replacement_booking_id, now),
    ).fetchone()
    if running is not None:
        raise WorkflowBusy("a booking delivery effect is still running")

    updated = con.execute(
        """UPDATE booking_workflow_effects
              SET status='skipped', next_attempt_at=NULL,
                  lease_token=NULL, lease_expires_at=NULL,
                  provider_ref=NULL, error_class='WorkflowSuperseded',
                  error_code='replacement_superseded',
                  completed_at=?, updated_at=?
            WHERE replacement_booking_id=?
              AND effect_kind <> 'client_cancel_ics'
              AND (
                  status IN ('pending','retry','blocked')
                  OR (status='running' AND lease_expires_at <= ?)
              )""",
        (now, now, replacement_booking_id, now),
    )
    return updated.rowcount


def summary(workflow_id: str) -> dict | None:
    """Return bounded workflow state suitable for an internal/mobile status view."""
    workflow_id = _validated_workflow_id(workflow_id)
    rows = db.all_(
        """SELECT workflow_id, source_booking_id, replacement_booking_id,
                  effect_kind, sequence_no, status, attempts, next_attempt_at,
                  completed_at, provider_ref, error_class, error_code
             FROM booking_workflow_effects
            WHERE workflow_id=? ORDER BY sequence_no""",
        (workflow_id,),
    )
    if not rows:
        return None
    statuses = {row["status"] for row in rows}
    if "blocked" in statuses:
        overall = "blocked"
    elif statuses <= TERMINAL_STATUSES:
        overall = "succeeded"
    elif "running" in statuses:
        overall = "running"
    elif "retry" in statuses:
        overall = "retry"
    else:
        overall = "pending"
    return {
        "workflow_id": rows[0]["workflow_id"],
        "status": overall,
        "source_booking_id": int(rows[0]["source_booking_id"]),
        "replacement_booking_id": int(rows[0]["replacement_booking_id"]),
        "effects": [
            {
                "kind": row["effect_kind"],
                "sequence": int(row["sequence_no"]),
                "status": row["status"],
                "attempts": int(row["attempts"]),
                "next_attempt_at": row["next_attempt_at"],
                "completed_at": row["completed_at"],
                "provider_ref": row["provider_ref"],
                "error_class": row["error_class"],
                "error_code": row["error_code"],
            }
            for row in rows
        ],
    }


__all__ = [
    "EFFECT_KINDS",
    "NotApplicable",
    "WorkflowBusy",
    "available",
    "dispatch_workflow",
    "enqueue_reschedule",
    "retry",
    "retry_in_transaction",
    "summary",
    "supersede_replacement",
    "sweep",
]
