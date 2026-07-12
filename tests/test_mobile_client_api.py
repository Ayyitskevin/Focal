"""Milestone 3 shared-client API contract tests.

Every route must re-derive authority from the exact guest principal: a gallery
exchange cannot read documents, a workspace exchange cannot open another
project, and a portal exchange cannot favorite (no visitor identity). These
tests exercise each principal kind end-to-end through the real unlock routes so
scope minting and enforcement are tested together.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import config, db, mobile_gallery_calendar_api, ratelimit
from app.main import app

pytestmark = pytest.mark.unit


def _device() -> dict:
    return {
        "installation_id": "8A31D2B4-5F81-4B21-8DFC-2E1A33F0A9C1",
        "name": "Client iPhone",
        "platform": "ios",
        "app_version": "2.0",
    }


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "client-api-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(
        mobile_gallery_calendar_api,
        "_studio_today",
        lambda: dt.date(2026, 7, 10),
    )
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    yield client
    client.close()
    ratelimit._hits.clear()


def _seed_world() -> dict:
    """One client with a project workspace, gallery, documents, and a booking,
    plus a second client/gallery that must never leak across a scope."""
    client_id = db.run(
        "INSERT INTO clients (name,company,email) VALUES ('Amelia Chen','','amelia@example.com')"
    )
    other_client_id = db.run(
        "INSERT INTO clients (name,email) VALUES ('Rival Client','rival@example.com')"
    )

    gallery_id = db.run(
        """INSERT INTO galleries (slug,title,client_id,pin,published,type,require_pin,created_at)
           VALUES ('amelia-wedding','Amelia + Sam',?, '4821',1,'gallery',1,'2026-07-01 12:00:00')""",
        (client_id,),
    )
    other_gallery_id = db.run(
        """INSERT INTO galleries (slug,title,client_id,pin,published,type,require_pin,created_at)
           VALUES ('rival-gallery','Rival',?, '9922',1,'gallery',1,'2026-07-02 12:00:00')""",
        (other_client_id,),
    )
    section_id = db.run(
        "INSERT INTO sections (gallery_id,name,position,proof_target) VALUES (?,?,0,2)",
        (gallery_id, "Ceremony"),
    )
    asset_ids = []
    for position in range(3):
        asset_ids.append(
            db.run(
                """INSERT INTO assets
                   (gallery_id,section_id,kind,filename,stored,status,position,created_at)
                   VALUES (?,?,?,?,?,'ready',?,'2026-07-01 12:01:00')""",
                (
                    gallery_id,
                    section_id,
                    "photo",
                    f"frame-{position}.jpg",
                    f"stored-{position}.jpg",
                    position,
                ),
            )
        )
    other_asset_id = db.run(
        """INSERT INTO assets (gallery_id,kind,filename,stored,status,position)
           VALUES (?,'photo','other.jpg','other-stored.jpg','ready',0)""",
        (other_gallery_id,),
    )

    project_id = db.run(
        """INSERT INTO projects
           (client_id,title,status,gallery_id,workspace_slug,workspace_pin,workspace_published)
           VALUES (?,?,'proposal_sent',?,?,?,1)""",
        (client_id, "Chen Wedding", gallery_id, "ws-chen", "5150"),
    )
    other_project_id = db.run(
        """INSERT INTO projects
           (client_id,title,status,workspace_slug,workspace_pin,workspace_published)
           VALUES (?,?,'proposal_sent','ws-rival','7777',1)""",
        (other_client_id, "Rival Project"),
    )

    proposal_id = db.run(
        """INSERT INTO proposals (project_id,slug,title,line_items,total_cents,status,sent_at)
           VALUES (?,?,?,?,?,'sent','2026-07-02 09:00:00')""",
        (
            project_id,
            "prop-chen",
            "Wedding collection",
            '[{"label":"Full day coverage","qty":1,"unit_cents":420000}]',
            420000,
        ),
    )
    db.run(
        """INSERT INTO proposals (project_id,slug,title,total_cents,status)
           VALUES (?,?,?,0,'draft')""",
        (project_id, "prop-draft", "Draft proposal"),
    )
    contract_id = db.run(
        """INSERT INTO contracts (project_id,slug,title,body,body_sha256,status,sent_at)
           VALUES (?,?,?,?,?,'sent','2026-07-02 10:00:00')""",
        (project_id, "con-chen", "Wedding agreement", "Terms are simple.", "abc123"),
    )
    invoice_id = db.run(
        """INSERT INTO invoices
           (project_id,slug,title,total_cents,deposit_cents,due_date,status,sent_at)
           VALUES (?,?,?,?,?,'2026-08-01','sent','2026-07-02 11:00:00')""",
        (project_id, "inv-chen", "Deposit invoice", 150000, 50000),
    )
    db.run(
        "INSERT INTO payments (invoice_id,amount_cents,kind) VALUES (?,?,'deposit')",
        (invoice_id, 50000),
    )

    portal_id = db.run(
        "INSERT INTO portals (client_id,slug,pin,published) VALUES (?,?,?,1)",
        (client_id, "portal-chen", "6161"),
    )

    event_id = db.run(
        """INSERT INTO event_types
           (slug,name,description,duration_min,location,color,buffer_before_min,
            buffer_after_min,min_notice_hours,max_per_day,booking_window_days,
            slot_step_min,active,position)
           VALUES ('walkthrough','Walkthrough','',45,'Studio','#123ABC',0,0,0,0,90,45,1,1)"""
    )
    booking_id = db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,start_utc,end_utc,tz,status,client_id,project_id)
           VALUES ('bk-chen',?, 'Amelia Chen','amelia@example.com','2099-02-01 15:00:00',
                   '2099-02-01 15:45:00','America/New_York','confirmed',?,?)""",
        (event_id, client_id, project_id),
    )
    db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,start_utc,end_utc,status,client_id)
           VALUES ('bk-rival',?, 'Rival','rival@example.com','2099-03-01 15:00:00',
                   '2099-03-01 15:45:00','confirmed',?)""",
        (event_id, other_client_id),
    )

    return {
        "client_id": client_id,
        "gallery_id": gallery_id,
        "other_gallery_id": other_gallery_id,
        "asset_ids": asset_ids,
        "other_asset_id": other_asset_id,
        "project_id": project_id,
        "other_project_id": other_project_id,
        "proposal_id": proposal_id,
        "contract_id": contract_id,
        "invoice_id": invoice_id,
        "portal_id": portal_id,
        "booking_id": booking_id,
    }


def _unlock(client: TestClient, kind: str, slug: str, pin: str | None) -> dict[str, str]:
    endpoint = {
        "gallery": "/api/v1/client-auth/gallery/unlock",
        "portal": "/api/v1/client-auth/portal/unlock",
        "workspace": "/api/v1/client-auth/workspace/unlock",
    }.get(kind, "/api/v1/client-auth/document/exchange")
    response = client.post(
        endpoint,
        json={"kind": kind, "slug": slug, "pin": pin, "device": _device()},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _owner_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/studio/login",
        json={"email": None, "password": "owner-password", "device": _device()},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_gallery_guest_scoped_reads_and_favorite_flow(api_client):
    seed = _seed_world()
    headers = _unlock(api_client, "gallery", "amelia-wedding", "4821")

    listing = api_client.get("/api/v1/client/galleries", headers=headers)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [seed["gallery_id"]]

    detail = api_client.get(f"/api/v1/client/galleries/{seed['gallery_id']}", headers=headers)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["summary"]["id"] == seed["gallery_id"]
    assert len(payload["assets"]) == 3
    first_links = payload["assets"][0]["links"]
    assert first_links["thumbnail_url"].startswith(
        f"https://studio.test/api/v1/media/galleries/{seed['gallery_id']}/assets/"
    )

    # Another client's gallery is a 404 (existence stays private) either way.
    other = api_client.get(f"/api/v1/client/galleries/{seed['other_gallery_id']}", headers=headers)
    assert other.status_code == 404

    asset_id = seed["asset_ids"][0]
    favorite_path = f"/api/v1/galleries/{seed['gallery_id']}/assets/{asset_id}/favorite"
    selected = api_client.put(favorite_path, headers=headers)
    assert selected.status_code == 200
    assert selected.json() == {
        "asset_id": asset_id,
        "selected": True,
        "section_selected_count": 1,
        "section_proof_target": 2,
    }
    # Idempotent re-select keeps state and count.
    assert api_client.put(favorite_path, headers=headers).json()["section_selected_count"] == 1

    # Proofing cap: target is 2; a third distinct pick is refused with a problem.
    second = seed["asset_ids"][1]
    third = seed["asset_ids"][2]
    assert (
        api_client.put(
            f"/api/v1/galleries/{seed['gallery_id']}/assets/{second}/favorite", headers=headers
        ).status_code
        == 200
    )
    capped = api_client.put(
        f"/api/v1/galleries/{seed['gallery_id']}/assets/{third}/favorite", headers=headers
    )
    assert capped.status_code == 409
    assert capped.json()["code"] == "gallery.proofing_limit"

    unselected = api_client.delete(favorite_path, headers=headers)
    assert unselected.status_code == 200
    assert unselected.json()["selected"] is False
    assert unselected.json()["section_selected_count"] == 1

    # A favorite call against a gallery outside this principal's scope is refused.
    foreign = api_client.put(
        f"/api/v1/galleries/{seed['other_gallery_id']}/assets/{seed['other_asset_id']}/favorite",
        headers=headers,
    )
    assert foreign.status_code == 403


def test_workspace_guest_documents_home_and_bookings(api_client):
    seed = _seed_world()
    headers = _unlock(api_client, "workspace", "ws-chen", "5150")

    proposals = api_client.get(f"/api/v1/projects/{seed['project_id']}/proposals", headers=headers)
    assert proposals.status_code == 200
    proposal_items = proposals.json()["items"]
    assert [item["id"] for item in proposal_items] == [seed["proposal_id"]]
    proposal = proposal_items[0]
    assert proposal["status"] == "sent"
    assert proposal["can_accept"] is True
    assert proposal["total"] == {"minor_units": 420000, "currency_code": "USD"}
    assert proposal["line_items"][0]["label"] == "Full day coverage"
    assert proposal["public_url"] == "https://studio.test/p/prop-chen"
    assert all(item["status"] != "draft" for item in proposal_items)

    contracts = api_client.get(f"/api/v1/projects/{seed['project_id']}/contracts", headers=headers)
    assert contracts.status_code == 200
    contract = contracts.json()["items"][0]
    assert contract["can_sign"] is True
    assert contract["public_url"] == "https://studio.test/c/con-chen"

    invoices = api_client.get(f"/api/v1/projects/{seed['project_id']}/invoices", headers=headers)
    assert invoices.status_code == 200
    invoice = invoices.json()["items"][0]
    assert invoice["total"]["minor_units"] == 150000
    assert invoice["paid"]["minor_units"] == 50000
    assert invoice["balance"]["minor_units"] == 100000
    assert invoice["payments"][0]["kind"] == "deposit"
    assert invoice["public_url"] == "https://studio.test/i/inv-chen"

    # Another project's documents are out of scope for this workspace.
    assert (
        api_client.get(
            f"/api/v1/projects/{seed['other_project_id']}/proposals", headers=headers
        ).status_code
        == 403
    )

    home = api_client.get("/api/v1/client/home", headers=headers)
    assert home.status_code == 200
    summary = home.json()
    assert summary["principal_kind"] == "workspace"
    assert summary["project_id"] == seed["project_id"]
    assert summary["gallery_id"] == seed["gallery_id"]
    kinds = [step["kind"] for step in summary["next_steps"]]
    assert kinds == ["proposal", "contract", "invoice", "gallery"]

    galleries = api_client.get("/api/v1/client/galleries", headers=headers)
    assert [item["id"] for item in galleries.json()["items"]] == [seed["gallery_id"]]

    bookings = api_client.get("/api/v1/client/bookings", headers=headers)
    assert bookings.status_code == 200
    booking_items = bookings.json()["items"]
    assert [item["id"] for item in booking_items] == [seed["booking_id"]]

    # Workspace guests have no visitor identity, so favorites are refused.
    assert (
        api_client.put(
            f"/api/v1/galleries/{seed['gallery_id']}/assets/{seed['asset_ids'][0]}/favorite",
            headers=headers,
        ).status_code
        == 403
    )


def test_portal_guest_sees_client_wide_galleries_and_bookings(api_client):
    seed = _seed_world()
    headers = _unlock(api_client, "portal", "portal-chen", "6161")

    galleries = api_client.get("/api/v1/client/galleries", headers=headers)
    assert galleries.status_code == 200
    assert [item["id"] for item in galleries.json()["items"]] == [seed["gallery_id"]]

    home = api_client.get("/api/v1/client/home", headers=headers)
    assert home.status_code == 200
    assert home.json()["principal_kind"] == "portal"
    assert home.json()["gallery_count"] == 1

    bookings = api_client.get("/api/v1/client/bookings", headers=headers)
    assert [item["id"] for item in bookings.json()["items"]] == [seed["booking_id"]]

    # A portal cannot read project documents; it has no workspace scope.
    assert (
        api_client.get(
            f"/api/v1/projects/{seed['project_id']}/proposals", headers=headers
        ).status_code
        == 403
    )


def test_document_guest_home_preview_and_isolation(api_client):
    seed = _seed_world()
    headers = _unlock(api_client, "invoice", "inv-chen", None)

    home = api_client.get("/api/v1/client/home", headers=headers)
    assert home.status_code == 200
    summary = home.json()
    assert summary["principal_kind"] == "document"
    assert summary["document"]["variant"] == "invoice"
    assert summary["document"]["balance"] == {"minor_units": 100000, "currency_code": "USD"}
    assert summary["document"]["public_url"] == "https://studio.test/i/inv-chen"

    # A single-document capability cannot widen into galleries, docs, or bookings.
    assert api_client.get("/api/v1/client/bookings", headers=headers).status_code == 403
    assert (
        api_client.get(
            f"/api/v1/projects/{seed['project_id']}/invoices", headers=headers
        ).status_code
        == 403
    )
    galleries = api_client.get("/api/v1/client/galleries", headers=headers)
    assert galleries.status_code == 200
    assert galleries.json()["items"] == []


def test_owner_reads_documents_and_client_routes_reject_owner(api_client):
    seed = _seed_world()
    headers = _owner_headers(api_client)

    proposals = api_client.get(f"/api/v1/projects/{seed['project_id']}/proposals", headers=headers)
    assert proposals.status_code == 200
    assert len(proposals.json()["items"]) == 1

    # /client/home models guest capabilities, not the studio principal.
    assert api_client.get("/api/v1/client/home", headers=headers).status_code == 403

    assert api_client.get("/api/v1/projects/999999/proposals", headers=headers).status_code == 404


def test_media_routes_enforce_bearer_scope_and_delivery_gates(api_client, monkeypatch):
    seed = _seed_world()
    gallery_id = seed["gallery_id"]
    asset_id = seed["asset_ids"][0]

    media_root = config.MEDIA_DIR / str(gallery_id)
    (media_root / "thumb").mkdir(parents=True)
    (media_root / "web").mkdir(parents=True)
    (media_root / "original").mkdir(parents=True)
    (media_root / "thumb" / "stored-0.jpg").write_bytes(b"thumb-bytes")
    (media_root / "web" / "stored-0.jpg").write_bytes(b"preview-bytes")
    (media_root / "original" / "stored-0.jpg").write_bytes(b"original-bytes")

    base = f"/api/v1/media/galleries/{gallery_id}/assets/{asset_id}"

    # No bearer, no bytes.
    assert api_client.get(f"{base}/thumbnail").status_code == 401

    guest = _unlock(api_client, "gallery", "amelia-wedding", "4821")
    thumb = api_client.get(f"{base}/thumbnail", headers=guest)
    assert thumb.status_code == 200
    assert thumb.content == b"thumb-bytes"
    assert thumb.headers["cache-control"] == "private, max-age=86400"
    assert api_client.get(f"{base}/preview", headers=guest).content == b"preview-bytes"
    assert api_client.get(f"{base}/download", headers=guest).content == b"original-bytes"
    # Photos have no poster variant.
    assert api_client.get(f"{base}/poster", headers=guest).status_code == 404

    owner = _owner_headers(api_client)
    assert api_client.get(f"{base}/thumbnail", headers=owner).status_code == 200

    # A guest for another gallery is refused before any file resolution.
    rival = _unlock(api_client, "gallery", "rival-gallery", "9922")
    assert api_client.get(f"{base}/thumbnail", headers=rival).status_code == 403

    # Workspace guests can view variants but never pull originals.
    workspace = _unlock(api_client, "workspace", "ws-chen", "5150")
    assert api_client.get(f"{base}/thumbnail", headers=workspace).status_code == 200
    assert api_client.get(f"{base}/download", headers=workspace).status_code == 403

    # A cut frame is 404 even with a valid session and a real file on disk
    # (the cull delivery gate is flag-gated on MISE_CULL_UI).
    monkeypatch.setattr(config, "CULL_UI", True)
    db.run("UPDATE assets SET cull_state='cut' WHERE id=?", (asset_id,))
    assert api_client.get(f"{base}/thumbnail", headers=guest).status_code == 404
    db.run("UPDATE assets SET cull_state='keep' WHERE id=?", (asset_id,))
    assert api_client.get(f"{base}/thumbnail", headers=guest).status_code == 200

    # Unpublishing invalidates the credential source: the bearer dies (401) and
    # stays revoked even after the gallery is re-published.
    db.run("UPDATE galleries SET published=0 WHERE id=?", (gallery_id,))
    assert api_client.get(f"{base}/thumbnail", headers=guest).status_code == 401
    db.run("UPDATE galleries SET published=1 WHERE id=?", (gallery_id,))
    assert api_client.get(f"{base}/thumbnail", headers=guest).status_code == 401
