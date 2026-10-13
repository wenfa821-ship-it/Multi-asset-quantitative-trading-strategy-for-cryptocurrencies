#!/usr/bin/env python3
"""One entry point for the daily read-only research jobs.

Wired into the com.tradingagent.newlisting launchd job so that adding a
research track never means adding another service.

    1) new-listing accumulator -> data/new_listings/records.jsonl  (README 18)
    2) realised-vs-implied vol  -> data/vol/rv_iv.jsonl            (README 19)
    3) options IV surface       -> data/options/surface.json       (live venue feed)
    4) options cost wall        -> data/options/cost_history.jsonl (README 34, 1 row/day)
    5) funding settlements      -> appended to data/ledger.okx_demo.jsonl (README 50)

Steps 3 and 4 are the options leg's data layer, in that order: the cost step
reads the surface file the first step just wrote. Both are read-only public
fetches. They run here rather than in a service of their own because a daily
snapshot is all the sample they need - and a second launchd job to accumulate
one file a day is how this project already collected three of them.

Step 5 is the one exception to the importlib rule above: it needs ccxt, which the
launchd interpreter does not have, so it is handed to `VENV_PYTHON` as a
subprocess. Leaving it in-process would have failed every morning - the same shape
as the httpx bug in README 45.2.

WHY PYTHON AND NOT A SHELL SCRIPT
---------------------------------
The first version of this was `daily_research.sh` invoked as
`/bin/zsh <abs path>/scripts/daily_research.sh`, and launchd returned 127 with

    /bin/zsh: can't open input file: .../scripts/daily_research.sh

even though the path was absolute and correct (plutil-verified) and the file was
present and executable. Under launchd, /bin/zsh could not read a file in this
directory while the same job launched as /opt/anaconda3/bin/python3 could read
the very same tree - a per-executable permission difference, not a path bug.
Rather than fight it, the orchestrator is Python, because that is the
interpreter this job can demonstrably read the project with.

Exit 0 unless BOTH steps fail, so one bad network moment does not paint the job
red and hide which half actually worked. Both steps are idempotent.

AND WHY EVERY STEP IS RETRIED
-----------------------------
2026-10-10 09:10: five of these ten steps came back rc != 0 with
`SSL: UNEXPECTED_EOF_WHILE_READING` - the same upstream proxy drop that had
already wiped out the 10-09 run (see the trading loop's own `venue call failed`
lines in the same window). Nothing retried them, so the run was simply lost and
had to be re-run by hand, twice in two days. The "options IV-RV needs 5-7 days of
fresh samples" gate in the 1.1 plan depends on this job, so a silent lost day is
not cosmetic. Every step therefore gets a bounded retry (3 attempts, 60s apart by
default, both overridable), and whatever is still failing at the end is written to
data/daily_job_status.json and printed as NEEDS RERUN instead of being buried in
an rc table.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PROJECT / "src"))


def run(module_name: str, argv: list[str]) -> int:
    mod = importlib.import_module(module_name)
    saved = sys.argv
    sys.argv = [module_name, *argv]
    try:
        return int(mod.main())
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv = saved


# A SECOND INTERPRETER, for steps that need third-party packages.
# The launchd job runs a bare python.org build with no ccxt and no httpx (see
# README 45.2 - that is exactly how the options track would have failed silently
# every morning). A step that talks to the venue therefore has to be handed to the
# interpreter that actually has the dependencies, rather than being skipped.
VENV_PYTHON = os.environ.get("TA_PYTHON") or "/opt/anaconda3/bin/python3"


def run_script(script: str, argv: list[str]) -> int:
    path = HERE / script
    if not Path(VENV_PYTHON).exists():
        print(f"{script}: skipped - {VENV_PYTHON} not found", flush=True)
        return 0
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [VENV_PYTHON, "-u", str(path), *argv], cwd=str(PROJECT), check=False
    )
    return int(proc.returncode)


# Bounded, because the failure mode we are fixing is "one proxy drop loses the
# whole day" - not "the venue is down for hours". Three tries spans two minutes,
# which is longer than the observed drops and short enough that the job still
# finishes well before the next one is due. The steps are idempotent (keyed by
# bill id / listing / merged writes), so a retry cannot double-count.
DEFAULT_ATTEMPTS = 3
DEFAULT_RETRY_SLEEP = 60.0

# What the job runs, in order, at module level so a test can walk the same list the
# job walks (2026-10-10: two of these steps had been added without the `.env`
# loader the others carry, and nothing noticed until a manual re-run).
INPROCESS_STEPS: tuple[tuple[str, list[str]], ...] = (
    ("new_listing_track", ["--max-age-days", "400"]),
    # 2026-10-13：**股票代币（instCategory=3）另立一本账**。原来只记 cat=1（真·加密新币），
    # 于是近 8 天 OKX 上线的 32 个交易对里、8 个已成熟的 X***（股票代币）**一个都没被记录** ——
    # 而那几个正是 TradFi 腿的池子成长候选（该腿的 13 个 symbol 定于 10-10，此后没再更新过）。
    # 窗口刻意只有 60 天：这本账要的是「最近在长什么」，不是把 400 天的股票代币全补一遍。
    ("new_listing_track", ["--categories", "3", "--max-age-days", "60",
                           "--store", "data/new_listings/tradfi_records.jsonl"]),
    ("vol_monitor", []),
    ("options_surface", []),
    ("options_cost", []),
)

SCRIPT_STEPS: tuple[tuple[str, list[str]], ...] = (
    # Funding is cash the book never recorded; it settles 3x/day on every open
    # swap. Idempotent by bill id, so a daily catch-up is enough.
    ("sync_funding.py", ["--yes"]),
    # Keep the funding RATE history growing for carry research. One page per perp
    # is ~3 days of new prints, so a daily run both tops up and (over several days)
    # completes the backfill this project could not finish in one pass - the proxy
    # drops about a third of the requests under load.
    ("fetch_funding_history.py", ["--yes", "--pages", "1"]),
    # ...and what the carry legs actually COLLECT. The rate history is what we rank
    # on; this is what the venue paid, and it is the only way to test the
    # 2026-10-09 prediction that the spike fades back to ~3 bps/day.
    ("funding_income.py", ["--write"]),
    # ...and the two of them side by side, which is the number a sizing decision
    # needs (2026-10-11, README 71). `income.jsonl` cannot answer it: its rows are
    # rolling windows, so adding them double-counts. This re-buckets the raw bills
    # by UTC day, divides by the market's own rate history, and drops any name
    # whose demo credit is an artefact - CRV's is 325x the market rate, ACT's 6x.
    # Exits 0 with the rate panel alone if the proxy is down, so it cannot make the
    # job flaky.
    ("carry_income_report.py", ["--write"]),
    # The progress table at the top of README.md is the single source of truth for
    # "what is next"; --check fails (rc=1) when it no longer matches
    # docs/progress.json, so drift shows up in this job's log instead of in a
    # confident wrong answer during the next盘点.
    ("progress.py", ["--check"]),
    # The TradFi leg is matched by a NAME PREFIX ("X"), and OKX keeps listing new
    # crypto in that namespace (XDP-USDT, 2026-09-28). Any such line starts drawing
    # on the 30% TradFi quota silently. This asks the live catalogue what each
    # X-prefixed demo line actually is and fails on a mismatch.
    ("leg_identity.py", ["--write", "data/universe/leg_identity.json"]),
    # Top up the daily bars the research reads. `ta data fetch` REPLACES the series,
    # so nothing was ever refreshed and every 1d file had gone four days stale by
    # 2026-10-09 - which froze the options sleeve's 30-day realized vol (its IV-RV
    # gate was reading a premium against a stale base). This merges instead, so it
    # cannot shrink history. It runs after the in-process steps, so options_cost
    # sees bars at most one day old, which is immaterial inside a 30-day window.
    ("refresh_daily_bars.py", ["--write"]),
    # Which symbols the venue ACTUALLY serves, twice a day. The 09:10 slot is
    # outside US market hours and 21:10 is inside them - the pair is what answers
    # "is the TradFi sleeve session-limited or dead?" (2026-10-10: 12 of its 13
    # symbols answered with zero bars on every timeframe while keeping 84+ bars on
    # disk, but that was measured with the US market closed).
    ("venue_availability.py", ["--write"]),
    # 1.2's detector half (2026-10-10): is any open carry pair persistently unpaid?
    # Offline, 0.1s, read-only against the venue - it turns "which pair stopped
    # paying" into a daily series instead of a memory. Acting on it is a separate
    # user-approved step; this only records what the rule WOULD do.
    ("carry_rotation.py", ["--write"]),
    # The NUMBERS half of the daily review, written to docs/reviews/<date>.md. It
    # reviews the PREVIOUS complete CST day (so the 09:10 run closes yesterday, and
    # the 21:10 run rewrites the same file - idempotent by construction). The prose
    # stays a judgement call: this only makes the arithmetic reproducible.
    ("daily_review.py", ["--write"]),
    # 1.1's gate (docs/options-sleeve-spec.md 4.1) evaluated daily instead of
    # remembered: RV(30d) - IV >= 1.5pp for 5 consecutive days. One row per day, so
    # the day the gate opens is a timestamp rather than someone's recollection.
    ("options_gate.py", ["--write"]),
    # The release hygiene checks (docs/RELEASING.md): the version number lives in
    # three files and the release notes are generated, so both are verified here
    # rather than remembered. A drift shows up as a failed step instead of as a
    # confident wrong answer during the next盘点.
    ("version_sync.py", ["--check"]),
    ("changelog_md.py", ["--check"]),
)


def run_with_retry(
    name: str,
    action,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    sleep_seconds: float = DEFAULT_RETRY_SLEEP,
    sleep=time.sleep,
) -> tuple[int, int]:
    """Run `action` until it succeeds or `attempts` is exhausted.

    Returns (return code, attempts used). A raised exception counts as a failed
    attempt, so a step that explodes on one interpreter state still gets the
    others instead of taking the whole job down with it.
    """
    tries = max(1, int(attempts))
    code = 1
    used = 0
    for attempt in range(1, tries + 1):
        used = attempt
        try:
            code = int(action() or 0)
        except SystemExit as exc:
            code = int(exc.code or 0)
        except Exception as exc:  # noqa: BLE001 - one track must not kill the rest
            print(f"{name}: attempt {attempt}/{tries} failed {type(exc).__name__}: {exc}", flush=True)
            code = 1
        if code == 0:
            return 0, used
        if attempt < tries:
            print(
                f"{name}: attempt {attempt}/{tries} rc={code} - retrying in {sleep_seconds:g}s",
                flush=True,
            )
            sleep(sleep_seconds)
    return code, used


def write_status(path: Path, codes: dict[str, int], attempts_used: dict[str, int]) -> list[str]:
    """Persist what still needs a re-run so the dashboard can show it.

    A failed step used to be visible only to whoever read the launchd log. The
    file is best-effort: not being able to write it must not fail the job.
    """
    needs_rerun = [name for name, code in codes.items() if code != 0]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "needs_rerun": needs_rerun,
        "steps": {
            name: {"rc": code, "attempts": attempts_used.get(name, 1)}
            for name, code in codes.items()
        },
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"could not write {path}: {exc}", flush=True)
    return needs_rerun


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="daily read-only research job")
    parser.add_argument(
        "--attempts",
        type=int,
        default=int(os.environ.get("TA_DAILY_ATTEMPTS", DEFAULT_ATTEMPTS)),
        help="tries per step before it counts as failed",
    )
    parser.add_argument(
        "--retry-sleep",
        type=float,
        default=float(os.environ.get("TA_DAILY_RETRY_SLEEP", DEFAULT_RETRY_SLEEP)),
        help="seconds between attempts",
    )
    parser.add_argument(
        "--status-file",
        default="data/daily_job_status.json",
        help="where to record the steps that still need a re-run",
    )
    parser.add_argument(
        "--rerun-failed",
        action="store_true",
        help="run only the steps the status file lists as needing a re-run",
    )
    args = parser.parse_args(argv)
    status_path = Path(args.status_file)
    if not status_path.is_absolute():
        status_path = PROJECT / status_path
    steps, script_steps = INPROCESS_STEPS, SCRIPT_STEPS
    codes: dict[str, int] = {}
    attempts_used: dict[str, int] = {}
    if args.rerun_failed:
        # The "补跑" the retry change promised: after a night like 2026-10-10 (the
        # 21:10 run left `new_listing_track` failing through a proxy storm) the point
        # of naming a failure is being able to act on exactly that list, instead of
        # re-running 16 minutes of work. Every step is idempotent, and the status
        # file is seeded with the previous outcome so it still describes the whole
        # run afterwards rather than only the part that ran again.
        try:
            previous = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"{status_path} unreadable - nothing to re-run")
            return 0
        wanted = [str(name) for name in (previous.get("needs_rerun") or [])]
        if not wanted:
            print("status file lists no failing steps - nothing to re-run")
            return 0
        codes = {
            str(name): int((info or {}).get("rc") or 0)
            for name, info in (previous.get("steps") or {}).items()
        }
        steps = tuple(step for step in INPROCESS_STEPS if step[0] in wanted)
        script_steps = tuple(step for step in SCRIPT_STEPS if step[0] in wanted)
        print(f"re-running {len(steps) + len(script_steps)} step(s): {', '.join(wanted)}")
    for name, step_argv in steps:
        print(f"=== {name} ===", flush=True)
        codes[name], attempts_used[name] = run_with_retry(
            name,
            lambda n=name, a=step_argv: run(n, a),
            attempts=args.attempts,
            sleep_seconds=args.retry_sleep,
        )
    for script, step_argv in script_steps:
        print(f"=== {script} ===", flush=True)
        codes[script], attempts_used[script] = run_with_retry(
            script,
            lambda s=script, a=step_argv: run_script(s, a),
            attempts=args.attempts,
            sleep_seconds=args.retry_sleep,
        )

    print("=== summary ===")
    for name, code in codes.items():
        print(f"  {name:<20} rc={code} attempts={attempts_used.get(name, 1)}")
    status_path = Path(args.status_file)
    if not status_path.is_absolute():
        status_path = PROJECT / status_path
    needs_rerun = write_status(status_path, codes, attempts_used)
    if needs_rerun:
        print(f"NEEDS RERUN ({len(needs_rerun)}): " + ", ".join(needs_rerun))
    if all(code != 0 for code in codes.values()):
        print("both steps failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
