"""The platform error contract.

Every error crossing an HTTP boundary is serialised as a :class:`ProblemDetail`
with a *stable machine-readable* ``code``. Clients switch on ``code``; humans
quote ``correlation_id``. Stack traces and driver messages never reach the
client.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    """Stable error codes. Values are part of the public API contract."""

    VALIDATION_FAILED = "validation_failed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    RATE_LIMITED = "rate_limited"
    REPLAYED_WEBHOOK = "replayed_webhook"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    INTERNAL_ERROR = "internal_error"


class ProblemDetail(BaseModel):
    """The body returned for every non-2xx API response."""

    code: ErrorCode = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable summary. Safe to display.")
    correlation_id: str = Field(description="Quote this when reporting a failure.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured, non-sensitive context such as failing field paths.",
    )


class PlatformError(Exception):
    """Base class for errors that map onto a :class:`ProblemDetail`."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_problem(self, correlation_id: str) -> ProblemDetail:
        return ProblemDetail(
            code=self.code,
            message=self.message,
            correlation_id=correlation_id,
            details=self.details,
        )


class UnauthenticatedError(PlatformError):
    """No usable credential was presented."""

    code = ErrorCode.UNAUTHENTICATED
    status_code = 401


class ForbiddenError(PlatformError):
    """A valid credential that is not permitted to do this.

    Carries no detail about which permission was missing or whether the target
    exists — that would let a caller map another tenant's resources.
    """

    code = ErrorCode.FORBIDDEN
    status_code = 403

    def __init__(self) -> None:
        super().__init__("You do not have access to this resource.")


class NotFoundError(PlatformError):
    code = ErrorCode.NOT_FOUND
    status_code = 404


class ConflictError(PlatformError):
    code = ErrorCode.CONFLICT
    status_code = 409


class IdempotencyKeyReusedError(PlatformError):
    """The same idempotency key was replayed with a different request body."""

    code = ErrorCode.IDEMPOTENCY_KEY_REUSED
    status_code = 409


class ValidationFailedError(PlatformError):
    code = ErrorCode.VALIDATION_FAILED
    status_code = 422


class PayloadTooLargeError(PlatformError):
    code = ErrorCode.PAYLOAD_TOO_LARGE
    status_code = 413


class RateLimitedError(PlatformError):
    code = ErrorCode.RATE_LIMITED
    status_code = 429


class ReplayedWebhookError(PlatformError):
    code = ErrorCode.REPLAYED_WEBHOOK
    status_code = 409


class DependencyUnavailableError(PlatformError):
    """A required infrastructure dependency is not usable right now."""

    code = ErrorCode.DEPENDENCY_UNAVAILABLE
    status_code = 503
