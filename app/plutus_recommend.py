"""One-way Plutus upsell hand-off after gallery analyze (Phase 1).

Mise POSTs mise_gallery_id to Plutus /recommend/mise-gallery. Failure is swallowed
in run_for_gallery so jobs never crash; the gallery row records last status for admin.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from . import config, db, features

log = logging.getLogger("mise.plutus")


class PlutusRecommendError(Exception):
    """Human-readable failure safe for admin UI."""


def is_enabled() -> bool:
    return bool(config.PLUTUS_URL and config.PLUTUS_TOKEN)


def _record(
    gallery_id: int,
    *,
    status: str,
    run_id: int | None = None,
    error: str | None = None,
    review_url: str | None = None,
    pitch_url: str | None = None,
    bundle_count: int | None = None,
    estimated_total_cents: int | None = None,
    bundles: list[dict] | None = None,
) -> None:
    # plutus_last_offer_url stores review_url for backward-compatible schema.
    # plutus_last_bundles persists the validated bundle catalogue (with SKUs) for ADR 0022
    # attribution; NULL when there is nothing valid to store, leaving the summary unaffected.
    db.run(
        """UPDATE galleries SET plutus_last_run_id=?, plutus_last_status=?,
              plutus_last_error=?, plutus_last_offer_url=?, plutus_last_pitch_url=?,
              plutus_last_bundle_count=?, plutus_last_estimated_cents=?, plutus_last_bundles=?,
              plutus_last_at=datetime('now')
              WHERE id=?""",
        (
            run_id,
            status,
            (error or None)[:500] if error else None,
            (review_url or None)[:500] if review_url else None,
            (pitch_url or None)[:500] if pitch_url else None,
            bundle_count,
            estimated_total_cents,
            json.dumps(bundles) if bundles else None,
            gallery_id,
        ),
    )


def parse_bundles(payload: dict | None) -> list[dict] | None:
    """Deterministically validate Plutus's proposed ``bundles`` into a normalized list for
    persistence (ADR 0022, piece 1) — the offers.schema.json analog of
    ``qwen_writeback.parse_structured``.

    Each bundle must have a non-empty ``label`` and an integer ``estimated_cents`` >= 0. The
    stable ``sku`` (the key that later links an accepted offer to an invoice line) is preserved
    when present and a non-empty string, else ``None`` — Plutus only emits SKUs after PLUTUS #1,
    so this stays useful (and inert) before then. Optional ``line_items`` are
    ``[{label, qty>=1, unit_cents>=0}]``.

    Returns the normalized list, or ``None`` when there is nothing valid to store (no/empty
    bundles) OR *any* bundle is malformed — a conservative all-or-nothing gate so a bad proposal
    never persists a partial catalogue. Non-raising: recording bundles is best-effort and never
    blocks the summary columns or touches money/invoice state.
    """
    if not isinstance(payload, dict):
        return None
    bundles = payload.get("bundles")
    if not isinstance(bundles, list) or not bundles:
        return None

    def _nonneg_int(value) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    out: list[dict] = []
    for b in bundles:
        if not isinstance(b, dict):
            return None
        label = b.get("label")
        if not isinstance(label, str) or not label.strip():
            return None
        if not _nonneg_int(b.get("estimated_cents")):
            return None
        sku = b.get("sku")
        sku = sku.strip() if isinstance(sku, str) and sku.strip() else None
        norm: dict = {"sku": sku, "label": label.strip(), "estimated_cents": b["estimated_cents"]}

        line_items = b.get("line_items")
        if line_items is not None:
            if not isinstance(line_items, list):
                return None
            items: list[dict] = []
            for li in line_items:
                if not isinstance(li, dict):
                    return None
                li_label = li.get("label")
                qty = li.get("qty")
                if not isinstance(li_label, str) or not li_label.strip():
                    return None
                if not (isinstance(qty, int) and not isinstance(qty, bool) and qty >= 1):
                    return None
                if not _nonneg_int(li.get("unit_cents")):
                    return None
                items.append(
                    {"label": li_label.strip(), "qty": qty, "unit_cents": li["unit_cents"]}
                )
            norm["line_items"] = items
        out.append(norm)
    return out


def _bundle_meta(payload: dict) -> tuple[int | None, int | None]:
    bundles = payload.get("bundles")
    if bundles is not None:
        count = len(bundles)
    elif payload.get("bundle_count") is not None:
        count = int(payload["bundle_count"])
    else:
        count = None
    cents = payload.get("estimated_total_cents")
    return count, int(cents) if cents is not None else None


def bundles_to_line_items(bundles: list[dict] | None) -> list[dict]:
    """Flatten persisted offer bundles (the ``parse_bundles`` shape) into invoice line-item
    dicts, each carrying the bundle's stable ``sku`` (ADR 0022 piece 2). A bundle with
    ``line_items`` yields one invoice line per line_item; a bundle without yields a single line
    from its ``label`` + ``estimated_cents``. Bundles missing a ``sku`` are SKIPPED — without the
    linkage key the line can't be attributed, so there's nothing to pre-fill (this is what keeps
    the pre-fill inert until Plutus emits SKUs, PLUTUS #1). Pure; returns ``[]`` for empty/None.
    This only PROPOSES invoice lines for an operator to add — it never creates or sends an
    invoice (audit §11.4)."""
    if not bundles:
        return []
    out: list[dict] = []
    for b in bundles:
        sku = b.get("sku")
        if not sku:
            continue
        line_items = b.get("line_items")
        if line_items:
            for li in line_items:
                out.append(
                    {
                        "label": li["label"],
                        "qty": li["qty"],
                        "unit_cents": li["unit_cents"],
                        "sku": sku,
                    }
                )
        else:
            out.append(
                {"label": b["label"], "qty": 1, "unit_cents": b["estimated_cents"], "sku": sku}
            )
    return out


def trigger_gallery_recommend(gallery_id: int) -> dict:
    if not is_enabled():
        raise PlutusRecommendError("Plutus is not configured")
    g = db.one(
        "SELECT id, published, type, argus_last_run_id FROM galleries WHERE id=?",
        (gallery_id,),
    )
    if not g:
        raise PlutusRecommendError(f"gallery {gallery_id} not found")
    if not g["published"]:
        raise PlutusRecommendError("gallery is not published")
    if g["type"] == "drop":
        raise PlutusRecommendError("transfers are not analyzed")

    fields: dict[str, str] = {"mise_gallery_id": str(gallery_id)}
    if g["argus_last_run_id"]:
        fields["argus_run_id"] = str(g["argus_last_run_id"])
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        f"{config.PLUTUS_URL}/recommend/mise-gallery",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {config.PLUTUS_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=config.PLUTUS_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:200]
        except Exception:
            pass
        raise PlutusRecommendError(
            f"Plutus returned HTTP {e.code}" + (f": {detail}" if detail else "")
        )
    except (urllib.error.URLError, TimeoutError) as e:
        reason = e.reason if hasattr(e, "reason") else e
        raise PlutusRecommendError(f"Plutus unreachable: {reason}")
    except (ValueError, json.JSONDecodeError):
        raise PlutusRecommendError("Plutus returned an unreadable response")

    if not isinstance(payload, dict) or not payload.get("run_id"):
        raise PlutusRecommendError("Plutus response missing run_id")

    log.info(
        "plutus recommend gallery %s -> run=%s bundles=%s",
        gallery_id,
        payload.get("run_id"),
        len(payload.get("bundles") or []),
    )
    return payload


def apply_callback(gallery_id: int, payload: dict) -> None:
    """Record Plutus hand-off result (from Mise job worker or Argus callback)."""
    status = (payload.get("status") or "done").strip()
    run_id = payload.get("run_id")
    error = payload.get("error")
    review_url = payload.get("review_url") or payload.get("offer_url")
    pitch_url = payload.get("pitch_url")
    bundle_count, estimated_cents = _bundle_meta(payload)
    bundles = parse_bundles(payload)
    if status == "done" and run_id is not None:
        _record(
            gallery_id,
            status="done",
            run_id=int(run_id),
            review_url=str(review_url) if review_url else None,
            pitch_url=str(pitch_url) if pitch_url else None,
            bundle_count=bundle_count,
            estimated_total_cents=estimated_cents,
            bundles=bundles,
        )
        return
    _record(
        gallery_id,
        status="error" if status != "done" else "done",
        run_id=int(run_id) if run_id is not None else None,
        error=str(error) if error else None,
        review_url=str(review_url) if review_url else None,
        pitch_url=str(pitch_url) if pitch_url else None,
        bundle_count=bundle_count,
        estimated_total_cents=estimated_cents,
        bundles=bundles,
    )


def _run_via_facade(gallery_id: int) -> None:
    """Phase 3 facade path: route the recommend through providers.resolve(OFFERS) — which
    still resolves to the legacy Plutus adapter — record provenance to ai_runs, and record
    plutus_last_* from the same result. Single Plutus call; same recording as the legacy
    path. Offers stay proposal-only; this never touches money/invoice state."""
    from . import ai_runs, providers

    pr = providers.resolve(providers.Capability.OFFERS).recommend_gallery(gallery_id)
    # Record the authoritative business state FIRST (the adapter is non-mutating and never
    # raises — see adapters.py). The ledger write comes after and is best-effort, so a
    # provenance failure can neither skip the plutus_last_* write nor cause the background
    # job to retry and re-issue the Plutus call (which would duplicate offer drafts).
    if not pr.ok:
        log.warning("plutus recommend failed for gallery %s (facade): %s", gallery_id, pr.error)
        _record(gallery_id, status="error", error=pr.error)
    else:
        out = pr.output or {}
        _record(
            gallery_id,
            status="done",
            run_id=int(out["run_id"]) if out.get("run_id") is not None else None,
            review_url=out.get("review_url"),
            pitch_url=out.get("pitch_url"),
            bundle_count=out.get("bundle_count"),
            estimated_total_cents=out.get("estimated_total_cents"),
            bundles=parse_bundles(out),
        )
    try:
        ai_runs.record(pr, subject_type="gallery", subject_id=gallery_id)
    except Exception:
        log.exception("plutus offer provenance failed for gallery %s", gallery_id)


def run_for_gallery(gallery_id: int) -> None:
    if not is_enabled():
        log.info("plutus recommend skipped for %s (not configured)", gallery_id)
        return
    if features.offers_provider_facade_enabled():
        _run_via_facade(gallery_id)
        return
    try:
        result = trigger_gallery_recommend(gallery_id)
    except PlutusRecommendError as e:
        log.warning("plutus recommend failed for gallery %s: %s", gallery_id, e)
        _record(gallery_id, status="error", error=str(e))
        return
    except Exception as e:
        log.exception("plutus recommend unexpected failure for gallery %s", gallery_id)
        _record(gallery_id, status="error", error=str(e)[:500])
        return

    bundle_count, estimated_cents = _bundle_meta(result)
    _record(
        gallery_id,
        status="done",
        run_id=int(result["run_id"]),
        review_url=result.get("review_url"),
        pitch_url=result.get("pitch_url"),
        bundle_count=bundle_count,
        estimated_total_cents=estimated_cents,
        bundles=parse_bundles(result),
    )
