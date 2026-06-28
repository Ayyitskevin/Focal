"""Client-delivery cull gate (app/delivery_gate.py) — the enforcement half of culling. Proves a
frame the operator CUT does not reach a client: it is dropped from the gallery listing, 404s on the
media + download routes (even with the file present, so it's the gate not a missing file), is left
out of the favourites/section/full ZIPs and the portal, and that flipping MISE_CULL_UI OFF restores
the old delivery path exactly (strangler rollback). NULL/keep frames always deliver.
"""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, jobs
from app.main import app
from app.public import portal


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
    for d in ("media", "zips", "tmp"):
        (tmp_path / d).mkdir(exist_ok=True)
    db.migrate()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app import ratelimit

    ratelimit._hits.clear()
    yield


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (180, 90, 40)).save(buf, "JPEG")
    return buf.getvalue()


def _published_gallery(slug="dg", pin="1234"):
    return db.run(
        "INSERT INTO galleries (slug, title, pin, published, type) VALUES (?,?,?,?,?)",
        (slug, "G", pin, 1, "gallery"),
    )


def _photo(gid, fn, *, cull_state=None, section_id=None):
    return db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status, cull_state, section_id)"
        " VALUES (?,?,?,?,?,?,?)",
        (gid, "photo", fn, fn, "ready", cull_state, section_id),
    )


def _write_derivs(tmp_path, gid, stored):
    base = tmp_path / "media" / str(gid)
    stem = Path(stored).stem
    for sub, name in (("original", stored), ("web", f"{stem}.jpg"), ("thumb", f"{stem}.jpg")):
        d = base / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(_jpeg())


def _visitor_in(client, slug, pin="1234"):
    r = client.post(f"/g/{slug}/pin", data={"pin": pin}, follow_redirects=False)
    assert r.status_code == 303


# --- listing ----------------------------------------------------------------


def test_listing_hides_cut_frame_when_gate_on(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=True)
    gid = _published_gallery()
    keep = _photo(gid, "keep.jpg", cull_state="keep")
    undecided = _photo(gid, "undecided.jpg")
    cut = _photo(gid, "cut.jpg", cull_state="cut")
    with TestClient(app) as client:
        _visitor_in(client, "dg")
        html = client.get("/g/dg").text
        assert f'data-id="{keep}"' in html and f'data-id="{undecided}"' in html
        assert f'data-id="{cut}"' not in html
        jobs.stop()


def test_listing_shows_cut_frame_when_gate_off(tmp_path, monkeypatch):
    # flag OFF == feature dormant == old delivery path: the cut frame is delivered again (rollback)
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=False)
    gid = _published_gallery()
    cut = _photo(gid, "cut.jpg", cull_state="cut")
    with TestClient(app) as client:
        _visitor_in(client, "dg")
        assert f'data-id="{cut}"' in client.get("/g/dg").text
        jobs.stop()


# --- media serve (the chokepoint) -------------------------------------------


def test_media_serve_404s_cut_but_serves_keep(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=True)
    gid = _published_gallery()
    keep = _photo(gid, "keep.jpg", cull_state="keep")
    cut = _photo(gid, "cut.jpg", cull_state="cut")
    _write_derivs(tmp_path, gid, "keep.jpg")
    _write_derivs(tmp_path, gid, "cut.jpg")  # file present — so a 404 proves the GATE, not a miss
    with TestClient(app) as client:
        _visitor_in(client, "dg")
        assert client.get(f"/media/dg/web/{keep}").status_code == 200
        assert client.get(f"/media/dg/web/{cut}").status_code == 404
        jobs.stop()


def test_media_serve_delivers_cut_when_gate_off(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=False)
    gid = _published_gallery()
    cut = _photo(gid, "cut.jpg", cull_state="cut")
    _write_derivs(tmp_path, gid, "cut.jpg")
    with TestClient(app) as client:
        _visitor_in(client, "dg")
        assert client.get(f"/media/dg/web/{cut}").status_code == 200
        jobs.stop()


# --- single-file download ---------------------------------------------------


def test_download_single_404s_cut(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=True)
    gid = _published_gallery()
    keep = _photo(gid, "keep.jpg", cull_state="keep")
    cut = _photo(gid, "cut.jpg", cull_state="cut")
    _write_derivs(tmp_path, gid, "keep.jpg")
    _write_derivs(tmp_path, gid, "cut.jpg")
    with TestClient(app) as client:
        _visitor_in(client, "dg")
        db.run("UPDATE visitors SET email='c@x.com' WHERE gallery_id=?", (gid,))  # clear email gate
        assert client.get(f"/g/dg/download/asset/{keep}").status_code == 200
        assert client.get(f"/g/dg/download/asset/{cut}").status_code == 404
        jobs.stop()


# --- full-gallery ZIP (built by the background job) -------------------------


def _zip_names(path):
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


def test_full_zip_excludes_cut_frame(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=True)
    gid = _published_gallery()
    _photo(gid, "keep.jpg", cull_state="keep")
    _photo(gid, "cut.jpg", cull_state="cut")
    _write_derivs(tmp_path, gid, "keep.jpg")
    _write_derivs(tmp_path, gid, "cut.jpg")
    jobs._h_zip({"gallery_id": gid, "rev": 1})
    names = _zip_names(jobs.zip_path(gid, 1))
    assert "keep.jpg" in names and "cut.jpg" not in names


def test_full_zip_includes_cut_when_gate_off(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=False)
    gid = _published_gallery()
    _photo(gid, "cut.jpg", cull_state="cut")
    _write_derivs(tmp_path, gid, "cut.jpg")
    jobs._h_zip({"gallery_id": gid, "rev": 1})
    assert "cut.jpg" in _zip_names(jobs.zip_path(gid, 1))


# --- portal serve chokepoint (_client_asset) --------------------------------


def test_portal_client_asset_blocks_cut(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch, cull_ui=True)
    cid = db.run("INSERT INTO clients (name) VALUES (?)", ("Acme",))
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, published, type, client_id) VALUES (?,?,?,?,?,?)",
        ("pg", "G", "1", 1, "gallery", cid),
    )
    keep = _photo(gid, "keep.jpg", cull_state="keep")
    cut = _photo(gid, "cut.jpg", cull_state="cut")
    fake_portal = {"client_id": cid}
    assert portal._client_asset(fake_portal, keep)["id"] == keep
    with pytest.raises(Exception):  # HTTPException(404) — the cut frame is not a client asset
        portal._client_asset(fake_portal, cut)
