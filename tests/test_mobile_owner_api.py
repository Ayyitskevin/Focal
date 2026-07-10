"""Focused native owner-read API contracts."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import config, db, mobile_auth, mobile_owner_api, ratelimit
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
