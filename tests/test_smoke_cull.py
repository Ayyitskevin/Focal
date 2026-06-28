"""AI-assisted culling — operator keep/cut write routes (admin), DB-backed. Proves the decision
persists + is audited, that 'restore' is reversible, that bulk-cull is server-scoped to the
gallery, that 'cut' NEVER deletes the asset, and that the whole surface 404s until the flag is on.
"""

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
