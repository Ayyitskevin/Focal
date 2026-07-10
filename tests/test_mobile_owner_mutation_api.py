"""Milestone 4A native owner mutation contracts."""

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from app import audit, config, db, mobile_auth, mobile_owner_mutation_api, ratelimit, workflows
from app.main import app

pytestmark = pytest.mark.unit


@pytest.fixture
def writer(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "owner-write-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "TIMEZONE", "UTC")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(
        mobile_owner_mutation_api.admin_studio, "_today", lambda: dt.date(2026, 7, 10)
    )
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    login = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": None,
            "password": "owner-password",
            "device": {
                "installation_id": "6127F7F8-69D5-4F25-9D42-0F24C97CB3BE",
                "name": "Owner iPhone",
                "platform": "ios",
                "app_version": "1.0",
            },
        },
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    yield client, {"Authorization": f"Bearer {token}"}
    client.close()
    ratelimit._hits.clear()


def _command_headers(headers: dict[str, str], key: uuid.UUID | None = None) -> dict[str, str]:
    return {**headers, "Idempotency-Key": str(key or uuid.uuid4())}


def _client_body(name: str = "Avery") -> dict:
    return {
        "name": name,
        "company": "Avery Foods",
        "email": "avery@example.test",
        "phone": "+1 555 0100",
        "notes": "Prefers morning reviews",
        "usage_rights": "North America digital",
        "market": "asheville",
    }


def _create_client(client: TestClient, headers: dict[str, str], name: str = "Avery") -> dict:
    response = client.post(
        "/api/v1/clients",
        headers=_command_headers(headers),
        json=_client_body(name),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_writes_require_exact_owner_scope_and_idempotency(writer, monkeypatch):
    client, headers = writer
    missing = client.post("/api/v1/clients", headers=headers, json=_client_body())
    assert missing.status_code == 422
    assert missing.json()["code"] == "request.idempotency_required"

    guest = mobile_auth.Principal(
        session_id="guest-session",
        tenant_key="self:https://studio.test",
        kind=mobile_auth.GALLERY_GUEST,
        resource_id=1,
        resource_variant=None,
        gallery_visitor_id=1,
        scopes=frozenset({"studio:read", "studio:write"}),
        device_name=None,
        device_platform=None,
        device_app_version=None,
        created_at=dt.datetime.now(dt.UTC),
        absolute_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *args, **kwargs: guest)
    denied = client.post(
        "/api/v1/clients",
        headers=_command_headers(headers),
        json=_client_body(),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "auth.insufficient_scope"


def test_client_create_is_session_idempotent_and_audited_once(writer):
    client, headers = writer
    key = uuid.uuid4()
    first = client.post(
        "/api/v1/clients",
        headers=_command_headers(headers, key),
        json=_client_body(),
    )
    replay = client.post(
        "/api/v1/clients",
        headers=_command_headers(headers, key),
        json=_client_body(),
    )
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert replay.headers["idempotency-replayed"] == "true"
    assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM mobile_commands")["n"] == 1
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='client' AND action='create'"
        )["n"]
        == 1
    )

    conflict = client.post(
        "/api/v1/clients",
        headers=_command_headers(headers, key),
        json=_client_body("Different"),
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "request.idempotency_conflict"
    assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 1


def test_client_update_uses_etag_and_replays_after_state_change(writer):
    client, headers = writer
    created = _create_client(client, headers)
    detail = client.get(f"/api/v1/clients/{created['id']}", headers=headers)
    assert detail.status_code == 200
    stale_etag = '"client-stale"'
    rejected = client.patch(
        f"/api/v1/clients/{created['id']}",
        headers={**_command_headers(headers), "If-Match": stale_etag},
        json={**_client_body(), "notes": "Should not land"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "resource.version_conflict"
    assert db.one("SELECT notes FROM clients WHERE id=?", (created["id"],))["notes"] == (
        "Prefers morning reviews"
    )

    for unsafe_match in ("*", f"W/{detail.headers['etag']}"):
        rejected = client.patch(
            f"/api/v1/clients/{created['id']}",
            headers={
                **_command_headers(headers),
                "If-Match": unsafe_match,
            },
            json={**_client_body(), "notes": "Should not land"},
        )
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "resource.version_conflict"
    assert (
        db.one(
            "SELECT notes FROM clients WHERE id=?",
            (created["id"],),
        )["notes"]
        == "Prefers morning reviews"
    )

    key = uuid.uuid4()
    body = {**_client_body(), "notes": "Updated from iPhone"}
    request_headers = {
        **_command_headers(headers, key),
        "If-Match": detail.headers["etag"],
    }
    updated = client.patch(f"/api/v1/clients/{created['id']}", headers=request_headers, json=body)
    replay = client.patch(f"/api/v1/clients/{created['id']}", headers=request_headers, json=body)
    assert updated.status_code == replay.status_code == 200
    assert updated.json()["notes"] == "Updated from iPhone"
    assert replay.json() == updated.json()
    assert replay.headers["idempotency-replayed"] == "true"
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='client' AND action='update'"
        )["n"]
        == 1
    )


def test_project_status_effect_is_recoverable_and_not_duplicated(writer, monkeypatch):
    client, headers = writer
    owner = _create_client(client, headers)
    project = client.post(
        "/api/v1/projects",
        headers=_command_headers(headers),
        json={"client_id": owner["id"], "title": "Summer launch"},
    )
    assert project.status_code == 201
    project_id = project.json()["id"]
    detail = client.get(f"/api/v1/projects/{project_id}", headers=headers)
    key = uuid.uuid4()
    body = {
        "title": "Summer launch",
        "status": "consultation_call",
        "notes": "Discovery complete",
        "shoot_on": "2026-08-15",
    }

    attempts = []

    def fail_once(trigger, project, **kwargs):
        attempts.append((trigger, project))
        raise RuntimeError("temporary workflow failure")

    monkeypatch.setattr(workflows, "fire_workflow", fail_once)
    changed = client.patch(
        f"/api/v1/projects/{project_id}",
        headers={**_command_headers(headers, key), "If-Match": detail.headers["etag"]},
        json=body,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == "consultation_call"
    assert (
        db.one(
            "SELECT effects_completed_at FROM mobile_commands WHERE idempotency_key=?", (str(key),)
        )["effects_completed_at"]
        is None
    )

    completed = []
    monkeypatch.setattr(
        workflows,
        "fire_workflow",
        lambda trigger, project, **kwargs: completed.append((trigger, project)),
    )
    replay = client.patch(
        f"/api/v1/projects/{project_id}",
        headers={**_command_headers(headers, key), "If-Match": detail.headers["etag"]},
        json=body,
    )
    assert replay.status_code == 200
    assert replay.headers["idempotency-replayed"] == "true"
    assert completed == [("status:consultation_call", project_id)]
    assert (
        db.one(
            "SELECT effects_completed_at FROM mobile_commands WHERE idempotency_key=?", (str(key),)
        )["effects_completed_at"]
        is not None
    )
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM audit_log WHERE entity_type='project' AND action='update'"
        )["n"]
        == 1
    )


def test_task_create_update_delete_are_audited_and_replay_safe(writer):
    client, headers = writer
    owner = _create_client(client, headers)
    project = client.post(
        "/api/v1/projects",
        headers=_command_headers(headers),
        json={"client_id": owner["id"], "title": "Campaign"},
    ).json()
    create_key = uuid.uuid4()
    task_body = {"title": "Prepare selects", "due_on": "2026-07-09", "project_id": project["id"]}
    created = client.post(
        "/api/v1/tasks",
        headers=_command_headers(headers, create_key),
        json=task_body,
    )
    assert created.status_code == 201, created.text
    assert created.json()["is_overdue"] is True
    task_id = created.json()["id"]

    detail = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    update_key = uuid.uuid4()
    updated = client.patch(
        f"/api/v1/tasks/{task_id}",
        headers={**_command_headers(headers, update_key), "If-Match": detail.headers["etag"]},
        json={**task_body, "done": True},
    )
    assert updated.status_code == 200
    assert updated.json()["done"] is True
    assert updated.json()["completed_at"].endswith("Z")

    latest = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    delete_key = uuid.uuid4()
    delete_headers = {
        **_command_headers(headers, delete_key),
        "If-Match": latest.headers["etag"],
    }
    deleted = client.delete(f"/api/v1/tasks/{task_id}", headers=delete_headers)
    replay = client.delete(f"/api/v1/tasks/{task_id}", headers=delete_headers)
    assert deleted.status_code == replay.status_code == 200
    assert replay.headers["idempotency-replayed"] == "true"
    assert client.get(f"/api/v1/tasks/{task_id}", headers=headers).status_code == 404
    actions = db.all_(
        "SELECT action FROM audit_log WHERE entity_type='task' AND entity_id=? ORDER BY id",
        (task_id,),
    )
    assert [row["action"] for row in actions] == ["create", "update", "delete"]


def test_audit_failure_rolls_back_business_write_and_command(writer, monkeypatch):
    client, headers = writer

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(audit, "log", fail_audit)
    response = client.post(
        "/api/v1/clients", headers=_command_headers(headers), json=_client_body()
    )
    assert response.status_code == 500
    assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM mobile_commands")["n"] == 0
