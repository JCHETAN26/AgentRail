"""Budgets and circuit breaking.

Both are pure: they take a state and a charge, and return the next state. No
clock, no I/O, no database. The caller supplies ``now`` where time matters, so
every test asserts on an exact instant rather than sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class BudgetKind(StrEnum):
    """The five budgets from ``BUILDPLAN.md`` line 679."""

    TOOL_CALLS = "tool_calls"
    TOKENS = "tokens"
    LOOP_ITERATIONS = "loop_iterations"
    LATENCY_MS = "latency_ms"
    COST_MICROS = "cost_micros"


#: Deliberately generous. A budget exists to stop a runaway agent, not to
#: second-guess a legitimately long task, and a limit that trips on normal work
#: teaches operators to raise it reflexively.
DEFAULT_BUDGETS: dict[BudgetKind, int] = {
    BudgetKind.TOOL_CALLS: 50,
    BudgetKind.TOKENS: 200_000,
    BudgetKind.LOOP_ITERATIONS: 25,
    BudgetKind.LATENCY_MS: 300_000,
    BudgetKind.COST_MICROS: 5_000_000,
}


class BudgetExceededError(Exception):
    """A budget was exhausted.

    Carries the overrun ledger as well as the kind, because the caller's own
    ``ledger`` variable is still the pre-charge one — the assignment never
    happened. Without this the recovery view would report a spend of zero for
    the very charge that broke the budget.
    """

    def __init__(self, kind: BudgetKind, limit: int, spent: int, ledger: BudgetLedger) -> None:
        super().__init__(f"{kind.value} budget exhausted: spent {spent} of {limit}")
        self.kind = kind
        self.limit = limit
        self.spent = spent
        self.ledger = ledger


@dataclass(frozen=True, slots=True)
class BudgetLedger:
    """What one run item has spent against each budget."""

    limits: dict[BudgetKind, int]
    spent: dict[BudgetKind, int]

    @classmethod
    def create(cls, overrides: dict[str, Any] | None = None) -> BudgetLedger:
        limits = dict(DEFAULT_BUDGETS)
        for raw_kind, raw_limit in (overrides or {}).items():
            try:
                kind = BudgetKind(raw_kind)
            except ValueError:
                continue  # An unknown budget name is inert, not fatal.
            if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) and raw_limit >= 0:
                limits[kind] = raw_limit
        return cls(limits=limits, spent=dict.fromkeys(BudgetKind, 0))

    @classmethod
    def restore(
        cls, overrides: dict[str, Any] | None, persisted: dict[str, Any] | None
    ) -> BudgetLedger:
        """Rebuild a ledger carrying forward what earlier attempts already spent.

        A budget is per *item*, not per attempt. Starting each retry from zero
        would let an item with two attempts spend twice its limit and still
        report the smaller number — so a limit of 1,500 tokens would never trip
        against two attempts of 1,000. The limits come from the suite either
        way; only the spend is restored.
        """
        ledger = cls.create(overrides)
        raw_spent = (persisted or {}).get("spent")
        if not isinstance(raw_spent, dict):
            return ledger
        spent = dict(ledger.spent)
        for raw_kind, raw_amount in raw_spent.items():
            try:
                kind = BudgetKind(raw_kind)
            except ValueError:
                continue
            if isinstance(raw_amount, int) and not isinstance(raw_amount, bool) and raw_amount >= 0:
                spent[kind] = raw_amount
        return replace(ledger, spent=spent)

    def charge(self, kind: BudgetKind, amount: int) -> BudgetLedger:
        """Spend against a budget, raising once the limit is passed.

        The charge is applied before the check, so the ledger records what was
        actually consumed rather than stopping one unit short and understating
        the overrun in the recovery view.
        """
        if amount < 0:
            raise ValueError("a budget charge cannot be negative")
        spent = dict(self.spent)
        spent[kind] = spent[kind] + amount
        ledger = replace(self, spent=spent)
        limit = self.limits[kind]
        if spent[kind] > limit:
            raise BudgetExceededError(kind, limit, spent[kind], ledger)
        return ledger

    def remaining(self, kind: BudgetKind) -> int:
        return max(self.limits[kind] - self.spent[kind], 0)

    def as_payload(self) -> dict[str, Any]:
        return {
            "limits": {kind.value: limit for kind, limit in self.limits.items()},
            "spent": {kind.value: spent for kind, spent in self.spent.items()},
            "remaining": {kind.value: self.remaining(kind) for kind in BudgetKind},
        }


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitBreaker:
    """One breaker per dependency.

    Closed passes traffic. It opens after ``threshold`` *consecutive* failures —
    consecutive, not cumulative, because a dependency that fails one call in
    fifty is degraded, not down, and tripping on that would take work offline
    that would otherwise succeed. After ``cooldown`` it half-opens and lets a
    single probe through: success closes it, failure opens it again.
    """

    dependency: str
    threshold: int = 5
    cooldown: timedelta = timedelta(seconds=30)
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    opened_at: datetime | None = None

    def allows(self, now: datetime) -> bool:
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.HALF_OPEN:
            return True
        return self._cooled_down(now)

    def record_success(self, now: datetime) -> CircuitBreaker:
        return replace(
            self,
            state=BreakerState.CLOSED,
            consecutive_failures=0,
            opened_at=None,
        )

    def record_failure(self, now: datetime) -> CircuitBreaker:
        failures = self.consecutive_failures + 1
        # A failure while probing sends it straight back to open, however few
        # consecutive failures have accumulated — the probe *was* the evidence.
        if self.state == BreakerState.HALF_OPEN or failures >= self.threshold:
            return replace(
                self,
                state=BreakerState.OPEN,
                consecutive_failures=failures,
                opened_at=now,
            )
        return replace(self, consecutive_failures=failures)

    def advance(self, now: datetime) -> CircuitBreaker:
        """Move an open breaker to half-open once its cooldown has elapsed.

        The failure streak resets here: half-open is a fresh trial, and the
        count describes the *current* streak rather than the dependency's whole
        history. Carrying the old count over would also make the half-open
        branch in ``record_failure`` untestable, since the stale count would
        already exceed the threshold on its own.
        """
        if self.state == BreakerState.OPEN and self._cooled_down(now):
            return replace(self, state=BreakerState.HALF_OPEN, consecutive_failures=0)
        return self

    def _cooled_down(self, now: datetime) -> bool:
        if self.opened_at is None:
            return True
        return now - self.opened_at >= self.cooldown

    def as_payload(self) -> dict[str, Any]:
        return {
            "dependency": self.dependency,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "threshold": self.threshold,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
        }
