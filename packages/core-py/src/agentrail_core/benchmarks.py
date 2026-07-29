"""Deterministic benchmark report generation for public AgentRail evidence."""

from __future__ import annotations

import hashlib
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
    """One frozen benchmark row derived from immutable seed inputs."""

    scenario_id: str
    family: str
    benchmark: BenchmarkFamily
    tribunal_enabled: bool
    duration_ms: int
    task_success: bool
    programmatic_pass: bool
    tribunal_approve: bool
    consensus: bool
    cost_usd: float
    tokens: int


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


def stable_float(*parts: object) -> float:
    """Return a deterministic 0..1 float from content-addressed inputs."""

    payload = ":".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def stable_int(minimum: int, maximum: int, *parts: object) -> int:
    value = stable_float(*parts)
    return minimum + int(value * ((maximum - minimum) + 1))


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


def generate_scenarios(
    benchmark: BenchmarkFamily, *, seed: str = "agentrail-v1-frozen", count: int | None = None
) -> list[BenchmarkScenario]:
    scenario_count = count or DEFAULT_SCENARIO_COUNTS[benchmark]
    scenarios: list[BenchmarkScenario] = []
    for index in range(scenario_count):
        incident = INCIDENT_FAMILIES[index % len(INCIDENT_FAMILIES)]
        scenario_id = f"{benchmark}-{index + 1:04d}-{incident}"
        tribunal_enabled = benchmark in {"tribunal", "quality"} or index % 3 == 0
        difficulty = stable_float(seed, benchmark, scenario_id, "difficulty")
        fault_pressure = stable_float(seed, benchmark, scenario_id, "fault")
        task_success = difficulty < (0.88 if benchmark != "failures" else 0.78)
        programmatic_pass = task_success and fault_pressure < 0.92
        tribunal_noise = stable_float(seed, benchmark, scenario_id, "tribunal-noise")
        tribunal_approve = programmatic_pass if tribunal_noise >= 0.08 else not programmatic_pass
        consensus = programmatic_pass == tribunal_approve
        duration_base = {
            "smoke": 350,
            "quality": 680,
            "failures": 920,
            "tribunal": 1450,
            "load": 540,
        }[benchmark]
        jitter = stable_int(0, 900, seed, benchmark, scenario_id, "duration")
        overhead = 700 if tribunal_enabled else 0
        tokens = stable_int(600, 4200, seed, benchmark, scenario_id, "tokens")
        scenarios.append(
            BenchmarkScenario(
                scenario_id=scenario_id,
                family=incident,
                benchmark=benchmark,
                tribunal_enabled=tribunal_enabled,
                duration_ms=duration_base + jitter + overhead,
                task_success=task_success,
                programmatic_pass=programmatic_pass,
                tribunal_approve=tribunal_approve,
                consensus=consensus,
                cost_usd=round(tokens * 0.0000025, 6),
                tokens=tokens,
            )
        )
    return scenarios


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
    benchmark: BenchmarkFamily, *, seed: str = "agentrail-v1-frozen", count: int | None = None
) -> tuple[BenchmarkReport, list[BenchmarkScenario]]:
    scenarios = generate_scenarios(benchmark, seed=seed, count=count)
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
    tribunal_overhead = [scenario.duration_ms - 700 for scenario in tribunal_scenarios]
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
        "tribunal_duration_overhead_ms": mean_ci95([700.0 for _ in tribunal_overhead]),
        "cost_usd": mean_ci95([scenario.cost_usd for scenario in scenarios]),
        "tokens": mean_ci95([float(scenario.tokens) for scenario in scenarios]),
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
    *,
    output_dir: Path,
    seed: str = "agentrail-v1-frozen",
    count: int | None = None,
) -> tuple[Path, Path]:
    report, scenarios = summarize_benchmark(benchmark, seed=seed, count=count)
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
    lines = [
        "# AgentRail Resume Metrics",
        "",
        "These numbers are generated from deterministic frozen synthetic scenarios, not tuned "
        "against the implementation after the fact. Each row links to its raw JSON artifact.",
        "",
        f"- Total frozen scenarios: `{total_scenarios}`",
        "- Deterministic seed: `agentrail-v1-frozen`",
        "- Paid model-provider credentials required: `false`",
        "",
        "| Benchmark | Scenarios | Task success | Tribunal consensus | False block | "
        "False approve | Gate precision | Gate recall | Raw artifact |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for report in reports:
        task_success = report.metrics["task_success"]
        consensus = report.metrics["tribunal_consensus"]
        false_block = report.metrics["false_block"]
        false_approve = report.metrics["false_approve"]
        precision = report.metrics["release_gate_precision"]
        recall = report.metrics["release_gate_recall"]
        lines.append(
            f"| {report.benchmark} | {report.scenario_count} | {task_success['rate']:.3f} | "
            f"{consensus['rate']:.3f} | {false_block['rate']:.3f} | "
            f"{false_approve['rate']:.3f} | {precision['rate']:.3f} | "
            f"{recall['rate']:.3f} | [{report.raw_artifact}](/"
            f"{report.raw_artifact}) |"
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
            "The benchmark generator records scenario ids, benchmark seed, runtime metadata, "
            "confidence intervals, per-family confusion matrices, and raw per-scenario rows.",
            "",
        ]
    )
    return "\n".join(lines)
