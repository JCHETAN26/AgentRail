"""Agent registry contract primitives."""

from __future__ import annotations

import pytest
from sqlalchemy.orm.attributes import set_committed_value

from agentrail_core.agents import (
    AgentVersion,
    ApprovalPolicy,
    DeterministicAdapter,
    FrameworkAdapter,
    LangGraphAdapter,
    RecordedAdapter,
    SideEffectClass,
    ToolContractError,
    agent_version_changed_fields,
    canonical_tool_contracts,
    parse_tool_contracts,
)
from agentrail_core.policy import ToolRiskLevel


def test_tool_contracts_are_canonicalised() -> None:
    contracts = canonical_tool_contracts(
        [
            {
                "name": " query_metrics ",
                "schema": {"type": "object"},
                "risk": "read",
                "side_effect_class": "NONE",
                "timeout": 10,
                "retry": 2,
                "approval_policy": "NEVER",
            }
        ]
    )

    assert contracts == [
        {
            "name": "query_metrics",
            "input_schema": {"type": "object"},
            "risk_level": "READ_ONLY",
            "side_effect_class": "NONE",
            "timeout_seconds": 10.0,
            "retry_budget": 2,
            "approval_policy": "NEVER",
        }
    ]


def test_tool_contract_rejects_duplicate_names() -> None:
    with pytest.raises(ToolContractError, match="duplicate"):
        parse_tool_contracts(
            [
                {"name": "restart_service", "risk_level": "HIGH_RISK_WRITE"},
                {"name": "restart_service", "risk_level": "HIGH_RISK_WRITE"},
            ]
        )


def test_tool_contract_rejects_unevaluable_fields() -> None:
    with pytest.raises(ToolContractError, match="timeout_seconds"):
        parse_tool_contracts([{"name": "restart_service", "timeout_seconds": 0}])

    with pytest.raises(ToolContractError, match="risk_level"):
        parse_tool_contracts([{"name": "restart_service", "risk_level": "sideways"}])


def test_adapter_protocol_and_builtin_adapters_validate_contracts() -> None:
    class Version:
        def __init__(self) -> None:
            self.graph_spec = {"entrypoint": "diagnose"}
            self.tool_contracts = [
                {
                    "name": "notify_oncall",
                    "risk_level": ToolRiskLevel.LOW_RISK_WRITE.value,
                    "side_effect_class": SideEffectClass.IDEMPOTENT_WRITE.value,
                    "approval_policy": ApprovalPolicy.RISK_BASED.value,
                }
            ]

    for adapter in (DeterministicAdapter(), RecordedAdapter(), LangGraphAdapter()):
        assert isinstance(adapter, FrameworkAdapter)
        adapter.validate_version(Version())  # type: ignore[arg-type]


def test_agent_version_changed_fields_detects_immutable_payload_mutations() -> None:
    version = AgentVersion(
        id="01ARZ3NDEKTSV4RRFFQ69G5FV1",
        agent_id="01ARZ3NDEKTSV4RRFFQ69G5FAG",
        version=1,
        content_digest="a" * 64,
        graph_spec={},
        prompt_bundle={"system": "original"},
        model_config={},
        tool_contracts=[],
        policy_bundle={},
        source_commit="abc1234",
    )
    for field in (
        "id",
        "agent_id",
        "version",
        "content_digest",
        "graph_spec",
        "prompt_bundle",
        "model_config",
        "tool_contracts",
        "policy_bundle",
        "source_commit",
    ):
        set_committed_value(version, field, getattr(version, field))

    version.prompt_bundle = {"system": "mutated"}

    assert agent_version_changed_fields(version) == ["prompt_bundle"]
