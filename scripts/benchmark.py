#!/usr/bin/env python
"""Generate deterministic AgentRail benchmark artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from agentrail_core.benchmarks import (
    DEFAULT_SCENARIO_COUNTS,
    BenchmarkFamily,
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
    parser.add_argument("--out", type=Path, default=Path("docs/benchmarks/artifacts"))
    parser.add_argument(
        "--resume-metrics",
        type=Path,
        default=Path("docs/benchmarks/RESUME_METRICS.md"),
        help="Markdown summary written by the report command.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.benchmark == "report":
        reports = []
        for benchmark in BENCHMARKS:
            json_path, markdown_path = write_benchmark_artifacts(
                benchmark,
                output_dir=args.out,
                seed=args.seed,
                count=args.count or DEFAULT_SCENARIO_COUNTS[benchmark],
            )
            report, _scenarios = summarize_benchmark(
                benchmark,
                seed=args.seed,
                count=args.count or DEFAULT_SCENARIO_COUNTS[benchmark],
            )
            reports.append(report)
            print(f"wrote {json_path} and {markdown_path}")  # noqa: T201
        args.resume_metrics.parent.mkdir(parents=True, exist_ok=True)
        args.resume_metrics.write_text(render_resume_metrics(reports))
        print(f"wrote {args.resume_metrics}")  # noqa: T201
        return 0

    json_path, markdown_path = write_benchmark_artifacts(
        args.benchmark,
        output_dir=args.out,
        seed=args.seed,
        count=args.count,
    )
    print(f"wrote {json_path} and {markdown_path}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
