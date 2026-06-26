"""Mnemosyne album foundation — DB-backed smoke (migration 066 + persistence).

Applies the real migrations against a temp DB, then proves: eligibility honors the
photo/ready filter, validate_layout reads the live eligible set, save_draft persists a
draft + its placements atomically, and save_draft REFUSES (writing nothing) when the
layout would omit-by-error, duplicate, or place a foreign asset, or is empty.
"""

from app import albums, config, db


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


def _gallery(slug="AlbGal"):
    return db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", (slug, "A", "1"))


def _asset(gallery_id, *, kind="photo", status="ready", filename="p.jpg"):
    return db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
        (gallery_id, kind, filename, f"stored/{filename}", status),
    )


def test_migration_creates_album_tables(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    names = {
        r["name"]
        for r in db.all_("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'album%'")
    }
    assert {"album_drafts", "album_placements"} <= names


def test_eligibility_is_photo_and_ready_only(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    photo = _asset(gid, kind="photo", status="ready")
    _asset(gid, kind="video", status="ready")  # video -> excluded
    _asset(gid, kind="photo", status="pending")  # not ready -> excluded
    other = _gallery("OtherGal")
    _asset(other, kind="photo", status="ready")  # different gallery -> excluded
    assert albums.eligible_asset_ids(gid) == {photo}


def test_save_draft_persists_draft_and_placements(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    a1, a2, a3 = _asset(gid), _asset(gid), _asset(gid)
    placements = [
        {"asset_id": a1, "spread": 0, "slot": 0},
        {"asset_id": a2, "spread": 0, "slot": 1},
        {"asset_id": a3, "spread": 1, "slot": 0},
    ]
    draft_id = albums.save_draft(gid, placements, provider="mock", model="mock-albums-1")

    draft = db.one("SELECT * FROM album_drafts WHERE id=?", (draft_id,))
    assert draft["gallery_id"] == gid and draft["status"] == "draft"
    assert draft["provider"] == "mock" and draft["spread_count"] == 2
    rows = db.all_(
        "SELECT asset_id, spread, slot FROM album_placements WHERE album_draft_id=? ORDER BY id",
        (draft_id,),
    )
    assert [r["asset_id"] for r in rows] == [a1, a2, a3]


def test_save_draft_refuses_foreign_asset_and_writes_nothing(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    a1 = _asset(gid)
    other = _gallery("Foreign")
    foreign = _asset(other)
    try:
        albums.save_draft(gid, [{"asset_id": a1}, {"asset_id": foreign}])
        raise AssertionError("expected LayoutError")
    except albums.LayoutError as e:
        assert any(i.code == "foreign_asset" for i in e.validation.issues)
    # the refused draft left no rows behind
    assert db.one("SELECT COUNT(*) AS n FROM album_drafts")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM album_placements")["n"] == 0


def test_save_draft_refuses_duplicate(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    a1 = _asset(gid)
    try:
        albums.save_draft(gid, [{"asset_id": a1, "spread": 0}, {"asset_id": a1, "spread": 1}])
        raise AssertionError("expected LayoutError")
    except albums.LayoutError as e:
        assert any(i.code == "duplicate" for i in e.validation.issues)
    assert db.one("SELECT COUNT(*) AS n FROM album_drafts")["n"] == 0


def test_save_draft_refuses_empty(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    _asset(gid)
    try:
        albums.save_draft(gid, [])
        raise AssertionError("expected LayoutError")
    except albums.LayoutError:
        pass
    assert db.one("SELECT COUNT(*) AS n FROM album_drafts")["n"] == 0


def test_validate_layout_surfaces_omitted_against_live_gallery(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    a1, a2 = _asset(gid), _asset(gid)
    v = albums.validate_layout(gid, [{"asset_id": a1, "spread": 0, "slot": 0}])
    assert v.ok and v.omitted == (a2,)
