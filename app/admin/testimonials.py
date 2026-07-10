"""Admin testimonial curation and client testimonial requests."""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config, db, security
from ..render import templates

log = logging.getLogger("mise.admin.testimonials")
router = APIRouter(prefix="/admin/studio", dependencies=[Depends(security.require_admin)])


def _validated_attribution(quote: str, attribution_name: str) -> tuple[str, str]:
    """Normalize the required testimonial fields consistently on create and edit."""
    quote = quote.strip()
    attribution_name = attribution_name.strip()
    if not quote or not attribution_name:
        raise HTTPException(status_code=400, detail="quote and name required")
    return quote, attribution_name


@router.get("/testimonials", response_class=HTMLResponse)
async def testimonials_list(request: Request):
    rows = db.all_(
        """SELECT t.*, g.title AS gallery_title, g.slug AS gallery_slug,
                  EXISTS(SELECT 1 FROM testimonial_requests tr
                         WHERE tr.testimonial_id=t.id) AS from_client
           FROM testimonials t
           LEFT JOIN galleries g ON g.id=t.gallery_id
           ORDER BY t.position, t.id DESC"""
    )
    galleries = db.all_("SELECT id, title FROM galleries ORDER BY created_at DESC")
    return templates.TemplateResponse(
        request,
        "admin/testimonials.html",
        {"testimonials": rows, "galleries": galleries, "base_url": config.BASE_URL},
    )


@router.post("/testimonials")
async def create_testimonial(
    quote: str = Form(...),
    attribution_name: str = Form(...),
    business: str = Form(""),
    gallery_id: int | None = Form(None),
    position: int = Form(0),
    published: bool = Form(False),
):
    quote, attribution_name = _validated_attribution(quote, attribution_name)
    testimonial_id = db.run(
        """INSERT INTO testimonials (quote, attribution_name, business,
                                     gallery_id, position, published)
           VALUES (?,?,?,?,?,?)""",
        (
            quote,
            attribution_name,
            business.strip() or None,
            gallery_id,
            position,
            1 if published else 0,
        ),
    )
    log.info("testimonial %s created", testimonial_id)
    return RedirectResponse("/admin/studio/testimonials", status_code=303)


@router.post("/testimonials/{testimonial_id}")
async def update_testimonial(
    testimonial_id: int,
    quote: str = Form(...),
    attribution_name: str = Form(...),
    business: str = Form(""),
    gallery_id: int | None = Form(None),
    position: int = Form(0),
    published: bool = Form(False),
):
    db.get_or_404("SELECT id FROM testimonials WHERE id=?", (testimonial_id,))
    quote, attribution_name = _validated_attribution(quote, attribution_name)
    db.run(
        """UPDATE testimonials SET quote=?, attribution_name=?, business=?,
              gallery_id=?, position=?, published=? WHERE id=?""",
        (
            quote,
            attribution_name,
            business.strip() or None,
            gallery_id,
            position,
            1 if published else 0,
            testimonial_id,
        ),
    )
    return RedirectResponse("/admin/studio/testimonials", status_code=303)


@router.post("/testimonials/{testimonial_id}/delete")
async def delete_testimonial(testimonial_id: int):
    db.run("DELETE FROM testimonials WHERE id=?", (testimonial_id,))
    return RedirectResponse("/admin/studio/testimonials", status_code=303)


@router.post("/projects/{project_id}/testimonial-request")
async def request_testimonial(project_id: int, gallery_id: int | None = Form(None)):
    """Create a tokened client-submission link for an existing project."""
    project = db.get_or_404("SELECT client_id FROM projects WHERE id=?", (project_id,))
    request_id = db.run(
        """INSERT INTO testimonial_requests
                  (slug, client_id, project_id, gallery_id)
           VALUES (?,?,?,?)""",
        (security.new_slug(), project["client_id"], project_id, gallery_id),
    )
    log.info("testimonial request %s raised for project %s", request_id, project_id)
    return RedirectResponse(f"/admin/studio/projects/{project_id}", status_code=303)
