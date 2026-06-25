"""Phase 1 integration: ai_runs provenance ledger + flag-gated caption facade wiring.

DB-backed (real tmp DB + migrations + admin routes), mocking only the outbound Odysseus
call — same pattern as test_smoke_argus.py. Proves the migration applies, ai_runs.record
writes a real row, and the caption-draft route behaves identically with the facade flag
OFF (legacy path, no provenance row) while routing through the facade + recording
provenance when ON.
"""

import pytest
from fastapi.testclient import TestClient

from app import ai_runs, caption_ai, config, db, jobs
from app.main import app
from app.providers import Capability, ProviderResult, ResultStatus, ReviewRequirement


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


def test_migration_creates_ai_runs_table(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    row = db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_runs'")
    assert row is not None


def test_record_inserts_real_row(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    pr = ProviderResult(
        capability=Capability.CONTENT,
        provider="odysseus",
        status=ResultStatus.OK,
        review=ReviewRequirement.HUMAN_REVIEW,
        output={"caption": "secret payload"},
        model="grok-x",
        latency_ms=11,
    )
    rid = ai_runs.record(pr, subject_type="retainer_caption", subject_id=5)
    row = db.one("SELECT * FROM ai_runs WHERE id=?", (rid,))
    assert row["capability"] == "content"
    assert row["provider"] == "odysseus"
    assert row["status"] == "ok"
    assert row["review"] == "human_review"
    assert row["model"] == "grok-x"
    assert row["subject_type"] == "retainer_caption" and row["subject_id"] == 5
    assert row["created_at"]


def _plan_with_caption(admin_client):
    """Create client -> project -> recurring plan via routes, then a draft caption row."""
    admin_client.post(
        "/admin/studio/clients",
        data={"name": "Retainer Co", "company": "Monthly Bites", "email": "a@b.com", "phone": ""},
        follow_redirects=False,
    )
    c = db.one("SELECT * FROM clients ORDER BY id DESC LIMIT 1")
    admin_client.post(
        f"/admin/studio/clients/{c['id']}/projects",
        data={"title": "Retainer"},
        follow_redirects=False,
    )
    proj = db.one("SELECT * FROM projects ORDER BY id DESC LIMIT 1")
    admin_client.post(
        f"/admin/studio/projects/{proj['id']}/recurring",
        data={"title": "Monthly content retainer"},
        follow_redirects=False,
    )
    plan = db.one("SELECT * FROM recurring_plans ORDER BY id DESC LIMIT 1")
    caption_id = db.run(
        "INSERT INTO retainer_captions (plan_id, period, label, body) VALUES (?,?,?,?)",
        (plan["id"], "2026-06", "Reel", ""),
    )
    return plan["id"], caption_id


def test_caption_draft_facade_off_is_legacy_with_no_provenance(admin_client, monkeypatch):
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", False)
    monkeypatch.setattr(
        caption_ai, "draft_caption", lambda ctx: {"caption": "Legacy caption.", "model": "legacy-m"}
    )
    r = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft", follow_redirects=False
    )
    assert r.status_code == 303
    cap = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    assert (
        cap["body"] == "Legacy caption."
        and cap["ai_model"] == "legacy-m"
        and cap["ai_drafted"] == 1
    )
    # legacy path records NO provenance row
    n = db.one(
        "SELECT COUNT(*) AS n FROM ai_runs WHERE subject_type='retainer_caption' AND subject_id=?",
        (caption_id,),
    )["n"]
    assert n == 0


def test_caption_draft_facade_on_records_provenance(admin_client, monkeypatch):
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", True)
    # the facade resolves to the legacy Odysseus adapter, which gates on is_enabled()
    monkeypatch.setattr(caption_ai, "is_enabled", lambda: True)
    monkeypatch.setattr(
        caption_ai,
        "draft_caption",
        lambda ctx: {"caption": "Bright plated dish.", "model": "grok-x"},
    )
    r = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft", follow_redirects=False
    )
    assert r.status_code == 303
    cap = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    assert cap["body"] == "Bright plated dish." and cap["ai_model"] == "grok-x"
    rows = db.all_(
        "SELECT * FROM ai_runs WHERE subject_type='retainer_caption' AND subject_id=?",
        (caption_id,),
    )
    assert len(rows) == 1
    assert rows[0]["capability"] == "content"
    assert rows[0]["provider"] == "odysseus"
    assert rows[0]["status"] == "ok"
    assert rows[0]["review"] == "human_review"


def test_caption_draft_facade_on_failure_is_non_mutating(admin_client, monkeypatch):
    """Facade ON but Odysseus disabled -> DISABLED result: caption untouched, but a
    provenance row still records the non-mutating failure."""
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", True)
    monkeypatch.setattr(caption_ai, "is_enabled", lambda: False)
    r = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft", follow_redirects=False
    )
    assert r.status_code == 303
    cap = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    # caption body left empty / undrafted — the failure mutated nothing
    assert (cap["body"] or "") == "" and not cap["ai_drafted"]
    row = db.one(
        "SELECT * FROM ai_runs WHERE subject_type='retainer_caption' AND subject_id=?",
        (caption_id,),
    )
    assert row["status"] == "disabled"
