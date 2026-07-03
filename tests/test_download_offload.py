"""Audit finding #4: section/favorites ZIP builds must not run on the event loop.

On the single shared worker a synchronous multi-GB ZIP build inside the async
handler froze EVERY other in-flight request — other clients' galleries, other
tenants, /healthz — until it finished. The build now goes through
run_in_threadpool, so the loop stays responsive while one client waits for their
archive. This pins that the build actually executes off the event-loop thread.
"""

import asyncio
import threading

import pytest
from starlette.requests import Request

from app import db, security
from app.public import downloads

pytestmark = pytest.mark.unit


def _seed_gallery_with_faved_asset():
    gid = db.run(
        "INSERT INTO galleries (slug, title, pin, published) VALUES (?,?,?,1)",
        (f"dl-offload-{db.one('SELECT COUNT(*) c FROM galleries')['c']}", "Tasting", "1234"),
    )
    slug = db.one("SELECT slug FROM galleries WHERE id=?", (gid,))["slug"]
    asset_id = db.run(
        "INSERT INTO assets (gallery_id, kind, filename, stored, status) "
        "VALUES (?,'photo','plate.jpg','plate.jpg','ready')",
        (gid,),
    )
    vid, cookie = security.create_visitor(gid)
    db.run("UPDATE visitors SET email=? WHERE id=?", ("guest@example.com", vid))
    db.run("INSERT INTO favorites (visitor_id, asset_id) VALUES (?,?)", (vid, asset_id))
    return slug, gid, cookie


def _request(slug, gid, cookie):
    name = security.visitor_cookie_name(gid)
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/g/{slug}/download/favorites",
            "query_string": b"",
            "headers": [
                (b"host", b"studio.example.com"),
                (b"cookie", f"{name}={cookie}".encode()),
            ],
            "scheme": "https",
            "server": ("studio.example.com", 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def test_favorites_zip_is_built_off_the_event_loop(monkeypatch):
    slug, gid, cookie = _seed_gallery_with_faved_asset()

    built_on: dict = {}

    def fake_store(gallery_id, assets, out):
        built_on["thread"] = threading.get_ident()
        out.write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # minimal empty-zip so FileResponse serves

    monkeypatch.setattr(downloads, "_store_zip", fake_store)

    resp = asyncio.run(downloads.download_favorites(_request(slug, gid, cookie), slug))

    # The response is the archive, and the build ran on a WORKER thread — not the
    # event-loop thread asyncio.run drives on this (the test's) thread.
    assert resp.status_code == 200
    assert built_on["thread"] != threading.get_ident()
