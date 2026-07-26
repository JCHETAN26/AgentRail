"""Process entrypoint for the worker."""

from __future__ import annotations

import asyncio

from agentrail_worker.settings import worker_settings
from agentrail_worker.worker import run_worker


def main() -> None:
    asyncio.run(run_worker(worker_settings()))


if __name__ == "__main__":  # pragma: no cover
    main()
