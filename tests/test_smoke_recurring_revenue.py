"""Recurring-revenue forecast (admin, read-only). Unit-tests the pure projection (evergreen vs
pause-at-term vs auto-roll, and month wrapping) for CI, and smoke-tests the studio-wide page:
MRR roll-up, a renewal coming up, and every active plan listed.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs
from app.admin.recurring import _forecast_months, _plan_active_in, forecast
from app.main import app

# --- pure projection (CI unit) ----------------------------------------------


@pytest.mark.unit
def test_forecast_evergreen_contributes_every_month():
    plans = [{"total_cents": 50000, "pause_at_term": 0, "renews_on": None}]
    proj = forecast(plans, ["2026-06", "2026-07", "2026-08"])
    assert [m["cents"] for m in proj] == [50000, 50000, 50000]
    assert all(m["n"] == 1 for m in proj)


@pytest.mark.unit
def test_forecast_pause_at_term_drops_after_renewal_month():
    plans = [{"total_cents": 50000, "pause_at_term": 1, "renews_on": "2026-07-15"}]
    proj = forecast(plans, ["2026-06", "2026-07", "2026-08"])
    assert [m["cents"] for m in proj] == [50000, 50000, 0]  # generates through July, stops in Aug


@pytest.mark.unit
def test_forecast_term_without_pause_auto_rolls():
    plans = [{"total_cents": 50000, "pause_at_term": 0, "renews_on": "2026-07-15"}]
    proj = forecast(plans, ["2026-06", "2026-08"])
    assert [m["cents"] for m in proj] == [50000, 50000]  # continues past the renewal


@pytest.mark.unit
def test_plan_active_in_and_months_wrap():
    assert _plan_active_in({"pause_at_term": 1, "renews_on": "2026-07-15"}, "2026-07")
    assert not _plan_active_in({"pause_at_term": 1, "renews_on": "2026-07-15"}, "2026-08")
    assert _forecast_months(dt.date(2026, 11, 15), 3) == ["2026-11", "2026-12", "2027-01"]


# --- route smoke ------------------------------------------------------------


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


def _plan(client_name, title, cents, *, renews_on=None, pause=0):
    cid = db.run("INSERT INTO clients (name) VALUES (?)", (client_name,))
    pid = db.run("INSERT INTO projects (client_id, title) VALUES (?,?)", (cid, f"{title} proj"))
    return db.run(
        "INSERT INTO recurring_plans (project_id, title, total_cents, active, renews_on, pause_at_term)"
        " VALUES (?,?,?,?,?,?)",
        (pid, title, cents, 1, renews_on, pause),
    )


def test_recurring_revenue_page_rolls_up(admin_client):
    _plan("Acme", "Evergreen content", 500000)
    soon = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    _plan("Borough", "Seasonal", 300000, renews_on=soon, pause=1)
    r = admin_client.get("/admin/studio/recurring-revenue")
    assert r.status_code == 200
    html = r.text
    assert "8,000" in html  # MRR = $5,000 + $3,000
    assert "Evergreen content" in html and "Seasonal" in html
    assert "renews " + soon in html  # renewal surfaced


def test_recurring_revenue_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/studio/recurring-revenue", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        jobs.stop()
