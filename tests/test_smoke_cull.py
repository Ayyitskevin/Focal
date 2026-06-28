"""AI-assisted culling — operator keep/cut write routes + the keyboard deck (admin), DB-backed.
Proves the decision persists + is audited, that 'restore' is reversible, that bulk-cull is
server-scoped to the gallery, that 'cut' NEVER deletes the asset, that the deck ranks by keeper
score and serves a large preview, that a deck fetch (HX-Request) gets a snappy 204, and that the
whole surface 404s until the flag is on.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs
from app.admin.cull import _ACTIONS
from app.main import app


def _configure_tmp_db(tmp_path, monkeypatch, *, cull_ui=True):
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
        "CULL_UI": cull_ui,
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


def _gallery_with_assets(n=3, slug="cullg"):
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", (slug, "G", "1"))
    ids = []
    for i in range(n):
        ids.append(
            db.run(
                "INSERT INTO assets (gallery_id, kind, filename, stored, status, argus_keeper_score)"
                " VALUES (?,?,?,?,?,?)",
                (gid, "photo", f"p{i}.jpg", f"p{i}.jpg", "ready", 0.2 + i * 0.3),
            )
        )
    return gid, ids


# --- pure mapping (CI unit) -------------------------------------------------


@pytest.mark.unit
def test_action_mapping():
    assert _ACTIONS == {"keep": "keep", "cut": "cut", "restore": None}


# --- write routes -----------------------------------------------------------


def test_cull_keep_then_cut_then_restore_persists_and_audits(admin_client):
    gid, ids = _gallery_with_assets(1)
    aid = ids[0]
    admin_client.post(f"/admin/galleries/{gid}/assets/{aid}/cull", data={"action": "cut"})
    row = db.one("SELECT cull_state, cull_decided_at, cull_source FROM assets WHERE id=?", (aid,))
    assert row["cull_state"] == "cut" and row["cull_decided_at"] and row["cull_source"] == "manual"
    # reversible: restore clears the decision
    admin_client.post(f"/admin/galleries/{gid}/assets/{aid}/cull", data={"action": "restore"})
    row = db.one("SELECT cull_state, cull_decided_at, cull_source FROM assets WHERE id=?", (aid,))
    assert (
        row["cull_state"] is None and row["cull_decided_at"] is None and row["cull_source"] is None
    )
    actions = {
        r["action"]
        for r in db.all_(
            "SELECT action FROM audit_log WHERE entity_type='asset' AND entity_id=?", (aid,)
        )
    }
    assert {"cull:cut", "cull:restore"} <= actions


def test_cut_never_deletes_the_asset(admin_client):
    gid, ids = _gallery_with_assets(1)
    aid = ids[0]
    admin_client.post(f"/admin/galleries/{gid}/assets/{aid}/cull", data={"action": "cut"})
    # the row (and its file pointer) survive — cut is a soft, reversible flag
    assert db.one("SELECT id FROM assets WHERE id=?", (aid,)) is not None


def test_bad_action_rejected(admin_client):
    gid, ids = _gallery_with_assets(1)
    r = admin_client.post(
        f"/admin/galleries/{gid}/assets/{ids[0]}/cull",
        data={"action": "delete"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert db.one("SELECT cull_state FROM assets WHERE id=?", (ids[0],))["cull_state"] is None


def test_bulk_cull_is_scoped_to_the_gallery(admin_client):
    gid, ids = _gallery_with_assets(2, slug="ga")
    other_gid, other_ids = _gallery_with_assets(1, slug="gb")
    # post this gallery's ids PLUS another gallery's id — the foreign id must be ignored
    data = {"action": "cut", "asset_ids": [str(i) for i in ids] + [str(other_ids[0])]}
    r = admin_client.post(
        f"/admin/galleries/{gid}/assets/bulk-cull", data=data, follow_redirects=False
    )
    assert r.status_code == 303
    assert all(
        db.one("SELECT cull_state FROM assets WHERE id=?", (i,))["cull_state"] == "cut" for i in ids
    )
    # the other gallery's asset is untouched (server-side scope, not client-trusted)
    assert db.one("SELECT cull_state FROM assets WHERE id=?", (other_ids[0],))["cull_state"] is None


def test_cull_routes_404_when_disabled(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=False)
    with TestClient(app) as client:
        client.post("/admin/login", data={"password": "test-pw"}, follow_redirects=False)
        gid, ids = _gallery_with_assets(1)
        r = client.post(
            f"/admin/galleries/{gid}/assets/{ids[0]}/cull",
            data={"action": "cut"},
            follow_redirects=False,
        )
        assert r.status_code == 404
        assert db.one("SELECT cull_state FROM assets WHERE id=?", (ids[0],))["cull_state"] is None
        jobs.stop()


def test_cull_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.post(
            "/admin/galleries/1/assets/1/cull", data={"action": "cut"}, follow_redirects=False
        )
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        jobs.stop()


def test_decision_returns_204_for_deck_fetch(admin_client):
    # the deck posts with HX-Request and wants an empty 204 (no full-page reload per keystroke);
    # a plain form post still gets a 303 (covered elsewhere). Both persist the decision.
    gid, ids = _gallery_with_assets(1)
    r = admin_client.post(
        f"/admin/galleries/{gid}/assets/{ids[0]}/cull",
        data={"action": "cut"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 204
    assert db.one("SELECT cull_state FROM assets WHERE id=?", (ids[0],))["cull_state"] == "cut"


# --- deck (GET) -------------------------------------------------------------


def _deck_queue(html: str):
    m = re.search(r'<script type="application/json" id="cull-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


def test_deck_renders_and_ranks_by_score(admin_client):
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("deckg", "G", "1"))
    # three scored photos + one unscored — deck order is best-first, unscored last
    for fn, score in [("a.jpg", 0.2), ("b.jpg", 0.9), ("c.jpg", 0.5), ("d.jpg", None)]:
        db.run(
            "INSERT INTO assets (gallery_id, kind, filename, stored, status, argus_keeper_score)"
            " VALUES (?,?,?,?,?,?)",
            (gid, "photo", fn, fn, "ready", score),
        )
    r = admin_client.get(f"/admin/galleries/{gid}/cull")
    assert r.status_code == 200
    q = _deck_queue(r.text)
    assert [item["file"] for item in q] == ["b.jpg", "c.jpg", "a.jpg", "d.jpg"]
    # the unscored frame is present but last, with a null score
    assert q[-1]["score"] is None


def test_deck_excludes_non_ready_and_video(admin_client):
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("deckx", "G", "1"))
    db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status, argus_keeper_score)"
        " VALUES (?,?,?,?,?,?)",
        (gid, "photo", "ok.jpg", "ok.jpg", "ready", 0.5),
    )
    db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
        (gid, "photo", "pending.jpg", "pending.jpg", "pending"),
    )
    db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
        (gid, "video", "clip.mp4", "clip.mp4", "ready"),
    )
    q = _deck_queue(admin_client.get(f"/admin/galleries/{gid}/cull").text)
    assert [item["file"] for item in q] == ["ok.jpg"]


def test_deck_404_when_disabled(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=False)
    with TestClient(app) as client:
        client.post("/admin/login", data={"password": "test-pw"}, follow_redirects=False)
        gid, _ = _gallery_with_assets(1)
        assert client.get(f"/admin/galleries/{gid}/cull").status_code == 404
        jobs.stop()


# --- preview (GET) ----------------------------------------------------------


def test_preview_serves_web_derivative(admin_client, tmp_path):
    gid, ids = _gallery_with_assets(1)  # stored = p0.jpg
    web = tmp_path / "media" / str(gid) / "web"
    web.mkdir(parents=True)
    (web / "p0.jpg").write_bytes(b"\xff\xd8\xff\xe0jpegbytes")
    r = admin_client.get(f"/admin/galleries/{gid}/cull/preview/{ids[0]}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_preview_404_when_derivative_missing(admin_client):
    gid, ids = _gallery_with_assets(1)  # no file written to disk
    assert admin_client.get(f"/admin/galleries/{gid}/cull/preview/{ids[0]}").status_code == 404


def test_preview_404_when_disabled(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=False)
    with TestClient(app) as client:
        client.post("/admin/login", data={"password": "test-pw"}, follow_redirects=False)
        gid, ids = _gallery_with_assets(1)
        assert client.get(f"/admin/galleries/{gid}/cull/preview/{ids[0]}").status_code == 404
        jobs.stop()
