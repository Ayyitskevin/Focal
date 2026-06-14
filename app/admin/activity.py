from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .. import db, jobs, security
from ..render import templates

router = APIRouter(prefix="/admin", dependencies=[Depends(security.require_admin)])


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_view(request: Request):
    failed = db.all_("SELECT * FROM jobs WHERE status='failed' "
                     "ORDER BY updated_at DESC LIMIT 50")
    recent = db.all_("SELECT * FROM jobs WHERE status!='failed' ORDER BY id DESC LIMIT 30")
    return templates.TemplateResponse(request, "admin/jobs.html",
                                      {"failed": failed, "recent": recent,
                                       "pending": jobs.pending_count()})


@router.post("/jobs/{job_id}/retry")
async def job_retry(job_id: int):
    if not jobs.retry(job_id):
        raise HTTPException(status_code=404, detail="no failed job with that id")
    return RedirectResponse("/admin/jobs", status_code=303)


@router.get("/emails", response_class=HTMLResponse)
async def emails_view(request: Request):
    rows = db.all_("""SELECT v.email, v.first_seen, g.id AS gallery_id, g.title
                      FROM visitors v JOIN galleries g ON g.id=v.gallery_id
                      WHERE v.email IS NOT NULL
                      ORDER BY v.first_seen DESC""")
    distinct = db.one("""SELECT COUNT(DISTINCT email) AS n FROM visitors
                         WHERE email IS NOT NULL""")["n"]
    return templates.TemplateResponse(request, "admin/emails.html",
                                      {"rows": rows, "distinct": distinct})


@router.get("/emails.txt", response_class=PlainTextResponse)
async def emails_export():
    rows = db.all_("""SELECT DISTINCT email FROM visitors
                      WHERE email IS NOT NULL ORDER BY email""")
    return "\n".join(r["email"] for r in rows) + ("\n" if rows else "")


@router.get("/today", response_class=HTMLResponse)
async def today_view(request: Request):
    """Single-page 'what happened in the last 24h?' across inquiries,
    downloads, favorites, sent emails, and portal visits. Threads with the
    sparklines (ship #57/#58) — sparkline says 'something happened'; this
    view says 'this is what.'"""
    inquiries_24h = db.all_(
        """SELECT * FROM inquiries
           WHERE created_at >= datetime('now', '-24 hours')
           ORDER BY created_at DESC""")
    downloads_24h = db.all_(
        """SELECT d.created_at, d.gallery_id, d.asset_id,
                  g.title AS gallery_title, g.slug AS gallery_slug,
                  v.email AS visitor_email, a.filename
           FROM downloads d
           JOIN galleries g ON g.id=d.gallery_id
           LEFT JOIN visitors v ON v.id=d.visitor_id
           LEFT JOIN assets a ON a.id=d.asset_id
           WHERE d.created_at >= datetime('now', '-24 hours')
           ORDER BY d.created_at DESC""")
    favorites_24h = db.all_(
        """SELECT g.id AS gallery_id, g.title AS gallery_title, g.slug,
                  COUNT(DISTINCT f.asset_id) AS n_assets,
                  MAX(f.created_at) AS most_recent
           FROM favorites f
           JOIN assets a ON a.id=f.asset_id
           JOIN galleries g ON g.id=a.gallery_id
           WHERE f.created_at >= datetime('now', '-24 hours')
           GROUP BY g.id ORDER BY most_recent DESC""")
    sent_24h = db.all_(
        """SELECT e.*, p.title AS project_title, c.name AS client_name
           FROM emails_log e
           LEFT JOIN projects p ON p.id=e.project_id
           LEFT JOIN clients c ON c.id=p.client_id
           WHERE e.created_at >= datetime('now', '-24 hours')
           ORDER BY e.created_at DESC""")
    portal_visits_24h = db.all_(
        """SELECT p.*, c.name AS client_name, c.company
           FROM portals p JOIN clients c ON c.id=p.client_id
           WHERE p.last_visit IS NOT NULL
             AND p.last_visit >= datetime('now', '-24 hours')
           ORDER BY p.last_visit DESC""")
    return templates.TemplateResponse(request, "admin/today.html",
                                      {"inquiries": inquiries_24h,
                                       "downloads": downloads_24h,
                                       "favorites": favorites_24h,
                                       "sent": sent_24h,
                                       "portal_visits": portal_visits_24h})


@router.get("/sent", response_class=HTMLResponse)
async def sent_emails_view(request: Request, offset: int = 0):
    """Manual send audit log — proposal/contract/invoice/delivery emails Kevin
    has fired from the studio. Paginated 50/page, newest first."""
    offset = max(0, offset)
    page_size = 50
    rows = db.all_("""SELECT e.*, p.title AS project_title,
                             c.name AS client_name, c.company
                      FROM emails_log e
                      LEFT JOIN projects p ON p.id=e.project_id
                      LEFT JOIN clients c ON c.id=p.client_id
                      ORDER BY e.created_at DESC, e.id DESC
                      LIMIT ? OFFSET ?""", (page_size, offset))
    total = db.one("SELECT COUNT(*) AS n FROM emails_log")["n"]
    kinds = {r["doc_kind"]: r["n"] for r in db.all_(
        "SELECT doc_kind, COUNT(*) AS n FROM emails_log GROUP BY doc_kind")}
    return templates.TemplateResponse(request, "admin/sent.html",
                                      {"rows": rows, "total": total,
                                       "kinds": kinds, "offset": offset,
                                       "page_size": page_size})


@router.get("/galleries/{gallery_id}/activity", response_class=HTMLResponse)
async def activity(request: Request, gallery_id: int):
    g = db.one("SELECT * FROM galleries WHERE id=?", (gallery_id,))
    visitors = db.all_("""SELECT v.*,
                          (SELECT COUNT(*) FROM downloads d WHERE d.visitor_id=v.id) AS n_dl,
                          (SELECT COUNT(*) FROM favorites f WHERE f.visitor_id=v.id) AS n_fav
                          FROM visitors v WHERE v.gallery_id=?
                          ORDER BY v.first_seen DESC""", (gallery_id,))
    downloads = db.all_("""SELECT d.created_at, d.asset_id, a.filename, v.email
                           FROM downloads d
                           LEFT JOIN assets a ON a.id=d.asset_id
                           LEFT JOIN visitors v ON v.id=d.visitor_id
                           WHERE d.gallery_id=? ORDER BY d.created_at DESC LIMIT 200""",
                        (gallery_id,))
    favorites = db.all_("""SELECT a.id, a.filename, COUNT(*) AS n
                           FROM favorites f JOIN assets a ON a.id=f.asset_id
                           WHERE a.gallery_id=? GROUP BY a.id ORDER BY n DESC, a.filename""",
                        (gallery_id,))
    return templates.TemplateResponse(request, "admin/activity.html",
                                      {"g": g, "visitors": visitors,
                                       "downloads": downloads, "favorites": favorites})


@router.get("/galleries/{gallery_id}/favorites.txt", response_class=PlainTextResponse)
async def favorites_export(gallery_id: int):
    rows = db.all_("""SELECT DISTINCT a.filename
                      FROM favorites f JOIN assets a ON a.id=f.asset_id
                      WHERE a.gallery_id=? ORDER BY a.filename""", (gallery_id,))
    return "\n".join(r["filename"] for r in rows) + ("\n" if rows else "")
