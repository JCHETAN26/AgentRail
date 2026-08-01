"""Agent decision validation.

The model is untrusted input. These tests exist to prove its answer is checked
before anything acts on it — a model that names a tool it was never offered, or
concludes with nothing, must fail the step rather than reach the gateway.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agentrail_core.agent_model import (
    AGENT_RESPONSE_SCHEMA,
    AgentAction,
    AgentActionKind,
    AgentModelError,
    AgentObservation,
    OpenAIAgentModelClient,
    RecordedAgentModelClient,
    parse_agent_action,
)

TOOLS = ["get_service_health", "search_logs", "restart_service"]


class TestActionValidation:
    def test_a_tool_call_is_parsed(self) -> None:
        action = parse_agent_action(
            {
                "kind": "tool_call",
                "reasoning": "Check health first.",
                "tool": "get_service_health",
                "arguments": {"service_name": "checkout-api"},
            },
            available_tools=TOOLS,
        )

        assert action.kind is AgentActionKind.TOOL_CALL
        assert action.tool == "get_service_health"
        assert action.arguments == {"service_name": "checkout-api"}

    def test_a_tool_the_agent_was_never_given_is_refused(self) -> None:
        # The case that matters. Dropping it would make the trace disagree with
        # what the model asked for; allowing it would route an unoffered tool
        # into the gateway.
        with pytest.raises(AgentModelError, match="not among its tools"):
            parse_agent_action(
                {"kind": "tool_call", "reasoning": "escalate", "tool": "drop_database"},
                available_tools=TOOLS,
            )

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            ({"kind": "wander", "reasoning": "x"}, "unknown action kind"),
            ({"kind": "tool_call", "reasoning": ""}, "no reasoning"),
            ({"kind": "tool_call", "reasoning": "x"}, "without naming a tool"),
            ({"kind": "conclude", "reasoning": "x"}, "without a diagnosis"),
            (
                {"kind": "tool_call", "reasoning": "x", "tool": "search_logs", "arguments": []},
                "non-object tool arguments",
            ),
        ],
    )
    def test_unusable_answers_are_rejected(self, content: dict[str, object], message: str) -> None:
        with pytest.raises(AgentModelError, match=message):
            parse_agent_action(content, available_tools=TOOLS)

    def test_the_schema_forbids_unlisted_fields(self) -> None:
        # A field nothing validates is a field a model can use to smuggle an
        # unchecked instruction into the trace.
        assert AGENT_RESPONSE_SCHEMA["additionalProperties"] is False


class TestRecordedClient:
    async def test_it_replays_the_captured_decisions_in_order(self) -> None:
        captured = [
            AgentAction(
                kind=AgentActionKind.TOOL_CALL,
                reasoning="one",
                tool="search_logs",
                arguments={"q": "errors"},
            ),
            AgentAction(kind=AgentActionKind.CONCLUDE, reasoning="two", diagnosis="pool exhausted"),
        ]
        client = RecordedAgentModelClient(actions=captured)

        first = await client.decide(
            AgentObservation(incident={}, available_tools=TOOLS, step_index=0)
        )
        second = await client.decide(
            AgentObservation(incident={}, available_tools=TOOLS, step_index=1)
        )

        # This is what makes a live benchmark reproducible: capture once, replay
        # exactly, no credentials and no drift.
        assert first.action.tool == "search_logs"
        assert second.action.diagnosis == "pool exhausted"
        assert first.provider == "recorded"

    async def test_it_still_runs_without_a_recording(self) -> None:
        client = RecordedAgentModelClient()

        first = await client.decide(
            AgentObservation(
                incident={"service": "checkout-api"}, available_tools=TOOLS, step_index=0
            )
        )
        later = await client.decide(
            AgentObservation(
                incident={"service": "checkout-api"}, available_tools=TOOLS, step_index=1
            )
        )

        # Existing suites must stay runnable with no provider configured.
        assert first.action.tool == "get_service_health"
        assert later.action.kind is AgentActionKind.CONCLUDE


class TestOpenAIClient:
    async def test_it_parses_a_structured_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "resp_1",
                    "model": "gpt-test",
                    "usage": {"input_tokens": 11, "output_tokens": 7},
                    "output": [
                        {
                            "content": [
                                {
                                    "text": json.dumps(
                                        {
                                            "kind": "tool_call",
                                            "reasoning": "Check health.",
                                            "tool": "get_service_health",
                                            "arguments": {"service_name": "checkout-api"},
                                        }
                                    )
                                }
                            ]
                        }
                    ],
                },
            )

        client = OpenAIAgentModelClient(
            api_key="test-key",
            model="gpt-test",
            transport=httpx.MockTransport(handler),
        )
        response = await client.decide(
            AgentObservation(incident={"service": "checkout-api"}, available_tools=TOOLS)
        )

        assert response.action.tool == "get_service_health"
        # Usage is recorded because a live benchmark's cost has to be measured
        # rather than assumed.
        assert response.usage["input_tokens"] == 11
        assert response.model == "gpt-test"

    async def test_a_200_with_an_unusable_body_is_an_error(self) -> None:
        client = OpenAIAgentModelClient(
            api_key="test-key",
            model="gpt-test",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"output": []})),
        )

        # Falling back to an empty action would put a decision nobody made into
        # the trace.
        with pytest.raises(AgentModelError, match="no structured content"):
            await client.decide(AgentObservation(incident={}, available_tools=TOOLS))

    async def test_a_provider_failure_is_an_error(self) -> None:
        client = OpenAIAgentModelClient(
            api_key="test-key",
            model="gpt-test",
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        )

        with pytest.raises(AgentModelError, match="HTTP 503"):
            await client.decide(AgentObservation(incident={}, available_tools=TOOLS))

    def test_an_empty_key_is_refused_at_construction(self) -> None:
        # Better than discovering it mid-benchmark on the twentieth scenario.
        with pytest.raises(AgentModelError, match="requires an API key"):
            OpenAIAgentModelClient(api_key="   ", model="gpt-test")
