"""Notion adapter modernization — version-configurable header + data-source create parent.

Pure unit (no DB, no network): urlopen is monkeypatched to capture the request, so we
assert the Notion-Version header and the create-page parent shape for both the legacy
(2022-06-28, database_id parent) and the 2025-09-03 (data_source_id parent) paths. Default
config = legacy behavior, byte-identical to before.
"""

import json

import pytest

from app import config, notion_sync

pytestmark = pytest.mark.unit


class _Resp:
    def __init__(self, body=b'{"id": "page-new"}'):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _capture(monkeypatch):
    """Patch urlopen to record the Request; returns a list that receives it."""
    captured = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _Resp()

    monkeypatch.setattr(notion_sync.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(config, "NOTION_TOKEN", "ntn_test")
    return captured


def _version(req) -> str:
    # urllib title-cases header keys: "Notion-Version" -> "Notion-version"
    return req.get_header("Notion-version")


def test_patch_uses_legacy_version_by_default(monkeypatch):
    cap = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTION_VERSION", "2022-06-28")
    notion_sync._patch_page("pg1", {"Status": {"select": {"name": "Delivered"}}})
    req = cap[0]
    assert req.get_method() == "PATCH"
    assert _version(req) == "2022-06-28"
    assert req.full_url.endswith("/v1/pages/pg1")


def test_version_is_config_driven(monkeypatch):
    cap = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTION_VERSION", "2025-09-03")
    notion_sync._patch_page("pg1", {})
    assert _version(cap[0]) == "2025-09-03"


def test_create_uses_database_parent_when_no_data_source(monkeypatch):
    cap = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTION_VERSION", "2022-06-28")
    page_id = notion_sync._create_page("db1", {"Name": {"title": []}})
    assert page_id == "page-new"
    body = json.loads(cap[0].data)
    assert body["parent"] == {"database_id": "db1"}  # legacy parent, unchanged


def test_create_uses_data_source_parent_when_configured(monkeypatch):
    cap = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTION_VERSION", "2025-09-03")
    notion_sync._create_page("db1", {"Name": {"title": []}}, data_source_id="ds_123")
    body = json.loads(cap[0].data)
    assert body["parent"] == {"type": "data_source_id", "data_source_id": "ds_123"}
    assert _version(cap[0]) == "2025-09-03"


def test_sync_booking_passes_configured_data_source(monkeypatch):
    """The booking-create call threads NOTION_BOOKINGS_DS through to the parent."""
    monkeypatch.setattr(config, "NOTION_TOKEN", "ntn_test")
    monkeypatch.setattr(config, "NOTION_BOOKINGS_DB", "bookings_db")
    monkeypatch.setattr(config, "NOTION_BOOKINGS_DS", "ds_bookings")
    fake = {
        "id": 1,
        "status": "confirmed",
        "start_utc": "2026-07-01 14:00:00",
        "end_utc": "2026-07-01 16:00:00",
        "name": "Acme",
        "event_name": "Tasting",
        "email": "a@b.com",
        "phone": "",
        "notes": "",
        "notion_page_id": None,
    }
    monkeypatch.setattr(notion_sync.db, "one", lambda sql, params=(): fake)
    monkeypatch.setattr(notion_sync.db, "run", lambda *a, **k: 1)
    seen = {}
    monkeypatch.setattr(
        notion_sync,
        "_create_page",
        lambda db_id, props, *, data_source_id=None: (
            seen.update(db=db_id, ds=data_source_id) or "page-x"
        ),
    )
    notion_sync.sync_booking(1)
    assert seen == {"db": "bookings_db", "ds": "ds_bookings"}
