"""Commercial deliverable templates: clone canned intake deliverables into a project as
normal audited project_deliverables rows. No send/charge/publish side effects.
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


def _project():
    cid = db.run("INSERT INTO clients (name, company) VALUES (?,?)", ("Acme", "Acme Group"))
    return db.run(
        "INSERT INTO projects (client_id, title, status) VALUES (?,?,?)",
        (cid, "Spring menu", "session_planning"),
    )


def test_clone_menu_stills_template_creates_audited_deliverables(admin_client):
    pid = _project()
    r = admin_client.post(
        f"/admin/studio/projects/{pid}/deliverables/template",
        data={"template_key": "menu_stills"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    rows = db.all_(
        "SELECT * FROM project_deliverables WHERE project_id=? AND deleted_at IS NULL "
        "ORDER BY sort_order",
        (pid,),
    )
    assert [r["label"] for r in rows] == [
        "Edited hero/detail images",
        "Social crop pack",
        "Web gallery",
    ]
    assert rows[0]["spec_qty"] == 25 and rows[0]["unit"] == "images"
    assert rows[0]["spec_format"] == "JPEG sRGB" and rows[0]["delivered_qty"] == 0
    assert db.one(
        "SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='project_deliverable' "
        "AND action='create'"
    )["n"] == len(rows)
    html = admin_client.get(f"/admin/studio/projects/{pid}").text
    assert "Clone template" in html and "Edited hero/detail images" in html


def test_clone_template_appends_after_existing_deliverables(admin_client):
    pid = _project()
    admin_client.post(
        f"/admin/studio/projects/{pid}/deliverables",
        data={"label": "Existing line", "spec_qty": "1", "unit": "files", "sort_order": "90"},
        follow_redirects=False,
    )
    admin_client.post(
        f"/admin/studio/projects/{pid}/deliverables/template",
        data={"template_key": "hero_reels"},
        follow_redirects=False,
    )
    first_template = db.one(
        "SELECT * FROM project_deliverables WHERE project_id=? AND label='Hero images'",
        (pid,),
    )
    assert first_template["sort_order"] == 100


def test_clone_template_rejects_unknown_key(admin_client):
    pid = _project()
    r = admin_client.post(
        f"/admin/studio/projects/{pid}/deliverables/template",
        data={"template_key": "bogus"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert (
        db.one("SELECT COUNT(*) AS n FROM project_deliverables WHERE project_id=?", (pid,))["n"]
        == 0
    )
