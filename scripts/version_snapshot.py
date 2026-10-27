#!/usr/bin/env python3
"""Freeze the current state as a VERSION, so later work happens inside one.

Why this exists
---------------
This project has no git history, and its artefacts (config, ledger, progress
table, README) change every round. That makes "what did the system look like when
we started tuning the strategies?" unanswerable - which is exactly the situation
you do NOT want when a strategy change later needs to be judged or reverted.

A version here is a small, self-describing snapshot:

    archive/versions/<version>/
        manifest.json      version, when, test count, runner pid, equity, gates
        NOTES.md           what this version IS, in prose
        progress.json      the path table as it stood
        config/*.toml      every config file, verbatim
        changelog.jsonl    the trace up to this point

It is not a backup system and it does not touch the running services. One call
creates one immutable directory; re-running for an existing version refuses
unless --force, because a version that can be silently rewritten is not a version.

    python3 scripts/version_snapshot.py --version v0.2.0 --notes "..."
    python3 scripts/version_snapshot.py --version v0.2.0 --list
"""

from __future__ import annotations

import argparse
import re
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta import __version__ as package_version  # noqa: E402
from ta.provenance import config_fingerprint, source_identity  # noqa: E402


def _equity_and_positions() -> dict:
    ledger = PROJECT / "data" / "ledger.okx_demo.jsonl"
    if not ledger.exists():
        return {}
    last = None
    with ledger.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("kind") == "equity":
                last = event
    if not last:
        return {}
    return {
        "equity": round(float(last.get("equity") or 0.0), 2),
        "cash": round(float(last.get("cash") or 0.0), 2),
        "positions": last.get("positions") or {},
        "as_of_ms": last.get("ts"),
    }


def _test_count() -> int | None:
    """Run the suite quietly. **A version whose tests do not pass is not a version.**

    2026-10-13 实测的两个毛病（当时差点把坏快照当版本发出去）：
    1. 原来只看输出里第一段数字 —— 一行 `1 failed, 399 passed` 会被读成 **tests=1**；
    2. 原来**不看返回码**，红着的树照样冻结，与这句 docstring 自相矛盾。
    现在：非零就 `SystemExit` 拒绝冻结；绿了才解析 `N passed`。
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests", "-p", "no:cacheprovider"],
            cwd=str(PROJECT), capture_output=True, text=True, timeout=600,
        )
    except Exception:  # noqa: BLE001 - a missing pytest must not block the archive
        return None
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout or "").strip().splitlines()[-12:])
        raise SystemExit("测试没过，**拒绝冻结版本**（先修绿再来）：\n" + tail)
    match = re.search(r"(\d+) passed", proc.stdout or "")
    return int(match.group(1)) if match else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    root = PROJECT / "archive" / "versions"
    if args.list:
        if not root.exists():
            print("no versions yet")
            return 0
        for path in sorted(root.iterdir()):
            manifest = path / "manifest.json"
            when = ""
            if manifest.exists():
                when = str((json.loads(manifest.read_text(encoding="utf-8")) or {}).get("created_at", ""))
            print(f"  {path.name:<12}{when}")
        return 0
    if not args.version:
        print("--version is required (or use --list)")
        return 2
    requested_version = args.version.removeprefix("v")
    if requested_version != package_version:
        print(
            f"refusing to freeze {args.version}: package version is {package_version}; "
            "update VERSION/pyproject/ta.__version__/README first"
        )
        return 1

    source = source_identity(PROJECT)
    if source["git_dirty"] and not args.allow_dirty:
        print(
            "refusing to freeze a dirty worktree: commit the exact source that this "
            "release claims to contain (use --allow-dirty only for forensic snapshots)"
        )
        return 1

    target = root / args.version
    if target.exists() and not args.force:
        print(f"{target} already exists - a version that can be rewritten is not a version")
        return 1
    # **先过测试闸门，再建目录**（2026-10-13 实测）：原来先建目录、最后才跑测试，
    # 于是一次被拒的冻结会留下**半个版本** —— 只有 changelog/config/progress、
    # 没有 manifest/NOTES/VERSION。目录一建出来，就得有人手工收拾。
    tests_passed = _test_count()
    target.mkdir(parents=True, exist_ok=True)

    config_dir = PROJECT / "config"
    (target / "config").mkdir(exist_ok=True)
    for path in sorted(config_dir.glob("*.toml")):
        shutil.copy2(path, target / "config" / path.name)
    for name in ("docs/progress.json", "docs/changelog.jsonl"):
        source = PROJECT / name
        if source.exists():
            shutil.copy2(source, target / Path(name).name)

    config_hash, config_files = config_fingerprint(PROJECT / "config")
    manifest = {
        "version": args.version,
        "package_version": package_version,
        "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "tests_passed": tests_passed,
        "source": source,
        "config_sha256": config_hash,
        "config_files": config_files,
        "account": _equity_and_positions(),
        "progress": json.loads((PROJECT / "docs" / "progress.json").read_text(encoding="utf-8")),
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (target / "NOTES.md").write_text(
        f"# {args.version}\n\n"
        f"Frozen {manifest['created_at']}. Package version {package_version}. "
        f"Tests: {manifest['tests_passed']}. Git: {source['git_sha'] or '-'} "
        f"({'dirty' if source['git_dirty'] else 'clean'}). "
        f"Config: {config_hash[:16]}.\n\n"
        f"{args.notes.strip()}\n\n"
        "Strategy changes after this point belong INSIDE this version: compare against\n"
        "its config/ and manifest.json rather than against memory.\n",
        encoding="utf-8",
    )
    print(f"frozen {args.version} -> {target}")
    print(f"  tests={manifest['tests_passed']}  equity={manifest['account'].get('equity')}  "
          f"rows={len(manifest['progress'].get('rows') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
