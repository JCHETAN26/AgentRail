from __future__ import annotations

import pytest

from agentrail_core.correlation import (
    CORRELATION_HEADER,
    TRACEPARENT_HEADER,
    CorrelationContext,
    context_from_headers,
    correlation_scope,
    current_context,
    parse_traceparent,
    render_traceparent,
)


class TestParseTraceparent:
    def test_parses_a_valid_sampled_header(self) -> None:
        header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

        parsed = parse_traceparent(header)

        assert parsed == ("4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7", True)

    def test_reports_unsampled_flag(self) -> None:
        header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00"

        parsed = parse_traceparent(header)

        assert parsed is not None
        assert parsed[2] is False

    def test_uppercase_input_is_normalised(self) -> None:
        header = "00-4BF92F3577B34DA6A3CE929D0E0E4736-00F067AA0BA902B7-01"

        assert parse_traceparent(header) is not None

    @pytest.mark.parametrize(
        "header",
        [
            None,
            "",
            "not-a-traceparent",
            "00-tooshort-00f067aa0ba902b7-01",
            # All-zero trace id and span id are invalid per the W3C spec.
            "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
        ],
    )
    def test_malformed_headers_are_rejected_without_raising(self, header: str | None) -> None:
        assert parse_traceparent(header) is None

    def test_render_round_trips(self) -> None:
        trace_id, span_id = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"

        rendered = render_traceparent(trace_id, span_id, sampled=False)

        assert parse_traceparent(rendered) == (trace_id, span_id, False)


class TestContextFromHeaders:
    def test_continues_an_inbound_trace(self) -> None:
        context = context_from_headers(
            {
                TRACEPARENT_HEADER: "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
                CORRELATION_HEADER: "cid_from_client",
            }
        )

        assert context.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert context.correlation_id == "cid_from_client"
        # A new span is started for this hop rather than reusing the parent's.
        assert context.span_id != "00f067aa0ba902b7"

    def test_generates_identifiers_when_headers_are_absent(self) -> None:
        context = context_from_headers({})

        assert context.correlation_id.startswith("cid_")
        assert len(context.trace_id) == 32
        assert len(context.span_id) == 16

    def test_malformed_traceparent_starts_a_new_trace(self) -> None:
        context = context_from_headers({TRACEPARENT_HEADER: "garbage"})

        assert len(context.trace_id) == 32

    def test_oversized_client_correlation_id_is_truncated(self) -> None:
        context = context_from_headers({CORRELATION_HEADER: "x" * 500})

        assert len(context.correlation_id) == 128

    def test_header_lookup_is_case_insensitive(self) -> None:
        context = context_from_headers({"X-Correlation-ID": "cid_mixed_case"})

        assert context.correlation_id == "cid_mixed_case"


class TestCorrelationScope:
    def test_binds_and_restores_context(self) -> None:
        context = CorrelationContext(correlation_id="cid_test", trace_id="a" * 32, span_id="b" * 16)

        assert current_context() is None
        with correlation_scope(context):
            assert current_context() == context
        assert current_context() is None

    def test_context_is_restored_after_an_exception(self) -> None:
        context = CorrelationContext(correlation_id="cid_test", trace_id="a" * 32, span_id="b" * 16)

        with pytest.raises(RuntimeError), correlation_scope(context):
            raise RuntimeError("boom")

        assert current_context() is None

    def test_outbound_headers_carry_both_identifiers(self) -> None:
        context = CorrelationContext(correlation_id="cid_test", trace_id="a" * 32, span_id="b" * 16)

        headers = context.to_headers()

        assert headers[CORRELATION_HEADER] == "cid_test"
        assert parse_traceparent(headers[TRACEPARENT_HEADER]) == ("a" * 32, "b" * 16, True)
