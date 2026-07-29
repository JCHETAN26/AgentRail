"""Run SLO evaluation."""

from agentrail_core.observability import SloStatus, evaluate_run_slo, render_prometheus_metrics


def test_run_slo_is_healthy_when_operational_metrics_clear_objectives() -> None:
    decision = evaluate_run_slo(
        {
            "quality": {"pass_rate": 0.99},
            "run": {"failed_count": 0},
            "reliability": {"stranded_count": 0},
            "budgets": {"spent": {"cost_micros": 100_000}},
            "canary": {"rollback_count": 0},
        }
    )

    assert decision.status == SloStatus.HEALTHY
    assert decision.violations == ()


def test_run_slo_names_every_violated_objective() -> None:
    decision = evaluate_run_slo(
        {
            "quality": {"pass_rate": 0.80},
            "run": {"failed_count": 2},
            "reliability": {"stranded_count": 1},
            "budgets": {"spent": {"cost_micros": 9_000_000}},
            "canary": {"rollback_count": 1},
        }
    )

    assert decision.status == SloStatus.VIOLATED
    assert len(decision.violations) == 5
    assert any("success_rate" in violation for violation in decision.violations)


def test_prometheus_metrics_escape_labels_and_report_readiness() -> None:
    payload = render_prometheus_metrics(
        service='api"one',
        version="1\n2",
        readiness={"postgresql": True, "redis": False},
    )

    assert 'agentrail_build_info{service="api\\"one",version="1\\n2"} 1' in payload
    assert 'dependency="postgresql"} 1' in payload
    assert 'dependency="redis"} 0' in payload
    assert payload.endswith("\n")
