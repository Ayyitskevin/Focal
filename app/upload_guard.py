"""Upload hardening (security Slice 1): per-file size ceiling + disguise rejection.

Two defenses for every file that lands on disk from an upload route:

- **Size cap while streaming.** The upload loop stops and deletes the partial file
  the instant it crosses a byte ceiling, so a buggy or hostile client can't stream
  unbounded bytes into one file and fill the volume. In hosted mode that volume is
  shared across studios, so an uncapped upload is a cross-tenant denial of service.
  (A per-tenant storage *quota* is the complete answer and is tracked separately;
  this is the per-file backstop.)
- **Content-disguise rejection.** Files whose leading bytes are markup/script — the
  classic "payload.html renamed to .jpg and served from the media origin" — are
  rejected under any media extension, and well-known image/PDF signatures must match
  their extension. This is defense in depth behind the global
  `X-Content-Type-Options: nosniff` header; video/HEIC containers vary too much to
  allowlist, so they pass the signature gate but still fail the markup gate.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException, UploadFile

log = logging.getLogger("mise.upload_guard")

# Unambiguous leading magic bytes, by extension. Absent = not signature-checked.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".tif": (b"II*\x00", b"MM\x00*"),
    ".tiff": (b"II*\x00", b"MM\x00*"),
    ".pdf": (b"%PDF",),
}
# Leading tokens that betray a markup/script payload — rejected under ANY extension.
_MARKUP = (b"<!doctype", b"<html", b"<script", b"<?xml", b"<?php", b"<svg", b"<!--")


def _looks_like_markup(head: bytes) -> bool:
    lead = head.lstrip()[:512].lower()
    return lead.startswith(b"<") or any(tok in lead for tok in _MARKUP)


def content_ok(ext: str, head: bytes) -> bool:
    """False if the leading bytes are a markup/script disguise or contradict a known
    image/PDF signature for ``ext``. Unknown-signature types (video, HEIC) only have
    to clear the markup gate."""
    if _looks_like_markup(head):
        return False
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    sigs = _MAGIC.get(ext)
    if sigs is not None:
        return any(head.startswith(s) for s in sigs)
    return True


async def save_capped(upload: UploadFile, dest: Path, ext: str, max_bytes: int) -> int:
    """Stream ``upload`` to ``dest`` (ext already validated) enforcing content + cap.

    Sniffs the first chunk for a disguise (415) and aborts + deletes the partial the
    moment the running size exceeds ``max_bytes`` (413) or the file is empty (400).
    Returns the byte size on success. The caller owns extension validation.
    """
    size = 0
    checked = False
    with dest.open("wb") as out:
        while chunk := await upload.read(1 << 20):
            if not checked:
                if not content_ok(ext, chunk):
                    dest.unlink(missing_ok=True)
                    log.warning(
                        "rejected disguised upload (ext=%s, %d bytes head)", ext, len(chunk)
                    )
                    raise HTTPException(
                        status_code=415, detail="file content does not match its type"
                    )
                checked = True
            size += len(chunk)
            if size > max_bytes:
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413, detail=f"file too large (max {max_bytes // (1 << 20)} MB)"
                )
            out.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="empty file")
    return size
