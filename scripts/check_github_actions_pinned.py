"""Verify GitHub Actions are pinned to immutable commit SHAs.

Mutable action tags such as ``actions/checkout@v7`` can move. Workflows should
use the commit SHA in ``uses:`` and keep the source tag as an inline comment so
Dependabot/humans still have the intended release line.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

USES_RE = re.compile(
    r"^(?P<indent>\s*)(?:-\s*)?uses:\s*(?P<target>[^#\s]+)(?:\s*#\s*(?P<comment>.+))?$"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_COMMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@v[0-9][A-Za-z0-9_.-]*$")


def iter_workflow_files(root: Path) -> list[Path]:
    return sorted((root / ".github" / "workflows").glob("*.yml")) + sorted(
        (root / ".github" / "workflows").glob("*.yaml")
    )


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        match = USES_RE.match(line)
        if match is None:
            continue

        target = match.group("target")
        if target.startswith(("./", "docker://")):
            continue
        if "@" not in target:
            errors.append(f"{path}:{line_number}: action reference has no @ ref: {target}")
            continue

        action, ref = target.rsplit("@", 1)
        if not SHA_RE.fullmatch(ref):
            errors.append(f"{path}:{line_number}: pin {action} to a 40-character commit SHA")
            continue

        comment = match.group("comment") or ""
        if not TAG_COMMENT_RE.fullmatch(comment):
            errors.append(f"{path}:{line_number}: add source tag comment like '# {action}@v1'")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    errors: list[str] = []
    for path in iter_workflow_files(args.root):
        errors.extend(check_file(path))

    if errors:
        sys.stdout.write("GitHub Actions pinning check failed:\n")
        for error in errors:
            sys.stdout.write(f"- {error}\n")
        return 1

    sys.stdout.write("All GitHub Actions use immutable commit SHA pins.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
