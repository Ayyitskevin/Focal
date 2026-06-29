"""AR aging (money-ops) + per-company statement (admin). Unit-tests the pure aging bucketer for
CI, and smoke-tests the statement page + CSV export: issued invoices and payments for the whole
group, date-range filtering, and the downloadable ledger.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs
from app.admin.money_ops import aging_buckets
from app.main import app

# --- pure aging bucketer (CI unit) ------------------------------------------


@pytest.mark.unit
def test_aging_buckets_partition_by_age():
    today = dt.date(2026, 6, 29)
    rows = [
        {"owed_cents": 1000, "due_date": None},  # no due date -> current
        {"owed_cents": 2000, "due_date": "2026-07-10"},  # future -> current
        {"owed_cents": 3000, "due_date": "2026-06-20"},  # 9 days -> 1-30
        {"owed_cents": 4000, "due_date": "2026-05-20"},  # 40 days -> 31-60
        {"owed_cents": 5000, "due_date": "2026-04-15"},  # 75 days -> 61-90
        {"owed_cents": 6000, "due_date": "2026-01-01"},  # >90 -> 90+
    ]
    b = aging_buckets(rows, today)
    assert b["current"]["cents"] == 3000 and b["current"]["n"] == 2
    assert b["d1_30"]["cents"] == 3000
    assert b["d31_60"]["cents"] == 4000
    assert b["d61_90"]["cents"] == 5000
    assert b["d90"]["cents"] == 6000


# --- statement + money-ops smoke --------------------------------------------


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


def _invoice(project_id, slug, title, cents, status, *, sent_at):
    return db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status, sent_at)"
        " VALUES (?,?,?,?,?,?)",
        (project_id, slug, title, cents, status, sent_at),
    )


def _build_group():
    group = db.run("INSERT INTO clients (name, company) VALUES (?,?)", ("Acme", "Acme Group"))
    venue = db.run(
        "INSERT INTO clients (name, company, parent_id) VALUES (?,?,?)",
        ("Acme DT", "Acme DT LLC", group),
    )
    proj = db.run("INSERT INTO projects (client_id, title) VALUES (?,?)", (venue, "Shoots"))
    inv_jun = _invoice(proj, "inv-jun", "June shoot", 100000, "sent", sent_at="2026-06-01 10:00:00")
    inv_jan = _invoice(proj, "inv-jan", "Jan shoot", 50000, "paid", sent_at="2026-01-01 10:00:00")
    db.run(
        "INSERT INTO payments (invoice_id, amount_cents, kind, created_at) VALUES (?,?,?,?)",
        (inv_jan, 50000, "full", "2026-01-05 12:00:00"),
    )
    return group, inv_jun, inv_jan


def test_statement_all_time(admin_client):
    group, _, _ = _build_group()
    html = admin_client.get(f"/admin/studio/companies/{group}/statement").text
    assert "June shoot" in html and "Jan shoot" in html
    assert "$1,500" in html  # invoiced in range = $150,000
    assert "$500" in html  # received in range = $50,000


def test_statement_date_range_filters(admin_client):
    group, _, _ = _build_group()
    html = admin_client.get(f"/admin/studio/companies/{group}/statement?start=2026-05-01").text
    assert "June shoot" in html  # issued 2026-06-01, in range
    assert "Jan shoot" not in html  # issued 2026-01-01, before range


def test_statement_csv_export(admin_client):
    group, _, _ = _build_group()
    r = admin_client.get(f"/admin/studio/companies/{group}/statement?format=csv")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    body = r.text
    assert body.startswith("issued,invoice,status,total_usd,paid_usd,balance_usd")
    assert "June shoot" in body and "TOTAL" in body


def test_money_ops_shows_aging(admin_client):
    group, _, _ = _build_group()
    # make the June invoice overdue so an aging band is populated
    db.run("UPDATE invoices SET due_date='2026-01-15' WHERE slug='inv-jun'")
    html = admin_client.get("/admin/money-ops").text
    assert "AR aging" in html


def test_statement_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/studio/companies/1/statement", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        jobs.stop()
