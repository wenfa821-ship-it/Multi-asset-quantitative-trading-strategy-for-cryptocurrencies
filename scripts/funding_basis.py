#!/usr/bin/env python3
"""Is perpetual funding a real, harvestable cash flow - or another cost wall?

Why this exists
---------------
Every edge tested so far has been *directional*: it needed to predict which way a
price would go, and three rounds of real-data work (four 15m strategies, the 1m
frequency study, the on-chain event study) all died on the same thing - the move
was not bigger than the cost, or there was no move at all.

Perpetual funding is structurally different. It is a **cash flow you can observe
in advance**: longs pay shorts (or the reverse) on a published schedule, and a
delta-neutral book (long spot + short perp) collects it without taking a view on
price. There is no forecast in the trade at all.

What this script answers
------------------------
1. What does funding actually pay, per symbol, in basis points per day?
2. What does one round trip cost (2 spot legs + 2 perp legs + slippage)?
3. Therefore: how many days must you hold before the carry pays for the entry
   and exit?  This is the cost wall for carry, exactly as `cost_wall.py` was for
   directional trading.
4. Rolling H-day windows, split 70/30 in time: does the *net* number survive out
   of sample?

    export TA_PROXY=http://127.0.0.1:7897
    python3 scripts/funding_basis.py --days 180
    python3 scripts/funding_basis.py --days 180 --perp-fee-bps 5 --slippage-bps 1

Read-only: public endpoints, no API keys, no orders.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

# Liquid OKX perpetuals. A symbol that does not exist just fails cleanly.
DEFAULT_SYMBOLS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "LINK/USDT:USDT",
    "AVAX/USDT:USDT",
    "ADA/USDT:USDT",
    "SUI/USDT:USDT",
    "NEAR/USDT:USDT",
    "APT/USDT:USDT",
    "TIA/USDT:USDT",
    "INJ/USDT:USDT",
]


def build_exchange(proxy: str):
    import ccxt  # type: ignore

    params: dict = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
    exchange = ccxt.okx(params)
    # Public market data only - deliberately NOT sandbox, because the demo
    # environment does not mirror real funding history.
    if proxy:
        exchange.proxies = {"http": proxy, "https": proxy}
    exchange.load_markets()
    return exchange


def cache_path(symbol: str) -> Path:
    safe = symbol.replace("/", "-").replace(":", "_")
    return PROJECT / "data" / "history" / "funding" / f"{safe}.jsonl"


def load_cache(symbol: str) -> list[dict]:
    path = cache_path(symbol)
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    out.sort(key=lambda r: int(r["ts"]))
    return out


def save_cache(symbol: str, rows: list[dict]) -> None:
    path = cache_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def fetch_funding(exchange, symbol: str, days: int, pause: float) -> list[dict]:
    """Funding history, paginated. OKX settles 3x/day, so ~days*3 rows."""
    since = int((time.time() - days * 86_400) * 1000)
    rows: dict[int, dict] = {}
    while True:
        try:
            batch = exchange.fetch_funding_rate_history(symbol, since=since, limit=100)
        except Exception as exc:  # noqa: BLE001 - one dead symbol must not stop the run
            print(f"  ! {symbol}: {type(exc).__name__}: {str(exc)[:90]}")
            break
        if not batch:
            break
        for item in batch:
            ts = int(item.get("timestamp") or 0)
            rate = item.get("fundingRate")
            if ts and rate is not None:
                rows[ts] = {"ts": ts, "rate": float(rate)}
        last = int(batch[-1].get("timestamp") or 0)
        if len(batch) < 100 or last <= since:
            break
        since = last + 1
        time.sleep(pause)
    return [rows[k] for k in sorted(rows)]


def rolling_windows(
    rates: list[float],
    *,
    hold_periods: int,
    cost_bps: float,
    positive_lookback: int = 0,
) -> list[float]:
    """Net % return of every overlapping H-payment hold, minus one round trip.

    Each funding payment applies to the notional, so summing rates over the hold
    is the gross carry. The cost is paid once per completed window because the
    trade is opened and closed once.
    """
    out: list[float] = []
    for start in range(0, len(rates) - hold_periods + 1):
        if positive_lookback:
            if start < positive_lookback:
                continue
            trailing = rates[start - positive_lookback : start]
            if any(value <= 0 for value in trailing):
                continue
        window = rates[start : start + hold_periods]
        gross_bps = 10_000.0 * sum(window)
        out.append(gross_bps - cost_bps)
    return out


def summarise(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(values),
        "median_bps": round(st.median(values), 2),
        "mean_bps": round(st.fmean(values), 2),
        "win_rate": round(100.0 * sum(1 for v in values if v > 0) / len(values), 1),
        "p10_bps": round(ordered[int(0.10 * (len(ordered) - 1))], 2),
        "p90_bps": round(ordered[int(0.90 * (len(ordered) - 1))], 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument(
        "--spot-fee-bps", type=float, default=10.0,
        help="taker on the spot leg (measured on this account: 10)",
    )
    ap.add_argument(
        "--perp-fee-bps", type=float, default=5.0,
        help="taker on the perp leg (OKX Lv1 published schedule: 5)",
    )
    ap.add_argument(
        "--slippage-bps", type=float, default=1.0,
        help="per leg; majors measured ~0.9bps spread",
    )
    ap.add_argument("--pause", type=float, default=0.35)
    ap.add_argument("--refresh", action="store_true", help="ignore the cache")
    ap.add_argument("--oos-split", type=float, default=0.70)
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    round_trip_bps = 2 * (args.spot_fee_bps + args.slippage_bps) + 2 * (
        args.perp_fee_bps + args.slippage_bps
    )

    proxy = (
        os.environ.get("TA_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or ""
    ).strip()

    print("=" * 96)
    print("永续资金费率 / 基差：这是现金流，不是方向预测")
    print("=" * 96)
    print(f"往返成本假设 = 2×现货({args.spot_fee_bps}+{args.slippage_bps}) + "
          f"2×永续({args.perp_fee_bps}+{args.slippage_bps}) = {round_trip_bps:.0f} bps")
    print(f"资金费结算：OKX 每 8 小时一次（每天 3 次）| 回看 {args.days} 天\n")

    exchange = None
    try:
        exchange = build_exchange(proxy)
    except Exception as exc:  # noqa: BLE001 - cache-only mode is supported
        print(f"（无法连接交易所，只用本地缓存：{type(exc).__name__}: {str(exc)[:80]}）\n")

    header = (
        f"{'币种':<18} {'记录':>5} {'中位/8h':>10} {'年化':>9} {'负费率占比':>10} "
        f"{'日均bps':>9} {'盈亏平衡天数':>12}"
    )
    print(header)
    print("-" * 96)

    results: dict[str, dict] = {}
    for symbol in symbols:
        rows = [] if args.refresh else load_cache(symbol)
        if exchange is not None and (not rows or args.refresh):
            rows = fetch_funding(exchange, symbol, args.days, args.pause)
            if rows:
                save_cache(symbol, rows)
        if len(rows) < 30:
            print(f"{symbol:<18} {'-':>5} 数据不足（{len(rows)} 条）")
            continue

        rates = [float(r["rate"]) for r in rows]
        # Settlement interval is NOT always 8h (TIA settled 4-hourly in this
        # window: 559 rows where every other symbol had 280). Derive it from the
        # data instead of assuming, or the annualised number is silently wrong.
        gaps = [
            (int(b["ts"]) - int(a["ts"])) / 1000.0
            for a, b in zip(rows, rows[1:])
            if int(b["ts"]) > int(a["ts"])
        ]
        interval_hours = (st.median(gaps) / 3600.0) if gaps else 8.0
        periods_per_day = 24.0 / interval_hours if interval_hours > 0 else 3.0
        per_period_median = st.median(rates)
        annual_pct = 100.0 * st.fmean(rates) * periods_per_day * 365
        negative_share = sum(1 for r in rates if r < 0) / len(rates)
        daily_bps = 10_000.0 * st.fmean(rates) * periods_per_day
        breakeven_days = (round_trip_bps / daily_bps) if daily_bps > 0 else float("inf")

        print(
            f"{symbol:<18} {len(rows):>5} {per_period_median * 100:>9.4f}% {annual_pct:>8.2f}% "
            f"{100 * negative_share:>9.1f}% {daily_bps:>9.2f} "
            f"{breakeven_days:>12.1f}"
        )
        results[symbol] = {
            "rows": rows,
            "rates": rates,
            "median_rate": per_period_median,
            "annual_pct": annual_pct,
            "negative_share": negative_share,
            "daily_bps": daily_bps,
            "breakeven_days": breakeven_days,
            "interval_hours": interval_hours,
            "periods_per_day": periods_per_day,
        }

    if not results:
        print("\n没有可用数据。先联网抓一次：TA_PROXY=... python3 scripts/funding_basis.py")
        return 2

    # ---- does the net number survive out of sample? ------------------------
    print("\n" + "=" * 96)
    print("滚动持有窗口的**净**收益（已扣掉一次往返成本），70/30 时间切分")
    print("=" * 96)

    for hold_days, label in ((1, "1天"), (7, "7天"), (30, "30天"), (90, "90天")):
        print(f"\n--- 持有 {label} ---")
        print(
            f"{'币种':<18} {'样本':<22} {'n':>5} {'中位bps':>9} {'均值bps':>9} "
            f"{'胜率%':>7} {'p10':>8} {'p90':>8}"
        )
        for symbol, data in results.items():
            rates = data["rates"]
            periods = max(1, int(round(hold_days * data["periods_per_day"])))
            windows = rolling_windows(rates, hold_periods=periods, cost_bps=round_trip_bps)
            if not windows:
                continue
            cut = int(len(windows) * args.oos_split)
            segments = [("全样本", windows), ("样本内70%", windows[:cut]), ("样本外30%", windows[cut:])]
            for name, values in segments:
                stats = summarise(values)
                if not stats.get("n"):
                    continue
                effective = len(values) / max(1, periods)
                print(
                    f"{symbol:<18} {name:<22} {stats['n']:>5} {stats['median_bps']:>9.2f} "
                    f"{stats['mean_bps']:>9.2f} {stats['win_rate']:>7.1f} "
                    f"{stats['p10_bps']:>8.2f} {stats['p90_bps']:>8.2f}"
                )
        if hold_days >= 30:
            print(
                f"  注意：这是**重叠**窗口，{hold_days} 天窗口下 {args.days} 天历史里"
                f"只有约 {args.days / hold_days:.1f} 个独立观测。"
                "胜率 100% 不代表 100% 可靠，它代表样本几乎是同一件事被数了很多遍。"
            )

    # ---- the honest verdict -------------------------------------------------
    print("\n" + "=" * 96)
    print("判定")
    print("=" * 96)
    positives = [s for s, d in results.items() if d["daily_bps"] > 0]
    breakevens = sorted(d["breakeven_days"] for d in results.values() if d["breakeven_days"] != float("inf"))
    if breakevens:
        print(f"  盈亏平衡天数：中位 {st.median(breakevens):.1f} 天，"
              f"最短 {breakevens[0]:.1f} 天，最长 {breakevens[-1]:.1f} 天")
    print(f"  正资金费率（多头付空头，做空永续能收钱）的币种：{len(positives)}/{len(results)}")
    print(
        "  读法：只有把'持有天数'拉到盈亏平衡天数以上，这笔现金流才开始为正；"
        "低于它的任何持仓都是在给交易所交手续费。"
    )
    print(
        "  样本外列才是结论。样本内好看、样本外翻负 = 又是费用与净收益同量级，"
        "和四策略在 180 天上死掉的原因完全一样。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
