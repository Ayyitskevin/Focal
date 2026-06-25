"""Phase 2 vision shadow runner.

Compare a registered vision *challenger* against the already-completed legacy Argus run
and record both to the ai_runs ledger for later evaluation (audit §9.5). This is
deliberately constrained:

* **Ledger-only.** It writes ai_runs rows and nothing else — never assets, never
  galleries, never a re-run of Argus. The legacy side is a *snapshot* of the run that
  already completed (no second cloud-vision call, no extra cost).
* **Inert by default.** It no-ops unless ``MISE_VISION_SHADOW`` is armed AND a vision
  challenger is registered (``providers.registry.challenger``). With no challenger wired,
  arming the flag changes nothing — production stays safe until a real challenger backend
  is added deliberately.
* **Background-safe.** It never raises; failures are logged and swallowed.
"""

from __future__ import annotations

import logging

from . import ai_runs, db, features
from .providers import (
    Capability,
    ProviderResult,
    ResultStatus,
    ReviewRequirement,
    registry,
    shadow,
)

log = logging.getLogger("mise.vision_shadow")


def legacy_result_for(gallery_id: int) -> ProviderResult | None:
    """Snapshot the COMPLETED legacy Argus run as a ProviderResult — no re-call.

    Returns None if the gallery has no completed ('done') run to shadow against, so the
    runner does not invent a baseline.
    """
    g = db.one(
        "SELECT argus_last_run_id, argus_last_status FROM galleries WHERE id=?",
        (gallery_id,),
    )
    if not g or (g["argus_last_status"] or "") != "done" or g["argus_last_run_id"] is None:
        return None
    return ProviderResult(
        capability=Capability.VISION,
        provider="argus",
        status=ResultStatus.OK,
        review=ReviewRequirement.HUMAN_REVIEW,
        output={"run_id": g["argus_last_run_id"]},
        model="argus",
    )


def run_for_gallery(gallery_id: int) -> dict | None:
    """Shadow the completed legacy run with the registered challenger.

    Records the legacy snapshot and the challenger result to ai_runs (linked by a shared
    correlation id) and returns the comparison. No-op (returns None) when shadow is off,
    no challenger is registered, or there is no completed legacy run. Never raises.
    """
    if not features.vision_shadow_enabled():
        return None
    chal = registry.challenger(Capability.VISION)
    if chal is None:
        return None
    try:
        legacy = legacy_result_for(gallery_id)
        if legacy is None:
            log.info("vision shadow skipped for %s (no completed legacy run)", gallery_id)
            return None
        challenger_result = chal.analyze_gallery(gallery_id)
        corr = f"shadow:gallery:{gallery_id}:{legacy.output['run_id']}"
        ai_runs.record(legacy, subject_type="gallery", subject_id=gallery_id, correlation_id=corr)
        ai_runs.record(
            challenger_result, subject_type="gallery", subject_id=gallery_id, correlation_id=corr
        )
        comparison = shadow.compare(legacy, challenger_result)
        log.info("vision shadow gallery %s -> %s", gallery_id, comparison)
        return comparison
    except Exception:
        log.exception("vision shadow failed for gallery %s", gallery_id)
        return None
