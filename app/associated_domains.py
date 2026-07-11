"""Apple App Site Association document for native Mise links."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from . import config

router = APIRouter()

_TEAM_ID = re.compile(r"^[A-Z0-9]{10}$")
_TOPIC = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}))*$")
_SHARED_PREFIXES = ("g", "portal", "w", "p", "c", "i")


def _application_identifier() -> str | None:
    team_id = (getattr(config, "APNS_TEAM_ID", "") or "").strip()
    topic = (getattr(config, "APNS_TOPIC", "") or "").strip()
    if not _TEAM_ID.fullmatch(team_id) or not _TOPIC.fullmatch(topic):
        return None
    return f"{team_id}.{topic}"


def _components() -> list[dict[str, object]]:
    # Exclusions come first so document action URLs never become app links.
    shared: list[dict[str, object]] = []
    for prefix in _SHARED_PREFIXES:
        shared.extend(
            (
                {
                    "exclude": True,
                    "/": f"/{prefix}/*/*",
                    "comment": "Only the exact shared capability page is supported.",
                },
                {
                    "/": f"/{prefix}/*",
                    "comment": "Open a shared Mise capability.",
                },
            )
        )
    return [
        {
            "/": "/app/*",
            "comment": "Authenticated, typed native routes.",
        },
        *shared,
    ]


def document() -> dict:
    app_id = _application_identifier()
    if app_id is None:
        # A bogus application association is worse than no association: clients
        # would cache it and silently route links to the wrong signed app.
        raise HTTPException(status_code=404)
    return {
        "applinks": {
            "apps": [],
            "details": [
                {
                    "appIDs": [app_id],
                    "components": _components(),
                }
            ],
        }
    }


@router.get("/.well-known/apple-app-site-association", include_in_schema=False)
@router.get("/apple-app-site-association", include_in_schema=False)
async def apple_app_site_association() -> JSONResponse:
    return JSONResponse(
        document(),
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
        media_type="application/json",
    )
