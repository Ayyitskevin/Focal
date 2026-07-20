"""Owner draft-gallery media authorization + video derivative contract (#183).

Studio owners must load media for unpublished draft galleries. Guests remain
subject to publish/expiry delivery gates. Video stills use poster; playback
uses the authenticated preview (MP4) path — never image-decode the MP4.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db, ratelimit
from app.main import app
from app.mobile_media import _resolve_path, build_media_links

pytestmark = pytest.mark.unit


def _device() -> dict:
    return {
        "installation_id": "A1B2C3D4-5E6F-7A8B-9C0D-1E2F3A4B5C6D",
        "name": "Owner iPhone",
        "platform": "ios",
        "app_version": "2.0",
    }


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "media-owner-draft-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    yield client
    client.close()
    ratelimit._hits.clear()


def _owner(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/studio/login",
        json={"email": None, "password": "owner-password", "device": _device()},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_draft_with_photo_and_video(media_root):
    gallery_id = db.run(
        """INSERT INTO galleries (slug, title, pin, published, type, require_pin, created_at)
           VALUES ('draft-preview','Draft Preview','1234',0,'gallery',1,'2026-07-01 12:00:00')"""
    )
    photo_id = db.run(
        """INSERT INTO assets
             (gallery_id, kind, status, filename, stored, position, created_at)
           VALUES (?, 'photo', 'ready', 'still.jpg', 'still.jpg', 1, '2026-07-01 12:01:00')""",
        (gallery_id,),
    )
    video_id = db.run(
        """INSERT INTO assets
             (gallery_id, kind, status, filename, stored, position, created_at)
           VALUES (?, 'video', 'ready', 'clip.mp4', 'clip.mp4', 2, '2026-07-01 12:02:00')""",
        (gallery_id,),
    )
    root = media_root / str(gallery_id)
    (root / "thumb").mkdir(parents=True)
    (root / "web").mkdir(parents=True)
    (root / "original").mkdir(parents=True)
    (root / "thumb" / "still.jpg").write_bytes(b"photo-thumb")
    (root / "web" / "still.jpg").write_bytes(b"photo-preview")
    (root / "original" / "still.jpg").write_bytes(b"photo-original")
    (root / "thumb" / "clip.jpg").write_bytes(b"video-thumb")
    (root / "web" / "clip_poster.jpg").write_bytes(b"video-poster")
    (root / "web" / "clip.mp4").write_bytes(b"video-mp4-bytes-not-an-image")
    (root / "original" / "clip.mp4").write_bytes(b"video-original")
    return {"gallery_id": gallery_id, "photo_id": photo_id, "video_id": video_id}


def test_owner_can_load_media_for_unpublished_draft_gallery(api_client):
    seed = _seed_draft_with_photo_and_video(config.MEDIA_DIR)
    owner = _owner(api_client)
    base = f"/api/v1/media/galleries/{seed['gallery_id']}/assets/{seed['photo_id']}"

    thumb = api_client.get(f"{base}/thumbnail", headers=owner)
    assert thumb.status_code == 200
    assert thumb.content == b"photo-thumb"

    preview = api_client.get(f"{base}/preview", headers=owner)
    assert preview.status_code == 200
    assert preview.content == b"photo-preview"


def test_guest_still_denied_on_unpublished_draft_gallery(api_client):
    seed = _seed_draft_with_photo_and_video(config.MEDIA_DIR)
    # Gallery is unpublished — guest unlock must fail closed before media.
    unlock = api_client.post(
        "/api/v1/client-auth/gallery/unlock",
        json={
            "kind": "gallery",
            "slug": "draft-preview",
            "pin": "1234",
            "device": _device(),
        },
    )
    # Unpublished galleries do not mint guest sessions.
    assert unlock.status_code in (401, 403, 404)

    base = f"/api/v1/media/galleries/{seed['gallery_id']}/assets/{seed['photo_id']}"
    assert api_client.get(f"{base}/thumbnail").status_code == 401


def test_video_poster_vs_playback_paths(api_client):
    seed = _seed_draft_with_photo_and_video(config.MEDIA_DIR)
    owner = _owner(api_client)
    base = f"/api/v1/media/galleries/{seed['gallery_id']}/assets/{seed['video_id']}"

    poster = api_client.get(f"{base}/poster", headers=owner)
    assert poster.status_code == 200
    assert poster.content == b"video-poster"
    assert "image" in poster.headers.get("content-type", "")

    playback = api_client.get(f"{base}/preview", headers=owner)
    assert playback.status_code == 200
    assert playback.content == b"video-mp4-bytes-not-an-image"
    # Must not be forced through an image media type for the MP4 derivative.
    ctype = playback.headers.get("content-type", "")
    assert "image/" not in ctype
    assert playback.content != poster.content

    # Photos never expose a poster variant.
    photo_base = f"/api/v1/media/galleries/{seed['gallery_id']}/assets/{seed['photo_id']}"
    assert api_client.get(f"{photo_base}/poster", headers=owner).status_code == 404


def test_build_media_links_split_video_poster_and_preview(api_client):
    seed = _seed_draft_with_photo_and_video(config.MEDIA_DIR)
    # Use a request-like origin via TestClient path construction helper.
    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"host", b"studio.test")],
        "client": ("testclient", 50000),
        "server": ("studio.test", 443),
    }
    request = Request(scope)
    links = build_media_links(request, seed["gallery_id"], seed["video_id"], "video")
    assert links["poster_url"] and links["poster_url"].endswith("/poster")
    assert links["preview_url"] and links["preview_url"].endswith("/preview")
    assert links["poster_url"] != links["preview_url"]

    asset = db.one("SELECT * FROM assets WHERE id=?", (seed["video_id"],))
    poster_path = _resolve_path(seed["gallery_id"], asset, "poster")
    preview_path = _resolve_path(seed["gallery_id"], asset, "preview")
    assert poster_path.name.endswith("_poster.jpg")
    assert preview_path.suffix == ".mp4"
