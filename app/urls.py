"""Public URL helpers that understand hosted tenant origins."""

from urllib.parse import urlsplit

from fastapi import Request

from . import config


def _request_origin(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


def public_base_url(request: Request | None = None) -> str:
    if config.SAAS_MODE:
        if request is not None:
            return _request_origin(request)
        from . import saas

        tenant = saas.current_tenant()
        if tenant:
            return saas.tenant_url(tenant["slug"], "").rstrip("/")
        return saas.platform_url("").rstrip("/")
    return config.BASE_URL.rstrip("/")


def origin_from_url(value: str) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return None
    netloc = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
    return f"{parsed.scheme}://{netloc}".lower()
