"""Focused capability, delivery-gate, and native gallery interaction tests."""

import datetime as dt
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app import config, db, mobile_gallery_delivery_api, ratelimit
from app.main import app

pytestmark = pytest.mark.unit


def _device() -> dict:
    return {
        "installation_id": "A9CF660A-C218-4E8A-9D93-77B30AF2CD51",
        "name": "Client iPhone",
        "platform": "ios",
        "app_version": "3.0",
    }


@pytest.fixture
def delivery_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "client-gallery-delivery-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(config, "CULL_UI", True)
    monkeypatch.setattr(
        mobile_gallery_delivery_api,
        "_studio_today",
        lambda: dt.date(2026, 7, 10),
    )
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    yield client
    client.close()
    ratelimit._hits.clear()


def _unlock(client: TestClient, slug: str = "client-gallery", pin: str = "2468") -> dict[str, str]:
    response = client.post(
        "/api/v1/client-auth/gallery/unlock",
        json={"kind": "gallery", "slug": slug, "pin": pin, "device": _device()},
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


def _asset(
    gallery_id: int,
    *,
    section_id: int | None,
    kind: str,
    filename: str,
    stored: str,
    status: str = "ready",
    cull_state: str | None = None,
    position: int = 0,
    duration: float | None = None,
) -> int:
    return db.run(
        """INSERT INTO assets
           (gallery_id,section_id,kind,filename,stored,status,position,cull_state,duration,
            created_at)
           VALUES (?,?,?,?,?,?,?,?,?,'2026-07-10 12:00:00')""",
        (
            gallery_id,
            section_id,
            kind,
            filename,
            stored,
            status,
            position,
            cull_state,
            duration,
        ),
    )


def _write_media(gallery_id: int, stored: str, kind: str, content: bytes) -> None:
    base = config.MEDIA_DIR / str(gallery_id)
    for directory in ("original", "thumb", "web"):
        (base / directory).mkdir(parents=True, exist_ok=True)
    stem = stored.rsplit(".", 1)[0]
    (base / "original" / stored).write_bytes(content)
    (base / "thumb" / f"{stem}.jpg").write_bytes(b"thumb-" + content)
    if kind == "video":
        (base / "web" / f"{stem}.mp4").write_bytes(b"preview-" + content)
        (base / "web" / f"{stem}_poster.jpg").write_bytes(b"poster-" + content)
    else:
        (base / "web" / f"{stem}.jpg").write_bytes(b"preview-" + content)


def _seed_delivery() -> dict[str, int]:
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,client_name,pin,published,content_rev,type,require_pin,expires_at,
            created_at)
           VALUES (?,?,?,?,1,9,'gallery',1,'2099-12-31','2026-07-10 10:00:00')""",
        ("client-gallery", "Client Gallery", "A. Client", "2468"),
    )
    section_id = db.run(
        "INSERT INTO sections (gallery_id,name,position,proof_target) VALUES (?,?,0,1)",
        (
            gallery_id,
            "Proofs",
        ),
    )
    photo_id = _asset(
        gallery_id,
        section_id=section_id,
        kind="photo",
        filename="Photo One.jpg",
        stored="photo-one.jpg",
        cull_state="keep",
        position=1,
    )
    second_photo_id = _asset(
        gallery_id,
        section_id=section_id,
        kind="photo",
        filename="Photo Two.jpg",
        stored="photo-two.jpg",
        cull_state="keep",
        position=2,
    )
    video_id = _asset(
        gallery_id,
        section_id=None,
        kind="video",
        filename="Review Film.mov",
        stored="review-film.mov",
        cull_state="keep",
        position=3,
        duration=120.0,
    )
    cut_id = _asset(
        gallery_id,
        section_id=section_id,
        kind="photo",
        filename="Do Not Deliver.jpg",
        stored="cut.jpg",
        cull_state="cut",
        position=4,
    )
    pending_id = _asset(
        gallery_id,
        section_id=section_id,
        kind="photo",
        filename="Pending.jpg",
        stored="pending.jpg",
        status="pending",
        position=5,
    )

    other_gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,pin,published,type,require_pin,expires_at,created_at)
           VALUES ('other-gallery','Other Gallery','1357',1,'gallery',1,'2099-12-31',
                   '2026-07-10 11:00:00')"""
    )
    other_section_id = db.run(
        "INSERT INTO sections (gallery_id,name,position) VALUES (?,'Other',0)",
        (other_gallery_id,),
    )
    other_asset_id = _asset(
        other_gallery_id,
        section_id=other_section_id,
        kind="video",
        filename="Other.mov",
        stored="other.mov",
        cull_state="keep",
        duration=60.0,
    )
    bad_parent_id = _asset(
        gallery_id,
        section_id=other_section_id,
        kind="photo",
        filename="Bad Parent.jpg",
        stored="bad-parent.jpg",
        cull_state="keep",
        position=6,
    )
    traversal_id = _asset(
        gallery_id,
        section_id=None,
        kind="photo",
        filename="Traversal.jpg",
        stored="../../outside.jpg",
        cull_state="keep",
        position=7,
    )
    missing_id = _asset(
        gallery_id,
        section_id=None,
        kind="photo",
        filename="Missing Derivatives.jpg",
        stored="missing.jpg",
        cull_state="keep",
        position=8,
    )

    for stored, kind, content in (
        ("photo-one.jpg", "photo", b"0123456789"),
        ("photo-two.jpg", "photo", b"abcdefghij"),
        ("review-film.mov", "video", b"video-bytes"),
        ("cut.jpg", "photo", b"secret-cut"),
        ("pending.jpg", "photo", b"secret-pending"),
    ):
        _write_media(gallery_id, stored, kind, content)
    _write_media(other_gallery_id, "other.mov", "video", b"other-secret")
    db.run(
        "UPDATE galleries SET cover_asset_id=?, argus_hero_asset_ids=? WHERE id=?",
        (photo_id, json.dumps([photo_id, cut_id, other_asset_id]), gallery_id),
    )
    return {
        "gallery": gallery_id,
        "section": section_id,
        "photo": photo_id,
        "second_photo": second_photo_id,
        "video": video_id,
        "cut": cut_id,
        "pending": pending_id,
        "other_gallery": other_gallery_id,
        "other_asset": other_asset_id,
        "bad_parent": bad_parent_id,
        "traversal": traversal_id,
        "missing": missing_id,
    }


def test_manifest_is_exact_guest_scoped_cacheable_and_secret_free(delivery_client):
    seeded = _seed_delivery()
    path = "/api/v1/client/gallery"
    assert delivery_client.get(path).status_code == 401
    assert delivery_client.get(path, headers=_owner_headers(delivery_client)).status_code == 403

    headers = _unlock(delivery_client)
    response = delivery_client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["vary"] == "Authorization"
    payload = response.json()
    assert payload["summary"]["id"] == seeded["gallery"]
    assert payload["summary"]["client_id"] is None
    assert payload["summary"]["project_id"] is None
    assert payload["summary"]["delivery_state"] == "proofing"
    assert payload["summary"]["favorite_count"] == 0
    assert payload["hero_asset_ids"] == [seeded["photo"]]
    assert payload["vision"] is None
    assert payload["cull_enabled"] is False
    delivered_ids = {asset["id"] for asset in payload["assets"]}
    assert delivered_ids == {
        seeded["photo"],
        seeded["second_photo"],
        seeded["video"],
        seeded["traversal"],
        seeded["missing"],
    }
    photo = next(asset for asset in payload["assets"] if asset["id"] == seeded["photo"])
    assert photo["favorite_count"] == 0
    assert photo["keeper_score"] is None
    assert photo["hero_potential"] is None
    assert photo["cull_state"] is None
    assert photo["links"]["thumbnail_url"] == (
        f"https://studio.test/api/v1/client/gallery/assets/{seeded['photo']}/thumbnail"
    )
    assert photo["links"]["poster_url"] is None
    video = next(asset for asset in payload["assets"] if asset["id"] == seeded["video"])
    assert video["links"]["poster_url"].endswith(f"/{seeded['video']}/poster")
    missing = next(asset for asset in payload["assets"] if asset["id"] == seeded["missing"])
    assert set(missing["links"].values()) == {None}
    traversal = next(asset for asset in payload["assets"] if asset["id"] == seeded["traversal"])
    assert set(traversal["links"].values()) == {None}

    serialized = json.dumps(payload)
    for secret in (
        "2468",
        "photo-one.jpg",
        "../../outside.jpg",
        "secret-cut",
        "other-secret",
    ):
        # Original client filename is intentionally present; stored basenames are not.
        assert secret not in serialized

    etag = response.headers["etag"]
    cached = delivery_client.get(path, headers={**headers, "If-None-Match": f"W/{etag}"})
    assert cached.status_code == 304
    assert cached.content == b""

    wrong_host = delivery_client.get("https://other.test/api/v1/client/gallery", headers=headers)
    assert wrong_host.status_code == 401


def test_live_ready_parent_and_cull_gates_revoke_every_delivery_path(delivery_client):
    seeded = _seed_delivery()
    headers = _unlock(delivery_client)
    base = "/api/v1/client/gallery/assets"

    for asset_id in (seeded["cut"], seeded["pending"], seeded["bad_parent"]):
        assert (
            delivery_client.get(f"{base}/{asset_id}/thumbnail", headers=headers).status_code == 404
        )
        assert (
            delivery_client.put(f"{base}/{asset_id}/favorite", headers=headers).status_code == 404
        )
    assert (
        delivery_client.get(f"{base}/{seeded['other_asset']}/preview", headers=headers).status_code
        == 404
    )
    assert (
        delivery_client.get(f"{base}/{seeded['photo']}/poster", headers=headers).status_code == 404
    )
    assert (
        delivery_client.get(f"{base}/{seeded['traversal']}/download", headers=headers).status_code
        == 404
    )

    db.run("UPDATE galleries SET published=0 WHERE id=?", (seeded["gallery"],))
    assert delivery_client.get("/api/v1/client/gallery", headers=headers).status_code == 401

    db.run(
        "UPDATE galleries SET published=1, expires_at=? WHERE id=?",
        ((dt.date(2026, 7, 10) - dt.timedelta(days=1)).isoformat(), seeded["gallery"]),
    )
    assert (
        delivery_client.get(f"{base}/{seeded['photo']}/thumbnail", headers=headers).status_code
        == 401
    )


def test_each_action_requires_its_exact_gallery_scope(delivery_client):
    seeded = _seed_delivery()
    headers = _unlock(delivery_client)
    session = db.one(
        """SELECT id FROM api_sessions
            WHERE principal_kind='gallery_guest' AND resource_id=?
            ORDER BY created_at DESC LIMIT 1""",
        (seeded["gallery"],),
    )
    db.run(
        "UPDATE api_sessions SET scopes_json=? WHERE id=?",
        (json.dumps([f"gallery:{seeded['gallery']}:read"]), session["id"]),
    )
    base = f"/api/v1/client/gallery/assets/{seeded['video']}"

    assert delivery_client.get("/api/v1/client/gallery", headers=headers).status_code == 200
    assert delivery_client.get(f"{base}/preview", headers=headers).status_code == 200
    assert delivery_client.get(f"{base}/download", headers=headers).status_code == 403
    assert delivery_client.put(f"{base}/favorite", headers=headers).status_code == 403
    assert delivery_client.get(f"{base}/comments", headers=headers).status_code == 403


def test_media_supports_private_etags_ranges_downloads_and_safe_disposition(delivery_client):
    seeded = _seed_delivery()
    headers = _unlock(delivery_client)
    base = f"/api/v1/client/gallery/assets/{seeded['video']}"

    ranged = delivery_client.get(f"{base}/preview", headers={**headers, "Range": "bytes=1-4"})
    assert ranged.status_code == 206
    assert ranged.content == b"revi"
    assert ranged.headers["content-range"] == "bytes 1-4/19"
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.headers["cache-control"] == "private, max-age=86400"
    assert ranged.headers["vary"] == "Authorization"
    etag = ranged.headers["etag"]

    cached = delivery_client.get(f"{base}/preview", headers={**headers, "If-None-Match": etag})
    assert cached.status_code == 304
    poster = delivery_client.get(f"{base}/poster", headers=headers)
    assert poster.status_code == 200
    assert poster.headers["content-type"].startswith("image/jpeg")

    download = delivery_client.get(f"{base}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == b"video-bytes"
    assert download.headers["content-type"].startswith("application/octet-stream")
    assert download.headers["cache-control"] == "private, no-cache"
    assert "filename*=utf-8''Review%20Film.mov" in download.headers["content-disposition"]
    logged = db.one(
        """SELECT COUNT(*) AS n FROM downloads
            WHERE gallery_id=? AND asset_id=? AND visitor_id IS NOT NULL""",
        (seeded["gallery"], seeded["video"]),
    )
    assert logged["n"] == 1


def test_symlinked_variant_directory_cannot_cross_gallery_root(delivery_client):
    seeded = _seed_delivery()
    headers = _unlock(delivery_client)
    thumb_dir = config.MEDIA_DIR / str(seeded["gallery"]) / "thumb"
    other_gallery_thumb = config.MEDIA_DIR / str(seeded["other_gallery"]) / "thumb"
    (other_gallery_thumb / "photo-one.jpg").write_bytes(b"must-not-cross-gallery")
    shutil.rmtree(thumb_dir)
    thumb_dir.symlink_to(other_gallery_thumb, target_is_directory=True)

    path = f"/api/v1/client/gallery/assets/{seeded['photo']}/thumbnail"
    assert delivery_client.get(path, headers=headers).status_code == 404
    manifest = delivery_client.get("/api/v1/client/gallery", headers=headers).json()
    photo = next(asset for asset in manifest["assets"] if asset["id"] == seeded["photo"])
    assert photo["links"]["thumbnail_url"] is None
    assert photo["links"]["preview_url"] is not None


def test_studio_clock_controls_expiring_state_and_delivery_revocation(delivery_client, monkeypatch):
    seeded = _seed_delivery()
    db.run("UPDATE galleries SET expires_at='2026-07-16' WHERE id=?", (seeded["gallery"],))
    headers = _unlock(delivery_client)

    live = delivery_client.get("/api/v1/client/gallery", headers=headers)
    assert live.status_code == 200
    assert live.json()["summary"]["delivery_state"] == "expiring"

    monkeypatch.setattr(
        mobile_gallery_delivery_api,
        "_studio_today",
        lambda: dt.date(2026, 7, 17),
    )
    assert delivery_client.get("/api/v1/client/gallery", headers=headers).status_code == 410


def test_favorites_are_visitor_idempotent_and_preserve_proof_cap(delivery_client):
    seeded = _seed_delivery()
    headers = _unlock(delivery_client)
    first = f"/api/v1/client/gallery/assets/{seeded['photo']}/favorite"
    second = f"/api/v1/client/gallery/assets/{seeded['second_photo']}/favorite"

    selected = delivery_client.put(first, headers=headers)
    assert selected.status_code == 200
    assert selected.json() == {
        "asset_id": seeded["photo"],
        "selected": True,
        "section_selected_count": 1,
        "section_proof_target": 1,
    }
    assert delivery_client.put(first, headers=headers).json() == selected.json()
    assert delivery_client.put(second, headers=headers).status_code == 409
    assert (
        db.one("SELECT COUNT(*) AS n FROM favorites WHERE asset_id=?", (seeded["photo"],))["n"] == 1
    )

    removed = delivery_client.delete(first, headers=headers)
    assert removed.status_code == 200
    assert removed.json()["selected"] is False
    assert removed.json()["section_selected_count"] == 0
    assert delivery_client.delete(first, headers=headers).json() == removed.json()
    assert delivery_client.put(second, headers=headers).status_code == 200

    # A second unlock is a distinct visitor and gets an independent proof budget.
    other_headers = _unlock(delivery_client)
    assert delivery_client.put(first, headers=other_headers).status_code == 200
    manifest = delivery_client.get("/api/v1/client/gallery", headers=other_headers).json()
    assert manifest["summary"]["favorite_count"] == 1
    assert next(asset for asset in manifest["assets"] if asset["id"] == seeded["photo"])[
        "is_favorite"
    ]


def test_video_comments_validate_parent_body_timecode_and_reopen_thread(delivery_client):
    seeded = _seed_delivery()
    headers = _unlock(delivery_client)
    path = f"/api/v1/client/gallery/assets/{seeded['video']}/comments"

    top = delivery_client.post(
        path,
        headers=headers,
        json={"body": "Change the opening frame", "timecode_seconds": 12.5},
    )
    assert top.status_code == 201
    assert top.json() == {
        "id": top.json()["id"],
        "asset_id": seeded["video"],
        "parent_id": None,
        "timecode_seconds": 12.5,
        "body": "Change the opening frame",
        "author_role": "client",
        "status": "open",
        "created_at": top.json()["created_at"],
    }
    top_id = top.json()["id"]
    db.run("UPDATE video_comments SET status='resolved' WHERE id=?", (top_id,))

    reply = delivery_client.post(
        path,
        headers=headers,
        json={"body": "Here is more detail", "timecode_seconds": 99.0, "parent_id": top_id},
    )
    assert reply.status_code == 201
    assert reply.json()["parent_id"] == top_id
    assert reply.json()["timecode_seconds"] == 12.5
    assert reply.json()["status"] == "open"

    listed = delivery_client.get(path, headers=headers)
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "private, no-cache"
    assert [comment["id"] for comment in listed.json()] == [top_id, reply.json()["id"]]
    assert all(comment["asset_id"] == seeded["video"] for comment in listed.json())
    assert all(comment["status"] == "open" for comment in listed.json())
    assert (
        delivery_client.get(
            path, headers={**headers, "If-None-Match": listed.headers["etag"]}
        ).status_code
        == 304
    )

    other_comment = db.run(
        """INSERT INTO video_comments
           (asset_id,gallery_id,author_role,timecode,body)
           VALUES (?,?, 'admin',5,'Other gallery')""",
        (seeded["other_asset"], seeded["other_gallery"]),
    )
    assert (
        delivery_client.post(
            path,
            headers=headers,
            json={"body": "Cross reply", "parent_id": other_comment},
        ).status_code
        == 400
    )
    for invalid in (
        {"body": "   "},
        {"body": "No", "timecode_seconds": -1},
        {"body": "x" * 4001},
        {"body": "Unknown field", "unexpected": "secret"},
    ):
        response = delivery_client.post(path, headers=headers, json=invalid)
        assert response.status_code == 422
        assert "secret" not in response.text

    photo_comments = f"/api/v1/client/gallery/assets/{seeded['photo']}/comments"
    assert delivery_client.get(photo_comments, headers=headers).status_code == 404
    assert delivery_client.get(f"{path}?limit=501", headers=headers).status_code == 422
