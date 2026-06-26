"""Operations: the album review UI (admin).

DB-backed (real tmp DB + admin routes), same pattern as test_smoke_offers_view.py.
Proves the queue lists drafts, the propose form creates a baseline draft, the detail page
renders the spreads + omitted photos + a live validity re-check, approve/reject transition
the status, and the write routes are admin-gated.
"""

import pytest
from fastapi.testclient import TestClient

from app import albums, config, db, jobs
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


def _gallery_with_photos(n=3, slug="AlbView"):
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", (slug, "Wedding", "1"))
    for i in range(n):
        db.run(
            "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
            (gid, "photo", f"p{i}.jpg", f"stored/p{i}.jpg", "ready"),
        )
    return gid


def test_queue_empty_state(admin_client):
    assert "No album drafts" in admin_client.get("/admin/albums").text


def test_propose_creates_draft_and_redirects_to_detail(admin_client):
    gid = _gallery_with_photos()
    r = admin_client.post("/admin/albums/propose", data={"gallery_id": str(gid)})
    assert r.status_code == 200  # followed the 303 to the detail page
    assert "Baseline album proposed" in r.text
    assert "Spread 1" in r.text  # spreads rendered
    assert albums.list_drafts(gid)  # a draft exists


def test_propose_rejects_gallery_without_photos(admin_client):
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("Empty", "E", "1"))
    r = admin_client.post("/admin/albums/propose", data={"gallery_id": str(gid)})
    assert "no ready photos" in r.text
    assert albums.list_drafts(gid) == []


def test_propose_rejects_unknown_gallery(admin_client):
    r = admin_client.post("/admin/albums/propose", data={"gallery_id": "9999"})
    assert "No gallery #9999" in r.text


def test_detail_shows_omitted(admin_client):
    gid = _gallery_with_photos(n=2)
    # hand-build a draft that places only one of the two ready photos
    a_ids = [
        r["id"] for r in db.all_("SELECT id FROM assets WHERE gallery_id=? ORDER BY id", (gid,))
    ]
    draft_id = albums.save_draft(gid, [{"asset_id": a_ids[0], "spread": 0, "slot": 0}])
    body = admin_client.get(f"/admin/albums/{draft_id}").text
    assert "Omitted" in body and "1 eligible photo" in body


def test_approve_and_reject_transition_status(admin_client):
    gid = _gallery_with_photos()
    draft_id = albums.propose_draft(gid)
    r = admin_client.post(f"/admin/albums/{draft_id}/approve")
    assert "Album approved" in r.text
    assert albums.get_draft(draft_id)["status"] == "approved"

    gid2 = _gallery_with_photos(slug="AlbView2")
    d2 = albums.propose_draft(gid2)
    r = admin_client.post(f"/admin/albums/{d2}/reject")
    assert "Album rejected" in r.text
    assert albums.get_draft(d2)["status"] == "rejected"


def test_queue_filter_by_status(admin_client):
    gid = _gallery_with_photos()
    draft_id = albums.propose_draft(gid)
    albums.set_status(draft_id, "approved")
    approved = admin_client.get("/admin/albums?status=approved").text
    assert "Wedding" in approved
    assert "No album drafts" in admin_client.get("/admin/albums?status=rejected").text


def test_detail_404_for_missing_draft_redirects(admin_client):
    r = admin_client.get("/admin/albums/4242", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/albums")


def test_album_routes_require_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        for method, path in (
            ("get", "/admin/albums"),
            ("post", "/admin/albums/propose"),
            ("post", "/admin/albums/1/approve"),
        ):
            r = getattr(anon, method)(path, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        jobs.stop()
