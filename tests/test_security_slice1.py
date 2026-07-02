"""Security Slice 1 (ADR 0061): upload caps + disguise reject, constant-time PIN,
email-header hardening."""

import asyncio

import pytest
from fastapi import HTTPException

from app import config, mailer, security, upload_guard

pytestmark = pytest.mark.unit


class _FakeUpload:
    """Minimal UploadFile stand-in: async .read(n) over a fixed byte string."""

    def __init__(self, data: bytes, chunk: int = 1 << 20):
        self._data = data
        self._chunk = chunk
        self._pos = 0

    async def read(self, n: int = -1) -> bytes:
        take = self._chunk if n is None or n < 0 else min(n, self._chunk)
        out = self._data[self._pos : self._pos + take]
        self._pos += len(out)
        return out


# ── content_ok: disguise + signature ─────────────────────────────────────────

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_content_ok_accepts_real_image_magic():
    assert upload_guard.content_ok(".jpg", JPEG)
    assert upload_guard.content_ok(".png", PNG)


def test_content_ok_rejects_markup_disguised_as_image():
    for payload in (b"<!doctype html>", b"<html>", b"  <script>alert(1)</script>", b"<svg/>"):
        assert upload_guard.content_ok(".jpg", payload) is False


def test_content_ok_rejects_wrong_signature_for_known_ext():
    assert upload_guard.content_ok(".png", JPEG) is False  # jpeg bytes, .png ext


def test_content_ok_lets_unknown_container_pass_the_markup_gate():
    # A .mov (no strict signature) with binary leading bytes is allowed through
    # (ffmpeg transcode is the real validator); markup .mov is still rejected.
    assert upload_guard.content_ok(".mov", b"\x00\x00\x00\x18ftypqt  ") is True
    assert upload_guard.content_ok(".mov", b"<html>") is False


# ── save_capped: cap + cleanup + empty ───────────────────────────────────────


def test_save_capped_writes_and_returns_size(tmp_path):
    dest = tmp_path / "a.jpg"
    size = asyncio.run(upload_guard.save_capped(_FakeUpload(JPEG), dest, ".jpg", 1 << 20))
    assert size == len(JPEG) and dest.exists()


def test_save_capped_rejects_oversize_and_deletes_partial(tmp_path):
    dest = tmp_path / "big.jpg"
    big = JPEG + b"\x00" * (3 << 20)  # > 1 MB cap
    with pytest.raises(HTTPException) as ei:
        asyncio.run(upload_guard.save_capped(_FakeUpload(big), dest, ".jpg", 1 << 20))
    assert ei.value.status_code == 413
    assert not dest.exists()  # partial cleaned up — no disk leak


def test_save_capped_rejects_disguise_and_deletes_partial(tmp_path):
    dest = tmp_path / "evil.jpg"
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            upload_guard.save_capped(_FakeUpload(b"<script>x</script>"), dest, ".jpg", 1 << 20)
        )
    assert ei.value.status_code == 415
    assert not dest.exists()


def test_save_capped_rejects_empty(tmp_path):
    dest = tmp_path / "empty.jpg"
    with pytest.raises(HTTPException) as ei:
        asyncio.run(upload_guard.save_capped(_FakeUpload(b""), dest, ".jpg", 1 << 20))
    assert ei.value.status_code == 400
    assert not dest.exists()


# ── constant-time PIN ─────────────────────────────────────────────────────────


def test_pin_matches():
    assert security.pin_matches("1234", "1234")
    assert security.pin_matches(" 1234 ", "1234")  # both stripped
    assert not security.pin_matches("1235", "1234")
    assert not security.pin_matches("123", "1234")  # length mismatch
    assert not security.pin_matches("1234", None)  # no PIN set
    assert not security.pin_matches("", "")  # empty never matches


# ── email header injection ────────────────────────────────────────────────────


def test_mailer_strips_header_injection_from_reply_to(monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "GMAIL_USER", "studio@example.com")
    monkeypatch.setattr(config, "SITE_NAME", "Studio")
    evil = "attacker@evil.com\r\nBcc: victim@example.com\r\nSubject: spam"
    msg = mailer._build_message("client@example.com", "Inquiry", "hi", reply_to=evil)
    reply_to = msg["Reply-To"]
    assert "\n" not in reply_to and "\r" not in reply_to
    assert "Bcc:" not in reply_to
    # The whole smuggled block collapses to one header value.
    assert msg["Subject"] == "Inquiry"  # not overridden by the injected "Subject:"
