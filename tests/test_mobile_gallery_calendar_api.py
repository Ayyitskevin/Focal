"""Focused owner gallery/calendar API contract tests."""

import datetime as dt
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import config, db, mobile_gallery_calendar_api, ratelimit
from app.main import app

pytestmark = pytest.mark.unit


def _device() -> dict:
    return {
        "installation_id": "6D7120A8-AFE7-4AAF-94ED-DA7FD2D37856",
        "name": "Owner iPhone",
        "platform": "ios",
        "app_version": "2.0",
    }


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "SECRET_KEY", "gallery-calendar-secret")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "owner-password")
    monkeypatch.setattr(config, "BASE_URL", "https://studio.test")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mise.db")
    monkeypatch.setattr(config, "CULL_UI", True)
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


def _owner_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/studio/login",
        json={"email": None, "password": "owner-password", "device": _device()},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_gallery() -> tuple[int, int, int]:
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,client_name,pin,published,content_rev,type,require_pin,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            "owner-gallery",
            "Owner Gallery",
            "A. Client",
            "4821",
            1,
            7,
            "gallery",
            1,
            "2026-07-10 12:00:00",
        ),
    )
    section_id = db.run(
        "INSERT INTO sections (gallery_id,name,position,proof_target) VALUES (?,?,?,?)",
        (gallery_id, "Finals", 0, 2),
    )
    ready_id = db.run(
        """INSERT INTO assets
           (gallery_id,section_id,kind,filename,stored,status,width,height,bytes,position,
            created_at,argus_alt_text,argus_keywords,argus_keeper_score,
            argus_hero_potential,cull_state)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            gallery_id,
            section_id,
            "photo",
            "C:\\private\\secret\\ready.jpg",
            "/srv/mise/4821/ready-original.jpg",
            "ready",
            6000,
            4000,
            123456,
            1,
            "2026-07-10 12:01:00",
            "A safe description",
            '["portrait", "client"]',
            0.97,
            1.5,
            "keep",
        ),
    )
    cut_id = db.run(
        """INSERT INTO assets
           (gallery_id,section_id,kind,filename,stored,status,position,cull_state)
           VALUES (?,?,?,?,?,'ready',?,'cut')""",
        (gallery_id, section_id, "photo", "cut.jpg", "/srv/private/cut.jpg", 2),
    )
    db.run(
        """INSERT INTO assets
           (gallery_id,section_id,kind,filename,stored,status,position)
           VALUES (?,?,?,?,?,'pending',?)""",
        (gallery_id, section_id, "photo", "pending.jpg", "/srv/private/pending.jpg", 3),
    )
    visitor_id = db.run(
        "INSERT INTO visitors (gallery_id,token) VALUES (?,?)", (gallery_id, "visitor-token")
    )
    db.run("INSERT INTO favorites (visitor_id,asset_id) VALUES (?,?)", (visitor_id, ready_id))
    db.run("INSERT INTO downloads (gallery_id,asset_id) VALUES (?,?)", (gallery_id, ready_id))
    db.run(
        """UPDATE galleries SET cover_asset_id=?, argus_last_run_id=11,
                  argus_last_job_id='job_safe-11', argus_last_status='done',
                  argus_last_at='2026-07-10 12:05:00', argus_analyzed_count=2,
                  argus_hero_asset_ids=?, argus_last_error=? WHERE id=?""",
        (
            ready_id,
            json.dumps([ready_id, cut_id, 999999]),
            "/srv/private/provider-response.txt",
            gallery_id,
        ),
    )
    other_gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,pin,published,type,require_pin,created_at)
           VALUES ('other-gallery','Other','9922',1,'gallery',1,'2026-07-09 12:00:00')"""
    )
    db.run(
        """INSERT INTO assets (gallery_id,kind,filename,stored,status,position)
           VALUES (?,'photo','other.jpg','/srv/private/other.jpg','ready',0)""",
        (other_gallery_id,),
    )
    return gallery_id, ready_id, other_gallery_id


def _seed_large_gallery(asset_count: int = 10_001) -> int:
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,pin,published,content_rev,type,require_pin,created_at)
           VALUES ('large-native-gallery','Large Native Gallery','2741',1,12,
                   'gallery',1,'2026-07-10 12:00:00')"""
    )
    with db.tx() as con:
        con.executemany(
            """INSERT INTO assets
               (gallery_id,kind,filename,stored,status,position,created_at)
               VALUES (?,'photo',?,?,'ready',?,'2026-07-10 12:01:00')""",
            (
                (
                    gallery_id,
                    f"frame-{position:05d}.jpg",
                    f"frame-{position:05d}.jpg",
                    position,
                )
                for position in range(asset_count)
            ),
        )
    return gallery_id


def _seed_schedule() -> int:
    event_id = db.run(
        """INSERT INTO event_types
           (slug,name,description,duration_min,location,color,buffer_before_min,
            buffer_after_min,min_notice_hours,max_per_day,booking_window_days,
            slot_step_min,active,position)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "mobile-session",
            "Mobile Session",
            "Planning call",
            45,
            "Studio",
            "#123abc",
            10,
            15,
            24,
            0,
            90,
            0,
            1,
            50,
        ),
    )
    db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,phone,notes,start_utc,end_utc,tz,status,
            created_at)
           VALUES (?,?,?,?,?,?,?,?,?,'confirmed',?)""",
        (
            "future-mobile",
            event_id,
            "A. Client",
            "client@example.com",
            "+15551234567",
            "Bring references",
            "2099-01-15 15:00:00",
            "2099-01-15 15:45:00",
            "America/New_York",
            "2026-07-10 12:00:00",
        ),
    )
    db.run(
        """INSERT INTO bookings
           (token,event_type_id,name,email,start_utc,end_utc,status)
           VALUES ('past-mobile',?,'Past','past@example.com','2020-01-01 10:00:00',
                   '2020-01-01 10:45:00','confirmed')""",
        (event_id,),
    )
    return event_id


def test_gallery_owner_auth_manifest_safety_and_conditional_cache(api_client, monkeypatch):
    gallery_id, ready_id, _ = _seed_gallery()

    assert api_client.get("/api/v1/galleries").status_code == 401
    with monkeypatch.context() as context:
        context.setattr(
            mobile_gallery_calendar_api.mobile_auth,
            "authenticate_request",
            lambda *_args, **_kwargs: SimpleNamespace(kind="gallery_guest"),
        )
        assert api_client.get("/api/v1/galleries").status_code == 403

    headers = _owner_headers(api_client)
    galleries = api_client.get("/api/v1/galleries", headers=headers)
    assert galleries.status_code == 200
    assert galleries.headers["cache-control"] == "private, no-cache"
    assert galleries.headers["vary"] == "Authorization"
    collection_etag = galleries.headers["etag"]
    assert (
        api_client.get(
            "/api/v1/galleries",
            headers={**headers, "If-None-Match": collection_etag},
        ).status_code
        == 304
    )

    detail = api_client.get(f"/api/v1/galleries/{gallery_id}", headers=headers)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["assets_has_more"] is False
    assert payload["assets_next_cursor"] is None
    assert payload["summary"]["asset_count"] == 1
    assert payload["summary"]["favorite_count"] == 1
    assert payload["summary"]["delivery_state"] == "proofing"
    assert payload["hero_asset_ids"] == [ready_id]
    assert [asset["id"] for asset in payload["assets"]] == [ready_id]
    assert payload["assets"][0]["filename"] == "ready.jpg"
    assert payload["assets"][0]["keeper_score"] == pytest.approx(0.97)
    assert payload["assets"][0]["hero_potential"] is None
    media_base = f"https://studio.test/api/v1/media/galleries/{gallery_id}/assets/{ready_id}"
    assert payload["assets"][0]["links"] == {
        "thumbnail_url": f"{media_base}/thumbnail",
        "preview_url": f"{media_base}/preview",
        "poster_url": None,
        "download_url": f"{media_base}/download",
    }
    assert payload["vision"]["error"] == "Analysis failed."
    serialized = json.dumps(payload)
    for secret in ("4821", "/srv/", "ready-original", "provider-response", "pending.jpg"):
        assert secret not in serialized

    detail_etag = detail.headers["etag"]
    cached = api_client.get(
        f"/api/v1/galleries/{gallery_id}",
        headers={**headers, "If-None-Match": f"W/{detail_etag}"},
    )
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["cache-control"] == "private, no-cache"

    assert api_client.get("/api/v1/galleries/999999", headers=headers).status_code == 404
    wrong_host = api_client.get("https://other.test/api/v1/galleries", headers=headers)
    assert wrong_host.status_code == 401


def test_gallery_detail_assets_are_bounded_signed_endpoint_scoped_pages(api_client, monkeypatch):
    gallery_id = _seed_large_gallery()
    other_gallery_id, _, _ = _seed_gallery()
    headers = _owner_headers(api_client)
    original_all = db.all_
    asset_queries: list[tuple[str, tuple]] = []

    def capture(sql: str, params: tuple = ()):
        if "FROM assets a LEFT JOIN favorites" in sql:
            asset_queries.append((sql, params))
        return original_all(sql, params)

    monkeypatch.setattr(db, "all_", capture)
    first = api_client.get(f"/api/v1/galleries/{gallery_id}", headers=headers)
    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["assets"]) == 100
    assert first_payload["assets_has_more"] is True
    assert first_payload["assets_next_cursor"]
    assert [asset["position"] for asset in first_payload["assets"]] == list(range(100))
    assert asset_queries and "LIMIT ?" in asset_queries[-1][0]
    assert asset_queries[-1][1][-1] == 101

    cursor = first_payload["assets_next_cursor"]
    second = api_client.get(
        f"/api/v1/galleries/{gallery_id}?limit=100&cursor={cursor}",
        headers=headers,
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["assets"]) == 100
    assert [asset["position"] for asset in second_payload["assets"]] == list(range(100, 200))
    assert {asset["id"] for asset in first_payload["assets"]}.isdisjoint(
        asset["id"] for asset in second_payload["assets"]
    )
    assert second.headers["etag"] != first.headers["etag"]
    assert second.headers["cache-control"] == "private, no-cache"
    assert second.headers["vary"] == "Authorization"
    assert (
        api_client.get(
            f"/api/v1/galleries/{gallery_id}?limit=100&cursor={cursor}",
            headers={**headers, "If-None-Match": first.headers["etag"]},
        ).status_code
        == 200
    )
    assert (
        api_client.get(
            f"/api/v1/galleries/{gallery_id}?limit=100&cursor={cursor}",
            headers={**headers, "If-None-Match": second.headers["etag"]},
        ).status_code
        == 304
    )

    tampered = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    assert (
        api_client.get(
            f"/api/v1/galleries/{gallery_id}?cursor={tampered}", headers=headers
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            f"/api/v1/galleries/{other_gallery_id}?cursor={cursor}", headers=headers
        ).status_code
        == 422
    )


def test_gallery_detail_asset_cursor_crosses_sections_ties_and_terminal_page(api_client):
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,pin,published,content_rev,type,require_pin,created_at)
           VALUES ('ordered-native-gallery','Ordered Native Gallery','2742',1,3,
                   'gallery',1,'2026-07-10 12:00:00')"""
    )
    first_section_id = db.run(
        "INSERT INTO sections (gallery_id,name,position) VALUES (?,?,?)",
        (gallery_id, "First", 0),
    )
    second_section_id = db.run(
        "INSERT INTO sections (gallery_id,name,position) VALUES (?,?,?)",
        (gallery_id, "Second", 1),
    )
    asset_ids: list[int] = []
    ordering = (
        (first_section_id, 0),
        (first_section_id, 0),
        (second_section_id, 0),
        (second_section_id, 1),
        (None, 0),
    )
    for index, (section_id, position) in enumerate(ordering, start=1):
        asset_ids.append(
            db.run(
                """INSERT INTO assets
                   (gallery_id,section_id,kind,filename,stored,status,position,created_at)
                   VALUES (?,?, 'photo',?,?,'ready',?,'2026-07-10 12:01:00')""",
                (
                    gallery_id,
                    section_id,
                    f"ordered-{index}.jpg",
                    f"ordered-{index}.jpg",
                    position,
                ),
            )
        )
    headers = _owner_headers(api_client)

    seen_ids: list[int] = []
    cursor = None
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        response = api_client.get(
            f"/api/v1/galleries/{gallery_id}",
            headers=headers,
            params=params,
        )
        assert response.status_code == 200
        payload = response.json()
        seen_ids.extend(asset["id"] for asset in payload["assets"])
        if not payload["assets_has_more"]:
            assert payload["assets_next_cursor"] is None
            assert len(payload["assets"]) == 1
            break
        cursor = payload["assets_next_cursor"]

    assert seen_ids == asset_ids
    assert len(seen_ids) == len(set(seen_ids))


def test_gallery_detail_asset_cursor_is_explicitly_non_snapshot(api_client):
    gallery_id = db.run(
        """INSERT INTO galleries
           (slug,title,pin,published,content_rev,type,require_pin,created_at)
           VALUES ('mutable-native-gallery','Mutable Native Gallery','2743',1,4,
                   'gallery',1,'2026-07-10 12:00:00')"""
    )
    original_ids = [
        db.run(
            """INSERT INTO assets
               (gallery_id,kind,filename,stored,status,position,created_at)
               VALUES (?,'photo',?,?,'ready',?,'2026-07-10 12:01:00')""",
            (gallery_id, f"original-{position}.jpg", f"original-{position}.jpg", position),
        )
        for position in (10, 20, 30)
    ]
    headers = _owner_headers(api_client)
    first = api_client.get(
        f"/api/v1/galleries/{gallery_id}",
        headers=headers,
        params={"limit": 2},
    )
    assert first.status_code == 200
    cursor = first.json()["assets_next_cursor"]
    assert cursor

    inserted_before_cursor = db.run(
        """INSERT INTO assets
           (gallery_id,kind,filename,stored,status,position,created_at)
           VALUES (?,'photo','late-ready.jpg','late-ready.jpg','ready',15,
                   '2026-07-10 12:02:00')""",
        (gallery_id,),
    )
    later = api_client.get(
        f"/api/v1/galleries/{gallery_id}",
        headers=headers,
        params={"limit": 2, "cursor": cursor},
    )
    assert later.status_code == 200
    assert [asset["id"] for asset in later.json()["assets"]] == [original_ids[2]]

    refreshed = api_client.get(f"/api/v1/galleries/{gallery_id}", headers=headers)
    assert refreshed.status_code == 200
    refreshed_ids = [asset["id"] for asset in refreshed.json()["assets"]]
    assert inserted_before_cursor in refreshed_ids


def test_gallery_pagination_is_bounded_and_signed(api_client, monkeypatch):
    _seed_gallery()
    headers = _owner_headers(api_client)
    original_all = db.all_
    gallery_queries: list[tuple[str, tuple]] = []

    def capture(sql: str, params: tuple = ()):
        if "FROM galleries g LEFT JOIN clients" in sql:
            gallery_queries.append((sql, params))
        return original_all(sql, params)

    monkeypatch.setattr(db, "all_", capture)
    first = api_client.get("/api/v1/galleries?limit=1", headers=headers)
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["has_more"] is True
    assert gallery_queries and "LIMIT ?" in gallery_queries[-1][0]
    assert gallery_queries[-1][1][-1] == 2

    cursor = first_payload["next_cursor"]
    second = api_client.get(f"/api/v1/galleries?limit=1&cursor={cursor}", headers=headers)
    assert second.status_code == 200
    assert second.json()["items"][0]["id"] != first_payload["items"][0]["id"]
    tampered = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    assert (
        api_client.get(f"/api/v1/galleries?cursor={tampered}", headers=headers).status_code == 422
    )
    assert api_client.get("/api/v1/galleries?limit=101", headers=headers).status_code == 422


def test_event_types_and_upcoming_booking_wire_shapes_and_cache(api_client):
    event_id = _seed_schedule()
    headers = _owner_headers(api_client)

    events = api_client.get("/api/v1/event-types", headers=headers)
    assert events.status_code == 200
    event = next(item for item in events.json()["items"] if item["id"] == event_id)
    assert set(event) == {
        "id",
        "slug",
        "name",
        "description",
        "duration_minutes",
        "location",
        "color_hex",
        "buffer_before_minutes",
        "buffer_after_minutes",
        "minimum_notice_hours",
        "maximum_per_day",
        "booking_window_days",
        "slot_step_minutes",
        "active",
    }
    assert event["color_hex"] == "#123ABC"
    assert event["maximum_per_day"] is None
    assert event["slot_step_minutes"] == 45
    assert (
        api_client.get(
            "/api/v1/event-types",
            headers={**headers, "If-None-Match": events.headers["etag"]},
        ).status_code
        == 304
    )

    bookings = api_client.get("/api/v1/bookings", headers=headers)
    assert bookings.status_code == 200
    booking_items = bookings.json()["items"]
    assert len(booking_items) == 1
    booking = booking_items[0]
    assert set(booking) == {
        "id",
        "event_type_id",
        "event_name",
        "name",
        "email",
        "phone",
        "notes",
        "start_at",
        "end_at",
        "time_zone",
        "status",
        "client_id",
        "project_id",
        "rescheduled_from_id",
        "cancel_reason",
        "cancelled_at",
        "created_at",
    }
    assert booking["event_type_id"] == event_id
    assert booking["status"] == "confirmed"
    assert booking["start_at"].endswith("Z")
    assert (
        api_client.get(
            "/api/v1/bookings",
            headers={**headers, "If-None-Match": bookings.headers["etag"]},
        ).status_code
        == 304
    )
