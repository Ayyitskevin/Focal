"""One-way Argus vision hand-off on gallery publish (Phase 6).

Mise POSTs mise_gallery_id to Argus /analyze-folder; Argus resolves originals via
ARGUS_MISE_MEDIA_ROOT on its host. Failure is EXPECTED on the mesh — every failure
path is swallowed in run_for_gallery so publish and background jobs never crash; the
gallery row records the last status for admin surfacing.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from . import config, db

log = logging.getLogger("mise.argus")


class ArgusAnalyzeError(Exception):
    """Human-readable failure safe for admin UI (no secrets)."""


def is_enabled() -> bool:
    """Armed only when BOTH Argus URL and bearer token are configured."""
    return bool(config.ARGUS_URL and config.ARGUS_TOKEN)


def _record(gallery_id: int, *, status: str, run_id: int | None = None,
            job_id: str | None = None, error: str | None = None) -> None:
    db.run("""UPDATE galleries SET argus_last_run_id=?, argus_last_job_id=?,
              argus_last_status=?, argus_last_error=?, argus_last_at=datetime('now')
              WHERE id=?""",
           (run_id, job_id, status, (error or None)[:500] if error else None, gallery_id))


def trigger_gallery_analyze(gallery_id: int) -> dict:
    """POST /analyze-folder for one published gallery. Returns Argus JSON body."""
    if not is_enabled():
        raise ArgusAnalyzeError("Argus is not configured")
    g = db.one("SELECT id, published, type, project_id FROM galleries WHERE id=?",
               (gallery_id,))
    if not g:
        raise ArgusAnalyzeError(f"gallery {gallery_id} not found")
    if not g["published"]:
        raise ArgusAnalyzeError("gallery is not published")
    if g["type"] == "drop":
        raise ArgusAnalyzeError("transfers are not analyzed")

    body = urllib.parse.urlencode({
        "mise_gallery_id": gallery_id,
        "limit": config.ARGUS_ANALYZE_LIMIT,
        "source": "mise",
    }).encode()
    req = urllib.request.Request(
        f"{config.ARGUS_URL}/analyze-folder",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {config.ARGUS_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=config.ARGUS_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:200]
        except Exception:
            pass
        raise ArgusAnalyzeError(f"Argus returned HTTP {e.code}" + (f": {detail}" if detail else ""))
    except (urllib.error.URLError, TimeoutError) as e:
        reason = e.reason if hasattr(e, "reason") else e
        raise ArgusAnalyzeError(f"Argus unreachable: {reason}")
    except (ValueError, json.JSONDecodeError):
        raise ArgusAnalyzeError("Argus returned an unreadable response")

    if not isinstance(payload, dict):
        raise ArgusAnalyzeError("Argus returned an unexpected response")

    run_id = payload.get("run_id")
    job_id = payload.get("job_id")
    if run_id is None and job_id is None:
        raise ArgusAnalyzeError("Argus response missing run_id and job_id")

    mode = payload.get("mode") or ("queued" if job_id else "sync")
    log.info("argus analyze gallery %s -> mode=%s run=%s job=%s",
             gallery_id, mode, run_id, job_id)
    return payload


def run_for_gallery(gallery_id: int) -> None:
    """Background job entry — never raises; records status on the gallery row."""
    if not is_enabled():
        log.info("argus analyze skipped for %s (not configured)", gallery_id)
        return
    try:
        result = trigger_gallery_analyze(gallery_id)
    except ArgusAnalyzeError as e:
        log.warning("argus analyze failed for gallery %s: %s", gallery_id, e)
        _record(gallery_id, status="error", error=str(e))
        return
    except Exception as e:
        log.exception("argus analyze unexpected failure for gallery %s", gallery_id)
        _record(gallery_id, status="error", error=str(e)[:500])
        return

    run_id = result.get("run_id")
    job_id = result.get("job_id")
    if job_id:
        status = "queued"
    elif run_id:
        status = "done"
    else:
        status = "error"
        _record(gallery_id, status=status, error="missing run_id and job_id")
        return
    _record(gallery_id, status=status, run_id=run_id, job_id=job_id)