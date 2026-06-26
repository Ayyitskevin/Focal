"""Internal vision challenger — Qwen3-VL on a local OpenAI-compatible endpoint.

The local challenger to Argus's cloud Grok vision path (audit §9.2/§9.6, ADR 0007):
**Qwen3-VL (32B)** served on ``mickeybot`` via an OpenAI-compatible endpoint (Ollama).
It is DORMANT until ``MISE_VISION_CHALLENGER_URL`` is set, and is used in **shadow only**
— ``app.vision_shadow`` records its result to the ai_runs ledger for comparison against
the legacy Argus run; it never writes to assets/galleries. It never raises (every failure
becomes a non-OK ``ProviderResult``).

Privacy (audit §13.4): point this at a trusted **local** endpoint only — cloud vision by
default is intentionally unsupported here. It sends downsized **web derivatives** (not
RAW/originals), capped at ``MISE_VISION_CHALLENGER_MAX_IMAGES``, to minimize exposure;
the ledger stores provenance metadata only (model/latency/status), never image content.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import config
from .contracts import Capability, ProviderResult, ResultStatus, ReviewRequirement

log = logging.getLogger("mise.vision_challenger")

# Asks for the same signals Argus produces (keywords / alt text / a brief culling note).
# The raw reply is stored for human review in the shadow ledger; strict schema parsing is
# the §9.5 evaluation step, validated against the live endpoint before any promotion.
_PROMPT = (
    "You are a photo-culling and metadata assistant for a professional food and event "
    "photographer. For the attached gallery images, return concise SEO keywords, one alt-"
    "text line per image, and a one-line technical/editorial culling note (sharpness, "
    "exposure, hero potential). Be terse and factual; do not invent people or places."
)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _gather_image_paths(gallery_id: int, limit: int) -> list[Path]:
    """Up to ``limit`` web-derivative JPEGs for the gallery (downsized, not originals)."""
    web = config.MEDIA_DIR / str(gallery_id) / "web"
    if not web.is_dir():
        return []
    return sorted(p for p in web.glob("*.jpg") if p.is_file())[: max(0, limit)]


def chat_completion(image_paths: list[Path], prompt: str) -> dict:
    """POST one OpenAI-compatible chat-completion with ``prompt`` + the given web
    derivatives (base64 data URLs) to the configured local endpoint; return the parsed JSON.
    Shared by the shadow adapter and the (dormant) structured writeback path so the endpoint
    plumbing lives in one place."""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for p in image_paths:
        b64 = base64.b64encode(p.read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    body = json.dumps(
        {
            "model": config.VISION_CHALLENGER_MODEL,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "stream": False,
        }
    ).encode()
    headers = {"Content-Type": "application/json"}
    if config.VISION_CHALLENGER_TOKEN:
        headers["Authorization"] = f"Bearer {config.VISION_CHALLENGER_TOKEN}"
    req = urllib.request.Request(
        f"{config.VISION_CHALLENGER_URL}/chat/completions",
        method="POST",
        data=body,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=config.VISION_CHALLENGER_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


class InternalVisionChallengerAdapter:
    """VISION challenger via a local OpenAI-compatible (Qwen3-VL) endpoint."""

    capability = Capability.VISION
    name = "qwen3-vl"
    # Eval-only: analyze_gallery returns a raw reply for the shadow ledger (ADR 0007), not
    # the structured asset writeback Argus performs. It therefore CANNOT serve production
    # vision until a writeback path exists — the registry interlock refuses to route
    # production here while this is False, so a misconfigured flag can't break the live path.
    serves_production = False

    def is_enabled(self) -> bool:
        return bool(config.VISION_CHALLENGER_URL)

    def _analyze(self, image_paths: list[Path]) -> dict:
        return chat_completion(image_paths, _PROMPT)

    def analyze_gallery(self, gallery_id: int, *, skip_dedup: bool = False) -> ProviderResult:
        if not self.is_enabled():
            return ProviderResult.disabled(self.capability, self.name)
        start = time.monotonic()
        paths = _gather_image_paths(gallery_id, config.VISION_CHALLENGER_MAX_IMAGES)
        if not paths:
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.INVALID_RESPONSE,
                "no web derivatives to analyze",
                latency_ms=_elapsed_ms(start),
            )
        try:
            payload = self._analyze(paths)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.PROVIDER_ERROR,
                f"vision challenger error: {reason}",
                latency_ms=_elapsed_ms(start),
            )
        except (ValueError, json.JSONDecodeError):
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.INVALID_RESPONSE,
                "vision challenger returned an unreadable response",
                latency_ms=_elapsed_ms(start),
            )
        try:
            analysis = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.INVALID_RESPONSE,
                "vision challenger returned an unexpected response shape",
                latency_ms=_elapsed_ms(start),
            )
        return ProviderResult(
            capability=self.capability,
            provider=self.name,
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={"image_count": len(paths), "analysis": (analysis or "").strip()[:2000]},
            model=config.VISION_CHALLENGER_MODEL,
            latency_ms=_elapsed_ms(start),
            cost_usd=0.0,  # local inference — no per-call provider charge
        )
