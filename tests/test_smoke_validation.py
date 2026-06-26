"""Validation-scoring harness — DB-backed smoke (migration 067 + persistence + report).

Applies the real migrations, then proves: the tables exist, add_item is idempotent,
record_score upserts on (item, model), and promotion_report joins human scores + the
ai_runs cost/latency ledger into the deterministic verdict end to end.
"""

import pytest

from app import ai_runs, config, db, validation
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


def _ok_run(model, *, latency, cost, provider):
    return ProviderResult(
        capability=Capability.VISION,
        provider=provider,
        status=ResultStatus.OK,
        review=ReviewRequirement.HUMAN_REVIEW,
        output={"x": 1},
        model=model,
        latency_ms=latency,
        cost_usd=cost,
    )


def test_migration_creates_validation_tables(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    names = {
        r["name"]
        for r in db.all_(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'validation%'"
        )
    }
    assert {"validation_items", "validation_scores"} <= names


def test_add_item_is_idempotent(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    a = validation.add_item("vision", "gallery", 1, label="Wedding A")
    b = validation.add_item("vision", "gallery", 1, label="Wedding A (again)")
    assert a == b  # UNIQUE(capability, subject_type, subject_id) -> same row
    assert db.one("SELECT COUNT(*) AS n FROM validation_items")["n"] == 1


def test_record_score_upserts_on_item_and_model(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    item = validation.add_item("vision", "gallery", 1)
    validation.record_score(item, "qwen", "qwen3-vl:32b", 0.6, scored_by="kevin")
    validation.record_score(item, "qwen", "qwen3-vl:32b", 0.9, scored_by="kevin")
    rows = db.all_(
        "SELECT score FROM validation_scores WHERE item_id=? AND model=?", (item, "qwen3-vl:32b")
    )
    assert len(rows) == 1 and rows[0]["score"] == 0.9  # re-scored in place, not doubled


def test_promotion_report_end_to_end(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "VALIDATION_MIN_PAIRED", 2)
    monkeypatch.setattr(config, "VALIDATION_PARITY_MARGIN", 0.0)

    items = [validation.add_item("vision", "gallery", g) for g in (10, 11, 12)]
    # challenger >= baseline on all three paired items
    for it, b, c in zip(items, (0.6, 0.7, 0.8), (0.7, 0.7, 0.9)):
        validation.record_score(it, "argus", "argus", b)
        validation.record_score(it, "qwen", "qwen3-vl:32b", c)

    # cost/latency from the ai_runs ledger
    ai_runs.record(_ok_run("argus", latency=800, cost=0.01, provider="argus"))
    ai_runs.record(_ok_run("qwen3-vl:32b", latency=1500, cost=0.0, provider="qwen"))

    rep = validation.promotion_report("vision", "argus", "qwen3-vl:32b")
    assert rep.paired == 3 and rep.total_items == 3
    assert rep.baseline.mean_score == pytest.approx(0.7) and rep.challenger.mean_score > 0.7
    assert rep.baseline.avg_latency_ms == 800 and rep.challenger.avg_cost_usd == 0.0
    assert rep.ready is True


def test_promotion_report_not_ready_when_unscored(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "VALIDATION_MIN_PAIRED", 2)
    validation.add_item("vision", "gallery", 10)
    validation.add_item("vision", "gallery", 11)
    rep = validation.promotion_report("vision", "argus", "qwen3-vl:32b")
    assert rep.total_items == 2 and rep.paired == 0 and rep.ready is False


def test_inactive_items_excluded_from_report(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "VALIDATION_MIN_PAIRED", 1)
    item = validation.add_item("vision", "gallery", 10)
    validation.record_score(item, "argus", "argus", 0.5)
    validation.record_score(item, "qwen", "qwen3-vl:32b", 0.9)
    db.run("UPDATE validation_items SET active=0 WHERE id=?", (item,))
    rep = validation.promotion_report("vision", "argus", "qwen3-vl:32b")
    assert rep.total_items == 0 and rep.paired == 0 and rep.ready is False


def test_scores_map_groups_by_item_and_model(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    a = validation.add_item("vision", "gallery", 10)
    b = validation.add_item("vision", "gallery", 11)
    validation.record_score(a, "argus", "argus", 0.5)
    validation.record_score(a, "qwen", "qwen3-vl:32b", 0.8)
    validation.record_score(b, "argus", "argus", 0.6)
    smap = validation.scores_map("vision")
    assert smap[a] == {"argus": 0.5, "qwen3-vl:32b": 0.8}
    assert smap[b] == {"argus": 0.6}


def test_deactivate_item_drops_it_from_listing(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    item = validation.add_item("vision", "gallery", 10)
    validation.deactivate_item(item)
    assert validation.list_items("vision") == []
    # the row (and any scores) survive for the record — only active flips
    assert db.one("SELECT active FROM validation_items WHERE id=?", (item,))["active"] == 0


def _gallery(slug):
    return db.run("INSERT INTO galleries (slug, title, pin) VALUES (?,?,?)", (slug, slug, "1"))


def _shadow_pair(gid):
    corr = f"shadow:gallery:{gid}:1"
    ai_runs.record(
        _ok_run("argus", latency=1, cost=0.0, provider="argus"),
        subject_type="gallery",
        subject_id=gid,
        correlation_id=corr,
    )
    ai_runs.record(
        _ok_run("qwen3-vl:32b", latency=1, cost=0.0, provider="qwen"),
        subject_type="gallery",
        subject_id=gid,
        correlation_id=corr,
    )


def test_shadow_candidates_lists_unenrolled_shadowed_galleries(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    g1, g2 = _gallery("Shadowed"), _gallery("Enrolled")
    _shadow_pair(g1)
    _shadow_pair(g2)
    validation.add_item("vision", "gallery", g2)  # g2 already in the set

    cands = validation.shadow_candidates("vision")
    ids = [c["gallery_id"] for c in cands]
    assert g1 in ids and g2 not in ids  # enrolled gallery is excluded
    assert next(c for c in cands if c["gallery_id"] == g1)["runs"] == 2


def test_shadow_candidates_ignores_non_shadow_vision_runs(tmp_path, monkeypatch):
    _configure_tmp_db(tmp_path, monkeypatch)
    gid = _gallery("PlainVision")
    # a vision run with no shadow correlation id is not a shadow comparison
    ai_runs.record(
        _ok_run("argus", latency=1, cost=0.0, provider="argus"),
        subject_type="gallery",
        subject_id=gid,
    )
    assert validation.shadow_candidates("vision") == []
