"""Versioned JSON boundary for Mise's native clients.

The mounted application publishes only the mobile contract and therefore gets a
small, scoped OpenAPI document. Tenant selection remains the responsibility of
the parent host middleware; no route accepts a tenant id, slug, or database path
as authority.
"""

from __future__ import annotations

import hashlib
import logging
from enum import Enum

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import (
    alerts,
    config,
    mobile_auth,
    mobile_client_api,
    mobile_gallery_calendar_api,
    mobile_media,
    mobile_owner_api,
    saas,
    urls,
)
from .mobile_api_schemas import (
    APIProblem,
    AuthSession,
    CurrentSession,
    DeviceSummary,
    Principal,
    RefreshTokenRequest,
    SessionListResponse,
    SessionSummary,
    SharedAccessUnlockRequest,
    StudioLoginRequest,
    TenantDescriptor,
    WorkspaceContext,
)

log = logging.getLogger("mise.mobile_api")

app = FastAPI(
    title="Mise Mobile API",
    summary="Native companion API for studio owners and scoped client guests.",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    servers=[{"url": "/api/v1"}],
)

_PROBLEM_RESPONSES = {
    401: {"model": APIProblem, "description": "Authentication failed"},
    403: {"model": APIProblem, "description": "Insufficient scope"},
    404: {"model": APIProblem, "description": "Resource not found"},
    410: {"model": APIProblem, "description": "Capability expired"},
    422: {"model": APIProblem, "description": "Request validation failed"},
    429: {"model": APIProblem, "description": "Rate limited"},
}


@app.middleware("http")
async def contain_unhandled_errors(request: Request, call_next):
    """Keep mounted-API failures inside its JSON boundary without logging values.

    FastAPI's generic 500 handler re-raises after sending its response so test
    clients can observe the exception. Across an ASGI mount that would also reach
    the parent logger, where a validation exception can include response inputs
    (including newly issued tokens). Catching at the sub-app middleware boundary
    preserves the problem contract and records only safe request/type metadata.
    """

    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001 - this is the API's final containment boundary
        request_id = getattr(request.state, "request_id", None)
        log.error(
            "unhandled mobile API error: %s %s type=%s request_id=%s",
            request.method,
            request.url.path,
            type(exc).__name__,
            request_id,
        )
        alerts.error_alert(
            f"{request.method} {request.url.path}|{type(exc).__name__}",
            f"{type(exc).__name__} on {request.method} {request.url.path} request_id={request_id}",
        )
        return _problem_response(
            request,
            status=500,
            code="server.internal",
            detail="The server could not complete the request.",
        )


def _problem_type(code: str) -> str:
    return f"https://mise.example/problems/{code.replace('.', '-').replace('_', '-')}"


def _problem_title(status: int) -> str:
    return {
        400: "Invalid request",
        401: "Authentication failed",
        402: "Studio unavailable",
        403: "Access denied",
        404: "Not found",
        405: "Method not allowed",
        409: "Conflict",
        410: "Access expired",
        422: "Request validation failed",
        429: "Too many requests",
        500: "Internal server error",
    }.get(status, "Request failed")


def _problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    detail: str,
    errors: list | None = None,
    retry_after: int | None = None,
) -> JSONResponse:
    problem = APIProblem(
        type=_problem_type(code),
        title=_problem_title(status),
        status=status,
        code=code,
        detail=detail,
        request_id=getattr(request.state, "request_id", None),
        errors=errors or [],
    )
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if retry_after is not None:
        headers["Retry-After"] = str(max(1, retry_after))
    return JSONResponse(
        jsonable_encoder(problem, exclude_none=True),
        status_code=status,
        headers=headers,
        media_type="application/problem+json",
    )


def mount_root_not_found(request: Request) -> JSONResponse:
    """Avoid Starlette's automatic `/api/v1` -> `/api/v1/` redirect."""

    return _problem_response(
        request,
        status=404,
        code="request.not_found",
        detail="API route not found.",
    )


@app.exception_handler(mobile_auth.MobileAuthError)
async def mobile_auth_error(request: Request, exc: mobile_auth.MobileAuthError) -> JSONResponse:
    return _problem_response(
        request,
        status=exc.status_code,
        code=exc.code,
        detail=exc.detail,
        retry_after=exc.retry_after,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Keep Pydantic's `input` and `ctx` members out of the response: an invalid
    # password/PIN field can otherwise be reflected verbatim in validation JSON.
    safe_errors = [
        {
            "loc": list(error.get("loc", ())),
            "msg": str(error.get("msg") or "Invalid value"),
            "type": str(error.get("type") or "value_error"),
        }
        for error in exc.errors()
    ]
    problem = APIProblem.from_fastapi_validation(
        safe_errors,
        request_id=getattr(request.state, "request_id", None),
    )
    return JSONResponse(
        jsonable_encoder(problem, exclude_none=True),
        status_code=422,
        headers={"Cache-Control": "no-store"},
        media_type="application/problem+json",
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else _problem_title(exc.status_code)
    return _problem_response(
        request,
        status=exc.status_code,
        code=f"request.http_{exc.status_code}",
        detail=detail,
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    # Fallback for an exception raised outside the route middleware itself. Never
    # interpolate or traceback-log `exc`: response-validation inputs may be secret.
    log.error(
        "unhandled mobile API boundary error: %s %s type=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    return _problem_response(
        request,
        status=500,
        code="server.internal",
        detail="The server could not complete the request.",
    )


def _enum_value(value: str | Enum) -> str:
    return str(value.value) if isinstance(value, Enum) else str(value)


def _request_origin(request: Request) -> str:
    origin = urls.origin_from_url(urls.request_origin(request))
    if origin is None:
        raise mobile_auth.MobileAuthError(
            400,
            "request.invalid_origin",
            "A valid request host is required.",
        )
    return origin


def _tenant_metadata(request: Request, *, canonical: bool) -> dict:
    request_origin = _request_origin(request)
    if config.SAAS_MODE:
        tenant = saas.current_tenant()
        if not tenant or tenant.get("deleted_at"):
            raise mobile_auth.MobileAuthError(
                404,
                "tenant.not_found",
                "This studio is unavailable.",
            )
        origin = request_origin
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
    origin = configured_origin if canonical and configured_origin else request_origin
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


def _tenant_descriptor(request: Request) -> TenantDescriptor:
    metadata = _tenant_metadata(request, canonical=True)
    auth_methods = ["shared_access"]
    if metadata["studio_password"]:
        auth_methods.insert(0, "studio_password")
    return TenantDescriptor(
        cache_namespace=metadata["cache_namespace"],
        slug=metadata["slug"],
        studio_name=metadata["display_name"],
        canonical_base_url=metadata["origin"],
        brand_accent_hex=metadata["brand_accent_hex"],
        time_zone=config.TIMEZONE,
        currency_code="USD",
        auth_methods=auth_methods,
    )


def _workspace_context(request: Request) -> WorkspaceContext:
    metadata = _tenant_metadata(request, canonical=False)
    return WorkspaceContext(
        cache_namespace=metadata["cache_namespace"],
        slug=metadata["slug"],
        display_name=metadata["display_name"],
        api_base_url=metadata["origin"],
        brand_accent_hex=metadata["brand_accent_hex"],
        time_zone=config.TIMEZONE,
        currency_code="USD",
    )


def _principal(request: Request, core: mobile_auth.Principal) -> Principal:
    metadata = _tenant_metadata(request, canonical=False)
    labels = {
        mobile_auth.GALLERY_GUEST: "Gallery access",
        mobile_auth.PORTAL_GUEST: "Client portal access",
        mobile_auth.WORKSPACE_GUEST: "Project workspace access",
        mobile_auth.DOCUMENT_GUEST: "Document access",
    }
    email = metadata["owner_email"] if core.kind == mobile_auth.STUDIO_OWNER else None
    return Principal(
        id=core.id,
        kind=core.kind,
        display_name=(
            metadata["display_name"]
            if core.kind == mobile_auth.STUDIO_OWNER
            else labels.get(core.kind, "Limited access")
        ),
        email=email,
        scopes=sorted(core.scopes),
    )


def _auth_session(request: Request, pair: mobile_auth.TokenPair) -> AuthSession:
    return AuthSession(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type="Bearer",
        access_token_expires_at=pair.access_expires_at,
        refresh_token_expires_at=pair.refresh_expires_at,
        workspace=_workspace_context(request),
        principal=_principal(request, pair.principal),
        session_id=pair.session_id,
    )


def _current_session(request: Request, core: mobile_auth.Principal) -> CurrentSession:
    return CurrentSession(
        workspace=_workspace_context(request),
        principal=_principal(request, core),
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _bearer_token(request: Request) -> str:
    parts = request.headers.get("authorization", "").split()
    if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1]:
        raise mobile_auth.MobileAuthError(
            401,
            "auth.invalid_token",
            "The token is invalid or expired.",
        )
    return parts[1]


def _authenticated(request: Request, *, scopes: tuple[str, ...] = ()) -> mobile_auth.Principal:
    return mobile_auth.authenticate_access(
        request,
        _bearer_token(request),
        required_scopes=scopes,
    )


def _device_kwargs(body: StudioLoginRequest | SharedAccessUnlockRequest) -> dict:
    return {
        "installation_id": body.device.installation_id,
        "device_name": body.device.name,
        "device_platform": body.device.platform,
        "device_app_version": body.device.app_version,
    }


@app.get(
    "/tenant",
    response_model=TenantDescriptor,
    responses={404: _PROBLEM_RESPONSES[404]},
    tags=["bootstrap"],
)
def tenant(request: Request, response: Response) -> TenantDescriptor:
    response.headers["Cache-Control"] = "public, max-age=300"
    return _tenant_descriptor(request)


@app.post(
    "/auth/studio/login",
    response_model=AuthSession,
    responses=_PROBLEM_RESPONSES,
    tags=["authentication"],
)
def studio_login(
    request: Request,
    response: Response,
    body: StudioLoginRequest,
) -> AuthSession:
    _no_store(response)
    pair = mobile_auth.issue_studio_owner_session(
        request,
        body.password.get_secret_value(),
        email=body.email,
        **_device_kwargs(body),
    )
    return _auth_session(request, pair)


@app.post(
    "/auth/refresh",
    response_model=AuthSession,
    responses=_PROBLEM_RESPONSES,
    tags=["authentication"],
)
def refresh(
    request: Request,
    response: Response,
    body: RefreshTokenRequest,
) -> AuthSession:
    _no_store(response)
    pair = mobile_auth.rotate_refresh(request, body.refresh_token.get_secret_value())
    return _auth_session(request, pair)


def _check_kind(body: SharedAccessUnlockRequest, expected: set[str]) -> str:
    kind = _enum_value(body.kind)
    if kind not in expected:
        raise mobile_auth.MobileAuthError(
            422,
            "request.kind_mismatch",
            "The shared-access kind does not match this endpoint.",
        )
    return kind


@app.post(
    "/client-auth/gallery/unlock",
    response_model=AuthSession,
    responses=_PROBLEM_RESPONSES,
    tags=["client authentication"],
)
def gallery_unlock(
    request: Request,
    response: Response,
    body: SharedAccessUnlockRequest,
) -> AuthSession:
    _no_store(response)
    _check_kind(body, {"gallery"})
    pair = mobile_auth.issue_gallery_session(
        request,
        body.slug,
        body.pin.get_secret_value() if body.pin else None,
        **_device_kwargs(body),
    )
    return _auth_session(request, pair)


@app.post(
    "/client-auth/portal/unlock",
    response_model=AuthSession,
    responses=_PROBLEM_RESPONSES,
    tags=["client authentication"],
)
def portal_unlock(
    request: Request,
    response: Response,
    body: SharedAccessUnlockRequest,
) -> AuthSession:
    _no_store(response)
    _check_kind(body, {"portal"})
    pair = mobile_auth.issue_portal_session(
        request,
        body.slug,
        body.pin.get_secret_value() if body.pin else "",
        **_device_kwargs(body),
    )
    return _auth_session(request, pair)


@app.post(
    "/client-auth/workspace/unlock",
    response_model=AuthSession,
    responses=_PROBLEM_RESPONSES,
    tags=["client authentication"],
)
def workspace_unlock(
    request: Request,
    response: Response,
    body: SharedAccessUnlockRequest,
) -> AuthSession:
    _no_store(response)
    _check_kind(body, {"workspace"})
    pair = mobile_auth.issue_workspace_session(
        request,
        body.slug,
        body.pin.get_secret_value() if body.pin else "",
        **_device_kwargs(body),
    )
    return _auth_session(request, pair)


@app.post(
    "/client-auth/document/exchange",
    response_model=AuthSession,
    responses=_PROBLEM_RESPONSES,
    tags=["client authentication"],
)
def document_exchange(
    request: Request,
    response: Response,
    body: SharedAccessUnlockRequest,
) -> AuthSession:
    _no_store(response)
    kind = _check_kind(body, {"proposal", "contract", "invoice"})
    pair = mobile_auth.issue_document_session(
        request,
        kind,
        body.slug,
        **_device_kwargs(body),
    )
    return _auth_session(request, pair)


@app.post(
    "/auth/logout",
    status_code=204,
    responses={401: _PROBLEM_RESPONSES[401]},
    tags=["authentication"],
)
def logout(request: Request) -> Response:
    # Invalid but syntactically valid tokens still receive an idempotent 204; this
    # avoids turning logout into a token-existence oracle.
    mobile_auth.logout(request, _bearer_token(request))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@app.get(
    "/me",
    response_model=CurrentSession,
    responses={401: _PROBLEM_RESPONSES[401]},
    tags=["authentication"],
)
def me(request: Request, response: Response) -> CurrentSession:
    _no_store(response)
    return _current_session(request, _authenticated(request))


@app.get(
    "/auth/sessions",
    response_model=SessionListResponse,
    responses={401: _PROBLEM_RESPONSES[401], 403: _PROBLEM_RESPONSES[403]},
    tags=["authentication"],
)
def sessions(request: Request, response: Response) -> SessionListResponse:
    _no_store(response)
    owner = _authenticated(request, scopes=("studio:read",))
    summaries = mobile_auth.list_sessions(request, owner)
    return SessionListResponse(
        sessions=[
            SessionSummary(
                id=item.session_id,
                device=DeviceSummary(
                    name=item.device_name,
                    platform=item.device_platform,
                    app_version=item.device_app_version,
                ),
                created_at=item.created_at,
                last_seen_at=item.last_seen_at,
                expires_at=item.absolute_expires_at,
                is_current=item.is_current,
                revoked_at=item.revoked_at,
            )
            for item in summaries
        ]
    )


@app.delete(
    "/auth/sessions/{session_id}",
    status_code=204,
    responses={
        401: _PROBLEM_RESPONSES[401],
        403: _PROBLEM_RESPONSES[403],
        404: _PROBLEM_RESPONSES[404],
    },
    tags=["authentication"],
)
def revoke_session(request: Request, session_id: str) -> Response:
    owner = _authenticated(request, scopes=("studio:read",))
    if not mobile_auth.revoke_session(request, owner, session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


# Feature routers stay independent of this module so authentication failures,
# validation problems, and unexpected errors still pass through this mounted
# application's single JSON/problem boundary.
app.include_router(mobile_owner_api.router)
app.include_router(mobile_gallery_calendar_api.router)
app.include_router(mobile_client_api.router)
app.include_router(mobile_media.router)
