"""Sign-in providers.

Two implementations behind one protocol:

* :class:`DevAuthProvider` — deterministic, no network, no credentials. This is
  what local development, CI and the public demo use, which is why the whole
  test suite and the end-to-end flow need no OAuth application. It **refuses to
  load in a deployed environment**.
* :class:`GitHubOAuthProvider` — the real thing, used in staging and production.

The domain never learns which one authenticated a user; it receives an
:class:`ExternalIdentity` either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

import httpx

from agentrail_core.errors import PlatformError, ValidationFailedError


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """A user as described by the identity provider."""

    provider: str
    #: The provider's stable identifier. Never the email — providers let users
    #: change that, and reusing it as a key allows account takeover.
    subject: str
    email: str
    display_name: str


class AuthProviderError(PlatformError):
    """The provider rejected the exchange or returned something unusable."""

    status_code = 401


class AuthProvider(Protocol):
    name: str

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        """Where to send the browser to begin sign-in."""
        raise NotImplementedError

    async def exchange(self, *, code: str, redirect_uri: str) -> ExternalIdentity:
        """Turn a callback code into a verified identity."""
        raise NotImplementedError


class DevAuthProvider:
    """Deterministic sign-in for local development, CI and the demo.

    The "code" is simply an email address. This is safe only because it is
    unavailable in deployed environments — enforced in :func:`build_auth_provider`
    and asserted by a test.
    """

    name = "dev"

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        query = urlencode({"state": state, "redirect_uri": redirect_uri})
        return f"/auth/dev?{query}"

    async def exchange(self, *, code: str, redirect_uri: str) -> ExternalIdentity:
        del redirect_uri
        email = code.strip().lower()
        if not _looks_like_email(email):
            raise ValidationFailedError(
                "Provide a valid email address to sign in.", details={"field": "email"}
            )
        local_part = email.split("@", 1)[0]
        return ExternalIdentity(
            provider=self.name,
            # Deterministic: the same email always maps to the same account.
            subject=f"dev:{email}",
            email=email,
            display_name=local_part.replace(".", " ").replace("_", " ").title(),
        )


class GitHubOAuthProvider:
    """GitHub OAuth. Used in deployed environments."""

    name = "github"
    AUTHORIZE_ENDPOINT = "https://github.com/login/oauth/authorize"
    # A public URL, not a credential — the rule fires on the name containing "token".
    TOKEN_ENDPOINT = "https://github.com/login/oauth/access_token"  # noqa: S105
    USER_ENDPOINT = "https://api.github.com/user"
    EMAILS_ENDPOINT = "https://api.github.com/user/emails"

    def __init__(
        self, *, client_id: str, client_secret: str, timeout_seconds: float = 10.0
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout_seconds

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "scope": "read:user user:email",
                "state": state,
                "allow_signup": "false",
            }
        )
        return f"{self.AUTHORIZE_ENDPOINT}?{query}"

    async def exchange(self, *, code: str, redirect_uri: str) -> ExternalIdentity:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await self._exchange_code(client, code=code, redirect_uri=redirect_uri)
            profile = await self._get_json(client, self.USER_ENDPOINT, token)
            email = profile.get("email") or await self._primary_verified_email(client, token)

        subject = profile.get("id")
        if subject is None or not email:
            raise AuthProviderError("GitHub did not return a usable identity.")

        return ExternalIdentity(
            provider=self.name,
            subject=str(subject),
            email=str(email).lower(),
            display_name=str(profile.get("name") or profile.get("login") or email),
        )

    async def _exchange_code(
        self, client: httpx.AsyncClient, *, code: str, redirect_uri: str
    ) -> str:
        try:
            response = await client.post(
                self.TOKEN_ENDPOINT,
                headers={"accept": "application/json"},
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AuthProviderError("Could not reach GitHub to complete sign-in.") from exc

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            # The error body can contain the code; never propagate it.
            raise AuthProviderError("GitHub rejected the sign-in attempt.")
        return str(token)

    async def _get_json(self, client: httpx.AsyncClient, url: str, token: str) -> dict[str, object]:
        try:
            response = await client.get(
                url,
                headers={
                    "authorization": f"Bearer {token}",
                    "accept": "application/vnd.github+json",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AuthProviderError("Could not read the GitHub profile.") from exc
        payload: dict[str, object] = response.json()
        return payload

    async def _primary_verified_email(self, client: httpx.AsyncClient, token: str) -> str | None:
        try:
            response = await client.get(
                self.EMAILS_ENDPOINT,
                headers={
                    "authorization": f"Bearer {token}",
                    "accept": "application/vnd.github+json",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        for entry in response.json():
            # An unverified address must never be trusted: it would allow
            # claiming an account by asserting somebody else's email.
            if entry.get("primary") and entry.get("verified"):
                return str(entry["email"])
        return None


def _looks_like_email(value: str) -> bool:
    """Bounded validation for the dev provider's deterministic identifier."""

    if len(value) > 320 or any(character.isspace() for character in value):
        return False
    if value.count("@") != 1:
        return False

    local_part, domain = value.split("@", 1)
    if not local_part or not domain or "." not in domain:
        return False

    labels = domain.split(".")
    return all(labels)
