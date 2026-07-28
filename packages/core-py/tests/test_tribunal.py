"""Deterministic Tribunal decisions."""

from __future__ import annotations

from agentrail_core.tribunal import (
    TribunalAgentRole,
    TribunalVerdictOutcome,
    decide_tribunal,
)

RUN = {
    "id": "01KTRIBUNAL00000000000000",
    "project_id": "01KPROJECT000000000000000",
    "state": "PASSED",
    "item_count": 16,
    "completed_count": 16,
    "failed_count": 0,
    "summary": {},
}


def comparison(*, pass_rate: float = 1.0, reproducible: bool = True) -> dict[str, object]:
    return {
        "id": "01KREPORT0000000000000000",
        "summary": {
            "pass_rate": pass_rate,
            "regression_count": 0,
            "reproducible": reproducible,
        },
        "evaluator_metrics": {},
        "category_metrics": {},
        "regressions": [],
    }


def test_clean_reproducible_evidence_is_approved() -> None:
    verdict = decide_tribunal(run=RUN, comparison=comparison())

    assert verdict.outcome is TribunalVerdictOutcome.APPROVED
    assert verdict.summary["agent_count"] == 6
    assert {finding.agent_role for finding in verdict.findings} >= {
        TribunalAgentRole.PROSECUTOR,
        TribunalAgentRole.DEFENDER,
        TribunalAgentRole.AUDITOR,
        TribunalAgentRole.ECONOMIST,
        TribunalAgentRole.HISTORIAN,
    }


def test_auditor_blocker_overrides_defender_approval() -> None:
    verdict = decide_tribunal(run=RUN, comparison=comparison(reproducible=False))

    assert verdict.outcome is TribunalVerdictOutcome.BLOCKED
    assert verdict.dissent["defender_supported_approval"] is True
    assert verdict.dissent["auditor_blockers"] == 1


def test_quality_warning_becomes_conditional() -> None:
    verdict = decide_tribunal(run=RUN | {"failed_count": 1}, comparison=comparison(pass_rate=0.9))

    assert verdict.outcome is TribunalVerdictOutcome.CONDITIONAL
    assert verdict.summary["warning_count"] == 1
