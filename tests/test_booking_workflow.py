"""Durable per-effect booking reschedule workflow primitives."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app import booking_notify, booking_workflow, config, db

pytestmark = pytest.mark.unit


class ProviderFailure(RuntimeError):
    code = "provider_unavailable"


@pytest.fixture
def workflow_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "BOOKING_WORKFLOW_ENABLED", True)
    monkeypatch.setattr(config, "BOOKING_WORKFLOW_LEASE_SECONDS", 120)
    monkeypatch.setattr(config, "BOOKING_WORKFLOW_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(config, "BOOKING_WORKFLOW_BATCH_SIZE", 20)
    monkeypatch.setattr(config, "GMAIL_USER", "studio@example.test")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "test-only")
    db.migrate()
    event_id = db.run(
        "INSERT INTO event_types (slug,name) VALUES (?,?)",
        (f"workflow-{uuid4().hex}", "Workflow test"),
    )
    source_id = _booking(event_id, status="cancelled")
    replacement_id = _booking(event_id, reschedule_of=source_id)
    return {
        "event_id": event_id,
        "source_id": source_id,
        "replacement_id": replacement_id,
        "workflow_id": f"wf-{uuid4().hex}",
    }


def _booking(event_id: int, *, status: str = "confirmed", reschedule_of: int | None = None) -> int:
    return db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,start_utc,end_utc,tz,status,reschedule_of)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            f"booking-{uuid4().hex}",
            event_id,
            "Workflow Client",
            "client@example.test",
            "2026-08-01 14:00:00",
            "2026-08-01 15:00:00",
            "UTC",
            status,
            reschedule_of,
        ),
    )


def _enqueue(state: dict, *, workflow_id: str | None = None) -> str:
    workflow_id = workflow_id or state["workflow_id"]
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        booking_workflow.enqueue_reschedule(
            con,
            state["source_id"],
            state["replacement_id"],
            workflow_id,
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return workflow_id


def _effect(kind: str):
    return db.one(
        "SELECT * FROM booking_workflow_effects WHERE effect_kind=?",
        (kind,),
    )


def test_schema_enqueue_is_atomic_and_replay_unique(workflow_db):
    columns = {row["name"] for row in db.all_("PRAGMA table_info(booking_workflow_effects)")}
    assert {
        "workflow_id",
        "source_booking_id",
        "replacement_booking_id",
        "effect_kind",
        "status",
        "attempts",
        "lease_token",
        "lease_expires_at",
    } <= columns
    assert not {"payload", "body", "email", "token", "contact"} & columns
    db.migrate()  # migration replay is a no-op

    con = db.connect()
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        booking_workflow.enqueue_reschedule(
            con,
            workflow_db["source_id"],
            workflow_db["replacement_id"],
            workflow_db["workflow_id"],
        )
        assert con.execute("SELECT COUNT(*) FROM booking_workflow_effects").fetchone()[0] == 6
        con.execute("ROLLBACK")
    finally:
        con.close()
    assert db.one("SELECT COUNT(*) AS n FROM booking_workflow_effects")["n"] == 0

    _enqueue(workflow_db)
    _enqueue(workflow_db)
    rows = db.all_(
        """SELECT effect_kind, sequence_no, status
             FROM booking_workflow_effects ORDER BY sequence_no"""
    )
    assert [(row["effect_kind"], row["sequence_no"]) for row in rows] == list(
        booking_workflow.EFFECT_KINDS
    )
    assert {row["status"] for row in rows} == {"pending"}

    with pytest.raises(ValueError, match="already assigned"):
        _enqueue(workflow_db, workflow_id="different-workflow")
    assert db.one("SELECT COUNT(*) AS n FROM booking_workflow_effects")["n"] == 6


def test_cancel_orders_request_while_independent_effects_continue(
    workflow_db,
    monkeypatch,
):
    clock = [1_000]
    monkeypatch.setattr(booking_workflow, "now_ts", lambda: clock[0])
    _enqueue(workflow_db)
    calls: list[str] = []
    fail_cancel = True

    def run(kind, _source_id, _replacement_id):
        nonlocal fail_cancel
        calls.append(kind)
        if kind == "client_cancel_ics" and fail_cancel:
            fail_cancel = False
            raise ProviderFailure("secret provider response")
        return f"ref-{kind}"

    monkeypatch.setattr(booking_notify, "run_reschedule_effect", run)

    # CANCEL retries; REQUEST remains held, while all four independent provider
    # effects finish and are never replayed.
    assert booking_workflow.dispatch_workflow(workflow_db["workflow_id"]) == 5
    assert calls == [
        "client_cancel_ics",
        "studio_reschedule_notice",
        "notion_booking_patch",
        "notion_session_link",
        "google_calendar_move",
    ]
    state = booking_workflow.summary(workflow_db["workflow_id"])
    assert state["status"] == "retry"
    assert [effect["status"] for effect in state["effects"]] == [
        "retry",
        "pending",
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert state["effects"][0]["next_attempt_at"] == 1_005
    assert state["effects"][0]["error_class"] == "ProviderFailure"
    assert state["effects"][0]["error_code"] == "provider_unavailable"

    clock[0] = 1_005
    assert booking_workflow.dispatch_workflow(workflow_db["workflow_id"]) == 2
    assert calls[-2:] == ["client_cancel_ics", "client_request_ics"]
    assert booking_workflow.dispatch_workflow(workflow_db["workflow_id"]) == 0
    state = booking_workflow.summary(workflow_db["workflow_id"])
    assert state["status"] == "succeeded"
    assert {effect["status"] for effect in state["effects"]} == {"succeeded"}
    assert [effect["attempts"] for effect in state["effects"]] == [2, 1, 1, 1, 1, 1]


def test_skipped_cancel_is_terminal_and_releases_request(workflow_db, monkeypatch):
    monkeypatch.setattr(booking_workflow, "now_ts", lambda: 2_000)
    _enqueue(workflow_db)
    calls: list[str] = []

    def run(kind, _source_id, _replacement_id):
        calls.append(kind)
        if kind == "client_cancel_ics":
            raise booking_workflow.NotApplicable("mail_disabled")
        return None

    monkeypatch.setattr(booking_notify, "run_reschedule_effect", run)

    assert booking_workflow.dispatch_workflow(workflow_db["workflow_id"]) == 6
    state = booking_workflow.summary(workflow_db["workflow_id"])
    assert state["status"] == "succeeded"
    assert state["effects"][0] == {
        "kind": "client_cancel_ics",
        "sequence": 10,
        "status": "skipped",
        "attempts": 1,
        "next_attempt_at": None,
        "completed_at": 2_000,
        "provider_ref": None,
        "error_class": "NotApplicable",
        "error_code": "mail_disabled",
    }
    assert calls[:2] == ["client_cancel_ics", "client_request_ics"]


def test_blocked_cancel_holds_request_and_manual_retry_is_selective(
    workflow_db,
    monkeypatch,
):
    monkeypatch.setattr(config, "BOOKING_WORKFLOW_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(booking_workflow, "now_ts", lambda: 3_000)
    _enqueue(workflow_db)
    fail_cancel = True
    calls: list[str] = []

    def run(kind, _source_id, _replacement_id):
        calls.append(kind)
        if kind == "client_cancel_ics" and fail_cancel:
            raise ProviderFailure("do not persist this response")
        return f"ref-{kind}"

    monkeypatch.setattr(booking_notify, "run_reschedule_effect", run)

    assert booking_workflow.dispatch_workflow(workflow_db["workflow_id"]) == 5
    state = booking_workflow.summary(workflow_db["workflow_id"])
    assert state["status"] == "blocked"
    assert state["effects"][0]["status"] == "blocked"
    assert state["effects"][1]["status"] == "pending"
    assert calls.count("client_request_ics") == 0
    assert booking_workflow.retry(workflow_db["workflow_id"]) is True
    assert booking_workflow.retry(workflow_db["workflow_id"]) is False

    fail_cancel = False
    assert booking_workflow.dispatch_workflow(workflow_db["workflow_id"]) == 2
    assert calls[-2:] == ["client_cancel_ics", "client_request_ics"]
    state = booking_workflow.summary(workflow_db["workflow_id"])
    assert state["status"] == "succeeded"
    assert [effect["attempts"] for effect in state["effects"]] == [1, 1, 1, 1, 1, 1]


def test_claim_is_single_owner_and_stale_lease_cannot_finalize(workflow_db, monkeypatch):
    clock = [4_000]
    monkeypatch.setattr(booking_workflow, "now_ts", lambda: clock[0])
    _enqueue(workflow_db)
    # Leave only CANCEL due so concurrent claimers cannot select independent work.
    db.run(
        """UPDATE booking_workflow_effects SET next_attempt_at=999999
           WHERE effect_kind <> 'client_cancel_ics'"""
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: booking_workflow._claim(), range(2)))
    claimed = [row for row in claims if row is not None]
    assert len(claimed) == 1
    first = claimed[0]
    assert first["effect_kind"] == "client_cancel_ics"

    clock[0] = 4_121
    second = booking_workflow._claim(workflow_id=workflow_db["workflow_id"])
    assert second["id"] == first["id"]
    assert second["lease_token"] != first["lease_token"]
    assert second["attempts"] == 2
    assert booking_workflow._finish(first, status="succeeded") is False
    assert _effect("client_cancel_ics")["lease_token"] == second["lease_token"]
    assert booking_workflow._finish(second, status="succeeded") is True
    assert booking_workflow._claim(workflow_id=workflow_db["workflow_id"]) is None


def test_expired_lease_at_attempt_limit_becomes_bounded_blocked_evidence(
    workflow_db,
    monkeypatch,
):
    monkeypatch.setattr(booking_workflow, "now_ts", lambda: 4_500)
    _enqueue(workflow_db)
    db.run(
        """UPDATE booking_workflow_effects
              SET status='running', attempts=3, next_attempt_at=NULL,
                  lease_token='0123456789abcdef0123456789abcdef',
                  lease_expires_at=4499, provider_ref='stale-provider-ref'
            WHERE effect_kind='client_cancel_ics'"""
    )
    db.run(
        """UPDATE booking_workflow_effects SET next_attempt_at=999999
           WHERE effect_kind <> 'client_cancel_ics'"""
    )

    assert booking_workflow._claim(workflow_id=workflow_db["workflow_id"]) is None
    exhausted = _effect("client_cancel_ics")
    assert exhausted["status"] == "blocked"
    assert exhausted["attempts"] == 3
    assert exhausted["lease_token"] is None
    assert exhausted["lease_expires_at"] is None
    assert exhausted["provider_ref"] is None
    assert exhausted["completed_at"] == 4_500
    assert exhausted["error_class"] == "LeaseExpired"
    assert exhausted["error_code"] == "lease_expired"


def test_summary_prioritizes_blocked_over_retry(workflow_db, monkeypatch):
    monkeypatch.setattr(booking_workflow, "now_ts", lambda: 4_600)
    _enqueue(workflow_db)
    db.run(
        """UPDATE booking_workflow_effects
              SET status='blocked', next_attempt_at=NULL, completed_at=4600,
                  error_class='ProviderFailure', error_code='provider_unavailable'
            WHERE effect_kind='client_cancel_ics'"""
    )
    db.run(
        """UPDATE booking_workflow_effects
              SET status='retry', next_attempt_at=4605
            WHERE effect_kind='studio_reschedule_notice'"""
    )

    state = booking_workflow.summary(workflow_db["workflow_id"])
    assert state["status"] == "blocked"
    assert {effect["status"] for effect in state["effects"]} >= {"blocked", "retry"}


def test_sweep_limit_feature_gate_and_bounded_failure_evidence(workflow_db, monkeypatch):
    monkeypatch.setattr(booking_workflow, "now_ts", lambda: 5_000)
    _enqueue(workflow_db)
    calls: list[str] = []

    def run(kind, _source_id, _replacement_id):
        calls.append(kind)
        return f"provider-{kind}"

    monkeypatch.setattr(booking_notify, "run_reschedule_effect", run)
    monkeypatch.setattr(config, "BOOKING_WORKFLOW_ENABLED", False)
    assert booking_workflow.sweep(2) == 0
    assert calls == []

    monkeypatch.setattr(config, "BOOKING_WORKFLOW_ENABLED", True)
    assert booking_workflow.sweep(2) == 2
    assert calls == ["client_cancel_ics", "client_request_ics"]
    assert booking_workflow.summary("missing-workflow") is None

    # Exception messages and unsafe codes can contain provider data. Only the
    # bounded class and a generic code enter SQLite.
    class UnsafeFailure(RuntimeError):
        code = "client@example.test bearer secret"

    def fail(kind, _source_id, _replacement_id):
        if kind == "studio_reschedule_notice":
            raise UnsafeFailure("contact client@example.test with bearer secret")
        return f"provider-{kind}"

    monkeypatch.setattr(config, "BOOKING_WORKFLOW_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(booking_notify, "run_reschedule_effect", fail)
    assert booking_workflow.sweep(1) == 1
    studio = _effect("studio_reschedule_notice")
    assert studio["status"] == "blocked"
    assert studio["error_class"] == "UnsafeFailure"
    assert studio["error_code"] == "exception"
    stored = "|".join(
        str(studio[key] or "") for key in ("error_class", "error_code", "provider_ref")
    )
    assert "client@example.test" not in stored
    assert "bearer secret" not in stored
