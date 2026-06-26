"""Qwen vision production-writeback — the path that lets the challenger REPLACE Argus.

Today the Qwen challenger only returns a raw reply for the shadow ledger (ADR 0007); to
ever serve production it must produce the same structured per-photo signals Argus writes
(keywords, alt text, keeper/hero scores) and persist them to the asset rows the rest of the
app reads. This module is that path — built now, but **dormant**:

* ``writeback_gallery`` is SELF-INTERLOCKED on the cutover seam
  (``registry.active_vision_provider``, ADR 0016): it refuses to run — and writes nothing —
  unless Qwen is the *eligible production* provider. The challenger is ``serves_production =
  False`` today, so the interlock keeps this inert until promotion is a deliberate code +
  flag change. Nothing in the running app calls it.
* The model only *proposes*: ``parse_structured`` is the deterministic validator the audit
  (§11.4) requires — a malformed/out-of-range model reply is rejected, never written.
* ``apply_to_gallery`` mirrors ``argus_writeback`` exactly (same columns, same basename
  match, idempotent, photo+ready only) so a promoted Qwen is a true drop-in.

The remaining work to actually flip it is: validate/tune ``STRUCTURED_PROMPT`` + parsing
against a live Qwen endpoint, set ``serves_production = True``, wire a trigger, and set
``MISE_VISION_PROVIDER=qwen``. See ADR 0017.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import config, db
from .providers import registry
from .providers.vision_challenger import (
    InternalVisionChallengerAdapter,
    _gather_image_paths,
    chat_completion,
)

log = logging.getLogger("mise.qwen_writeback")

_HERO_LIMIT = 5
_HERO_MIN_SCORE = 0.5
_QWEN = "qwen3-vl"

# Asks Qwen for STRICT JSON, one object per image keyed by basename, with exactly the
# signals argus_writeback persists. (Prompt wording is the live-tuning surface — the
# parse/validate/writeback below is sound regardless of how the wording evolves.)
STRUCTURED_PROMPT = (
    "You are a photo-culling and metadata assistant for a professional food and event "
    "photographer. For EACH attached image return STRICT JSON only (no prose, no code "
    'fences) of the form: {"photos": [{"basename": "<exact file name>", "keywords": '
    '["..."], "alt_text": "one line", "keeper_score": 0.0-1.0, "hero_potential": 0.0-1.0}]}.'
    " keeper_score and hero_potential are floats in [0,1]; keywords is a short list of SEO "
    "terms; do not invent people or places."
)


class QwenWritebackError(ValueError):
    """The model's structured reply was unparseable or violated the schema. Non-mutating —
    a draft that fails validation is never written (audit §11.4)."""


def _num_in_unit(value, field: str):
    """Validate an optional 0..1 float; None passes through. Raises on out-of-range/non-num."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QwenWritebackError(f"{field} must be a number in [0,1], got {value!r}")
    v = float(value)
    if not (0.0 <= v <= 1.0):
        raise QwenWritebackError(f"{field} {v} out of range [0,1]")
    return v


def parse_structured(content) -> list[dict]:
    """Deterministically validate the model's structured reply into a list of normalized
    photo dicts ``{basename, keywords, alt_text, keeper_score, hero_potential}``.

    ``content`` is the model's text (possibly wrapped in prose/fences) or an already-parsed
    object. Raises :class:`QwenWritebackError` on anything malformed — the validator never
    guesses, so a bad proposal cannot reach the writeback.
    """
    data = content
    if isinstance(content, str):
        text = content.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # tolerate prose/code-fence wrapping: parse the outermost {...}
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                raise QwenWritebackError("no JSON object in model reply") from None
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise QwenWritebackError(f"unparseable JSON in model reply: {exc}") from None

    if isinstance(data, dict):
        photos = data.get("photos")
    elif isinstance(data, list):
        photos = data
    else:
        raise QwenWritebackError("structured reply is not an object or list")
    if not isinstance(photos, list):
        raise QwenWritebackError("'photos' must be a list")

    out: list[dict] = []
    for i, p in enumerate(photos):
        if not isinstance(p, dict):
            raise QwenWritebackError(f"photo {i} is not an object")
        basename = p.get("basename")
        if not isinstance(basename, str) or not basename.strip():
            raise QwenWritebackError(f"photo {i} has no basename")
        keywords = p.get("keywords") or []
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            raise QwenWritebackError(f"photo {i} keywords must be a list of strings")
        alt = p.get("alt_text")
        if alt is not None and not isinstance(alt, str):
            raise QwenWritebackError(f"photo {i} alt_text must be a string")
        out.append(
            {
                "basename": basename.strip(),
                "keywords": [k.strip() for k in keywords if k.strip()],
                "alt_text": (alt or "").strip() or None,
                "keeper_score": _num_in_unit(p.get("keeper_score"), f"photo {i} keeper_score"),
                "hero_potential": _num_in_unit(
                    p.get("hero_potential"), f"photo {i} hero_potential"
                ),
            }
        )
    return out


def _basename_key(name: str) -> str:
    return Path(name).name.lower()


def apply_to_gallery(gallery_id: int, photos: list[dict]) -> dict:
    """Write validated per-photo signals onto the gallery's assets — the deterministic
    writeback. Mirrors ``argus_writeback.apply_to_gallery``: matches by basename to
    photo+ready assets of THIS gallery only, writes the same ``argus_*`` columns
    (role-named for vision, shared by the rest of the app), recomputes the gallery hero set,
    and is idempotent. Touches no other gallery and no money/contract state."""
    assets = db.all_(
        """SELECT id, stored, filename FROM assets
           WHERE gallery_id=? AND kind='photo' AND status='ready'""",
        (gallery_id,),
    )
    by_stored = {_basename_key(a["stored"]): a for a in assets}
    by_filename = {_basename_key(a["filename"]): a for a in assets}

    matched = 0
    hero_rows: list[tuple[float, int]] = []
    for photo in photos:
        key = _basename_key(photo["basename"])
        asset = by_stored.get(key) or by_filename.get(key)
        if not asset:
            continue  # a signal for a photo not in this gallery is ignored, never written
        keywords = photo["keywords"]
        keeper = photo["keeper_score"]
        hero = photo["hero_potential"]
        db.run(
            """UPDATE assets SET argus_alt_text=?, argus_keywords=?, argus_keeper_score=?,
                      argus_hero_potential=? WHERE id=?""",
            (
                photo["alt_text"],
                json.dumps(keywords) if keywords else None,
                keeper,
                hero,
                asset["id"],
            ),
        )
        matched += 1
        if hero is not None and hero >= _HERO_MIN_SCORE:
            hero_rows.append((hero, int(asset["id"])))

    hero_rows.sort(key=lambda row: (-row[0], row[1]))
    hero_ids = [asset_id for _, asset_id in hero_rows[:_HERO_LIMIT]]
    db.run(
        "UPDATE galleries SET argus_hero_asset_ids=?, argus_analyzed_count=? WHERE id=?",
        (json.dumps(hero_ids) if hero_ids else None, matched, gallery_id),
    )
    return {
        "gallery_id": gallery_id,
        "matched": matched,
        "photo_count": len(photos),
        "hero_asset_ids": hero_ids,
    }


def _fetch_structured(gallery_id: int) -> list[dict]:
    """Call the live Qwen endpoint for structured signals and validate them. Only reached
    once the interlock has confirmed Qwen is the eligible production provider."""
    paths = _gather_image_paths(gallery_id, config.VISION_CHALLENGER_MAX_IMAGES)
    if not paths:
        return []
    payload = chat_completion(paths, STRUCTURED_PROMPT)
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise QwenWritebackError(f"unexpected response shape: {exc}") from None
    return parse_structured(content)


def writeback_gallery(gallery_id: int) -> dict:
    """Run the Qwen structured analysis and write asset signals — the production replacement
    for argus_writeback once Qwen is promoted.

    SELF-INTERLOCKED and never raises. It writes nothing unless the cutover seam designates
    Qwen as the *eligible production* provider (``registry.active_vision_provider``), so it
    stays fully inert — a no-op returning ``{"skipped": True, ...}`` — until promotion is a
    deliberate ``serves_production`` + ``MISE_VISION_PROVIDER`` change. Background-job-safe.
    """
    status = registry.active_vision_provider()
    if status["effective"] != _QWEN or not status["eligible"]:
        log.info("qwen writeback inert for gallery %s: %s", gallery_id, status["reason"])
        return {"skipped": True, "reason": status["reason"]}
    try:
        photos = _fetch_structured(gallery_id)
        if not photos:
            return {"skipped": True, "reason": "no structured signals"}
        return apply_to_gallery(gallery_id, photos)
    except Exception as exc:
        log.warning("qwen writeback failed for gallery %s: %s", gallery_id, exc)
        return {"error": str(exc)[:200]}


def enqueue_writeback(gallery_id: int) -> int | None:
    """Queue a production Qwen writeback for a gallery — the trigger half of the cutover.

    Returns the job id, or ``None`` when the interlock does not currently make Qwen the
    eligible production provider (so a caller gets immediate "not promoted" feedback instead
    of a job that silently skips). The handler (:func:`writeback_gallery`) is itself
    interlocked, so this is safe either way; checking here just avoids queueing a guaranteed
    no-op. This is what a live trigger (or the operator's manual button) calls — wiring it
    into the automatic analyze path is the deliberate promote-time step (see ADR 0017)."""
    status = registry.active_vision_provider()
    if status["effective"] != _QWEN or not status["eligible"]:
        return None
    from . import jobs

    return jobs.enqueue("qwen_writeback_gallery", {"gallery_id": gallery_id})


def preview_gallery(gallery_id: int) -> dict:
    """Asset-safe DRY RUN: fetch Qwen's structured signals for a gallery and validate them
    WITHOUT writing anything — the prompt-tuning loop (audit §9.5). Lets the operator see the
    parsed per-photo signals (and any validation rejection) against the live endpoint before
    promotion, so tuning ``STRUCTURED_PROMPT`` is a tight loop, not a guess.

    Requires only that the challenger endpoint is configured — independent of the production
    interlock, because tuning necessarily happens *before* the flag flip. Never mutates an
    asset, never raises; returns ``{"ok": True, "photos", "count"}`` or ``{"ok": False,
    "error"}``."""
    if not config.VISION_CHALLENGER_URL:
        return {
            "ok": False,
            "error": "challenger endpoint not configured (set MISE_VISION_CHALLENGER_URL)",
        }
    try:
        photos = _fetch_structured(gallery_id)
    except QwenWritebackError as exc:
        return {"ok": False, "error": f"validation rejected the reply: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"challenger call failed: {str(exc)[:200]}"}
    return {"ok": True, "photos": photos, "count": len(photos)}


def readiness() -> dict:
    """Report what remains before Qwen can serve production vision — the cutover checklist.

    Read-only and non-raising. Each check is ``{key, label, ok, detail}``; ``ready`` is True
    only when *every* check passes (the interlock is eligible AND the validation gate is
    green). It turns promotion from "remember four steps" into an in-app preflight; it decides
    and changes nothing. ``next_step`` is the first unmet check's action."""
    from . import validation  # local import keeps the module import graph flat

    status = registry.active_vision_provider()
    challenger = InternalVisionChallengerAdapter()
    endpoint_ok = bool(config.VISION_CHALLENGER_URL)
    prod_capable = bool(getattr(challenger, "serves_production", False))
    flag_qwen = config.VISION_PROVIDER in ("qwen", "qwen3-vl", "challenger")
    interlock_ok = status["eligible"] and status["effective"] == _QWEN

    try:
        rep = validation.promotion_report("vision", "argus", config.VISION_CHALLENGER_MODEL)
        gate_ok = rep.ready
        gate_detail = f"paired {rep.paired}/{rep.min_paired} · " + (
            "ready" if rep.ready else "not ready"
        )
    except Exception as exc:  # gate is informational here — never let it break the preflight
        gate_ok = False
        gate_detail = f"gate unavailable: {str(exc)[:120]}"

    checks = [
        {
            "key": "endpoint",
            "label": "Challenger endpoint configured",
            "ok": endpoint_ok,
            "detail": config.VISION_CHALLENGER_URL
            or "set MISE_VISION_CHALLENGER_URL to a trusted local endpoint",
        },
        {
            "key": "writeback",
            "label": "Challenger declares a production writeback",
            "ok": prod_capable,
            "detail": "serves_production is True"
            if prod_capable
            else "after validating STRUCTURED_PROMPT against the live endpoint, set "
            "InternalVisionChallengerAdapter.serves_production = True (a reviewed code change)",
        },
        {
            "key": "flag",
            "label": "MISE_VISION_PROVIDER points at Qwen",
            "ok": flag_qwen,
            "detail": f"MISE_VISION_PROVIDER={config.VISION_PROVIDER!r}",
        },
        {
            "key": "interlock",
            "label": "Interlock routes production to Qwen",
            "ok": interlock_ok,
            "detail": status["reason"],
        },
        {
            "key": "gate",
            "label": "Validation gate is green",
            "ok": gate_ok,
            "detail": gate_detail,
        },
    ]
    ready = all(c["ok"] for c in checks)
    unmet = [c for c in checks if not c["ok"]]
    next_step = (
        "Qwen is the eligible production provider — writeback runs when triggered."
        if ready
        else f"{unmet[0]['label']}: {unmet[0]['detail']}"
    )
    return {
        "checks": checks,
        "ready": ready,
        "effective": status["effective"],
        "eligible": status["eligible"],
        "remaining": len(unmet),
        "next_step": next_step,
    }
