#!/usr/bin/env python3
"""Does a six-dimension environment score actually separate forward returns?

The framework this borrows from claims a multi-dimension dashboard tells you how
much position to hold. That claim is testable without any new data: walk the
stored 1h bars, score the environment at each step using ONLY bars up to that
moment, then look at what the universe did over the next few hours.

Read it as a separation study, not a backtest:

  * one evaluation per `--step` bars (default 24 = daily), because consecutive
    hours overlap almost completely and would fake the sample size;
  * every symbol at that instant shares the same environment, so the rows are
    NOT independent - the honest comparison is between bucket MEANS, and a large
    n is not the same thing as a large sample (the lesson from README 26);
  * nothing is traded and no cost is charged; this only answers "is there
    anything there to size".

    python3 scripts/monitor_study.py --timeframe 1h --step 24 --horizons 4,24
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.history import read_bars  # noqa: E402
from ta.monitor import detect  # noqa: E402

LABELS = ("risk_on", "neutral", "cautious", "defensive", "risk_off")


def load_universe(directory: Path, timeframe: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for path in sorted(directory.glob(f"*-USDT__{timeframe}.jsonl")):
        symbol = path.name.split("__")[0].replace("-", "/", 1)
        bars = read_bars(path)
        if len(bars) > 300:
            out[symbol] = bars
    return out


def summarise(values: list[float]) -> str:
    if not values:
        return "n=0"
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
    wins = sum(1 for v in values if v > 0)
    return (
        f"n={len(values):5d}  median={st.median(values):+6.2f}%  mean={st.mean(values):+6.2f}%  "
        f"win={100.0 * wins / len(values):5.1f}%  p90={p90:+6.2f}%"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(PROJECT / "data" / "history" / "okx_demo"))
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--step", type=int, default=24, help="bars between evaluations")
    ap.add_argument("--horizons", default="4,24")
    ap.add_argument("--warmup", type=int, default=200)
    ap.add_argument("--start", type=int, default=0, help="first evaluation index")
    ap.add_argument("--end", type=int, default=0, help="last evaluation index (0 = end)")
    args = ap.parse_args()

    universe = load_universe(Path(args.dir), args.timeframe)
    if not universe:
        print("no history found")
        return 1
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]

    length = args.end or min(len(b) for b in universe.values())
    start = max(args.warmup, args.start)
    print(f"universe={len(universe)} symbols  bars={length} ({args.timeframe})")
    print(f"evaluating every {args.step} bars from index {start} "
          f"-> ~{max(0, (length - start) // args.step)} evaluations\n")

    # bucket label -> horizon -> forward returns (%)
    buckets: dict[str, dict[int, list[float]]] = {name: {h: [] for h in horizons} for name in LABELS}
    scores: dict[int, list[float]] = {h: [] for h in horizons}
    score_values: list[int] = []

    for idx in range(start, length - max(horizons), args.step):
        window = {s: bars[: idx + 1] for s, bars in universe.items()}
        state = detect(window)
        score_values.append(state.score)
        for h in horizons:
            moves = []
            for symbol, bars in universe.items():
                now = bars[idx].close
                later = bars[idx + h].close
                if now > 0:
                    moves.append(100.0 * (later / now - 1.0))
            buckets[state.label][h].extend(moves)
            scores[h].extend([(state.score, m) for m in moves])

    print("=== forward return by monitor label ===")
    for name in LABELS:
        row = buckets[name]
        if not row[horizons[0]]:
            print(f"{name:10s} (never fired)")
            continue
        print(f"{name:10s}")
        for h in horizons:
            print(f"    +{h:3d} bars  {summarise(row[h])}")

    print("\n=== the framework's own claim: >=3 red lights => reduce ===")
    for h in horizons:
        off = buckets["risk_off"][h]
        on = [v for name in ("risk_on", "neutral") for v in buckets[name][h]]
        print(f"  +{h:3d} bars  risk_off  {summarise(off)}")
        print(f"  +{h:3d} bars  risk_on/neutral  {summarise(on)}")

    print("\n=== monotonicity check: score band vs forward return ===")
    bands = [(6, 14), (15, 19), (20, 24), (25, 30)]
    for h in horizons:
        line = []
        for lo, hi in bands:
            vals = [m for s, m in scores[h] if lo <= s <= hi]
            line.append(f"{lo}-{hi}: {st.mean(vals):+.2f}% (n={len(vals)})" if vals else f"{lo}-{hi}: -")
        print(f"  +{h:3d} bars  " + " | ".join(line))

    if score_values:
        print(f"\nscore range observed: {min(score_values)}..{max(score_values)} "
              f"(mean {st.mean(score_values):.1f})")
    print("\nCaveat: at any instant every symbol shares one environment, so a bucket"
          "\nwith n=3000 is still ~150 independent days. Judge direction, not p-values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
