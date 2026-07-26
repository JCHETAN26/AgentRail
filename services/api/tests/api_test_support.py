"""Shared helpers for the API test suite.

Uniquely qualified module name (rather than a relative import from ``conftest``)
because pytest collects these directories without a package root — the same
reason ``worker_test_support`` exists.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import FastAPI

#: Port 1 is reserved and never has a listener, so connections fail immediately
#: rather than hanging or — worse — reaching a developer's running stack.
UNREACHABLE = 1


@dataclass
class Tenant:
    """A signed-in user with their own organisation and project.

    Two of these, created independently, are what the cross-tenant tests use to
    prove isolation.
    """

    client: httpx.AsyncClient
    email: str
    user_id: str
    organisation_id: str
    project_id: str


async def sign_in(app: FastAPI, email: str) -> httpx.AsyncClient:
    """Sign in through the real dev provider and keep the session cookie."""
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://api")
    response = await client.post("/api/v1/auth/dev/session", json={"email": email})
    assert response.status_code == 200, response.text
    return client


async def provision_tenant(app: FastAPI, email: str, organisation_name: str) -> Tenant:
    """Create a fully independent tenant through the public API only."""
    client = await sign_in(app, email)

    me = (await client.get("/api/v1/auth/me")).json()
    organisation = await client.post("/api/v1/organisations", json={"name": organisation_name})
    assert organisation.status_code == 201, organisation.text
    organisation_id = organisation.json()["id"]

    projects = await client.get(f"/api/v1/organisations/{organisation_id}/projects")
    project_id = projects.json()["items"][0]["id"]

    return Tenant(
        client=client,
        email=email,
        user_id=me["user"]["id"],
        organisation_id=organisation_id,
        project_id=project_id,
    )
