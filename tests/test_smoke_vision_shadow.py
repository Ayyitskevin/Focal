"""Phase 2 integration: the vision shadow runner against a real DB.

Proves that with the flag armed and a challenger registered, shadowing a completed Argus
run records exactly two linked ai_runs rows (legacy snapshot + challenger) and writes
NOTHING to assets or galleries. Same DB-backed pattern as test_smoke_argus.py.
"""

import json

import pytest

from app import config, db, jobs, vision_shadow
from app.providers import Capability, registry
from app.providers import vision_challenger as vc
from app.providers.mocks import MockVisionChallengerAdapter


def _configure_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(config, "ZIP_DIR", tmp_path / "zips")
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(config, "BRAND_DIR", tmp_path / "brand")
    monkeypatch.setattr(config, "RECEIPTS_DIR", tmp_path / "receipts")
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "test-pw")
    db.migrate()


@pytest.fixture(autouse=True)
def _reset_registry():
    registry.reset()
    yield
    registry.reset()


def _gallery_with_completed_run(run_id=42):
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)",
        ("ShadowGal01", "Shadow", "1234"),
    )
    db.run(
        "UPDATE galleries SET argus_last_run_id=?, argus_last_status='done', "
        "argus_analyzed_count=3, argus_hero_asset_ids='[1,2]' WHERE id=?",
        (run_id, gid),
    )
    return gid


def test_handler_registered():
    assert "vision_shadow_gallery" in jobs.HANDLERS


def test_shadow_records_two_linked_runs_and_touches_no_assets(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "VISION_SHADOW", True)
    gid = _gallery_with_completed_run(run_id=42)
    before = db.one("SELECT * FROM galleries WHERE id=?", (gid,))

    with registry.use_challenger(Capability.VISION, MockVisionChallengerAdapter()):
        comparison = vision_shadow.run_for_gallery(gid)

    assert comparison is not None
    assert comparison["both_ok"] is True
    assert comparison["legacy_provider"] == "argus"
    assert comparison["challenger_provider"] == "mock-challenger"

    rows = db.all_(
        "SELECT * FROM ai_runs WHERE subject_type='gallery' AND subject_id=? ORDER BY id",
        (gid,),
    )
    assert len(rows) == 2
    providers = {r["provider"] for r in rows}
    assert providers == {"argus", "mock-challenger"}
    # both rows share one correlation id linking the comparison pair
    assert rows[0]["correlation_id"] == rows[1]["correlation_id"]
    assert rows[0]["correlation_id"].startswith("shadow:gallery:")
    assert all(r["capability"] == "vision" for r in rows)

    # ASSET-SAFE: the gallery's Argus columns are unchanged, no assets created
    after = db.one("SELECT * FROM galleries WHERE id=?", (gid,))
    assert after["argus_last_run_id"] == before["argus_last_run_id"]
    assert after["argus_analyzed_count"] == before["argus_analyzed_count"]
    assert after["argus_hero_asset_ids"] == before["argus_hero_asset_ids"]
    assert db.one("SELECT COUNT(*) AS n FROM assets WHERE gallery_id=?", (gid,))["n"] == 0


def test_shadow_noop_when_legacy_run_not_done(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "VISION_SHADOW", True)
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)",
        ("ShadowGal02", "Shadow2", "1234"),
    )  # no completed Argus run
    with registry.use_challenger(Capability.VISION, MockVisionChallengerAdapter()):
        assert vision_shadow.run_for_gallery(gid) is None
    assert db.one("SELECT COUNT(*) AS n FROM ai_runs WHERE subject_id=?", (gid,))["n"] == 0


def _fake_sync_argus(monkeypatch, run_id=7):
    import json

    from app import argus_analyze

    class FakeResp:
        def read(self):
            return json.dumps(
                {"mode": "sync", "run_id": run_id, "review_url": f"http://argus:8010/runs/{run_id}"}
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(argus_analyze.urllib.request, "urlopen", lambda req, timeout: FakeResp())


def test_completed_argus_run_enqueues_shadow_when_flag_on(tmp_path, monkeypatch):
    from app import argus_analyze

    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus:8010")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    monkeypatch.setattr(config, "VISION_SHADOW", True)
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
        ("ShadowEnq01", "Enq", "1234"),
    )
    enq = []
    monkeypatch.setattr(jobs, "enqueue", lambda kind, payload: enq.append((kind, payload)) or 1)
    _fake_sync_argus(monkeypatch)
    argus_analyze.run_for_gallery(gid)
    assert ("vision_shadow_gallery", {"gallery_id": gid}) in enq


def test_completed_argus_run_no_shadow_when_flag_off(tmp_path, monkeypatch):
    from app import argus_analyze

    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus:8010")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    monkeypatch.setattr(config, "VISION_SHADOW", False)
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
        ("ShadowEnq02", "Enq2", "1234"),
    )
    enq = []
    monkeypatch.setattr(jobs, "enqueue", lambda kind, payload: enq.append((kind, payload)) or 1)
    _fake_sync_argus(monkeypatch)
    argus_analyze.run_for_gallery(gid)
    assert ("vision_shadow_gallery", {"gallery_id": gid}) not in enq


def test_real_qwen_challenger_autoregisters_and_shadow_records(tmp_path, monkeypatch):
    """End-to-end with the REAL Qwen3-VL adapter: configuring its URL auto-registers it as
    the VISION challenger; with a real web derivative on disk and a mocked OpenAI-compatible
    endpoint, shadow records two linked ai_runs rows (argus + qwen3-vl) and touches no
    assets."""
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "VISION_SHADOW", True)
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    monkeypatch.setattr(config, "VISION_CHALLENGER_MODEL", "qwen3-vl:32b")
    gid = _gallery_with_completed_run(run_id=55)

    # a real (tiny) web derivative the adapter will read + base64-encode
    web = config.MEDIA_DIR / str(gid) / "web"
    web.mkdir(parents=True, exist_ok=True)
    (web / "shot.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg\xff\xd9")

    class _Resp:
        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "keywords: plated dish; alt: a bowl"}}]}
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(vc.urllib.request, "urlopen", lambda req, timeout: _Resp())

    # no use_challenger() — relies on registry auto-registration from the configured URL
    comparison = vision_shadow.run_for_gallery(gid)
    assert comparison is not None and comparison["challenger_provider"] == "qwen3-vl"

    rows = db.all_(
        "SELECT * FROM ai_runs WHERE subject_type='gallery' AND subject_id=? ORDER BY id", (gid,)
    )
    assert {r["provider"] for r in rows} == {"argus", "qwen3-vl"}
    chal = next(r for r in rows if r["provider"] == "qwen3-vl")
    assert chal["status"] == "ok" and chal["model"] == "qwen3-vl:32b"
    # asset-safe: no assets created for this gallery
    assert db.one("SELECT COUNT(*) AS n FROM assets WHERE gallery_id=?", (gid,))["n"] == 0
