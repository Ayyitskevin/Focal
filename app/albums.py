"""Mnemosyne album foundation — the deterministic layout validator + draft persistence.

An *album draft* is a curated, ordered subset of a gallery's photos arranged into
spreads. A (future) Mnemosyne worker will *propose* one; a human approves it before
anything prints. This module is the deterministic floor under that proposal — the part
the 2026-06-25 audit (§11.4, "model proposes, deterministic code validates") insists is
NOT the model's job.

The one invariant it enforces: an album draft must **never silently omit, duplicate, or
misassign** a photo.

* **duplicate**   — the same asset placed twice -> hard issue.
* **foreign**     — a placed asset that is not an eligible photo of *this* gallery
                    (wrong gallery, a video, or a not-ready/failed asset) -> hard issue.
* **misassign**   — two photos in the same (spread, slot), or a malformed placement
                    (no integer asset id, negative spread/slot) -> hard issue.
* **omitted**     — eligible photos with no placement. Omission is the photographer's
                    editorial right (an album is a subset), so it is NOT a hard issue —
                    but it is always *surfaced*, never silent, so a human can confirm the
                    cull was intentional.

The validator performs at most one read (the gallery's eligible photo ids) and decides
nothing about money, publication, or print. ``save_draft`` refuses to persist a layout
with any hard issue, so a bad proposal cannot become a stored draft. Everything here is
dormant: nothing in the running app calls it yet (see ADR 0009).
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import ai_runs, audit, db
from .providers import Capability, ProviderResult, ResultStatus, ReviewRequirement, registry

log = logging.getLogger("mise.albums")

# A photo is eligible for an album when it belongs to the gallery, is a photo (not a
# video), and finished processing. Mirrors the assets CHECK constraints in 001_init.
_ELIGIBLE_SQL = "SELECT id FROM assets WHERE gallery_id = ? AND kind = 'photo' AND status = 'ready'"


@dataclass(frozen=True)
class LayoutIssue:
    """One correctness violation. ``code`` is stable for callers to branch on; ``detail``
    is human-readable. ``asset_id`` / ``spread`` / ``slot`` are populated when relevant."""

    code: str  # "duplicate" | "foreign_asset" | "slot_collision" | "bad_placement"
    detail: str
    asset_id: int | None = None
    spread: int | None = None
    slot: int | None = None


@dataclass(frozen=True)
class LayoutValidation:
    """The full, order-independent verdict for a proposed layout.

    ``issues`` is every hard violation (not just the first) so a reviewer sees the whole
    picture. ``omitted`` is surfaced eligible-but-unplaced photos. ``ok`` is True only
    when there are no hard issues; omission alone never fails validation.
    """

    eligible: tuple[int, ...]
    placed: tuple[int, ...]
    omitted: tuple[int, ...]
    issues: tuple[LayoutIssue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.issues


def _as_index(value: Any) -> int | None:
    """Coerce a spread/slot to a non-negative int, or None if it isn't one. bool is
    rejected even though it is an int subclass (a True slot is a bug, not slot 1)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def validate_core(eligible_ids: set[int], placements: list[dict]) -> LayoutValidation:
    """Pure validation: no DB, no I/O. ``eligible_ids`` is the gallery's eligible photo
    set; ``placements`` is the proposed layout. Exhaustively reports every hard issue and
    surfaces every omission. This is the function tests pin the invariant against."""
    issues: list[LayoutIssue] = []
    placed: list[int] = []
    seen_slots: dict[tuple[int, int], int] = {}

    for i, p in enumerate(placements):
        raw_id = p.get("asset_id")
        spread = _as_index(p.get("spread", 0))
        slot = _as_index(p.get("slot", 0))

        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            issues.append(LayoutIssue("bad_placement", f"placement {i} has no integer asset_id"))
            continue
        if spread is None or slot is None:
            issues.append(
                LayoutIssue(
                    "bad_placement",
                    f"placement {i} (asset {raw_id}) has a non-negative-integer spread/slot",
                    asset_id=raw_id,
                )
            )
            continue

        placed.append(raw_id)

        if raw_id not in eligible_ids:
            issues.append(
                LayoutIssue(
                    "foreign_asset",
                    f"asset {raw_id} is not an eligible photo of this gallery",
                    asset_id=raw_id,
                )
            )

        key = (spread, slot)
        if key in seen_slots:
            issues.append(
                LayoutIssue(
                    "slot_collision",
                    f"assets {seen_slots[key]} and {raw_id} both claim spread {spread} slot {slot}",
                    asset_id=raw_id,
                    spread=spread,
                    slot=slot,
                )
            )
        else:
            seen_slots[key] = raw_id

    # Duplicates: report each offending asset once, regardless of how many times it repeats.
    for asset_id, count in Counter(placed).items():
        if count > 1:
            issues.append(
                LayoutIssue(
                    "duplicate",
                    f"asset {asset_id} is placed {count} times",
                    asset_id=asset_id,
                )
            )

    placed_set = set(placed)
    omitted = tuple(sorted(a for a in eligible_ids if a not in placed_set))
    # Sort issues for a stable, reviewer-friendly order (code, then asset).
    issues.sort(key=lambda x: (x.code, x.asset_id or 0, x.spread or 0, x.slot or 0))
    return LayoutValidation(
        eligible=tuple(sorted(eligible_ids)),
        placed=tuple(placed),
        omitted=omitted,
        issues=tuple(issues),
    )


def eligible_asset_ids(gallery_id: int) -> set[int]:
    """The set of photo asset ids eligible for an album of ``gallery_id`` (photo + ready)."""
    return {r["id"] for r in db.all_(_ELIGIBLE_SQL, (gallery_id,))}


def validate_layout(gallery_id: int, placements: list[dict]) -> LayoutValidation:
    """Validate a proposed layout against the gallery's *current* eligible photos.

    The thin DB wrapper over :func:`validate_core`: it resolves the eligible set, then
    delegates. Non-mutating — it only reads.
    """
    return validate_core(eligible_asset_ids(gallery_id), placements)


class LayoutError(ValueError):
    """A layout that violates the never-omit/duplicate/misassign invariant. Carries the
    full :class:`LayoutValidation` so a caller can surface every issue, not just a string."""

    def __init__(self, validation: LayoutValidation) -> None:
        self.validation = validation
        codes = ", ".join(sorted({i.code for i in validation.issues})) or "empty"
        super().__init__(f"album layout rejected: {codes}")


def save_draft(
    gallery_id: int,
    placements: list[dict],
    *,
    provider: str | None = None,
    model: str | None = None,
    notes: str | None = None,
) -> int:
    """Validate then persist an album draft for ``gallery_id``; return the new draft id.

    Refuses (raises :class:`LayoutError`) when the layout has any hard issue OR is empty —
    a bad or empty proposal must never become a stored draft. The draft lands at status
    ``draft`` (HUMAN_REVIEW); a human transition is what later approves it. Draft +
    placements are written in one transaction so a draft never persists half its photos.
    """
    validation = validate_layout(gallery_id, placements)
    if not validation.ok or not placements:
        raise LayoutError(validation)

    spread_count = len({_as_index(p.get("spread", 0)) for p in placements})
    with db.tx() as con:
        cur = con.execute(
            """INSERT INTO album_drafts (gallery_id, status, provider, model, spread_count, notes)
               VALUES (?, 'draft', ?, ?, ?, ?)""",
            (gallery_id, provider, model, spread_count, notes),
        )
        draft_id = cur.lastrowid
        con.executemany(
            """INSERT INTO album_placements (album_draft_id, asset_id, spread, slot)
               VALUES (?, ?, ?, ?)""",
            [
                (
                    draft_id,
                    p["asset_id"],
                    _as_index(p.get("spread", 0)),
                    _as_index(p.get("slot", 0)),
                )
                for p in placements
            ],
        )
    return draft_id


# ── proposer + draft lifecycle (the Mnemosyne worker seam + human review) ─────────

DRAFT_STATUSES = ("draft", "approved", "rejected")
_INTERNAL_PROVIDER = "internal"
_INTERNAL_MODEL = "album-baseline-1"


def propose_layout(eligible_ids, *, per_spread: int = 2) -> list[dict]:
    """Deterministic baseline layout: eligible photos in id order, ``per_spread`` slots per
    spread. Pure — the safe default proposal a human then reviews/edits/approves. A future
    Mnemosyne model can replace this as the proposer; the validator guards either source so
    the baseline is never silently omitting/duplicating/misassigning a photo."""
    if per_spread < 1:
        raise ValueError("per_spread must be >= 1")
    return [
        {"asset_id": asset_id, "spread": i // per_spread, "slot": i % per_spread}
        for i, asset_id in enumerate(sorted(eligible_ids))
    ]


def _provider_placements(
    gallery_id: int, eligible: set[int], per_spread: int
) -> tuple[list[dict], str, str]:
    """Prefer a registered ALBUMS provider's proposal — the seam a future Mnemosyne backend
    plugs into — and fall back to the deterministic internal baseline. Returns
    ``(placements, provider, model)``. A registered provider's output is still handed to
    ``save_draft``'s validator, so a bad model proposal cannot become a stored draft."""
    try:
        adapter = registry.resolve(Capability.ALBUMS)
    except ValueError:
        adapter = None
    if adapter is not None and adapter.is_enabled():
        result = adapter.propose_album(gallery_id, sorted(eligible))
        if result.ok and result.output and result.output.get("placements"):
            return result.output["placements"], result.provider, (result.model or "")
    return propose_layout(eligible, per_spread=per_spread), _INTERNAL_PROVIDER, _INTERNAL_MODEL


def propose_draft(gallery_id: int, *, per_spread: int = 2) -> int | None:
    """Propose an album draft for ``gallery_id`` and persist it in HUMAN_REVIEW state.

    Returns the new draft id, or None when the gallery has no eligible photos. The layout
    is validated by :func:`save_draft` before it persists, so a bad proposal never becomes
    a stored draft. ALBUMS provenance is recorded to ai_runs best-effort (a ledger failure
    never blocks the draft). Nothing is printed or charged — a human still approves.
    """
    eligible = eligible_asset_ids(gallery_id)
    if not eligible:
        return None
    placements, provider, model = _provider_placements(gallery_id, eligible, per_spread)
    draft_id = save_draft(gallery_id, placements, provider=provider, model=model)
    _record_provenance(gallery_id, draft_id, provider, model, len(placements))
    return draft_id


def _record_provenance(
    gallery_id: int, draft_id: int, provider: str, model: str, placement_count: int
) -> None:
    try:
        ai_runs.record(
            ProviderResult(
                capability=Capability.ALBUMS,
                provider=provider,
                status=ResultStatus.OK,
                review=ReviewRequirement.HUMAN_REVIEW,
                output={"draft_id": draft_id, "placement_count": placement_count},
                model=model or None,
            ),
            subject_type="gallery",
            subject_id=gallery_id,
            correlation_id=f"album:gallery:{gallery_id}:{draft_id}",
        )
    except Exception:
        log.exception("album provenance failed for gallery %s draft %s", gallery_id, draft_id)


def get_draft(draft_id: int) -> dict | None:
    row = db.one("SELECT * FROM album_drafts WHERE id=?", (draft_id,))
    return dict(row) if row else None


def draft_placements(draft_id: int) -> list[dict]:
    """The draft's placements joined to their asset (filename + gallery for thumbnails),
    ordered by spread then slot — the layout a reviewer sees."""
    rows = db.all_(
        """SELECT p.asset_id, p.spread, p.slot, a.filename, a.gallery_id
           FROM album_placements p JOIN assets a ON a.id = p.asset_id
           WHERE p.album_draft_id=? ORDER BY p.spread, p.slot, p.id""",
        (draft_id,),
    )
    return [dict(r) for r in rows]


def list_drafts(gallery_id: int | None = None, status: str | None = None) -> list[dict]:
    sql = """SELECT d.*, g.slug, g.title,
                    (SELECT COUNT(*) FROM album_placements p WHERE p.album_draft_id=d.id)
                        AS placement_count
             FROM album_drafts d JOIN galleries g ON g.id = d.gallery_id WHERE 1=1"""
    params: list = []
    if gallery_id is not None:
        sql += " AND d.gallery_id=?"
        params.append(gallery_id)
    if status:
        sql += " AND d.status=?"
        params.append(status)
    sql += " ORDER BY d.created_at DESC, d.id DESC"
    return [dict(r) for r in db.all_(sql, tuple(params))]


def set_status(draft_id: int, status: str) -> None:
    """Transition a draft's review state (draft -> approved/rejected). A human action; it
    records the decision and prints/charges nothing. Raises on an unknown status."""
    if status not in DRAFT_STATUSES:
        raise ValueError(f"invalid album draft status: {status!r}")
    db.run(
        "UPDATE album_drafts SET status=?, updated_at=datetime('now') WHERE id=?",
        (status, draft_id),
    )


class OrderError(ValueError):
    """The album draft can't be ordered — e.g. it isn't approved. Non-mutating."""


def mark_ordered(
    draft_id: int,
    *,
    size: str | None = None,
    cover: str | None = None,
    notes: str | None = None,
) -> None:
    """Record that an APPROVED album draft was ordered, with its spec (size/cover/notes) —
    the record-only fulfillment step (ADR 0019). It prints nothing, hands off to no vendor,
    and charges nothing; the operator orders however they do today and this captures the
    decision + spec for the record.

    ``ordered_at`` is set on the first order and preserved on later spec edits, so the
    original order date is stable. Raises :class:`OrderError` unless the draft is approved.
    The column update and the audit row commit together.
    """
    draft = db.one("SELECT id, status, ordered_at FROM album_drafts WHERE id=?", (draft_id,))
    if not draft:
        raise OrderError(f"no album draft #{draft_id}")
    if draft["status"] != "approved":
        raise OrderError("only an approved album can be marked ordered")
    size = (size or "").strip() or None
    cover = (cover or "").strip() or None
    notes = (notes or "").strip() or None
    first_order = draft["ordered_at"] is None
    with db.tx() as con:
        con.execute(
            """UPDATE album_drafts
               SET ordered_at = COALESCE(ordered_at, datetime('now')),
                   order_size = ?, order_cover = ?, order_notes = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (size, cover, notes, draft_id),
        )
        audit.log(
            con,
            "album_draft",
            draft_id,
            "album_ordered" if first_order else "album_order_updated",
            diff={"size": size, "cover": cover, "notes": notes},
        )


def clear_order(draft_id: int) -> None:
    """Undo an order mark (operator marked one by mistake) — clears the date + spec. Records
    nothing to money/print state because there was none. Idempotent."""
    with db.tx() as con:
        con.execute(
            """UPDATE album_drafts
               SET ordered_at = NULL, order_size = NULL, order_cover = NULL, order_notes = NULL,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (draft_id,),
        )
        audit.log(con, "album_draft", draft_id, "album_order_cleared")
