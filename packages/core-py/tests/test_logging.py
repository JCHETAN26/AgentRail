from __future__ import annotations

import json
import logging

import pytest

from agentrail_core.correlation import CorrelationContext, correlation_scope
from agentrail_core.logging import REDACTED, JsonFormatter, is_sensitive_key, redact


@pytest.fixture
def formatter() -> JsonFormatter:
    return JsonFormatter(service="test-service", environment="test")


def make_record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJsonFormatter:
    def test_emits_parseable_json_with_base_fields(self, formatter: JsonFormatter) -> None:
        payload = json.loads(formatter.format(make_record("hello")))

        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["service"] == "test-service"
        assert payload["environment"] == "test"
        assert payload["logger"] == "test.logger"
        assert payload["timestamp"].endswith("+00:00")

    def test_includes_correlation_fields_when_context_is_bound(
        self, formatter: JsonFormatter
    ) -> None:
        context = CorrelationContext(correlation_id="cid_abc", trace_id="a" * 32, span_id="b" * 16)

        with correlation_scope(context):
            payload = json.loads(formatter.format(make_record("with context")))

        assert payload["correlation_id"] == "cid_abc"
        assert payload["trace_id"] == "a" * 32
        assert payload["span_id"] == "b" * 16

    def test_omits_correlation_fields_when_no_context_is_bound(
        self, formatter: JsonFormatter
    ) -> None:
        payload = json.loads(formatter.format(make_record("no context")))

        assert "correlation_id" not in payload

    def test_extra_fields_are_included(self, formatter: JsonFormatter) -> None:
        payload = json.loads(formatter.format(make_record("job", job_id="01JOB", attempt=2)))

        assert payload["job_id"] == "01JOB"
        assert payload["attempt"] == 2

    def test_sensitive_extra_fields_are_redacted(self, formatter: JsonFormatter) -> None:
        payload = json.loads(
            formatter.format(
                make_record(
                    "leaky",
                    api_key="sk-live-should-never-appear",
                    authorization="Bearer nope",
                    job_id="01JOB",
                )
            )
        )

        assert payload["api_key"] == REDACTED
        assert payload["authorization"] == REDACTED
        assert payload["job_id"] == "01JOB"
        assert "sk-live-should-never-appear" not in json.dumps(payload)


class TestRedaction:
    @pytest.mark.parametrize(
        "key",
        [
            "api_key",
            "API_KEY",
            "github_webhook_secret",
            "Authorization",
            "session_cookie",
            "db_password",
            "access_token",
            "system_prompt",
            "private_key",
        ],
    )
    def test_sensitive_keys_are_detected(self, key: str) -> None:
        assert is_sensitive_key(key) is True

    @pytest.mark.parametrize("key", ["job_id", "state", "duration_ms", "service"])
    def test_ordinary_keys_are_not_redacted(self, key: str) -> None:
        assert is_sensitive_key(key) is False

    def test_redaction_recurses_into_nested_structures(self) -> None:
        payload = {
            "outer": {"password": "hunter2", "safe": 1},
            "items": [{"token": "abc"}, {"ok": True}],
        }

        result = redact(payload)

        assert result == {
            "outer": {"password": REDACTED, "safe": 1},
            "items": [{"token": REDACTED}, {"ok": True}],
        }

    def test_deeply_nested_payloads_are_truncated_rather_than_recursing_forever(self) -> None:
        payload: dict[str, object] = {"level": 0}
        cursor = payload
        for depth in range(1, 12):
            nested: dict[str, object] = {"level": depth}
            cursor["next"] = nested
            cursor = nested

        rendered = json.dumps(redact(payload))

        assert "[truncated]" in rendered
