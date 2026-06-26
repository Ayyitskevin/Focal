"""Offers: sending an approved offer to the client (admin, money-path-adjacent).

DB-backed (real tmp DB + admin routes), same pattern as test_smoke_ai_ops.py. Proves the
compose page pre-fills the editable draft, the approval guard holds on both GET and POST,
and a send goes through the Gmail path + records emails_log / sent-state / audit — while
never charging or invoicing.
"""

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs, mailer
from app.main import app


def _configure_tmp_db(tmp_path, monkeypatch):
    for attr, val in {
        "DATA_DIR": tmp_path,
        "DB_PATH": tmp_path / "mise.db",
        "MEDIA_DIR": tmp_path / "media",
        "ZIP_DIR": tmp_path / "zips",
        "TMP_DIR": tmp_path / "tmp",
        "BRAND_DIR": tmp_path / "brand",
        "RECEIPTS_DIR": tmp_path / "receipts",
        "SECRET_KEY": "test-secret",
        "ADMIN_PASSWORD": "test-pw",
    }.items():
        monkeypatch.setattr(config, attr, val)
    db.migrate()


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.post("/admin/login", data={"password": "test-pw"}, follow_redirects=False)
        assert r.status_code == 303
        yield client
    jobs.stop()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app import ratelimit

    ratelimit._hits.clear()
    yield


def _offer(*, decision="approved", email="dana@example.com", url="https://plutus.example/o/1"):
    """A client + gallery carrying a ready Plutus offer in the given decision state."""
    cid = db.run("INSERT INTO clients (name, email) VALUES (?,?)", ("Dana Reyes", email))
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, client_id) VALUES (?,?,?,?)",
        ("OfferG", "Spring Wedding", "1", cid),
    )
    db.run(
        "UPDATE galleries SET plutus_last_status='done', plutus_last_offer_url=?, "
        "plutus_last_estimated_cents=30000, plutus_last_at=datetime('now'), "
        "plutus_offer_decision=? WHERE id=?",
        (url, decision, gid),
    )
    return gid


def test_compose_prefills_draft_for_approved_offer(admin_client):
    gid = _offer()
    body = admin_client.get(f"/admin/offers/{gid}/send").text
    assert "Send offer to client" in body
    assert "https://plutus.example/o/1" in body  # the approved offer link
    assert "Hi Dana" in body  # first name from the client record
    assert "dana@example.com" in body  # pre-filled recipient


def test_compose_refused_until_approved(admin_client):
    gid = _offer(decision=None)
    r = admin_client.get(f"/admin/offers/{gid}/send", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]


def test_send_emails_records_and_marks_sent(admin_client, monkeypatch):
    gid = _offer()
    sent = {}
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(
        mailer,
        "send",
        lambda to, subject, body, *a, **k: sent.update(to=to, subject=subject, body=body),
    )
    r = admin_client.post(
        f"/admin/offers/{gid}/send",
        data={
            "to": "dana@example.com",
            "subject": "Your options",
            "message": "Hi Dana, here you go.",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert sent == {
        "to": "dana@example.com",
        "subject": "Your options",
        "body": "Hi Dana, here you go.",
    }
    # emails_log row written for the offer (doc_kind 'other' — the constrained escape hatch)
    e = db.one("SELECT doc_kind, doc_id, to_email FROM emails_log WHERE doc_id=?", (gid,))
    assert e["doc_kind"] == "other" and e["to_email"] == "dana@example.com"
    # gallery marked sent; money state untouched (decision still approved, no invoice created)
    g = db.one(
        "SELECT plutus_offer_sent_at, plutus_offer_sent_to FROM galleries WHERE id=?", (gid,)
    )
    assert g["plutus_offer_sent_at"] is not None and g["plutus_offer_sent_to"] == "dana@example.com"
    assert db.one("SELECT COUNT(*) AS n FROM invoices")["n"] == 0
    # audit trail records the send
    a = db.one("SELECT action FROM audit_log WHERE entity_type='gallery' AND entity_id=?", (gid,))
    assert a["action"] == "offer_emailed"


def test_send_refused_when_not_approved(admin_client, monkeypatch):
    gid = _offer(decision=None)
    called = {"n": 0}
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda *a, **k: called.update(n=called["n"] + 1))
    r = admin_client.post(
        f"/admin/offers/{gid}/send",
        data={"to": "dana@example.com", "subject": "s", "message": "m"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert called["n"] == 0  # nothing sent
    assert (
        db.one("SELECT plutus_offer_sent_at FROM galleries WHERE id=?", (gid,))[
            "plutus_offer_sent_at"
        ]
        is None
    )


def test_send_refused_when_mailer_unconfigured(admin_client, monkeypatch):
    gid = _offer()
    monkeypatch.setattr(mailer, "configured", lambda: False)
    r = admin_client.post(
        f"/admin/offers/{gid}/send",
        data={"to": "dana@example.com", "subject": "s", "message": "m"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert (
        db.one("SELECT plutus_offer_sent_at FROM galleries WHERE id=?", (gid,))[
            "plutus_offer_sent_at"
        ]
        is None
    )


def test_offer_send_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/offers/1/send", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/login"
        jobs.stop()
