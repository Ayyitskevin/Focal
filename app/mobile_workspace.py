"""Host-derived workspace metadata shared by the native API routers.

The request host and the already-selected SaaS tenant are the only workspace
selectors.  Keeping this derivation in one module prevents a device endpoint
from accepting a tenant identifier or drifting from the cache namespace issued
during authentication.
"""

from __future__ import annotations

import hashlib

from fastapi import Request

from . import config, mobile_auth, saas, urls


def request_origin(request: Request) -> str:
    origin = urls.origin_from_url(urls.request_origin(request))
    if origin is None:
        raise mobile_auth.MobileAuthError(
            400,
            "request.invalid_origin",
            "A valid request host is required.",
        )
    return origin


def tenant_metadata(request: Request, *, canonical: bool) -> dict:
    """Return safe workspace metadata without consulting request data fields."""

    incoming_origin = request_origin(request)
    if config.SAAS_MODE:
        tenant = saas.current_tenant()
        if not tenant or tenant.get("deleted_at"):
            raise mobile_auth.MobileAuthError(
                404,
                "tenant.not_found",
                "This studio is unavailable.",
            )
        origin = incoming_origin
        stable_identity = f"hosted\0{int(tenant['id'])}\0{origin}"
        return {
            "cache_namespace": "workspace_"
            + hashlib.sha256(stable_identity.encode()).hexdigest()[:24],
            "slug": tenant["slug"],
            "display_name": tenant["studio_name"],
            "origin": origin,
            "brand_accent_hex": (tenant.get("brand_accent") or "#2F5C45").upper(),
            "owner_email": tenant.get("owner_email"),
            "studio_password": True,
        }

    configured_origin = urls.origin_from_url(config.BASE_URL)
    origin = configured_origin if canonical and configured_origin else incoming_origin
    stable_identity = f"self-hosted\0{origin}"
    return {
        "cache_namespace": "workspace_" + hashlib.sha256(stable_identity.encode()).hexdigest()[:24],
        "slug": None,
        "display_name": config.SITE_NAME,
        "origin": origin,
        "brand_accent_hex": "#2F5C45",
        # Self-hosted Mise has a password principal, not a durable owner account;
        # a configured mail sender must not be presented as authenticated identity.
        "owner_email": None,
        "studio_password": bool(config.ADMIN_PASSWORD),
    }
