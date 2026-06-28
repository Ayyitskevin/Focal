"""Culling — the operator's keep/cut decision per asset (AI-assisted, human-decided).

The vision sidecars score every photo (argus_keeper_score, migration 064); this is where the
operator acts on those scores. AI only proposes a ranking — every keep/cut here is an explicit
human click, recorded on the asset's cull_state (migration 077) and audited. "cut" is a soft,
REVERSIBLE flag: it never deletes an original/derivative and (in this slice) never changes what a
client can see — a delivery gate is a separate, reviewed change. The destructive delete stays its
own confirm-gated route in galleries.py.

Inert until armed: every route 404s unless config.CULL_UI is on, so shipping this changes nothing
on a host until the operator flips the flag. Writes are admin-gated; CSRF is enforced globally.
"""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import audit, config, db, security

log = logging.getLogger("mise.admin.cull")
router = APIRouter(prefix="/admin", dependencies=[Depends(security.require_admin)])

# Operator actions → the stored cull_state they set. 'restore' clears the decision (back to
# undecided), the reversibility guarantee.
_ACTIONS = {"keep": "keep", "cut": "cut", "restore": None}


def _require_enabled() -> None:
    if not config.CULL_UI:
        raise HTTPException(status_code=404, detail="culling is not enabled")


def _apply_cull(con, gallery_id: int, asset_id: int, action: str) -> bool:
    """Set one asset's cull_state for `action` (keep/cut/restore), scoped to the gallery, and
    audit the change in the caller's transaction. Returns True if a row was updated. Never
    touches the file or the row beyond the three cull_* columns — fully reversible."""
    prior = con.execute(
        "SELECT cull_state FROM assets WHERE id=? AND gallery_id=?", (asset_id, gallery_id)
    ).fetchone()
    if not prior:
        return False
    new_state = _ACTIONS[action]
    # cull_source records that a human decided ('manual'); the SCORE provenance lives elsewhere.
    if new_state is None:
        con.execute(
            "UPDATE assets SET cull_state=NULL, cull_decided_at=NULL, cull_source=NULL "
            "WHERE id=? AND gallery_id=?",
            (asset_id, gallery_id),
        )
    else:
        con.execute(
            "UPDATE assets SET cull_state=?, cull_decided_at=datetime('now'), cull_source='manual' "
            "WHERE id=? AND gallery_id=?",
            (new_state, asset_id, gallery_id),
        )
    audit.log(
        con,
        "asset",
        asset_id,
        f"cull:{action}",
        diff={"cull_state": [prior["cull_state"], new_state]},
    )
    return True


@router.post("/galleries/{gallery_id}/assets/{asset_id}/cull")
async def cull_asset(gallery_id: int, asset_id: int, action: str = Form(...)):
    """Record the operator's keep / cut / restore decision on one asset. Reversible; writes no
    file and (this slice) gates no delivery — just the decision + an audit row."""
    _require_enabled()
    if action not in _ACTIONS:
        raise HTTPException(status_code=400, detail="action must be keep, cut, or restore")
    with db.tx() as con:
        if not _apply_cull(con, gallery_id, asset_id, action):
            raise HTTPException(status_code=404, detail="asset not in this gallery")
    return RedirectResponse(f"/admin/galleries/{gallery_id}", status_code=303)


@router.post("/galleries/{gallery_id}/assets/bulk-cull")
async def bulk_cull(request: Request, gallery_id: int):
    """Apply one keep/cut/restore to many assets at once (e.g. 'cut all low-score candidates').
    Server-side scoped to this gallery — a posted id from another gallery is silently skipped, so
    a tampered form can't reach across galleries. Each asset's change is audited."""
    _require_enabled()
    form = await request.form()
    action = form.get("action") or ""
    if action not in _ACTIONS:
        raise HTTPException(status_code=400, detail="action must be keep, cut, or restore")
    n = 0
    with db.tx() as con:
        for raw in form.getlist("asset_ids"):
            try:
                aid = int(raw)
            except (TypeError, ValueError):
                continue
            if _apply_cull(con, gallery_id, aid, action):
                n += 1
    log.info("bulk cull %s: %s assets -> %s (gallery %s)", action, n, action, gallery_id)
    return RedirectResponse(f"/admin/galleries/{gallery_id}", status_code=303)
