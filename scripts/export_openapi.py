"""Write the API's OpenAPI document to ``packages/contracts/openapi.json``.

The snapshot is committed. CI regenerates it and fails if the result differs,
which makes an accidental, undocumented contract change impossible to merge.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentrail_api.app import create_app
from agentrail_api.settings import ApiSettings

OUTPUT = Path(__file__).resolve().parents[1] / "packages" / "contracts" / "openapi.json"


def render() -> str:
    # Settings are pinned rather than read from the environment so the document
    # is identical on every machine.
    app = create_app(ApiSettings(_env_file=None, environment="ci"))
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    document = render()
    check_only = "--check" in sys.argv

    if check_only:
        if not OUTPUT.exists():
            print(f"{OUTPUT} is missing. Run: make contracts", file=sys.stderr)  # noqa: T201
            return 1
        if OUTPUT.read_text(encoding="utf-8") != document:
            print(  # noqa: T201
                f"{OUTPUT} is out of date. Run: make contracts",
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(document, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
