"""Typed wire contracts for the Mise mobile API v1 authentication slice.

These models deliberately do not mirror SQLite rows.  In particular, tenant selection
continues to come from the request host, and none of the public response types has a field
for a PIN, credential hash, refresh-token hash, or server filesystem path.

The project currently installs Pydantic 2 through FastAPI. These models inherit from the
installed top-level ``BaseModel`` because FastAPI 0.138 rejects models imported from
Pydantic 2's ``pydantic.v1`` compatibility namespace.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    constr,
    field_validator,
    model_validator,
)

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")

OpaqueID = constr(strip_whitespace=True, min_length=1, max_length=255)
DisplayName = constr(strip_whitespace=True, min_length=1, max_length=200)
BoundedString = constr(strip_whitespace=True, min_length=1, max_length=255)
LongBoundedString = constr(strip_whitespace=True, min_length=1, max_length=4096)


def _aware_utc(value: datetime | None) -> datetime | None:
    """Reject ambiguous instants and normalize every accepted offset to UTC."""

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include an RFC 3339 UTC offset")
    return value.astimezone(UTC)


def _unique_strings(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("values must be unique")
    return values


def _valid_brand_accent(value: str | None) -> str | None:
    if value is not None and not _HEX_COLOR.fullmatch(value):
        raise ValueError("brand_accent_hex must be a six-digit CSS hex color")
    return value


def _valid_time_zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("time_zone must be an IANA time zone") from exc
    return value


def _valid_currency_code(value: str) -> str:
    value = value.upper()
    if not _CURRENCY_CODE.fullmatch(value):
        raise ValueError("currency_code must be a three-letter ISO 4217 code")
    return value


class MobileAPIModel(BaseModel):
    """Strict base for request/response DTOs published in the mobile OpenAPI schema."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class PrincipalKind(StrEnum):
    STUDIO_OWNER = "studio_owner"
    GALLERY_GUEST = "gallery_guest"
    PORTAL_GUEST = "portal_guest"
    WORKSPACE_GUEST = "workspace_guest"
    DOCUMENT_GUEST = "document_guest"


class SharedAccessKind(StrEnum):
    GALLERY = "gallery"
    PORTAL = "portal"
    WORKSPACE = "workspace"
    PROPOSAL = "proposal"
    CONTRACT = "contract"
    INVOICE = "invoice"


class TenantDescriptor(MobileAPIModel):
    """Public, non-enumerating descriptor returned by the tenant host."""

    cache_namespace: OpaqueID
    slug: BoundedString | None = None
    studio_name: DisplayName
    canonical_base_url: AnyHttpUrl
    brand_accent_hex: str | None = Field(default=None, max_length=7)
    time_zone: BoundedString
    currency_code: str = Field(..., min_length=3, max_length=3)
    auth_methods: list[BoundedString] = Field(default_factory=list, max_length=16)
    # Funnel links so the free companion app never hardcodes web-admin paths:
    # where a new studio signs up, and where THIS studio's owner manages the
    # hosted subscription (both open in the system browser, never in-app —
    # ADR 0070 keeps every purchase on the web). Null when self-hosted.
    signup_url: AnyHttpUrl | None = None
    manage_billing_url: AnyHttpUrl | None = None

    @field_validator("brand_accent_hex")
    @classmethod
    def valid_brand_accent(cls, value: str | None) -> str | None:
        return _valid_brand_accent(value)

    @field_validator("time_zone")
    @classmethod
    def valid_time_zone(cls, value: str) -> str:
        return _valid_time_zone(value)

    @field_validator("currency_code")
    @classmethod
    def valid_currency_code(cls, value: str) -> str:
        return _valid_currency_code(value)

    @field_validator("auth_methods")
    @classmethod
    def auth_methods_unique(cls, value: list[str]) -> list[str]:
        return _unique_strings(value)


class DeviceContext(MobileAPIModel):
    """Client-supplied device description used for session attribution.

    ``installation_id`` is an opaque client-generated UUID-like identifier.  It is useful
    for revocation and notification registration, but is never authentication or
    authorization evidence by itself and is stored server-side only as a hash.
    """

    installation_id: OpaqueID
    name: constr(strip_whitespace=True, min_length=1, max_length=120)
    platform: constr(strip_whitespace=True, min_length=1, max_length=32)
    app_version: constr(strip_whitespace=True, min_length=1, max_length=64)


class DeviceSummary(MobileAPIModel):
    """Safe device metadata displayed in the owner's session list."""

    name: constr(strip_whitespace=True, min_length=1, max_length=120) | None = None
    platform: constr(strip_whitespace=True, min_length=1, max_length=32) | None = None
    app_version: constr(strip_whitespace=True, min_length=1, max_length=64) | None = None


class StudioLoginRequest(MobileAPIModel):
    email: constr(strip_whitespace=True, min_length=3, max_length=320) | None = None
    password: SecretStr = Field(..., min_length=1, max_length=1024)
    device: DeviceContext

    @field_validator("email", mode="before")
    @classmethod
    def empty_email_is_absent(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class SharedAccessUnlockRequest(MobileAPIModel):
    kind: SharedAccessKind
    slug: BoundedString
    pin: SecretStr | None = None
    device: DeviceContext

    @field_validator("pin", mode="before")
    @classmethod
    def empty_pin_is_absent(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("pin")
    @classmethod
    def valid_pin(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value()
        if len(raw) != 4 or not raw.isdigit():
            raise ValueError("pin must contain exactly four digits")
        return value


class RefreshTokenRequest(MobileAPIModel):
    refresh_token: SecretStr = Field(..., min_length=1, max_length=4096)


class WorkspaceContext(MobileAPIModel):
    """Tenant-safe workspace identity embedded in every authenticated session."""

    cache_namespace: OpaqueID
    slug: BoundedString | None = None
    display_name: DisplayName
    api_base_url: AnyHttpUrl
    brand_accent_hex: str | None = Field(default=None, max_length=7)
    time_zone: BoundedString
    currency_code: str = Field(..., min_length=3, max_length=3)

    @field_validator("brand_accent_hex")
    @classmethod
    def valid_brand_accent(cls, value: str | None) -> str | None:
        return _valid_brand_accent(value)

    @field_validator("time_zone")
    @classmethod
    def valid_time_zone(cls, value: str) -> str:
        return _valid_time_zone(value)

    @field_validator("currency_code")
    @classmethod
    def valid_currency_code(cls, value: str) -> str:
        return _valid_currency_code(value)


class Principal(MobileAPIModel):
    id: OpaqueID
    kind: PrincipalKind
    display_name: DisplayName
    email: constr(strip_whitespace=True, min_length=3, max_length=320) | None = None
    scopes: list[BoundedString] = Field(default_factory=list, max_length=128)

    @field_validator("scopes")
    @classmethod
    def scopes_unique(cls, value: list[str]) -> list[str]:
        return _unique_strings(value)


class AuthSession(MobileAPIModel):
    """Token response decoded by the iOS ``AuthSession`` model.

    ``session_id`` is additive metadata for diagnostics/revocation.  The current Swift
    decoder safely ignores it; every field required by Swift remains required here.
    """

    access_token: LongBoundedString
    refresh_token: LongBoundedString | None = None
    token_type: Literal["Bearer"]
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime | None = None
    workspace: WorkspaceContext
    principal: Principal
    available_commands: list[BoundedString] = Field(max_length=128)
    session_id: OpaqueID | None = None

    @field_validator("available_commands")
    @classmethod
    def available_commands_unique(cls, value: list[str]) -> list[str]:
        return _unique_strings(value)

    @field_validator("access_token_expires_at", "refresh_token_expires_at")
    @classmethod
    def expiry_is_utc(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value)

    @model_validator(mode="after")
    def valid_expiry_order(self) -> AuthSession:
        access_expiry = self.access_token_expires_at
        refresh_expiry = self.refresh_token_expires_at
        if access_expiry is not None and refresh_expiry is not None:
            if refresh_expiry < access_expiry:
                raise ValueError("refresh token cannot expire before the access token")
        return self


class CurrentSession(MobileAPIModel):
    """The deliberately token-free response body for ``GET /api/v1/me``."""

    workspace: WorkspaceContext
    principal: Principal
    available_commands: list[BoundedString] = Field(max_length=128)

    @field_validator("available_commands")
    @classmethod
    def available_commands_unique(cls, value: list[str]) -> list[str]:
        return _unique_strings(value)


class SessionSummary(MobileAPIModel):
    """Revocable owner session shown by ``GET /api/v1/auth/sessions``."""

    id: OpaqueID
    device: DeviceSummary
    created_at: datetime
    last_seen_at: datetime | None = None
    expires_at: datetime
    is_current: bool = False
    revoked_at: datetime | None = None

    @field_validator("created_at", "last_seen_at", "expires_at", "revoked_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value)

    @model_validator(mode="after")
    def valid_session_times(self) -> SessionSummary:
        created_at = self.created_at
        expires_at = self.expires_at
        last_seen_at = self.last_seen_at
        revoked_at = self.revoked_at
        if created_at is not None and expires_at is not None and expires_at < created_at:
            raise ValueError("session cannot expire before it was created")
        if created_at is not None and last_seen_at is not None and last_seen_at < created_at:
            raise ValueError("last_seen_at cannot precede created_at")
        if created_at is not None and revoked_at is not None and revoked_at < created_at:
            raise ValueError("revoked_at cannot precede created_at")
        return self


class SessionListResponse(MobileAPIModel):
    sessions: list[SessionSummary] = Field(default_factory=list, max_length=500)


class FieldViolation(MobileAPIModel):
    path: list[str] = Field(default_factory=list, max_length=32)
    message: constr(strip_whitespace=True, min_length=1, max_length=2000)
    code: constr(strip_whitespace=True, min_length=1, max_length=255) | None = None


class FastAPIValidationItem(MobileAPIModel):
    """The stable subset of one FastAPI/Pydantic validation error item."""

    loc: list[str | int] = Field(default_factory=list, max_length=32)
    msg: constr(strip_whitespace=True, min_length=1, max_length=2000)
    type: constr(strip_whitespace=True, min_length=1, max_length=255) | None = None

    def as_field_violation(self) -> FieldViolation:
        return FieldViolation(
            path=[str(component) for component in self.loc], message=self.msg, code=self.type
        )


class APIProblem(MobileAPIModel):
    """RFC 9457-style problem details plus Mise's stable extension fields.

    ``type``, ``title``, ``status``, and ``detail`` are RFC 9457 members. ``code``,
    ``request_id``, and ``errors`` are application extensions consumed by the Swift client.
    Every field is optional so this also decodes the smaller problems FastAPI may produce.
    """

    type: constr(strip_whitespace=True, min_length=1, max_length=2048) | None = None
    title: constr(strip_whitespace=True, min_length=1, max_length=500) | None = None
    status: int | None = Field(default=None, ge=100, le=599)
    code: constr(strip_whitespace=True, min_length=1, max_length=255) | None = None
    detail: constr(strip_whitespace=True, min_length=1, max_length=4000) | None = None
    request_id: constr(strip_whitespace=True, min_length=1, max_length=255) | None = None
    errors: list[FieldViolation] = Field(default_factory=list, max_length=100)

    @classmethod
    def from_fastapi_validation(
        cls,
        detail: list[dict[str, Any]],
        *,
        request_id: str | None = None,
        status: int = 422,
    ) -> APIProblem:
        """Convert FastAPI's default validation detail into the published problem shape."""

        items = [FastAPIValidationItem.model_validate(item) for item in detail]
        return cls(
            type="https://mise.example/problems/validation",
            title="Request validation failed",
            status=status,
            code="request.validation_failed",
            detail="One or more fields are invalid.",
            request_id=request_id,
            errors=[item.as_field_violation() for item in items],
        )


# A descriptive alias for server code that prefers the RFC terminology.
ProblemDetails = APIProblem


__all__ = [
    "APIProblem",
    "AuthSession",
    "CurrentSession",
    "DeviceContext",
    "DeviceSummary",
    "FastAPIValidationItem",
    "FieldViolation",
    "MobileAPIModel",
    "Principal",
    "PrincipalKind",
    "ProblemDetails",
    "RefreshTokenRequest",
    "SessionListResponse",
    "SessionSummary",
    "SharedAccessKind",
    "SharedAccessUnlockRequest",
    "StudioLoginRequest",
    "TenantDescriptor",
    "ValidationError",
    "WorkspaceContext",
]
