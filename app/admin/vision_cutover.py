"""Vision cutover — the operator preflight for promoting the Qwen challenger over Argus.

The promotion seam (ADR 0016) and the dormant production writeback (ADR 0017) are both
built; this page is their cockpit. It surfaces three things and decides none of them:

* the readiness **checklist** — what remains before Qwen can serve production vision, with the
  exact next step (``qwen_writeback.readiness``);
* a per-gallery **dry-run preview** — Qwen's structured per-photo signals validated but
  *written nowhere* (``qwen_writeback.preview_gallery``), the prompt-tuning loop;
* a manual **writeback** trigger (``qwen_writeback.enqueue_writeback``) that the interlock
  refuses until Qwen is the eligible production provider.

Nothing here flips the provider — that stays a deliberate flag + reviewed code change.
Admin-gated; same-origin enforced by the global CSRF middleware.
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import qwen_writeback, security
from ..render import templates

log = logging.getLogger("mise.admin.vision_cutover")
router = APIRouter(prefix="/admin/vision-cutover", dependencies=[Depends(security.require_admin)])


def _ctx(*, msg: str = "", err: str = "", preview: dict | None = None, preview_gid=None) -> dict:
    return {
        "readiness": qwen_writeback.readiness(),
        "preview": preview,
        "preview_gid": preview_gid,
        "msg": msg,
        "err": err,
    }


@router.get("", response_class=HTMLResponse)
async def cutover_view(request: Request, msg: str = "", err: str = ""):
    return templates.TemplateResponse(request, "admin/vision_cutover.html", _ctx(msg=msg, err=err))


def _gallery_id(form) -> int | None:
    try:
        return int(form.get("gallery_id") or "")
    except (TypeError, ValueError):
        return None


@router.post("/preview", response_class=HTMLResponse)
async def preview(request: Request):
    """Dry-run Qwen on one gallery and render the parsed signals. Asset-safe — writes
    nothing — so it renders the result inline rather than via a redirect."""
    form = await request.form()
    gid = _gallery_id(form)
    if gid is None:
        return templates.TemplateResponse(
            request, "admin/vision_cutover.html", _ctx(err="A numeric gallery id is required.")
        )
    result = qwen_writeback.preview_gallery(gid)
    log.info("qwen preview gallery %s -> ok=%s", gid, result.get("ok"))
    return templates.TemplateResponse(
        request, "admin/vision_cutover.html", _ctx(preview=result, preview_gid=gid)
    )


def _redirect(msg: str = "", err: str = "") -> RedirectResponse:
    q = []
    if msg:
        q.append(f"msg={msg}")
    if err:
        q.append(f"err={err}")
    suffix = ("?" + "&".join(q)) if q else ""
    return RedirectResponse(f"/admin/vision-cutover{suffix}", status_code=303)


@router.post("/run")
async def run(request: Request):
    """Manually queue a production writeback for one gallery. The interlock refuses unless
    Qwen is the eligible production provider, so this mutates nothing until promotion."""
    form = await request.form()
    gid = _gallery_id(form)
    if gid is None:
        return _redirect(err="A numeric gallery id is required.")
    job_id = qwen_writeback.enqueue_writeback(gid)
    if job_id is None:
        return _redirect(
            err="Refused: Qwen is not the eligible production provider — see the checklist."
        )
    log.info("qwen writeback queued for gallery %s (job %s)", gid, job_id)
    return _redirect(msg=f"Queued Qwen writeback for gallery {gid}.")
