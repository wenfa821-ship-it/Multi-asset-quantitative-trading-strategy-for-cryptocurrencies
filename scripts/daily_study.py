#!/usr/bin/env python3
"""Does a DAILY version of the mainstream strategies survive recalibration?

README section 30 answered the naive question: moving 1h/15m strategies onto
daily bars collapses the cost line (about 1/60th of the fee spend per day) but
never turns the result positive, and the trend strategies stop trading entirely
because `retest_window_bars = 15` means "15 hours" on 1h and "15 days" on daily.

This script does the follow-up: keep the conventional indicator periods in BARS
(EMA20/100, RSI14, ATR14 mean the same thing on any timeframe) but re-express the
parameters that actually encode TIME - the retest window, the max holding period,
the confirm-frame slope lookback - in days. It also drops the higher-timeframe
confirm entirely in half the variants, because moving the confirm frame from 4h
to 1w turns "EMA50 = 8 days" into "EMA50 = 350 days", which is not the same filter.

It reports EVERY variant, not the best one. Picking the maximum of a grid this
small is how a backtest turns into a story, so the table is the deliverable and
no variant is promoted to a config here.

Universe: the WHOLE configured pool (all 20 spot symbols), because that is what
the live feed replays. An earlier version of this study hand-picked "the symbols
with enough history" - which changes the question being asked and quietly
inflates the sample, since the replay window is the intersection across the pool
and the youngest listing (TIA, 462 daily bars) is what actually bounds it. The
per-strategy scope still comes from each strategy's own symbol list, so
mean_reversion stays on majors only, exactly as configured.

    python3 scripts/daily_study.py
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.backtest import run_backtest  # noqa: E402
from ta.config import Config  # noqa: E402
from ta.history import load_frames  # noqa: E402
from ta.strategies import build_strategy  # noqa: E402

FULL = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "OKB/USDT", "DOGE/USDT", "XRP/USDT",
        "ADA/USDT", "AVAX/USDT", "LINK/USDT", "NEAR/USDT"]

KEEP = FULL + ["INJ/USDT", "ONDO/USDT", "W/USDT"]

GRIDS = {
    # retest window in DAYS (1h version used 15 bars = 15 hours), confirm on/off
    "trend_breakout": [
        {"retest_window_bars": r, "confirm_timeframe": c}
        for c in ("1w", "")
        for r in (0, 1, 3)
    ],
    # time stop in DAYS (15m version used 64 bars = 16 hours)
    "trend_pullback": [
        {"max_holding_bars": h, "confirm_timeframe": c, "confirm_slope_lookback": 1}
        for c in ("1w", "")
        for h in (0, 2, 8)
    ],
    "mean_reversion": [{"confirm_timeframe": c} for c in ("1w", "")],
}

_OLD_KEEP = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "OKB/USDT",
    "DOGE/USDT", "XRP/USDT", "ADA/USDT", "AVAX/USDT",
    "LINK/USDT", "NEAR/USDT", "INJ/USDT", "ONDO/USDT", "W/USDT",
]


def run(config: Config, name: str, overrides: dict, frames, cut_ts: float):
    spec = config.strategy(name)
    params = {**spec.params, **overrides}
    spec = dataclasses.replace(spec, params=params)
    # The strategy keeps its OWN scope (mean_reversion is majors-only); only the
    # replay window is the whole-pool intersection.
    strategy = build_strategy(spec, config.risk, config.resolve_symbols(spec))
    base_tf = strategy.timeframe or config.universe.timeframe
    timeframes = [base_tf]
    for cand in (base_tf, getattr(strategy, "confirm_timeframe", "")):
        if cand and cand not in timeframes:
            timeframes.append(cand)
    result = run_backtest(
        strategy,
        frames,
        config.risk,
        initial_cash=10_000.0,
        fee_bps=10.0,
        slippage_bps=5.0,
        timeframe=base_tf,
        symbol_tiers=config.symbol_tiers,
        entry_after_ts=cut_ts,
    )
    return result


def sweep(config: Config, keep: list[str], label: str, fraction: float = 0.7) -> None:
    frames = load_frames(config.data_dir / "history", "okx_demo", keep, ["1d", "1w"])
    frames = {s: f for s, f in frames.items() if s in keep and "1d" in f}
    symbols = sorted(frames)
    # The replay runs on the UNION timeline since README 35, so the window is the
    # union too. It used to be the intersection, which is why a 482-symbol pool
    # (listing dates spanning years) collapsed to nothing.
    stamps: set[int] = set()
    for f in frames.values():
        stamps.update(b.ts for b in f["1d"])
    shared = sorted(stamps)
    cut_ts = shared[int(len(shared) * fraction)]
    oos_days = (shared[-1] - cut_ts) / 86_400_000
    print(f"\n########## {label}: {len(symbols)} symbols in the feed, shared {len(shared)} "
          f"daily bars, cut {fraction:.0%} -> OOS≈{oos_days:.0f} days ##########")

    for name, variants in GRIDS.items():
        print(f"=== {name} ===")
        print(f"  {'variant':44s} {'trades':>7s} {'ret%':>7s} {'PF':>7s} {'win%':>6s} "
              f"{'fees$':>7s} {'expo%':>6s}")
        for over in variants:
            label_v = " ".join(f"{k}={v!r}" for k, v in over.items())
            try:
                r = run(config, name, over, frames, cut_ts)
            except Exception as exc:  # noqa: BLE001
                print(f"  {label_v[:44]:44s} ERROR {type(exc).__name__}: {str(exc)[:40]}")
                continue
            print(f"  {label_v[:44]:44s} {r.trades:7d} {r.total_return_pct:7.2f} "
                  f"{r.profit_factor:7.2f} {r.win_rate_pct:6.1f} {r.fees_paid:7.2f} "
                  f"{r.exposure_pct:6.1f}")
        print()


def main() -> int:
    config = Config.load("config-daily2")
    # --universe all: every symbol that has daily history on disk (482 as of
    # 2026-10-08), not just the 20 the config trades. The whole point of the
    # backfill was to re-ask the daily question on a sample where a single
    # sub-period cannot carry the answer (README 39.5).
    import argparse as _ap

    parser = _ap.ArgumentParser()
    parser.add_argument("--universe", choices=("config", "all"), default="all")
    parser.add_argument("--quick", action="store_true",
                        help="one variant per strategy instead of the full grid")
    args = parser.parse_args()

    if args.universe == "all":
        directory = config.data_dir / "history" / "okx_demo"
        pool = sorted(
            p.name.split("__")[0].replace("-USDT_", "/USDT:", 1).replace("-", "/", 1)
            for p in directory.glob("*__1d.jsonl")
        )
        print(f"universe=ALL history ({len(pool)} symbols with daily bars)")
    else:
        pool = sorted(config.traded_symbols)
        print(f"universe=config ({len(pool)} symbols)")

    if args.quick:
        GRIDS.clear()
        GRIDS.update({
            "trend_breakout": [{"retest_window_bars": 0, "confirm_timeframe": ""}],
            "trend_pullback": [{"max_holding_bars": 0, "confirm_timeframe": ""}],
            "mean_reversion": [{"confirm_timeframe": "1w"}],
        })
    sweep(config, pool, "sample", fraction=0.70)
    sweep(config, pool, "same sample, later cut (period check)", fraction=0.85)
    return 0
    config = Config.load("config-daily2")
    frames = load_frames(config.data_dir / "history", "okx_demo", KEEP, ["1d", "1w"])
    frames = {s: f for s, f in frames.items() if s in KEEP and "1d" in f}
    symbols = sorted(frames)
    base = frames[symbols[0]]["1d"]
    cut_index = int(len(base) * 0.7)
    cut_ts = base[cut_index].ts
    oos_days = (base[-1].ts - cut_ts) / 86_400_000
    print(f"universe={len(symbols)} symbols  shared daily bars={len(base)}  "
          f"OOS≈{oos_days:.0f} days\n")

    grids = {
        # retest window in DAYS (1h version used 15 bars = 15 hours), confirm on/off
        "trend_breakout": [
            {"retest_window_bars": r, "confirm_timeframe": c}
            for c in ("1w", "")
            for r in (0, 1, 3)
        ],
        # time stop in DAYS (15m version used 64 bars = 16 hours)
        "trend_pullback": [
            {"max_holding_bars": h, "confirm_timeframe": c, "confirm_slope_lookback": 1}
            for c in ("1w", "")
            for h in (0, 2, 8)
        ],
        "mean_reversion": [
            {"confirm_timeframe": c} for c in ("1w", "")
        ],
    }

    for name, variants in grids.items():
        print(f"=== {name} ===")
        print(f"  {'variant':44s} {'trades':>7s} {'ret%':>7s} {'PF':>6s} {'win%':>6s} "
              f"{'fees$':>7s} {'expo%':>6s}")
        for over in variants:
            label = " ".join(f"{k}={v!r}" for k, v in over.items())
            try:
                r = run(config, name, symbols, over, frames, cut_ts)
            except Exception as exc:  # noqa: BLE001
                print(f"  {label[:44]:44s} ERROR {type(exc).__name__}: {str(exc)[:40]}")
                continue
            print(f"  {label[:44]:44s} {r.trades:7d} {r.total_return_pct:7.2f} "
                  f"{r.profit_factor:6.2f} {r.win_rate_pct:6.1f} {r.fees_paid:7.2f} "
                  f"{r.exposure_pct:6.1f}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
