"""Agent registry persistence models.

Definitions are stable logical identities inside a project. Versions are
immutable snapshots of graph, prompt, model, tool and policy configuration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentrail_core.db import Base
from agentrail_core.policy import ToolRiskLevel


class ToolContractError(ValueError):
    """A tool contract cannot be evaluated or enforced."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"tool_contract: {reason}")
        self.reason = reason


class SideEffectClass(StrEnum):
    NONE = "NONE"
    IDEMPOTENT_WRITE = "IDEMPOTENT_WRITE"
    NON_IDEMPOTENT_WRITE = "NON_IDEMPOTENT_WRITE"


class ApprovalPolicy(StrEnum):
    NEVER = "NEVER"
    RISK_BASED = "RISK_BASED"
    ALWAYS = "ALWAYS"


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Executable contract for one agent-visible tool."""

    name: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    risk_level: ToolRiskLevel = ToolRiskLevel.HIGH_RISK_WRITE
    side_effect_class: SideEffectClass = SideEffectClass.NONE
    timeout_seconds: float = 30.0
    retry_budget: int = 0
    approval_policy: ApprovalPolicy = ApprovalPolicy.RISK_BASED

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk_level"] = self.risk_level.value
        payload["side_effect_class"] = self.side_effect_class.value
        payload["approval_policy"] = self.approval_policy.value
        return payload


@runtime_checkable
class FrameworkAdapter(Protocol):
    """Contract every agent runtime adapter must satisfy."""

    @property
    def name(self) -> str:
        """Stable adapter identifier stored with execution evidence."""

    def validate_version(self, version: AgentVersion) -> None:
        """Reject an agent version this adapter cannot execute."""


@dataclass(frozen=True, slots=True)
class DeterministicAdapter:
    """CI-safe adapter for rule-based deterministic execution."""

    name: str = "deterministic"

    def validate_version(self, version: AgentVersion) -> None:
        parse_tool_contracts(version.tool_contracts)


@dataclass(frozen=True, slots=True)
class RecordedAdapter:
    """Replay adapter for recorded trajectories and demos."""

    name: str = "recorded"

    def validate_version(self, version: AgentVersion) -> None:
        parse_tool_contracts(version.tool_contracts)


@dataclass(frozen=True, slots=True)
class LangGraphAdapter:
    """Adapter contract marker for LangGraph-backed agent runtimes."""

    name: str = "langgraph"

    def validate_version(self, version: AgentVersion) -> None:
        parse_tool_contracts(version.tool_contracts)
        if not isinstance(version.graph_spec, dict):
            raise ToolContractError("graph_spec must be an object for LangGraph execution")


def parse_tool_contracts(raw: list[dict[str, Any]] | None) -> list[ToolContract]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ToolContractError("tool_contracts must be a list")
    contracts = [_parse_tool_contract(item, index=index) for index, item in enumerate(raw)]
    names = [contract.name for contract in contracts]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ToolContractError(f"duplicate tool name(s): {', '.join(duplicates)}")
    return contracts


def canonical_tool_contracts(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [contract.as_payload() for contract in parse_tool_contracts(raw)]


def _parse_tool_contract(raw: dict[str, Any], *, index: int) -> ToolContract:
    if not isinstance(raw, dict):
        raise ToolContractError(f"tool_contracts[{index}] must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ToolContractError(f"tool_contracts[{index}].name must be a non-empty string")
    schema = raw.get("input_schema", raw.get("schema", {}))
    if not isinstance(schema, dict):
        raise ToolContractError(f"tool_contracts[{index}].input_schema must be an object")
    timeout = raw.get("timeout_seconds", raw.get("timeout", 30.0))
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
        raise ToolContractError(f"tool_contracts[{index}].timeout_seconds must be positive")
    retry_budget = raw.get("retry_budget", raw.get("retry", 0))
    if isinstance(retry_budget, bool) or not isinstance(retry_budget, int) or retry_budget < 0:
        raise ToolContractError(f"tool_contracts[{index}].retry_budget must be a non-negative int")
    return ToolContract(
        name=name.strip(),
        input_schema=schema,
        risk_level=_risk(raw.get("risk_level", raw.get("risk", ToolRiskLevel.HIGH_RISK_WRITE))),
        side_effect_class=_side_effect_class(
            raw.get("side_effect_class", SideEffectClass.NONE),
            f"tool_contracts[{index}].side_effect_class",
        ),
        timeout_seconds=float(timeout),
        retry_budget=retry_budget,
        approval_policy=_approval_policy(
            raw.get("approval_policy", ApprovalPolicy.RISK_BASED),
            f"tool_contracts[{index}].approval_policy",
        ),
    )


def _risk(value: Any) -> ToolRiskLevel:
    aliases = {
        "read": ToolRiskLevel.READ_ONLY,
        "readonly": ToolRiskLevel.READ_ONLY,
        "low": ToolRiskLevel.LOW_RISK_WRITE,
        "write": ToolRiskLevel.LOW_RISK_WRITE,
        "high": ToolRiskLevel.HIGH_RISK_WRITE,
        "prohibited": ToolRiskLevel.PROHIBITED,
    }
    if isinstance(value, ToolRiskLevel):
        return value
    if not isinstance(value, str):
        raise ToolContractError("risk_level must be a known risk level")
    normalised = value.strip()
    alias = aliases.get(normalised.lower())
    if alias is not None:
        return alias
    try:
        return ToolRiskLevel(normalised)
    except ValueError:
        raise ToolContractError(f"risk_level is not known: {value!r}") from None


def _side_effect_class(value: Any, field_name: str) -> SideEffectClass:
    if isinstance(value, SideEffectClass):
        return value
    if not isinstance(value, str):
        raise ToolContractError(f"{field_name} must be a string")
    try:
        return SideEffectClass(value.strip())
    except ValueError:
        raise ToolContractError(f"{field_name} is not known: {value!r}") from None


def _approval_policy(value: Any, field_name: str) -> ApprovalPolicy:
    if isinstance(value, ApprovalPolicy):
        return value
    if not isinstance(value, str):
        raise ToolContractError(f"{field_name} must be a string")
    try:
        return ApprovalPolicy(value.strip())
    except ValueError:
        raise ToolContractError(f"{field_name} is not known: {value!r}") from None


class AgentDefinition(Base):
    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_agent_definitions_project_slug"),
        Index("ix_agent_definitions_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
        UniqueConstraint("agent_id", "content_digest", name="uq_agent_versions_agent_digest"),
        Index("ix_agent_versions_agent_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_spec: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    prompt_bundle: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    model_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    tool_contracts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    policy_bundle: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    source_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def agent_version_changed_fields(version: AgentVersion) -> list[str]:
    state = inspect(version)
    return [attribute.key for attribute in state.attrs if attribute.history.has_changes()]


@event.listens_for(AgentVersion, "before_update")
def _prevent_agent_version_update(_mapper: Any, _connection: Any, target: AgentVersion) -> None:
    changed = agent_version_changed_fields(target)
    if changed:
        raise ValueError(
            f"agent_versions are immutable after creation; changed fields: {', '.join(changed)}"
        )
