"""Release-candidate acceptance: real owner → client vertical + integrity probes.

Drives shipped FastAPI entry points (login, tasks, galleries, media, favorites,
public invoice, hosted storage middleware). Does not re-implement authorization
or status logic in the test body.

Scenarios covered:
- owner auth → studio task mutation → gallery manage/preview
- client unlock of only authorized gallery → media GET → favorite reflected
- unauthorized / rival gallery fail-closed
- empty gallery honest empty (not a false success studio)
- draft/unpublished: owner media ok, guest unlock denied
- large-gallery first page bounds (≤100, has_more)
- missing tenant storage → correlated 503, no silent empty-studio recreation
- reviewer seeder remains fail-closed
- owner invoice preview does not mint client first-view
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import alerts, config, db, ratelimit, saas
from app.main import app

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _device(name: str = "RC Device") -> dict:
    return {
        "installation_id": "RC000001-2345-6789-ABCD-EF0123456789",
        "name": name,
        "platform": "ios",
        "app_version": "2.0",
    }


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "rc-acceptance-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    yield client
    client.close()
    ratelimit._hits.clear()


def _owner(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/studio/login",
        json={"email": None, "password": "owner-password", "device": _device("Owner")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["principal"]["kind"] == "studio_owner"
    return {"Authorization": f"Bearer {body['access_token']}"}


def _unlock_gallery(client: TestClient, slug: str, pin: str) -> dict[str, str]:
    r = client.post(
        "/api/v1/client-auth/gallery/unlock",
        json={"kind": "gallery", "slug": slug, "pin": pin, "device": _device("Client")},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_studio_world(media_root: Path) -> dict:
    client_id = db.run(
        "INSERT INTO clients (name, company, email) VALUES (?,?,?)",
        ("Amelia Chen", "", "amelia@example.com"),
    )
    rival_id = db.run(
        "INSERT INTO clients (name, email) VALUES (?,?)",
        ("Rival", "rival@example.com"),
    )
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug, title, client_id, pin, published, type, require_pin, created_at)
           VALUES ('rc-wedding','RC Wedding',?,'4821',1,'gallery',1,'2026-07-01 12:00:00')""",
        (client_id,),
    )
    rival_gallery_id = db.run(
        """INSERT INTO galleries
           (slug, title, client_id, pin, published, type, require_pin, created_at)
           VALUES ('rc-rival','RC Rival',?,'9922',1,'gallery',1,'2026-07-02 12:00:00')""",
        (rival_id,),
    )
    draft_gallery_id = db.run(
        """INSERT INTO galleries
           (slug, title, client_id, pin, published, type, require_pin, created_at)
           VALUES ('rc-draft','RC Draft',?,'1111',0,'gallery',1,'2026-07-03 12:00:00')""",
        (client_id,),
    )
    empty_gallery_id = db.run(
        """INSERT INTO galleries
           (slug, title, pin, published, type, require_pin, created_at)
           VALUES ('rc-empty','RC Empty','0000',1,'gallery',1,'2026-07-04 12:00:00')"""
    )
    section_id = db.run(
        "INSERT INTO sections (gallery_id, name, position, proof_target) VALUES (?,?,0,5)",
        (gallery_id, "Ceremony"),
    )
    asset_ids = []
    for i in range(3):
        aid = db.run(
            """INSERT INTO assets
               (gallery_id, section_id, kind, filename, stored, status, position, created_at)
               VALUES (?,?,?,?,?,'ready',?,'2026-07-01 12:01:00')""",
            (gallery_id, section_id, "photo", f"frame-{i}.jpg", f"stored-{i}.jpg", i),
        )
        asset_ids.append(aid)
        root = media_root / str(gallery_id)
        (root / "thumb").mkdir(parents=True, exist_ok=True)
        (root / "web").mkdir(parents=True, exist_ok=True)
        (root / "thumb" / f"stored-{i}.jpg").write_bytes(f"thumb-{i}".encode())
        (root / "web" / f"stored-{i}.jpg").write_bytes(f"preview-{i}".encode())

    rival_asset = db.run(
        """INSERT INTO assets (gallery_id, kind, filename, stored, status, position)
           VALUES (?,'photo','rival.jpg','rival.jpg','ready',0)""",
        (rival_gallery_id,),
    )
    draft_asset = db.run(
        """INSERT INTO assets (gallery_id, kind, filename, stored, status, position)
           VALUES (?,'photo','draft.jpg','draft.jpg','ready',0)""",
        (draft_gallery_id,),
    )
    draft_root = media_root / str(draft_gallery_id)
    (draft_root / "thumb").mkdir(parents=True, exist_ok=True)
    (draft_root / "web").mkdir(parents=True, exist_ok=True)
    (draft_root / "thumb" / "draft.jpg").write_bytes(b"draft-thumb")
    (draft_root / "web" / "draft.jpg").write_bytes(b"draft-preview")

    project_id = db.run(
        "INSERT INTO projects (client_id, title, status, gallery_id) VALUES (?,?,?,?)",
        (client_id, "Chen Wedding", "session_planning", gallery_id),
    )
    invoice_id = db.run(
        """INSERT INTO invoices
           (project_id, slug, title, line_items, total_cents, deposit_cents,
            due_date, status, sent_at)
           VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
        (
            project_id,
            "rc-inv-sent",
            "Deposit",
            '[{"label":"Shoot","qty":1,"price_cents":150000}]',
            150000,
            0,
            "2026-08-01",
            "sent",
        ),
    )
    task_id = db.run("INSERT INTO tasks (title) VALUES (?)", ("Cull ceremony selects",))
    return {
        "client_id": client_id,
        "gallery_id": gallery_id,
        "rival_gallery_id": rival_gallery_id,
        "draft_gallery_id": draft_gallery_id,
        "empty_gallery_id": empty_gallery_id,
        "asset_ids": asset_ids,
        "rival_asset": rival_asset,
        "draft_asset": draft_asset,
        "project_id": project_id,
        "invoice_id": invoice_id,
        "task_id": task_id,
    }


# ── vertical path ────────────────────────────────────────────────────────────


def test_rc_owner_client_vertical_task_gallery_favorite_media(api_client):
    """Owner signs in, acts, client is scoped, favorite reflects, rival denied."""
    seed = _seed_studio_world(config.MEDIA_DIR)
    owner = _owner(api_client)

    # Owner mutation: complete a studio task through the real API.
    done = api_client.put(f"/api/v1/tasks/{seed['task_id']}/completion", headers=owner)
    assert done.status_code == 200, done.text
    assert done.json()["done"] is True
    assert db.one("SELECT done FROM tasks WHERE id=?", (seed["task_id"],))["done"] == 1

    # Owner can list and open the published gallery (manage/preview surface).
    listing = api_client.get("/api/v1/galleries", headers=owner)
    assert listing.status_code == 200
    owner_ids = {g["id"] for g in listing.json()["items"]}
    assert seed["gallery_id"] in owner_ids
    assert seed["draft_gallery_id"] in owner_ids  # drafts visible to owner

    owner_detail = api_client.get(f"/api/v1/galleries/{seed['gallery_id']}", headers=owner)
    assert owner_detail.status_code == 200
    assert owner_detail.json()["summary"]["id"] == seed["gallery_id"]
    fav_before = owner_detail.json()["summary"].get("favorite_count", 0)

    # Client unlock of authorized gallery only.
    guest = _unlock_gallery(api_client, "rc-wedding", "4821")
    client_list = api_client.get("/api/v1/client/galleries", headers=guest)
    assert client_list.status_code == 200
    assert [g["id"] for g in client_list.json()["items"]] == [seed["gallery_id"]]

    # Rival gallery is existence-private (404), not a success empty list item.
    rival_detail = api_client.get(
        f"/api/v1/client/galleries/{seed['rival_gallery_id']}", headers=guest
    )
    assert rival_detail.status_code == 404

    # Authorized media GET.
    asset_id = seed["asset_ids"][0]
    media = api_client.get(
        f"/api/v1/media/galleries/{seed['gallery_id']}/assets/{asset_id}/thumbnail",
        headers=guest,
    )
    assert media.status_code == 200
    assert media.content == b"thumb-0"

    # Client favorite on authorized asset.
    fav = api_client.put(
        f"/api/v1/galleries/{seed['gallery_id']}/assets/{asset_id}/favorite",
        headers=guest,
    )
    assert fav.status_code == 200
    assert fav.json()["selected"] is True

    # Favorite is reflected for owner without inventing rival state.
    owner_after = api_client.get(f"/api/v1/galleries/{seed['gallery_id']}", headers=owner)
    assert owner_after.status_code == 200
    fav_after = owner_after.json()["summary"]["favorite_count"]
    assert fav_after == fav_before + 1
    assert (
        db.one(
            """SELECT COUNT(*) AS n FROM favorites f
               JOIN assets a ON a.id=f.asset_id
               WHERE a.gallery_id=? AND f.asset_id=?""",
            (seed["gallery_id"], asset_id),
        )["n"]
        == 1
    )
    assert (
        db.one(
            """SELECT COUNT(*) AS n FROM favorites f
               JOIN assets a ON a.id=f.asset_id
               WHERE a.gallery_id=?""",
            (seed["rival_gallery_id"],),
        )["n"]
        == 0
    )

    # Unauthorized favorite on rival gallery fail-closed.
    foreign = api_client.put(
        f"/api/v1/galleries/{seed['rival_gallery_id']}/assets/{seed['rival_asset']}/favorite",
        headers=guest,
    )
    assert foreign.status_code == 403

    # Unrelated invoice row must still be sent (no client-view mint from this path).
    inv = db.one("SELECT status, viewed_at FROM invoices WHERE id=?", (seed["invoice_id"],))
    assert inv["status"] == "sent"
    assert inv["viewed_at"] is None


def test_rc_empty_gallery_is_honest_empty_not_false_studio(api_client):
    seed = _seed_studio_world(config.MEDIA_DIR)
    owner = _owner(api_client)
    detail = api_client.get(f"/api/v1/galleries/{seed['empty_gallery_id']}", headers=owner)
    assert detail.status_code == 200
    body = detail.json()
    assert body["summary"]["id"] == seed["empty_gallery_id"]
    assert body["assets"] == []
    assert body["assets_has_more"] is False
    assert body["assets_next_cursor"] is None
    # Studio still has other clients/galleries — empty gallery ≠ empty studio.
    assert db.one("SELECT COUNT(*) AS n FROM clients")["n"] >= 2
    assert db.one("SELECT COUNT(*) AS n FROM galleries")["n"] >= 3


def test_rc_draft_owner_media_guest_denied(api_client):
    seed = _seed_studio_world(config.MEDIA_DIR)
    owner = _owner(api_client)
    base = f"/api/v1/media/galleries/{seed['draft_gallery_id']}/assets/{seed['draft_asset']}"

    assert api_client.get(f"{base}/thumbnail", headers=owner).status_code == 200
    assert api_client.get(f"{base}/thumbnail", headers=owner).content == b"draft-thumb"

    unlock = api_client.post(
        "/api/v1/client-auth/gallery/unlock",
        json={
            "kind": "gallery",
            "slug": "rc-draft",
            "pin": "1111",
            "device": _device("Client"),
        },
    )
    assert unlock.status_code in (401, 403, 404)
    assert api_client.get(f"{base}/thumbnail").status_code == 401


def test_rc_large_gallery_first_page_bounded(api_client):
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug, title, pin, published, type, require_pin, created_at)
           VALUES ('rc-large','RC Large','5555',1,'gallery',1,'2026-07-01 12:00:00')"""
    )
    section_id = db.run(
        "INSERT INTO sections (gallery_id, name, position) VALUES (?,?,0)",
        (gallery_id, "All"),
    )
    for i in range(105):
        db.run(
            """INSERT INTO assets
               (gallery_id, section_id, kind, filename, stored, status, position, created_at)
               VALUES (?,?,?,?,?,'ready',?,'2026-07-01 12:00:00')""",
            (gallery_id, section_id, "photo", f"a{i}.jpg", f"a{i}.jpg", i),
        )
    owner = _owner(api_client)
    first = api_client.get(f"/api/v1/galleries/{gallery_id}?limit=100", headers=owner)
    assert first.status_code == 200
    payload = first.json()
    assert len(payload["assets"]) == 100
    assert payload["assets_has_more"] is True
    assert payload["assets_next_cursor"]
    # Whole-manifest failure would 422/500 above 10k; 105 assets must open.
    assert payload["summary"]["asset_count"] == 105

    second = api_client.get(
        f"/api/v1/galleries/{gallery_id}?limit=100&cursor={payload['assets_next_cursor']}",
        headers=owner,
    )
    assert second.status_code == 200
    assert len(second.json()["assets"]) == 5
    assert second.json()["assets_has_more"] is False


def test_rc_owner_invoice_preview_does_not_mint_client_view(api_client, tmp_path, monkeypatch):
    """Admin-session open of /i/{slug} leaves sent + viewed_at null; public flips once."""
    seed = _seed_studio_world(config.MEDIA_DIR)
    # Admin login for session cookie (owner web preview path).
    login = api_client.post(
        "/admin/login", data={"password": "owner-password"}, follow_redirects=False
    )
    assert login.status_code == 303

    before = db.one("SELECT status, viewed_at FROM invoices WHERE id=?", (seed["invoice_id"],))
    assert before["status"] == "sent" and before["viewed_at"] is None

    preview = api_client.get("/i/rc-inv-sent")
    assert preview.status_code == 200
    after_owner = db.one("SELECT status, viewed_at FROM invoices WHERE id=?", (seed["invoice_id"],))
    assert after_owner["status"] == "sent"
    assert after_owner["viewed_at"] is None

    # Real client path uses a cookie-free client.
    with TestClient(app, base_url="https://studio.test") as pub:
        first = pub.get("/i/rc-inv-sent")
        assert first.status_code == 200
        row = db.one("SELECT status, viewed_at FROM invoices WHERE id=?", (seed["invoice_id"],))
        assert row["status"] == "viewed"
        assert row["viewed_at"] is not None
        viewed_at = row["viewed_at"]
        second = pub.get("/i/rc-inv-sent")
        assert second.status_code == 200
        row2 = db.one("SELECT status, viewed_at FROM invoices WHERE id=?", (seed["invoice_id"],))
        assert row2["status"] == "viewed"
        assert row2["viewed_at"] == viewed_at


# ── storage fail-loud (no silent empty studio) ───────────────────────────────


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "SAAS_TRIAL_DAYS", 14)
    monkeypatch.setattr(config, "SECRET_KEY", "rc-saas-not-default-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "operator-password-long")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def test_rc_missing_tenant_storage_fail_loud_no_empty_recreation(tmp_path, monkeypatch, caplog):
    _configure_saas(tmp_path, monkeypatch)
    tenant = saas.create_tenant("rcalpha", "RC Alpha", "rc@example.com", "secret12345")
    lost = tmp_path / "lost-rcalpha"
    saas.tenant_data_path(tenant["slug"]).rename(lost)
    saas._MIGRATED_TENANT_DBS.clear()

    alert_calls = []
    monkeypatch.setattr(
        alerts, "ops_alert", lambda signature, text: alert_calls.append((signature, text))
    )

    async def call_next(_request):
        # Must never run — middleware fails closed before empty success.
        return saas.JSONResponse({"clients": 0})

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/clients",
            "query_string": b"",
            "headers": [
                (b"host", b"rcalpha.mise.test"),
                (b"accept", b"application/json"),
            ],
            "scheme": "https",
            "server": ("rcalpha.mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )
    with caplog.at_level(logging.ERROR, logger="mise.saas"):
        response = asyncio.run(saas.tenant_middleware(request, call_next))

    body = json.loads(response.body)
    assert response.status_code == 503
    assert body["code"] == "tenant.storage_unavailable"
    assert "request_id" in body
    assert body["request_id"]
    # No filesystem / email leak to the client.
    assert "secret" not in body["detail"].lower()
    assert "rc@example.com" not in json.dumps(body)
    assert str(config.SAAS_TENANT_DATA_DIR) not in json.dumps(body)
    # No silent empty studio recreation.
    assert not saas.tenant_data_path("rcalpha").exists()
    assert (lost / "mise.db").is_file()
    assert alert_calls, "operator signal required for recovery"
    assert "restore" in alert_calls[0][1].lower()


# ── demo / capability fail-closed ────────────────────────────────────────────


def test_rc_seed_demo_tenant_fail_closed():
    import importlib.util

    script = ROOT / "scripts" / "seed_demo_tenant.py"
    spec = importlib.util.spec_from_file_location("seed_demo_tenant_rc", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    with pytest.raises(SystemExit, match=r"185|disabled|unsafe"):
        mod.seed_demo_tenant(
            slug="demo",
            studio_name="Demo",
            owner_email="demo@example.com",
            password="x",
            preset="wedding",
        )


def test_rc_native_commercial_preview_does_not_open_client_invoice_url():
    """Structural: owner AR UI must not Link to publicURL (client mutation path)."""
    text = (ROOT / "ios/Mise/Features/Commercial/CommercialView.swift").read_text()
    assert 'Link("Open invoice"' not in text
    assert "destination: inv.publicURL" not in text
    assert "Preview invoice" in text


def test_rc_gallery_video_still_not_image_decode_mp4():
    viewer = (ROOT / "ios/Mise/Features/Shared/GalleryViewer.swift").read_text()
    assert "GalleryMediaPresentation" in viewer
    assert "AuthenticatedRemoteVideo" in viewer
    video = (ROOT / "ios/Mise/Features/Shared/AuthenticatedRemoteVideo.swift").read_text()
    assert "VideoPlayer" in video or "AVPlayer" in video
