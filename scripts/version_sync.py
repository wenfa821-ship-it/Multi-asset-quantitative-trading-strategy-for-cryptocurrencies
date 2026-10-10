#!/usr/bin/env python3
"""One version number, three places, checked automatically.

`VERSION` is the source of truth; `pyproject.toml` and `ta.__version__` must agree
with it. They did not: measured 2026-10-10 the archive had reached v0.4.6 while
both still said 0.2.0, so the manifest of every frozen version recorded a package
version that had been stale for five releases. A version that is written down in
three places drifts exactly like the changelog status column did - unless a script
checks it, which is what this is.

    python3 scripts/version_sync.py            # report
    python3 scripts/version_sync.py --check    # exit 1 when they disagree
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def read_versions() -> dict[str, str]:
    version_file = PROJECT / "VERSION"
    pyproject = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    init = (PROJECT / "src" / "ta" / "__init__.py").read_text(encoding="utf-8")
    return {
        "VERSION": version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "",
        "pyproject.toml": (re.search(r'^version = "([^"]+)"', pyproject, flags=re.M) or [None, ""])[1],
        "src/ta/__init__.py": (re.search(r'^__version__ = "([^"]+)"', init, flags=re.M) or [None, ""])[1],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 when the three disagree")
    args = ap.parse_args()

    found = read_versions()
    for source, value in found.items():
        print(f"  {source:<22}{value or '(missing)'}")
    values = set(found.values())
    if len(values) == 1 and "" not in values:
        print("all three agree")
        return 0
    print("VERSION, pyproject.toml and ta.__version__ disagree - fix before freezing a version")
    return 1 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
