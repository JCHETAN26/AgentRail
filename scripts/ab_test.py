#!/usr/bin/env python
"""Run the A/B discrimination test against a live stack.

uv run agentrail-api &
uv run agentrail-worker &
uv run python scripts/ab_test.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agentrail_api.ab_harness import ABHarnessError, run_discrimination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--items", type=int, default=12)
    parser.add_argument("--controls", type=int, default=5)
    parser.add_argument("--out", type=Path, default=Path("docs/benchmarks/artifacts/ab.json"))
    args = parser.parse_args()

    try:
        report = asyncio.run(
            run_discrimination(api_url=args.api_url, item_count=args.items, controls=args.controls)
        )
    except ABHarnessError as failure:
        print(f"a/b test failed: {failure}")
        return 1

    summary = report.as_summary()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n")

    print(
        f"regressions blocked : {summary['regressions_blocked']}"
        f"/{summary['regressions_submitted']}"
        f"  ({summary['detection_rate']:.0%})"
    )
    print(
        f"false blocks        : {summary['controls_blocked']}"
        f"/{summary['controls_submitted']}"
        f"  ({summary['false_block_rate']:.0%})"
    )
    print(
        f"tribunal approved   : {summary['tribunal_approved_controls']}"
        f"/{summary['controls_submitted']} controls, "
        f"{summary['tribunal_approved_regressions']}"
        f"/{summary['regressions_submitted']} regressions"
    )
    print(f"verdict latency     : {summary['verdict_ms_median']}ms median")
    for arm in summary["arms"]:
        mark = "regressed" if arm["regressed"] else "control  "
        print(
            f"  {mark}  {arm['arm']:26} pass={arm['pass_rate']:.3f}  "
            f"gate={arm['gate']:8} tribunal={arm['tribunal']}"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
