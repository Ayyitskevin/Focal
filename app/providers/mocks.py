"""Deterministic mock adapters for tests and shadow-mode harnesses.

These never touch the network or the DB and return fixed, input-derived outputs so a
test can assert exact provenance. They satisfy the Phase 0 requirement for a
provider-independent way to exercise the contract, and they double as the seam the
roadmap uses to A/B an internal implementation against the legacy adapter offline.
"""

from __future__ import annotations

from .contracts import Capability, ProviderResult, ResultStatus, ReviewRequirement


class MockVisionAdapter:
    capability = Capability.VISION
    name = "mock"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def is_enabled(self) -> bool:
        return self.enabled

    def analyze_gallery(self, gallery_id: int, *, skip_dedup: bool = False) -> ProviderResult:
        if not self.enabled:
            return ProviderResult.disabled(self.capability, self.name)
        return ProviderResult(
            capability=self.capability,
            provider=self.name,
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={"run_id": 1000 + gallery_id, "job_id": None, "mode": "sync"},
            model="mock-vision-1",
            latency_ms=0,
            cost_usd=0.0,
        )


class MockVisionChallengerAdapter:
    """A deterministic VISION challenger (distinct provider/model/latency from the
    legacy mock) so shadow-comparison tests see a real difference to report."""

    capability = Capability.VISION
    name = "mock-challenger"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def is_enabled(self) -> bool:
        return self.enabled

    def analyze_gallery(self, gallery_id: int, *, skip_dedup: bool = False) -> ProviderResult:
        if not self.enabled:
            return ProviderResult.disabled(self.capability, self.name)
        return ProviderResult(
            capability=self.capability,
            provider=self.name,
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={"run_id": 9000 + gallery_id, "job_id": None, "mode": "sync"},
            model="mock-vision-challenger",
            latency_ms=5,
            cost_usd=0.002,
        )


class MockOffersAdapter:
    capability = Capability.OFFERS
    name = "mock"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def is_enabled(self) -> bool:
        return self.enabled

    def recommend_gallery(self, gallery_id: int) -> ProviderResult:
        if not self.enabled:
            return ProviderResult.disabled(self.capability, self.name)
        return ProviderResult(
            capability=self.capability,
            provider=self.name,
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={
                "run_id": 2000 + gallery_id,
                "bundle_count": 3,
                "estimated_total_cents": 30000,
                "review_url": None,
                "pitch_url": None,
            },
            model="mock-offers-1",
            latency_ms=0,
            cost_usd=0.0,
        )


class MockCaptionAdapter:
    capability = Capability.CONTENT
    name = "mock"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def is_enabled(self) -> bool:
        return self.enabled

    def draft(self, ctx: dict) -> ProviderResult:
        if not self.enabled:
            return ProviderResult.disabled(self.capability, self.name)
        label = (ctx.get("label") or "draft").strip()
        return ProviderResult(
            capability=self.capability,
            provider=self.name,
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={"caption": f"[mock caption] {label}"},
            model="mock-content-1",
            latency_ms=0,
            cost_usd=0.0,
            tokens=12,
        )


class MockAlbumAdapter:
    """A deterministic ALBUMS challenger: proposes a trivial layout (each photo in its own
    spread, in id order) so a test can assert exact, input-derived provenance without a
    real Mnemosyne backend. The proposal is metadata-shaped only — the deterministic
    validator in app/albums still owns correctness; this never writes a draft."""

    capability = Capability.ALBUMS
    name = "mock"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def is_enabled(self) -> bool:
        return self.enabled

    def propose_album(self, gallery_id: int, asset_ids: list[int] | None = None) -> ProviderResult:
        if not self.enabled:
            return ProviderResult.disabled(self.capability, self.name)
        ids = sorted(asset_ids or [])
        placements = [{"asset_id": a, "spread": i, "slot": 0} for i, a in enumerate(ids)]
        return ProviderResult(
            capability=self.capability,
            provider=self.name,
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={"placements": placements, "spread_count": len(placements)},
            model="mock-albums-1",
            latency_ms=0,
            cost_usd=0.0,
        )


class FailingAdapter:
    """A provider that always fails — used to prove failures stay non-mutating.

    Works for any capability; the caller passes the capability it stands in for.
    """

    name = "mock-failing"

    def __init__(self, capability: Capability, *, status: ResultStatus | None = None) -> None:
        self.capability = capability
        self._status = status or ResultStatus.PROVIDER_ERROR

    def is_enabled(self) -> bool:
        return True

    def _fail(self) -> ProviderResult:
        return ProviderResult.failure(
            self.capability, self.name, self._status, "mock failure", latency_ms=0
        )

    # Capability-shaped entry points so a FailingAdapter is drop-in for any legacy one.
    def analyze_gallery(self, gallery_id: int, *, skip_dedup: bool = False) -> ProviderResult:
        return self._fail()

    def recommend_gallery(self, gallery_id: int) -> ProviderResult:
        return self._fail()

    def draft(self, ctx: dict) -> ProviderResult:
        return self._fail()

    def propose_album(self, gallery_id: int, asset_ids: list[int] | None = None) -> ProviderResult:
        return self._fail()
