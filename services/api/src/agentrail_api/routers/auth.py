"""Sign-in, sign-out and identity of the current caller."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, EmailStr

from agentrail_api.auth import service as auth_service
from agentrail_api.auth.service import SESSION_COOKIE_NAME
from agentrail_api.dependencies import ActorDep, SessionDep, SettingsDep, build_auth_provider
from agentrail_api.identity import service as identity_service
from agentrail_api.identity.schemas import (
    MeResponse,
    OrganisationMembershipResponse,
    OrganisationResponse,
    UserResponse,
)
from agentrail_api.settings import ApiSettings
from agentrail_core.errors import ProblemDetail, UnauthenticatedError, ValidationFailedError
from agentrail_core.identity import generate_oauth_state
from agentrail_core.logging import get_logger

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
logger = get_logger(__name__)

OAUTH_STATE_COOKIE = "agentrail_oauth_state"
_OAUTH_STATE_TTL_SECONDS = 600


class AuthProviderInfo(BaseModel):
    name: str
    label: str
    #: True when the provider needs no credentials and no network — local, CI
    #: and the public demo.
    deterministic: bool


class ProvidersResponse(BaseModel):
    providers: list[AuthProviderInfo]


class DevSignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


def _set_session_cookie(response: Response, token: str, settings: ApiSettings) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,  # unreadable from JavaScript, so XSS cannot exfiltrate it
        secure=settings.cookies_are_secure,
        samesite="lax",  # blocks cross-site POSTs from carrying the session
        path="/",
    )


@router.get("/providers", response_model=ProvidersResponse, summary="List sign-in providers")
async def list_providers(settings: SettingsDep) -> ProvidersResponse:
    providers: list[AuthProviderInfo] = []
    if settings.dev_auth_enabled:
        providers.append(
            AuthProviderInfo(name="dev", label="Continue with email", deterministic=True)
        )
    if settings.github_oauth_configured:
        providers.append(
            AuthProviderInfo(name="github", label="Continue with GitHub", deterministic=False)
        )
    return ProvidersResponse(providers=providers)


@router.post(
    "/dev/session",
    response_model=MeResponse,
    summary="Sign in without a provider (non-deployed environments only)",
    responses={404: {"model": ProblemDetail, "description": "Disabled in this environment."}},
)
async def dev_sign_in(
    body: DevSignInRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> MeResponse:
    """Deterministic sign-in used by local development, CI and the demo.

    Unavailable in staging and production: :meth:`ApiSettings.dev_auth_enabled`
    is false there, and this route reports 404 rather than 403 so its existence
    is not advertised.
    """
    if not settings.dev_auth_enabled:
        raise ValidationFailedError("Unknown sign-in method.")

    provider = build_auth_provider(settings, "dev")
    identity = await provider.exchange(code=body.email, redirect_uri=settings.web_base_url)

    user = await auth_service.upsert_user(session, identity)
    _, token = await auth_service.create_session(
        session, user, ttl_seconds=settings.session_ttl_seconds
    )
    await session.commit()

    _set_session_cookie(response, token, settings)
    logger.info("sign_in", extra={"provider": "dev", "user_id": user.id})

    memberships = await identity_service.list_organisations_for_actor(
        session, auth_service.Actor(user=user)
    )
    return MeResponse(
        user=UserResponse.model_validate(user),
        principal_kind="user",
        organisations=[
            OrganisationMembershipResponse(
                organisation=OrganisationResponse.model_validate(item.organisation),
                role=item.role,
            )
            for item in memberships
        ],
    )


@router.get("/github/authorize", summary="Begin GitHub sign-in")
async def github_authorize(request: Request, settings: SettingsDep) -> RedirectResponse:
    if not settings.github_oauth_configured:
        raise ValidationFailedError("GitHub sign-in is not configured.")

    provider = build_auth_provider(settings, "github")
    state = generate_oauth_state()
    redirect_uri = str(request.url_for("github_callback"))

    response = RedirectResponse(
        provider.authorize_url(state=state, redirect_uri=redirect_uri),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    # The state is echoed back by GitHub and compared against this cookie, which
    # is what stops an attacker replaying their own callback into your session.
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=_OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.cookies_are_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/github/callback", name="github_callback", summary="Complete GitHub sign-in")
async def github_callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    code: Annotated[str, Query(min_length=1, max_length=512)],
    state: Annotated[str, Query(min_length=1, max_length=512)],
) -> RedirectResponse:
    expected = request.cookies.get(OAUTH_STATE_COOKIE)
    if not expected or expected != state:
        raise UnauthenticatedError("Sign-in could not be verified. Start again.")

    provider = build_auth_provider(settings, "github")
    identity = await provider.exchange(
        code=code, redirect_uri=str(request.url_for("github_callback"))
    )

    user = await auth_service.upsert_user(session, identity)
    _, token = await auth_service.create_session(
        session, user, ttl_seconds=settings.session_ttl_seconds
    )
    await session.commit()

    logger.info("sign_in", extra={"provider": "github", "user_id": user.id})
    response = RedirectResponse(
        settings.web_base_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    _set_session_cookie(response, token, settings)
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    return response


class SignOutResponse(BaseModel):
    status: Literal["signed_out"] = "signed_out"


@router.post("/signout", response_model=SignOutResponse, summary="Sign out")
async def sign_out(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> SignOutResponse:
    """Revoke the session server-side, not just in the browser.

    Clearing the cookie alone would leave a token that still authenticates if it
    was captured.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await auth_service.revoke_session(session, token)
        await session.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=settings.cookies_are_secure)
    return SignOutResponse()


@router.get(
    "/me",
    response_model=MeResponse,
    summary="The current caller and their organisations",
    responses={401: {"model": ProblemDetail, "description": "Not signed in."}},
)
async def read_me(actor: ActorDep, session: SessionDep) -> MeResponse:
    memberships = await identity_service.list_organisations_for_actor(session, actor)
    return MeResponse(
        user=UserResponse.model_validate(actor.user) if actor.user else None,
        principal_kind="user" if actor.is_user else "api_key",
        organisations=[
            OrganisationMembershipResponse(
                organisation=OrganisationResponse.model_validate(item.organisation),
                role=item.role,
            )
            for item in memberships
        ],
    )
