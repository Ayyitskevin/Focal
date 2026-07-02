"""Batch B / Slice B2: real product screenshots on /demo.

The demo page previously had ZERO images (launch-gap audit) — CSS-drawn
mockups only, with real screenshots sitting on the launch-kit backlog. The
three shots are captured from a live seeded studio (client gallery, client
invoice, onboarding checklist), optimized to web weight, and committed under
static/demo/.
"""

import asyncio
from pathlib import Path

import pytest
from PIL import Image
from starlette.requests import Request

from app import config, saas

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parent.parent
SHOTS = ["gallery", "invoice", "onboarding"]


def test_demo_shots_are_committed_web_weight_images():
    for name in SHOTS:
        path = REPO / "static" / "demo" / f"{name}.webp"
        assert path.exists(), f"static/demo/{name}.webp missing"
        assert path.stat().st_size < 150_000, f"{name} too heavy for a marketing page"
        with Image.open(path) as im:
            assert im.format == "WEBP" and im.width == 1280


def test_demo_page_renders_the_screenshots(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "b2-secret")
    monkeypatch.setattr(config, "BASE_URL", "https://mise.test")
    monkeypatch.setattr(config, "SAAS_ROOT_DOMAIN", "mise.test")
    monkeypatch.setattr(config, "SAAS_MARKETING_HOST", "mise.test")
    monkeypatch.setattr(config, "SAAS_CONTROL_DB_PATH", tmp_path / "control.db")
    monkeypatch.setattr(config, "SAAS_TENANT_DATA_DIR", tmp_path / "tenants")
    saas._MIGRATED_TENANT_DBS.clear()
    saas.migrate_control()
    req = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/demo",
            "query_string": b"",
            "headers": [(b"host", b"mise.test"), (b"accept", b"text/html")],
            "scheme": "https",
            "server": ("mise.test", 443),
            "client": ("127.0.0.1", 50000),
        }
    )
    body = asyncio.run(saas.demo(req)).body.decode()
    for name in SHOTS:
        assert f'src="/static/demo/{name}.webp"' in body
    # Accessibility + layout-shift hygiene: alt text and explicit dimensions.
    assert body.count('loading="lazy"') >= 3
    assert 'alt="A PIN-gated client gallery' in body
