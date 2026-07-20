"""Static structural checks for launch-integrity source contracts.

These assert the shipped source still encodes the launch fixes when a full
iOS toolchain is unavailable on the agent host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def test_commercial_view_does_not_open_client_invoice_url():
    text = (ROOT / "ios/Mise/Features/Commercial/CommercialView.swift").read_text()
    assert 'Link("Open invoice"' not in text
    assert "destination: inv.publicURL" not in text
    assert "Preview invoice" in text
    assert "first-view" in text or "sent → viewed" in text or "client first-view" in text


def test_gallery_lightbox_uses_poster_and_authenticated_video():
    viewer = (ROOT / "ios/Mise/Features/Shared/GalleryViewer.swift").read_text()
    assert "GalleryMediaPresentation" in viewer
    assert "stillURL" in viewer
    assert "playbackURL" in viewer
    assert "AuthenticatedRemoteVideo" in viewer
    # Video stills prefer poster/thumbnail — never the MP4 preview path first.
    assert "if asset.kind == .video" in viewer
    assert "asset.links.posterURL ?? asset.links.thumbnailURL" in viewer
    # Lightbox routes video through AuthenticatedRemoteVideo, not image-only decode.
    assert "lightboxPage(for:" in viewer or "lightboxPage(for" in viewer

    video = (ROOT / "ios/Mise/Features/Shared/AuthenticatedRemoteVideo.swift").read_text()
    assert "VideoPlayer" in video
    assert "AVPlayer" in video
    assert "loader.data(for:" in video or "loader.data(for" in video


def test_seed_demo_tenant_remains_fail_closed_tombstone():
    text = (ROOT / "scripts/seed_demo_tenant.py").read_text()
    assert "DISABLED_MESSAGE" in text
    assert "SystemExit" in text
    assert "raise SystemExit" in text
    # Must not open application config/DB.
    assert "from app" not in text
    assert "import app" not in text
    assert "sqlite3" not in text
