"""Canary deployment decisions."""

from agentrail_core.deployments import DeploymentDecision, evaluate_canary


def test_healthy_canary_promotes() -> None:
    decision = evaluate_canary(
        baseline={"error_rate": 0.005, "p95_latency_ms": 120, "cost_per_1k": 0.10},
        observed={
            "success_rate": 0.99,
            "error_rate": 0.006,
            "p95_latency_ms": 140,
            "cost_per_1k": 0.11,
        },
        thresholds={
            "min_success_rate": 0.95,
            "max_error_rate": 0.02,
            "max_p95_latency_delta_ms": 100,
            "max_cost_delta_per_1k": 0.05,
        },
    )

    assert decision.decision == DeploymentDecision.PROMOTE
    assert decision.reasons == ()
    assert decision.deltas["p95_latency_ms"] == 20


def test_degraded_canary_rolls_back_with_reasons() -> None:
    decision = evaluate_canary(
        baseline={"error_rate": 0.005, "p95_latency_ms": 120, "cost_per_1k": 0.10},
        observed={
            "success_rate": 0.90,
            "error_rate": 0.04,
            "p95_latency_ms": 280,
            "cost_per_1k": 0.20,
        },
        thresholds={
            "min_success_rate": 0.95,
            "max_error_rate": 0.02,
            "max_p95_latency_delta_ms": 100,
            "max_cost_delta_per_1k": 0.05,
        },
    )

    assert decision.decision == DeploymentDecision.ROLLBACK
    assert len(decision.reasons) == 4
    assert decision.deltas["error_rate"] == 0.035
