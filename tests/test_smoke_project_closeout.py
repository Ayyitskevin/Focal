"""Project closeout readiness: read-only checklist over the commercial spine.

It reconciles shot list, deliverable spec, license, invoice/open AR, gallery, and workspace on the
project page. It never sends, charges, publishes, or closes anything.
"""

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs
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


def _project(**kw):
    cid = db.run("INSERT INTO clients (name, company) VALUES (?,?)", ("Acme", "Acme Group"))
    vals = {
        "client_id": cid,
        "title": "Spring menu",
        "status": "session_planning",
        "gallery_id": None,
        "workspace_slug": None,
        "workspace_pin": None,
        "workspace_published": 0,
    }
    vals.update(kw)
    pid = db.run(
        """INSERT INTO projects
           (client_id, title, status, gallery_id, workspace_slug, workspace_pin, workspace_published)
           VALUES (:client_id, :title, :status, :gallery_id, :workspace_slug, :workspace_pin,
                   :workspace_published)""",
        vals,
    )
    return cid, pid


def test_closeout_panel_flags_missing_spine(admin_client):
    _, pid = _project()
    html = admin_client.get(f"/admin/studio/projects/{pid}").text
    assert "Closeout readiness" in html
    assert "0/7 checks ready" in html
    assert "No shot list yet" in html
    assert "No deliverable spec" in html
    assert "No project licence" in html
    assert "No invoice" in html
    assert "No linked gallery" in html


def test_closeout_panel_ready_when_spine_is_reconciled(admin_client):
    cid = db.run("INSERT INTO clients (name, company) VALUES (?,?)", ("Ready Che", "Ready Co"))
    gid = db.run(
        "INSERT INTO galleries (slug, title, client_name, pin, published) VALUES (?,?,?,?,1)",
        ("ready-gallery", "Ready gallery", "Ready Che", "1234"),
    )
    pid = db.run(
        """INSERT INTO projects
           (client_id, title, status, gallery_id, workspace_slug, workspace_pin, workspace_published)
           VALUES (?,?,?,?,?,?,1)""",
        (cid, "Ready project", "session_planning", gid, "ready-workspace", "2468"),
    )
    db.run(
        "INSERT INTO shot_list (project_id, title, category, priority) VALUES (?,?,?,?)",
        (pid, "Plated hero", "Hero Dish", "must"),
    )
    db.run(
        """INSERT INTO project_deliverables
           (project_id, label, spec_qty, unit, delivered_qty)
           VALUES (?,?,?,?,?)""",
        (pid, "Hero images", 25, "images", 25),
    )
    invoice_id = db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status) VALUES (?,?,?,?,?)",
        (pid, "ready-invoice", "Ready invoice", 250000, "paid"),
    )
    db.run(
        "INSERT INTO payments (invoice_id, amount_cents, kind) VALUES (?,?,?)",
        (invoice_id, 250000, "full"),
    )
    db.run(
        """INSERT INTO licenses
           (holder_client_id, project_id, invoice_id, title, status, published)
           VALUES (?,?,?,?,?,1)""",
        (cid, pid, invoice_id, "Ready social licence", "active"),
    )

    html = admin_client.get(f"/admin/studio/projects/{pid}").text
    assert "7/7 checks ready" in html
    assert "Ready to close" in html
    assert "25/25 delivered" in html
    assert "No open balance" in html
    assert "Client workspace is live" in html


def test_closeout_panel_surfaces_open_ar(admin_client):
    _, pid = _project()
    invoice_id = db.run(
        "INSERT INTO invoices (project_id, slug, title, total_cents, status) VALUES (?,?,?,?,?)",
        (pid, "open-ar", "Open AR invoice", 10000, "sent"),
    )
    db.run(
        "INSERT INTO payments (invoice_id, amount_cents, kind) VALUES (?,?,?)",
        (invoice_id, 3000, "deposit"),
    )

    html = admin_client.get(f"/admin/studio/projects/{pid}").text
    assert "Latest issued: sent" in html
    assert "70.00 outstanding" in html
