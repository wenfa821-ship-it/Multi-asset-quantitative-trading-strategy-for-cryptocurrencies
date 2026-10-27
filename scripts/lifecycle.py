#!/usr/bin/env python3
"""Trunk-based release and production lifecycle checks.

    python3 scripts/lifecycle.py preflight [--allow-dirty]
    python3 scripts/lifecycle.py verify [--allow-dirty-source]
    python3 scripts/lifecycle.py status
    python3 scripts/lifecycle.py rollback-plan

The lifecycle is deliberately environment-oriented rather than branch-oriented:
short-lived code branches, immutable release tags, one deployable trunk, and a
production identity recorded by the process itself.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta import __version__  # noqa: E402
from ta.config import Config  # noqa: E402
from ta.provenance import config_fingerprint, read_runtime_provenance  # noqa: E402


def _run(name: str, cmd: list[str], *, timeout: int = 900) -> bool:
    print(f"[{name}] {' '.join(cmd)}", flush=True)
    try:
        proc = subprocess.run(cmd, cwd=PROJECT, check=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[{name}] FAIL {type(exc).__name__}: {exc}", flush=True)
        return False
    if proc.returncode:
        print(f"[{name}] FAIL rc={proc.returncode}", flush=True)
        return False
    print(f"[{name}] PASS", flush=True)
    return True


def _git(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=PROJECT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _dirty() -> bool:
    return bool(_git("status", "--porcelain", "--untracked-files=normal"))


def preflight(args: argparse.Namespace) -> int:
    """Validate a clean, reproducible release candidate."""
    checks: list[bool] = []
    if _dirty() and not args.allow_dirty:
        print("[git-clean] FAIL worktree has uncommitted changes; commit first")
        return 1
    if _dirty():
        print("[git-clean] SKIP --allow-dirty")

    commands = [
        ("tests", [sys.executable, "-m", "pytest", "-q", "tests"]),
        ("version", [sys.executable, "scripts/version_sync.py", "--check"]),
        ("progress", [sys.executable, "scripts/progress.py", "--check"]),
        ("changelog", [sys.executable, "scripts/changelog_md.py", "--check"]),
        ("diff", ["git", "diff", "--check"]),
    ]
    for name, cmd in commands:
        checks.append(_run(name, cmd))
    return 0 if all(checks) else 1


def _metrics(url: str, timeout: float) -> dict[str, float]:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - loopback
        text = response.read().decode("utf-8")
    values: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, raw = line.partition(" ")
        try:
            values[name] = float(raw)
        except ValueError:
            continue
    return values


def _launchd_runner() -> tuple[str, int]:
    if sys.platform != "darwin":
        return "", 0
    label = f"gui/{os.getuid()}/com.tradingagent.runner"
    try:
        proc = subprocess.run(
            ["launchctl", "print", label],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "", 0
    state = "running" if re.search(r"^\s*state = running$", proc.stdout, re.M) else ""
    match = re.search(r"^\s*pid = (\d+)$", proc.stdout, re.M)
    return state, int(match.group(1)) if match else 0


def verify(args: argparse.Namespace) -> int:
    """Verify that the running process matches this checkout and is healthy."""
    config = Config.load(str(PROJECT / args.config_dir))
    provenance = read_runtime_provenance(config)
    problems: list[str] = []
    if not provenance.get("available"):
        problems.append(f"runtime provenance unavailable: {provenance.get('reason', '')}")
    else:
        if provenance.get("version") != __version__:
            problems.append(
                f"runtime version {provenance.get('version')} != checkout {__version__}"
            )
        config_hash, _ = config_fingerprint(Path(config.config_dir))
        if provenance.get("config_sha256") != config_hash:
            problems.append("running config fingerprint differs from checkout config")
        if provenance.get("git_dirty") and not args.allow_dirty_source:
            problems.append("running process was started from a dirty source tree")
        checkout_sha = _git("rev-parse", "HEAD")
        if provenance.get("git_sha") and provenance.get("git_sha") != checkout_sha:
            problems.append("running Git SHA differs from checkout HEAD")
        if not provenance.get("git_tag") and not args.allow_untagged:
            problems.append("running process is not on an exact release tag")

    state, pid = _launchd_runner()
    if state != "running" or pid <= 0:
        problems.append("launchd runner is not running")
    try:
        metrics = _metrics(args.metrics_url, args.timeout)
    except Exception as exc:  # noqa: BLE001 - report one concise lifecycle verdict
        metrics = {}
        problems.append(f"metrics unavailable: {type(exc).__name__}: {exc}")
    if metrics:
        if metrics.get("ta_ticks_total", 0) <= 0:
            problems.append("runner has not completed a tick")
        if metrics.get("ta_kill_switch", 1) != 0:
            problems.append("runner kill switch is active")

    print(f"version        {__version__}")
    if provenance.get("available"):
        print(f"deployed       v{provenance.get('version')}  "
              f"git={str(provenance.get('git_sha') or '')[:12]}  "
              f"tag={provenance.get('git_tag') or '-'}  "
              f"dirty={str(provenance.get('git_dirty')).lower()}  "
              f"build={provenance.get('build_id')}")
    print(f"launchd        state={state or '-'} pid={pid or '-'}")
    if metrics:
        print(f"metrics        ticks={metrics.get('ta_ticks_total', 0):g} "
              f"kill={metrics.get('ta_kill_switch', 0):g} "
              f"degraded={metrics.get('ta_venue_degraded', 0):g}")
    if problems:
        print("\nFAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nPASS")
    return 0


def status(_: argparse.Namespace) -> int:
    config = Config.load(str(PROJECT / "config"))
    provenance = read_runtime_provenance(config)
    config_hash, _ = config_fingerprint(Path(config.config_dir))
    print(f"checkout       v{__version__}  git={_git('rev-parse', 'HEAD')[:12] or '-'}  "
          f"dirty={str(_dirty()).lower()}")
    print(f"config         {config_hash[:16]}")
    if provenance.get("available"):
        print(f"runtime        v{provenance.get('version')}  "
              f"git={str(provenance.get('git_sha') or '')[:12]}  "
              f"tag={provenance.get('git_tag') or '-'}  "
              f"dirty={str(provenance.get('git_dirty')).lower()}  "
              f"build={provenance.get('build_id')}")
        print(f"started        {provenance.get('started_at')}  pid={provenance.get('pid')}")
    else:
        print(f"runtime        unavailable: {provenance.get('reason', '')}")
    tags = _git("tag", "--sort=-v:refname").splitlines()[:5]
    print(f"recent tags    {', '.join(tags) or '-'}")
    return 0


def rollback_plan(_: argparse.Namespace) -> int:
    tags = _git("tag", "--sort=-v:refname").splitlines()
    if len(tags) < 2:
        print("need at least two release tags for a rollback plan")
        return 1
    current = tags[0]
    previous = tags[1]
    print(f"current tag    {current}")
    print(f"rollback tag   {previous}")
    print("steps:")
    print(f"  1. confirm {previous} has a complete archive/versions/{previous}/manifest.json")
    print("  2. stop further deploys and inspect config/ledger compatibility")
    print(f"  3. check out/build {previous} in a clean worktree")
    print("  4. deploy the exact tag and restart through launchd")
    print("  5. run lifecycle.py verify; keep the failed release evidence")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="trading-agent release lifecycle")
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight", help="test and consistency gate before release")
    pre.add_argument("--allow-dirty", action="store_true")
    pre.set_defaults(func=preflight)
    ver = sub.add_parser("verify", help="verify the running deployment")
    ver.add_argument("--config-dir", default="config")
    ver.add_argument("--metrics-url", default="http://127.0.0.1:9101/metrics")
    ver.add_argument("--timeout", type=float, default=20.0)
    ver.add_argument("--allow-dirty-source", action="store_true")
    ver.add_argument("--allow-untagged", action="store_true")
    ver.set_defaults(func=verify)
    stat = sub.add_parser("status", help="show checkout/runtime provenance")
    stat.set_defaults(func=status)
    rollback = sub.add_parser("rollback-plan", help="print tag-based rollback checklist")
    rollback.set_defaults(func=rollback_plan)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
