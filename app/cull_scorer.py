"""Local keeper-scorer for AI-assisted culling — populates ``argus_keeper_score`` from the local
Qwen3-VL challenger so the cull deck can rank a gallery the cloud Argus path hasn't scored (or
when the operator is local-first). It is **distinct from** ``qwen_writeback`` (the Argus
*replacement*, locked behind the production cutover interlock): this writes ONLY the keeper score,
keyed by ``asset_id``, gated by its own flag — it does not promote Qwen to production vision, it
just feeds the deck.

Why per-asset (not the 4-image inline batch the shadow/preview path uses): each photo is scored in
its OWN call — one image in, one float out — so there is no cross-photo basename matching and a
malformed reply drops a single asset, never a whole batch. It runs as a background job over EVERY
ready photo (no MAX_IMAGES cap). Local inference → cost 0; one provenance row per run lands in
``ai_runs`` (ADR 0006). Never raises. The model only proposes a score; a human still keeps/cuts in
the deck (§11.4) — nothing here decides delivery.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from . import ai_runs, config, db
from .providers.contracts import Capability, ProviderResult, ResultStatus, ReviewRequirement
from .providers.vision_challenger import chat_completion

log = logging.getLogger("mise.cull_scorer")

_QWEN = "qwen3-vl"

# One image in, one float out. STRICT JSON keeps the parse trivial and the failure mode local to
# the single asset. Wording is the live-tuning surface; the parse/validate below is sound regardless.
SCORE_PROMPT = (
    "You are a photo-culling assistant for a professional food and event photographer. Rate the "
    "SINGLE attached photo's keeper quality — technical (focus, exposure, motion blur) and "
    "editorial (composition, moment). Return STRICT JSON only, no prose and no code fences: "
    '{"keeper_score": 0.0-1.0} where 1.0 is a definite keeper and 0.0 is a clear reject.'
)


class CullScoreError(ValueError):
    """The model's reply had no usable keeper_score. Non-mutating — a bad reply scores nothing."""


def is_enabled() -> bool:
    """Armed only when the operator set the flag AND a local challenger endpoint exists."""
    return bool(config.CULL_SCORER and config.VISION_CHALLENGER_URL)


def _parse_score(content) -> float:
    """Pull a validated keeper_score in [0,1] out of the model reply (tolerating prose/fence
    wrapping). Raises :class:`CullScoreError` on anything missing, non-numeric, or out of range —
    the validator never guesses, so a bad proposal writes nothing."""
    data = content
    if isinstance(content, str):
        text = content.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                raise CullScoreError("no JSON object in reply") from None
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise CullScoreError(f"unparseable JSON: {exc}") from None
    if not isinstance(data, dict):
        raise CullScoreError("reply is not a JSON object")
    score = data.get("keeper_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise CullScoreError(f"keeper_score must be a number in [0,1], got {score!r}")
    score = float(score)
    if not (0.0 <= score <= 1.0):
        raise CullScoreError(f"keeper_score {score} out of range [0,1]")
    return score


def score_one(image_path: Path) -> float:
    """Score a single web derivative through the local endpoint. Raises on a transport error or a
    reply the validator rejects (the caller drops just this asset)."""
    payload = chat_completion([image_path], SCORE_PROMPT)
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise CullScoreError(f"unexpected response shape: {exc}") from None
    return _parse_score(content)


def _web_path(gallery_id: int, stored: str) -> Path:
    return config.MEDIA_DIR / str(gallery_id) / "web" / f"{Path(stored).stem}.jpg"


def score_gallery(gallery_id: int) -> dict:
    """Score every ready photo in a gallery, writing ``argus_keeper_score`` keyed by asset_id.
    Background-job-safe: never raises, and a single asset's failure (missing derivative, bad reply)
    is counted and skipped, not fatal. Writes ONLY the keeper score — keywords/alt-text/hero stay
    whatever Argus (or nothing) set, so this never clobbers production metadata. Records one
    provenance row in ai_runs. Returns a summary dict."""
    if not is_enabled():
        return {"skipped": True, "reason": "cull scorer not configured"}
    try:
        photos = db.all_(
            "SELECT id, stored FROM assets WHERE gallery_id=? AND kind='photo' AND status='ready'",
            (gallery_id,),
        )
    except Exception as exc:  # pragma: no cover - defensive; a select failure shouldn't kill a job
        log.warning("cull score gallery %s: asset query failed: %s", gallery_id, exc)
        return {"error": str(exc)[:200]}
    if not photos:
        return {"skipped": True, "reason": "no ready photos"}

    start = time.monotonic()
    scored = failed = 0
    for a in photos:
        path = _web_path(gallery_id, a["stored"])
        if not path.is_file():
            failed += 1
            continue
        try:
            score = score_one(path)
        except Exception as exc:
            log.warning("cull score gallery %s asset %s failed: %s", gallery_id, a["id"], exc)
            failed += 1
            continue
        db.run(
            "UPDATE assets SET argus_keeper_score=? WHERE id=? AND gallery_id=?",
            (score, a["id"], gallery_id),
        )
        scored += 1

    latency_ms = int((time.monotonic() - start) * 1000)
    status = ResultStatus.OK if scored else ResultStatus.PROVIDER_ERROR
    result = ProviderResult(
        capability=Capability.VISION,
        provider=_QWEN,
        status=status,
        review=ReviewRequirement.HUMAN_REVIEW,
        output={"scored": scored, "failed": failed, "total": len(photos)},
        model=config.VISION_CHALLENGER_MODEL,
        latency_ms=latency_ms,
        cost_usd=0.0,  # local inference — no per-call provider charge
        error=None if scored else "no photos scored",
    )
    try:
        ai_runs.record(result, subject_type="gallery", subject_id=gallery_id)
    except Exception as exc:  # provenance is best-effort; never let it fail the scoring run
        log.warning("cull score gallery %s: ledger record failed: %s", gallery_id, exc)
    log.info("cull score gallery %s: scored %s, failed %s", gallery_id, scored, failed)
    return {"scored": scored, "failed": failed, "total": len(photos)}


def enqueue(gallery_id: int) -> int | None:
    """Queue a background scoring run for a gallery, or ``None`` when the scorer is not armed
    (so a caller gets immediate "not configured" feedback instead of a job that silently skips)."""
    if not is_enabled():
        return None
    from . import jobs

    return jobs.enqueue("cull_score_gallery", {"gallery_id": gallery_id})
