"""Phase 1 integration: ai_runs provenance ledger + flag-gated caption facade wiring.

DB-backed (real tmp DB + migrations + admin routes), mocking only the outbound Odysseus
call — same pattern as test_smoke_argus.py. Proves the migration applies, ai_runs.record
writes a real row, and the caption-draft route behaves identically with the facade flag
OFF (legacy path, no provenance row) while routing through the facade + recording
provenance when ON.
"""

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ai_runs, caption_ai, config, db, jobs, providers, saas
from app.admin import recurring
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
        caption_ai,
        "draft_caption",
        lambda ctx, *, idempotency_key=None: {
            "caption": "Legacy caption.",
            "model": "legacy-m",
        },
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


def test_caption_draft_facade_on_records_provenance(admin_client, monkeypatch, caplog):
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", True)
    # the facade resolves to the legacy Odysseus adapter, which gates on is_enabled()
    monkeypatch.setattr(caption_ai, "is_enabled", lambda: True)
    monkeypatch.setattr(
        caption_ai,
        "draft_caption",
        lambda ctx, *, idempotency_key=None: {
            "caption": "Bright plated dish.",
            "model": "grok-x",
        },
    )
    with caplog.at_level("INFO", logger="mise.admin.recurring"):
        r = admin_client.post(
            f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft",
            follow_redirects=False,
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
    assert "grok-x" not in caplog.text


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
    assert cap["ai_claim_token"] is None and cap["ai_claimed_at"] is None


def test_approved_caption_is_rejected_before_provider_call(admin_client, monkeypatch):
    plan_id, caption_id = _plan_with_caption(admin_client)
    db.run(
        """UPDATE retainer_captions
              SET status='approved', body='Human-approved copy',
                  revision=revision+1, updated_at=datetime('now')
            WHERE id=?""",
        (caption_id,),
    )
    calls = []
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", False)
    monkeypatch.setattr(
        caption_ai,
        "draft_caption",
        lambda context, *, idempotency_key=None: (
            calls.append(context) or {"caption": "Must not be used", "model": "unexpected"}
        ),
    )

    response = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == []
    row = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    assert row["status"] == "approved"
    assert row["body"] == "Human-approved copy"
    assert not row["ai_drafted"]


def test_caption_draft_cannot_clobber_concurrent_human_edit(admin_client, monkeypatch):
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", False)

    def draft_after_edit(_context, *, idempotency_key=None):
        db.run(
            """UPDATE retainer_captions
                  SET body='Human edit won', revision=revision+1,
                      updated_at=datetime('now')
                WHERE id=?""",
            (caption_id,),
        )
        return {"caption": "Late AI output", "model": "test-model"}

    monkeypatch.setattr(caption_ai, "draft_caption", draft_after_edit)
    response = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "caption_error=" in response.headers["location"]
    row = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    assert row["body"] == "Human edit won"
    assert not row["ai_drafted"]
    assert (
        db.one(
            """SELECT COUNT(*) AS n FROM audit_log
                WHERE action='caption_ai_drafted'
                  AND entity_type='recurring_plan' AND entity_id=?""",
            (plan_id,),
        )["n"]
        == 0
    )


def test_concurrent_web_generation_claim_allows_exactly_one_provider_call(
    admin_client,
    monkeypatch,
):
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", False)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocking_provider(_context, *, idempotency_key=None):
        calls.append("called")
        entered.set()
        assert release.wait(timeout=5)
        return {"caption": "Single paid result", "model": "test-model"}

    monkeypatch.setattr(caption_ai, "draft_caption", blocking_provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            lambda: asyncio.run(recurring.draft_caption(plan_id, caption_id, replace="1"))
        )
        assert entered.wait(timeout=5)
        second = pool.submit(
            lambda: asyncio.run(recurring.draft_caption(plan_id, caption_id, replace="1"))
        )
        second_response = second.result(timeout=5)
        assert "caption_error=" in second_response.headers["location"]
        assert calls == ["called"]
        release.set()
        first_response = first.result(timeout=5)

    assert first_response.status_code == 303
    assert "caption_error=" not in first_response.headers["location"]
    caption = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    assert caption["body"] == "Single paid result"
    assert caption["ai_claim_token"] is None and caption["ai_claimed_at"] is None


def test_web_facade_inflight_result_cannot_enter_replacement_database(
    admin_client,
    monkeypatch,
):
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", True)
    live = Path(config.DB_PATH)
    parked = live.with_name("parked-web-original.db")
    replacement_identity = None

    class _ReplacingAdapter:
        def draft(self, _context, *, idempotency_key=None):
            nonlocal replacement_identity
            saas._scrub_mobile_caption_suggestions_for_offboarding(live)
            live.rename(parked)
            for suffix in ("-wal", "-shm"):
                companion = Path(f"{live}{suffix}")
                if companion.exists():
                    companion.rename(Path(f"{parked}{suffix}"))
            db.migrate()
            replacement_identity = db.one(
                "SELECT database_identity FROM mobile_runtime_state WHERE singleton=1"
            )["database_identity"]
            return ProviderResult(
                capability=Capability.CONTENT,
                provider="odysseus",
                status=ResultStatus.OK,
                review=ReviewRequirement.HUMAN_REVIEW,
                output={"caption": "MUST_NOT_CROSS_TENANTS"},
                model="PRIVATE-MODEL",
            )

    monkeypatch.setattr(providers, "resolve", lambda _capability: _ReplacingAdapter())

    response = asyncio.run(recurring.draft_caption(plan_id, caption_id, replace="1"))

    assert "caption_error=" in response.headers["location"]
    assert replacement_identity is not None
    assert db.one("SELECT COUNT(*) AS n FROM ai_runs")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM retainer_captions")["n"] == 0
    with sqlite3.connect(parked) as con:
        con.row_factory = sqlite3.Row
        original_identity = con.execute(
            "SELECT database_identity FROM mobile_runtime_state WHERE singleton=1"
        ).fetchone()["database_identity"]
        caption = con.execute(
            "SELECT * FROM retainer_captions WHERE id=?",
            (caption_id,),
        ).fetchone()
        assert original_identity != replacement_identity
        assert caption["body"] == ""
        assert caption["ai_claim_token"] is not None
        assert con.execute("SELECT COUNT(*) FROM ai_runs").fetchone()[0] == 0


def test_caption_draft_identity_token_blocks_delete_reinsert_aba(admin_client, monkeypatch):
    plan_id, caption_id = _plan_with_caption(admin_client)
    original = db.one("SELECT identity_token FROM retainer_captions WHERE id=?", (caption_id,))[
        "identity_token"
    ]
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", True)

    class _ReplacingCaptionAdapter:
        def draft(self, _context, *, idempotency_key=None):
            with db.tx() as con:
                con.execute("DELETE FROM retainer_captions WHERE id=?", (caption_id,))
                con.execute(
                    """INSERT INTO retainer_captions
                       (id,plan_id,period,label,body,status,revision)
                       VALUES (?,?,?,?,?,'draft',0)""",
                    (caption_id, plan_id, "2026-06", "Reel", "Replacement row"),
                )
            return ProviderResult(
                capability=Capability.CONTENT,
                provider="odysseus",
                status=ResultStatus.OK,
                review=ReviewRequirement.HUMAN_REVIEW,
                output={"caption": "Late AI output"},
                model="test-model",
            )

    monkeypatch.setattr(providers, "resolve", lambda _capability: _ReplacingCaptionAdapter())
    response = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "caption_error=" in response.headers["location"]
    row = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    assert row["identity_token"] != original
    assert row["revision"] == 0
    assert row["body"] == "Replacement row"
    assert not row["ai_drafted"]
    assert db.one("SELECT COUNT(*) AS n FROM ai_runs")["n"] == 0


def test_ambiguous_web_provider_failure_keeps_claim_and_blocks_second_paid_call(
    admin_client,
    monkeypatch,
):
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", False)
    calls = []

    def ambiguous_failure(_context, *, idempotency_key=None):
        calls.append(idempotency_key)
        raise caption_ai.CaptionDraftError(
            "AI drafting provider is unavailable",
            provider_attempted=True,
        )

    monkeypatch.setattr(caption_ai, "draft_caption", ambiguous_failure)
    first = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft",
        follow_redirects=False,
    )
    second = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft",
        follow_redirects=False,
    )

    assert first.status_code == second.status_code == 303
    assert len(calls) == 1 and calls[0]
    caption = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    assert caption["body"] == ""
    assert caption["ai_claim_token"] == calls[0]
    assert caption["ai_claimed_at"] is not None


def test_facade_pre_network_invalid_context_releases_claim(admin_client, monkeypatch):
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", True)
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_URL", "https://odysseus.test/caption")
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_TOKEN", "test-token")
    db.run(
        "UPDATE retainer_captions SET note=? WHERE id=?",
        ("x" * 4_001, caption_id),
    )
    monkeypatch.setattr(
        caption_ai,
        "_open_provider",
        lambda *_args, **_kwargs: pytest.fail("invalid context must not reach the network"),
    )

    response = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft",
        follow_redirects=False,
    )

    assert response.status_code == 303
    caption = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    assert caption["body"] == ""
    assert caption["ai_claim_token"] is None and caption["ai_claimed_at"] is None
    run = db.one("SELECT status,error FROM ai_runs WHERE subject_id=?", (caption_id,))
    assert run["status"] == "provider_error"
    assert run["error"] == "AI drafting request is invalid"


def test_stale_web_claim_is_unknown_and_never_retried(admin_client, monkeypatch):
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", False)
    token = "123e4567-e89b-12d3-a456-426614174000"
    db.run(
        """UPDATE retainer_captions
              SET ai_claim_token=?,ai_claimed_at='2000-01-01 00:00:00'
            WHERE id=?""",
        (token, caption_id),
    )
    monkeypatch.setattr(
        caption_ai,
        "draft_caption",
        lambda *_args, **_kwargs: pytest.fail("stale claim must not call provider"),
    )

    response = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "caption_error=" in response.headers["location"]
    caption = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    assert caption["ai_claim_token"] == token


def test_cancelled_web_request_keeps_claim_while_provider_thread_finishes(
    admin_client,
    monkeypatch,
):
    plan_id, caption_id = _plan_with_caption(admin_client)
    monkeypatch.setattr(config, "PROVIDER_FACADE_CONTENT", False)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocking_provider(_context, *, idempotency_key=None):
        calls.append(idempotency_key)
        entered.set()
        assert release.wait(timeout=5)
        return {"caption": "orphaned response", "model": "test-model"}

    monkeypatch.setattr(caption_ai, "draft_caption", blocking_provider)

    async def cancel_inflight():
        task = asyncio.create_task(recurring.draft_caption(plan_id, caption_id, replace="1"))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()

    asyncio.run(cancel_inflight())

    assert len(calls) == 1 and calls[0]
    caption = db.one("SELECT * FROM retainer_captions WHERE id=?", (caption_id,))
    assert caption["body"] == ""
    assert caption["ai_claim_token"] == calls[0]
    blocked = admin_client.post(
        f"/admin/studio/recurring/{plan_id}/captions/{caption_id}/draft",
        follow_redirects=False,
    )
    assert blocked.status_code == 303
    assert len(calls) == 1
