"""Integration tests for job execution.

These run against real PostgreSQL and the real sandbox application. They are the
evidence for the Phase 0 reliability claim: at-least-once delivery cannot cause
a job to execute twice.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from worker_test_support import WORKER_ID, JobFactory

from agentrail_core.ids import new_sortable_id
from agentrail_core.jobs import Job, JobState
from agentrail_worker.runner import JobOutcome, JobRunner
from agentrail_worker.sandbox_client import SandboxClient

pytestmark = pytest.mark.integration


async def load(factory: async_sessionmaker[AsyncSession], job_id: str) -> Job:
    async with factory() as session:
        job = await session.get(Job, job_id)
    assert job is not None
    return job


class TestSuccessfulExecution:
    async def test_completes_a_pending_job(
        self,
        runner: JobRunner,
        make_job: JobFactory,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        job_id = await make_job(message="hello")

        outcome = await runner.process(job_id)

        assert outcome is JobOutcome.COMPLETED
        job = await load(session_factory, job_id)
        assert job.state == JobState.COMPLETED
        assert job.result == {
            "echo": "hello",
            "digest": "2cf24dba5fb0a30e",
            "sandbox_version": "0.1.0",
        }

    async def test_records_execution_metadata(
        self,
        runner: JobRunner,
        make_job: JobFactory,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        job_id = await make_job()

        await runner.process(job_id)

        job = await load(session_factory, job_id)
        assert job.attempts == 1
        assert job.worker_id == WORKER_ID
        assert job.started_at is not None
        assert job.completed_at is not None
        assert job.version == 3  # created, claimed, completed

    async def test_result_is_deterministic_for_the_same_message(
        self,
        runner: JobRunner,
        make_job: JobFactory,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        first_id = await make_job(message="repeatable")
        second_id = await make_job(message="repeatable")

        await runner.process(first_id)
        await runner.process(second_id)

        first = await load(session_factory, first_id)
        second = await load(session_factory, second_id)
        assert first.result == second.result


class TestDuplicateDelivery:
    async def test_redelivering_a_completed_job_is_a_no_op(
        self,
        runner: JobRunner,
        make_job: JobFactory,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        job_id = await make_job()
        await runner.process(job_id)
        original = await load(session_factory, job_id)

        outcome = await runner.process(job_id)

        assert outcome is JobOutcome.SKIPPED
        again = await load(session_factory, job_id)
        assert again.attempts == original.attempts == 1
        assert again.completed_at == original.completed_at
        assert again.version == original.version

    async def test_ten_duplicate_deliveries_execute_exactly_once(
        self,
        runner: JobRunner,
        make_job: JobFactory,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        job_id = await make_job()

        outcomes = [await runner.process(job_id) for _ in range(10)]

        assert outcomes.count(JobOutcome.COMPLETED) == 1
        assert outcomes.count(JobOutcome.SKIPPED) == 9
        assert (await load(session_factory, job_id)).attempts == 1

    async def test_two_workers_racing_on_one_job_produce_a_single_execution(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sandbox: SandboxClient,
        make_job: JobFactory,
    ) -> None:
        job_id = await make_job()
        first = JobRunner(session_factory, sandbox, worker_id="worker-a")
        second = JobRunner(session_factory, sandbox, worker_id="worker-b")

        outcomes = await asyncio.gather(first.process(job_id), second.process(job_id))

        assert sorted(outcomes) == [JobOutcome.COMPLETED, JobOutcome.SKIPPED]
        job = await load(session_factory, job_id)
        assert job.attempts == 1
        assert job.worker_id in {"worker-a", "worker-b"}

    async def test_an_unknown_identifier_is_reported_as_missing(self, runner: JobRunner) -> None:
        outcome = await runner.process(new_sortable_id())

        assert outcome is JobOutcome.MISSING

    @pytest.mark.parametrize("state", [JobState.COMPLETED, JobState.FAILED])
    async def test_terminal_jobs_are_never_reopened(
        self,
        runner: JobRunner,
        make_job: JobFactory,
        session_factory: async_sessionmaker[AsyncSession],
        state: JobState,
    ) -> None:
        job_id = await make_job(state=state)

        outcome = await runner.process(job_id)

        assert outcome is JobOutcome.SKIPPED
        assert (await load(session_factory, job_id)).state == state


class TestFailureHandling:
    async def test_sandbox_outage_fails_the_job_with_a_stable_error_code(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        unreachable_sandbox: SandboxClient,
        make_job: JobFactory,
    ) -> None:
        job_id = await make_job()
        runner = JobRunner(session_factory, unreachable_sandbox, worker_id=WORKER_ID)

        outcome = await runner.process(job_id)

        assert outcome is JobOutcome.FAILED
        job = await load(session_factory, job_id)
        assert job.state == JobState.FAILED
        assert job.error_code == "dependency_unavailable"
        assert job.completed_at is not None
        assert job.result is None

    async def test_unsupported_kind_fails_without_calling_the_sandbox(
        self,
        runner: JobRunner,
        make_job: JobFactory,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        job_id = await make_job(kind="restart_production")

        outcome = await runner.process(job_id)

        assert outcome is JobOutcome.FAILED
        assert (await load(session_factory, job_id)).state == JobState.FAILED

    async def test_a_failed_job_is_not_retried_by_redelivery(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        unreachable_sandbox: SandboxClient,
        make_job: JobFactory,
    ) -> None:
        """Automatic retry with a budget arrives in Phase 5; Phase 0 must not
        silently re-run a terminal job."""
        job_id = await make_job()
        runner = JobRunner(session_factory, unreachable_sandbox, worker_id=WORKER_ID)
        await runner.process(job_id)

        outcome = await runner.process(job_id)

        assert outcome is JobOutcome.SKIPPED
        assert (await load(session_factory, job_id)).attempts == 1
