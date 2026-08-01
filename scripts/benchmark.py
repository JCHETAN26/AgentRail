#!/usr/bin/env python
"""Measure AgentRail by running it, and publish what was observed.

This drives real evaluation runs through the API, waits for the worker, and
reports the persisted outcomes. It needs a running stack:

    uv run agentrail-api &
    uv run agentrail-worker &
    uv run python scripts/benchmark.py report

That requirement is the point. The previous version needed nothing running
because it computed its results from a hash of the seed string, so every
published figure — task success, latency, gate precision — described that
string rather than this system.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from agentrail_api.benchmark_runner import BenchmarkRunError, run_benchmark
from agentrail_core.benchmarks import (
    DEFAULT_SCENARIO_COUNTS,
    INCIDENT_FAMILIES,
    BenchmarkFamily,
    BenchmarkReport,
    render_resume_metrics,
    summarize_benchmark,
    write_benchmark_artifacts,
)

BENCHMARKS: tuple[BenchmarkFamily, ...] = ("smoke", "quality", "failures", "tribunal", "load")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", choices=(*BENCHMARKS, "report"))
    parser.add_argument("--seed", default="agentrail-v1-frozen")
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--out", type=Path, default=Path("docs/benchmarks/artifacts"))
    parser.add_argument(
        "--resume-metrics",
        type=Path,
        default=Path("docs/benchmarks/RESUME_METRICS.md"),
        help="Markdown summary written by the report command.",
    )
    return parser.parse_args()


async def _measure_and_write(
    benchmark: BenchmarkFamily, args: argparse.Namespace
) -> tuple[Path, Path, BenchmarkReport]:
    scenarios = await run_benchmark(
        benchmark,
        api_url=args.api_url,
        item_count=args.count or DEFAULT_SCENARIO_COUNTS[benchmark],
        scenario_families=list(INCIDENT_FAMILIES),
    )
    json_path, markdown_path = write_benchmark_artifacts(
        benchmark, scenarios, output_dir=args.out, seed=args.seed
    )
    report, _ = summarize_benchmark(benchmark, scenarios, seed=args.seed)
    return json_path, markdown_path, report


async def _run(args: argparse.Namespace) -> int:
    targets: tuple[Any, ...] = BENCHMARKS if args.benchmark == "report" else (args.benchmark,)
    reports: list[BenchmarkReport] = []
    for benchmark in targets:
        json_path, markdown_path, report = await _measure_and_write(benchmark, args)
        reports.append(report)
        print(f"measured {benchmark}: wrote {json_path} and {markdown_path}")  # noqa: T201

    if args.benchmark == "report":
        args.resume_metrics.parent.mkdir(parents=True, exist_ok=True)
        args.resume_metrics.write_text(render_resume_metrics(reports))
        print(f"wrote {args.resume_metrics}")  # noqa: T201
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(_run(args))
    except BenchmarkRunError as failure:
        print(f"benchmark failed: {failure}")  # noqa: T201
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
