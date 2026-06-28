"""Local keeper-scorer (app/cull_scorer.py) — populates argus_keeper_score from the local Qwen
endpoint for the cull deck. No live model calls: the endpoint (chat_completion) is monkeypatched.
Proves the reply parser validates strictly, that scoring writes per-asset (asset_id-keyed) and
skips a missing derivative without dying, that it's a no-op until armed, that one provenance row
lands in ai_runs, and that the deck's rescore trigger is gated (deck flag + scorer armed).
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, cull_scorer, db, jobs
from app.cull_scorer import CullScoreError, _parse_score, is_enabled
from app.main import app


def _configure(tmp_path, monkeypatch, *, cull_ui=True, scorer=True, url="http://local-qwen/v1"):
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
        "CULL_UI": cull_ui,
        "CULL_SCORER": scorer,
        "VISION_CHALLENGER_URL": url,
    }.items():
        monkeypatch.setattr(config, attr, val)
    (tmp_path / "media").mkdir(exist_ok=True)
    db.migrate()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app import ratelimit

    ratelimit._hits.clear()
    yield


def _fake_reply(score):
    return {"choices": [{"message": {"content": '{"keeper_score": ' + str(score) + "}"}}]}


def _photo(gid, fn, *, web_dir=None):
    aid = db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) VALUES (?,?,?,?,?)",
        (gid, "photo", fn, fn, "ready"),
    )
    if web_dir is not None:
        web_dir.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        Image.new("RGB", (16, 16), (120, 120, 120)).save(buf, "JPEG")
        (web_dir / f"{fn.rsplit('.', 1)[0]}.jpg").write_bytes(buf.getvalue())
    return aid


# --- pure parse / arming (CI unit) ------------------------------------------


@pytest.mark.unit
def test_parse_score_accepts_plain_and_wrapped():
    assert _parse_score('{"keeper_score": 0.8}') == 0.8
    assert _parse_score('```json\n{"keeper_score": 0.0}\n```') == 0.0
    assert _parse_score('Sure! {"keeper_score": 1}  hope that helps') == 1.0
    assert _parse_score({"keeper_score": 0.5}) == 0.5  # already-parsed object


@pytest.mark.unit
def test_parse_score_rejects_bad():
    for bad in [
        '{"keeper_score": 1.5}',
        '{"keeper_score": "high"}',
        "{}",
        "not json",
        '{"keeper_score": true}',
    ]:
        with pytest.raises(CullScoreError):
            _parse_score(bad)


@pytest.mark.unit
def test_is_enabled_needs_flag_and_url(monkeypatch):
    monkeypatch.setattr(config, "CULL_SCORER", True)
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://x/v1")
    assert is_enabled()
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "")
    assert not is_enabled()  # no endpoint
    monkeypatch.setattr(config, "VISION_CHALLENGER_URL", "http://x/v1")
    monkeypatch.setattr(config, "CULL_SCORER", False)
    assert not is_enabled()  # flag off


# --- score_gallery (DB, mocked endpoint) ------------------------------------


def test_score_gallery_writes_by_asset_id_and_skips_missing(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("sc", "G", "1"))
    web = tmp_path / "media" / str(gid) / "web"
    a1 = _photo(gid, "a.jpg", web_dir=web)
    a2 = _photo(gid, "b.jpg", web_dir=web)
    a3 = _photo(gid, "c.jpg")  # no web derivative — must be skipped, not fatal
    monkeypatch.setattr(cull_scorer, "chat_completion", lambda paths, prompt: _fake_reply(0.66))
    res = cull_scorer.score_gallery(gid)
    assert res == {"scored": 2, "failed": 1, "total": 3}
    assert db.one("SELECT argus_keeper_score AS s FROM assets WHERE id=?", (a1,))["s"] == 0.66
    assert db.one("SELECT argus_keeper_score AS s FROM assets WHERE id=?", (a2,))["s"] == 0.66
    assert db.one("SELECT argus_keeper_score AS s FROM assets WHERE id=?", (a3,))["s"] is None
    # one provenance row for the run
    n = db.one(
        "SELECT COUNT(*) AS n FROM ai_runs WHERE subject_type='gallery' AND subject_id=?", (gid,)
    )["n"]
    assert n == 1


def test_score_gallery_counts_bad_reply_as_failed(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("sc2", "G", "1"))
    web = tmp_path / "media" / str(gid) / "web"
    aid = _photo(gid, "a.jpg", web_dir=web)
    monkeypatch.setattr(
        cull_scorer, "chat_completion", lambda paths, prompt: _fake_reply('"not-a-number"')
    )
    res = cull_scorer.score_gallery(gid)
    assert res == {"scored": 0, "failed": 1, "total": 1}
    assert db.one("SELECT argus_keeper_score AS s FROM assets WHERE id=?", (aid,))["s"] is None


def test_score_gallery_noop_when_disabled(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, scorer=False)
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("sc3", "G", "1"))
    _photo(gid, "a.jpg", web_dir=tmp_path / "media" / str(gid) / "web")
    assert cull_scorer.score_gallery(gid)["skipped"] is True


# --- rescore trigger (admin route) ------------------------------------------


def _login(client):
    r = client.post("/admin/login", data={"password": "test-pw"}, follow_redirects=False)
    assert r.status_code == 303


def test_rescore_enqueues_when_armed(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, cull_ui=True, scorer=True)
    monkeypatch.setattr(jobs, "enqueue", lambda kind, payload: 123)  # don't actually run a job
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("rs", "G", "1"))
    with TestClient(app) as client:
        _login(client)
        r = client.post(f"/admin/galleries/{gid}/cull/rescore", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith(f"/galleries/{gid}/cull")
        jobs.stop()


def test_rescore_503_when_scorer_off(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, cull_ui=True, scorer=False)  # deck on, scorer not armed
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("rs2", "G", "1"))
    with TestClient(app) as client:
        _login(client)
        r = client.post(f"/admin/galleries/{gid}/cull/rescore", follow_redirects=False)
        assert r.status_code == 503
        jobs.stop()


def test_rescore_404_when_cull_ui_off(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, cull_ui=False, scorer=True)
    gid = db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", ("rs3", "G", "1"))
    with TestClient(app) as client:
        _login(client)
        r = client.post(f"/admin/galleries/{gid}/cull/rescore", follow_redirects=False)
        assert r.status_code == 404
        jobs.stop()
