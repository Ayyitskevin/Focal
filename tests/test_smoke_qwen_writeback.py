"""Qwen writeback — DB-backed: the deterministic asset writeback + the dormancy interlock.

Proves apply_to_gallery writes the same argus_* asset columns + gallery hero set as
argus_writeback (matched by basename, photo+ready only, idempotent, foreign signals
ignored), and that writeback_gallery is INERT until the cutover seam designates Qwen as
the eligible production provider — so the scaffold mutates nothing today.
"""

import json
from types import SimpleNamespace

from app import config, db, jobs, qwen_writeback, validation
from app.providers.vision_challenger import InternalVisionChallengerAdapter


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


def _gallery():
    return db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("QwenG", "Q", "1"))


def _asset(gid, stored, *, kind="photo", status="ready"):
    return db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
        (gid, kind, stored, stored, status),
    )


def test_apply_writes_argus_columns_and_hero_set(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    a1 = _asset(gid, "p1.jpg")
    _asset(gid, "p2.jpg")
    photos = [
        {
            "basename": "p1.jpg",
            "keywords": ["plate"],
            "alt_text": "a plate",
            "keeper_score": 0.9,
            "hero_potential": 0.8,
        },
        {
            "basename": "p2.jpg",
            "keywords": [],
            "alt_text": None,
            "keeper_score": 0.4,
            "hero_potential": 0.2,
        },
    ]
    res = qwen_writeback.apply_to_gallery(gid, photos)
    assert res["matched"] == 2 and res["hero_asset_ids"] == [a1]  # only a1 clears 0.5

    r1 = db.one(
        "SELECT argus_alt_text, argus_keywords, argus_keeper_score FROM assets WHERE id=?", (a1,)
    )
    assert r1["argus_alt_text"] == "a plate" and json.loads(r1["argus_keywords"]) == ["plate"]
    assert r1["argus_keeper_score"] == 0.9
    g = db.one(
        "SELECT argus_hero_asset_ids, argus_analyzed_count FROM galleries WHERE id=?", (gid,)
    )
    assert json.loads(g["argus_hero_asset_ids"]) == [a1] and g["argus_analyzed_count"] == 2


def test_apply_ignores_foreign_and_nonready_assets(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    _asset(gid, "video.mp4", kind="video")  # not a photo
    _asset(gid, "pending.jpg", status="pending")  # not ready
    res = qwen_writeback.apply_to_gallery(
        gid,
        [
            {
                "basename": "video.mp4",
                "keywords": [],
                "alt_text": None,
                "keeper_score": 0.9,
                "hero_potential": 0.9,
            },
            {
                "basename": "pending.jpg",
                "keywords": [],
                "alt_text": None,
                "keeper_score": 0.9,
                "hero_potential": 0.9,
            },
            {
                "basename": "nope.jpg",
                "keywords": [],
                "alt_text": None,
                "keeper_score": 0.9,
                "hero_potential": 0.9,
            },
        ],
    )
    assert res["matched"] == 0  # none are eligible photo+ready assets of this gallery


def test_apply_is_scoped_to_one_gallery(tmp_path, monkeypatch):
    """apply_to_gallery touches ONLY this gallery's assets. A same-basename asset in a
    DIFFERENT gallery must never be written — the `WHERE gallery_id=?` scoping is the only
    thing preventing one client's Qwen writeback from overwriting another client's signals
    (alt text, keeper/hero scores) in money/rights-adjacent metadata. The existing
    foreign-asset test only covers UNKNOWN basenames, not gallery scoping."""
    _configure_tmp_db(tmp_path, monkeypatch)
    ga = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("GalA", "A", "1"))
    gb = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("GalB", "B", "1"))
    a_asset = _asset(ga, "p1.jpg")  # same basename in both galleries
    b_asset = _asset(gb, "p1.jpg")
    res = qwen_writeback.apply_to_gallery(
        ga,
        [
            {
                "basename": "p1.jpg",
                "keywords": ["k"],
                "alt_text": "alt",
                "keeper_score": 0.9,
                "hero_potential": 0.8,
            }
        ],
    )
    assert res["matched"] == 1
    # gallery A's asset written; gallery B's same-named asset untouched
    assert db.one("SELECT argus_keeper_score FROM assets WHERE id=?", (a_asset,))[
        "argus_keeper_score"
    ] == 0.9
    assert (
        db.one("SELECT argus_keeper_score FROM assets WHERE id=?", (b_asset,))["argus_keeper_score"]
        is None
    )
    # B's gallery rollup is untouched too
    assert (
        db.one("SELECT argus_analyzed_count FROM galleries WHERE id=?", (gb,))[
            "argus_analyzed_count"
        ]
        is None
    )


def test_apply_is_idempotent(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    a1 = _asset(gid, "p1.jpg")
    photos = [
        {
            "basename": "p1.jpg",
            "keywords": ["x"],
            "alt_text": "x",
            "keeper_score": 0.7,
            "hero_potential": 0.6,
        }
    ]
    qwen_writeback.apply_to_gallery(gid, photos)
    qwen_writeback.apply_to_gallery(gid, photos)  # second run = same state
    assert (
        db.one("SELECT argus_keeper_score FROM assets WHERE id=?", (a1,))["argus_keeper_score"]
        == 0.7
    )
    assert (
        db.one("SELECT argus_analyzed_count FROM galleries WHERE id=?", (gid,))[
            "argus_analyzed_count"
        ]
        == 1
    )


def test_writeback_gallery_is_inert_until_qwen_is_eligible(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    a1 = _asset(gid, "p1.jpg")
    # default: challenger serves_production=False -> interlock refuses, writes NOTHING
    res = qwen_writeback.writeback_gallery(gid)
    assert res.get("skipped") is True
    assert (
        db.one("SELECT argus_keeper_score FROM assets WHERE id=?", (a1,))["argus_keeper_score"]
        is None
    )


def test_writeback_gallery_runs_once_qwen_is_promoted(tmp_path, monkeypatch):
    """Prove the gate OPENS: with the challenger flipped production-capable + configured +
    selected, writeback_gallery runs the (mocked) analysis and applies it."""
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "VISION_PROVIDER", "qwen")
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    monkeypatch.setattr(InternalVisionChallengerAdapter, "serves_production", True, raising=False)
    gid = _gallery()
    a1 = _asset(gid, "p1.jpg")
    monkeypatch.setattr(
        qwen_writeback,
        "_fetch_structured",
        lambda g: [
            {
                "basename": "p1.jpg",
                "keywords": ["k"],
                "alt_text": "alt",
                "keeper_score": 0.95,
                "hero_potential": 0.9,
            }
        ],
    )
    res = qwen_writeback.writeback_gallery(gid)
    assert res.get("matched") == 1 and res["hero_asset_ids"] == [a1]
    assert (
        db.one("SELECT argus_keeper_score FROM assets WHERE id=?", (a1,))["argus_keeper_score"]
        == 0.95
    )


def _promote(monkeypatch):
    """Make the interlock treat Qwen as the eligible production provider (mirrors the
    promoted-writeback test): challenger configured + flagged + production-capable."""
    monkeypatch.setattr(config, "VISION_PROVIDER", "qwen")
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    monkeypatch.setattr(InternalVisionChallengerAdapter, "serves_production", True, raising=False)


def test_readiness_lists_steps_remaining_by_default(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    r = qwen_writeback.readiness()
    assert r["ready"] is False
    keys = {c["key"]: c["ok"] for c in r["checks"]}
    # default posture: no endpoint, eval-only challenger, flag still argus -> nothing satisfied
    assert keys == {
        "endpoint": False,
        "writeback": False,
        "flag": False,
        "interlock": False,
        "gate": False,
    }
    assert r["remaining"] == 5 and r["effective"] == "argus" and r["next_step"]


def test_readiness_ready_once_promoted_and_gate_green(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    _promote(monkeypatch)
    # green gate is the human-scored half — stub the deterministic report as ready
    monkeypatch.setattr(
        validation,
        "promotion_report",
        lambda *a, **k: SimpleNamespace(ready=True, paired=20, min_paired=20),
    )
    r = qwen_writeback.readiness()
    assert r["ready"] is True and r["remaining"] == 0
    assert r["effective"] == "qwen3-vl" and r["eligible"] is True


def test_preview_is_asset_safe(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    a1 = _asset(gid, "p1.jpg")
    # endpoint unset -> a clear non-mutating error, no call attempted
    assert qwen_writeback.preview_gallery(gid)["ok"] is False
    # with an endpoint, preview parses (mocked) signals but writes NOTHING
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://mickeybot:11434/v1")
    monkeypatch.setattr(
        qwen_writeback,
        "_fetch_structured",
        lambda g: [
            {
                "basename": "p1.jpg",
                "keywords": ["k"],
                "alt_text": "alt",
                "keeper_score": 0.9,
                "hero_potential": 0.9,
            }
        ],
    )
    res = qwen_writeback.preview_gallery(gid)
    assert res["ok"] is True and res["count"] == 1 and res["photos"][0]["basename"] == "p1.jpg"
    assert (
        db.one("SELECT argus_keeper_score FROM assets WHERE id=?", (a1,))["argus_keeper_score"]
        is None
    )


def test_enqueue_writeback_refused_until_eligible(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery()
    assert qwen_writeback.enqueue_writeback(gid) is None  # interlock refuses by default
    _promote(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        jobs, "enqueue", lambda kind, payload: captured.update(kind=kind, payload=payload) or 7
    )
    assert qwen_writeback.enqueue_writeback(gid) == 7
    assert captured["kind"] == "qwen_writeback_gallery" and captured["payload"] == {
        "gallery_id": gid
    }
