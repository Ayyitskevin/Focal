"""Operations: the read-only validation gate view (admin).

DB-backed (real tmp DB + admin routes), same pattern as test_smoke_offers_view.py.
Proves the page renders the verdict + per-model means, surfaces the readiness state, shows
the validation set, exports CSV, has an empty state, and is gated behind admin auth.
"""

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs, validation
from app.main import app


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
    monkeypatch.setattr(config, "VALIDATION_MIN_PAIRED", 2)
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


def _seed_ready():
    for g, b, c in ((10, 0.6, 0.8), (11, 0.7, 0.9)):
        it = validation.add_item("vision", "gallery", g, label=f"Case {g}")
        validation.record_score(it, "argus", "argus", b)
        validation.record_score(it, "qwen", "qwen3-vl:32b", c)


def test_view_renders_verdict_and_models(admin_client):
    _seed_ready()
    body = admin_client.get("/admin/validation").text
    assert "Validation gate" in body
    assert "qwen3-vl:32b" in body and "argus" in body
    assert "Ready to promote" in body  # challenger better on 2 paired >= min_paired 2
    assert "Case 10" in body and "Case 11" in body


def test_view_not_ready_state(admin_client):
    # only baseline scored -> no paired evidence -> not ready
    it = validation.add_item("vision", "gallery", 10, label="Case 10")
    validation.record_score(it, "argus", "argus", 0.9)
    body = admin_client.get("/admin/validation").text
    assert "Not ready" in body


def test_view_empty_state(admin_client):
    body = admin_client.get("/admin/validation").text
    assert "validation set is empty" in body
    assert "Not ready" in body


def test_validation_csv_export(admin_client):
    _seed_ready()
    r = admin_client.get("/admin/validation.csv")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "Model,Score" in r.text
    assert "qwen3-vl:32b" in r.text


def test_validation_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/validation", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/login"
        jobs.stop()
