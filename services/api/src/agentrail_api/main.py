"""Process entrypoint for the API.

Uvicorn is given an explicit grace period so that in-flight requests finish
before the process exits, which is what makes a rolling deploy non-disruptive.
"""

from __future__ import annotations

import uvicorn

from agentrail_api.app import create_app
from agentrail_api.settings import api_settings


def main() -> None:
    settings = api_settings()
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - containers must bind all interfaces
        port=8000,
        log_config=None,  # structured logging is configured in the lifespan handler
        access_log=False,  # CorrelationMiddleware emits the access log instead
        timeout_graceful_shutdown=int(settings.shutdown_grace_seconds),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
