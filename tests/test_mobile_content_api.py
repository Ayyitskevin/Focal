"""Owner caption workspace, immutable suggestion, and worker safety contracts."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import (
    config,
    db,
    jobs,
    mobile_auth,
    mobile_content_api,
    providers,
    ratelimit,
    saas,
)
from app.main import app

pytestmark = pytest.mark.unit

_SUMMARY_FIELDS = {
    "id",
    "version_id",
    "revision",
    "client_display_name",
    "plan_title",
    "period",
    "label",
    "body_preview",
    "status",
    "ai_assisted",
    "updated_at",
}
_PAGE_FIELDS = {"items", "next_cursor", "has_more", "suggestions_enabled"}
_DETAIL_FIELDS = {
    "id",
    "version_id",
    "revision",
    "client_display_name",
    "plan_id",
    "plan_title",
    "period",
    "label",
    "body",
    "note",
    "status",
    "ai_assisted",
    "ai_drafted_at",
    "suggestions_enabled",
    "created_at",
    "updated_at",
}
_SUGGESTION_FIELDS = {
    "id",
    "caption_id",
    "state",
    "review",
    "candidate_text",
    "failure_reason",
    "base_revision",
    "stale",
    "created_at",
    "expires_at",
    "completed_at",
}
_INTERNAL_FIELDS = {
    "context",
    "context_json",
    "provider",
    "model",
    "error",
    "error_message",
    "output",
    "job_id",
    "session_id",
    "idempotency_key",
}


def _device(name: str) -> dict[str, str]:
    return {
        "installation_id": str(uuid.uuid4()),
        "name": name,
        "platform": "ios",
        "app_version": "6.0",
    }


def _owner_headers(
    client: TestClient,
    *,
    email: str | None = None,
    password: str = "owner-password",
    name: str = "Owner iPhone",
) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": email,
            "password": password,
            "device": _device(name),
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _guest_headers(client: TestClient, slug: str = "content-gallery") -> dict[str, str]:
    response = client.post(
        "/api/v1/client-auth/gallery/unlock",
        json={
            "kind": "gallery",
            "slug": slug,
            "pin": "2468",
            "device": _device("Client iPhone"),
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_content(label_prefix: str = "") -> dict[str, int]:
    client_id = db.run(
        "INSERT INTO clients (name,company,email,notes) VALUES (?,?,?,?)",
        (
            f"{label_prefix}Avery Client",
            f"{label_prefix}Avery Foods",
            "avery@example.test",
            "PRIVATE CLIENT NOTES",
        ),
    )
    project_id = db.run(
        "INSERT INTO projects (client_id,title) VALUES (?,?)",
        (client_id, f"{label_prefix}Campaign"),
    )
    plan_id = db.run(
        """INSERT INTO recurring_plans
           (project_id,title,line_items,total_cents,quota)
           VALUES (?,?, '[]', 75000, '[]')""",
        (project_id, f"{label_prefix}Monthly Social"),
    )
    draft_id = db.run(
        """INSERT INTO retainer_captions
           (plan_id,period,label,body,note,created_at)
           VALUES (?,?,?,?,?,'2026-07-11 10:00:00')""",
        (
            plan_id,
            "2026-07",
            f"{label_prefix}Hero",
            "Existing human caption",
            "PRIVATE CAPTION NOTE",
        ),
    )
    second_id = db.run(
        """INSERT INTO retainer_captions
           (plan_id,period,label,body,note,ai_drafted,ai_model,ai_drafted_at,
            ai_draft_original,created_at)
           VALUES (?,?,?,?,?,1,'PRIVATE-MODEL','2026-07-11 10:30:00',?,
                   '2026-07-11 11:00:00')""",
        (
            plan_id,
            "2026-08",
            f"{label_prefix}Carousel",
            "  first\n second\tthird  ",
            None,
            "first second third",
        ),
    )
    approved_id = db.run(
        """INSERT INTO retainer_captions
           (plan_id,period,label,body,note,status,created_at)
           VALUES (?,?,?,?,?,'approved','2026-07-11 12:00:00')""",
        (
            plan_id,
            "2026-09",
            f"{label_prefix}Approved",
            "Approved copy",
            None,
        ),
    )
    delivery_id = db.run(
        """INSERT INTO retainer_deliveries (plan_id,period,label,qty,note)
           VALUES (?,?,?,?,?)""",
        (plan_id, "2026-07", "Hero", 1, "Manual delivery"),
    )
    invoice_id = db.run(
        """INSERT INTO invoices
           (project_id,slug,title,line_items,total_cents,status,recurring_plan_id)
           VALUES (?,?,?, '[]', 75000, 'draft', ?)""",
        (project_id, f"invoice-{label_prefix or 'self'}", "Monthly invoice", plan_id),
    )
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,pin,published,type,require_pin,expires_at)
           VALUES (?,?,?,1,'gallery',1,'2099-12-31')""",
        (f"{label_prefix.lower()}content-gallery", "Content Gallery", "2468"),
    )
    return {
        "client": client_id,
        "project": project_id,
        "plan": plan_id,
        "draft": draft_id,
        "second": second_id,
        "approved": approved_id,
        "delivery": delivery_id,
        "invoice": invoice_id,
        "gallery": gallery_id,
    }


@dataclass
class ContentEnv:
    client: TestClient
    owner: dict[str, str]
    guest: dict[str, str]
    ids: dict[str, int]
    kicked: list[int]


@pytest.fixture(autouse=True)
def _reset_providers():
    providers.reset()
    yield
    providers.reset()


@pytest.fixture
def content(tmp_path, monkeypatch) -> ContentEnv:
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "mobile-content-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "MOBILE_CONTENT_SUGGESTIONS", True)
    monkeypatch.setattr(config, "MOBILE_CONTENT_DAILY_LIMIT", 10)
    monkeypatch.setattr(config, "MOBILE_CONTENT_CONCURRENT_LIMIT", 5)
    monkeypatch.setattr(config, "MOBILE_CONTENT_SUGGESTION_TTL_HOURS", 24)
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_URL", "https://provider.test/caption")
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_TOKEN", "provider-test-token")
    ratelimit._hits.clear()
    db.migrate()
    ids = _seed_content()
    kicked: list[int] = []
    monkeypatch.setattr(jobs, "kick", kicked.append)
    client = TestClient(app, base_url="https://studio.test")
    owner = _owner_headers(client)
    guest = _guest_headers(client)
    yield ContentEnv(client, owner, guest, ids, kicked)
    client.close()
    ratelimit._hits.clear()


def _detail(content: ContentEnv, caption_id: int | None = None):
    return content.client.get(
        f"/api/v1/content/captions/{caption_id or content.ids['draft']}",
        headers=content.owner,
    )


def _command_headers(
    headers: dict[str, str],
    etag: str,
    *,
    key: uuid.UUID | str | None = None,
) -> dict[str, str]:
    return {
        **headers,
        "If-Match": etag,
        "Idempotency-Key": str(key or uuid.uuid4()),
    }


def _create_suggestion(
    content: ContentEnv,
    caption_id: int | None = None,
    *,
    instruction: str | None = None,
    key: uuid.UUID | str | None = None,
    headers: dict[str, str] | None = None,
):
    caption_id = caption_id or content.ids["draft"]
    auth = headers or content.owner
    detail = content.client.get(
        f"/api/v1/content/captions/{caption_id}",
        headers=auth,
    )
    assert detail.status_code == 200, detail.text
    body = {} if instruction is None else {"instruction": instruction}
    return content.client.post(
        f"/api/v1/content/captions/{caption_id}/suggestions",
        headers=_command_headers(auth, detail.headers["etag"], key=key),
        json=body,
    )


class _CapturingProvider:
    def __init__(self, result: providers.ProviderResult, callback=None):
        self.result = result
        self.callback = callback
        self.calls: list[tuple[dict, str | None]] = []

    def draft(self, context: dict, *, idempotency_key: str | None = None):
        self.calls.append((context, idempotency_key))
        if self.callback is not None:
            self.callback()
        return self.result


def _success_result(
    caption: object = "Generated candidate",
    *,
    provider: str = "PRIVATE-PROVIDER",
    model: str = "PRIVATE-MODEL",
) -> providers.ProviderResult:
    return providers.ProviderResult(
        capability=providers.Capability.CONTENT,
        provider=provider,
        status=providers.ResultStatus.OK,
        review=providers.ReviewRequirement.HUMAN_REVIEW,
        output={"caption": caption},
        model=model,
        latency_ms=123,
    )


def _run_with(adapter: _CapturingProvider, suggestion_id: str) -> None:
    with providers.use(providers.Capability.CONTENT, adapter):
        mobile_content_api.run_caption_suggestion(suggestion_id)


def _request(method: str, path: str, headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [
                (name.lower().encode(), value.encode())
                for name, value in {"Host": "studio.test", **headers}.items()
            ],
            "scheme": "https",
            "server": ("studio.test", 443),
            "client": ("127.0.0.1", 12345),
        }
    )


def _ready_suggestion(
    content: ContentEnv,
    *,
    caption_id: int | None = None,
    candidate: str = "Generated candidate",
    callback=None,
):
    caption_id = caption_id or content.ids["draft"]
    created = _create_suggestion(content, caption_id)
    assert created.status_code == 202, created.text
    suggestion_id = created.json()["id"]
    adapter = _CapturingProvider(_success_result(candidate), callback=callback)
    _run_with(adapter, suggestion_id)
    polled = content.client.get(
        f"/api/v1/content/captions/{caption_id}/suggestions/{suggestion_id}",
        headers=content.owner,
    )
    return created, polled, adapter


def _business_snapshot(caption_id: int) -> dict[str, object]:
    caption = db.one(
        "SELECT body,status,revision FROM retainer_captions WHERE id=?",
        (caption_id,),
    )
    return {
        "caption": tuple(caption) if caption is not None else None,
        "deliveries": [tuple(row) for row in db.all_("SELECT id,qty FROM retainer_deliveries")],
        "invoices": [tuple(row) for row in db.all_("SELECT id,status,total_cents FROM invoices")],
    }


def test_migration_identity_and_all_web_caption_changes_advance_revision(content):
    migration = db.one(
        "SELECT name FROM schema_migrations WHERE name='085_mobile_caption_suggestions.sql'"
    )
    columns = {row["name"]: row for row in db.all_("PRAGMA table_info(retainer_captions)")}
    indexes = {row["name"] for row in db.all_("PRAGMA index_list(retainer_captions)")}
    triggers = {
        row["name"] for row in db.all_("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    identities = [
        row["identity_token"] for row in db.all_("SELECT identity_token FROM retainer_captions")
    ]
    runtime = db.one(
        "SELECT database_identity,offboarding FROM mobile_runtime_state WHERE singleton=1"
    )
    usage_indexes = {row["name"] for row in db.all_("PRAGMA index_list(mobile_caption_usage)")}

    assert migration is not None
    assert columns["revision"]["notnull"] == 1
    assert str(columns["revision"]["dflt_value"]) == "0"
    assert {
        "updated_at",
        "identity_token",
        "ai_claim_token",
        "ai_claimed_at",
    } <= set(columns)
    assert "idx_retainer_captions_identity" in indexes
    assert "idx_retainer_captions_ai_claim" in indexes
    assert "trg_retainer_captions_identity" in triggers
    assert len(set(identities)) == len(identities)
    assert all(re.fullmatch(r"[0-9a-f]{32}", value) for value in identities)
    assert re.fullmatch(r"[0-9a-f]{32}", runtime["database_identity"])
    assert runtime["offboarding"] == 0
    assert {
        "idx_mobile_caption_usage_state",
        "idx_mobile_caption_usage_accepted",
    } <= usage_indexes

    before = _detail(content)
    version_id = before.json()["version_id"]
    admin_login = content.client.post(
        "/admin/login",
        data={"password": "owner-password"},
        follow_redirects=False,
    )
    assert admin_login.status_code == 303
    edited = content.client.post(
        f"/admin/studio/recurring/{content.ids['plan']}/captions/{content.ids['draft']}",
        data={"label": "Edited Hero", "body": "Edited body", "note": "Edited note"},
        follow_redirects=False,
    )
    after_edit = _detail(content)
    approved = content.client.post(
        f"/admin/studio/recurring/{content.ids['plan']}/captions/{content.ids['draft']}/status",
        data={"status": "approved"},
        follow_redirects=False,
    )
    after_approval = _detail(content)

    assert edited.status_code == approved.status_code == 303
    assert after_edit.json()["revision"] == before.json()["revision"] + 1
    assert after_approval.json()["revision"] == before.json()["revision"] + 2
    assert after_edit.json()["version_id"] == after_approval.json()["version_id"] == version_id
    assert before.headers["etag"] != after_edit.headers["etag"] != after_approval.headers["etag"]
    assert db.one("SELECT COUNT(*) AS n FROM retainer_deliveries")["n"] == 1


def test_mobile_content_migration_rollback_is_executable(content):
    rollback = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "rollback"
        / "085_mobile_caption_suggestions.sql"
    )
    con = db.connect()
    try:
        con.executescript(rollback.read_text())
        columns = {row["name"] for row in con.execute("PRAGMA table_info(retainer_captions)")}
        suggestion_table = con.execute(
            """SELECT name FROM sqlite_master
                WHERE type='table' AND name='mobile_caption_suggestions'"""
        ).fetchone()
        usage_table = con.execute(
            """SELECT name FROM sqlite_master
                WHERE type='table' AND name='mobile_caption_usage'"""
        ).fetchone()
        runtime_table = con.execute(
            """SELECT name FROM sqlite_master
                WHERE type='table' AND name='mobile_runtime_state'"""
        ).fetchone()
        migration_marker = con.execute(
            """SELECT name FROM schema_migrations
                WHERE name='085_mobile_caption_suggestions.sql'"""
        ).fetchone()
    finally:
        con.close()

    assert suggestion_table is None
    assert usage_table is None
    assert runtime_table is None
    assert migration_marker is None
    assert {
        "revision",
        "updated_at",
        "identity_token",
        "ai_claim_token",
        "ai_claimed_at",
    }.isdisjoint(columns)

    # The rollback marker is deliberately removed, so the exact forward
    # migration is safe to redeploy instead of being silently skipped.
    db.migrate()
    assert (
        db.one(
            """SELECT name FROM sqlite_master
            WHERE type='table' AND name='mobile_runtime_state'"""
        )
        is not None
    )
    assert (
        db.one(
            """SELECT name FROM sqlite_master
            WHERE type='table' AND name='mobile_caption_usage'"""
        )
        is not None
    )
    assert (
        db.one(
            """SELECT name FROM schema_migrations
            WHERE name='085_mobile_caption_suggestions.sql'"""
        )
        is not None
    )


def test_mobile_content_migration_rollback_is_atomic_on_failure(content):
    rollback = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "rollback"
        / "085_mobile_caption_suggestions.sql"
    )
    con = db.connect()
    try:
        con.execute(
            """CREATE VIEW rollback_blocker AS
               SELECT identity_token FROM retainer_captions"""
        )
        con.commit()
        with pytest.raises(sqlite3.OperationalError):
            con.executescript(rollback.read_text())
        con.rollback()

        columns = {row["name"] for row in con.execute("PRAGMA table_info(retainer_captions)")}
        tables = {
            row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        marker = con.execute(
            """SELECT 1 FROM schema_migrations
                WHERE name='085_mobile_caption_suggestions.sql'"""
        ).fetchone()
    finally:
        con.close()

    assert {
        "revision",
        "updated_at",
        "identity_token",
        "ai_claim_token",
        "ai_claimed_at",
    } <= columns
    assert {
        "mobile_runtime_state",
        "mobile_caption_usage",
        "mobile_caption_suggestions",
    } <= tables
    assert marker is not None


def test_mobile_content_rollback_refuses_unreconciled_web_claim(content):
    rollback = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "rollback"
        / "085_mobile_caption_suggestions.sql"
    )
    db.run(
        """UPDATE retainer_captions
              SET ai_claim_token='123e4567-e89b-12d3-a456-426614174000',
                  ai_claimed_at=datetime('now')
            WHERE id=?""",
        (content.ids["draft"],),
    )
    con = db.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.executescript(rollback.read_text())
        con.rollback()
        columns = {row["name"] for row in con.execute("PRAGMA table_info(retainer_captions)")}
        usage_table = con.execute(
            """SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='mobile_caption_usage'"""
        ).fetchone()
        marker = con.execute(
            """SELECT 1 FROM schema_migrations
                WHERE name='085_mobile_caption_suggestions.sql'"""
        ).fetchone()
    finally:
        con.close()

    assert "ai_claim_token" in columns
    assert usage_table is not None
    assert marker is not None


def test_offboarding_barrier_rejects_a_previously_admitted_suggestion_create(content):
    caption_id = content.ids["draft"]
    detail = _detail(content)
    headers = _command_headers(content.owner, detail.headers["etag"])
    request = _request(
        "POST",
        f"/api/v1/content/captions/{caption_id}/suggestions",
        headers,
    )
    token = content.owner["Authorization"].split(" ", 1)[1]
    principal = mobile_auth.authenticate_access(
        request,
        token,
        required_scopes={"studio:write"},
    )

    # Authentication has already admitted the principal. The deletion barrier
    # commits before route mutation begins; the in-transaction recheck must win.
    saas._scrub_mobile_caption_suggestions_for_offboarding(Path(config.DB_PATH))
    with pytest.raises(mobile_auth.MobileAuthError) as exc_info:
        mobile_content_api.create_caption_suggestion(
            request,
            Response(),
            mobile_content_api.CaptionSuggestionRequest(instruction="must not persist"),
            principal,
            caption_id,
        )

    assert exc_info.value.status_code == 401
    assert db.one("SELECT COUNT(*) AS n FROM mobile_caption_suggestions")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='mobile_caption_suggestion'")["n"] == 0
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM mobile_commands WHERE operation LIKE 'content.caption.suggest:%'"
        )["n"]
        == 0
    )


def test_failed_offboarding_keeps_running_capacity_claim_until_worker_reconciles(
    content,
    monkeypatch,
):
    monkeypatch.setattr(config, "MOBILE_CONTENT_CONCURRENT_LIMIT", 1)
    created = _create_suggestion(content)
    suggestion_id = created.json()["id"]
    db.run(
        """UPDATE mobile_caption_suggestions
              SET status='running',provider_attempted_at=datetime('now')
            WHERE id=?""",
        (suggestion_id,),
    )

    saas._scrub_mobile_caption_suggestions_for_offboarding(Path(config.DB_PATH))
    saas._restore_mobile_runtime_after_failed_offboarding(Path(config.DB_PATH))

    usage = db.one("SELECT state FROM mobile_caption_usage WHERE id=?", (suggestion_id,))
    assert usage["state"] == "active"
    second_owner = _owner_headers(content.client, name="Offboarding Recovery iPad")
    blocked = _create_suggestion(
        content,
        content.ids["second"],
        headers=second_owner,
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "content.concurrent_limit"

    mobile_content_api.run_caption_suggestion(suggestion_id)
    usage = db.one("SELECT state FROM mobile_caption_usage WHERE id=?", (suggestion_id,))
    assert usage["state"] == "finished"


def test_revocation_between_paid_claim_and_precall_check_skips_provider(content, monkeypatch):
    created = _create_suggestion(content)
    assert created.status_code == 202
    suggestion_id = created.json()["id"]
    stored = db.one(
        "SELECT session_id FROM mobile_caption_suggestions WHERE id=?",
        (suggestion_id,),
    )
    session_id = str(stored["session_id"])
    original_bound = mobile_content_api._bound_runtime_transaction
    intercepted = False

    @contextmanager
    def revoke_then_bind(path, identity):
        nonlocal intercepted
        if not intercepted:
            intercepted = True
            con = db.connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                mobile_auth._revoke_session_tx(
                    con,
                    session_id,
                    mobile_auth._now_ts(),
                    "test_precall_revoke",
                )
                con.commit()
            finally:
                con.close()
        with original_bound(path, identity) as con:
            yield con

    monkeypatch.setattr(
        mobile_content_api,
        "_bound_runtime_transaction",
        revoke_then_bind,
    )
    adapter = _CapturingProvider(_success_result("must never be generated"))

    _run_with(adapter, suggestion_id)

    assert intercepted is True
    assert adapter.calls == []
    row = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (suggestion_id,))
    assert row["status"] == "failed" and row["failure_code"] == "session_ended"
    assert row["context_json"] is None and row["candidate_text"] is None
    assert db.one("SELECT COUNT(*) AS n FROM ai_runs")["n"] == 0


def test_inflight_worker_cannot_write_after_database_path_replacement(content):
    created = _create_suggestion(content)
    assert created.status_code == 202
    suggestion_id = created.json()["id"]
    live = Path(config.DB_PATH)
    parked = live.with_name("parked-original.db")
    replacement_identity = None

    def replace_database():
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

    adapter = _CapturingProvider(
        _success_result("MUST_NOT_ENTER_REPLACEMENT"),
        callback=replace_database,
    )

    _run_with(adapter, suggestion_id)

    assert len(adapter.calls) == 1
    assert replacement_identity is not None
    assert db.one("SELECT COUNT(*) AS n FROM ai_runs")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM mobile_caption_suggestions")["n"] == 0
    with sqlite3.connect(parked) as con:
        con.row_factory = sqlite3.Row
        old_identity = con.execute(
            "SELECT database_identity FROM mobile_runtime_state WHERE singleton=1"
        ).fetchone()["database_identity"]
        old_row = con.execute(
            "SELECT * FROM mobile_caption_suggestions WHERE id=?",
            (suggestion_id,),
        ).fetchone()
        assert old_identity != replacement_identity
        assert old_row["status"] == "failed"
        assert old_row["context_json"] is None and old_row["candidate_text"] is None


def test_caption_list_and_detail_are_exact_private_conditional_projections(content):
    page = content.client.get("/api/v1/content/captions", headers=content.owner)

    assert page.status_code == 200
    assert set(page.json()) == _PAGE_FIELDS
    assert [item["id"] for item in page.json()["items"]] == [
        content.ids["approved"],
        content.ids["second"],
        content.ids["draft"],
    ]
    assert all(set(item) == _SUMMARY_FIELDS for item in page.json()["items"])
    assert page.json()["items"][1] == {
        "id": content.ids["second"],
        "version_id": page.json()["items"][1]["version_id"],
        "revision": 0,
        "client_display_name": "Avery Foods",
        "plan_title": "Monthly Social",
        "period": "2026-08",
        "label": "Carousel",
        "body_preview": "first second third",
        "status": "draft",
        "ai_assisted": True,
        "updated_at": "2026-07-11T11:00:00Z",
    }
    assert page.json()["suggestions_enabled"] is True
    assert page.json()["has_more"] is False
    assert page.json()["next_cursor"] is None
    assert page.headers["cache-control"] == "private, no-cache"
    assert page.headers["vary"] == "Authorization"
    assert page.headers["etag"].startswith('"content-captions-v1-')

    unchanged = content.client.get(
        "/api/v1/content/captions",
        headers={**content.owner, "If-None-Match": f"W/{page.headers['etag']}"},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == page.headers["etag"]

    detail = _detail(content)
    payload = detail.json()
    assert detail.status_code == 200
    assert set(payload) == _DETAIL_FIELDS
    assert payload == {
        "id": content.ids["draft"],
        "version_id": payload["version_id"],
        "revision": 0,
        "client_display_name": "Avery Foods",
        "plan_id": content.ids["plan"],
        "plan_title": "Monthly Social",
        "period": "2026-07",
        "label": "Hero",
        "body": "Existing human caption",
        "note": "PRIVATE CAPTION NOTE",
        "status": "draft",
        "ai_assisted": False,
        "ai_drafted_at": None,
        "suggestions_enabled": True,
        "created_at": "2026-07-11T10:00:00Z",
        "updated_at": "2026-07-11T10:00:00Z",
    }
    assert re.fullmatch(r"[0-9a-f]{32}", payload["version_id"])
    assert detail.headers["cache-control"] == "private, no-cache"
    assert detail.headers["vary"] == "Authorization"
    assert detail.headers["etag"].startswith('"content-caption-v1-0-')
    conditional = content.client.get(
        f"/api/v1/content/captions/{content.ids['draft']}",
        headers={**content.owner, "If-None-Match": detail.headers["etag"]},
    )
    assert conditional.status_code == 304
    assert conditional.content == b""


def test_caption_keyset_pagination_survives_insert_and_queries_are_strict(content):
    first = content.client.get(
        "/api/v1/content/captions",
        params={"limit": 1},
        headers=content.owner,
    )
    assert first.status_code == 200
    assert [item["id"] for item in first.json()["items"]] == [content.ids["approved"]]
    assert first.json()["has_more"] is True
    cursor = first.json()["next_cursor"]
    inserted = db.run(
        """INSERT INTO retainer_captions (plan_id,period,label,body)
           VALUES (?,?,?,?)""",
        (content.ids["plan"], "2026-10", "Inserted", "Newer concurrent row"),
    )
    second = content.client.get(
        "/api/v1/content/captions",
        params={"limit": 1, "cursor": cursor},
        headers=content.owner,
    )
    fresh = content.client.get(
        "/api/v1/content/captions",
        params={"limit": 1},
        headers=content.owner,
    )
    assert second.status_code == fresh.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == [content.ids["second"]]
    assert fresh.json()["items"][0]["id"] == inserted
    assert fresh.headers["etag"] != first.headers["etag"]

    bad_requests = [
        "/api/v1/content/captions?unknown=1",
        "/api/v1/content/captions?limit=1&limit=2",
        "/api/v1/content/captions?limit=0",
        "/api/v1/content/captions?limit=101",
        "/api/v1/content/captions?cursor=not-a-cursor",
        f"/api/v1/content/captions/{content.ids['draft']}?extra=1",
    ]
    for path in bad_requests:
        response = content.client.get(path, headers=content.owner)
        assert response.status_code == 422, (path, response.text)
        assert response.headers["content-type"].startswith("application/problem+json")


def test_content_routes_require_exact_owner_bearer_host_and_never_admin_cookie(content):
    assert _detail(content).status_code == 200
    assert content.client.get("/api/v1/content/captions").status_code == 401
    assert content.client.get("/api/v1/content/captions", headers=content.guest).status_code == 403
    assert (
        content.client.get(
            "/api/v1/content/captions",
            headers={**content.owner, "Host": "attacker.test"},
        ).status_code
        == 401
    )

    login = content.client.post(
        "/admin/login",
        data={"password": "owner-password"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert "mise_admin" in content.client.cookies
    cookie_only = content.client.get("/api/v1/content/captions")
    assert cookie_only.status_code == 401
    assert cookie_only.json()["code"] == "auth.invalid_token"

    read_only_session = db.one(
        """SELECT id FROM api_sessions
            WHERE principal_kind='studio_owner' ORDER BY created_at LIMIT 1"""
    )
    db.run(
        "UPDATE api_sessions SET scopes_json='[\"studio:read\"]' WHERE id=?",
        (read_only_session["id"],),
    )
    assert _detail(content).status_code == 200
    denied = _create_suggestion(content)
    assert denied.status_code == 403
    assert denied.json()["code"] == "auth.insufficient_scope"


def test_generation_is_default_off_and_creates_no_command_job_or_audit(content, monkeypatch):
    monkeypatch.setattr(config, "MOBILE_CONTENT_SUGGESTIONS", False)
    detail = _detail(content)
    page = content.client.get("/api/v1/content/captions", headers=content.owner)
    response = _create_suggestion(content)

    assert detail.json()["suggestions_enabled"] is False
    assert page.json()["suggestions_enabled"] is False
    assert response.status_code == 404
    assert response.json()["code"] == "content.suggestions_disabled"
    assert db.one("SELECT COUNT(*) AS n FROM mobile_commands")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM mobile_caption_suggestions")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='mobile_caption_suggestion'")["n"] == 0
    assert (
        db.one("SELECT COUNT(*) AS n FROM audit_log WHERE action='suggestion_requested'")["n"] == 0
    )
    assert content.kicked == []


def test_worker_rechecks_kill_switch_after_enqueue(content, monkeypatch):
    created = _create_suggestion(content, instruction="must be scrubbed")
    suggestion_id = created.json()["id"]
    monkeypatch.setattr(config, "MOBILE_CONTENT_SUGGESTIONS", False)
    adapter = _CapturingProvider(_success_result("must not be generated"))

    _run_with(adapter, suggestion_id)

    assert adapter.calls == []
    row = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (suggestion_id,))
    assert row["status"] == "failed"
    assert row["failure_code"] == "disabled"
    assert row["context_json"] is None
    assert row["candidate_text"] is None


def test_suggestion_creation_is_202_durable_replay_safe_and_conflict_checked(content):
    key = uuid.uuid4()
    first = _create_suggestion(content, instruction="Keep it concise", key=key)
    replay = _create_suggestion(content, instruction="Keep it concise", key=key)
    conflict = _create_suggestion(content, instruction="Different intent", key=key)

    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    assert set(first.json()) == _SUGGESTION_FIELDS
    assert first.json()["state"] == "queued"
    assert first.json()["candidate_text"] is None
    assert first.json()["failure_reason"] is None
    assert first.json()["review"] == "human_review"
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["vary"] == "Authorization"
    assert first.headers["location"].endswith(first.json()["id"])
    assert replay.headers["idempotency-replayed"] == "true"
    assert "idempotency-replayed" not in first.headers
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "request.idempotency_conflict"
    assert db.one("SELECT COUNT(*) AS n FROM mobile_caption_suggestions")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM mobile_commands")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='mobile_caption_suggestion'")["n"] == 1
    assert (
        db.one("SELECT COUNT(*) AS n FROM audit_log WHERE action='suggestion_requested'")["n"] == 1
    )
    job = db.one("SELECT id,kind,payload,status FROM jobs WHERE kind='mobile_caption_suggestion'")
    assert json.loads(job["payload"]) == {"suggestion_id": first.json()["id"]}
    assert job["status"] == "queued"
    assert content.kicked == [job["id"]]


def test_concurrent_same_key_suggestion_creation_has_one_durable_effect(content):
    caption_id = content.ids["draft"]
    detail = _detail(content, caption_id)
    key = uuid.uuid4()
    headers = _command_headers(content.owner, detail.headers["etag"], key=key)
    barrier = threading.Barrier(2)

    def create_once():
        client = TestClient(app, base_url="https://studio.test")
        try:
            barrier.wait()
            response = client.post(
                f"/api/v1/content/captions/{caption_id}/suggestions",
                headers=headers,
                json={"instruction": "A concurrent retry"},
            )
            return (
                response.status_code,
                response.json(),
                response.headers.get("idempotency-replayed"),
            )
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: create_once(), range(2)))

    assert [result[0] for result in results] == [202, 202]
    assert results[0][1] == results[1][1]
    assert sorted(result[2] or "" for result in results) == ["", "true"]
    suggestion_id = results[0][1]["id"]
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM mobile_caption_suggestions WHERE id=?",
            (suggestion_id,),
        )["n"]
        == 1
    )
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='mobile_caption_suggestion'")["n"] == 1
    assert (
        db.one(
            """SELECT COUNT(*) AS n FROM audit_log
                WHERE entity_type='retainer_caption' AND entity_id=?
                  AND action='suggestion_requested'""",
            (caption_id,),
        )["n"]
        == 1
    )


def test_generation_enforces_tenant_concurrent_and_daily_limits(content, monkeypatch, caplog):
    monkeypatch.setattr(config, "MOBILE_CONTENT_CONCURRENT_LIMIT", 1)
    with caplog.at_level("INFO", logger="mise.mobile_content"):
        first = _create_suggestion(content, content.ids["draft"])
        concurrent = _create_suggestion(content, content.ids["second"])
    assert first.status_code == 202
    assert concurrent.status_code == 429
    assert concurrent.json()["code"] == "content.concurrent_limit"
    assert concurrent.headers["retry-after"] == "30"

    db.run(
        "UPDATE mobile_caption_suggestions SET status='failed',context_json=NULL,failure_code='internal'"
    )
    monkeypatch.setattr(config, "MOBILE_CONTENT_CONCURRENT_LIMIT", 5)
    monkeypatch.setattr(config, "MOBILE_CONTENT_DAILY_LIMIT", 1)
    with caplog.at_level("INFO", logger="mise.mobile_content"):
        daily = _create_suggestion(content, content.ids["second"])
    assert daily.status_code == 429
    assert daily.json()["code"] == "content.daily_limit"
    assert daily.headers["retry-after"] == "3600"
    assert db.one("SELECT COUNT(*) AS n FROM mobile_caption_suggestions")["n"] == 1
    assert "quota denied (kind=concurrent)" in caplog.text
    assert "quota denied (kind=daily)" in caplog.text
    assert first.json()["id"] not in caplog.text


def test_worker_success_passes_minimal_context_and_exposes_only_candidate(content):
    before = _business_snapshot(content.ids["draft"])
    created = _create_suggestion(content, instruction="  Cafe\u0301 👩‍💻  ")
    suggestion_id = created.json()["id"]
    stored = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (suggestion_id,))
    assert json.loads(stored["context_json"]) == {
        "instruction": "Café 👩‍💻",
        "label": "Hero",
        "period": "2026-07",
    }
    assert not (
        {"body", "note", "client", "client_display_name", "plan_title"}
        & set(json.loads(stored["context_json"]))
    )

    adapter = _CapturingProvider(
        _success_result("  Cafe\u0301 👩‍💻\nready  "),
    )
    _run_with(adapter, suggestion_id)
    polled = content.client.get(
        f"/api/v1/content/captions/{content.ids['draft']}/suggestions/{suggestion_id}",
        headers=content.owner,
    )

    assert adapter.calls == [
        (
            {"instruction": "Café 👩‍💻", "label": "Hero", "period": "2026-07"},
            suggestion_id,
        )
    ]
    assert polled.status_code == 200
    assert set(polled.json()) == _SUGGESTION_FIELDS
    assert polled.json()["state"] == "ready"
    assert polled.json()["candidate_text"] == "Café 👩‍💻\nready"
    assert polled.json()["stale"] is False
    assert not (_INTERNAL_FIELDS & set(polled.json()))
    row = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (suggestion_id,))
    assert row["context_json"] is None
    assert row["candidate_text"] == "Café 👩‍💻\nready"
    assert row["provider"] == "PRIVATE-PROVIDER"
    assert row["model"] == "PRIVATE-MODEL"
    assert _business_snapshot(content.ids["draft"]) == before
    run = db.one("SELECT * FROM ai_runs WHERE idempotency_key=?", (suggestion_id,))
    assert run["subject_type"] == "retainer_caption"
    assert run["subject_id"] == content.ids["draft"]
    assert "Generated candidate" not in json.dumps(dict(run))


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (
            providers.ProviderResult.disabled(providers.Capability.CONTENT, "private-disabled"),
            "disabled",
        ),
        (
            providers.ProviderResult.failure(
                providers.Capability.CONTENT,
                "private-provider",
                providers.ResultStatus.PROVIDER_ERROR,
                "PRIVATE RAW PROVIDER ERROR",
            ),
            "provider_error",
        ),
        (
            providers.ProviderResult.failure(
                providers.Capability.CONTENT,
                "private-provider",
                providers.ResultStatus.INVALID_RESPONSE,
                "PRIVATE RAW INVALID BODY",
            ),
            "invalid_response",
        ),
    ],
)
def test_worker_failures_are_safe_terminal_and_scrubbed(content, result, reason):
    created = _create_suggestion(content, instruction="PRIVATE CONTEXT")
    suggestion_id = created.json()["id"]
    adapter = _CapturingProvider(result)
    _run_with(adapter, suggestion_id)
    response = content.client.get(
        f"/api/v1/content/captions/{content.ids['draft']}/suggestions/{suggestion_id}",
        headers=content.owner,
    )

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["failure_reason"] == reason
    assert response.json()["candidate_text"] is None
    assert "PRIVATE" not in response.text
    row = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (suggestion_id,))
    assert row["context_json"] is None
    assert row["candidate_text"] is None
    assert row["provider"] is None
    assert row["model"] is None
    assert row["failure_code"] == reason
    run = db.one("SELECT * FROM ai_runs WHERE idempotency_key=?", (suggestion_id,))
    assert run is not None
    assert "PRIVATE RAW" not in str(run["error"] or "")


def test_caption_delete_cannot_erase_daily_or_concurrent_usage_claim(
    content,
    monkeypatch,
):
    monkeypatch.setattr(config, "MOBILE_CONTENT_CONCURRENT_LIMIT", 1)
    monkeypatch.setattr(config, "MOBILE_CONTENT_DAILY_LIMIT", 1)
    created = _create_suggestion(content, content.ids["draft"])
    suggestion_id = created.json()["id"]
    db.run("DELETE FROM retainer_captions WHERE id=?", (content.ids["draft"],))
    replacement_id = db.run(
        """INSERT INTO retainer_captions (plan_id,period,label,body)
           VALUES (?,?,?,?)""",
        (content.ids["plan"], "2026-08", "Replacement", ""),
    )

    concurrent = _create_suggestion(content, replacement_id)
    assert concurrent.status_code == 429
    assert concurrent.json()["code"] == "content.concurrent_limit"

    # The queued job sees that its private suggestion cascaded away and releases
    # capacity, while the immutable one-day count remains.
    mobile_content_api.run_caption_suggestion(suggestion_id)
    usage = db.one("SELECT * FROM mobile_caption_usage WHERE id=?", (suggestion_id,))
    assert usage["state"] == "finished" and usage["finished_at"] is not None
    daily = _create_suggestion(content, replacement_id)
    assert daily.status_code == 429
    assert daily.json()["code"] == "content.daily_limit"


@pytest.mark.parametrize(
    "caption",
    [None, "", "x" * 10_001, "unsafe\x00caption", "unsafe\u202ecaption"],
)
def test_worker_maps_malformed_output_to_invalid_response(content, caption):
    created = _create_suggestion(content)
    suggestion_id = created.json()["id"]
    adapter = _CapturingProvider(_success_result(caption))
    _run_with(adapter, suggestion_id)
    response = content.client.get(
        f"/api/v1/content/captions/{content.ids['draft']}/suggestions/{suggestion_id}",
        headers=content.owner,
    )

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["failure_reason"] == "invalid_response"
    assert response.json()["candidate_text"] is None


def test_worker_contains_truthy_non_object_output_as_invalid_response(content):
    created = _create_suggestion(content)
    suggestion_id = created.json()["id"]
    result = providers.ProviderResult(
        capability=providers.Capability.CONTENT,
        provider="private-provider",
        status=providers.ResultStatus.OK,
        review=providers.ReviewRequirement.HUMAN_REVIEW,
        output=["not", "an", "object"],
        model="private-model",
    )
    adapter = _CapturingProvider(result)

    _run_with(adapter, suggestion_id)
    response = content.client.get(
        f"/api/v1/content/captions/{content.ids['draft']}/suggestions/{suggestion_id}",
        headers=content.owner,
    )

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["failure_reason"] == "invalid_response"
    assert response.json()["candidate_text"] is None


def test_worker_contains_provider_exception_as_safe_failure(content):
    created = _create_suggestion(content)
    suggestion_id = created.json()["id"]

    def provider_raises():
        raise RuntimeError("PRIVATE PROVIDER EXCEPTION")

    adapter = _CapturingProvider(_success_result(), callback=provider_raises)
    _run_with(adapter, suggestion_id)
    response = content.client.get(
        f"/api/v1/content/captions/{content.ids['draft']}/suggestions/{suggestion_id}",
        headers=content.owner,
    )

    assert len(adapter.calls) == 1
    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["failure_reason"] == "provider_error"
    assert "PRIVATE" not in response.text


def test_running_restart_becomes_unknown_without_second_provider_attempt(content, caplog):
    created = _create_suggestion(content)
    suggestion_id = created.json()["id"]
    db.run(
        """UPDATE mobile_caption_suggestions
              SET status='running',provider_attempted_at=datetime('now')
            WHERE id=?""",
        (suggestion_id,),
    )
    adapter = _CapturingProvider(_success_result())
    with caplog.at_level("INFO", logger="mise.mobile_content"):
        _run_with(adapter, suggestion_id)
        _run_with(adapter, suggestion_id)
    row = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (suggestion_id,))

    assert adapter.calls == []
    assert row["status"] == "failed"
    assert row["failure_code"] == "unknown_outcome"
    assert row["context_json"] is None
    assert row["candidate_text"] is None
    assert "reason=unknown_outcome" in caplog.text
    assert suggestion_id not in caplog.text


def test_session_revoke_before_call_and_during_commit_prevents_candidate_write(content):
    before = _create_suggestion(content)
    before_id = before.json()["id"]
    before_row = db.one(
        "SELECT session_id FROM mobile_caption_suggestions WHERE id=?", (before_id,)
    )
    db.run(
        "UPDATE api_sessions SET revoked_at=1,revoke_reason='test' WHERE id=?",
        (before_row["session_id"],),
    )
    before_adapter = _CapturingProvider(_success_result("must not run"))
    _run_with(before_adapter, before_id)
    ended = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (before_id,))
    assert before_adapter.calls == []
    assert ended["status"] == "failed"
    assert ended["failure_code"] == "session_ended"
    assert ended["session_id"] is None
    assert ended["context_json"] is None

    second_owner = _owner_headers(content.client, name="Second Owner iPad")
    detail = content.client.get(
        f"/api/v1/content/captions/{content.ids['second']}", headers=second_owner
    )
    during = content.client.post(
        f"/api/v1/content/captions/{content.ids['second']}/suggestions",
        headers=_command_headers(second_owner, detail.headers["etag"]),
        json={},
    )
    assert during.status_code == 202
    during_id = during.json()["id"]
    during_row = db.one(
        "SELECT session_id FROM mobile_caption_suggestions WHERE id=?",
        (during_id,),
    )

    def revoke_during_provider():
        db.run(
            "UPDATE api_sessions SET revoked_at=2,revoke_reason='test' WHERE id=?",
            (during_row["session_id"],),
        )

    during_adapter = _CapturingProvider(
        _success_result("must not commit"),
        callback=revoke_during_provider,
    )
    _run_with(during_adapter, during_id)
    scrubbed = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (during_id,))
    assert len(during_adapter.calls) == 1
    assert scrubbed["status"] == "failed"
    assert scrubbed["failure_code"] == "session_ended"
    assert scrubbed["session_id"] is None
    assert scrubbed["candidate_text"] is None
    assert scrubbed["provider"] is None
    assert scrubbed["model"] is None


def test_revoking_running_session_keeps_concurrent_slot_until_provider_returns(
    content,
    monkeypatch,
):
    monkeypatch.setattr(config, "MOBILE_CONTENT_CONCURRENT_LIMIT", 1)
    first = _create_suggestion(content, content.ids["draft"])
    first_id = first.json()["id"]
    first_session = db.one(
        "SELECT session_id FROM mobile_caption_suggestions WHERE id=?",
        (first_id,),
    )["session_id"]
    entered = threading.Event()
    release = threading.Event()

    def block_provider():
        entered.set()
        assert release.wait(timeout=5)

    adapter = _CapturingProvider(_success_result("late result"), callback=block_provider)
    second_owner = _owner_headers(content.client, name="Revoking Owner iPad")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_with, adapter, first_id)
        assert entered.wait(timeout=5)
        revoked = content.client.delete(
            f"/api/v1/auth/sessions/{first_session}",
            headers=second_owner,
        )
        assert revoked.status_code == 204
        usage = db.one("SELECT state FROM mobile_caption_usage WHERE id=?", (first_id,))
        assert usage["state"] == "active"
        blocked = _create_suggestion(
            content,
            content.ids["second"],
            headers=second_owner,
        )
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "content.concurrent_limit"
        release.set()
        future.result(timeout=5)

    usage = db.one("SELECT state FROM mobile_caption_usage WHERE id=?", (first_id,))
    assert usage["state"] == "finished"
    accepted = _create_suggestion(
        content,
        content.ids["second"],
        headers=second_owner,
    )
    assert accepted.status_code == 202


@pytest.mark.parametrize("race", ["edit", "approve"])
def test_worker_result_is_stale_after_human_edit_or_approval(content, race):
    def human_change():
        if race == "edit":
            db.run(
                """UPDATE retainer_captions
                      SET body='Human won',revision=revision+1,updated_at=datetime('now')
                    WHERE id=?""",
                (content.ids["draft"],),
            )
        else:
            db.run(
                """UPDATE retainer_captions
                      SET status='approved',revision=revision+1,updated_at=datetime('now')
                    WHERE id=?""",
                (content.ids["draft"],),
            )

    created, polled, adapter = _ready_suggestion(content, callback=human_change)
    assert len(adapter.calls) == 1
    assert polled.status_code == 200
    assert polled.json()["state"] == "ready"
    assert polled.json()["stale"] is True
    current = _detail(content)
    apply = content.client.patch(
        f"/api/v1/content/captions/{content.ids['draft']}",
        headers=_command_headers(content.owner, current.headers["etag"]),
        json={"body": "Generated candidate", "suggestion_id": created.json()["id"]},
    )
    assert apply.status_code == 409
    if race == "edit":
        assert apply.json()["code"] == "content.suggestion_stale"
        assert (
            db.one("SELECT body FROM retainer_captions WHERE id=?", (content.ids["draft"],))["body"]
            == "Human won"
        )
    else:
        assert apply.json()["code"] == "content.caption_not_editable"
        assert (
            db.one("SELECT status FROM retainer_captions WHERE id=?", (content.ids["draft"],))[
                "status"
            ]
            == "approved"
        )


def test_caption_delete_and_same_id_reinsert_cannot_receive_inflight_output(content):
    original = _detail(content).json()

    def replace_caption():
        db.run("DELETE FROM retainer_captions WHERE id=?", (content.ids["draft"],))
        db.run(
            """INSERT INTO retainer_captions
               (id,plan_id,period,label,body,created_at)
               VALUES (?,?,?,?,?,'2026-07-12 10:00:00')""",
            (
                content.ids["draft"],
                content.ids["plan"],
                "2026-07",
                "Replacement",
                "Replacement body",
            ),
        )

    created, polled, adapter = _ready_suggestion(content, callback=replace_caption)
    assert len(adapter.calls) == 1
    assert polled.status_code == 404
    assert polled.json()["code"] == "content.suggestion_not_found"
    replacement = _detail(content).json()
    assert replacement["body"] == "Replacement body"
    assert replacement["revision"] == 0
    assert replacement["version_id"] != original["version_id"]
    assert db.one("SELECT COUNT(*) AS n FROM mobile_caption_suggestions")["n"] == 0
    assert created.json()["candidate_text"] is None


def test_manual_patch_is_strongly_versioned_replayed_once_and_rejects_approved(content):
    before = _detail(content)
    key = uuid.uuid4()
    body = {"body": "  Human edited body 👩‍💻  "}
    first = content.client.patch(
        f"/api/v1/content/captions/{content.ids['draft']}",
        headers=_command_headers(content.owner, before.headers["etag"], key=key),
        json=body,
    )
    replay = content.client.patch(
        f"/api/v1/content/captions/{content.ids['draft']}",
        headers=_command_headers(content.owner, before.headers["etag"], key=key),
        json=body,
    )
    conflict = content.client.patch(
        f"/api/v1/content/captions/{content.ids['draft']}",
        headers=_command_headers(content.owner, before.headers["etag"], key=key),
        json={"body": "Different body"},
    )
    assert first.status_code == replay.status_code == 200
    assert set(first.json()) == _DETAIL_FIELDS
    assert first.json()["body"] == "Human edited body 👩‍💻"
    assert first.json()["revision"] == before.json()["revision"] + 1
    assert first.json()["status"] == "draft"
    assert first.json()["ai_assisted"] is False
    assert first.headers["etag"] != before.headers["etag"]
    assert first.headers["cache-control"] == "no-store"
    assert replay.json() == first.json()
    assert replay.headers["idempotency-replayed"] == "true"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "request.idempotency_conflict"
    assert (
        db.one("SELECT revision FROM retainer_captions WHERE id=?", (content.ids["draft"],))[
            "revision"
        ]
        == 1
    )
    assert db.one("SELECT COUNT(*) AS n FROM audit_log WHERE action='body_updated'")["n"] == 1

    approved = _detail(content, content.ids["approved"])
    denied = content.client.patch(
        f"/api/v1/content/captions/{content.ids['approved']}",
        headers=_command_headers(content.owner, approved.headers["etag"]),
        json={"body": "Must not overwrite"},
    )
    assert denied.status_code == 409
    assert denied.json()["code"] == "content.caption_not_editable"
    assert (
        db.one("SELECT body FROM retainer_captions WHERE id=?", (content.ids["approved"],))["body"]
        == "Approved copy"
    )


def test_explicit_suggestion_apply_is_session_bound_replayed_once_and_non_publishing(content):
    before_business = _business_snapshot(content.ids["draft"])
    _, ready, _ = _ready_suggestion(content, candidate="Original AI candidate")
    suggestion_id = ready.json()["id"]
    second_owner = _owner_headers(content.client, name="Other Owner iPad")
    hidden = content.client.get(
        f"/api/v1/content/captions/{content.ids['draft']}/suggestions/{suggestion_id}",
        headers=second_owner,
    )
    assert hidden.status_code == 404

    current = _detail(content)
    key = uuid.uuid4()
    headers = _command_headers(content.owner, current.headers["etag"], key=key)
    request_body = {
        "body": "Human-reviewed final",
        "suggestion_id": suggestion_id,
    }
    applied = content.client.patch(
        f"/api/v1/content/captions/{content.ids['draft']}",
        headers=headers,
        json=request_body,
    )
    replay = content.client.patch(
        f"/api/v1/content/captions/{content.ids['draft']}",
        headers=headers,
        json=request_body,
    )

    assert applied.status_code == replay.status_code == 200
    assert applied.json()["body"] == "Human-reviewed final"
    assert applied.json()["status"] == "draft"
    assert applied.json()["ai_assisted"] is True
    assert applied.json()["revision"] == current.json()["revision"] + 1
    assert replay.json() == applied.json()
    assert replay.headers["idempotency-replayed"] == "true"
    caption = db.one("SELECT * FROM retainer_captions WHERE id=?", (content.ids["draft"],))
    assert caption["ai_draft_original"] == "Original AI candidate"
    assert caption["ai_model"] == "PRIVATE-MODEL"
    assert caption["status"] == "draft"
    suggestion = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (suggestion_id,))
    assert suggestion["status"] == "applied"
    assert suggestion["candidate_text"] is None
    assert suggestion["context_json"] is None
    assert suggestion["provider"] is None
    assert suggestion["model"] is None
    assert db.one("SELECT COUNT(*) AS n FROM audit_log WHERE action='body_updated'")["n"] == 1
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM mobile_commands WHERE operation LIKE 'content.caption.update:%'"
        )["n"]
        == 1
    )
    after_business = _business_snapshot(content.ids["draft"])
    assert after_business["deliveries"] == before_business["deliveries"]
    assert after_business["invoices"] == before_business["invoices"]
    assert after_business["caption"][1] == before_business["caption"][1] == "draft"

    reuse = content.client.patch(
        f"/api/v1/content/captions/{content.ids['draft']}",
        headers=_command_headers(content.owner, applied.headers["etag"]),
        json=request_body,
    )
    assert reuse.status_code == 409
    assert reuse.json()["code"] == "content.suggestion_not_ready"


def test_expiration_and_owner_revocation_scrub_transient_content(content):
    _, ready, _ = _ready_suggestion(content, candidate="SHORT-LIVED PRIVATE OUTPUT")
    suggestion_id = ready.json()["id"]
    db.run(
        "UPDATE mobile_caption_suggestions SET expires_at=datetime('now','-1 second') WHERE id=?",
        (suggestion_id,),
    )
    expired = content.client.get(
        f"/api/v1/content/captions/{content.ids['draft']}/suggestions/{suggestion_id}",
        headers=content.owner,
    )
    assert expired.status_code == 200
    assert expired.json()["state"] == "expired"
    assert expired.json()["candidate_text"] is None
    assert "PRIVATE" not in expired.text
    row = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (suggestion_id,))
    assert row["context_json"] is None
    assert row["candidate_text"] is None
    assert row["provider"] is None
    assert row["model"] is None
    assert row["failure_code"] is None

    second = _create_suggestion(content, content.ids["second"], instruction="PRIVATE CONTEXT")
    second_id = second.json()["id"]
    session_id = db.one(
        "SELECT session_id FROM mobile_caption_suggestions WHERE id=?", (second_id,)
    )["session_id"]
    other_owner = _owner_headers(content.client, name="Revoking iPad")
    revoked = content.client.delete(
        f"/api/v1/auth/sessions/{session_id}",
        headers=other_owner,
    )
    assert revoked.status_code == 204
    scrubbed = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (second_id,))
    assert scrubbed["session_id"] is None
    assert scrubbed["status"] == "failed"
    assert scrubbed["failure_code"] == "session_ended"
    assert scrubbed["context_json"] is None
    assert scrubbed["candidate_text"] is None
    assert scrubbed["provider"] is None
    assert scrubbed["model"] is None


def test_periodic_sweep_scrubs_untouched_expired_ready_candidate(
    content,
    monkeypatch,
):
    created = _create_suggestion(content)
    suggestion_id = created.json()["id"]
    adapter = _CapturingProvider(_success_result("UNTOUCHED PRIVATE CANDIDATE"))
    _run_with(adapter, suggestion_id)
    ready = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (suggestion_id,))
    assert ready["status"] == "ready"
    assert ready["candidate_text"] == "UNTOUCHED PRIVATE CANDIDATE"
    assert ready["provider"] == "PRIVATE-PROVIDER"
    assert ready["model"] == "PRIVATE-MODEL"
    db.run(
        "UPDATE mobile_caption_suggestions SET expires_at=datetime('now','-1 second') WHERE id=?",
        (suggestion_id,),
    )

    checkpoints = []
    monkeypatch.setattr(
        db,
        "checkpoint_truncate",
        lambda: checkpoints.append("called") or True,
    )
    mobile_content_api.sweep_expired_suggestions()

    expired = db.one("SELECT * FROM mobile_caption_suggestions WHERE id=?", (suggestion_id,))
    assert expired["status"] == "expired"
    assert expired["context_json"] is None
    assert expired["candidate_text"] is None
    assert expired["provider"] is None
    assert expired["model"] is None
    assert expired["failure_code"] is None
    assert checkpoints == ["called"]


def test_periodic_sweep_skips_checkpoint_when_nothing_changed(content, monkeypatch):
    monkeypatch.setattr(
        db,
        "checkpoint_truncate",
        lambda: pytest.fail("unchanged sweep must not checkpoint the WAL"),
    )

    mobile_content_api.sweep_expired_suggestions()


def _configure_hosted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "mobile-content-hosted-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "MOBILE_CONTENT_SUGGESTIONS", True)
    monkeypatch.setattr(config, "MOBILE_CONTENT_DAILY_LIMIT", 1)
    monkeypatch.setattr(config, "MOBILE_CONTENT_CONCURRENT_LIMIT", 1)
    monkeypatch.setattr(config, "MOBILE_CONTENT_SUGGESTION_TTL_HOURS", 24)
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_URL", "https://provider.test/caption")
    monkeypatch.setattr(config, "ODYSSEUS_CAPTION_TOKEN", "provider-test-token")
    ratelimit._hits.clear()
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def test_hosted_overlapping_ids_sessions_quotas_and_cursors_are_tenant_isolated(
    tmp_path,
    monkeypatch,
):
    _configure_hosted(tmp_path, monkeypatch)
    monkeypatch.setattr(jobs, "kick", lambda _job_id: None)
    alpha = saas.create_tenant(
        "alpha",
        "Alpha Studio",
        "owner@alpha.test",
        "alpha-password",
    )
    beta = saas.create_tenant(
        "beta",
        "Beta Studio",
        "owner@beta.test",
        "beta-password",
    )
    with saas.tenant_runtime(alpha):
        alpha_ids = _seed_content("Alpha ")
    with saas.tenant_runtime(beta):
        beta_ids = _seed_content("Beta ")
    assert alpha_ids["draft"] == beta_ids["draft"] == 1
    assert alpha_ids["second"] == beta_ids["second"] == 2

    alpha_client = TestClient(app, base_url="https://alpha.mise.test")
    beta_client = TestClient(app, base_url="https://beta.mise.test")
    try:
        alpha_owner = _owner_headers(
            alpha_client,
            email="owner@alpha.test",
            password="alpha-password",
            name="Alpha iPhone",
        )
        beta_owner = _owner_headers(
            beta_client,
            email="owner@beta.test",
            password="beta-password",
            name="Beta iPhone",
        )
        alpha_page = alpha_client.get(
            "/api/v1/content/captions?limit=1",
            headers=alpha_owner,
        )
        beta_page = beta_client.get(
            "/api/v1/content/captions?limit=1",
            headers=beta_owner,
        )
        assert alpha_page.status_code == beta_page.status_code == 200
        assert alpha_page.json()["items"][0]["label"] == "Alpha Approved"
        assert beta_page.json()["items"][0]["label"] == "Beta Approved"
        assert beta_client.get("/api/v1/content/captions", headers=alpha_owner).status_code == 401
        cross_cursor = beta_client.get(
            "/api/v1/content/captions",
            params={"limit": 1, "cursor": alpha_page.json()["next_cursor"]},
            headers=beta_owner,
        )
        assert cross_cursor.status_code == 422
        assert cross_cursor.json()["code"] == "pagination.invalid_cursor"

        shared_key = uuid.uuid4()
        alpha_detail = alpha_client.get("/api/v1/content/captions/1", headers=alpha_owner)
        beta_detail = beta_client.get("/api/v1/content/captions/1", headers=beta_owner)
        alpha_suggestion = alpha_client.post(
            "/api/v1/content/captions/1/suggestions",
            headers=_command_headers(alpha_owner, alpha_detail.headers["etag"], key=shared_key),
            json={"instruction": "Alpha only"},
        )
        beta_suggestion = beta_client.post(
            "/api/v1/content/captions/1/suggestions",
            headers=_command_headers(beta_owner, beta_detail.headers["etag"], key=shared_key),
            json={"instruction": "Beta only"},
        )
        assert alpha_suggestion.status_code == beta_suggestion.status_code == 202
        assert alpha_suggestion.json()["id"] != beta_suggestion.json()["id"]
        hidden = beta_client.get(
            f"/api/v1/content/captions/1/suggestions/{alpha_suggestion.json()['id']}",
            headers=beta_owner,
        )
        assert hidden.status_code == 404
        with saas.tenant_runtime(alpha):
            alpha_row = db.one("SELECT context_json FROM mobile_caption_suggestions")
            assert json.loads(alpha_row["context_json"])["instruction"] == "Alpha only"
        with saas.tenant_runtime(beta):
            beta_row = db.one("SELECT context_json FROM mobile_caption_suggestions")
            assert json.loads(beta_row["context_json"])["instruction"] == "Beta only"
    finally:
        alpha_client.close()
        beta_client.close()
        ratelimit._hits.clear()


def test_content_openapi_has_exact_models_parameters_and_response_headers(content):
    schema_response = content.client.get("/api/v1/openapi.json")
    assert schema_response.status_code == 200
    schema = schema_response.json()
    paths = schema["paths"]
    expected_paths = {
        "/content/captions": {"get"},
        "/content/captions/{caption_id}": {"get", "patch"},
        "/content/captions/{caption_id}/suggestions": {"post"},
        "/content/captions/{caption_id}/suggestions/{suggestion_id}": {"get"},
    }
    assert {
        path: set(paths[path]) for path in paths if path.startswith("/content/")
    } == expected_paths

    models = schema["components"]["schemas"]
    expected_model_fields = {
        "ContentCaptionSummary": _SUMMARY_FIELDS,
        "ContentCaptionPage": _PAGE_FIELDS,
        "ContentCaptionDetail": _DETAIL_FIELDS,
        "CaptionSuggestionRequest": {"instruction"},
        "CaptionBodyUpdate": {"body", "suggestion_id"},
        "CaptionSuggestion": _SUGGESTION_FIELDS,
    }
    for name, fields in expected_model_fields.items():
        assert set(models[name]["properties"]) == fields
        assert models[name]["additionalProperties"] is False

    operations = {
        ("/content/captions", "get"): {
            ("cursor", "query", False),
            ("limit", "query", False),
            ("Authorization", "header", True),
            ("If-None-Match", "header", False),
        },
        ("/content/captions/{caption_id}", "get"): {
            ("caption_id", "path", True),
            ("Authorization", "header", True),
            ("If-None-Match", "header", False),
        },
        ("/content/captions/{caption_id}/suggestions", "post"): {
            ("caption_id", "path", True),
            ("Authorization", "header", True),
            ("If-Match", "header", True),
            ("Idempotency-Key", "header", True),
        },
        ("/content/captions/{caption_id}/suggestions/{suggestion_id}", "get"): {
            ("caption_id", "path", True),
            ("suggestion_id", "path", True),
            ("Authorization", "header", True),
        },
        ("/content/captions/{caption_id}", "patch"): {
            ("caption_id", "path", True),
            ("Authorization", "header", True),
            ("If-Match", "header", True),
            ("Idempotency-Key", "header", True),
        },
    }
    for (path, method), expected in operations.items():
        parameters = {
            (item["name"], item["in"], bool(item.get("required")))
            for item in paths[path][method]["parameters"]
        }
        assert parameters == expected

    expected_headers = {
        ("/content/captions", "get", "200"): {"Cache-Control", "ETag", "Vary"},
        ("/content/captions", "get", "304"): {"Cache-Control", "ETag", "Vary"},
        ("/content/captions/{caption_id}", "get", "200"): {"Cache-Control", "ETag", "Vary"},
        ("/content/captions/{caption_id}", "get", "304"): {
            "Cache-Control",
            "ETag",
            "Vary",
        },
        ("/content/captions/{caption_id}/suggestions", "post", "202"): {
            "Cache-Control",
            "Idempotency-Replayed",
            "Location",
            "Vary",
        },
        ("/content/captions/{caption_id}/suggestions/{suggestion_id}", "get", "200"): {
            "Cache-Control",
            "Vary",
        },
        ("/content/captions/{caption_id}", "patch", "200"): {
            "Cache-Control",
            "ETag",
            "Idempotency-Replayed",
            "Vary",
        },
    }
    for (path, method, status), headers in expected_headers.items():
        documented = set(paths[path][method]["responses"][status].get("headers", {}))
        assert documented == headers
