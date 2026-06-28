"""Money operations — the read-only money/AR pane (admin).

DB-backed, same pattern as test_smoke_ai_ops.py / test_smoke_offer_scorecard.py. Proves the
aggregations (approved-but-unsent offers, collected-30d, past-due AR), the rendered tiles, and
admin-gating.
"""

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs
from app.admin import money_ops
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


def _seed():
    cid = db.run("INSERT INTO clients (name) VALUES (?)", ("Dana",))
    pid = db.run("INSERT INTO projects (client_id, title) VALUES (?,?)", (cid, "Spring menu"))
    # an open invoice past its due date -> AR to chase, with a recent partial payment
    iid = db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status, due_date) "
        "VALUES (?,?,?,?,?,?)",
        (pid, "inv-1", "Order", 50000, "sent", "2020-01-01"),
    )
    db.run(
        "INSERT INTO payments (invoice_id, amount_cents, kind) VALUES (?,?,?)",
        (iid, 20000, "deposit"),
    )
    return pid


def test_collected_recent_sums_last_30_days(admin_client):
    _seed()
    c = money_ops._collected_recent()
    assert c["n"] == 1 and c["cents"] == 20000


def test_overdue_counts_open_past_due_invoices(admin_client):
    _seed()
    o = money_ops._overdue()
    assert o["count"] == 1 and o["cents"] == 50000  # full total of the sent, past-due invoice


def test_money_ops_renders(admin_client):
    _seed()
    body = admin_client.get("/admin/money-ops").text
    assert "Money operations" in body
    assert "Invoices past due" in body
    assert "$500.00" in body  # past-due AR
    assert "$200.00" in body  # collected in the last 30 days


def test_empty_state_renders(admin_client):
    body = admin_client.get("/admin/money-ops").text
    assert "Money operations" in body and "$0.00" in body


def test_money_ops_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/money-ops", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        jobs.stop()
