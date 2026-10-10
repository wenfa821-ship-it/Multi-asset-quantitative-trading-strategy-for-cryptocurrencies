#!/usr/bin/env python3
"""Measure the cost wall for a given timeframe, on real venue data.

Answers one question: **how big does a trade's expected move have to be before
it can pay for itself?** Everything about "should I trade faster?" follows from
that ratio, and it has nothing to do with how clever the signal is.

    export TA_PROXY=http://127.0.0.1:7897
    python3 scripts/cost_wall.py --venue okx_demo --timeframe 1m
    python3 scripts/cost_wall.py --timeframe 15m --fee-bps 10 --slippage-bps 1

The venue part (live spreads) needs the network; the move statistics come from
`data/history/<venue>/*.jsonl`, so it also works offline once you have fetched.
"""

from __future__ import annotations

import argparse
import os
import statistics as st
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.history import list_history, read_bars  # noqa: E402
from ta.util import tf_to_ms  # noqa: E402


def live_spreads(config: Config, venue: str, symbols: list[str]) -> dict[str, float]:
    proxy = (
        os.environ.get("TA_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or ""
    ).strip()
    try:
        import ccxt

        section = config.venue(venue)
        params: dict = {"enableRateLimit": True, "options": {"defaultType": section.market_type}}
        if proxy:
            params["proxies"] = {"http": proxy, "https": proxy}
        exchange = getattr(ccxt, section.exchange_id)(params)
        if section.sandbox:
            exchange.set_sandbox_mode(True)
        exchange.load_markets()
    except Exception as exc:  # noqa: BLE001 - offline is a supported mode
        print(f"  (live spreads unavailable: {exc})")
        return {}

    out: dict[str, float] = {}
    for symbol in symbols:
        try:
            book = exchange.fetch_order_book(symbol, 5)
            bid, ask = book["bids"][0][0], book["asks"][0][0]
            mid = (bid + ask) / 2
            if mid > 0:
                out[symbol] = 10_000.0 * (ask - bid) / mid
        except Exception:  # noqa: BLE001
            continue
    return out


def median_move_bps(bars, periods: int = 1) -> float | None:
    """Median absolute move over `periods` bars, in bps."""
    closes = [bar.close for bar in bars]
    if len(closes) <= periods:
        return None
    moves = [
        abs(closes[i] / closes[i - periods] - 1.0) * 10_000.0
        for i in range(periods, len(closes))
        if closes[i - periods]
    ]
    return st.median(moves) if moves else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--venue", default="okx_demo")
    parser.add_argument("--timeframe", default="")
    parser.add_argument("--fee-bps", type=float, default=None, help="per side; default from config")
    parser.add_argument("--slippage-bps", type=float, default=None)
    parser.add_argument("--no-network", action="store_true")
    args = parser.parse_args()

    config = Config.load(args.config_dir)
    venue_section = config.venue(args.venue)
    timeframe = args.timeframe or config.universe.timeframe
    fee = venue_section.fee_bps if args.fee_bps is None else args.fee_bps
    slip = venue_section.slippage_bps if args.slippage_bps is None else args.slippage_bps
    round_trip = (fee + slip) * 2.0

    rows = [r for r in list_history(config.data_dir / "history", args.venue) if r["timeframe"] == timeframe]
    if not rows:
        print(f"no local {timeframe} history for {args.venue}")
        print(f"run: ta data fetch --config-dir {args.config_dir} --timeframes {timeframe}")
        return 2

    symbols = [r["symbol"] for r in rows]
    spreads = {} if args.no_network else live_spreads(config, args.venue, symbols)

    print(f"\ntimeframe {timeframe} | fee {fee}bps/side + {slip}bps slippage"
          f" -> round trip {round_trip:.1f}bps")
    print(f"{'symbol':<14} {'bars':>7} {'spread':>8} {'move(bps)':>10} {'need x move':>12}")
    moves: list[float] = []
    for row in sorted(rows, key=lambda r: r["symbol"]):
        bars = read_bars(row["path"])
        move = median_move_bps(bars)
        if not move:  # a symbol with a flat median (illiquid stub) cannot be judged
            continue
        moves.append(move)
        spread = spreads.get(row["symbol"])
        cost = round_trip + (spread or 0.0)
        print(
            f"{row['symbol']:<14} {row['bars']:>7} "
            f"{(f'{spread:.2f}' if spread is not None else '-'):>8} {move:>10.2f} "
            f"{cost / move:>11.2f}x"
        )
    if moves:
        median_move = st.median(moves)
        print(
            f"\nmedian move per {timeframe} bar : {median_move:.2f} bps"
            f"\nround-trip cost                 : {round_trip:.1f} bps"
            f"\n=> a trade must capture         : {round_trip / median_move:.2f}x the "
            f"median bar move just to break even"
        )
        per_day = 1440.0 / (tf_to_ms(timeframe) / 60_000.0)
        print(
            f"\nif you take one trade per bar ({per_day:.0f}/day), fees alone cost "
            f"{round_trip * per_day / 100:.1f}% of notional per day"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
