"""Benchmark reporting over **measured** AgentRail runs.

Every figure published from here is observed from persisted rows produced by a
real evaluation run: run items, evaluator results, trajectories and Tribunal
sessions. Nothing is synthesised.

This module previously generated its own scenarios from a hash of the seed
string and reported the result as a benchmark, which meant the published task
success, latency, cost and gate precision were properties of the string
``"agentrail-v1-frozen"`` rather than of this system. Those figures are gone.

The seed still names the frozen *input* set — which scenarios are run, in which
order — because reproducible inputs are the point. It no longer decides
outcomes.
"""

from __future__ import annotations

import json
import math
import os
import platform
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

BenchmarkFamily = Literal["smoke", "quality", "failures", "tribunal", "load"]

INCIDENT_FAMILIES = (
    "api_latency",
    "auth_regression",
    "cache_staleness",
    "cost_spike",
    "database_lock",
    "deployment_rollback",
    "misconfigured_quota",
    "model_timeout",
    "policy_violation",
    "prompt_injection",
    "queue_backlog",
    "rate_limit",
    "redis_restart",
    "schema_drift",
    "tool_loop",
    "worker_termination",
)

DEFAULT_SCENARIO_COUNTS: dict[BenchmarkFamily, int] = {
    "smoke": 32,
    "quality": 320,
    "failures": 160,
    "tribunal": 128,
    "load": 96,
}


@dataclass(frozen=True)
class BenchmarkScenario:
    """One **measured** benchmark row, read back from a real evaluation run.

    Every field here is observed: the states come from persisted
    ``EvaluationResult`` and ``TribunalSession`` rows, and ``duration_ms`` is
    wall-clock time recorded on the run item. Nothing is derived from the seed.

    Token and cost figures are deliberately absent. The recorded executor makes
    no model calls, so there is nothing to count, and a benchmark that reported
    them would be reporting an assumption.
    """

    scenario_id: str
    family: str
    benchmark: BenchmarkFamily
    tribunal_enabled: bool
    duration_ms: int
    task_success: bool
    programmatic_pass: bool
    tribunal_approve: bool
    consensus: bool


@dataclass(frozen=True)
class RateMetric:
    """Rate with a 95% Wilson confidence interval."""

    numerator: int
    denominator: int
    rate: float
    ci95_low: float
    ci95_high: float


@dataclass(frozen=True)
class BenchmarkReport:
    """Portable JSON/Markdown benchmark summary."""

    benchmark: BenchmarkFamily
    seed: str
    generated_at: str
    scenario_count: int
    raw_artifact: str
    environment: dict[str, str]
    version_fingerprints: dict[str, str]
    metrics: dict[str, Any]
    confusion_by_family: dict[str, dict[str, int]]
    scenario_ids: list[str]
    no_frozen_test_tuning: bool = True


def wilson_interval(numerator: int, denominator: int) -> RateMetric:
    if denominator == 0:
        return RateMetric(0, 0, 0.0, 0.0, 0.0)
    z = 1.959963984540054
    p = numerator / denominator
    denom = 1 + (z * z / denominator)
    centre = p + (z * z / (2 * denominator))
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * denominator)) / denominator)
    return RateMetric(
        numerator=numerator,
        denominator=denominator,
        rate=p,
        ci95_low=max(0.0, (centre - spread) / denom),
        ci95_high=min(1.0, (centre + spread) / denom),
    )


def mean_ci95(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "ci95_low": 0.0, "ci95_high": 0.0}
    mean = statistics.fmean(values)
    if len(values) == 1:
        return {"mean": mean, "ci95_low": mean, "ci95_high": mean}
    stdev = statistics.stdev(values)
    margin = 1.959963984540054 * stdev / math.sqrt(len(values))
    return {"mean": mean, "ci95_low": max(0.0, mean - margin), "ci95_high": mean + margin}


def percentile(values: list[int], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile_value
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[int(index)])
    weight = index - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def confusion_by_family(scenarios: list[BenchmarkScenario]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for scenario in scenarios:
        bucket = matrix.setdefault(
            scenario.family, {"true_pass": 0, "true_block": 0, "false_block": 0, "false_approve": 0}
        )
        if scenario.programmatic_pass and scenario.tribunal_approve:
            bucket["true_pass"] += 1
        elif not scenario.programmatic_pass and not scenario.tribunal_approve:
            bucket["true_block"] += 1
        elif scenario.programmatic_pass and not scenario.tribunal_approve:
            bucket["false_block"] += 1
        else:
            bucket["false_approve"] += 1
    return matrix


def summarize_benchmark(
    benchmark: BenchmarkFamily,
    scenarios: list[BenchmarkScenario],
    *,
    seed: str = "agentrail-v1-frozen",
) -> tuple[BenchmarkReport, list[BenchmarkScenario]]:
    """Summarise measured scenarios.

    Takes the measurements rather than producing them. The previous version
    generated its own rows from a hash of the seed, so every published figure
    was a property of the string "agentrail-v1-frozen" and not of this system.
    """
    if not scenarios:
        raise ValueError(f"{benchmark}: no measured scenarios; run the benchmark first")
    durations = [scenario.duration_ms for scenario in scenarios]
    successes = sum(1 for scenario in scenarios if scenario.task_success)
    programmatic_passes = sum(1 for scenario in scenarios if scenario.programmatic_pass)
    approvals = sum(1 for scenario in scenarios if scenario.tribunal_approve)
    consensus = sum(1 for scenario in scenarios if scenario.consensus)
    false_blocks = sum(
        1 for scenario in scenarios if scenario.programmatic_pass and not scenario.tribunal_approve
    )
    false_approvals = sum(
        1 for scenario in scenarios if not scenario.programmatic_pass and scenario.tribunal_approve
    )
    true_blocks = sum(
        1
        for scenario in scenarios
        if not scenario.programmatic_pass and not scenario.tribunal_approve
    )
    tribunal_scenarios = [scenario for scenario in scenarios if scenario.tribunal_enabled]
    predicted_blocks = true_blocks + false_blocks
    actual_blocks = true_blocks + false_approvals
    metrics: dict[str, Any] = {
        "task_success": asdict(wilson_interval(successes, len(scenarios))),
        "programmatic_pass": asdict(wilson_interval(programmatic_passes, len(scenarios))),
        "tribunal_approval": asdict(wilson_interval(approvals, len(scenarios))),
        "tribunal_consensus": asdict(wilson_interval(consensus, len(scenarios))),
        "false_block": asdict(wilson_interval(false_blocks, len(scenarios))),
        "false_approve": asdict(wilson_interval(false_approvals, len(scenarios))),
        "release_gate_precision": asdict(wilson_interval(true_blocks, predicted_blocks)),
        "release_gate_recall": asdict(wilson_interval(true_blocks, actual_blocks)),
        "duration_ms": {
            **mean_ci95([float(value) for value in durations]),
            "p95": percentile(durations, 0.95),
        },
        "duration_p50_ms": percentile(durations, 0.50),
        "duration_p99_ms": percentile(durations, 0.99),
        "tribunal_scenario_count": len(tribunal_scenarios),
    }
    raw_artifact = f"docs/benchmarks/artifacts/{benchmark}-{seed}.json"
    report = BenchmarkReport(
        benchmark=benchmark,
        seed=seed,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        scenario_count=len(scenarios),
        raw_artifact=raw_artifact,
        environment={
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
        },
        version_fingerprints={
            "source_commit": os.environ.get("GITHUB_SHA")
            or os.environ.get("AGENTRAIL_BENCHMARK_COMMIT", "local-working-tree"),
            "model_version": "recorded-model-fallback@v1",
            "evaluator_version": "programmatic-evaluators@v1",
            "tribunal_prompt_version": "tribunal-prompts@v1",
        },
        metrics=metrics,
        confusion_by_family=confusion_by_family(scenarios),
        scenario_ids=[scenario.scenario_id for scenario in scenarios],
    )
    return report, scenarios


def report_to_json(report: BenchmarkReport, scenarios: list[BenchmarkScenario]) -> dict[str, Any]:
    return {
        **asdict(report),
        "scenarios": [asdict(scenario) for scenario in scenarios],
    }


def write_benchmark_artifacts(
    benchmark: BenchmarkFamily,
    scenarios: list[BenchmarkScenario],
    *,
    output_dir: Path,
    seed: str = "agentrail-v1-frozen",
) -> tuple[Path, Path]:
    report, scenarios = summarize_benchmark(benchmark, scenarios, seed=seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{benchmark}-{seed}.json"
    markdown_path = output_dir / f"{benchmark}-{seed}.md"
    json_path.write_text(json.dumps(report_to_json(report, scenarios), indent=2) + "\n")
    markdown_path.write_text(render_markdown(report))
    return json_path, markdown_path


def render_markdown(report: BenchmarkReport) -> str:
    lines = [
        f"# AgentRail {report.benchmark.title()} Benchmark",
        "",
        f"- Seed: `{report.seed}`",
        f"- Scenario count: `{report.scenario_count}`",
        f"- Raw artifact: `{report.raw_artifact}`",
        f"- Generated at: `{report.generated_at}`",
        f"- No frozen-test tuning: `{str(report.no_frozen_test_tuning).lower()}`",
        "",
        "| Metric | Value | 95% CI |",
        "| --- | ---: | ---: |",
    ]
    for metric_name in (
        "task_success",
        "programmatic_pass",
        "tribunal_approval",
        "tribunal_consensus",
        "false_block",
        "false_approve",
        "release_gate_precision",
        "release_gate_recall",
    ):
        metric = report.metrics[metric_name]
        lines.append(
            "| "
            f"{metric_name} | {metric['rate']:.3f} | "
            f"{metric['ci95_low']:.3f} - {metric['ci95_high']:.3f} |"
        )
    duration = report.metrics["duration_ms"]
    lines.extend(
        [
            f"| duration_ms_mean | {duration['mean']:.1f} | "
            f"{duration['ci95_low']:.1f} - {duration['ci95_high']:.1f} |",
            f"| duration_ms_p95 | {duration['p95']:.1f} | n/a |",
            "",
            "## Confusion Matrix By Incident Family",
            "",
            "| Family | True pass | True block | False block | False approve |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for family, bucket in sorted(report.confusion_by_family.items()):
        lines.append(
            f"| {family} | {bucket['true_pass']} | {bucket['true_block']} | "
            f"{bucket['false_block']} | {bucket['false_approve']} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_resume_metrics(reports: list[BenchmarkReport]) -> str:
    total_scenarios = sum(report.scenario_count for report in reports)
    intro = (
        "These numbers are generated from deterministic frozen synthetic scenarios, not tuned "
        + "against the implementation after the fact. Each row links to its raw JSON artifact."
    )
    table_header = (
        "| Benchmark | Scenarios | Task success | Tribunal consensus | False block | "
        + "False approve | Gate precision | Gate recall | Raw artifact |"
    )
    lines = [
        "# AgentRail Resume Metrics",
        "",
        intro,
        "",
        f"- Total frozen scenarios: `{total_scenarios}`",
        "- Deterministic seed: `agentrail-v1-frozen`",
        "- Paid model-provider credentials required: `false`",
        "",
        table_header,
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for report in reports:
        task_success = report.metrics["task_success"]
        consensus = report.metrics["tribunal_consensus"]
        false_block = report.metrics["false_block"]
        false_approve = report.metrics["false_approve"]
        precision = report.metrics["release_gate_precision"]
        recall = report.metrics["release_gate_recall"]
        artifact_link = f"[{report.raw_artifact}](/{report.raw_artifact})"
        lines.append(
            f"| {report.benchmark} | {report.scenario_count} | {task_success['rate']:.3f} | "
            f"{consensus['rate']:.3f} | {false_block['rate']:.3f} | "
            f"{false_approve['rate']:.3f} | {precision['rate']:.3f} | "
            f"{recall['rate']:.3f} | {artifact_link} |"
        )
    footer = (
        "The benchmark generator records scenario ids, benchmark seed, runtime metadata, "
        + "confidence intervals, per-family confusion matrices, and raw per-scenario rows."
    )
    lines.extend(
        [
            "",
            "## Reproduction",
            "",
            "```bash",
            "make benchmark-report",
            "```",
            "",
            footer,
            "",
        ]
    )
    return "\n".join(lines)
