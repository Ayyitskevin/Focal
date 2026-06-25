"""Legacy adapters — wrap today's external sidecars behind the Phase 0 contract.

Each adapter is a thin, behavior-preserving shell over an existing Mise module. It
delegates to that module's **non-mutating trigger / read** function (``draft_caption``,
``trigger_gallery_analyze``, ``trigger_gallery_recommend``, ``packs_for_client``) and
normalizes the return — or the module's own typed error — into a ``ProviderResult``.

Critically, the adapters do NOT write to the database. Persistence of run status,
asset writeback, and provenance stays exactly where it is today (the legacy
``run_for_gallery`` / ``apply_callback`` paths and their callers), completely
untouched. This keeps "provider call" cleanly separated from "business-state write"
(audit §11.3) and means importing or exercising an adapter can never mutate a record.

This is strangler step 2 (wrap the legacy service as an adapter). Nothing in the
running app imports this package yet; the caller cutover is a later roadmap slice.
"""

from __future__ import annotations

import time

from .. import argus_analyze, caption_ai, platekit, plutus_recommend
from .contracts import Capability, ProviderResult, ResultStatus, ReviewRequirement


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


class LegacyArgusVisionAdapter:
    """VISION via the external Argus service (``app.argus_analyze``)."""

    capability = Capability.VISION
    name = "argus"

    def is_enabled(self) -> bool:
        return argus_analyze.is_enabled()

    def analyze_gallery(self, gallery_id: int, *, skip_dedup: bool = False) -> ProviderResult:
        if not self.is_enabled():
            return ProviderResult.disabled(self.capability, self.name)
        start = time.monotonic()
        try:
            payload = argus_analyze.trigger_gallery_analyze(gallery_id, skip_dedup=skip_dedup)
        except argus_analyze.ArgusAnalyzeError as exc:
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.PROVIDER_ERROR,
                str(exc),
                latency_ms=_elapsed_ms(start),
            )
        latency = _elapsed_ms(start)
        run_id = payload.get("run_id")
        job_id = payload.get("job_id")
        if run_id is None and job_id is None:
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.INVALID_RESPONSE,
                "Argus response missing run_id and job_id",
                latency_ms=latency,
            )
        return ProviderResult(
            capability=self.capability,
            provider=self.name,
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={
                "run_id": run_id,
                "job_id": job_id,
                "mode": payload.get("mode") or ("queued" if job_id else "sync"),
            },
            model=self.name,
            latency_ms=latency,
        )


class LegacyPlutusOffersAdapter:
    """OFFERS via the external Plutus service (``app.plutus_recommend``)."""

    capability = Capability.OFFERS
    name = "plutus"

    def is_enabled(self) -> bool:
        return plutus_recommend.is_enabled()

    def recommend_gallery(self, gallery_id: int) -> ProviderResult:
        if not self.is_enabled():
            return ProviderResult.disabled(self.capability, self.name)
        start = time.monotonic()
        try:
            payload = plutus_recommend.trigger_gallery_recommend(gallery_id)
        except plutus_recommend.PlutusRecommendError as exc:
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.PROVIDER_ERROR,
                str(exc),
                latency_ms=_elapsed_ms(start),
            )
        latency = _elapsed_ms(start)
        # Mirror plutus_recommend's own bundle-count / total parsing (kept inline so
        # the adapter does not depend on a private helper; the roadmap consolidates
        # this normalization at cutover).
        bundles = payload.get("bundles")
        if bundles is not None:
            bundle_count = len(bundles)
        elif payload.get("bundle_count") is not None:
            bundle_count = int(payload["bundle_count"])
        else:
            bundle_count = None
        cents = payload.get("estimated_total_cents")
        estimated_cents = int(cents) if cents is not None else None
        return ProviderResult(
            capability=self.capability,
            provider=self.name,
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={
                "run_id": payload.get("run_id"),
                "bundle_count": bundle_count,
                "estimated_total_cents": estimated_cents,
                "review_url": payload.get("review_url"),
                "pitch_url": payload.get("pitch_url"),
            },
            model=self.name,
            latency_ms=latency,
        )


class LegacyOdysseusCaptionAdapter:
    """CONTENT (caption draft) via Odysseus (``app.caption_ai``).

    Odysseus owns model routing; Mise hands it context and takes back one caption plus
    the model name. The caption is a reversible draft a human must accept — never auto
    published — hence ``HUMAN_REVIEW``.
    """

    capability = Capability.CONTENT
    name = "odysseus"

    def is_enabled(self) -> bool:
        return caption_ai.is_enabled()

    def draft(self, ctx: dict) -> ProviderResult:
        if not self.is_enabled():
            return ProviderResult.disabled(self.capability, self.name)
        start = time.monotonic()
        try:
            result = caption_ai.draft_caption(ctx)
        except caption_ai.CaptionDraftError as exc:
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.PROVIDER_ERROR,
                str(exc),
                latency_ms=_elapsed_ms(start),
            )
        # caption_ai.draft_caption guarantees a non-empty caption (it raises
        # CaptionDraftError otherwise), but guard defensively so the adapter always
        # returns a ProviderResult and never raises for a future/mocked provider that
        # returns a malformed dict.
        caption = result.get("caption")
        if not caption:
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.INVALID_RESPONSE,
                "caption provider returned no caption",
                latency_ms=_elapsed_ms(start),
            )
        return ProviderResult(
            capability=self.capability,
            provider=self.name,
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={"caption": caption},
            model=result.get("model"),
            latency_ms=_elapsed_ms(start),
        )


class LegacyDionysusPackAdapter:
    """CONTENT (approved packs) via the Platekit / Dionysus bridge (``app.platekit``).

    ``packs_for_client`` is a pure read that never raises — it returns a status dict.
    We map that status onto the contract so the same review/error machinery applies.
    """

    capability = Capability.CONTENT
    name = "dionysus"

    def is_enabled(self) -> bool:
        return platekit.is_enabled()

    def packs(self, client, *, include_drafts: bool = False) -> ProviderResult:
        if not self.is_enabled():
            return ProviderResult.disabled(self.capability, self.name)
        start = time.monotonic()
        data = platekit.packs_for_client(client, include_drafts=include_drafts)
        latency = _elapsed_ms(start)
        status = data.get("status")
        if status == "ok":
            return ProviderResult(
                capability=self.capability,
                provider=self.name,
                status=ResultStatus.OK,
                review=ReviewRequirement.HUMAN_REVIEW,
                output={"slug": data.get("slug"), "packs": data.get("packs") or []},
                model=self.name,
                latency_ms=latency,
            )
        if status in ("not_configured", "missing_slug"):
            # No outbound call was made — the feature is dormant ("not_configured") or
            # the client has no Platekit slug ("missing_slug", platekit.py). Both are
            # config states, not provider/upstream failures, so they map to DISABLED
            # (contract: DISABLED == "no outbound call made"). Genuine call failures
            # ("not_found", "error") still fall through to PROVIDER_ERROR below.
            return ProviderResult.disabled(self.capability, self.name)
        return ProviderResult.failure(
            self.capability,
            self.name,
            ResultStatus.PROVIDER_ERROR,
            data.get("message") or f"Dionysus status {status}",
            latency_ms=latency,
        )
