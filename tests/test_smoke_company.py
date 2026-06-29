"""Per-company command view (admin) — read-only group roll-up. Builds a group (parent + venue)
with a project, an overdue invoice, an active retainer (over on one line, behind on another), and
an active licence, then asserts the company page aggregates the whole group: MRR, outstanding +
overdue AR, retainer utilisation (behind + advisory overage), the licence, the venue, the project.
"""

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs, mailer
from app.admin import common, recurring, studio
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


def _client(name, *, parent_id=None, company=None, email=None, billing_email=None):
    return db.run(
        "INSERT INTO clients (name, company, parent_id, email, billing_email) VALUES (?,?,?,?,?)",
        (name, company, parent_id, email, billing_email),
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
    assert "Next actions" in html and "Chase past-due invoice" in html
    assert f"/admin/studio/companies/{group}/ar-chase" in html


def test_company_view_single_client_is_a_group_of_one(admin_client):
    solo = _client("Solo Bistro", company="Solo Bistro Inc")
    r = admin_client.get(f"/admin/studio/companies/{solo}")
    assert r.status_code == 200
    assert "Solo Bistro" in r.text
    assert "No active retainers" in r.text and "No open projects" in r.text
    assert "No urgent next actions" in r.text


def test_repeat_client_cadence_flags_due_company_and_client_list(admin_client, monkeypatch):
    monkeypatch.setattr(studio, "_today", lambda: dt.date(2026, 6, 29))
    monkeypatch.setattr(common, "today", lambda: dt.date(2026, 6, 29))
    group = _client("Cadence Group", company="Cadence Hospitality")
    venue = _client("Cadence Downtown", parent_id=group)
    for shoot_date in ("2026-01-01", "2026-03-01", "2026-04-29"):
        _project(venue, f"Menu shoot {shoot_date}", status="project_closed", shoot_date=shoot_date)

    html = admin_client.get(f"/admin/studio/companies/{group}").text
    assert "Repeat cadence" in html
    assert "59d" in html  # median interval: Jan→Mar and Mar→Apr are both 59 days
    assert "due for a shoot (2d overdue)" in html

    clients_html = admin_client.get("/admin/studio/clients").text
    assert "Cadence" in clients_html
    assert "due for a shoot (2d overdue)" in clients_html


def test_repeat_client_cadence_suppressed_when_future_shoot_exists(admin_client, monkeypatch):
    monkeypatch.setattr(studio, "_today", lambda: dt.date(2026, 6, 29))
    monkeypatch.setattr(common, "today", lambda: dt.date(2026, 6, 29))
    group = _client("Booked Group", company="Booked Hospitality")
    for shoot_date in ("2026-01-01", "2026-03-01", "2026-04-29"):
        _project(group, f"Past shoot {shoot_date}", status="project_closed", shoot_date=shoot_date)
    _project(group, "Summer menu", status="session_planning", shoot_date="2026-07-10")

    html = admin_client.get(f"/admin/studio/companies/{group}").text
    assert "scheduled 2026-07-10" in html
    assert "due for a shoot" not in html


def test_company_next_actions_rank_money_project_and_cadence(admin_client, monkeypatch):
    monkeypatch.setattr(studio, "_today", lambda: dt.date(2026, 6, 29))
    monkeypatch.setattr(common, "today", lambda: dt.date(2026, 6, 29))
    group = _client("Action Group", company="Action Hospitality")
    overdue_project = _project(group, "Past launch", status="project_closed")
    active_project = _project(group, "Fall menu", status="session_planning")
    for shoot_date in ("2026-01-01", "2026-03-01", "2026-04-29"):
        _project(
            group, f"Closed shoot {shoot_date}", status="project_closed", shoot_date=shoot_date
        )

    db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status, due_date)"
        " VALUES (?,?,?,?,?,?)",
        (overdue_project, "past-due", "Past due balance", 90000, "sent", "2026-05-01"),
    )
    db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status) VALUES (?,?,?,?,?)",
        (active_project, "draft-fall", "Fall draft", 180000, "draft"),
    )

    html = admin_client.get(f"/admin/studio/companies/{group}").text
    assert "Next actions" in html
    assert "Chase past-due invoice" in html
    assert "Send draft invoice" in html
    assert "Record usage licence" in html
    assert "Schedule repeat shoot" in html
    positions = [
        html.index("Chase past-due invoice"),
        html.index("Send draft invoice"),
        html.index("Record usage licence"),
        html.index("Schedule repeat shoot"),
    ]
    assert positions == sorted(positions)


def test_studio_activity_surfaces_top_commercial_actions(admin_client):
    group = _client("Activity Group", company="Activity Hospitality")
    project = _project(group, "Launch closeout", status="project_closed")
    db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status, due_date)"
        " VALUES (?,?,?,?,?,?)",
        (project, "activity-past-due", "Activity invoice", 140000, "sent", "2000-01-01"),
    )

    html = admin_client.get("/admin/studio/activity").text
    assert "Commercial actions" in html
    assert "Chase past-due invoice" in html
    assert "Activity Hospitality" in html
    assert "Activity invoice" in html
    assert "/admin/studio/companies/" in html and "/ar-chase?invoice_id=" in html


def test_company_ar_chase_compose_and_manual_send(admin_client, monkeypatch):
    group = _client(
        "Activity Group",
        company="Activity Hospitality",
        email="owner@activity.test",
        billing_email="ap@activity.test",
    )
    project = _project(group, "Launch closeout", status="project_closed")
    invoice_id = db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status, due_date)"
        " VALUES (?,?,?,?,?,?)",
        (project, "activity-past-due", "Activity invoice", 140000, "sent", "2000-01-01"),
    )
    db.run(
        "INSERT INTO payments (invoice_id, amount_cents, kind) VALUES (?,?,?)",
        (invoice_id, 40000, "deposit"),
    )

    html = admin_client.get(
        f"/admin/studio/companies/{group}/ar-chase?invoice_id={invoice_id}"
    ).text
    assert "Open past-due invoices" in html
    assert "Activity invoice" in html
    assert "ap@activity.test" in html
    assert "/admin/studio/companies/" in html and "/statement" in html
    assert "/i/activity-past-due" in html
    assert "1,000.00" in html

    invoice_html = admin_client.get(f"/admin/studio/invoices/{invoice_id}").text
    assert "Draft AR chase email" in invoice_html
    assert f"/admin/studio/companies/{group}/ar-chase?invoice_id={invoice_id}" in invoice_html

    sent = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: sent.append((to, subject, body)))
    r = admin_client.post(
        f"/admin/studio/companies/{group}/ar-chase/email",
        data={
            "invoice_id": str(invoice_id),
            "to": "ap@activity.test",
            "subject": "Follow-up on open invoice balance - Activity Hospitality",
            "message": "Please see the open invoice.",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/studio/companies/{group}"
    assert sent == [
        (
            "ap@activity.test",
            "Follow-up on open invoice balance - Activity Hospitality",
            "Please see the open invoice.",
        )
    ]
    row = db.one("SELECT * FROM emails_log WHERE doc_kind='other' AND doc_id=?", (group,))
    assert row["project_id"] == project
    assert row["to_email"] == "ap@activity.test"
    assert db.one("SELECT status FROM invoices WHERE id=?", (invoice_id,))["status"] == "sent"


def test_company_ar_chase_skips_settled_overdue_invoice(admin_client):
    group = _client("Settled Group", company="Settled Hospitality")
    project = _project(group, "Paid launch", status="project_closed")
    invoice_id = db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status, due_date)"
        " VALUES (?,?,?,?,?,?)",
        (project, "settled-past-due", "Settled invoice", 80000, "sent", "2000-01-01"),
    )
    db.run(
        "INSERT INTO payments (invoice_id, amount_cents, kind) VALUES (?,?,?)",
        (invoice_id, 80000, "full"),
    )

    company_html = admin_client.get(f"/admin/studio/companies/{group}").text
    assert "Past-due invoices" not in company_html
    assert "Draft chase email" not in company_html

    html = admin_client.get(f"/admin/studio/companies/{group}/ar-chase").text
    assert "No overdue open invoices" in html
    assert "Send email" not in html


def test_company_view_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/studio/companies/1", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        jobs.stop()
