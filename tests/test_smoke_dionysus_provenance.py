"""Phase 4: Dionysus pack-draft provenance into the ai_runs ledger (flag-gated).

DB-backed. platekit._record writes platekit_last_* on every notify_argus_complete
outcome; with the content facade flag armed it now also logs one CONTENT/dionysus ai_runs
row, mapping done/queued -> ok, skipped -> disabled, else -> provider_error. Flag off =
no ledger row (unchanged behavior).
"""

from app import config, db, platekit


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


def _gallery(slug="DioGal"):
    return db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", (slug, "D", "1"))


def test_provenance_recorded_when_flag_on(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", True)
    gid = _gallery()
    platekit._record(gid, status="done", job_id="j1", pack_id=3)
    row = db.one("SELECT * FROM ai_runs WHERE subject_type='gallery' AND subject_id=?", (gid,))
    assert row["capability"] == "content" and row["provider"] == "dionysus"
    assert row["status"] == "ok" and row["review"] == "human_review"


def test_status_mapping_error_and_skipped(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", True)
    g1, g2 = _gallery("DioErr"), _gallery("DioSkip")
    platekit._record(g1, status="error", error="Dionysus 500")
    platekit._record(g2, status="skipped", error="no client Platekit slug")
    assert (
        db.one("SELECT status FROM ai_runs WHERE subject_id=?", (g1,))["status"] == "provider_error"
    )
    assert db.one("SELECT status FROM ai_runs WHERE subject_id=?", (g2,))["status"] == "disabled"


def test_provenance_failure_never_raises_and_legacy_write_survives(tmp_path, monkeypatch):
    """The provenance record runs inside _record, called from a fire-and-forget hook, so a
    ledger failure must be swallowed AND must not corrupt the primary platekit_last_* write
    (which runs first)."""
    from app import ai_runs

    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", True)
    gid = _gallery()

    def boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(ai_runs, "record", boom)
    # must not propagate
    platekit._record(gid, status="done", job_id="j1")
    # primary write still landed
    row = db.one(
        "SELECT platekit_last_status, platekit_last_job_id FROM galleries WHERE id=?", (gid,)
    )
    assert row["platekit_last_status"] == "done" and row["platekit_last_job_id"] == "j1"


def test_no_provenance_when_flag_off(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", False)
    gid = _gallery()
    platekit._record(gid, status="done")
    # legacy platekit_last_* still written; no ai_runs row
    assert (
        db.one("SELECT platekit_last_status FROM galleries WHERE id=?", (gid,))[
            "platekit_last_status"
        ]
        == "done"
    )
    assert db.one("SELECT COUNT(*) AS n FROM ai_runs WHERE subject_id=?", (gid,))["n"] == 0
