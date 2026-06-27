"""Operations: the vision cutover preflight page (admin).

DB-backed (real tmp DB + admin routes), same pattern as test_smoke_ai_ops.py. Proves the
checklist renders, the manual writeback is refused (interlock) and writes nothing, the
dry-run preview is asset-safe, and the page is admin-gated.
"""

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs, qwen_writeback
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
    monkeypatch.setattr(config, "VISION_CHALLENGER_MODEL", "qwen3-vl:32b")
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


def test_checklist_renders_not_ready_by_default(admin_client):
    body = admin_client.get("/admin/vision-cutover").text
    assert "Vision cutover" in body
    assert "Promotion readiness" in body
    assert "step" in body and "remaining" in body  # the not-ready verdict badge
    assert "Production provider now:" in body


def test_manual_writeback_is_refused_until_eligible(admin_client):
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("CutG", "G", "1"))
    a1 = db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
        (gid, "photo", "p.jpg", "p.jpg", "ready"),
    )
    r = admin_client.post(
        "/admin/vision-cutover/run", data={"gallery_id": gid}, follow_redirects=False
    )
    assert r.status_code == 303 and "Refused" in r.headers["location"]
    # nothing queued, nothing written
    assert db.one("SELECT COUNT(*) AS n FROM jobs")["n"] == 0
    assert (
        db.one("SELECT argus_keeper_score FROM assets WHERE id=?", (a1,))["argus_keeper_score"]
        is None
    )


def test_preview_writes_nothing(admin_client, monkeypatch):
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("PrevG", "G", "1"))
    a1 = db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
        (gid, "photo", "p1.jpg", "p1.jpg", "ready"),
    )
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    monkeypatch.setattr(
        qwen_writeback,
        "_fetch_structured",
        lambda g: [
            {
                "basename": "p1.jpg",
                "keywords": ["plate"],
                "alt_text": "a plate",
                "keeper_score": 0.9,
                "hero_potential": 0.8,
            }
        ],
    )
    body = admin_client.post("/admin/vision-cutover/preview", data={"gallery_id": gid}).text
    assert "p1.jpg" in body and "parsed (not written)" in body
    assert (
        db.one("SELECT argus_keeper_score FROM assets WHERE id=?", (a1,))["argus_keeper_score"]
        is None
    )


def test_vision_cutover_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/vision-cutover", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/login"
        jobs.stop()


def test_vision_cutover_post_routes_require_admin(tmp_path, monkeypatch):
    """The two POST routes are gated only by the shared router-level require_admin; the GET
    landing is the only one with an explicit anon test. /run is the manual production-
    writeback trigger and /preview hits the (potentially live) challenger endpoint — a
    refactor that re-routed either must not expose it unauthenticated. Lock both."""
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        for path in ("/admin/vision-cutover/run", "/admin/vision-cutover/preview"):
            r = anon.post(path, data={"gallery_id": 1}, follow_redirects=False)
            assert r.status_code == 303, path
            assert r.headers["location"] == "/admin/login", path
        jobs.stop()
