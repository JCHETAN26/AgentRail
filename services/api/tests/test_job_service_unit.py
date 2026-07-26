from __future__ import annotations

from agentrail_api.jobs.schemas import CreateJobRequest, JobKind
from agentrail_api.jobs.service import request_fingerprint


class TestRequestFingerprint:
    def test_identical_requests_share_a_fingerprint(self) -> None:
        first = CreateJobRequest(message="hello")
        second = CreateJobRequest(message="hello", kind=JobKind.NOOP)

        assert request_fingerprint(first) == request_fingerprint(second)

    def test_different_messages_produce_different_fingerprints(self) -> None:
        assert request_fingerprint(CreateJobRequest(message="a")) != request_fingerprint(
            CreateJobRequest(message="b")
        )

    def test_fingerprint_is_a_sha256_hex_digest(self) -> None:
        fingerprint = request_fingerprint(CreateJobRequest(message="hello"))

        assert len(fingerprint) == 64
        assert int(fingerprint, 16) >= 0

    def test_fingerprint_is_stable_across_processes(self) -> None:
        """Pinned so a future refactor cannot silently invalidate stored keys."""
        assert request_fingerprint(CreateJobRequest(message="hello")) == (
            "93590e4873f08f22a69a19e6539dc5741d3f9308b05d3df91481759157509384"
        )
