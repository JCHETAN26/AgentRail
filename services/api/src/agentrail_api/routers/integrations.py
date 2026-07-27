"""GitHub webhook receiver.

This is the only unauthenticated write endpoint in the platform, so the
signature check is the whole security boundary. It runs before the body is
parsed as JSON, before anything is looked up, and before any work is done.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, Request, Response, status

from agentrail_api.dependencies import SessionDep, SettingsDep
from agentrail_api.release import service
from agentrail_core.errors import ProblemDetail
from agentrail_core.github import verify_webhook_signature
from agentrail_core.logging import get_logger

logger = get_logger(__name__)

#: Event names this endpoint recognises. Anything else is logged as a literal.
_KNOWN_EVENTS: frozenset[str] = frozenset(
    {"pull_request", "ping", "push", "check_run", "check_suite", "installation"}
)
#: Pull-request actions this endpoint acts on.
_ACTED_ACTIONS: frozenset[str] = frozenset({"synchronize", "opened", "reopened"})


def _known(value: str | None, allowed: frozenset[str]) -> str:
    """Return *our* matching constant, never the caller's string.

    An allowlist rather than an escape. Everything reaching this endpoint is
    unauthenticated until the signature is checked, and some of it is logged
    before that — so nothing a caller writes may reach a log line. The logs are
    meant to be evidence, and evidence must not be writable by the subject of
    the investigation.

    Note the loop rather than ``return value`` after a membership test. The two
    are equal as strings but not the same object, and only returning the element
    from our own set makes the log argument provably ours — which is both the
    real property we want and what a taint analysis can verify.
    """
    if value is None:
        return "<unrecognised>"
    for candidate in sorted(allowed):
        if value == candidate:
            return candidate
    return "<unrecognised>"


router = APIRouter(prefix="/api/v1/integrations/github", tags=["integrations"])

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Signature missing or invalid."},
    422: {"model": ProblemDetail, "description": "Unparseable body."},
}


@router.post(
    "/webhook",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive a signed GitHub webhook",
    responses=_ERRORS,
)
async def receive_github_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> Response:
    body = await request.body()

    # An unconfigured secret rejects everything. Failing open here would turn a
    # public URL into an unauthenticated way to cancel other people's runs.
    if not verify_webhook_signature(
        payload=body,
        signature=x_hub_signature_256,
        secret=settings.github_webhook_secret or "",
    ):
        logger.warning(
            "github_webhook_rejected", extra={"event": _known(x_github_event, _KNOWN_EVENTS)}
        )
        # 401 with no detail. Which part of the signature was wrong is exactly
        # what an attacker needs, and exactly what the log is for.
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

    if x_github_event != "pull_request":
        # Acknowledged and ignored. Returning an error for events we did not
        # subscribe to would make GitHub retry them forever.
        return Response(status_code=status.HTTP_202_ACCEPTED)

    action = str(payload.get("action", ""))
    if action not in _ACTED_ACTIONS:
        return Response(status_code=status.HTTP_202_ACCEPTED)

    pull_request = payload.get("pull_request") or {}
    repository = payload.get("repository") or {}
    head = pull_request.get("head") or {}
    owner = str((repository.get("owner") or {}).get("login", ""))
    repo_name = str(repository.get("name", ""))
    head_sha = str(head.get("sha", ""))
    pull_number = pull_request.get("number")

    if not owner or not repo_name or not head_sha or not isinstance(pull_number, int):
        return Response(status_code=status.HTTP_202_ACCEPTED)

    cancelled = await service.cancel_superseded_runs(
        session,
        owner=owner,
        repository=repo_name,
        pull_number=pull_number,
        head_sha=head_sha,
    )
    await session.commit()
    logger.info(
        "github_pull_request_received",
        extra={
            "action": _known(action, _ACTED_ACTIONS),
            "pull_number": pull_number,
            "superseded_run_count": len(cancelled),
        },
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)
