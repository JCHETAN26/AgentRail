"""Public contracts for identity and tenancy."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from agentrail_core.identity import Permission, Role


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    created_at: datetime


class OrganisationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    created_at: datetime


class OrganisationMembershipResponse(BaseModel):
    organisation: OrganisationResponse
    role: Role = Field(description="The caller's role in this organisation.")


class MeResponse(BaseModel):
    """Everything the console needs to render its shell after sign-in."""

    user: UserResponse | None = Field(
        default=None, description="Null when the caller is a service account."
    )
    principal_kind: str
    organisations: list[OrganisationMembershipResponse]


class CreateOrganisationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class MemberResponse(BaseModel):
    user: UserResponse
    role: Role
    created_at: datetime


class AddMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Role


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    name: str
    slug: str
    created_at: datetime


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class ApiKeyResponse(BaseModel):
    """An API key as listed. Never includes the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    key_id: str = Field(description="Public identifier. Not a credential.")
    name: str
    role: Role
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None


class CreatedApiKeyResponse(BaseModel):
    """Returned once, at creation.

    ``token`` is the only time the full credential exists anywhere; the platform
    stores only its SHA-256 digest and cannot show it again.
    """

    key: ApiKeyResponse
    token: str = Field(description="Copy this now. It cannot be retrieved later.")


class CreateApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    role: Role = Field(default=Role.DEVELOPER, description="Cannot exceed your own role.")
    scopes: list[Permission] = Field(
        default_factory=list,
        description="Optional narrowing. Empty means the role's full permissions.",
    )
    expires_at: datetime | None = None


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    correlation_id: str | None
    created_at: datetime
