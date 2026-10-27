#!/usr/bin/env python3
"""Emit one GitHub Actions error annotation summarizing pytest failures."""

from __future__ import annotations

import sys
from pathlib import Path


def annotation_for(text: str) -> str:
    lines = text.splitlines()
    failed = [line for line in lines if line.startswith("FAILED ")]
    message = "\n".join(failed or lines[-80:])
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::error title=Pytest failures::{escaped}"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: ci_annotate_pytest.py <pytest-log>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"::error title=Pytest log unavailable::{exc}")
        return 1
    print(annotation_for(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
