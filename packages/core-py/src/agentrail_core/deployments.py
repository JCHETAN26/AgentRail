"""Canary deployment decisions and history.

Phase 12 keeps the deploy target simulated, but the record is real: a release
candidate gets a canary slice, the observed metrics are compared with the
configured limits, and the result is either promotion or rollback. The decision
function is pure so the same evidence always produces the same release history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentrail_core.db import Base


class DeploymentState(StrEnum):
    CANARY = "canary"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"


class DeploymentDecision(StrEnum):
    PROMOTE = "promote"
    ROLLBACK = "rollback"


_DEPLOYMENT_STATES = ", ".join(f"'{state.value}'" for state in DeploymentState)


@dataclass(frozen=True, slots=True)
class CanaryDecision:
    decision: DeploymentDecision
    reasons: tuple[str, ...]
    metrics: dict[str, Any]
    deltas: dict[str, float]

    @property
    def promotes(self) -> bool:
        return self.decision is DeploymentDecision.PROMOTE

    def as_payload(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "metrics": self.metrics,
            "deltas": self.deltas,
        }


def evaluate_canary(
    *,
    baseline: dict[str, Any],
    observed: dict[str, Any],
    thresholds: dict[str, Any],
) -> CanaryDecision:
    """Compare observed canary health with baseline and limits."""
    reasons: list[str] = []
    deltas = {
        "error_rate": _number(observed.get("error_rate")) - _number(baseline.get("error_rate")),
        "p95_latency_ms": _number(observed.get("p95_latency_ms"))
        - _number(baseline.get("p95_latency_ms")),
        "cost_per_1k": _number(observed.get("cost_per_1k")) - _number(baseline.get("cost_per_1k")),
    }

    min_success_rate = _optional_number(thresholds.get("min_success_rate"))
    if min_success_rate is not None and _number(observed.get("success_rate")) < min_success_rate:
        reasons.append(
            "success_rate "
            f"{_number(observed.get('success_rate')):.1%} below required {min_success_rate:.1%}"
        )

    max_error_rate = _optional_number(thresholds.get("max_error_rate"))
    if max_error_rate is not None and _number(observed.get("error_rate")) > max_error_rate:
        reasons.append(
            "error_rate "
            f"{_number(observed.get('error_rate')):.1%} above allowed {max_error_rate:.1%}"
        )

    max_latency_delta_ms = _optional_number(thresholds.get("max_p95_latency_delta_ms"))
    if max_latency_delta_ms is not None and deltas["p95_latency_ms"] > max_latency_delta_ms:
        reasons.append(
            "p95_latency_ms delta "
            f"{deltas['p95_latency_ms']:.0f}ms above allowed {max_latency_delta_ms:.0f}ms"
        )

    max_cost_delta = _optional_number(thresholds.get("max_cost_delta_per_1k"))
    if max_cost_delta is not None and deltas["cost_per_1k"] > max_cost_delta:
        reasons.append(
            f"cost_per_1k delta {deltas['cost_per_1k']:.4f} above allowed {max_cost_delta:.4f}"
        )

    return CanaryDecision(
        decision=DeploymentDecision.ROLLBACK if reasons else DeploymentDecision.PROMOTE,
        reasons=tuple(reasons),
        metrics={"baseline": baseline, "observed": observed, "thresholds": thresholds},
        deltas=deltas,
    )


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    return _number(value)


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint(f"state IN ({_DEPLOYMENT_STATES})", name="ck_deployments_state"),
        CheckConstraint(
            "traffic_percent >= 0 AND traffic_percent <= 100", name="ck_deployments_traffic"
        ),
        Index("ix_deployments_project_id", "project_id"),
        Index("ix_deployments_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evaluation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    gate_evaluation_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("gate_evaluations.id", ondelete="SET NULL"), nullable=True
    )
    candidate_agent_version_id: Mapped[str] = mapped_column(String(26), nullable=False)
    environment: Mapped[str] = mapped_column(String(100), nullable=False, default="canary")
    state: Mapped[DeploymentState] = mapped_column(
        String(32),
        nullable=False,
        default=DeploymentState.CANARY,
        server_default=DeploymentState.CANARY.value,
    )
    traffic_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    workload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    canary_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    thresholds: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    deltas: Mapped[dict[str, float]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    decision: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    rollback_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
