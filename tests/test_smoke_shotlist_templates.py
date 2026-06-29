"""Commercial shot-list templates: clone canned intake lists into a project as normal
audited shot_list rows. No sync/publish side effects; the operator edits the rows afterward.
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


def test_clone_hero_detail_template_creates_audited_shots(admin_client):
    pid = _project()
    r = admin_client.post(
        f"/admin/studio/projects/{pid}/shots/template",
        data={"template_key": "hero_detail"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    rows = db.all_(
        "SELECT * FROM shot_list WHERE project_id=? AND deleted_at IS NULL ORDER BY sort_order",
        (pid,),
    )
    assert [r["title"] for r in rows][:3] == [
        "Plated hero, three-quarter",
        "Overhead hero",
        "Texture/detail close-up",
    ]
    assert rows[0]["priority"] == "must" and rows[0]["category"] == "Hero Dish"
    assert rows[-1]["priority"] == "if-time"
    assert db.one(
        "SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='shot_list' AND action='create'"
    )["n"] == len(rows)
    html = admin_client.get(f"/admin/studio/projects/{pid}").text
    assert "Clone template" in html and "Plated hero, three-quarter" in html


def test_clone_template_appends_after_existing_shots(admin_client):
    pid = _project()
    admin_client.post(
        f"/admin/studio/projects/{pid}/shots",
        data={"title": "Existing opener", "sort_order": "90"},
        follow_redirects=False,
    )
    admin_client.post(
        f"/admin/studio/projects/{pid}/shots/template",
        data={"template_key": "menu_three_part"},
        follow_redirects=False,
    )
    first_template = db.one(
        "SELECT * FROM shot_list WHERE project_id=? AND title='Full menu lineup'", (pid,)
    )
    assert first_template["sort_order"] == 100


def test_clone_template_rejects_unknown_key(admin_client):
    pid = _project()
    r = admin_client.post(
        f"/admin/studio/projects/{pid}/shots/template",
        data={"template_key": "bogus"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert db.one("SELECT COUNT(*) AS n FROM shot_list WHERE project_id=?", (pid,))["n"] == 0
