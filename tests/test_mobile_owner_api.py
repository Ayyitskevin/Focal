"""Focused native owner-read API contracts."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import config, db, mobile_api_helpers, mobile_auth, mobile_owner_api, ratelimit
from app.main import app

pytestmark = pytest.mark.unit


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "owner-api-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "TIMEZONE", "UTC")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(mobile_owner_api, "_studio_today", lambda: dt.date(2026, 7, 10))
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
    yield client, {"Authorization": f"Bearer {token}"}, token
    client.close()
    ratelimit._hits.clear()


def test_requires_exact_owner_principal(owner, monkeypatch):
    client, headers, _ = owner
    assert client.get("/api/v1/clients").status_code == 401
    assert client.get("/api/v1/clients", headers=headers).status_code == 200
    guest = mobile_auth.Principal(
        session_id="s",
        tenant_key="self:https://studio.test",
        kind=mobile_auth.GALLERY_GUEST,
        resource_id=1,
        resource_variant=None,
        gallery_visitor_id=1,
        scopes=frozenset({"studio:read"}),
        device_name=None,
        device_platform=None,
        device_app_version=None,
        created_at=dt.datetime.now(dt.UTC),
        absolute_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *args, **kwargs: guest)
    response = client.get("/api/v1/clients", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "auth.insufficient_scope"


def test_safe_cursor_pages_revalidate_and_reauthorize(owner):
    client, headers, token = owner
    for index in range(27):
        db.run(
            "INSERT INTO clients (name, notes) VALUES (?,?)",
            (f"Client {index:02d}", "PRIVATE-NOTES-MUST-NOT-LEAK"),
        )
    client_id = db.one("SELECT MAX(id) AS id FROM clients")["id"]
    db.run(
        "INSERT INTO portals (client_id, slug, pin, published) VALUES (?,?,?,1)",
        (client_id, "internal-portal", "9831"),
    )
    project_id = db.run(
        """INSERT INTO projects
           (client_id,title,status,notes,notion_page_id,shoot_date,
            workspace_slug,workspace_pin,workspace_published)
           VALUES (?,?,?,?,?,?,?,?,1)""",
        (
            client_id,
            "Native Launch",
            "session_planning",
            "PRIVATE-PROJECT-NOTES",
            "PRIVATE-NOTION-ID",
            "2026-08-14",
            "internal-workspace",
            "7719",
        ),
    )
    first = client.get("/api/v1/clients", headers=headers)
    assert first.status_code == 200
    assert len(first.json()["items"]) == 25
    assert first.json()["has_more"] is True
    assert first.headers["cache-control"] == "private, no-cache"
    assert "PRIVATE-NOTES-MUST-NOT-LEAK" not in first.text
    assert "pin" not in first.text
    cached = client.get(
        "/api/v1/clients", headers={**headers, "If-None-Match": first.headers["etag"]}
    )
    assert cached.status_code == 304
    cursor = first.json()["next_cursor"]
    tampered = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    invalid = client.get("/api/v1/clients", params={"cursor": tampered}, headers=headers)
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "pagination.invalid_cursor"
    second = client.get(
        "/api/v1/clients",
        params={"cursor": cursor},
        headers=headers,
    )
    assert len(second.json()["items"]) == 2
    project_response = client.get("/api/v1/projects", headers=headers)
    project = project_response.json()["items"][0]
    assert project["id"] == project_id
    assert project["shoot_on"] == "2026-08-14"
    assert project["workspace_published"] is True
    assert "PRIVATE-PROJECT-NOTES" not in project_response.text
    assert "PRIVATE-NOTION-ID" not in project_response.text
    assert "workspace_pin" not in project_response.text
    client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert (
        client.get(
            "/api/v1/clients",
            params={"cursor": cursor},
            headers=headers,
        ).status_code
        == 401
    )


def test_owner_collection_cursor_wire_is_unchanged(owner):
    assert (
        mobile_owner_api._encode_cursor("clients", 42) == "djE6Y2xpZW50czo0MhZyvWITAsrLfMWlbQdd8E0"
    )
    assert (
        mobile_owner_api._encode_cursor("projects", 42)
        == "djE6cHJvamVjdHM6NDLuNakQooiyA-NHUzxITS5t"
    )


def test_dashboard_authoritative_money_dates_and_etag(owner):
    client, headers, _ = owner
    today = mobile_owner_api._studio_today()
    cid = db.run("INSERT INTO clients (name, company) VALUES ('Casey','Case Co')")
    pid = db.run(
        "INSERT INTO projects (client_id,title,status,shoot_date) VALUES (?,?,?,?)",
        (cid, "Campaign", "session_planning", (today + dt.timedelta(days=3)).isoformat()),
    )
    db.run("INSERT INTO inquiries (name,email,message) VALUES ('Lead','lead@example.test','Hi')")
    db.run(
        "INSERT INTO tasks (title,due_date,project_id) VALUES (?,?,?)",
        ("Late edit", (today - dt.timedelta(days=1)).isoformat(), pid),
    )
    db.run(
        """INSERT INTO invoices
           (project_id,slug,title,total_cents,due_date,status)
           VALUES (?,?,?,?,?,'sent')""",
        (pid, "overdue", "Overdue", 10_000, (today - dt.timedelta(days=1)).isoformat()),
    )
    db.run(
        """INSERT INTO invoices
           (project_id,slug,title,total_cents,deposit_cents,due_date,status)
           VALUES (?,?,?,?,?,?,'deposit_paid')""",
        (pid, "deposit", "Deposit", 20_000, 5_000, (today + dt.timedelta(days=1)).isoformat()),
    )
    response = client.get("/api/v1/dashboard", headers=headers)
    body = response.json()
    assert response.status_code == 200
    assert body["outstanding"] == {
        "count": 2,
        "amount": {"minor_units": 25_000, "currency_code": "USD"},
    }
    assert body["upcoming_projects_14_days"] == 1
    assert body["overdue_invoice_count"] == 1
    assert body["tasks_due_count"] == 1
    assert [item["balance"]["minor_units"] for item in body["open_invoices"]] == [
        10_000,
        15_000,
    ]
    assert body["upcoming_shoots"][0]["shoot_on"] == (today + dt.timedelta(days=3)).isoformat()
    assert body["generated_at"].endswith("Z")
    cached = client.get(
        "/api/v1/dashboard", headers={**headers, "If-None-Match": response.headers["etag"]}
    )
    assert cached.status_code == 304


def _principal(
    *,
    kind: str = mobile_auth.STUDIO_OWNER,
    scopes: frozenset[str],
    resource_id: int | None = None,
    gallery_visitor_id: int | None = None,
) -> mobile_auth.Principal:
    return mobile_auth.Principal(
        session_id="read-only-session",
        tenant_key="self:https://studio.test",
        kind=kind,
        resource_id=resource_id,
        resource_variant=None,
        gallery_visitor_id=gallery_visitor_id,
        scopes=scopes,
        device_name="Owner iPad",
        device_platform="ios",
        device_app_version="1.0",
        created_at=dt.datetime.now(dt.UTC),
        absolute_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
    )


def _insert_task(
    title: str,
    *,
    due_on: str | None,
    project_id: int | None = None,
    done: bool = False,
) -> int:
    return db.run(
        "INSERT INTO tasks (title,due_date,project_id,done) VALUES (?,?,?,?)",
        (title, due_on, project_id, int(done)),
    )


def test_open_tasks_keyset_pages_are_complete_private_and_reauthorized(owner):
    client, headers, token = owner
    client_id = db.run("INSERT INTO clients (name) VALUES ('Task client')")
    project_id = db.run(
        "INSERT INTO projects (client_id,title,status) VALUES (?,?,?)",
        (client_id, "Task project", "session_planning"),
    )
    overdue_first = _insert_task("Overdue first", due_on="2026-07-08")
    overdue_second = _insert_task("Overdue second", due_on="2026-07-08")
    today_first = _insert_task("Today first", due_on="2026-07-10", project_id=project_id)
    today_second = _insert_task("Today second", due_on="2026-07-10")
    upcoming = _insert_task("Upcoming", due_on="2026-07-12")
    undated_first = _insert_task("Undated first", due_on=None)
    undated_second = _insert_task("Undated second", due_on=None)
    completed = _insert_task("Completed private task", due_on="2026-07-07", done=True)

    expected_ids = [
        overdue_second,
        overdue_first,
        today_second,
        today_first,
        upcoming,
        undated_second,
        undated_first,
    ]
    observed: list[dict] = []
    seen_cursors: set[str] = set()
    cursor = None
    first_cursor = None

    while True:
        params = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get("/api/v1/tasks", params=params, headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-cache"
        assert response.headers["vary"] == "Authorization"
        assert response.headers["etag"].startswith('W/"')
        body = response.json()
        observed.extend(body["items"])
        if first_cursor is None:
            first_cursor = body["next_cursor"]
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        cursor = body["next_cursor"]
        assert cursor and cursor not in seen_cursors
        seen_cursors.add(cursor)

    assert [item["id"] for item in observed] == expected_ids
    assert completed not in {item["id"] for item in observed}
    assert all(
        set(item) == {"id", "title", "due_on", "project_id", "project_title", "is_overdue"}
        for item in observed
    )
    assert [item["is_overdue"] for item in observed] == [
        True,
        True,
        False,
        False,
        False,
        False,
        False,
    ]
    project_item = next(item for item in observed if item["id"] == today_first)
    assert project_item["project_title"] == "Task project"
    assert next(item for item in observed if item["id"] == undated_first)["project_id"] is None

    client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert first_cursor is not None
    reauthorized = client.get(
        "/api/v1/tasks",
        params={"cursor": first_cursor, "limit": 2},
        headers=headers,
    )
    assert reauthorized.status_code == 401


def test_open_tasks_etag_changes_on_completion_and_reopen(owner):
    client, headers, _ = owner
    task_id = _insert_task("Confirm the final timeline", due_on="2026-07-11")

    initial = client.get("/api/v1/tasks", headers=headers)
    assert initial.status_code == 200
    initial_etag = initial.headers["etag"]
    assert [item["id"] for item in initial.json()["items"]] == [task_id]
    cached = client.get(
        "/api/v1/tasks",
        headers={**headers, "If-None-Match": initial_etag},
    )
    assert cached.status_code == 304
    assert cached.headers["cache-control"] == "private, no-cache"
    assert cached.headers["vary"] == "Authorization"
    assert cached.headers["etag"] == initial_etag

    assert client.put(f"/api/v1/tasks/{task_id}/completion", headers=headers).status_code == 200
    completed = client.get(
        "/api/v1/tasks",
        headers={**headers, "If-None-Match": initial_etag},
    )
    assert completed.status_code == 200
    assert completed.json()["items"] == []
    assert completed.headers["etag"] != initial_etag

    assert client.delete(f"/api/v1/tasks/{task_id}/completion", headers=headers).status_code == 200
    reopened = client.get(
        "/api/v1/tasks",
        headers={**headers, "If-None-Match": completed.headers["etag"]},
    )
    assert reopened.status_code == 200
    assert [item["id"] for item in reopened.json()["items"]] == [task_id]
    assert reopened.headers["etag"] == initial_etag


def test_open_tasks_overdue_state_uses_studio_day_and_changes_etag(owner, monkeypatch):
    client, headers, _ = owner
    task_id = _insert_task("Deliver today", due_on="2026-07-10")

    today = client.get("/api/v1/tasks", headers=headers)
    assert today.status_code == 200
    assert today.json()["items"] == [
        {
            "id": task_id,
            "title": "Deliver today",
            "due_on": "2026-07-10",
            "project_id": None,
            "project_title": None,
            "is_overdue": False,
        }
    ]

    monkeypatch.setattr(mobile_owner_api, "_studio_today", lambda: dt.date(2026, 7, 11))
    tomorrow = client.get(
        "/api/v1/tasks",
        headers={**headers, "If-None-Match": today.headers["etag"]},
    )
    assert tomorrow.status_code == 200
    assert tomorrow.json()["items"][0]["is_overdue"] is True
    assert tomorrow.headers["etag"] != today.headers["etag"]


def test_open_tasks_accepts_read_only_owner_but_refuses_missing_or_guest(owner, monkeypatch):
    client, headers, _ = owner
    task_id = _insert_task("Read-only task", due_on=None)
    assert client.get("/api/v1/tasks").status_code == 401

    read_only = _principal(scopes=frozenset({"studio:read"}))
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *args, **kwargs: read_only)
    assert client.get("/api/v1/tasks", headers=headers).status_code == 200
    denied_write = client.put(f"/api/v1/tasks/{task_id}/completion", headers=headers)
    assert denied_write.status_code == 403
    assert denied_write.json()["code"] == "auth.insufficient_scope"

    no_read_scope = _principal(scopes=frozenset({"studio:write"}))

    def refuse_missing_read_scope(*_args, required_scopes=(), **_kwargs):
        assert required_scopes == ("studio:read",)
        assert "studio:read" not in no_read_scope.scopes
        raise mobile_auth.MobileAuthError(
            403,
            "auth.insufficient_scope",
            "This token does not grant the required scope.",
        )

    monkeypatch.setattr(mobile_auth, "authenticate_request", refuse_missing_read_scope)
    denied_scope = client.get("/api/v1/tasks", headers=headers)
    assert denied_scope.status_code == 403
    assert denied_scope.json()["code"] == "auth.insufficient_scope"

    guest = _principal(
        kind=mobile_auth.GALLERY_GUEST,
        resource_id=1,
        gallery_visitor_id=1,
        scopes=frozenset({"studio:read"}),
    )
    monkeypatch.setattr(mobile_auth, "authenticate_request", lambda *args, **kwargs: guest)
    denied_read = client.get("/api/v1/tasks", headers=headers)
    assert denied_read.status_code == 403
    assert denied_read.json()["code"] == "auth.insufficient_scope"


@pytest.mark.parametrize("limit", [0, 101])
def test_open_tasks_enforces_limit_bounds(owner, limit):
    client, headers, _ = owner
    response = client.get("/api/v1/tasks", params={"limit": limit}, headers=headers)
    assert response.status_code == 422


def test_open_tasks_uses_default_and_maximum_page_sizes(owner):
    client, headers, _ = owner
    for index in range(101):
        _insert_task(f"Task {index:03d}", due_on="2026-07-10")

    default_page = client.get("/api/v1/tasks", headers=headers)
    assert default_page.status_code == 200
    assert len(default_page.json()["items"]) == 25
    assert default_page.json()["has_more"] is True

    maximum_page = client.get("/api/v1/tasks", params={"limit": 100}, headers=headers)
    assert maximum_page.status_code == 200
    assert len(maximum_page.json()["items"]) == 100
    assert maximum_page.json()["has_more"] is True


@pytest.mark.parametrize(
    "kind,values",
    [
        ("galleries", (0, "2026-07-10", 1)),
        ("owner-tasks-all-v1", (0, "2026-07-10", 1)),
        ("owner-tasks-open-v1", (2, "", 1)),
        ("owner-tasks-open-v1", (1, "2026-07-10", 1)),
        ("owner-tasks-open-v1", (0, "", 1)),
        ("owner-tasks-open-v1", (0, "not-a-date", 1)),
        ("owner-tasks-open-v1", (0, "20260710", 1)),
        ("owner-tasks-open-v1", (0, "2026-07-10", 0)),
        ("owner-tasks-open-v1", (0, "2026-07-10", 2**63)),
    ],
)
def test_open_tasks_rejects_signed_cross_filter_or_impossible_cursors(owner, kind, values):
    client, headers, _ = owner
    cursor = mobile_api_helpers.encode_keyset_cursor(kind, values)
    response = client.get("/api/v1/tasks", params={"cursor": cursor}, headers=headers)
    assert response.status_code == 422
    assert response.json()["code"] == "pagination.invalid_cursor"


@pytest.mark.parametrize("malformed", ["not-base64!", "unsigned-random-cursor"])
def test_open_tasks_rejects_unsigned_cursor(owner, malformed):
    client, headers, _ = owner
    invalid = client.get("/api/v1/tasks", params={"cursor": malformed}, headers=headers)
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "pagination.invalid_cursor"


def test_open_tasks_rejects_tampered_cursor(owner):
    client, headers, _ = owner
    _insert_task("First", due_on="2026-07-10")
    _insert_task("Second", due_on="2026-07-11")
    first = client.get("/api/v1/tasks", params={"limit": 1}, headers=headers)
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor
    tampered = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    invalid = client.get("/api/v1/tasks", params={"cursor": tampered}, headers=headers)
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "pagination.invalid_cursor"


def test_open_tasks_refuses_to_emit_noncanonical_stored_boundary(owner):
    with pytest.raises(ValueError, match="canonical YYYY-MM-DD"):
        mobile_owner_api._encode_task_cursor({"id": 1, "due_date": "20260710"})
