"""Internal album challenger — a Mnemosyne album-layout proposer on a local endpoint.

The local challenger to Mise's deterministic BASELINE album proposer
(``app.albums.propose_layout``). DORMANT until ``MISE_ALBUM_CHALLENGER_URL`` is set, and
``serves_production = False`` until its layouts are proven to beat the baseline (ADR 0011/0023) —
the registry interlock (``providers.registry.active_album_provider``) refuses to route production
album proposals here while False, so a misconfigured flag can never replace the baseline with an
unproven model.

Mise POSTs ``{gallery_id, asset_ids}`` and reads back the albums schema
``{placements:[{asset_id,spread,slot}], model, notes}``. The deterministic validator
(``app.albums``) re-checks every placement before a draft persists, so a bad proposal can never
omit/duplicate/misassign a photo. Privacy: ids only, never media. Never raises — every failure
becomes a non-OK ``ProviderResult``.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from .. import config
from .contracts import Capability, ProviderResult, ResultStatus, ReviewRequirement

log = logging.getLogger("mise.album_challenger")


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


class InternalAlbumChallengerAdapter:
    """ALBUMS challenger via a local Mnemosyne endpoint (dormant until configured)."""

    capability = Capability.ALBUMS
    name = "mnemosyne"
    # Eval-only: the registry interlock refuses to route production album proposals here while
    # this is False, so a flag pointing at it falls back to the baseline. Flip to True only after
    # its layouts beat the baseline on a human-scored set (ADR 0011/0023).
    serves_production = False

    def is_enabled(self) -> bool:
        return bool(config.ALBUM_CHALLENGER_URL)

    def propose_album(self, gallery_id: int, asset_ids: list[int] | None = None) -> ProviderResult:
        if not self.is_enabled():
            return ProviderResult.disabled(self.capability, self.name)
        start = time.monotonic()
        body = json.dumps({"gallery_id": gallery_id, "asset_ids": sorted(asset_ids or [])}).encode()
        headers = {"Content-Type": "application/json"}
        if config.ALBUM_CHALLENGER_TOKEN:
            headers["Authorization"] = f"Bearer {config.ALBUM_CHALLENGER_TOKEN}"
        req = urllib.request.Request(
            f"{config.ALBUM_CHALLENGER_URL}/propose", method="POST", data=body, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=config.ALBUM_CHALLENGER_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.PROVIDER_ERROR,
                f"album challenger error: {getattr(exc, 'reason', exc)}",
                latency_ms=_elapsed_ms(start),
            )
        except (ValueError, json.JSONDecodeError):
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.INVALID_RESPONSE,
                "album challenger returned an unreadable response",
                latency_ms=_elapsed_ms(start),
            )
        placements = payload.get("placements") if isinstance(payload, dict) else None
        if not isinstance(placements, list):
            return ProviderResult.failure(
                self.capability,
                self.name,
                ResultStatus.INVALID_RESPONSE,
                "album challenger response missing 'placements'",
                latency_ms=_elapsed_ms(start),
            )
        return ProviderResult(
            capability=self.capability,
            provider=self.name,
            status=ResultStatus.OK,
            review=ReviewRequirement.HUMAN_REVIEW,
            output={"placements": placements, "notes": payload.get("notes")},
            model=(payload.get("model") or config.ALBUM_CHALLENGER_MODEL),
            latency_ms=_elapsed_ms(start),
            cost_usd=0.0,  # local inference — no per-call provider charge
        )
