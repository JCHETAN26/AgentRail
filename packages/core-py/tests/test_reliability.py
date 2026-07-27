"""Budget accounting and circuit-breaker transitions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentrail_core.reliability import (
    BreakerState,
    BudgetExceededError,
    BudgetKind,
    BudgetLedger,
    CircuitBreaker,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def test_charging_within_the_limit_accumulates() -> None:
    ledger = BudgetLedger.create({"tool_calls": 3})

    ledger = ledger.charge(BudgetKind.TOOL_CALLS, 1).charge(BudgetKind.TOOL_CALLS, 1)

    assert ledger.spent[BudgetKind.TOOL_CALLS] == 2
    assert ledger.remaining(BudgetKind.TOOL_CALLS) == 1


def test_exhausting_a_budget_names_the_budget_that_broke() -> None:
    ledger = BudgetLedger.create({"tool_calls": 2})

    with pytest.raises(BudgetExceededError) as caught:
        ledger.charge(BudgetKind.TOOL_CALLS, 3)

    assert caught.value.kind == BudgetKind.TOOL_CALLS
    assert caught.value.limit == 2
    assert caught.value.spent == 3
    # The caller's own ledger is still pre-charge, so the overrun has to travel
    # on the exception or the recorded spend understates what was consumed.
    assert caught.value.ledger.spent[BudgetKind.TOOL_CALLS] == 3


def test_the_ledger_is_immutable_so_a_failed_charge_cannot_corrupt_it() -> None:
    ledger = BudgetLedger.create({"tokens": 10})

    charged = ledger.charge(BudgetKind.TOKENS, 4)

    assert ledger.spent[BudgetKind.TOKENS] == 0
    assert charged.spent[BudgetKind.TOKENS] == 4


def test_an_unknown_budget_name_is_inert_rather_than_fatal() -> None:
    ledger = BudgetLedger.create({"warp_core": 3, "tokens": 7})

    assert ledger.limits[BudgetKind.TOKENS] == 7
    assert BudgetKind.TOOL_CALLS in ledger.limits


def test_a_negative_charge_is_refused() -> None:
    with pytest.raises(ValueError):
        BudgetLedger.create().charge(BudgetKind.TOKENS, -1)


def test_breaker_opens_only_after_consecutive_failures() -> None:
    """One failure in a healthy stream is degradation, not an outage."""
    breaker = CircuitBreaker(dependency="cloudops-sandbox", threshold=3)

    breaker = breaker.record_failure(NOW).record_failure(NOW)
    assert breaker.state == BreakerState.CLOSED

    breaker = breaker.record_success(NOW)
    assert breaker.consecutive_failures == 0

    breaker = breaker.record_failure(NOW).record_failure(NOW)
    assert breaker.state == BreakerState.CLOSED

    breaker = breaker.record_failure(NOW)
    assert breaker.state == BreakerState.OPEN
    assert breaker.opened_at == NOW


def test_open_breaker_refuses_traffic_until_its_cooldown_elapses() -> None:
    breaker = CircuitBreaker(
        dependency="cloudops-sandbox", threshold=1, cooldown=timedelta(seconds=30)
    ).record_failure(NOW)

    assert breaker.allows(NOW) is False
    assert breaker.allows(NOW + timedelta(seconds=29)) is False
    assert breaker.allows(NOW + timedelta(seconds=30)) is True


def test_half_open_probe_closes_on_success() -> None:
    breaker = CircuitBreaker(
        dependency="cloudops-sandbox", threshold=1, cooldown=timedelta(seconds=30)
    ).record_failure(NOW)

    probing = breaker.advance(NOW + timedelta(seconds=31))
    assert probing.state == BreakerState.HALF_OPEN

    recovered = probing.record_success(NOW + timedelta(seconds=32))
    assert recovered.state == BreakerState.CLOSED
    assert recovered.consecutive_failures == 0


def test_half_open_probe_reopens_on_failure_without_waiting_for_the_threshold() -> None:
    """The probe was the evidence — one failure is enough to reopen.

    The breaker opens on its threshold, then half-opens with a *reset* streak,
    so the single probe failure below is nowhere near the threshold on its own.
    Only the half-open branch can reopen it.
    """
    breaker = CircuitBreaker(
        dependency="cloudops-sandbox", threshold=3, cooldown=timedelta(seconds=30)
    )
    for _ in range(3):
        breaker = breaker.record_failure(NOW)
    assert breaker.state == BreakerState.OPEN

    probing = breaker.advance(NOW + timedelta(seconds=31))
    assert probing.state == BreakerState.HALF_OPEN
    assert probing.consecutive_failures == 0

    reopened = probing.record_failure(NOW + timedelta(seconds=32))

    assert reopened.state == BreakerState.OPEN
    assert reopened.consecutive_failures == 1
    assert reopened.opened_at == NOW + timedelta(seconds=32)


def test_advance_leaves_a_breaker_alone_before_its_cooldown() -> None:
    breaker = CircuitBreaker(
        dependency="cloudops-sandbox", threshold=1, cooldown=timedelta(seconds=30)
    ).record_failure(NOW)

    assert breaker.advance(NOW + timedelta(seconds=5)).state == BreakerState.OPEN


def test_restore_carries_earlier_attempts_spend_forward() -> None:
    """A budget is per item, not per attempt.

    Starting each retry from zero would let two attempts of 1,000 tokens pass a
    1,500-token limit and then report only 1,000 spent.
    """
    first = BudgetLedger.create({"tokens": 1_500}).charge(BudgetKind.TOKENS, 1_000)

    second = BudgetLedger.restore({"tokens": 1_500}, first.as_payload())

    assert second.spent[BudgetKind.TOKENS] == 1_000
    assert second.remaining(BudgetKind.TOKENS) == 500
    with pytest.raises(BudgetExceededError):
        second.charge(BudgetKind.TOKENS, 1_000)


def test_restore_tolerates_missing_or_malformed_persisted_state() -> None:
    for persisted in (None, {}, {"spent": "nonsense"}, {"spent": {"warp_core": 3}}):
        ledger = BudgetLedger.restore({"tokens": 10}, persisted)
        assert ledger.spent[BudgetKind.TOKENS] == 0
