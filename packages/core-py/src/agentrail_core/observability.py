"""Run-level observability and SLO evaluation.

The data here is intentionally derived from existing execution, evaluator,
release and deployment records. Phase 13 makes those signals operationally
visible without inventing another source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SloStatus(StrEnum):
    HEALTHY = "healthy"
    VIOLATED = "violated"


@dataclass(frozen=True, slots=True)
class SloDecision:
    status: SloStatus
    objectives: dict[str, Any]
    violations: tuple[str, ...]

    @property
    def healthy(self) -> bool:
        return self.status is SloStatus.HEALTHY

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "objectives": self.objectives,
            "violations": list(self.violations),
        }


DEFAULT_RUN_SLOS: dict[str, Any] = {
    "min_success_rate": 0.95,
    "max_error_items": 0,
    "max_stranded_items": 0,
    "max_rollback_count": 0,
    "max_cost_micros": 5_000_000,
}


def evaluate_run_slo(
    metrics: dict[str, Any], objectives: dict[str, Any] | None = None
) -> SloDecision:
    """Evaluate a run metrics snapshot against operational objectives."""
    resolved = {**DEFAULT_RUN_SLOS, **(objectives or {})}
    violations: list[str] = []

    success_rate = _number(metrics.get("quality", {}).get("pass_rate"))
    if success_rate < _number(resolved["min_success_rate"]):
        violations.append(
            f"success_rate {success_rate:.1%} below {_number(resolved['min_success_rate']):.1%}"
        )

    error_items = _integer(metrics.get("run", {}).get("failed_count"))
    if error_items > _integer(resolved["max_error_items"]):
        violations.append(f"failed_items {error_items} above {resolved['max_error_items']}")

    stranded = _integer(metrics.get("reliability", {}).get("stranded_count"))
    if stranded > _integer(resolved["max_stranded_items"]):
        violations.append(f"stranded_items {stranded} above {resolved['max_stranded_items']}")

    rollbacks = _integer(metrics.get("canary", {}).get("rollback_count"))
    if rollbacks > _integer(resolved["max_rollback_count"]):
        violations.append(f"rollback_count {rollbacks} above {resolved['max_rollback_count']}")

    cost = _integer(metrics.get("budgets", {}).get("spent", {}).get("cost_micros"))
    if cost > _integer(resolved["max_cost_micros"]):
        violations.append(f"cost_micros {cost} above {resolved['max_cost_micros']}")

    return SloDecision(
        status=SloStatus.VIOLATED if violations else SloStatus.HEALTHY,
        objectives=resolved,
        violations=tuple(violations),
    )


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)
