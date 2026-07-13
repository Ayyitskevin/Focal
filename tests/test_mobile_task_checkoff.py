"""Native owner task check-off — the first /api/v1 owner mutation (M4a / queue S6).

Contract: PUT/DELETE on a task's completion sub-resource is server-authoritative and
idempotent (a repeat call is a safe no-op returning current state), each real
transition writes exactly one audit_log row, and the write requires an owner bearer
carrying studio:write — a guest or a read-only owner token is refused.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import config, db, mobile_auth, ratelimit
from app.main import app

pytestmark = pytest.mark.unit


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "task-api-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "TIMEZONE", "UTC")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    login = client.post(
        "/api/v1/auth/studio/login",
        json={
            "email": None,
            "password": "owner-password",
            "device": {
                "installation_id": "A8A06DC2-2034-4E3B-B07D-0CBFD2455B98",
                "name": "Owner iPhone",
                "platform": "ios",
                "app_version": "1.0",
            },
        },
    )
    token = login.json()["access_token"]
    yield client, {"Authorization": f"Bearer {token}"}
    client.close()
    ratelimit._hits.clear()


def _make_task(title: str = "Cull the Rossi tasting menu") -> int:
    return db.run("INSERT INTO tasks (title) VALUES (?)", (title,))


def _audit_rows(task_id: int) -> list:
    return db.all_(
        "SELECT action, actor, diff_json FROM audit_log WHERE entity_type='task' AND entity_id=?"
        " ORDER BY id",
        (task_id,),
    )


def test_mark_done_sets_state_and_audits(owner):
    client, headers = owner
    task_id = _make_task()

    resp = client.put(f"/api/v1/tasks/{task_id}/completion", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == task_id
    assert body["done"] is True
    assert body["completed_at"] is not None  # server-stamped

    # The row actually flipped, and exactly one audit row was written.
    assert db.one("SELECT done FROM tasks WHERE id=?", (task_id,))["done"] == 1
    rows = _audit_rows(task_id)
    assert len(rows) == 1
    assert rows[0]["action"] == "complete"
    assert rows[0]["actor"] == "owner"


def test_repeat_check_off_is_idempotent(owner):
    client, headers = owner
    task_id = _make_task()

    first = client.put(f"/api/v1/tasks/{task_id}/completion", headers=headers).json()
    second = client.put(f"/api/v1/tasks/{task_id}/completion", headers=headers)
    assert second.status_code == 200
    # Same terminal state, and the completed_at from the FIRST transition is preserved
    # (a no-op does not re-stamp the clock).
    assert second.json()["done"] is True
    assert second.json()["completed_at"] == first["completed_at"]
    # Idempotent: the second call added no second audit row.
    assert len(_audit_rows(task_id)) == 1


def test_reopen_clears_state_and_audits(owner):
    client, headers = owner
    task_id = _make_task()
    client.put(f"/api/v1/tasks/{task_id}/completion", headers=headers)

    resp = client.delete(f"/api/v1/tasks/{task_id}/completion", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["done"] is False
    assert resp.json()["completed_at"] is None
    assert db.one("SELECT done, done_at FROM tasks WHERE id=?", (task_id,))["done_at"] is None

    # Reopening an already-open task is a no-op (no third audit row).
    again = client.delete(f"/api/v1/tasks/{task_id}/completion", headers=headers)
    assert again.status_code == 200
    actions = [r["action"] for r in _audit_rows(task_id)]
    assert actions == ["complete", "reopen"]


def test_unknown_task_is_404(owner):
    client, headers = owner
    resp = client.put("/api/v1/tasks/999999/completion", headers=headers)
    assert resp.status_code == 404


def test_write_requires_bearer(owner):
    client, _ = owner
    task_id = _make_task()
    assert client.put(f"/api/v1/tasks/{task_id}/completion").status_code == 401


def test_guest_principal_is_refused(owner, monkeypatch):
    client, headers = owner
    task_id = _make_task()
    guest = mobile_auth.Principal(
        session_id="s",
        tenant_key="self:https://studio.test",
        kind=mobile_auth.GALLERY_GUEST,
        resource_id=1,
        resource_variant=None,
        gallery_visitor_id=1,
        scopes=frozenset({"gallery:1:favorite"}),
        device_name=None,
        device_platform=None,
        device_app_version=None,
        created_at=dt.datetime.now(dt.UTC),
        absolute_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *a, **k: guest)
    resp = client.put(f"/api/v1/tasks/{task_id}/completion", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "auth.insufficient_scope"
    assert db.one("SELECT done FROM tasks WHERE id=?", (task_id,))["done"] == 0


def test_read_only_owner_cannot_write(owner, monkeypatch):
    client, headers = owner
    task_id = _make_task()
    read_only = mobile_auth.Principal(
        session_id="s",
        tenant_key="self:https://studio.test",
        kind=mobile_auth.STUDIO_OWNER,
        resource_id=None,
        resource_variant=None,
        gallery_visitor_id=None,
        scopes=frozenset({"studio:read"}),  # no studio:write
        device_name=None,
        device_platform=None,
        device_app_version=None,
        created_at=dt.datetime.now(dt.UTC),
        absolute_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *a, **k: read_only)
    resp = client.put(f"/api/v1/tasks/{task_id}/completion", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "auth.insufficient_scope"
    assert db.one("SELECT done FROM tasks WHERE id=?", (task_id,))["done"] == 0
