from __future__ import annotations

import asyncio

from agentrail_core.errors import DependencyUnavailableError
from agentrail_core.health import evaluate_readiness


async def healthy() -> None:
    return None


async def failing() -> None:
    raise DependencyUnavailableError("nope")


async def hanging() -> None:
    await asyncio.sleep(10)


class TestEvaluateReadiness:
    async def test_all_dependencies_up_reports_ready(self) -> None:
        response = await evaluate_readiness(
            service="api", version="0.1.0", checks={"postgresql": healthy, "redis": healthy}
        )

        assert response.status == "ready"
        assert [item.status for item in response.dependencies] == ["up", "up"]

    async def test_a_single_failure_makes_the_service_not_ready(self) -> None:
        response = await evaluate_readiness(
            service="api", version="0.1.0", checks={"postgresql": healthy, "redis": failing}
        )

        assert response.status == "not_ready"
        statuses = {item.name: item.status for item in response.dependencies}
        assert statuses == {"postgresql": "up", "redis": "down"}

    async def test_failure_detail_names_the_error_type_without_leaking_a_message(self) -> None:
        response = await evaluate_readiness(
            service="api", version="0.1.0", checks={"redis": failing}
        )

        assert response.dependencies[0].detail == "DependencyUnavailableError"

    async def test_a_hanging_check_times_out_instead_of_blocking_the_probe(self) -> None:
        response = await evaluate_readiness(
            service="api",
            version="0.1.0",
            checks={"postgresql": hanging},
            timeout_seconds=0.05,
        )

        assert response.status == "not_ready"
        assert response.dependencies[0].detail == "timeout"

    async def test_checks_run_concurrently(self) -> None:
        async def slow() -> None:
            await asyncio.sleep(0.1)

        started = asyncio.get_running_loop().time()
        await evaluate_readiness(
            service="api",
            version="0.1.0",
            checks={f"dep-{index}": slow for index in range(5)},
        )
        elapsed = asyncio.get_running_loop().time() - started

        # Serial execution would take ~0.5s.
        assert elapsed < 0.3

    async def test_dependencies_are_reported_in_a_stable_order(self) -> None:
        response = await evaluate_readiness(
            service="api",
            version="0.1.0",
            checks={"redis": healthy, "postgresql": healthy, "object_storage": healthy},
        )

        assert [item.name for item in response.dependencies] == [
            "object_storage",
            "postgresql",
            "redis",
        ]
