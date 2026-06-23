"""Argus Phase 6 wiring — mock outbound HTTP only."""

import json
import os
import tempfile

os.environ.setdefault("MISE_DATA_DIR", tempfile.mkdtemp(prefix="mise-argus-test-"))
os.environ.setdefault("MISE_SECRET_KEY", "test-secret")
os.environ.setdefault("MISE_ADMIN_PASSWORD", "test-pw")
os.environ.setdefault("MISE_ENV_FILE", "/nonexistent")

import pytest
from fastapi.testclient import TestClient

from app import argus_analyze, config, db
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin(client):
    r = client.post("/admin/login", data={"password": os.environ["MISE_ADMIN_PASSWORD"]},
                    follow_redirects=False)
    assert r.status_code == 303
    return client


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app import ratelimit
    ratelimit._hits.clear()
    yield


def test_argus_is_enabled(monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "")
    assert argus_analyze.is_enabled() is False
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus:8010")
    assert argus_analyze.is_enabled() is False
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    assert argus_analyze.is_enabled() is True


def test_publish_enqueues_argus_job(admin, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus:8010")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)",
                 ("ArgusPub01", "Argus Pub", "1234"))

    def n_jobs():
        return db.one("""SELECT COUNT(*) AS n FROM jobs WHERE kind='argus_analyze_gallery'
                         AND json_extract(payload,'$.gallery_id')=?""", (gid,))["n"]

    r = admin.post(f"/admin/galleries/{gid}/settings",
                   data={"title": "Argus Pub", "pin": "1234", "published": "true"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert n_jobs() == 1
    admin.post(f"/admin/galleries/{gid}/settings",
               data={"title": "Argus Pub", "pin": "1234", "published": "true"},
               follow_redirects=False)
    assert n_jobs() == 1


def test_run_for_gallery_records_queued(admin, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus:8010")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    gid = db.run("INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
                 ("ArgusRun01", "Run", "1234"))

    class FakeResp:
        def read(self):
            return json.dumps({"mode": "queued", "job_id": "job-abc", "status": "queued"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(argus_analyze.urllib.request, "urlopen",
                        lambda req, timeout: FakeResp())
    argus_analyze.run_for_gallery(gid)
    row = db.one("SELECT * FROM galleries WHERE id=?", (gid,))
    assert row["argus_last_job_id"] == "job-abc"
    assert row["argus_last_status"] == "queued"
    assert row["argus_last_at"]


def test_run_for_gallery_records_sync_run(admin, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus:8010")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    gid = db.run("INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
                 ("ArgusSync01", "Sync", "1234"))

    class FakeResp:
        def read(self):
            return json.dumps({"mode": "sync", "run_id": 42, "count": 3}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(argus_analyze.urllib.request, "urlopen",
                        lambda req, timeout: FakeResp())
    argus_analyze.run_for_gallery(gid)
    row = db.one("SELECT * FROM galleries WHERE id=?", (gid,))
    assert row["argus_last_run_id"] == 42
    assert row["argus_last_status"] == "done"


def test_run_for_gallery_swallows_errors(admin, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus:8010")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    gid = db.run("INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
                 ("ArgusErr01", "Err", "1234"))

    def boom(req, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(argus_analyze.urllib.request, "urlopen", boom)
    argus_analyze.run_for_gallery(gid)  # must not raise
    row = db.one("SELECT * FROM galleries WHERE id=?", (gid,))
    assert row["argus_last_status"] == "error"
    assert "timed out" in row["argus_last_error"]


def test_manual_analyze_route(admin, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus:8010")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    gid = db.run("INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
                 ("ArgusMan01", "Manual", "1234"))
    before = db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='argus_analyze_gallery'")["n"]
    r = admin.post(f"/admin/galleries/{gid}/argus-analyze", follow_redirects=False)
    assert r.status_code == 303
    after = db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='argus_analyze_gallery'")["n"]
    assert after == before + 1


def test_galleries_api(admin):
    saved_url = config.ARGUS_URL
    saved_token = config.ARGUS_TOKEN
    try:
        config.ARGUS_URL = "http://argus:8010"
        config.ARGUS_TOKEN = ""
        r = admin.get("/api/galleries")
        assert r.status_code == 503

        config.ARGUS_TOKEN = "api-secret"
        gid = db.run("INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
                     ("ApiGal01", "API Gal", "1234"))
        headers = {"Authorization": "Bearer api-secret"}
        ok = admin.get("/api/galleries", headers=headers)
        assert ok.status_code == 200
        body = ok.json()
        ids = [g["id"] for g in body["galleries"]]
        assert gid in ids
        match = next(g for g in body["galleries"] if g["id"] == gid)
        assert match["slug"] == "ApiGal01"
        assert match["published"] is True

        bad = admin.get("/api/galleries", headers={"Authorization": "Bearer wrong"})
        assert bad.status_code == 401
    finally:
        config.ARGUS_URL = saved_url
        config.ARGUS_TOKEN = saved_token