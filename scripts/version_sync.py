#!/usr/bin/env python3
"""One version number, FOUR places, checked automatically.

`VERSION` is the source of truth; `pyproject.toml` and `ta.__version__` must agree
with it. They did not: measured 2026-10-10 the archive had reached v0.4.6 while
both still said 0.2.0, so the manifest of every frozen version recorded a package
version that had been stale for five releases. A version that is written down in
three places drifts exactly like the changelog status column did - unless a script
checks it, which is what this is.

2026-10-14 - the fourth place was found the same way: README.md's own header said
**当前版本 v1.1.7** while VERSION said **1.1.22** - fifteen releases of drift, in
the first paragraph of the entry document, and nothing was checking it because the
"three places" list only ever covered code. `docs/RELEASING.md` step 4 always
required the README to be updated; now the script requires it too.

    python3 scripts/version_sync.py            # report
    python3 scripts/version_sync.py --check    # exit 1 when they disagree
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

# README 的版本出现在这一句里：`**当前版本 v1.1.22**（2026-10-14）。版本线：…`
# 只认 `当前版本` 后面那个 `v<数字点>`，免得把正文里别的版本号（如「1.0 的范围」）也抓进来。
README_VERSION = re.compile(r"\*\*当前版本\s*v([0-9][0-9.]*)\*\*")


def read_versions(project: Path | None = None) -> dict[str, str]:
    root = project or PROJECT
    version_file = root / "VERSION"
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    init = (root / "src" / "ta" / "__init__.py").read_text(encoding="utf-8")
    found = {
        "VERSION": version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "",
        "pyproject.toml": (re.search(r'^version = "([^"]+)"', pyproject, flags=re.M) or [None, ""])[1],
        "src/ta/__init__.py": (re.search(r'^__version__ = "([^"]+)"', init, flags=re.M) or [None, ""])[1],
    }
    readme_version = read_readme_version(root)
    if readme_version is not None:
        found["README.md"] = readme_version
    return found


def read_readme_version(project: Path | None = None) -> str | None:
    """README 头部那句「当前版本 vX」里的 X；没有这句话就返回 None（不加进对账）。

    返回 None 而不是 "" —— 「这句话被删了」与「这句话写错了」是两件事，前者不该把
    daily job 变成红轮（README 可能被重写过头部），后者必须拦下。
    """
    root = project or PROJECT
    try:
        text = (root / "README.md").read_text(encoding="utf-8")
    except OSError:
        return None
    match = README_VERSION.search(text)
    return match.group(1) if match else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 when they disagree")
    args = ap.parse_args()

    found = read_versions()
    for source, value in found.items():
        print(f"  {source:<22}{value or '(missing)'}")
    values = set(found.values())
    if len(values) == 1 and "" not in values:
        print(f"all {len(found)} agree")
        return 0
    print("VERSION, pyproject.toml, ta.__version__ and README.md disagree - "
          "fix before freezing a version")
    if read_readme_version() is None:
        print("（README.md 里没有『**当前版本 vX**』那句话，未参与对账 —— "
              "要么把它加回来，要么这次漂移就没人看得见）")
    return 1 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
