"""Per-company command view (admin) — read-only group roll-up. Builds a group (parent + venue)
with a project, an overdue invoice, an active retainer (over on one line, behind on another), and
an active licence, then asserts the company page aggregates the whole group: MRR, outstanding +
overdue AR, retainer utilisation (behind + advisory overage), the licence, the venue, the project.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs
from app.admin import recurring
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


def _client(name, *, parent_id=None, company=None):
    return db.run(
        "INSERT INTO clients (name, company, parent_id) VALUES (?,?,?)", (name, company, parent_id)
    )


def _project(client_id, title, *, status="session_planning", shoot_date=None):
    return db.run(
        "INSERT INTO projects (client_id, title, status, shoot_date) VALUES (?,?,?,?)",
        (client_id, title, status, shoot_date),
    )


def test_company_view_rolls_up_the_group(admin_client):
    group = _client("Acme Group", company="Acme Hospitality")
    venue = _client("Acme Downtown", company="Acme DT LLC", parent_id=group)
    proj_v = _project(venue, "Launch shoot", shoot_date="2000-02-02")
    proj_g = _project(group, "Monthly content project")

    # overdue, issued invoice under the venue's project
    db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status, due_date)"
        " VALUES (?,?,?,?,?,?)",
        (proj_v, "inv-1", "Launch invoice", 120000, "sent", "2000-01-01"),
    )

    # active retainer: over on Hero images (advisory overage), behind on Reels
    quota = json.dumps(
        [
            {"label": "Hero images", "target": 20, "unit": "images", "overage_rate_cents": 5000},
            {"label": "Reels", "target": 4, "unit": "reels", "overage_rate_cents": 0},
        ]
    )
    plan = db.run(
        "INSERT INTO recurring_plans (project_id, title, total_cents, active, quota)"
        " VALUES (?,?,?,?,?)",
        (proj_g, "Monthly content", 500000, 1, quota),
    )
    period = recurring._period()
    db.run(
        "INSERT INTO retainer_deliveries (plan_id, period, label, qty) VALUES (?,?,?,?)",
        (plan, period, "Hero images", 25),
    )

    # active licence held by the venue
    db.run(
        "INSERT INTO licenses (holder_client_id, title, usage_tier, exclusivity, status, published)"
        " VALUES (?,?,?,?,?,?)",
        (venue, "Q1 Social Pack", "standard", "non_exclusive", "active", 1),
    )

    r = admin_client.get(f"/admin/studio/companies/{group}")
    assert r.status_code == 200
    html = r.text
    assert "Acme Group" in html and "Acme Downtown" in html  # group + venue
    assert "Monthly content" in html  # retainer
    assert "Q1 Social Pack" in html  # licence
    assert "Launch invoice" in html  # overdue invoice surfaced
    assert "Launch shoot" in html  # open project
    assert "5,000" in html  # MRR $5,000 (usd0)
    assert "250.00" in html  # advisory overage 5 × $50 (usd)
    assert "1,200" in html  # outstanding / overdue $1,200
    assert "behind" in html.lower()  # Reels behind pace


def test_company_view_single_client_is_a_group_of_one(admin_client):
    solo = _client("Solo Bistro", company="Solo Bistro Inc")
    r = admin_client.get(f"/admin/studio/companies/{solo}")
    assert r.status_code == 200
    assert "Solo Bistro" in r.text
    assert "No active retainers" in r.text and "No open projects" in r.text


def test_company_view_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/studio/companies/1", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        jobs.stop()
