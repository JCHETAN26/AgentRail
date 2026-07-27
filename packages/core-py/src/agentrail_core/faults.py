"""Deterministic fault profiles.

The fault families are the ones enumerated in ``BUILDPLAN.md`` section 15.
Selection is deterministic — by item index and attempt number, never by a random
draw — so a faulted run reproduces exactly like a clean one. A profile that
fired on item 7 attempt 1 fires there again on every replay of the same suite,
which is what makes a failure worth capturing in a trajectory at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FaultFamily(StrEnum):
    MODEL = "model"
    TOOL = "tool"
    PLATFORM = "platform"


class FaultKind(StrEnum):
    """Every fault this platform knows how to inject.

    Values are persisted in suite definitions and surfaced in trajectories, so
    they are part of the public contract and may not be renamed.
    """

    MODEL_TIMEOUT = "model.timeout"
    MODEL_RATE_LIMIT = "model.rate_limit"
    MODEL_MALFORMED_OUTPUT = "model.malformed_output"
    MODEL_REFUSAL = "model.refusal"
    MODEL_WRONG_TOOL = "model.wrong_tool"
    MODEL_INVALID_ARGUMENTS = "model.invalid_arguments"
    MODEL_TOOL_LOOP = "model.tool_loop"
    MODEL_PARTIAL_STREAM = "model.partial_stream"

    TOOL_LATENCY = "tool.latency"
    TOOL_TIMEOUT = "tool.timeout"
    TOOL_HTTP_500 = "tool.http_500"
    TOOL_MALFORMED_RESPONSE = "tool.malformed_response"
    TOOL_STALE_DATA = "tool.stale_data"
    TOOL_RATE_LIMIT = "tool.rate_limit"
    TOOL_DEPENDENCY_UNAVAILABLE = "tool.dependency_unavailable"

    PLATFORM_DUPLICATE_DELIVERY = "platform.duplicate_delivery"
    PLATFORM_DELAYED_EVENT = "platform.delayed_event"
    PLATFORM_WORKER_TERMINATION = "platform.worker_termination"
    PLATFORM_LEASE_EXPIRY = "platform.lease_expiry"
    PLATFORM_REDIS_RESTART = "platform.redis_restart"
    PLATFORM_POSTGRES_TRANSIENT = "platform.postgres_transient"
    PLATFORM_OBJECT_STORE_FAILURE = "platform.object_store_failure"
    PLATFORM_ANALYTICS_OUTAGE = "platform.analytics_outage"


FAULT_FAMILIES: dict[FaultKind, FaultFamily] = {
    kind: FaultFamily(kind.value.split(".", 1)[0]) for kind in FaultKind
}

#: A fault is retryable when repeating the attempt could plausibly succeed:
#: something timed out, was throttled, or a dependency was briefly down.
#:
#: The rest are deterministic failures of the agent's own reasoning — a refusal,
#: the wrong tool, invalid arguments, a malformed answer. Those reproduce
#: identically on a second attempt, so retrying them burns the budget and hides
#: the finding. They go terminal on the first occurrence.
RETRYABLE_FAULTS: frozenset[FaultKind] = frozenset(
    {
        FaultKind.MODEL_TIMEOUT,
        FaultKind.MODEL_RATE_LIMIT,
        FaultKind.MODEL_PARTIAL_STREAM,
        FaultKind.TOOL_LATENCY,
        FaultKind.TOOL_TIMEOUT,
        FaultKind.TOOL_HTTP_500,
        FaultKind.TOOL_RATE_LIMIT,
        FaultKind.TOOL_DEPENDENCY_UNAVAILABLE,
        FaultKind.PLATFORM_DUPLICATE_DELIVERY,
        FaultKind.PLATFORM_DELAYED_EVENT,
        FaultKind.PLATFORM_WORKER_TERMINATION,
        FaultKind.PLATFORM_LEASE_EXPIRY,
        FaultKind.PLATFORM_REDIS_RESTART,
        FaultKind.PLATFORM_POSTGRES_TRANSIENT,
        FaultKind.PLATFORM_OBJECT_STORE_FAILURE,
        FaultKind.PLATFORM_ANALYTICS_OUTAGE,
    }
)


class FaultProfileError(ValueError):
    """A suite carried a fault profile that cannot be executed.

    Raised with the offending index and reason so the caller can point at the
    exact profile, in the same spirit as dataset validation reports.
    """

    def __init__(self, index: int, reason: str) -> None:
        super().__init__(f"fault_profiles[{index}]: {reason}")
        self.index = index
        self.reason = reason


@dataclass(frozen=True, slots=True)
class FaultProfile:
    """One declarative rule: inject ``kind`` into the items it selects."""

    kind: FaultKind
    #: Fire on every item whose index is a multiple of this. ``1`` means every
    #: item. Ignored when ``item_indexes`` is given.
    every_n: int = 1
    #: Fire only on these exact item indexes. Takes precedence over ``every_n``.
    item_indexes: tuple[int, ...] = ()
    #: Fire only on these 1-based attempt numbers. Empty means every attempt.
    #: ``(1,)`` is the interesting case: the first attempt fails and the retry
    #: succeeds, which is how recovery gets proven rather than asserted.
    attempts: tuple[int, ...] = ()
    #: Free-form, redaction-safe detail carried into the trajectory step.
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def family(self) -> FaultFamily:
        return FAULT_FAMILIES[self.kind]

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_FAULTS

    def matches(self, *, item_index: int, attempt: int) -> bool:
        if self.attempts and attempt not in self.attempts:
            return False
        if self.item_indexes:
            return item_index in self.item_indexes
        return item_index % self.every_n == 0


@dataclass(frozen=True, slots=True)
class InjectedFault:
    """The single fault chosen for one attempt at one item."""

    kind: FaultKind
    family: FaultFamily
    retryable: bool
    item_index: int
    attempt: int
    detail: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "family": self.family.value,
            "retryable": self.retryable,
            "item_index": self.item_index,
            "attempt": self.attempt,
            "detail": self.detail,
        }


def parse_fault_profiles(raw: list[dict[str, Any]] | None) -> tuple[FaultProfile, ...]:
    """Turn a suite's stored ``fault_profiles`` into executable rules.

    Suites have carried this column since Phase 4, but it was accepted as an
    untyped blob and only counted. Anything that cannot be executed is rejected
    here rather than silently ignored at run time, where a profile that never
    fires looks exactly like a profile that found no faults.
    """
    if not raw:
        return ()
    profiles: list[FaultProfile] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise FaultProfileError(index, "must be an object")
        raw_kind = entry.get("kind")
        if not isinstance(raw_kind, str):
            raise FaultProfileError(index, f"unknown fault kind {raw_kind!r}")
        try:
            kind = FaultKind(raw_kind)
        except ValueError:
            raise FaultProfileError(index, f"unknown fault kind {raw_kind!r}") from None
        every_n = entry.get("every_n", 1)
        if not isinstance(every_n, int) or isinstance(every_n, bool) or every_n < 1:
            raise FaultProfileError(index, "every_n must be an integer of at least 1")
        item_indexes = _int_tuple(entry.get("item_indexes"), index, "item_indexes", minimum=0)
        attempts = _int_tuple(entry.get("attempts"), index, "attempts", minimum=1)
        detail = entry.get("detail", {})
        if not isinstance(detail, dict):
            raise FaultProfileError(index, "detail must be an object")
        profiles.append(
            FaultProfile(
                kind=kind,
                every_n=every_n,
                item_indexes=item_indexes,
                attempts=attempts,
                detail=detail,
            )
        )
    return tuple(profiles)


def plan_fault(
    profiles: tuple[FaultProfile, ...], *, item_index: int, attempt: int
) -> InjectedFault | None:
    """Choose the fault for one attempt, or ``None`` to run clean.

    First match in declaration order wins. Order is the author's priority, so
    two overlapping profiles resolve predictably instead of by set iteration.
    """
    for profile in profiles:
        if profile.matches(item_index=item_index, attempt=attempt):
            return InjectedFault(
                kind=profile.kind,
                family=profile.family,
                retryable=profile.retryable,
                item_index=item_index,
                attempt=attempt,
                detail=dict(profile.detail),
            )
    return None


def _int_tuple(value: Any, index: int, field_name: str, *, minimum: int) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise FaultProfileError(index, f"{field_name} must be a list of integers")
    parsed: list[int] = []
    for element in value:
        if not isinstance(element, int) or isinstance(element, bool) or element < minimum:
            raise FaultProfileError(
                index, f"{field_name} entries must be integers of at least {minimum}"
            )
        parsed.append(element)
    return tuple(parsed)
