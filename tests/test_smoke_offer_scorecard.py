"""Offers: the acceptance/revenue scorecard (admin, read-only).

DB-backed, same pattern as test_smoke_ai_ops.py. Proves the funnel counts + windows, the
project-level revenue attribution proxy (no double-count across a project's galleries), and
admin-gating.
"""

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs
from app.admin import offer_scorecard
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


def _g(slug, cents, *, decision=None, sent=False, project_id=None, when="+0 days"):
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, project_id) VALUES (?,?,?,?)",
        (slug, slug.upper(), "1", project_id),
    )
    db.run(
        "UPDATE galleries SET plutus_last_status='done', plutus_last_estimated_cents=?, "
        "plutus_last_at=datetime('now', ?), plutus_offer_decision=? WHERE id=?",
        (cents, when, decision, gid),
    )
    if sent:  # sent a day ago, so a payment recorded 'now' counts as after the send
        db.run(
            "UPDATE galleries SET plutus_offer_sent_at=datetime('now','-1 day') WHERE id=?", (gid,)
        )
    return gid


def _seed():
    cid = db.run("INSERT INTO clients (name, email) VALUES (?,?)", ("Dana", "d@e.com"))
    pid = db.run("INSERT INTO projects (client_id, title) VALUES (?,?)", (cid, "Wedding"))
    _g("ga", 30000, decision="approved", sent=True, project_id=pid)  # proposed+approved+sent
    _g("gb", 20000, decision="rejected")  # proposed+rejected
    _g("gc", 10000)  # proposed, undecided
    _g("gd", 5000, when="-200 days")  # proposed long ago (outside 30/60d)
    # revenue on project P, recorded after the offer send
    iid = db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status) VALUES (?,?,?,?,?)",
        (pid, "inv-1", "Album order", 45000, "paid"),
    )
    db.run(
        "INSERT INTO payments (invoice_id, amount_cents, kind) VALUES (?,?,?)", (iid, 45000, "full")
    )
    return pid


def test_funnel_counts_and_windows(admin_client):
    _seed()
    allw = offer_scorecard._funnel(None)
    assert allw["proposed"] == 4 and allw["approved"] == 1 and allw["rejected"] == 1
    assert allw["sent"] == 1
    assert allw["proposed_value"] == "$650.00" and allw["approved_value"] == "$300.00"
    assert allw["approval_rate"] == "25%" and allw["send_rate"] == "100%"
    # the 200-day-old offer drops out of the 30-day window
    assert offer_scorecard._funnel("-30 days")["proposed"] == 3


def test_revenue_proxy_is_project_level(admin_client):
    _seed()
    rev = offer_scorecard._revenue_proxy()
    assert rev["sent_total"] == 1 and rev["projects"] == 1 and rev["converted"] == 1
    assert rev["revenue"] == "$450.00" and rev["conversion_rate"] == "100%"


def test_scorecard_renders(admin_client):
    _seed()
    body = admin_client.get("/admin/offers-scorecard").text
    assert "Offer scorecard" in body
    assert "$650.00" in body  # proposed pipeline, all-time
    assert "$450.00" in body  # attributed revenue
    assert "attribution proxy" in body.lower()


def test_empty_state_renders(admin_client):
    body = admin_client.get("/admin/offers-scorecard").text
    assert "Offer scorecard" in body
    assert "$0.00" in body  # no offers yet


def test_scorecard_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/offers-scorecard", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/login"
        jobs.stop()
