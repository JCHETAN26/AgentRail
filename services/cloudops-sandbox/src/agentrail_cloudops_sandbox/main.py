"""Process entrypoint for the CloudOps sandbox."""

from __future__ import annotations

import uvicorn

from agentrail_cloudops_sandbox.app import SandboxSettings, create_app


def main() -> None:
    settings = SandboxSettings()
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - containers must bind all interfaces
        port=8100,
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=int(settings.shutdown_grace_seconds),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
