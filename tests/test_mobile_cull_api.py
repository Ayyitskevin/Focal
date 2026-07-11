"""Owner-only native cull review, media, and reversible decision contracts."""

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import audit, config, db, mobile_cull_api, ratelimit, saas
from app.main import app

pytestmark = pytest.mark.unit


def _device(name: str) -> dict:
    return {
        "installation_id": "9C5E868F-469F-4D17-B3DC-55DC1859D72A",
        "name": name,
        "platform": "ios",
        "app_version": "5.0",
    }


@pytest.fixture
def cull_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "native-cull-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(config, "CULL_UI", True)
    ratelimit._hits.clear()
    db.migrate()
    client = TestClient(app, base_url="https://studio.test")
    yield client
    client.close()
    ratelimit._hits.clear()


def _owner_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/studio/login",
        json={"email": None, "password": "owner-password", "device": _device("Owner iPhone")},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _hosted_owner_headers(
    client: TestClient,
    *,
    email: str,
    password: str,
    name: str,
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


def _configure_hosted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "native-cull-hosted-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    monkeypatch.setattr(config, "CULL_UI", True)
    ratelimit._hits.clear()
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _guest_headers(client: TestClient, slug: str) -> dict[str, str]:
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


def _asset(
    gallery_id: int,
    *,
    filename: str,
    stored: str,
    score: float | None,
    state: str | None,
    position: int,
    kind: str = "photo",
    status: str = "ready",
) -> int:
    return db.run(
        """INSERT INTO assets
           (gallery_id,kind,filename,stored,status,position,argus_keeper_score,
            argus_hero_potential,cull_state,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,'2026-07-11 09:00:00')""",
        (
            gallery_id,
            kind,
            filename,
            stored,
            status,
            position,
            score,
            score / 2 if score is not None else None,
            state,
        ),
    )


def _write_derivatives(gallery_id: int, stored: str, content: bytes) -> None:
    root = Path(config.MEDIA_DIR) / str(gallery_id)
    (root / "thumb").mkdir(parents=True, exist_ok=True)
    (root / "web").mkdir(parents=True, exist_ok=True)
    stem = Path(stored).stem
    (root / "thumb" / f"{stem}.jpg").write_bytes(b"thumb-" + content)
    (root / "web" / f"{stem}.jpg").write_bytes(b"preview-" + content)


def _seed_cull() -> dict[str, int]:
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,pin,published,type,require_pin,content_rev,created_at)
           VALUES ('native-cull','Native Cull','2468',1,'gallery',1,5,
                   '2026-07-11 09:00:00')"""
    )
    high_id = _asset(
        gallery_id,
        filename=r"C:\private\high.jpg",
        stored="high.jpg",
        score=0.95,
        state="cut",
        position=1,
    )
    mid_id = _asset(
        gallery_id,
        filename="mid.jpg",
        stored="mid.jpg",
        score=0.6,
        state="keep",
        position=2,
    )
    low_id = _asset(
        gallery_id,
        filename="low.jpg",
        stored="low.jpg",
        score=0.2,
        state=None,
        position=3,
    )
    unscored_id = _asset(
        gallery_id,
        filename="unscored.jpg",
        stored="unscored.jpg",
        score=None,
        state=None,
        position=0,
    )
    pending_id = _asset(
        gallery_id,
        filename="pending.jpg",
        stored="pending.jpg",
        score=1,
        state=None,
        position=0,
        status="pending",
    )
    video_id = _asset(
        gallery_id,
        filename="video.mp4",
        stored="video.mp4",
        score=1,
        state=None,
        position=0,
        kind="video",
    )
    other_gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,pin,published,type,require_pin,created_at)
           VALUES ('other-cull','Other','1357',1,'gallery',1,'2026-07-11 09:00:00')"""
    )
    foreign_id = _asset(
        other_gallery_id,
        filename="foreign.jpg",
        stored="foreign.jpg",
        score=0.99,
        state=None,
        position=0,
    )
    _write_derivatives(gallery_id, "high.jpg", b"high")
    _write_derivatives(gallery_id, "mid.jpg", b"mid")
    return {
        "gallery": gallery_id,
        "high": high_id,
        "mid": mid_id,
        "low": low_id,
        "unscored": unscored_id,
        "pending": pending_id,
        "video": video_id,
        "other_gallery": other_gallery_id,
        "foreign": foreign_id,
    }


def _seed_hosted_cull(tenant: dict, label: str) -> tuple[int, list[int], Path]:
    with saas.tenant_runtime(tenant):
        gallery_id = db.run(
            """INSERT INTO galleries
               (slug,title,pin,published,type,require_pin,content_rev,created_at)
               VALUES ('native-cull','Native Cull','2468',1,'gallery',1,5,
                       '2026-07-11 09:00:00')"""
        )
        asset_ids = [
            _asset(
                gallery_id,
                filename=f"{label}-high.jpg",
                stored="shared-high.jpg",
                score=0.9,
                state=None,
                position=1,
            ),
            _asset(
                gallery_id,
                filename=f"{label}-low.jpg",
                stored="shared-low.jpg",
                score=0.4,
                state=None,
                position=2,
            ),
        ]
        _write_derivatives(gallery_id, "shared-high.jpg", label.encode())
        _write_derivatives(gallery_id, "shared-low.jpg", f"{label}-low".encode())
        media_root = Path(config.MEDIA_DIR)
    return gallery_id, asset_ids, media_root


def _item(client: TestClient, headers: dict[str, str], gallery_id: int, asset_id: int) -> dict:
    response = client.get(
        f"/api/v1/galleries/{gallery_id}/cull?limit=100",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["items"] if item["asset_id"] == asset_id)


def _command_headers(
    headers: dict[str, str],
    etag: str | None,
    key: uuid.UUID | None = None,
) -> dict[str, str]:
    result = {**headers, "Idempotency-Key": str(key or uuid.uuid4())}
    if etag is not None:
        result["If-Match"] = etag
    return result


def test_cull_page_is_bounded_safe_conditional_and_includes_cut_assets(cull_client):
    ids = _seed_cull()
    assert cull_client.get(f"/api/v1/galleries/{ids['gallery']}/cull").status_code == 401
    headers = _owner_headers(cull_client)

    first = cull_client.get(
        f"/api/v1/galleries/{ids['gallery']}/cull?limit=2",
        headers=headers,
    )
    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, no-cache"
    assert first.headers["vary"] == "Authorization"
    payload = first.json()
    assert [item["asset_id"] for item in payload["items"]] == [ids["high"], ids["mid"]]
    assert payload["items"][0]["state"] == "cut"
    assert payload["items"][0]["filename"] == "high.jpg"
    assert payload["counts"] == {
        "total": 4,
        "keep": 1,
        "cut": 1,
        "undecided": 2,
        "scored": 3,
    }
    assert payload["has_more"] is True
    assert payload["next_cursor"]
    assert payload["items"][0]["thumbnail_url"] == (
        f"https://studio.test/api/v1/galleries/{ids['gallery']}/cull/assets/{ids['high']}/thumbnail"
    )
    assert payload["items"][0]["preview_url"].endswith(f"/{ids['high']}/preview")
    assert payload["items"][0]["etag"].startswith('"cull-asset-')

    cached = cull_client.get(
        f"/api/v1/galleries/{ids['gallery']}/cull?limit=2",
        headers={**headers, "If-None-Match": f"W/{first.headers['etag']}"},
    )
    assert cached.status_code == 304
    assert cached.content == b""

    second = cull_client.get(
        f"/api/v1/galleries/{ids['gallery']}/cull?limit=2&cursor={payload['next_cursor']}",
        headers=headers,
    )
    assert second.status_code == 200
    assert [item["asset_id"] for item in second.json()["items"]] == [
        ids["low"],
        ids["unscored"],
    ]
    assert second.json()["items"][0]["thumbnail_url"] is None
    assert second.json()["has_more"] is False

    serialized = json.dumps(payload)
    for secret in ("2468", "C:\\private", "high.jpg.jpg", str(config.MEDIA_DIR)):
        assert secret not in serialized
    returned_ids = {item["asset_id"] for item in payload["items"] + second.json()["items"]}
    assert returned_ids.isdisjoint({ids["pending"], ids["video"], ids["foreign"]})

    cursor = payload["next_cursor"]
    tampered = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    assert (
        cull_client.get(
            f"/api/v1/galleries/{ids['gallery']}/cull?cursor={tampered}",
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        cull_client.get(
            f"/api/v1/galleries/{ids['gallery']}/cull?limit=101",
            headers=headers,
        ).status_code
        == 422
    )
    assert cull_client.get("/api/v1/galleries/999999/cull", headers=headers).status_code == 404


def test_cull_media_is_exact_owner_only_scoped_and_conditional(cull_client, tmp_path):
    ids = _seed_cull()
    owner = _owner_headers(cull_client)
    guest = _guest_headers(cull_client, "native-cull")
    base = f"/api/v1/galleries/{ids['gallery']}/cull/assets/{ids['high']}"

    assert (
        cull_client.get(
            f"/api/v1/galleries/{ids['gallery']}/cull",
            headers=guest,
        ).status_code
        == 403
    )
    assert cull_client.get(f"{base}/preview", headers=guest).status_code == 403
    assert (
        cull_client.patch(
            f"/api/v1/galleries/{ids['gallery']}/assets/{ids['high']}/cull",
            headers=_command_headers(guest, '"any-version"'),
            json={"action": "keep"},
        ).status_code
        == 403
    )
    preview = cull_client.get(f"{base}/preview", headers=owner)
    assert preview.status_code == 200
    assert preview.content == b"preview-high"
    assert preview.headers["content-type"] == "image/jpeg"
    assert preview.headers["cache-control"] == "private, max-age=86400"
    assert preview.headers["vary"] == "Authorization"
    assert (
        cull_client.get(
            f"{base}/preview",
            headers={**owner, "If-None-Match": preview.headers["etag"]},
        ).status_code
        == 304
    )
    preview_path = Path(config.MEDIA_DIR) / str(ids["gallery"]) / "web" / "high.jpg"
    old_stat = preview_path.stat()
    replacement = preview_path.with_suffix(".replacement")
    replacement.write_bytes(b"changed-high")
    os.utime(
        replacement,
        ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns),
    )
    replacement.replace(preview_path)
    replaced_preview = cull_client.get(
        f"{base}/preview",
        headers={**owner, "If-None-Match": preview.headers["etag"]},
    )
    assert replaced_preview.status_code == 200
    assert replaced_preview.content == b"changed-high"
    assert replaced_preview.headers["etag"] != preview.headers["etag"]

    thumbnail = cull_client.get(f"{base}/thumbnail", headers=owner)
    assert thumbnail.status_code == 200
    assert thumbnail.content == b"thumb-high"
    assert cull_client.get(f"{base}/download", headers=owner).status_code == 404
    assert (
        cull_client.get(
            f"/api/v1/galleries/{ids['other_gallery']}/cull/assets/{ids['high']}/preview",
            headers=owner,
        ).status_code
        == 404
    )
    assert (
        cull_client.get(
            f"/api/v1/galleries/{ids['gallery']}/cull/assets/{ids['low']}/preview",
            headers=owner,
        ).status_code
        == 404
    )

    # A derivative symlink is rejected even when it points to a readable JPEG.
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    thumb_path = Path(config.MEDIA_DIR) / str(ids["gallery"]) / "thumb" / "high.jpg"
    thumb_path.unlink()
    thumb_path.symlink_to(outside)
    assert cull_client.get(f"{base}/thumbnail", headers=owner).status_code == 404

    wrong_host = cull_client.get(
        f"https://other.test/api/v1/galleries/{ids['gallery']}/cull",
        headers=owner,
    )
    assert wrong_host.status_code == 401


def test_drop_gallery_is_rejected_by_every_owner_cull_surface(cull_client):
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,pin,published,type,require_pin,content_rev,created_at)
           VALUES ('native-drop','Native Drop','unused',1,'drop',0,7,
                   '2026-07-11 09:00:00')"""
    )
    asset_id = _asset(
        gallery_id,
        filename="drop-frame.jpg",
        stored="drop-frame.jpg",
        score=0.8,
        state=None,
        position=1,
    )
    _write_derivatives(gallery_id, "drop-frame.jpg", b"drop")
    owner = _owner_headers(cull_client)

    page = cull_client.get(f"/api/v1/galleries/{gallery_id}/cull", headers=owner)
    media = cull_client.get(
        f"/api/v1/galleries/{gallery_id}/cull/assets/{asset_id}/preview",
        headers=owner,
    )
    decision = cull_client.patch(
        f"/api/v1/galleries/{gallery_id}/assets/{asset_id}/cull",
        headers=_command_headers(owner, '"drop-version"'),
        json={"action": "cut"},
    )

    assert page.status_code == 404
    assert page.json()["code"] == "cull.gallery_not_found"
    assert media.status_code == 404
    assert media.json()["code"] == "cull.asset_not_found"
    assert decision.status_code == 404
    assert decision.json()["code"] == "cull.asset_not_found"
    row = db.one(
        "SELECT status, cull_state FROM assets WHERE id=? AND gallery_id=?",
        (asset_id, gallery_id),
    )
    assert tuple(row) == ("ready", None)
    assert db.one("SELECT content_rev FROM galleries WHERE id=?", (gallery_id,))["content_rev"] == 7
    assert db.one("SELECT COUNT(*) AS n FROM mobile_commands")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM audit_log")["n"] == 0


def test_cull_decision_is_replay_safe_versioned_audited_and_reversible(cull_client):
    ids = _seed_cull()
    owner = _owner_headers(cull_client)
    item = _item(cull_client, owner, ids["gallery"], ids["low"])
    path = f"/api/v1/galleries/{ids['gallery']}/assets/{ids['low']}/cull"
    before_revision = db.one(
        "SELECT content_rev FROM galleries WHERE id=?",
        (ids["gallery"],),
    )["content_rev"]

    missing_key = cull_client.patch(
        path,
        headers={**owner, "If-Match": item["etag"]},
        json={"action": "cut"},
    )
    assert missing_key.status_code == 422
    assert missing_key.json()["code"] == "request.idempotency_required"

    missing_match = cull_client.patch(
        path,
        headers=_command_headers(owner, None),
        json={"action": "cut"},
    )
    assert missing_match.status_code == 422
    assert missing_match.json()["code"] == "resource.if_match_required"

    for unsafe in ("*", f"W/{item['etag']}", '"stale"'):
        rejected = cull_client.patch(
            path,
            headers=_command_headers(owner, unsafe),
            json={"action": "cut"},
        )
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "resource.version_conflict"

    key = uuid.uuid4()
    command_headers = _command_headers(owner, item["etag"], key)
    changed = cull_client.patch(path, headers=command_headers, json={"action": "cut"})
    replay = cull_client.patch(path, headers=command_headers, json={"action": "cut"})
    assert changed.status_code == replay.status_code == 200
    assert changed.json()["state"] == "cut"
    assert changed.json()["media_revision"] == item["media_revision"]
    assert changed.headers["etag"] == changed.json()["etag"]
    assert changed.headers["cache-control"] == "no-store"
    assert replay.json() == changed.json()
    assert replay.headers["idempotency-replayed"] == "true"

    row = db.one(
        """SELECT status, stored, cull_state, cull_source, cull_decided_at
             FROM assets WHERE id=?""",
        (ids["low"],),
    )
    assert row["status"] == "ready"
    assert row["stored"] == "low.jpg"
    assert row["cull_state"] == "cut"
    assert row["cull_source"] == "manual"
    assert row["cull_decided_at"]
    assert (
        db.one("SELECT content_rev FROM galleries WHERE id=?", (ids["gallery"],))["content_rev"]
        == before_revision + 1
    )
    evidence = db.one(
        """SELECT action, actor FROM audit_log
            WHERE entity_type='asset' AND entity_id=?""",
        (ids["low"],),
    )
    assert dict(evidence) == {"action": "cull:cut", "actor": "mobile_owner"}
    assert (
        db.one(
            "SELECT COUNT(*) AS n FROM mobile_commands WHERE idempotency_key=?",
            (str(key),),
        )["n"]
        == 1
    )

    conflict = cull_client.patch(path, headers=command_headers, json={"action": "keep"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "request.idempotency_conflict"

    restored = cull_client.patch(
        path,
        headers=_command_headers(owner, changed.json()["etag"]),
        json={"action": "restore"},
    )
    assert restored.status_code == 200
    assert restored.json()["state"] is None
    restored_row = db.one(
        "SELECT cull_state, cull_source, cull_decided_at FROM assets WHERE id=?",
        (ids["low"],),
    )
    assert tuple(restored_row) == (None, None, None)
    assert (
        db.one("SELECT content_rev FROM galleries WHERE id=?", (ids["gallery"],))["content_rev"]
        == before_revision + 2
    )

    # The state is undecided again, but the audit revision prevents an old
    # pre-cut validator from succeeding after this ABA transition.
    aba = cull_client.patch(
        path,
        headers=_command_headers(owner, item["etag"]),
        json={"action": "keep"},
    )
    assert aba.status_code == 409
    assert aba.json()["code"] == "resource.version_conflict"

    foreign = _item(cull_client, owner, ids["other_gallery"], ids["foreign"])
    cross_gallery = cull_client.patch(
        f"/api/v1/galleries/{ids['gallery']}/assets/{ids['foreign']}/cull",
        headers=_command_headers(owner, foreign["etag"]),
        json={"action": "cut"},
    )
    assert cross_gallery.status_code == 404
    assert (
        db.one("SELECT cull_state FROM assets WHERE id=?", (ids["foreign"],))["cull_state"] is None
    )


def test_cull_failure_rolls_back_and_feature_flag_fails_closed(
    cull_client,
    monkeypatch,
):
    ids = _seed_cull()
    owner = _owner_headers(cull_client)
    item = _item(cull_client, owner, ids["gallery"], ids["low"])
    path = f"/api/v1/galleries/{ids['gallery']}/assets/{ids['low']}/cull"
    before_revision = db.one(
        "SELECT content_rev FROM galleries WHERE id=?",
        (ids["gallery"],),
    )["content_rev"]

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    with monkeypatch.context() as context:
        context.setattr(audit, "log", fail_audit)
        failed = cull_client.patch(
            path,
            headers=_command_headers(owner, item["etag"]),
            json={"action": "cut"},
        )
    assert failed.status_code == 500
    assert db.one("SELECT cull_state FROM assets WHERE id=?", (ids["low"],))["cull_state"] is None
    assert (
        db.one("SELECT content_rev FROM galleries WHERE id=?", (ids["gallery"],))["content_rev"]
        == before_revision
    )
    assert db.one("SELECT COUNT(*) AS n FROM mobile_commands")["n"] == 0

    monkeypatch.setattr(config, "CULL_UI", False)
    assert (
        cull_client.get(f"/api/v1/galleries/{ids['gallery']}/cull", headers=owner).status_code
        == 404
    )
    assert (
        cull_client.get(
            f"/api/v1/galleries/{ids['gallery']}/cull/assets/{ids['high']}/preview",
            headers=owner,
        ).status_code
        == 404
    )
    disabled = cull_client.patch(
        path,
        headers=_command_headers(owner, item["etag"]),
        json={"action": "cut"},
    )
    assert disabled.status_code == 404
    assert db.one("SELECT cull_state FROM assets WHERE id=?", (ids["low"],))["cull_state"] is None


def test_cull_cursor_rejects_a_changed_score_snapshot(cull_client):
    ids = _seed_cull()
    owner = _owner_headers(cull_client)
    first = cull_client.get(
        f"/api/v1/galleries/{ids['gallery']}/cull?limit=1",
        headers=owner,
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]

    db.run(
        "UPDATE assets SET argus_keeper_score=0.99 WHERE id=? AND gallery_id=?",
        (ids["low"], ids["gallery"]),
    )
    changed = cull_client.get(
        f"/api/v1/galleries/{ids['gallery']}/cull?limit=1&cursor={cursor}",
        headers=owner,
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "pagination.collection_changed"


def test_cull_etag_tracks_reinserted_assets_and_replaced_derivatives(cull_client):
    ids = _seed_cull()
    owner = _owner_headers(cull_client)
    low = _item(cull_client, owner, ids["gallery"], ids["low"])
    db.run("DELETE FROM assets WHERE id=? AND gallery_id=?", (ids["low"], ids["gallery"]))
    db.run(
        """INSERT INTO assets
           (id,gallery_id,kind,filename,stored,status,position,argus_keeper_score,
            argus_hero_potential,created_at)
           VALUES (?,?,'photo','low.jpg','replacement.jpg','ready',3,0.2,0.1,
                   '2026-07-11 10:00:00')""",
        (ids["low"], ids["gallery"]),
    )
    stale_identity = cull_client.patch(
        f"/api/v1/galleries/{ids['gallery']}/assets/{ids['low']}/cull",
        headers=_command_headers(owner, low["etag"]),
        json={"action": "cut"},
    )
    assert stale_identity.status_code == 409
    assert stale_identity.json()["code"] == "resource.version_conflict"

    high = _item(cull_client, owner, ids["gallery"], ids["high"])
    preview = Path(config.MEDIA_DIR) / str(ids["gallery"]) / "web" / "high.jpg"
    preview.write_bytes(b"a-new-and-different-preview")
    refreshed_high = _item(cull_client, owner, ids["gallery"], ids["high"])
    assert refreshed_high["media_revision"] != high["media_revision"]
    stale_media = cull_client.patch(
        f"/api/v1/galleries/{ids['gallery']}/assets/{ids['high']}/cull",
        headers=_command_headers(owner, high["etag"]),
        json={"action": "keep"},
    )
    assert stale_media.status_code == 409
    assert stale_media.json()["code"] == "resource.version_conflict"


def test_cull_decision_rolls_back_if_review_media_changes_during_command(
    cull_client,
    monkeypatch,
):
    ids = _seed_cull()
    owner = _owner_headers(cull_client)
    item = _item(cull_client, owner, ids["gallery"], ids["low"])
    path = f"/api/v1/galleries/{ids['gallery']}/assets/{ids['low']}/cull"
    preview = Path(config.MEDIA_DIR) / str(ids["gallery"]) / "web" / "low.jpg"
    before_revision = db.one(
        "SELECT content_rev FROM galleries WHERE id=?",
        (ids["gallery"],),
    )["content_rev"]
    original = mobile_cull_api._cull_item
    calls = 0

    def replace_after_version_check(request, row):
        nonlocal calls
        value = original(request, row)
        calls += 1
        if calls == 1:
            preview.write_bytes(b"replacement-during-decision")
        return value

    monkeypatch.setattr(mobile_cull_api, "_cull_item", replace_after_version_check)
    changed = cull_client.patch(
        path,
        headers=_command_headers(owner, item["etag"]),
        json={"action": "cut"},
    )

    assert changed.status_code == 409
    assert changed.json()["code"] == "cull.media_changed"
    assert db.one("SELECT cull_state FROM assets WHERE id=?", (ids["low"],))["cull_state"] is None
    assert (
        db.one("SELECT content_rev FROM galleries WHERE id=?", (ids["gallery"],))["content_rev"]
        == before_revision
    )
    assert db.one("SELECT COUNT(*) AS n FROM mobile_commands")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM audit_log")["n"] == 0


def test_fresh_reaffirmation_is_audited_but_does_not_bump_delivery_revision(cull_client):
    ids = _seed_cull()
    owner = _owner_headers(cull_client)
    high = _item(cull_client, owner, ids["gallery"], ids["high"])
    before_revision = db.one(
        "SELECT content_rev FROM galleries WHERE id=?",
        (ids["gallery"],),
    )["content_rev"]
    key = uuid.uuid4()
    headers = _command_headers(owner, high["etag"], key)

    reaffirmed = cull_client.patch(
        f"/api/v1/galleries/{ids['gallery']}/assets/{ids['high']}/cull",
        headers=headers,
        json={"action": "cut"},
    )
    replay = cull_client.patch(
        f"/api/v1/galleries/{ids['gallery']}/assets/{ids['high']}/cull",
        headers=headers,
        json={"action": "cut"},
    )
    assert reaffirmed.status_code == replay.status_code == 200
    assert reaffirmed.json()["etag"] != high["etag"]
    assert replay.headers["idempotency-replayed"] == "true"
    assert (
        db.one("SELECT content_rev FROM galleries WHERE id=?", (ids["gallery"],))["content_rev"]
        == before_revision
    )
    assert (
        db.one(
            """SELECT COUNT(*) AS n FROM audit_log
                WHERE entity_type='asset' AND entity_id=? AND action='cull:cut'
                  AND actor='mobile_owner'""",
            (ids["high"],),
        )["n"]
        == 1
    )


def test_cull_path_ids_are_bounded_to_sqlite_int64(cull_client):
    ids = _seed_cull()
    owner = _owner_headers(cull_client)
    item = _item(cull_client, owner, ids["gallery"], ids["high"])
    oversized = str(2**63)

    assert cull_client.get(f"/api/v1/galleries/{oversized}/cull", headers=owner).status_code == 422
    assert (
        cull_client.get(
            f"/api/v1/galleries/{ids['gallery']}/cull/assets/{oversized}/preview",
            headers=owner,
        ).status_code
        == 422
    )
    assert (
        cull_client.patch(
            f"/api/v1/galleries/{ids['gallery']}/assets/{oversized}/cull",
            headers=_command_headers(owner, item["etag"]),
            json={"action": "cut"},
        ).status_code
        == 422
    )


def test_hosted_cull_isolates_overlapping_ids_media_and_commands(tmp_path, monkeypatch):
    _configure_hosted(tmp_path, monkeypatch)
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
    alpha_gallery, alpha_assets, alpha_media_root = _seed_hosted_cull(alpha, "alpha")
    beta_gallery, beta_assets, beta_media_root = _seed_hosted_cull(beta, "beta")

    assert alpha_gallery == beta_gallery == 1
    assert alpha_assets == beta_assets == [1, 2]
    assert alpha_media_root != beta_media_root
    assert (alpha_media_root / "1/web/shared-high.jpg").read_bytes() == b"preview-alpha"
    assert (beta_media_root / "1/web/shared-high.jpg").read_bytes() == b"preview-beta"

    alpha_client = TestClient(app, base_url="https://alpha.mise.test")
    beta_client = TestClient(app, base_url="https://beta.mise.test")
    try:
        alpha_owner = _hosted_owner_headers(
            alpha_client,
            email="owner@alpha.test",
            password="alpha-password",
            name="Alpha iPhone",
        )
        beta_owner = _hosted_owner_headers(
            beta_client,
            email="owner@beta.test",
            password="beta-password",
            name="Beta iPhone",
        )
        alpha_page = alpha_client.get(
            "/api/v1/galleries/1/cull?limit=1",
            headers=alpha_owner,
        )
        beta_page = beta_client.get(
            "/api/v1/galleries/1/cull?limit=1",
            headers=beta_owner,
        )
        assert alpha_page.status_code == beta_page.status_code == 200
        assert alpha_page.json()["items"][0]["filename"] == "alpha-high.jpg"
        assert beta_page.json()["items"][0]["filename"] == "beta-high.jpg"
        assert alpha_page.json()["items"][0]["preview_url"].startswith("https://alpha.mise.test/")
        assert beta_page.json()["items"][0]["preview_url"].startswith("https://beta.mise.test/")

        alpha_preview = alpha_client.get(
            "/api/v1/galleries/1/cull/assets/1/preview",
            headers=alpha_owner,
        )
        beta_preview = beta_client.get(
            "/api/v1/galleries/1/cull/assets/1/preview",
            headers=beta_owner,
        )
        assert alpha_preview.status_code == beta_preview.status_code == 200
        assert alpha_preview.content == b"preview-alpha"
        assert beta_preview.content == b"preview-beta"

        assert beta_client.get("/api/v1/galleries/1/cull", headers=alpha_owner).status_code == 401
        assert (
            beta_client.get(
                "/api/v1/galleries/1/cull/assets/1/preview",
                headers=alpha_owner,
            ).status_code
            == 401
        )
        cross_tenant_cursor = beta_client.get(
            f"/api/v1/galleries/1/cull?limit=1&cursor={alpha_page.json()['next_cursor']}",
            headers=beta_owner,
        )
        assert cross_tenant_cursor.status_code == 422
        assert cross_tenant_cursor.json()["code"] == "pagination.invalid_cursor"

        alpha_decision = alpha_client.patch(
            "/api/v1/galleries/1/assets/1/cull",
            headers=_command_headers(alpha_owner, alpha_page.json()["items"][0]["etag"]),
            json={"action": "cut"},
        )
        assert alpha_decision.status_code == 200
        assert alpha_decision.json()["state"] == "cut"
        assert (
            beta_client.get(
                "/api/v1/galleries/1/cull?limit=1",
                headers=beta_owner,
            ).json()["items"][0]["state"]
            is None
        )
    finally:
        alpha_client.close()
        beta_client.close()
        ratelimit._hits.clear()

    with saas.tenant_runtime(alpha):
        assert db.one("SELECT cull_state FROM assets WHERE id=1")["cull_state"] == "cut"
        assert db.one("SELECT COUNT(*) AS n FROM mobile_commands")["n"] == 1
    with saas.tenant_runtime(beta):
        assert db.one("SELECT cull_state FROM assets WHERE id=1")["cull_state"] is None
        assert db.one("SELECT COUNT(*) AS n FROM mobile_commands")["n"] == 0
