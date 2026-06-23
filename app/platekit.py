"""Read-only client for Platekit/Dionysus content packs.

Mise stays the photography operating system; Platekit owns campaign-pack
generation and approval. This bridge only reads approved/exported packs for a
client-like organization slug and degrades to an empty admin panel when disabled
or unreachable.
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger("mise.platekit")

_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def is_enabled() -> bool:
    return bool(config.PLATEKIT_API_BASE and config.PLATEKIT_API_TOKEN)


def normalize_slug(value: str) -> str:
    return _SLUG_CHARS.sub("-", (value or "").strip().lower()).strip("-")


def slug_for_client(client) -> str:
    explicit = normalize_slug(client["platekit_slug"] or "") if "platekit_slug" in client.keys() else ""
    if explicit:
        return explicit
    base = (client["company"] or client["name"] or "").strip()
    return normalize_slug(base)


def signup_url(client) -> str:
    company = client["company"] or client["name"] or ""
    params = urllib.parse.urlencode({
        "company": company,
        "name": client["name"] or "",
        "email": client["email"] or "",
        "audience": "restaurant",
    })
    return f"https://platekit.kleephotography.com/?{params}#signup"


def _empty(*, slug: str, status: str, message: str, enabled: bool | None = None) -> dict:
    return {
        "enabled": is_enabled() if enabled is None else enabled,
        "slug": slug,
        "status": status,
        "message": message,
        "packs": [],
        "signup_url": "",
    }


def packs_for_client(client, *, include_drafts: bool = False) -> dict:
    slug = slug_for_client(client)
    if not is_enabled():
        state = _empty(slug=slug, status="not_configured",
                       message="Platekit bridge is not configured", enabled=False)
        state["signup_url"] = signup_url(client)
        return state
    if not slug:
        state = _empty(slug=slug, status="missing_slug",
                       message="Client does not have a usable Platekit slug")
        state["signup_url"] = signup_url(client)
        return state

    base = config.PLATEKIT_API_BASE.rstrip("/")
    qs = urllib.parse.urlencode({"include_drafts": "true"}) if include_drafts else ""
    url = f"{base}/api/mise/organizations/{urllib.parse.quote(slug)}/packs"
    if qs:
        url = f"{url}?{qs}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {config.PLATEKIT_API_TOKEN}",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=config.PLATEKIT_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            state = _empty(slug=slug, status="not_found",
                           message="No matching Platekit organization")
            state["signup_url"] = signup_url(client)
            return state
        log.warning("Platekit returned HTTP %s for slug=%s", e.code, slug)
        state = _empty(slug=slug, status="error",
                       message=f"Platekit returned HTTP {e.code}")
        state["signup_url"] = signup_url(client)
        return state
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("Platekit unreachable for slug=%s: %s", slug, e)
        state = _empty(slug=slug, status="error",
                       message="Platekit is unreachable")
        state["signup_url"] = signup_url(client)
        return state
    except (ValueError, json.JSONDecodeError):
        state = _empty(slug=slug, status="error",
                       message="Platekit returned an unreadable response")
        state["signup_url"] = signup_url(client)
        return state

    return {
        "enabled": True,
        "slug": slug,
        "status": "ok",
        "message": "",
        "packs": payload.get("packs") or [],
        "signup_url": signup_url(client),
    }
