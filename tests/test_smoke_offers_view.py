"""Operations: the read-only offers review queue (admin).

DB-backed (real tmp DB + admin routes), same pattern as test_smoke_ai_runs_view.py.
Proves the queue renders offers with status/value/links, filters by status, totals the
ready pipeline, exports CSV, shows an empty state, and is gated behind admin auth.
"""

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs
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


def _seed_offers():
    done = db.run(
        "INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
        ("OffDone01", "Spring Tasting", "1234"),
    )
    db.run(
        """UPDATE galleries SET plutus_last_status='done', plutus_last_bundle_count=3,
              plutus_last_estimated_cents=30000, plutus_last_offer_url='https://plutus.test/runs/1',
              plutus_last_pitch_url='https://plutus.test/runs/1/pitch',
              plutus_last_at=datetime('now') WHERE id=?""",
        (done,),
    )
    err = db.run(
        "INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
        ("OffErr01", "Gala Night", "1234"),
    )
    db.run(
        """UPDATE galleries SET plutus_last_status='error', plutus_last_error='Plutus 401',
              plutus_last_at=datetime('now') WHERE id=?""",
        (err,),
    )
    return done, err


def test_offers_view_renders_with_value_and_links(admin_client):
    _seed_offers()
    body = admin_client.get("/admin/offers").text
    assert "Offers" in body
    assert "Spring Tasting" in body and "Gala Night" in body
    assert "Ready" in body and "Error" in body
    assert "$300.00" in body  # estimated value + pipeline total
    assert "https://plutus.test/runs/1" in body  # review-offer click-through
    assert "Plutus 401" in body  # error surfaced, not buried


def test_offers_filter_done_only(admin_client):
    _seed_offers()
    done_only = admin_client.get("/admin/offers?status=done").text
    assert "Spring Tasting" in done_only
    assert "Gala Night" not in done_only


def test_offers_pipeline_total_counts_only_ready(admin_client):
    _seed_offers()
    # a second ready offer → pipeline total should sum to $500.00 (error offer excluded)
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
        ("OffDone02", "Brunch Set", "1234"),
    )
    db.run(
        "UPDATE galleries SET plutus_last_status='done', plutus_last_estimated_cents=20000, "
        "plutus_last_at=datetime('now') WHERE id=?",
        (gid,),
    )
    assert "$500.00" in admin_client.get("/admin/offers").text


def test_offers_csv_export(admin_client):
    _seed_offers()
    r = admin_client.get("/admin/offers.csv")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "Gallery,Client,Status" in r.text
    assert "Spring Tasting" in r.text


def test_offers_empty_state(admin_client):
    assert "No offers" in admin_client.get("/admin/offers").text


def test_offers_requires_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.get("/admin/offers", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/login"
        jobs.stop()
