"""Project deliverable specs (admin) — the contracted "what we owe" per shoot. Create/update/delete
(audited, soft-delete), validation (label + unit), the project-page panel, and the delivered/spec
roll-up on the company view's active-project row.
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


def _project(*, status="session_planning"):
    cid = db.run("INSERT INTO clients (name, company) VALUES (?,?)", ("Acme", "Acme Group"))
    pid = db.run(
        "INSERT INTO projects (client_id, title, status) VALUES (?,?,?)", (cid, "Launch", status)
    )
    return cid, pid


def _add(client, pid, **kw):
    data = {"label": "Hero images", "spec_qty": "25", "unit": "images", **kw}
    return client.post(
        f"/admin/studio/projects/{pid}/deliverables", data=data, follow_redirects=False
    )


def test_create_deliverable(admin_client):
    _, pid = _project()
    r = _add(admin_client, pid, spec_format="JPEG sRGB")
    assert r.status_code == 303
    d = db.one("SELECT * FROM project_deliverables WHERE project_id=?", (pid,))
    assert d["label"] == "Hero images" and d["spec_qty"] == 25 and d["unit"] == "images"
    assert d["spec_format"] == "JPEG sRGB" and d["delivered_qty"] == 0
    assert db.one(
        "SELECT 1 AS x FROM audit_log WHERE entity_type='project_deliverable' AND action='create'"
    )


def test_create_rejects_bad_unit_and_blank_label(admin_client):
    _, pid = _project()
    assert _add(admin_client, pid, unit="bogus").status_code == 400
    assert _add(admin_client, pid, label="   ").status_code == 400
    assert db.one("SELECT COUNT(*) AS n FROM project_deliverables")["n"] == 0


def test_update_delivered_count_audits(admin_client):
    _, pid = _project()
    _add(admin_client, pid)
    did = db.one("SELECT id FROM project_deliverables WHERE project_id=?", (pid,))["id"]
    r = admin_client.post(
        f"/admin/studio/deliverables/{did}",
        data={"label": "Hero images", "spec_qty": "25", "unit": "images", "delivered_qty": "20"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        db.one("SELECT delivered_qty FROM project_deliverables WHERE id=?", (did,))["delivered_qty"]
        == 20
    )
    assert db.one(
        "SELECT 1 AS x FROM audit_log WHERE entity_type='project_deliverable' AND entity_id=? "
        "AND action='update'",
        (did,),
    )


def test_delete_is_soft(admin_client):
    _, pid = _project()
    _add(admin_client, pid)
    did = db.one("SELECT id FROM project_deliverables WHERE project_id=?", (pid,))["id"]
    admin_client.post(f"/admin/studio/deliverables/{did}/delete")
    row = db.one("SELECT deleted_at FROM project_deliverables WHERE id=?", (did,))
    assert row is not None and row["deleted_at"] is not None  # soft, not gone


def test_project_page_shows_deliverables(admin_client):
    _, pid = _project()
    _add(admin_client, pid)
    html = admin_client.get(f"/admin/studio/projects/{pid}").text
    assert "Deliverables" in html and "Hero images" in html


def test_company_view_shows_deliverable_progress(admin_client):
    cid, pid = _project()
    _add(admin_client, pid)  # 0/25
    did = db.one("SELECT id FROM project_deliverables WHERE project_id=?", (pid,))["id"]
    db.run("UPDATE project_deliverables SET delivered_qty=20 WHERE id=?", (did,))
    html = admin_client.get(f"/admin/studio/companies/{cid}").text
    assert "deliverables 20/25" in html


def test_deliverables_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.post(
            "/admin/studio/projects/1/deliverables", data={"label": "x"}, follow_redirects=False
        )
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        jobs.stop()
