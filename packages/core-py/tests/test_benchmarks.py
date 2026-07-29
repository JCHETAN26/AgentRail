"""Deterministic benchmark evidence tests."""

from __future__ import annotations

import json
from pathlib import Path

from agentrail_core.benchmarks import (
    generate_scenarios,
    render_resume_metrics,
    summarize_benchmark,
    wilson_interval,
    write_benchmark_artifacts,
)


def test_benchmark_scenarios_are_reproducible() -> None:
    first = generate_scenarios("tribunal", seed="fixed", count=24)
    second = generate_scenarios("tribunal", seed="fixed", count=24)
    changed = generate_scenarios("tribunal", seed="different", count=24)

    assert first == second
    assert first != changed
    assert len({scenario.scenario_id for scenario in first}) == 24


def test_benchmark_summary_includes_tribunal_quality_metrics() -> None:
    report, scenarios = summarize_benchmark("tribunal", seed="fixed", count=64)

    assert report.scenario_count == 64
    assert len(scenarios) == 64
    assert report.no_frozen_test_tuning is True
    assert report.metrics["tribunal_consensus"]["denominator"] == 64
    assert report.metrics["false_block"]["denominator"] == 64
    assert report.metrics["false_approve"]["denominator"] == 64
    assert "release_gate_precision" in report.metrics
    assert "release_gate_recall" in report.metrics
    assert report.version_fingerprints["model_version"] == "recorded-model-fallback@v1"
    assert "p95" in report.metrics["duration_ms"]
    assert set(report.confusion_by_family["api_latency"]) == {
        "true_pass",
        "true_block",
        "false_block",
        "false_approve",
    }


def test_wilson_interval_bounds_rate() -> None:
    metric = wilson_interval(88, 100)

    assert metric.ci95_low < metric.rate < metric.ci95_high
    assert 0.0 <= metric.ci95_low <= 1.0
    assert 0.0 <= metric.ci95_high <= 1.0


def test_benchmark_artifacts_are_written(tmp_path: Path) -> None:
    json_path, markdown_path = write_benchmark_artifacts(
        "quality", output_dir=tmp_path, seed="fixed", count=16
    )

    payload = json.loads(json_path.read_text())
    markdown = markdown_path.read_text()

    assert payload["benchmark"] == "quality"
    assert payload["scenario_count"] == 16
    assert len(payload["scenarios"]) == 16
    assert "Confidence" not in markdown
    assert "95% CI" in markdown


def test_resume_metrics_links_to_raw_artifacts() -> None:
    quality, _quality_scenarios = summarize_benchmark("quality", seed="fixed", count=16)
    tribunal, _tribunal_scenarios = summarize_benchmark("tribunal", seed="fixed", count=16)

    markdown = render_resume_metrics([quality, tribunal])

    assert "Total frozen scenarios: `32`" in markdown
    assert "Gate precision" in markdown
    assert "docs/benchmarks/artifacts/quality-fixed.json" in markdown
    assert "docs/benchmarks/artifacts/tribunal-fixed.json" in markdown
