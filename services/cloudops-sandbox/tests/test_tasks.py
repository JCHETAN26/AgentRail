from __future__ import annotations

import pytest

from agentrail_cloudops_sandbox import __version__
from agentrail_cloudops_sandbox.tasks import DIGEST_LENGTH, execute_noop


class TestExecuteNoop:
    def test_echoes_the_message(self) -> None:
        assert execute_noop("hello")["echo"] == "hello"

    def test_digest_is_stable_across_calls(self) -> None:
        """Determinism is what makes CI assertions and replay possible."""
        assert execute_noop("hello") == execute_noop("hello")

    def test_digest_is_the_documented_sha256_prefix(self) -> None:
        # sha256("hello") = 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
        result = execute_noop("hello")

        assert result["digest"] == "2cf24dba5fb0a30e"
        assert len(result["digest"]) == DIGEST_LENGTH

    def test_different_messages_produce_different_digests(self) -> None:
        assert execute_noop("a")["digest"] != execute_noop("b")["digest"]

    def test_reports_the_sandbox_version(self) -> None:
        assert execute_noop("hello")["sandbox_version"] == __version__

    @pytest.mark.parametrize("message", ["", " ", "unicode ✅ 日本語", "x" * 500])
    def test_handles_edge_case_messages(self, message: str) -> None:
        result = execute_noop(message)

        assert result["echo"] == message
        assert len(result["digest"]) == DIGEST_LENGTH
