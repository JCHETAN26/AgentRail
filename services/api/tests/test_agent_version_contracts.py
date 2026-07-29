"""Agent version contract boundary tests."""

from __future__ import annotations

import pytest

from agentrail_api.agents.schemas import CreateAgentVersionRequest
from agentrail_api.agents.service import version_content_digest
from agentrail_core.agents import canonical_tool_contracts


def request_with_contract(contract: dict[str, object]) -> CreateAgentVersionRequest:
    return CreateAgentVersionRequest(
        graph_spec={"entrypoint": "diagnose"},
        prompt_bundle={"system": "Diagnose."},
        model_config={"provider": "recorded"},
        tool_contracts=[contract],
        policy_bundle={},
        source_commit="abcdef1",
    )


def test_version_digest_uses_canonical_tool_contracts() -> None:
    aliased = request_with_contract({"name": "query_metrics", "risk": "read"})
    explicit = request_with_contract(
        {
            "name": "query_metrics",
            "input_schema": {},
            "risk_level": "READ_ONLY",
            "side_effect_class": "NONE",
            "timeout_seconds": 30.0,
            "retry_budget": 0,
            "approval_policy": "RISK_BASED",
        }
    )

    assert version_content_digest(aliased) == version_content_digest(explicit)


def test_canonical_tool_contracts_reject_bad_registration_payloads() -> None:
    with pytest.raises(ValueError):
        canonical_tool_contracts([{"name": "danger", "retry_budget": -1}])
