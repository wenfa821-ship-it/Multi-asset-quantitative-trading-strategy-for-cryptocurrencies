#!/usr/bin/env python3
"""Top up the daily bars the research scripts read, without truncating them.

The problem this closes
-----------------------
`ta data fetch` writes the series it just downloaded - it REPLACES the file. So
the only way to keep `data/history/okx_demo/*__1d.jsonl` current was to re-fetch
the whole window, and nobody did: measured 2026-10-09, every 1d file ended at
2026-10-05. The live runner is unaffected (it pulls bars from the venue), but the
research side is not:

  * `scripts/options_cost.py` computes the 30-day realized vol from those files,
    so its `variance_premium_pp` was IV minus a FROZEN RV - identical on the
    10-08 and 10-09 rows while IV moved. That is the number the options gate
    (path-5) is waiting on.
  * every backtest run on disk (`ta backtest --source history`) ended 4 days ago.

So this fetches a short recent window and MERGES it, which is the shape a daily
job needs: cheap, idempotent, and it cannot shrink history.

Credentials come from `.env` (README 50.4): the launchd unit exports the proxy but
not the API keys, so the venue is built without `OKX_DEMO_API_KEY` unless this
loads it. 2026-10-10: this step was the last one in the daily job failing with
"API key is not set" - the same omission `venue_availability.py` had, on the one
step whose whole job is keeping `options_cost`'s 30-day realized vol fresh.

    python3 scripts/refresh_daily_bars.py --write                # option underlyings
    python3 scripts/refresh_daily_bars.py --write --symbols BTC/USDT,ETH/USDT
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config, load_env_file  # noqa: E402
from ta.history import fetch_symbol, history_path, merge_bars, read_bars  # noqa: E402
from ta.venues import build_venue  # noqa: E402

# The families scripts/options_cost.py reads daily closes for. Keeping this list
# here (rather than reading the surface file) means the refresh still runs on a
# day when the surface fetch failed.
DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def universe_symbols(config, extra: list[str] | None = None) -> list[str]:
    """配置里的**全部标的** + 期权标的（去重、保序）。

    2026-10-13 实测的缺口：默认只刷期权标的（BTC/ETH/SOL），于是
    `data/history/okx_demo/*__1d.jsonl` 里 **52 个配置标的中有 49 个停在 5.5 天前**
    （482 个文件里只有 3 个是当天刷的）。而**研究侧读的正是这些文件**：
    `scripts/refit.py` 与 `scripts/daily_study.py` 都走 `ta.history.load_frames` ——
    也就是说参数重拟与每日研究一直在看「一周前的世界」，脚本自己的 docstring 也点名过这件事
    （「every backtest run on disk ended 4 days ago」），但当时只给期权标的打了补丁。
    每日 52 个标的各拉一次日线（60 根）约一分钟 —— 便宜、幂等、且**只合并不截断**。
    """
    out: list[str] = []
    for symbol in list(extra or DEFAULT_SYMBOLS):
        if symbol not in out:
            out.append(symbol)
    for items in (config.universe.symbols_by_tier or {}).values():
        for symbol in items:
            text = str(symbol)
            if text not in out:
                out.append(text)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                    help='逗号分隔；写 "universe" = 配置里的全部标的（研究侧读的就是这些文件）')
    ap.add_argument("--timeframe", default="1d",
                    help="逗号分隔，例如 15m,1h,4h,1d。研究侧读的不止日线："
                         "trend_breakout/perp 读 1h、mean_reversion 读 15m、"
                         "TradFi 读 1d，而回测还会加载 confirm 帧（4h/1h）")
    ap.add_argument("--bars", type=int, default=60, help="recent window to fetch and merge")
    ap.add_argument("--env-file", default=".env", help="credentials, read like the runner does")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    loaded = load_env_file(PROJECT / args.env_file)
    if loaded:
        print(f"loaded {loaded} value(s) from {args.env_file}")

    config = Config.load(args.config_dir)
    if args.symbols == "universe":
        symbols = universe_symbols(config)
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    venue = build_venue(config, args.venue)
    venue.connect()
    timeframes = [t.strip() for t in str(args.timeframe).split(",") if t.strip()]
    print(f"venue={args.venue} timeframes={','.join(timeframes)} bars={args.bars} "
          f"symbols={len(symbols)}")

    updated = 0
    refreshed: dict[str, int] = {}
    attempted = 0
    for timeframe in timeframes:
        hit = 0
        for symbol in symbols:
            # config.data_dir / "history" is the layout `ta data fetch` uses
            # (history_dir() itself only appends the venue), and getting this wrong is
            # silent in the worst way: the first version of this script pointed at
            # <project>/<venue>/ and cheerfully created a parallel copy of the series
            # there while the files the research reads stayed four days stale.
            path = history_path(config.data_dir / "history", args.venue, symbol, timeframe)
            attempted += 1
            try:
                bars = fetch_symbol(venue.ex, symbol, timeframe, args.bars)
            except Exception as exc:  # noqa: BLE001 - one symbol must not kill the step
                print(f"  {timeframe} {symbol}: fetch failed "
                      f"({type(exc).__name__}: {str(exc)[:80]})")
                continue
            if not bars:
                print(f"  {timeframe} {symbol}: no bars returned (not written)")
                continue
            before = read_bars(path)
            last_before = before[-1].ts if before else 0
            last_new = bars[-1].ts
            if not args.write:
                print(f"  {timeframe} {symbol}: would merge {len(bars)} bars "
                      f"(have {len(before)}, newest now {last_new}, newest on disk {last_before})")
                continue
            total = merge_bars(path, bars)
            gained = sum(1 for b in bars if b.ts > last_before)
            if gained:
                hit += 1
            print(f"  {timeframe} {symbol}: merged -> {total} bars (+{gained} new)")
        refreshed[timeframe] = hit
        updated += hit

    venue.close()
    if not args.write:
        print("DRY RUN - re-run with --write")
        return 0
    # 分时间帧报，别把四个帧的短板藏进一个总数里（2026-10-13：只刷 1d 时，
    # 那句"refreshed 52/52"看着全绿，而 15m 序列其实停在 5 天前）
    print("refreshed by timeframe: " + "、".join(
        f"{tf} {refreshed.get(tf, 0)}/{len(symbols)}" for tf in timeframes))
    print(f"total refreshed {updated}/{attempted} (symbol × timeframe)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
