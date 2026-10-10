#!/usr/bin/env python3
"""Turn the availability time series into the TradFi tier decision.

The question `tradfi-tier-narrow` is waiting on
------------------------------------------------
The 30% TradFi sleeve was picked off DISK history (share of daily bars with a real
print). Measured against the venue on 2026-10-10, 12 of its 13 symbols answered
with ZERO bars on 1d/1h/15m while keeping 84+ bars on disk, and 11 had a bid with
no ask. That left two very different readings, and only a time series can tell them
apart:

  * **session-limited** - data appears during US market hours and stops outside it.
    The tier is right; the entry rule needs a session filter.
  * **dead** - no data at any hour. The tier is wrong; the pool should be narrowed.

`scripts/venue_availability.py` records one row per run with `us_session` on it, so
this reads those rows back and names which of the two it is, per symbol. It decides
nothing on its own: with only out-of-session runs recorded it says so instead of
guessing.

    python3 scripts/tradfi_tier.py
    python3 scripts/tradfi_tier.py --write data/universe/tradfi_tier.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def load_rows(path: Path) -> list[dict]:
    rows = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def has_bars(observation: dict) -> bool:
    return any(int(observation.get(key) or 0) > 0
               for key in ("bars_1d", "bars_1h", "bars_15m"))


def two_sided(observation: dict) -> bool:
    return bool(observation.get("bid")) and bool(observation.get("ask"))


def classify(observations: list[dict]) -> dict:
    """One symbol's verdict from every observation ever recorded for it.

    `tradable` deliberately needs BOTH halves (bars AND a two-sided book): a symbol
    the venue will not price is one the depth gate refuses anyway, so counting it as
    available would just move the refusal one step later.
    """

    def tradable(obs: dict) -> bool:
        return has_bars(obs) and two_sided(obs)

    in_session = [o for o in observations if o.get("us_session")]
    out_session = [o for o in observations if not o.get("us_session")]
    if any(tradable(o) for o in observations):
        verdict = "tradable" if any(tradable(o) for o in in_session) else "tradable_outside_session"
    elif any(has_bars(o) for o in observations):
        verdict = "bars_but_one_sided"
    else:
        verdict = "no_data"
    return {
        "observations": len(observations),
        "in_session_observations": len(in_session),
        "out_session_observations": len(out_session),
        "tradable_in_session": any(tradable(o) for o in in_session),
        "tradable_outside_session": any(tradable(o) for o in out_session),
        "max_bars_1h": max((int(o.get("bars_1h") or 0) for o in observations), default=0),
        "verdict": verdict,
    }


def summarise(history: list[dict]) -> dict:
    observations: dict[str, list[dict]] = {}
    order: list[str] = []
    for run in history:
        us_session = bool(run.get("us_session"))
        for row in run.get("rows") or []:
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            if symbol not in observations:
                observations[symbol] = []
                order.append(symbol)
            observations[symbol].append({**row, "us_session": us_session})
    symbols = {symbol: classify(observations[symbol]) for symbol in order}
    in_session_runs = sum(1 for run in history if run.get("us_session"))
    keep = [s for s, info in symbols.items() if info["tradable_in_session"]]
    outside_only = [s for s, info in symbols.items() if info["tradable_outside_session"]
                    and not info["tradable_in_session"]]
    return {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "runs": len(history),
        "in_session_runs": in_session_runs,
        "decided": in_session_runs > 0,
        "keep": keep,
        "tradable_only_outside_session": outside_only,
        "symbols": symbols,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="data/universe/availability.jsonl")
    ap.add_argument("--out", default="data/universe/tradfi_tier.json")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    history = load_rows(PROJECT / args.history)
    if not history:
        print(f"no runs in {args.history}")
        return 1
    report = summarise(history)

    print(f"观测：{report['runs']} 次运行，其中美股时段 {report['in_session_runs']} 次")
    print(f"{'symbol':16s}{'verdict':26s}{'1h bars':>9s}  观测(盘中/盘外)")
    for symbol, info in report["symbols"].items():
        print(f"{symbol:16s}{info['verdict']:26s}{info['max_bars_1h']:>9d}  "
              f"{info['in_session_observations']}/{info['out_session_observations']}")

    if not report["decided"]:
        print("\n结论：还缺『美股时段』的观测 —— 现在不能判定是『时段受限』还是『根本不供数据』。")
        print("      答案来自 com.tradingagent.tradfi-session（23:10 / 01:10 CST = 15:10 / 17:10 UTC，")
        print("      两者都在美股常规时段 13:30-20:00 UTC 内）；每日任务的两个槽位都在盘外，指望不上。")
    else:
        print(f"\n结论：盘中可交易 {len(report['keep'])} 个：{', '.join(report['keep']) or '（无）'}")
        if report["tradable_only_outside_session"]:
            print(f"      只在盘外可交易：{', '.join(report['tradable_only_outside_session'])}")
        narrow = [s for s, i in report["symbols"].items() if not i["tradable_in_session"]]
        print(f"      应收窄掉的 {len(narrow)} 个：{', '.join(narrow)}")

    if args.write:
        path = Path(args.out)
        if not path.is_absolute():
            path = PROJECT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {path}")
    else:
        print("DRY RUN - re-run with --write to store the verdict")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
