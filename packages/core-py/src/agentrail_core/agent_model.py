"""Model-driven agent decisions.

Until now the only LLM integration in AgentRail was the Tribunal's. The agent
itself was always the recorded executor, which builds its tool arguments from
the run item's *index* and always reports success — so every "task success"
figure the platform published measured a hard-coded pass, not a decision.

This is the missing half: a client the agent asks *what should I do next*, and
whose answer is a tool call or a final diagnosis.

Two properties matter more than the provider:

**The model never touches a tool.** It names one. The runner's ``ToolGateway``
applies it, which is what keeps the budget ledger, the policy gate and the
idempotent side-effect ledger enforceable against a model that has never heard
of them. A model asking for a forbidden tool gets refused by the same code that
refuses a graph.

**Its output is schema-checked before it is believed.** A model that returns
prose where a tool name belongs fails the step rather than propagating a
malformed action into the ledger.

Determinism is preserved by recording rather than by avoiding models: a live run
captures a trajectory, and that trajectory replays exactly. See
``RecordedAgentModelClient``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx


class AgentModelError(RuntimeError):
    """The model could not be consulted, or answered unusably."""


class AgentActionKind(StrEnum):
    """What the model decided to do this step."""

    TOOL_CALL = "tool_call"
    #: The agent believes it has diagnosed the incident and is done.
    CONCLUDE = "conclude"


#: The shape a model must answer in. Kept minimal on purpose: every field here
#: has to be checked, and a field nothing validates is a field a model can use
#: to smuggle an unvalidated instruction into the trace.
AGENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "reasoning"],
    "properties": {
        "kind": {"type": "string", "enum": [kind.value for kind in AgentActionKind]},
        "reasoning": {"type": "string"},
        "tool": {"type": ["string", "null"]},
        "arguments": {"type": ["object", "null"]},
        "diagnosis": {"type": ["string", "null"]},
    },
}


@dataclass(frozen=True, slots=True)
class AgentObservation:
    """What the agent knows when it is asked to decide.

    Tool results accumulate here across steps. The incident text and the
    available tools come from the run item and the agent version, so a model
    cannot invent a tool that was never offered to it — and if it names one
    anyway, :func:`parse_agent_action` rejects the answer.
    """

    incident: dict[str, Any]
    available_tools: list[str]
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    step_index: int = 0
    max_steps: int = 8


@dataclass(frozen=True, slots=True)
class AgentAction:
    """One validated decision."""

    kind: AgentActionKind
    reasoning: str
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    diagnosis: str | None = None


@dataclass(frozen=True, slots=True)
class AgentModelResponse:
    """A decision plus the provenance needed to reproduce or audit it."""

    action: AgentAction
    provider: str
    model: str
    response_id: str
    usage: dict[str, Any] = field(default_factory=dict)


class AgentModelClient(Protocol):
    async def decide(self, observation: AgentObservation) -> AgentModelResponse:
        """Return the next action, or raise ``AgentModelError``."""


def parse_agent_action(content: dict[str, Any], *, available_tools: list[str]) -> AgentAction:
    """Validate a model's answer into an action.

    Rejects rather than repairs. A tool call naming a tool the agent was never
    given is the case that matters: silently dropping it would make the trace
    disagree with what the model actually asked for, and silently allowing it
    would route an unoffered tool into the gateway.
    """
    raw_kind = content.get("kind")
    if raw_kind not in {kind.value for kind in AgentActionKind}:
        raise AgentModelError(f"model returned an unknown action kind: {raw_kind!r}")
    kind = AgentActionKind(raw_kind)

    reasoning = content.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise AgentModelError("model returned no reasoning for its action")

    if kind is AgentActionKind.CONCLUDE:
        diagnosis = content.get("diagnosis")
        if not isinstance(diagnosis, str) or not diagnosis.strip():
            raise AgentModelError("model concluded without a diagnosis")
        return AgentAction(kind=kind, reasoning=reasoning, diagnosis=diagnosis)

    tool = content.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise AgentModelError("model asked for a tool call without naming a tool")
    if tool not in available_tools:
        raise AgentModelError(
            f"model asked for {tool!r}, which is not among its tools: {sorted(available_tools)}"
        )
    arguments = content.get("arguments")
    if arguments is not None and not isinstance(arguments, dict):
        raise AgentModelError("model returned non-object tool arguments")
    return AgentAction(kind=kind, reasoning=reasoning, tool=tool, arguments=dict(arguments or {}))


SYSTEM_PROMPT = (
    "You are an on-call site reliability engineer responding to a production incident. "
    "Investigate using the tools you are given, then conclude with a diagnosis. "
    "Call read-only tools before any tool that changes the system. "
    "Answer only in the required JSON schema."
)


class RecordedAgentModelClient:
    """Replays a captured decision sequence instead of calling a provider.

    This is how a live benchmark stays reproducible. A real run is captured once
    and replayed exactly afterwards, so CI and the frozen benchmark never need
    credentials and never drift, while the numbers still came from a model.

    Given no recording it degrades to a deterministic investigate-then-conclude
    sequence, which keeps existing suites runnable without a provider.
    """

    def __init__(
        self, *, actions: list[AgentAction] | None = None, model: str = "recorded-agent-v1"
    ) -> None:
        self._actions = list(actions or [])
        self._model = model

    async def decide(self, observation: AgentObservation) -> AgentModelResponse:
        if observation.step_index < len(self._actions):
            action = self._actions[observation.step_index]
        else:
            action = self._default_action(observation)
        return AgentModelResponse(
            action=action,
            provider="recorded",
            model=self._model,
            response_id=f"recorded:{observation.step_index}",
            usage={"recorded": True},
        )

    def _default_action(self, observation: AgentObservation) -> AgentAction:
        read_only = [tool for tool in observation.available_tools if tool.startswith("get_")]
        if observation.step_index == 0 and read_only:
            return AgentAction(
                kind=AgentActionKind.TOOL_CALL,
                reasoning="Check the service's health before acting on it.",
                tool=read_only[0],
                arguments={"service_name": observation.incident.get("service", "unknown")},
            )
        return AgentAction(
            kind=AgentActionKind.CONCLUDE,
            reasoning="Recorded fallback concluded from the observations gathered.",
            diagnosis=f"Investigated {observation.incident.get('service', 'the service')}.",
        )


class OpenAIAgentModelClient:
    """OpenAI Responses API adapter for agent decisions.

    Deliberately the same shape as ``OpenAITribunalModelClient``: structured
    output enforced by schema, provider and usage returned for provenance, and
    an explicit timeout so a hung provider fails the item rather than the worker.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise AgentModelError("OpenAI agent model provider requires an API key.")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    async def decide(self, observation: AgentObservation) -> AgentModelResponse:
        payload = {
            "model": self._model,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "incident": observation.incident,
                            "available_tools": observation.available_tools,
                            "tool_results_so_far": observation.tool_results,
                            "step": observation.step_index,
                            "max_steps": observation.max_steps,
                        },
                        sort_keys=True,
                        default=str,
                    ),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "agentrail_agent_action",
                    "schema": AGENT_RESPONSE_SCHEMA,
                    "strict": True,
                }
            },
        }
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            transport=self._transport,
        ) as client:
            response = await client.post(
                "/responses",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise AgentModelError(
                    f"OpenAI agent request failed with HTTP {exc.response.status_code}."
                ) from exc
            body = response.json()

        action = parse_agent_action(
            _structured_content(body), available_tools=observation.available_tools
        )
        return AgentModelResponse(
            action=action,
            provider="openai",
            model=str(body.get("model") or self._model),
            response_id=str(body.get("id") or ""),
            usage=dict(body.get("usage") or {}),
        )


def _structured_content(body: dict[str, Any]) -> dict[str, Any]:
    """Pull the JSON object out of a Responses API body.

    A provider that returns a 200 with an unusable body is a failure, not a
    default: falling back to an empty action would put a decision nobody made
    into the trace.
    """
    for item in body.get("output", []) or []:
        for chunk in item.get("content", []) or []:
            text = chunk.get("text")
            if not isinstance(text, str):
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as invalid:
                raise AgentModelError("model returned content that is not JSON") from invalid
            if isinstance(parsed, dict):
                return parsed
    raise AgentModelError("model response contained no structured content")
