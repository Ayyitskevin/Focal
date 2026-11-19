"""Native inquiry email reply delivery and replay contracts."""

import datetime as dt
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient

from app import config, db, mailer, mobile_auth, ratelimit
from app.main import app

pytestmark = pytest.mark.unit


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "reply-api-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "TIMEZONE", "UTC")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "GMAIL_USER", "studio@example.test")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "fake-test-password")
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    login = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": None,
            "password": "owner-password",
            "device": {
                "installation_id": "A8A06DC2-2034-4E3B-B07D-0CBFD2455B98",
                "name": "Owner iPhone",
                "platform": "ios",
                "app_version": "1.0",
            },
        },
    )
    token = login.json()["access_token"]
    yield client, {"Authorization": f"Bearer {token}"}
    client.close()
    ratelimit._hits.clear()


def _inquiry(email="lead@example.test"):
    return db.run(
        "INSERT INTO inquiries (name,email,message,kind) VALUES (?,?,?,'contact')",
        ("Test Lead", email, "Can you photograph our event?"),
    )


def _post(client, headers, inquiry_id, key, body="Thanks — I can help."):
    return client.post(
        f"/api/v1/inquiries/{inquiry_id}/reply",
        headers={**headers, "Idempotency-Key": str(key)},
        json={"channel": "email", "subject": "Re: your inquiry", "body": body},
    )


def test_reply_success_and_exact_replay_sends_once(owner, monkeypatch):
    client, headers = owner
    inquiry_id = _inquiry()
    key = uuid.uuid4()
    calls = []
    monkeypatch.setattr(mailer, "send", lambda *args, **kwargs: calls.append((args, kwargs)))

    first = _post(client, headers, inquiry_id, key)
    repeated = _post(client, headers, inquiry_id, key)

    assert first.status_code == 201
    assert first.json()["code"] == "inquiry.reply_sent"
    assert first.json()["delivery_status"] == "sent"
    assert first.json()["message_id"] is not None
    assert first.json()["detail"]["summary"]["is_replied"] is True
    assert repeated.status_code == 201
    assert repeated.json()["message_id"] == first.json()["message_id"]
    assert len(calls) == 1
    message = db.one("SELECT * FROM messages WHERE inquiry_id=?", (inquiry_id,))
    assert message["direction"] == "out"
    assert message["channel"] == "email"
    assert message["body"] == "Thanks — I can help."
    assert message["provider_msg_id"] == calls[0][1]["message_id"]
    assert message["provider_msg_id"].startswith("<inquiry-reply-")
    assert db.one("SELECT COUNT(*) AS n FROM emails_log WHERE doc_id=?", (inquiry_id,))["n"] == 1
    assert db.one("SELECT emailed FROM inquiries WHERE id=?", (inquiry_id,))["emailed"] == 1
    audit = db.one("SELECT action, actor FROM audit_log WHERE entity_type='inquiry'")
    assert (audit["action"], audit["actor"]) == ("reply", "owner")
    receipt = db.one("SELECT * FROM api_idempotency_replays")
    assert receipt["response_status"] == 201
    assert len(receipt["key_hash"]) == 64
    receipt_text = receipt["response_json"] + receipt["key_hash"] + receipt["request_hash"]
    assert str(key) not in receipt_text
    assert "lead@example.test" not in receipt_text
    assert "Thanks — I can help." not in receipt_text
    assert "Re: your inquiry" not in receipt_text

    conflict = _post(client, headers, inquiry_id, key, body="Different content")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency.key_conflict"
    assert len(calls) == 1


def test_reply_provider_exception_is_unknown_and_never_auto_retries(owner, monkeypatch):
    client, headers = owner
    inquiry_id = _inquiry()
    key = uuid.uuid4()
    calls = []

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("simulated ambiguous SMTP failure")

    monkeypatch.setattr(mailer, "send", fail)

    first = _post(client, headers, inquiry_id, key)
    repeated = _post(client, headers, inquiry_id, key)

    assert first.status_code == 202
    assert first.json()["code"] == "inquiry.delivery_unknown"
    assert first.json()["delivery_status"] == "unknown"
    assert first.json()["message_id"] is None
    assert first.json()["detail"]["summary"]["is_replied"] is False
    assert repeated.status_code == 202
    assert len(calls) == 1
    assert db.one("SELECT COUNT(*) AS n FROM messages")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM emails_log")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='inquiry'")["n"] == 0
    receipt = db.one("SELECT response_status, response_json FROM api_idempotency_replays")
    assert receipt["response_status"] == 202
    assert json.loads(receipt["response_json"])["delivery_status"] == "unknown"


def test_reply_commit_failure_after_smtp_stays_unknown(owner, monkeypatch):
    client, headers = owner
    inquiry_id = _inquiry()
    key = uuid.uuid4()
    calls = []
    monkeypatch.setattr(mailer, "send", lambda *args, **kwargs: calls.append((args, kwargs)))
    original_connect = db.connect

    class FailingConnection:
        commit_calls = 0

        def __init__(self, connection):
            self._connection = connection
            self.isolation_level = connection.isolation_level

        def execute(self, sql, parameters=()):
            return self._connection.execute(sql, parameters)

        def commit(self):
            type(self).commit_calls += 1
            if type(self).commit_calls == 2:
                raise RuntimeError("simulated post-SMTP commit failure")
            return self._connection.commit()

        def __getattr__(self, name):
            return getattr(self._connection, name)

    monkeypatch.setattr(
        db,
        "connect",
        lambda *args, **kwargs: FailingConnection(original_connect(*args, **kwargs)),
    )

    first = _post(client, headers, inquiry_id, key)
    repeated = _post(client, headers, inquiry_id, key)

    assert first.status_code == 202
    assert first.json()["code"] == "inquiry.delivery_unknown"
    assert repeated.status_code == 202
    assert len(calls) == 1
    assert db.one("SELECT COUNT(*) AS n FROM messages")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM emails_log")["n"] == 0
    assert db.one("SELECT emailed FROM inquiries WHERE id=?", (inquiry_id,))["emailed"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='inquiry'")["n"] == 0
    assert db.one("SELECT response_status FROM api_idempotency_replays")["response_status"] == 202


def test_reply_connection_failure_after_smtp_stays_unknown(owner, monkeypatch):
    client, headers = owner
    inquiry_id = _inquiry()
    key = uuid.uuid4()
    calls = []
    original_connect = db.connect

    def accept(*args, **kwargs):
        calls.append((args, kwargs))

        def fail_once(*args, **kwargs):
            monkeypatch.setattr(db, "connect", original_connect)
            raise sqlite3.OperationalError("database temporarily unavailable")

        monkeypatch.setattr(db, "connect", fail_once)

    monkeypatch.setattr(mailer, "send", accept)
    for _ in range(2):
        response = _post(client, headers, inquiry_id, key)
        assert response.status_code == 202
        assert response.json()["code"] == "inquiry.delivery_unknown"
    assert len(calls) == 1
    assert db.one("SELECT COUNT(*) AS n FROM messages")["n"] == 0


def test_reply_replay_after_inquiry_deletion_returns_not_found(owner, monkeypatch):
    client, headers = owner
    inquiry_id = _inquiry()
    key = uuid.uuid4()
    calls = []

    def ambiguous(*args, **kwargs):
        calls.append((args, kwargs))
        raise TimeoutError("simulated provider timeout")

    monkeypatch.setattr(mailer, "send", ambiguous)
    assert _post(client, headers, inquiry_id, key).status_code == 202
    db.run("DELETE FROM inquiries WHERE id=?", (inquiry_id,))
    replay = _post(client, headers, inquiry_id, key)
    assert replay.status_code == 404
    assert replay.json()["code"] == "inquiry.not_found"
    assert len(calls) == 1


def test_reply_validation_and_authority(owner, monkeypatch):
    client, headers = owner
    inquiry_id = _inquiry(email="")
    key = uuid.uuid4()
    calls = []
    monkeypatch.setattr(mailer, "send", lambda *args, **kwargs: calls.append((args, kwargs)))

    missing_recipient = _post(client, headers, inquiry_id, key)
    assert missing_recipient.status_code == 422
    assert missing_recipient.json()["code"] == "inquiry.recipient_missing"

    configured_inquiry = _inquiry()
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "")
    unavailable = _post(client, headers, configured_inquiry, key)
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "inquiry.delivery_unavailable"

    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "fake-test-password")
    read_only = mobile_auth.Principal(
        session_id="read-only",
        tenant_key="self:https://studio.test",
        kind=mobile_auth.STUDIO_OWNER,
        resource_id=None,
        resource_variant=None,
        gallery_visitor_id=None,
        scopes=frozenset({"studio:read"}),
        device_name=None,
        device_platform=None,
        device_app_version=None,
        created_at=dt.datetime.now(dt.UTC),
        absolute_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *args, **kwargs: read_only)
    refused = _post(client, headers, configured_inquiry, key)
    assert refused.status_code == 403
    assert calls == []


def test_reply_guest_and_invalid_input_matrix(owner, monkeypatch):
    client, headers = owner
    inquiry_id = _inquiry()
    key = uuid.uuid4()
    calls = []
    monkeypatch.setattr(mailer, "send", lambda *args, **kwargs: calls.append((args, kwargs)))
    original_auth = mobile_auth.authenticate_request
    guest = mobile_auth.Principal(
        session_id="guest",
        tenant_key="self:https://studio.test",
        kind=mobile_auth.GALLERY_GUEST,
        resource_id=1,
        resource_variant=None,
        gallery_visitor_id=None,
        scopes=frozenset({"studio:write"}),
        device_name=None,
        device_platform=None,
        device_app_version=None,
        created_at=dt.datetime.now(dt.UTC),
        absolute_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *args, **kwargs: guest)
    refused = _post(client, headers, inquiry_id, key)
    assert refused.status_code == 403
    assert refused.json()["code"] == "auth.insufficient_scope"
    assert calls == []

    monkeypatch.setattr(mobile_auth, "authenticate_request", original_auth)
    path = f"/api/v1/inquiries/{inquiry_id}/reply"
    invalid_payloads = [
        (
            {**headers, "Idempotency-Key": "not-a-uuid"},
            {"channel": "email", "subject": "s", "body": "b"},
        ),
        ({**headers, "Idempotency-Key": str(key)}, {"channel": "sms", "subject": "s", "body": "b"}),
        (
            {**headers, "Idempotency-Key": str(key)},
            {"channel": "email", "subject": " ", "body": "b"},
        ),
        (
            {**headers, "Idempotency-Key": str(key)},
            {"channel": "email", "subject": "s", "body": " "},
        ),
        (
            {**headers, "Idempotency-Key": str(key)},
            {"channel": "email", "subject": "s" * 201, "body": "b"},
        ),
        (
            {**headers, "Idempotency-Key": str(key)},
            {"channel": "email", "subject": "s", "body": "b" * 100_001},
        ),
        (
            {**headers, "Idempotency-Key": str(key)},
            {"channel": "email", "subject": "s", "body": "b", "unexpected": True},
        ),
    ]
    for request_headers, payload in invalid_payloads:
        response = client.post(path, headers=request_headers, json=payload)
        assert response.status_code == 422
        assert response.json()["code"] == "request.validation_failed"
    assert calls == []


def test_concurrent_identical_replies_send_at_most_once(owner, monkeypatch):
    _, headers = owner
    inquiry_id = _inquiry()
    key = uuid.uuid4()
    barrier = Barrier(2)
    send_started = Event()
    finish_send = Event()
    calls = []

    def send(*args, **kwargs):
        calls.append((args, kwargs))
        send_started.set()
        assert finish_send.wait(timeout=5)

    monkeypatch.setattr(mailer, "send", send)

    def reply():
        with TestClient(app, base_url="https://studio.test") as threaded_client:
            barrier.wait()
            return _post(threaded_client, headers, inquiry_id, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(reply)
        second_future = pool.submit(reply)
        assert send_started.wait(timeout=2)
        finish_send.set()
        responses = [first_future.result(), second_future.result()]

    assert {response.status_code for response in responses} in ({201}, {201, 202})
    assert any(response.status_code == 201 for response in responses)
    assert len(calls) == 1
    assert db.one("SELECT COUNT(*) AS n FROM messages")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM emails_log")["n"] == 1
