"""GitHub webhook verification and Check Run reporting.

Two things live here, and the split matters.

``verify_webhook_signature`` is pure, offline and exhaustively tested. It is the
only thing standing between a public endpoint and anyone who can guess a URL, so
it does not depend on a network, a clock or a configured application.

``CheckRunPublisher`` is a protocol with two implementations, mirroring the
authentication providers from Phase 1: a recording one that touches nothing and
is what tests and the demo use, and a real one for deployed environments. The
gate decision is made without either of them — GitHub is where a verdict is
*delivered*, never where it is *decided*.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

#: GitHub sends the signature in this header, prefixed with the algorithm.
SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"

_PREFIX = "sha256="


def verify_webhook_signature(*, payload: bytes, signature: str | None, secret: str) -> bool:
    """Check that a webhook body really came from GitHub.

    Compared in constant time, like every other credential in this codebase: a
    byte-by-byte comparison that returns early leaks how much of a forged
    signature was correct, which is enough to construct one.

    An absent header, a wrong prefix, and a wrong digest are all one answer.
    Distinguishing them would tell an attacker which part to fix.
    """
    if not secret or not signature or not signature.startswith(_PREFIX):
        return False
    expected = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature[len(_PREFIX) :])


class CheckConclusion(StrEnum):
    """The subset of GitHub's conclusions this platform ever reports."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    #: Used when the gate could not run at all — a missing report, say. It is
    #: deliberately not `failure`: "we could not judge this" and "we judged it
    #: and it is bad" must not look the same on a pull request.
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class CheckRunRequest:
    """One Check Run, as this platform describes it."""

    owner: str
    repository: str
    head_sha: str
    name: str
    conclusion: CheckConclusion
    title: str
    summary: str
    #: File-anchored notes GitHub renders inline on the diff. Empty is fine.
    annotations: tuple[dict[str, Any], ...] = ()
    details_url: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repository": self.repository,
            "head_sha": self.head_sha,
            "name": self.name,
            "conclusion": self.conclusion.value,
            "output": {
                "title": self.title,
                "summary": self.summary,
                "annotations": list(self.annotations),
            },
            "details_url": self.details_url,
        }


class CheckRunPublisher(Protocol):
    """Somewhere to send a verdict."""

    @property
    def name(self) -> str:
        """Identifies which publisher handled a verdict, for the record."""

    async def publish(self, request: CheckRunRequest) -> dict[str, Any]:
        """Deliver the check run and return whatever identifies it."""


@dataclass
class RecordingCheckRunPublisher:
    """Publishes nowhere and remembers everything.

    This is what the test suite and the public demo use. It exists for the same
    reason the deterministic auth provider does: the platform must be fully
    exercisable without a configured GitHub App, or the tests would depend on a
    credential nobody reviewing this repository has.
    """

    published: list[CheckRunRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "recording"

    async def publish(self, request: CheckRunRequest) -> dict[str, Any]:
        self.published.append(request)
        return {
            "provider": self.name,
            "delivered": False,
            "check_run": request.as_payload(),
        }


def annotations_from_violations(
    violations: list[dict[str, Any]], *, path: str
) -> tuple[dict[str, Any], ...]:
    """Turn gate violations into inline notes on a pull request.

    Anchored to a single path — the release policy file — because a threshold
    failure is a fact about the *policy* being unmet, not about any particular
    line of the diff. Pointing it at an arbitrary source line would be a guess
    dressed up as precision.
    """
    return tuple(
        {
            "path": path,
            "start_line": 1,
            "end_line": 1,
            "annotation_level": "failure",
            "title": str(violation.get("kind", "release_rule")),
            "message": str(violation.get("message", "A release rule was not met.")),
        }
        for violation in violations
    )
