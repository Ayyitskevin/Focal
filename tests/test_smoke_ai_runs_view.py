"""Operations: the read-only ai_runs operator view (admin).

DB-backed (real tmp DB + admin routes), same pattern as test_smoke_argus.py. Proves the
ledger page renders, filters by capability, surfaces a non-OK run with its error, exports
CSV, and is gated behind admin auth.
"""

import pytest
from fastapi.testclient import TestClient

from app import ai_runs, config, db, jobs
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


def _seed_runs():
    ai_runs.record(
        ProviderResult(
            capability=Capability.CONTENT,
            provider="odysseus",
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={"caption": "x"},
            model="grok-x",
            latency_ms=120,
        ),
        subject_type="retainer_caption",
        subject_id=5,
    )
    ai_runs.record(
        ProviderResult.failure(
            Capability.VISION,
            "argus",
            ResultStatus.PROVIDER_ERROR,
            "Argus unreachable",
            latency_ms=9,
        ),
        subject_type="gallery",
        subject_id=8,
    )


def test_ai_runs_view_renders_with_status_and_error(admin_client):
    _seed_runs()
    body = admin_client.get("/admin/ai-runs").text
    assert "AI runs" in body
    assert "Content · odysseus" in body
    assert "Vision · argus" in body
    assert "grok-x" in body
    # a non-OK run surfaces its status badge + error text (not buried)
    assert "Provider error" in body
    assert "Argus unreachable" in body


def test_ai_runs_view_capability_filter(admin_client):
    _seed_runs()
    vision_only = admin_client.get("/admin/ai-runs?cap=vision").text
    assert "Vision · argus" in vision_only
    assert "Content · odysseus" not in vision_only


def test_shadow_pair_renders_as_grouped_comparison(admin_client):
    corr = "shadow:gallery:5:42"
    for provider, model, latency in (("argus", "argus", 100), ("qwen3-vl", "qwen3-vl:32b", 40)):
        ai_runs.record(
            ProviderResult(
                capability=Capability.VISION,
                provider=provider,
                status=ResultStatus.OK,
                review=ReviewRequirement.HUMAN_REVIEW,
                output={"run_id": 42},
                model=model,
                latency_ms=latency,
                cost_usd=0.0,
            ),
            subject_type="gallery",
            subject_id=5,
            correlation_id=corr,
        )
    body = admin_client.get("/admin/ai-runs").text
    assert "Shadow comparison" in body
    assert "Vision · argus" in body and "Vision · qwen3-vl" in body


def test_ai_runs_csv_export(admin_client):
    _seed_runs()
    r = admin_client.get("/admin/ai-runs.csv")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "Capability,Provider,Status" in r.text
    assert "provider_error" in r.text


def test_ai_runs_empty_state(admin_client):
    body = admin_client.get("/admin/ai-runs").text
    assert "No AI runs" in body


def test_ai_runs_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/ai-runs", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/login"
        jobs.stop()
