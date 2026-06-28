"""Plutus upsell integration smoke tests."""

from __future__ import annotations

import json

from app import config, db, jobs, plutus_recommend


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


def test_plutus_is_enabled(monkeypatch):
    monkeypatch.setattr(config, "PLUTUS_URL", "")
    monkeypatch.setattr(config, "PLUTUS_TOKEN", "")
    assert plutus_recommend.is_enabled() is False
    monkeypatch.setattr(config, "PLUTUS_URL", "http://plutus:8030")
    assert plutus_recommend.is_enabled() is False
    monkeypatch.setattr(config, "PLUTUS_TOKEN", "secret")
    assert plutus_recommend.is_enabled() is True


def test_run_for_gallery_records_done(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "PLUTUS_URL", "http://plutus:8030")
    monkeypatch.setattr(config, "PLUTUS_TOKEN", "secret")
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, type, published) VALUES (?,?,?,?,1)",
        ("abc", "Test", "1234", "gallery"),
    )
    payload = {
        "run_id": 12,
        "bundles": [{"id": "wall-hero"}, {"id": "album"}],
        "bundle_count": 2,
        "estimated_total_cents": 12500,
        "review_url": "https://plutus.test/runs/12",
        "pitch_url": "https://plutus.test/runs/12/pitch.txt",
    }

    class _Resp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(plutus_recommend.urllib.request, "urlopen", lambda req, timeout: _Resp())
    plutus_recommend.run_for_gallery(gid)
    row = db.one(
        """SELECT plutus_last_run_id, plutus_last_status, plutus_last_offer_url,
                  plutus_last_pitch_url, plutus_last_bundle_count, plutus_last_estimated_cents
           FROM galleries WHERE id=?""",
        (gid,),
    )
    assert row["plutus_last_run_id"] == 12
    assert row["plutus_last_status"] == "done"
    assert row["plutus_last_offer_url"] == payload["review_url"]
    assert row["plutus_last_pitch_url"] == payload["pitch_url"]
    assert row["plutus_last_bundle_count"] == 2
    assert row["plutus_last_estimated_cents"] == 12500


def test_apply_callback_records_review_url(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, type, published) VALUES (?,?,?,?,1)",
        ("abc", "Test", "1234", "gallery"),
    )
    plutus_recommend.apply_callback(
        gid,
        {
            "status": "done",
            "run_id": 9,
            "review_url": "https://plutus.test/runs/9",
            "pitch_url": "https://plutus.test/runs/9/pitch.txt",
        },
    )
    row = db.one("SELECT plutus_last_offer_url FROM galleries WHERE id=?", (gid,))
    assert row["plutus_last_offer_url"] == "https://plutus.test/runs/9"


def test_parse_bundles_normalizes_valid_with_sku_and_line_items():
    payload = {
        "bundles": [
            {
                "sku": " WALL-HERO ",
                "label": " Wall hero ",
                "estimated_cents": 12000,
                "line_items": [{"label": " 16x24 print ", "qty": 1, "unit_cents": 12000}],
            },
            {"sku": "ALBUM", "label": "Album", "estimated_cents": 30000},
        ]
    }
    assert plutus_recommend.parse_bundles(payload) == [
        {
            "sku": "WALL-HERO",
            "label": "Wall hero",
            "estimated_cents": 12000,
            "line_items": [{"label": "16x24 print", "qty": 1, "unit_cents": 12000}],
        },
        {"sku": "ALBUM", "label": "Album", "estimated_cents": 30000},
    ]


def test_parse_bundles_keeps_sku_none_when_absent():
    # pre-PLUTUS-#1: bundles arrive in the offers shape but without a SKU yet -> persisted with
    # sku=None (dormant linkage), never rejected for the missing key.
    out = plutus_recommend.parse_bundles(
        {"bundles": [{"label": "Album", "estimated_cents": 30000}]}
    )
    assert out == [{"sku": None, "label": "Album", "estimated_cents": 30000}]


def test_parse_bundles_returns_none_on_malformed_or_empty():
    assert plutus_recommend.parse_bundles({}) is None  # no bundles key
    assert plutus_recommend.parse_bundles(None) is None  # not a dict
    assert plutus_recommend.parse_bundles({"bundles": []}) is None  # empty
    assert plutus_recommend.parse_bundles({"bundles": "nope"}) is None  # not a list
    # legacy-shaped bundles (no label / estimated_cents) -> nothing valid to store
    assert plutus_recommend.parse_bundles({"bundles": [{"id": "wall-hero"}]}) is None
    # any malformed bundle rejects the WHOLE list (all-or-nothing gate)
    assert (
        plutus_recommend.parse_bundles(
            {
                "bundles": [
                    {"label": "ok", "estimated_cents": 100},
                    {"label": "", "estimated_cents": 5},
                ]
            }
        )
        is None
    )
    # estimated_cents must be a non-negative int (bool rejected)
    assert (
        plutus_recommend.parse_bundles({"bundles": [{"label": "x", "estimated_cents": -1}]}) is None
    )
    assert (
        plutus_recommend.parse_bundles({"bundles": [{"label": "x", "estimated_cents": True}]})
        is None
    )
    # a malformed line item (qty < 1) rejects the bundle
    assert (
        plutus_recommend.parse_bundles(
            {
                "bundles": [
                    {
                        "label": "x",
                        "estimated_cents": 100,
                        "line_items": [{"label": "li", "qty": 0, "unit_cents": 5}],
                    }
                ]
            }
        )
        is None
    )


def test_apply_callback_persists_validated_bundles(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, type, published) VALUES (?,?,?,?,1)",
        ("abc", "Test", "1234", "gallery"),
    )
    plutus_recommend.apply_callback(
        gid,
        {
            "status": "done",
            "run_id": 9,
            "estimated_total_cents": 42000,
            "bundles": [
                {"sku": "ALBUM", "label": "Album", "estimated_cents": 30000},
                {"sku": "WALL", "label": "Wall", "estimated_cents": 12000},
            ],
        },
    )
    row = db.one(
        """SELECT plutus_last_bundle_count, plutus_last_estimated_cents, plutus_last_bundles
           FROM galleries WHERE id=?""",
        (gid,),
    )
    assert row["plutus_last_bundle_count"] == 2 and row["plutus_last_estimated_cents"] == 42000
    stored = json.loads(row["plutus_last_bundles"])
    assert [b["sku"] for b in stored] == ["ALBUM", "WALL"]
    assert stored[0] == {"sku": "ALBUM", "label": "Album", "estimated_cents": 30000}


def test_apply_callback_stores_null_bundles_when_malformed_but_keeps_summary(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, type, published) VALUES (?,?,?,?,1)",
        ("abc", "Test", "1234", "gallery"),
    )
    # legacy-shaped bundles -> bundles column NULL, but the summary columns still record
    plutus_recommend.apply_callback(
        gid,
        {
            "status": "done",
            "run_id": 7,
            "estimated_total_cents": 5000,
            "bundles": [{"id": "wall-hero"}, {"id": "album"}],
        },
    )
    row = db.one(
        """SELECT plutus_last_status, plutus_last_bundle_count, plutus_last_estimated_cents,
                  plutus_last_bundles FROM galleries WHERE id=?""",
        (gid,),
    )
    assert row["plutus_last_status"] == "done"
    assert row["plutus_last_bundle_count"] == 2  # summary from _bundle_meta is unaffected
    assert row["plutus_last_estimated_cents"] == 5000
    assert row["plutus_last_bundles"] is None  # nothing valid to persist


def test_apply_callback_bundles_idempotent_overwrite(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, type, published) VALUES (?,?,?,?,1)",
        ("abc", "Test", "1234", "gallery"),
    )
    payload = {
        "status": "done",
        "run_id": 9,
        "estimated_total_cents": 30000,
        "bundles": [{"sku": "ALBUM", "label": "Album", "estimated_cents": 30000}],
    }
    plutus_recommend.apply_callback(gid, payload)
    plutus_recommend.apply_callback(gid, payload)  # re-delivery overwrites, never duplicates
    stored = json.loads(
        db.one("SELECT plutus_last_bundles FROM galleries WHERE id=?", (gid,))[
            "plutus_last_bundles"
        ]
    )
    assert len(stored) == 1 and stored[0]["sku"] == "ALBUM"


def test_argus_callback_enqueues_plutus(tmp_path, monkeypatch):
    from app import argus_analyze

    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "PLUTUS_URL", "http://plutus:8030")
    monkeypatch.setattr(config, "PLUTUS_TOKEN", "secret")
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, type, published) VALUES (?,?,?,?,1)",
        ("abc", "Test", "1234", "gallery"),
    )
    enqueued: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        jobs,
        "enqueue",
        lambda kind, payload: enqueued.append((kind, payload)) or 1,
    )
    argus_analyze.apply_callback(gid, {"status": "done", "run_id": 5})
    assert ("plutus_recommend_gallery", {"gallery_id": gid}) in enqueued
