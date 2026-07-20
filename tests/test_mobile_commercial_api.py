"""Owner commercial-spine API contract tests (queue S8).

Every route is owner-only and read-only, and it mirrors the admin derivations
in app/commercial.py. These tests pin the contract boundaries: owner-only
access, root-client (company) scoping with 404s, no admin `href` string ever
serialized, integer-cents money, and the structured `target` translation.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import config, db, ratelimit
from app.admin import studio as admin_studio
from app.main import app

pytestmark = pytest.mark.unit

_TODAY = dt.date(2026, 8, 15)


def _device() -> dict:
    return {
        "installation_id": "9C21D2B4-5F81-4B21-8DFC-2E1A33F0A9C9",
        "name": "Owner iPhone",
        "platform": "ios",
        "app_version": "2.0",
    }


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "commercial-api-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(admin_studio, "_today", lambda: _TODAY)
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    yield client
    client.close()
    ratelimit._hits.clear()


def _seed() -> dict:
    # Root client = the "company"; a child venue must never appear as its own company.
    company_id = db.run(
        """INSERT INTO clients (name, company, email, billing_email, parent_id)
           VALUES ('Blue Plate Group','Blue Plate Group','ops@blueplate.example',
                   'ap@blueplate.example', NULL)"""
    )
    child_id = db.run(
        "INSERT INTO clients (name, company, email, parent_id) VALUES ('Downtown','',?,?)",
        ("dt@blueplate.example", company_id),
    )
    # A project with closeout gaps (no shot list / deliverables, workspace unpublished).
    project_id = db.run(
        """INSERT INTO projects (client_id,title,status,gallery_id,workspace_published,shoot_date)
           VALUES (?,?,'session_planning',NULL,0,'2026-09-01')""",
        (child_id, "Q4 Menu Refresh"),
    )
    # Past-due issued invoice with an open balance -> AR chase action + overdue rows.
    overdue_id = db.run(
        """INSERT INTO invoices (project_id,slug,title,total_cents,deposit_cents,due_date,status)
           VALUES (?,?,?,?,0,'2026-06-15','sent')""",
        (project_id, "inv-overdue", "November coverage", 250000),
    )
    # A draft invoice -> "Send draft invoice" action.
    db.run(
        """INSERT INTO invoices (project_id,slug,title,total_cents,deposit_cents,due_date,status)
           VALUES (?,?,?,?,0,NULL,'draft')""",
        (project_id, "inv-draft", "December coverage", 90000),
    )
    return {
        "company_id": company_id,
        "child_id": child_id,
        "project_id": project_id,
        "overdue_id": overdue_id,
    }


def _owner(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/studio/login",
        json={"email": None, "password": "owner-password", "device": _device()},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _gallery_guest(client: TestClient) -> dict[str, str]:
    gallery_id = db.run(
        """INSERT INTO galleries (slug,title,pin,published,type,require_pin,created_at)
           VALUES ('guest-gallery','Guest','4821',1,'gallery',1,'2026-07-01 12:00:00')"""
    )
    assert gallery_id
    r = client.post(
        "/api/v1/client-auth/gallery/unlock",
        json={"kind": "gallery", "slug": "guest-gallery", "pin": "4821", "device": _device()},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _assert_no_admin_hrefs(text: str) -> None:
    assert "/admin/" not in text
    assert "admin/studio" not in text


def test_companies_lists_roots_only(api_client):
    seed = _seed()
    headers = _owner(api_client)
    resp = api_client.get("/api/v1/companies", headers=headers)
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()["items"]]
    assert seed["company_id"] in ids
    assert seed["child_id"] not in ids  # a child venue is not its own company
    company = next(c for c in resp.json()["items"] if c["id"] == seed["company_id"])
    assert company["name"] == "Blue Plate Group"
    assert company["billing_email"] == "ap@blueplate.example"


def test_commercial_actions_queue_and_targets(api_client):
    seed = _seed()
    headers = _owner(api_client)
    resp = api_client.get("/api/v1/commercial/actions", headers=headers)
    assert resp.status_code == 200
    _assert_no_admin_hrefs(resp.text)
    items = resp.json()["items"]
    assert resp.json()["has_more"] is False
    # The queue shows the single top-ranked action per company; whichever it is,
    # it carries the company context and a well-formed structured target.
    top = next(i for i in items if i["company_id"] == seed["company_id"])
    assert top["priority"] >= 1
    assert top["severity"] in {"ok", "attention", "missing"}
    assert top["target"]["company_id"] == seed["company_id"]
    assert top["target"]["kind"] in {
        "company",
        "ar_chase",
        "project",
        "invoice",
        "gallery",
        "workspace",
        "other",
    }


def test_company_next_actions_scoping(api_client):
    seed = _seed()
    headers = _owner(api_client)
    resp = api_client.get(f"/api/v1/companies/{seed['company_id']}/next-actions", headers=headers)
    assert resp.status_code == 200
    _assert_no_admin_hrefs(resp.text)
    body = resp.json()
    assert body["company_id"] == seed["company_id"]
    titles = {a["title"] for a in body["actions"]}
    assert "Chase past-due invoice" in titles
    assert "Send draft invoice" in titles
    # The AR-chase href translates to a typed ar_chase target with the invoice id.
    ar_action = next(a for a in body["actions"] if a["title"] == "Chase past-due invoice")
    assert ar_action["target"]["kind"] == "ar_chase"
    assert ar_action["target"]["company_id"] == seed["company_id"]
    assert ar_action["target"]["invoice_id"] == seed["overdue_id"]
    draft_action = next(a for a in body["actions"] if a["title"] == "Send draft invoice")
    assert draft_action["target"]["kind"] == "invoice"

    # A child venue is not a company; an unknown id is a company 404.
    assert (
        api_client.get(
            f"/api/v1/companies/{seed['child_id']}/next-actions", headers=headers
        ).status_code
        == 404
    )
    assert (
        api_client.get("/api/v1/companies/999999/next-actions", headers=headers).status_code == 404
    )


def test_ar_chase_assist_money_urls_and_filter(api_client):
    seed = _seed()
    headers = _owner(api_client)
    resp = api_client.get(f"/api/v1/companies/{seed['company_id']}/ar-chase", headers=headers)
    assert resp.status_code == 200
    _assert_no_admin_hrefs(resp.text)
    body = resp.json()
    assert body["owed"] == {"minor_units": 250000, "currency_code": "USD"}
    inv = body["overdue_invoices"][0]
    assert inv["invoice_id"] == seed["overdue_id"]
    assert inv["total"]["minor_units"] == 250000
    assert inv["owed"]["minor_units"] == 250000
    assert inv["public_url"] == "https://studio.test/i/inv-overdue"
    assert "slug" not in inv  # raw slug is never exposed, only the public URL
    # public_url is for chase-email copy only — not an owner preview open target.
    assert inv["public_url"].startswith("https://studio.test/i/")
    assert body["cadence"]["status"] == "never"
    assert body["draft"]["to"] == "ap@blueplate.example"
    assert body["draft"]["subject"].startswith("Follow-up on open invoice balance - ")

    # invoice_id filter narrows to one; a non-matching id 404s.
    one = api_client.get(
        f"/api/v1/companies/{seed['company_id']}/ar-chase",
        params={"invoice_id": seed["overdue_id"]},
        headers=headers,
    )
    assert one.status_code == 200
    assert len(one.json()["overdue_invoices"]) == 1
    assert (
        api_client.get(
            f"/api/v1/companies/{seed['company_id']}/ar-chase",
            params={"invoice_id": 424242},
            headers=headers,
        ).status_code
        == 404
    )


def test_project_closeout_shape_and_targets(api_client):
    seed = _seed()
    headers = _owner(api_client)
    resp = api_client.get(f"/api/v1/projects/{seed['project_id']}/closeout", headers=headers)
    assert resp.status_code == 200
    _assert_no_admin_hrefs(resp.text)
    body = resp.json()
    assert body["project_id"] == seed["project_id"]
    assert body["total"] == len(body["items"]) == 7
    assert body["ready"] is False
    keys = {item["key"] for item in body["items"]}
    assert keys == {"shots", "deliverables", "license", "invoice", "ar", "gallery", "workspace"}
    shots = next(i for i in body["items"] if i["key"] == "shots")
    assert shots["severity"] == "missing"
    assert shots["target"] == {
        "kind": "project",
        "company_id": None,
        "project_id": seed["project_id"],
        "invoice_id": None,
        "gallery_id": None,
        "section": "shots",
        "url": None,
    }

    assert api_client.get("/api/v1/projects/999999/closeout", headers=headers).status_code == 404


def test_owner_only_access(api_client):
    _seed()
    # No bearer -> 401.
    assert api_client.get("/api/v1/companies").status_code == 401
    # A gallery guest is authenticated but not the owner -> 403 everywhere.
    guest = _gallery_guest(api_client)
    for path in ("/api/v1/companies", "/api/v1/commercial/actions"):
        r = api_client.get(path, headers=guest)
        assert r.status_code == 403, (path, r.status_code)
