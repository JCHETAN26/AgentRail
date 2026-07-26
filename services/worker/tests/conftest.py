from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from worker_test_support import WORKER_ID, JobFactory

from agentrail_cloudops_sandbox.app import SandboxSettings, create_app
from agentrail_core.identity import Organisation, Project
from agentrail_core.ids import new_sortable_id
from agentrail_core.jobs import TERMINAL_STATES, Job, JobState
from agentrail_worker.runner import JobRunner
from agentrail_worker.sandbox_client import SandboxClient


@pytest.fixture
async def sandbox() -> AsyncIterator[SandboxClient]:
    """A client wired to the real sandbox application over in-process ASGI.

    Not a mock: the actual FastAPI app, the actual deterministic task. Only the
    socket is elided.
    """
    app = create_app(SandboxSettings(_env_file=None, environment="test"))
    client = SandboxClient(
        "http://sandbox", timeout_seconds=5.0, transport=httpx.ASGITransport(app=app)
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def unreachable_sandbox() -> AsyncIterator[SandboxClient]:
    # Port 1 never has a listener, so the connection is refused immediately.
    client = SandboxClient("http://127.0.0.1:1", timeout_seconds=1.0)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def runner(session_factory: async_sessionmaker[AsyncSession], sandbox: SandboxClient) -> JobRunner:
    return JobRunner(session_factory, sandbox, worker_id=WORKER_ID)


@pytest.fixture
async def project_id(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """An organisation and project for jobs to belong to.

    The worker never performs an authorisation check — it executes work that the
    API already authorised — but every job still needs a tenant, so the tests
    create a real one rather than a placeholder identifier.
    """
    organisation = Organisation(id=new_sortable_id(), name="Worker Tests", slug="worker-tests")
    project = Project(
        id=new_sortable_id(),
        organisation_id=organisation.id,
        name="Default",
        slug="default",
    )
    async with session_factory() as session:
        session.add(organisation)
        session.add(project)
        await session.commit()
    return project.id


@pytest.fixture
def make_job(session_factory: async_sessionmaker[AsyncSession], project_id: str) -> JobFactory:
    """Insert a job row directly, bypassing the API."""

    async def _make_job(
        *,
        message: str = "hello",
        kind: str = "noop",
        state: JobState = JobState.PENDING,
        created_at: datetime | None = None,
    ) -> str:
        job = Job(
            id=new_sortable_id(),
            project_id=project_id,
            kind=kind,
            state=state,
            correlation_id="cid_test",
            trace_id="a" * 32,
            payload={"message": message},
            attempts=0,
            version=1,
            # The check constraint requires terminal states to have a completion time.
            completed_at=datetime.now(UTC) if state in TERMINAL_STATES else None,
        )
        if created_at is not None:
            job.created_at = created_at
        async with session_factory() as session:
            session.add(job)
            await session.commit()
        return job.id

    return _make_job
