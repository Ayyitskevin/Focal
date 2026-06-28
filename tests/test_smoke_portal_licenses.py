"""Client-facing licence summary on the portal (public, PIN-gated). DB-backed. Proves an ACTIVE
licence the client holds renders as structured usage rights (channels/territory/term), that the
fee is never shown, that draft/expired licences don't surface, and that it's behind the portal PIN.
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
def client(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as c:
        yield c
    jobs.stop()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app import ratelimit

    ratelimit._hits.clear()
    yield


def _portal_with_license(*, status="active", **lic):
    cid = db.run("INSERT INTO clients (name, company) VALUES (?,?)", ("Dana", "Blue Plate Co"))
    db.run(
        "INSERT INTO portals (client_id, slug, pin, published) VALUES (?,?,?,1)",
        (cid, "blueplate", "1234"),
    )
    cols = {
        "holder_client_id": cid,
        "title": "Spring menu — social",
        "scope": "All plated dishes",
        "usage_tier": "extended",
        "exclusivity": "non_exclusive",
        "territory": '["US"]',
        "channels": '["organic_social"]',
        "status": status,
        "starts_on": "2026-01-01",
        "ends_on": "2026-12-31",
        "perpetual": 0,
        "fee_cents": 120000,
        "coverage_scope": "holder_only",
    }
    cols.update(lic)
    keys = list(cols)
    ph = ",".join("?" * len(keys))
    db.run(f"INSERT INTO licenses ({','.join(keys)}) VALUES ({ph})", tuple(cols.values()))
    return cid


def _unlock(client) -> None:
    r = client.post("/portal/blueplate/pin", data={"pin": "1234"}, follow_redirects=False)
    assert r.status_code == 303


def test_active_license_shows_structured_rights(client):
    _portal_with_license()
    _unlock(client)
    body = client.get("/portal/blueplate").text
    assert "Your usage rights" in body
    assert "Spring menu — social" in body
    assert "Extended" in body
    assert "Organic Social" in body  # humanized channel slug
    assert "2026-01-01" in body and "2026-12-31" in body  # term


def test_fee_is_never_shown_to_client(client):
    _portal_with_license(fee_cents=120000)
    _unlock(client)
    body = client.get("/portal/blueplate").text
    assert "1,200" not in body and "1200" not in body  # the $1,200 licensing fee never leaks


def test_draft_license_is_not_shown(client):
    _portal_with_license(status="draft")
    _unlock(client)
    body = client.get("/portal/blueplate").text
    assert "Your usage rights" not in body
    assert "Spring menu" not in body


def test_expired_license_is_not_shown(client):
    _portal_with_license(status="expired")
    _unlock(client)
    body = client.get("/portal/blueplate").text
    assert "Spring menu" not in body


def test_licenses_gated_behind_pin(client):
    _portal_with_license()
    # no PIN unlock → the portal shows the PIN prompt, not the licence content
    body = client.get("/portal/blueplate").text
    assert "Spring menu" not in body
