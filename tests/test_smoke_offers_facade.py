"""Phase 3: offers (Plutus) through the providers facade + ai_runs provenance.

The flag-default check is a pure unit test (runs in the -m unit gate). The behavior
checks are DB-backed (real tmp DB + the real trigger with a mocked Plutus HTTP call),
mirroring test_smoke_plutus.py: flag ON records an OFFERS provenance row AND the usual
plutus_last_* status; flag OFF is the unchanged legacy path with no ai_runs row.
"""

import json

import pytest

from app import config, db, features, plutus_recommend


@pytest.mark.unit
def test_offers_facade_flag_default():
    from unittest.mock import patch

    with patch("app.features.config") as cfg:
        cfg.PROVIDER_FACADE_OFFERS = False
        assert features.offers_provider_facade_enabled() is False
        cfg.PROVIDER_FACADE_OFFERS = True
        assert features.offers_provider_facade_enabled() is True


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


def _arm_plutus(monkeypatch):
    monkeypatch.setattr(config, "PLUTUS_URL", "http://plutus:8030")
    monkeypatch.setattr(config, "PLUTUS_TOKEN", "secret")


def _published_gallery():
    return db.run(
        "INSERT INTO galleries (slug, title, pin, published, type) VALUES (?,?,?,1,'gallery')",
        ("OffersGal01", "Offers", "1234"),
    )


def _mock_plutus_response(monkeypatch, payload):
    class _Resp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(plutus_recommend.urllib.request, "urlopen", lambda req, timeout: _Resp())


_PAYLOAD = {
    "run_id": 12,
    "bundles": [{"a": 1}, {"b": 2}],
    "review_url": "https://plutus.test/runs/12",
    "pitch_url": "https://plutus.test/runs/12/pitch",
    "estimated_total_cents": 12500,
}


def test_facade_on_records_provenance_and_plutus_last(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    _arm_plutus(monkeypatch)
    monkeypatch.setattr(config, "PROVIDER_FACADE_OFFERS", True)
    gid = _published_gallery()
    _mock_plutus_response(monkeypatch, _PAYLOAD)

    plutus_recommend.run_for_gallery(gid)

    # legacy status columns recorded exactly as the legacy path would
    row = db.one(
        """SELECT plutus_last_run_id, plutus_last_status, plutus_last_offer_url,
                  plutus_last_pitch_url, plutus_last_bundle_count, plutus_last_estimated_cents
           FROM galleries WHERE id=?""",
        (gid,),
    )
    assert row["plutus_last_run_id"] == 12 and row["plutus_last_status"] == "done"
    assert row["plutus_last_offer_url"] == _PAYLOAD["review_url"]
    assert row["plutus_last_bundle_count"] == 2 and row["plutus_last_estimated_cents"] == 12500

    # PLUS one OFFERS provenance row in the ledger
    runs = db.all_("SELECT * FROM ai_runs WHERE subject_type='gallery' AND subject_id=?", (gid,))
    assert len(runs) == 1
    assert runs[0]["capability"] == "offers" and runs[0]["provider"] == "plutus"
    assert runs[0]["status"] == "ok" and runs[0]["review"] == "human_review"


def test_facade_off_is_legacy_with_no_provenance(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    _arm_plutus(monkeypatch)
    monkeypatch.setattr(config, "PROVIDER_FACADE_OFFERS", False)
    gid = _published_gallery()
    _mock_plutus_response(monkeypatch, _PAYLOAD)

    plutus_recommend.run_for_gallery(gid)

    row = db.one("SELECT plutus_last_run_id, plutus_last_status FROM galleries WHERE id=?", (gid,))
    assert row["plutus_last_run_id"] == 12 and row["plutus_last_status"] == "done"
    n = db.one("SELECT COUNT(*) AS n FROM ai_runs WHERE subject_id=?", (gid,))["n"]
    assert n == 0


def test_facade_on_provider_error_records_error_row(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    _arm_plutus(monkeypatch)
    monkeypatch.setattr(config, "PROVIDER_FACADE_OFFERS", True)
    gid = _published_gallery()

    def boom(req, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(plutus_recommend.urllib.request, "urlopen", boom)
    plutus_recommend.run_for_gallery(gid)

    row = db.one("SELECT plutus_last_status FROM galleries WHERE id=?", (gid,))
    assert row["plutus_last_status"] == "error"
    run = db.one("SELECT * FROM ai_runs WHERE subject_id=?", (gid,))
    assert run["capability"] == "offers" and run["status"] == "provider_error"
