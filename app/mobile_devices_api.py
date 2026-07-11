"""Owner-only APNs device registration for the native API.

Tenant, session, principal, origin, and workspace identity are all derived from
the authenticated request.  The wire contract never returns an installation
identifier, token, token hash, ciphertext, or database identifier.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import mobile_auth, push_notifications

_TOKEN_RE = re.compile(r"^[0-9a-fA-F]+$")
_IF_MATCH = "If-Match"

router = APIRouter()


class DeviceAPIModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


class NotificationPreferencePatch(DeviceAPIModel):
    new_bookings: bool | None = None
    booking_changes: bool | None = None
    proposal_responses: bool | None = None
    payments: bool | None = None

    @field_validator(
        "new_bookings",
        "booking_changes",
        "proposal_responses",
        "payments",
        mode="before",
    )
    @classmethod
    def supplied_preferences_are_not_null(cls, value):
        if value is None:
            raise ValueError("notification preferences cannot be null")
        return value

    @model_validator(mode="after")
    def has_a_change(self) -> NotificationPreferencePatch:
        if not self.as_mapping():
            raise ValueError("at least one notification preference is required")
        return self

    def as_mapping(self) -> dict[str, bool]:
        return self.model_dump(exclude_none=True)


class NotificationPreferences(DeviceAPIModel):
    new_bookings: bool
    booking_changes: bool
    proposal_responses: bool
    payments: bool


class DeviceRegistrationRequest(DeviceAPIModel):
    installation_id: str = Field(min_length=36, max_length=36)
    apns_token: str = Field(min_length=32, max_length=512)
    environment: Literal["sandbox", "production"]
    locale: str = Field(min_length=1, max_length=35)
    app_version: str = Field(min_length=1, max_length=64)
    preferences: NotificationPreferencePatch | None = None

    @field_validator("installation_id")
    @classmethod
    def valid_installation_id(cls, value: str) -> str:
        try:
            parsed = uuid.UUID(value)
        except ValueError as exc:
            raise ValueError("installation_id must be a UUID") from exc
        if str(parsed) != value.casefold():
            raise ValueError("installation_id must use canonical UUID syntax")
        return str(parsed)

    @field_validator("apns_token")
    @classmethod
    def valid_apns_token(cls, value: str) -> str:
        if len(value) % 2 or not _TOKEN_RE.fullmatch(value):
            raise ValueError("token must contain an even number of hexadecimal characters")
        return value.lower()

    @field_validator("locale")
    @classmethod
    def valid_locale(cls, value: str) -> str:
        if not all(character.isalnum() or character in "-_" for character in value):
            raise ValueError("locale contains unsupported characters")
        return value


class DeviceRegistrationResponse(DeviceAPIModel):
    environment: Literal["sandbox", "production"]
    locale: str
    app_version: str
    preferences: NotificationPreferences
    active: bool
    registered_at: dt.datetime
    updated_at: dt.datetime

    @field_validator("registered_at", "updated_at")
    @classmethod
    def timestamps_are_utc(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("device timestamps must include an offset")
        return value.astimezone(dt.UTC)


class DevicePreferenceRequest(DeviceAPIModel):
    preferences: NotificationPreferencePatch


def require_studio_reader(request: Request) -> mobile_auth.Principal:
    principal = mobile_auth.authenticate_request(
        request,
        required_scopes=("studio:read",),
    )
    if principal.kind != mobile_auth.STUDIO_OWNER:
        raise mobile_auth.MobileAuthError(
            403,
            "auth.insufficient_scope",
            "Studio owner access is required.",
        )
    return principal


StudioReader = Annotated[mobile_auth.Principal, Depends(require_studio_reader)]


def _push_call(call):
    try:
        return call()
    except push_notifications.PushNotificationError as exc:
        raise mobile_auth.MobileAuthError(
            exc.status_code,
            exc.code,
            exc.detail,
        ) from exc


def _timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _response(
    registration: push_notifications.DeviceRegistration,
) -> DeviceRegistrationResponse:
    return DeviceRegistrationResponse(
        environment=registration.environment,
        locale=registration.locale,
        app_version=registration.app_version,
        preferences=NotificationPreferences.model_validate(registration.preferences),
        active=registration.active,
        registered_at=_timestamp(registration.registered_at),
        updated_at=_timestamp(registration.updated_at),
    )


def _private(
    response: Response,
    registration: push_notifications.DeviceRegistration | None = None,
) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Authorization"
    if registration is not None:
        response.headers["ETag"] = push_notifications.device_etag(registration)


@router.post(
    "/devices",
    response_model=DeviceRegistrationResponse,
    tags=["notifications"],
)
def register_device(
    request: Request,
    response: Response,
    body: DeviceRegistrationRequest,
    principal: StudioReader,
) -> DeviceRegistrationResponse:
    registration = _push_call(
        lambda: push_notifications.upsert_owner_device(
            request,
            principal,
            installation_id=body.installation_id,
            token=body.apns_token,
            environment=body.environment,
            locale=body.locale,
            app_version=body.app_version,
            preferences=body.preferences.as_mapping() if body.preferences else None,
        )
    )
    _private(response, registration)
    return _response(registration)


@router.get(
    "/devices/current",
    response_model=DeviceRegistrationResponse,
    tags=["notifications"],
)
def current_device(
    response: Response,
    principal: StudioReader,
) -> DeviceRegistrationResponse:
    registration = _push_call(lambda: push_notifications.current_device(principal))
    if registration is None:
        raise mobile_auth.MobileAuthError(
            404,
            "device.not_found",
            "No notification registration exists for this device.",
        )
    _private(response, registration)
    return _response(registration)


@router.patch(
    "/devices/current",
    response_model=DeviceRegistrationResponse,
    tags=["notifications"],
)
def update_device_preferences(
    request: Request,
    response: Response,
    body: DevicePreferenceRequest,
    principal: StudioReader,
) -> DeviceRegistrationResponse:
    if_match = request.headers.get(_IF_MATCH, "").strip()
    if not if_match:
        raise mobile_auth.MobileAuthError(
            422,
            "resource.if_match_required",
            "Reload notification preferences before saving changes.",
        )
    registration = _push_call(
        lambda: push_notifications.update_current_preferences(
            principal,
            body.preferences.as_mapping(),
            if_match=if_match,
        )
    )
    _private(response, registration)
    return _response(registration)


@router.delete(
    "/devices/current",
    status_code=204,
    tags=["notifications"],
)
def delete_current_device(principal: StudioReader) -> Response:
    # Intentionally idempotent: deletion must not reveal whether a token still
    # exists, and local logout must be able to retry it safely.
    _push_call(lambda: push_notifications.deactivate_current(principal))
    return Response(
        status_code=204,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Vary": "Authorization",
        },
    )
