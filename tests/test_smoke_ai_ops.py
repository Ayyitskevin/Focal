"""Operations: the AI operations dashboard (admin).

DB-backed (real tmp DB + admin routes), same pattern as test_smoke_offers_view.py.
Proves the one-pane view aggregates the four AI surfaces — needs-attention tiles, the
ledger summary, and the vision gate verdict — reads only (writes nothing), and is
admin-gated.
"""

import pytest
from fastapi.testclient import TestClient

from app import ai_runs, albums, config, db, jobs, validation
from app.main import app
from app.providers import Capability, ProviderResult, ResultStatus, ReviewRequirement


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
    monkeypatch.setattr(config, "VISION_CHALLENGER_MODEL", "qwen3-vl:32b")
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


def test_empty_state_renders(admin_client):
    body = admin_client.get("/admin/ai-ops").text
    assert "AI operations" in body
    assert "Not ready" in body  # vision gate with an empty validation set
    assert "No runs yet" in body  # empty ledger breakdown


def test_aggregates_pending_offer_and_album_and_ledger(admin_client):
    # one undecided 'done' offer worth $300
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("OpsG", "G", "1"))
    db.run(
        "UPDATE galleries SET plutus_last_status='done', plutus_last_estimated_cents=30000, "
        "plutus_last_at=datetime('now') WHERE id=?",
        (gid,),
    )
    # one pending album draft
    db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
        (gid, "photo", "p.jpg", "stored/p.jpg", "ready"),
    )
    albums.propose_draft(gid)
    # one OK + one error ledger row
    for status in (ResultStatus.OK, ResultStatus.PROVIDER_ERROR):
        ai_runs.record(
            ProviderResult(
                capability=Capability.VISION,
                provider="argus",
                status=status,
                review=ReviewRequirement.HUMAN_REVIEW,
                error=None if status.is_ok else "boom",
            )
        )

    body = admin_client.get("/admin/ai-ops").text
    assert "$300.00 proposed" in body  # undecided offer value tile
    assert "Album drafts to review" in body
    assert "Provider errors in ledger" in body
    # ledger by-capability breakdown shows the albums provenance + vision rows
    assert "Vision" in body and "Albums" in body


def test_vision_gate_ready_shows_through(admin_client, monkeypatch):
    # seed 1 paired item and drop the coverage threshold so the gate reads ready
    monkeypatch.setattr(config, "VALIDATION_MIN_PAIRED", 1)
    item = validation.add_item("vision", "gallery", 1)
    validation.record_score(item, "argus", "argus", 0.6)
    validation.record_score(item, "qwen", "qwen3-vl:32b", 0.9)
    assert "Ready to promote" in admin_client.get("/admin/ai-ops").text


def test_ai_ops_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/ai-ops", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/login"
        jobs.stop()
