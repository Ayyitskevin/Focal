"""Albums: marking an approved album ordered (admin, record-only).

DB-backed (real tmp DB + admin routes), same pattern as test_smoke_ai_ops.py. Proves the
order step records the spec + ordered date on an approved draft, is refused for a
non-approved draft, can be updated and cleared, writes an audit row — and never prints,
contacts a vendor, or charges.
"""

import json

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


def _draft(*, approved=True):
    """A gallery with a ready photo, a proposed album draft, optionally approved."""
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("AlbG", "Album G", "1")
    )
    db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
        (gid, "photo", "p1.jpg", "p1.jpg", "ready"),
    )
    draft_id = albums.propose_draft(gid)
    if approved:
        albums.set_status(draft_id, "approved")
    return draft_id


def test_order_records_spec_and_marks_ordered(admin_client):
    did = _draft()
    r = admin_client.post(
        f"/admin/albums/{did}/order",
        data={"size": "10x10", "cover": "linen", "notes": "rush before June"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    d = db.one(
        "SELECT ordered_at, order_size, order_cover, order_notes FROM album_drafts WHERE id=?",
        (did,),
    )
    assert d["ordered_at"] is not None
    assert d["order_size"] == "10x10" and d["order_cover"] == "linen"
    assert d["order_notes"] == "rush before June"
    a = db.one(
        "SELECT action FROM audit_log WHERE entity_type='album_draft' AND entity_id=?", (did,)
    )
    assert a["action"] == "album_ordered"
    body = admin_client.get(f"/admin/albums/{did}").text
    assert "Ordered" in body and "10x10" in body


def test_order_refused_unless_approved(admin_client):
    did = _draft(approved=False)  # status 'draft'
    r = admin_client.post(
        f"/admin/albums/{did}/order", data={"size": "8x8"}, follow_redirects=False
    )
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert db.one("SELECT ordered_at FROM album_drafts WHERE id=?", (did,))["ordered_at"] is None


def test_update_order_keeps_ordered_and_changes_spec(admin_client):
    did = _draft()
    admin_client.post(f"/admin/albums/{did}/order", data={"size": "8x8"})
    admin_client.post(f"/admin/albums/{did}/order", data={"size": "12x12", "cover": "leather"})
    d = db.one("SELECT ordered_at, order_size, order_cover FROM album_drafts WHERE id=?", (did,))
    assert d["ordered_at"] is not None  # still ordered
    assert d["order_size"] == "12x12" and d["order_cover"] == "leather"  # spec updated
    actions = {
        r["action"]
        for r in db.all_(
            "SELECT action FROM audit_log WHERE entity_type='album_draft' AND entity_id=?", (did,)
        )
    }
    assert {"album_ordered", "album_order_updated"} <= actions


def test_clear_order(admin_client):
    did = _draft()
    admin_client.post(f"/admin/albums/{did}/order", data={"size": "8x8", "notes": "n"})
    r = admin_client.post(f"/admin/albums/{did}/order/clear", follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    d = db.one("SELECT ordered_at, order_size, order_notes FROM album_drafts WHERE id=?", (did,))
    assert d["ordered_at"] is None and d["order_size"] is None and d["order_notes"] is None


def test_order_sheet_renders_for_approved(admin_client):
    did = _draft()
    admin_client.post(f"/admin/albums/{did}/order", data={"size": "10x10", "cover": "linen"})
    body = admin_client.get(f"/admin/albums/{did}/order-sheet").text
    assert "Album order sheet" in body
    assert "p1.jpg" in body  # the photo manifest is in the sheet
    assert "10x10" in body and "linen" in body  # the recorded spec
    assert "window.print()" in body  # print-to-PDF affordance


def test_manifest_csv_lists_photos_in_order(admin_client):
    did = _draft()
    admin_client.post(f"/admin/albums/{did}/order", data={"size": "8x8"})
    r = admin_client.get(f"/admin/albums/{did}/order.csv")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    text = r.text
    assert "Spread,Slot,Filename,Asset ID" in text  # manifest header
    assert "p1.jpg" in text and "8x8" in text


def test_export_refused_until_approved(admin_client):
    did = _draft(approved=False)  # status 'draft'
    r = admin_client.get(f"/admin/albums/{did}/order-sheet", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    r2 = admin_client.get(f"/admin/albums/{did}/order.csv", follow_redirects=False)
    assert r2.status_code == 303 and "err=" in r2.headers["location"]


def test_album_order_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.post("/admin/albums/1/order", data={"size": "8x8"}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/login"
        r2 = anon.get("/admin/albums/1/order-sheet", follow_redirects=False)
        assert r2.status_code == 303 and r2.headers["location"] == "/admin/login"
        jobs.stop()


def _ordered_album_on_project(admin_client, *, order=True):
    """An approved album draft whose gallery belongs to a project; optionally marked ordered."""
    cid = db.run("INSERT INTO clients (name) VALUES (?)", ("Dana",))
    pid = db.run("INSERT INTO projects (client_id, title) VALUES (?,?)", (cid, "Wedding"))
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, project_id) VALUES (?,?,?,?)",
        ("AlbP", "Album P", "1", pid),
    )
    db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
        (gid, "photo", "p1.jpg", "p1.jpg", "ready"),
    )
    did = albums.propose_draft(gid)
    albums.set_status(did, "approved")
    if order:
        admin_client.post(
            f"/admin/albums/{did}/order", data={"size": "10x10"}, follow_redirects=False
        )
    return pid, did


def test_build_invoice_from_ordered_album(admin_client):
    pid, did = _ordered_album_on_project(admin_client)
    r = admin_client.post(f"/admin/studio/invoices/from-album/{did}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/studio/invoices/")
    inv = db.one("SELECT * FROM invoices WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,))
    items = json.loads(inv["line_items"])
    assert len(items) == 1
    line = items[0]
    # clean line: labeled from the album spec, $0 for the operator to price, NO sku (not attributed)
    assert line["label"].startswith("Album") and "10x10" in line["label"]
    assert line["qty"] == 1 and line["unit_cents"] == 0 and "sku" not in line
    assert inv["status"] == "draft" and inv["total_cents"] == 0


def test_build_invoice_from_album_refused_when_not_ordered(admin_client):
    _pid, did = _ordered_album_on_project(admin_client, order=False)
    r = admin_client.post(f"/admin/studio/invoices/from-album/{did}", follow_redirects=False)
    assert r.status_code == 400


def test_build_invoice_from_album_refused_without_project(admin_client):
    # _draft()'s gallery has no project_id; ordering it then building an invoice is refused
    did = _draft()
    admin_client.post(f"/admin/albums/{did}/order", data={"size": "8x8"}, follow_redirects=False)
    r = admin_client.post(f"/admin/studio/invoices/from-album/{did}", follow_redirects=False)
    assert r.status_code == 400
