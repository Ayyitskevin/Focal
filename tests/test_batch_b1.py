"""Batch B / Slice B1: og:image link cards.

Before this slice the marketing pages had NO og:image at all (launch-gap
audit) — every X/Slack/iMessage share of /, /pricing, or /demo rendered a
text-only preview at exactly the moment launch buzz makes link cards matter.
The card is a committed 1200x630 PNG rendered from the repo's own fonts and
palette (scripts/og-card.html + generate-og-card.mjs).
"""

import asyncio
from pathlib import Path

import pytest
from PIL import Image
from starlette.requests import Request

from app import config, saas

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parent.parent


def _configure_saas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "b1-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()


def _request(path):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": [(b"host", b"mise.test"), (b"accept", b"text/html")],
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )


def test_og_card_asset_is_a_committed_1200x630_png():
    path = REPO / "static" / "og-card.png"
    assert path.exists(), "static/og-card.png missing — run node scripts/generate-og-card.mjs"
    with Image.open(path) as im:
        assert im.size == (1200, 630) and im.format == "PNG"
    assert path.stat().st_size < 300_000  # a link preview, not a wallpaper


@pytest.mark.parametrize(
    ("handler", "path"),
    [("saas_home", "/"), ("pricing", "/pricing"), ("demo", "/demo")],
)
def test_marketing_pages_serve_large_image_cards(tmp_path, monkeypatch, handler, path):
    _configure_saas(tmp_path, monkeypatch)
    resp = asyncio.run(getattr(saas, handler)(_request(path)))
    assert resp.status_code == 200
    body = resp.body.decode()
    # Absolute URL (crawlers don't resolve relative og:image), correct card type.
    assert 'property="og:image" content="https://mise.test/static/og-card.png"' in body
    assert '<meta name="twitter:card" content="summary_large_image">' in body
    assert 'property="og:image:width" content="1200"' in body
