"""Webhook signature verification and check-run publishing."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from agentrail_core.github import (
    CheckConclusion,
    CheckRunRequest,
    RecordingCheckRunPublisher,
    annotations_from_violations,
    verify_webhook_signature,
)

SECRET = "a-webhook-secret"
PAYLOAD = b'{"action":"synchronize","number":42}'


def sign(payload: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_a_genuine_signature_verifies() -> None:
    assert verify_webhook_signature(payload=PAYLOAD, signature=sign(PAYLOAD), secret=SECRET) is True


def test_a_tampered_body_is_rejected() -> None:
    signature = sign(PAYLOAD)

    assert (
        verify_webhook_signature(payload=PAYLOAD + b" ", signature=signature, secret=SECRET)
        is False
    )


def test_a_signature_from_a_different_secret_is_rejected() -> None:
    forged = sign(PAYLOAD, secret="not-the-secret")

    assert verify_webhook_signature(payload=PAYLOAD, signature=forged, secret=SECRET) is False


@pytest.mark.parametrize(
    "signature",
    [
        None,
        "",
        "sha1=deadbeef",
        hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest(),  # prefix missing
        "sha256=",
        "sha256=not-hex",
    ],
)
def test_malformed_signatures_are_all_rejected_identically(signature: str | None) -> None:
    """One answer for every failure. Telling an attacker which part was wrong
    tells them which part to fix."""
    assert verify_webhook_signature(payload=PAYLOAD, signature=signature, secret=SECRET) is False


def test_an_unconfigured_secret_rejects_everything() -> None:
    """Failing open here would make the endpoint an unauthenticated write."""
    assert verify_webhook_signature(payload=PAYLOAD, signature=sign(PAYLOAD), secret="") is False


def test_an_empty_body_still_verifies_when_correctly_signed() -> None:
    assert verify_webhook_signature(payload=b"", signature=sign(b""), secret=SECRET) is True


async def test_the_recording_publisher_delivers_nothing_and_remembers_everything() -> None:
    publisher = RecordingCheckRunPublisher()
    request = CheckRunRequest(
        owner="acme",
        repository="agent",
        head_sha="c" * 40,
        name="AgentRail / release gate",
        conclusion=CheckConclusion.FAILURE,
        title="1 release rule failed.",
        summary="Overall pass rate 88.0% is below the required 95.0%.",
    )

    result = await publisher.publish(request)

    assert publisher.published == [request]
    assert result["delivered"] is False
    assert result["check_run"]["conclusion"] == "failure"


def test_a_gate_that_could_not_run_is_neutral_not_a_failure() -> None:
    """'We could not judge this' and 'we judged it and it is bad' must not look
    the same on a pull request."""
    assert CheckConclusion.NEUTRAL.value == "neutral"
    assert CheckConclusion.NEUTRAL is not CheckConclusion.FAILURE


def test_violations_become_annotations_anchored_to_the_policy() -> None:
    annotations = annotations_from_violations(
        [
            {"kind": "min_pass_rate", "message": "Overall pass rate 88.0% is below 95.0%."},
            {"kind": "max_regressions", "message": "4 regressions exceed the allowed 0."},
        ],
        path=".agentrail/release-policy.json",
    )

    assert len(annotations) == 2
    assert all(item["path"] == ".agentrail/release-policy.json" for item in annotations)
    assert all(item["annotation_level"] == "failure" for item in annotations)
    assert annotations[0]["title"] == "min_pass_rate"


def test_no_violations_produce_no_annotations() -> None:
    assert annotations_from_violations([], path=".agentrail/release-policy.json") == ()
