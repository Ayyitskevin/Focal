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


# ── scoring-entry write routes ───────────────────────────────────────────────────


def test_add_item_via_form(admin_client):
    r = admin_client.post(
        "/admin/validation/items",
        data={
            "subject_type": "gallery",
            "subject_id": "42",
            "label": "Spring A",
            "expected": "warm",
        },
    )
    assert r.status_code == 200  # followed the 303 back to the page
    assert "Spring A" in r.text and "Added to the validation set" in r.text
    assert validation.list_items("vision")[0]["subject_id"] == 42


def test_add_item_rejects_non_numeric_subject(admin_client):
    r = admin_client.post(
        "/admin/validation/items", data={"subject_type": "gallery", "subject_id": "nope"}
    )
    assert "numeric subject id is required" in r.text
    assert validation.list_items("vision") == []


def test_record_scores_via_form_drives_verdict(admin_client):
    # the fixture sets MIN_PAIRED=2, so score two items to satisfy the gate's coverage bar.
    for g, b, c in ((10, 0.6, 0.9), (11, 0.7, 0.95)):
        it = validation.add_item("vision", "gallery", g, label=f"Case {g}")
        r = admin_client.post(
            f"/admin/validation/items/{it}/scores",
            data={"baseline_score": str(b), "challenger_score": str(c)},
        )
        assert "Score saved" in r.text
    body = admin_client.get("/admin/validation").text
    assert "Ready to promote" in body  # both paired, challenger better, min_paired=2 met


def test_record_scores_one_side_only(admin_client):
    it = validation.add_item("vision", "gallery", 10)
    r = admin_client.post(
        f"/admin/validation/items/{it}/scores",
        data={"baseline_score": "0.5", "challenger_score": ""},
    )
    assert "Score saved" in r.text
    smap = validation.scores_map("vision")
    assert smap[it] == {"argus": 0.5}  # only baseline recorded


def test_record_scores_rejects_out_of_range_and_stores_nothing(admin_client):
    it = validation.add_item("vision", "gallery", 10)
    r = admin_client.post(
        f"/admin/validation/items/{it}/scores",
        data={"baseline_score": "1.5", "challenger_score": "0.3"},
    )
    assert "between 0.00 and 1.00" in r.text
    # the whole submission is rejected — baseline out of range, so nothing is stored
    assert validation.scores_map("vision").get(it) is None


def test_deactivate_item_drops_it_from_the_set(admin_client):
    it = validation.add_item("vision", "gallery", 10, label="Doomed")
    r = admin_client.post(f"/admin/validation/items/{it}/deactivate")
    assert "Removed from the validation set" in r.text
    assert validation.list_items("vision") == []


def test_write_routes_require_admin(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    with TestClient(app) as anon:
        r = anon.post("/admin/validation/items", data={"subject_id": "1"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        jobs.stop()


# ── shadow→validation bridge ─────────────────────────────────────────────────────


def _shadow_pair(gid):
    """Two ai_runs rows (legacy + challenger) sharing a shadow correlation id, as the
    vision shadow runner records them."""
    for model, prov in (("argus", "argus"), ("qwen3-vl:32b", "qwen")):
        db.run(
            """INSERT INTO ai_runs (capability, provider, status, review, model,
                                    subject_type, subject_id, correlation_id)
               VALUES ('vision', ?, 'ok', 'human_review', ?, 'gallery', ?, ?)""",
            (prov, model, gid, f"shadow:gallery:{gid}:1"),
        )


def test_shadow_candidate_appears_then_enrolls(admin_client):
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)",
        ("ShadowG", "Shadow Wedding", "1"),
    )
    _shadow_pair(gid)
    body = admin_client.get("/admin/validation").text
    assert "From vision shadow runs" in body and "Shadow Wedding" in body

    # one-click enrol uses the existing add-item route (as the button does)
    admin_client.post(
        "/admin/validation/items",
        data={"subject_type": "gallery", "subject_id": str(gid), "label": "Shadow Wedding"},
    )
    assert validation.list_items("vision")[0]["subject_id"] == gid
    # now enrolled -> no longer a candidate
    assert validation.shadow_candidates("vision") == []
    assert "From vision shadow runs" not in admin_client.get("/admin/validation").text


def test_no_shadow_candidates_section_when_none(admin_client):
    assert "From vision shadow runs" not in admin_client.get("/admin/validation").text


def test_promotion_status_shows_argus_as_production_provider(admin_client):
    # the cutover seam surfaces the effective production provider; default is argus
    assert "Production provider:" in admin_client.get("/admin/validation").text
    assert "argus" in admin_client.get("/admin/validation").text
