"""Products render adapter — the dormant seam a future Aphrodite render worker plugs into.

Mirrors the vision challenger's posture: it conforms to the provider contract but does NOT
serve production. It is enabled only when ``MISE_PRODUCTS_RENDER_URL`` is set, and even then
the foundation ships without a wired backend — ``render`` returns a non-OK ``ProviderResult``
rather than calling out, so no image is generated and no spend occurs until a real worker is
implemented behind this seam (ADR 0021). The deterministic budget/consent/export guards live
in ``app/products.py``; this is only the (inert) capability adapter.
"""

from __future__ import annotations

from .. import config
from .contracts import Capability, ProviderResult, ResultStatus, ReviewRequirement


class ProductsRenderAdapter:
    """PRODUCTS capability adapter — dormant scaffold (no production render path yet)."""

    capability = Capability.PRODUCTS
    name = "aphrodite"
    # No writeback/production path exists; the cutover-style interlock pattern keeps any
    # future router from treating this as production until a backend is deliberately wired.
    serves_production = False

    def is_enabled(self) -> bool:
        return bool(config.PRODUCTS_RENDER_URL)

    def render(
        self, gallery_id: int, source_asset_id: int | None, spec: str | None
    ) -> ProviderResult:
        if not self.is_enabled():
            return ProviderResult.disabled(
                self.capability, self.name, review=ReviewRequirement.EXPLICIT_COMMIT
            )
        # Dormant foundation: no backend is wired in. A real Aphrodite render worker replaces
        # this body, after which app/products.create_render persists the budget-guarded result.
        return ProviderResult.failure(
            self.capability,
            self.name,
            ResultStatus.PROVIDER_ERROR,
            "product render backend not implemented",
            review=ReviewRequirement.EXPLICIT_COMMIT,
        )
