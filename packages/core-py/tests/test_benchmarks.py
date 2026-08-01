"""Benchmark reporting over measured scenarios.

These tests once asserted that ``generate_scenarios`` was reproducible, which
was true and meaningless: it derived every outcome from a hash of the seed, so
reproducibility was a property of the hash rather than of the platform. That
generator is gone. What is left summarises measurements, and that is what these
tests check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentrail_core.benchmarks import (
    BenchmarkScenario,
    render_resume_metrics,
    summarize_benchmark,
    wilson_interval,
    write_benchmark_artifacts,
)


def measured(
    count: int,
    *,
    benchmark: str = "tribunal",
    passing: int | None = None,
    tribunal_enabled: bool = True,
) -> list[BenchmarkScenario]:
    """Scenarios shaped like ones a real run produces."""
    passing = count if passing is None else passing
    return [
        BenchmarkScenario(
            scenario_id=f"{benchmark}-run-{index:04d}",
            family="api_latency" if index % 2 == 0 else "queue_backlog",
            benchmark=benchmark,  # type: ignore[arg-type]
            tribunal_enabled=tribunal_enabled,
            duration_ms=10 + index,
            task_success=index < passing,
            programmatic_pass=index < passing,
            tribunal_approve=index < passing,
            consensus=True,
        )
        for index in range(count)
    ]


def test_a_summary_counts_what_it_was_given() -> None:
    report, scenarios = summarize_benchmark("tribunal", measured(64), seed="fixed")

    assert report.scenario_count == 64
    assert len(scenarios) == 64
    assert report.metrics["tribunal_consensus"]["denominator"] == 64
    assert report.metrics["false_block"]["denominator"] == 64
    assert "release_gate_precision" in report.metrics
    assert set(report.confusion_by_family["api_latency"]) == {
        "true_pass",
        "true_block",
        "false_block",
        "false_approve",
    }


def test_latency_percentiles_come_from_the_measurements() -> None:
    report, _ = summarize_benchmark("smoke", measured(100, benchmark="smoke"), seed="fixed")

    # durations are 10..109, so the percentiles must land inside that range and
    # in order. A reported latency that ignored its input is the failure this
    # whole rewrite exists to prevent.
    assert 10 <= report.metrics["duration_p50_ms"] <= 109
    assert report.metrics["duration_p50_ms"] <= report.metrics["duration_ms"]["p95"]
    assert report.metrics["duration_ms"]["p95"] <= report.metrics["duration_p99_ms"]


def test_failures_are_reported_rather_than_smoothed() -> None:
    report, _ = summarize_benchmark("failures", measured(20, passing=15), seed="fixed")

    assert report.metrics["task_success"]["rate"] == pytest.approx(0.75)
    assert report.metrics["task_success"]["numerator"] == 15


def test_unmeasurable_figures_are_absent() -> None:
    report, _ = summarize_benchmark("smoke", measured(8, benchmark="smoke"), seed="fixed")

    # The recorded executor makes no model calls, so there is nothing to count.
    # Publishing a token or cost figure would be publishing an assumption.
    assert "cost_usd" not in report.metrics
    assert "tokens" not in report.metrics


def test_an_empty_measurement_set_is_refused() -> None:
    # Summarising nothing used to be impossible because the data was invented.
    # Now it means the run did not happen, and saying so beats reporting zeroes.
    with pytest.raises(ValueError, match="no measured scenarios"):
        summarize_benchmark("smoke", [], seed="fixed")


def test_wilson_interval_bounds_rate() -> None:
    metric = wilson_interval(88, 100)

    assert metric.rate == pytest.approx(0.88)
    assert 0.0 <= metric.ci95_low <= metric.rate <= metric.ci95_high <= 1.0


def test_benchmark_artifacts_are_written(tmp_path: Path) -> None:
    json_path, markdown_path = write_benchmark_artifacts(
        "smoke", measured(12, benchmark="smoke"), output_dir=tmp_path, seed="fixed"
    )

    payload = json.loads(json_path.read_text())
    assert payload["scenario_count"] == 12
    assert len(payload["scenarios"]) == 12
    assert markdown_path.read_text().startswith("#")


def test_resume_metrics_links_to_raw_artifacts() -> None:
    report, _ = summarize_benchmark("smoke", measured(8, benchmark="smoke"), seed="fixed")

    rendered = render_resume_metrics([report])

    assert "smoke" in rendered
    assert "docs/benchmarks/artifacts/smoke-fixed.json" in rendered
