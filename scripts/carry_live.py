#!/usr/bin/env python3
"""Screen EVERY USDT perpetual on the live venue for carry, right now.

`scripts/carry.py` can open/hold/close a carry pair but has to be told which
symbol; `scripts/monitor_study.py`-style backtests answer the historical
question. This closes the gap between them: it walks the venue's whole SWAP
catalogue, reads each contract's CURRENT funding, annualises it, and subtracts
the measured round-trip cost so the output is a list of symbols that are or are
not worth carrying today.

Why the whole catalogue matters: README 33 counted 155 USDT perpetuals on the
demo venue while our configuration trades none of them, and README 26 measured
carry on 13. Screening all 155 is the difference between "carry is probably
positive somewhere" and "here are the contracts where it is positive now".

Read-only: public funding endpoints and tickers, no keys, no orders.

FUNDING COMES FROM THE LIVE CATALOGUE, EXECUTION FROM THE DEMO ONE (measured
2026-10-07). The demo venue's funding is not merely noisy, it is wrong: comparing
the same contracts side by side, live BTC-USDT-SWAP averaged +0.000046 per 8h
(~5.0% a year) while demo BTC averaged **-0.000643** - the wrong sign - and demo
BARD averaged -0.001952, 39x the live magnitude. Live ETH/SOL/DOGE sat at a
believable 2.8-8.0% annualised. So the ranking is built on live public rates
(which need no keys) and only the *execution* stays on the demo venue.

    export HTTPS_PROXY=http://127.0.0.1:7897
    python3 scripts/carry_live.py --top 12
    python3 scripts/carry_live.py --min-annual 5 --write data/carry/screen.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

# Measured on the demo venue 2026-10-07 with a real two-leg round trip (README
# 27.3): fees 30.0bps + spread/leg-risk 14.8bps. Reused rather than re-guessed,
# because carry's whole economics is this number divided by the funding it
# collects.
ROUND_TRIP_BPS = 44.9
# Fallback only. Funding intervals are NOT uniform on OKX: BTC settles every 8h,
# but a number of younger contracts settle HOURLY (measured 2026-10-07: LINEA and
# BARD return one row per hour, thirteen times the settlements of BTC). Assuming
# 3/day for those inflates their annualised funding by ~8x, which is exactly how
# the first run of this screener produced "1642% a year" headlines.
DEFAULT_SETTLEMENTS_PER_DAY = 3.0


def settlements_per_day(timestamps: list[int]) -> tuple[float, float]:
    """(settlements per day, median gap in hours) inferred from the history."""
    if len(timestamps) < 3:
        return DEFAULT_SETTLEMENTS_PER_DAY, 8.0
    gaps = sorted(
        b - a for a, b in zip(sorted(timestamps), sorted(timestamps)[1:]) if b > a
    )
    if not gaps:
        return DEFAULT_SETTLEMENTS_PER_DAY, 8.0
    median_ms = gaps[len(gaps) // 2]
    hours = median_ms / 3_600_000.0
    if hours <= 0:
        return DEFAULT_SETTLEMENTS_PER_DAY, 8.0
    return 24.0 / hours, hours


MIN_SAMPLES = 20
# Above this mean-per-settlement a row is almost certainly pinned at OKX's funding
# CAP rather than reflecting a rate anyone could harvest: 0.5% per settlement is
# 1.5%/day at 3 settlements, and arbitrage does not leave that open for a month.
# The first honest run put nine contracts above it, all of them 1-hour-settlement
# listings - a data-semantics question to settle BEFORE any of them is traded, not
# a trade idea. Such rows are shown but never recommended.
SUSPECT_MEAN = 0.005

# The three gates a symbol must clear before it is a carry candidate at all.
MIN_POSITIVE_SHARE = 0.80
MAX_BREAKEVEN_DAYS = 30.0


def carry_eligible(
    daily_bps: float,
    median_daily_bps: float,
    positive_share: float,
    breakeven_days: float | None,
    *,
    min_positive_share: float = MIN_POSITIVE_SHARE,
    max_breakeven_days: float = MAX_BREAKEVEN_DAYS,
) -> tuple[bool, str]:
    """(eligible, why-not) for one symbol.

    Three gates, each of which the 2026-10-09 leaderboard would have failed:

      1. mean > 0            - expected income is positive (mean IS the income);
      2. median > 0 and the positive share is high - the income is not carried by
         a few spike settlements (NG: mean 12 bps/day, median 0.00, 39% positive);
      3. breakeven <= 30 days - the one-off round trip is recoverable in a
         reasonable holding period.
    """
    if daily_bps <= 0:
        return False, "mean<=0"
    if median_daily_bps <= 0:
        return False, "median<=0 (spike-only)"
    if positive_share < min_positive_share:
        return False, f"positive share {positive_share:.0%} < {min_positive_share:.0%}"
    if breakeven_days is None or breakeven_days > max_breakeven_days:
        return False, f"breakeven {breakeven_days} > {max_breakeven_days:.0f}d"
    return True, ""


# Lives in ta.execution.sizing so the RUNNER can use the same rule at order time;
# re-exported here because the screener's tests and callers import it from this
# module (a screen is a snapshot - the basis can move before the order goes out).
from ta.execution.sizing import basis_bps  # noqa: E402,F401


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", default="okx_demo_swap")
    ap.add_argument(
        "--funding-source",
        default="live",
        choices=("live", "demo"),
        help="live = real market funding (public, no keys); demo = the demo venue's",
    )
    ap.add_argument("--quote", default="USDT")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument(
        "--pair-notional",
        type=float,
        default=2000.0,
        help=(
            "size to price the depth probe at, in USD per pair (the runner's "
            "carry_notional_pct x equity is ~$2k today). A symbol that passes the "
            "funding gates can still be impossible to enter: measured 2026-10-09, "
            "ETHW's book could not absorb $2k at all and NOT's cost 325 bps."
        ),
    )
    ap.add_argument("--min-annual", type=float, default=0.0,
                    help="only report contracts above this annualised %%")
    ap.add_argument("--pause", type=float, default=0.06)
    ap.add_argument("--days", type=int, default=30,
                    help="funding lookback used for the MEAN (the current rate alone lies)")
    ap.add_argument("--hold-days", type=float, default=30.0,
                    help="holding period used to net the measured cost off the funding")
    ap.add_argument(
        "--max-basis-discount-bps",
        type=float,
        default=10.0,
        help=(
            "refuse pairs whose perp is more than this far BELOW spot at entry. "
            "The pair realises P - S on convergence, so a perp discount is a cost "
            "paid up front (ACT opened 133 bps below spot on 2026-10-09)."
        ),
    )
    ap.add_argument("--write", default="")
    args = ap.parse_args()

    from ta.config import Config
    from ta.venues import build_venue

    config = Config.load("config-exp")          # holds the swap venue section
    venue = build_venue(config, args.venue)
    try:
        paper_fee_bps = float(config.venue(args.venue).fee_bps)
    except Exception:  # noqa: BLE001 - a missing fee is reported, not guessed
        paper_fee_bps = 0.0
    venue.connect()
    if not venue.market_type == "swap":
        raise SystemExit("this screener needs market_type = swap")

    markets = [
        (symbol, market)
        for symbol, market in venue.ex.markets.items()
        if market.get("swap") and market.get("linear") and market.get("quote") == args.quote
    ]
    print(f"venue={args.venue}  永续合约 {len(markets)} 个 ({args.quote} 结算)")
    print(f"往返成本假设 {ROUND_TRIP_BPS} bps（实测，README 27.3）\n")

    # A single instantaneous funding print is not a rate you can annualise: the
    # first version of this screener ranked by it and put AAVE at "1642% a year",
    # which is just OKX's 0.015 cap on a spike. Funding mean-reverts within days,
    # so the decision number is the MEAN over --days, with the current print shown
    # beside it. (README 26 priced carry off the 30-day mean for the same reason.)
    since = int(time.time() * 1000) - args.days * 86_400_000
    live_client = None
    if args.funding_source == "live":
        import httpx

        live_client = httpx.Client(proxy="http://127.0.0.1:7897", timeout=40.0)
        print("资金费率来源：实盘公开接口（模拟盘费率实测不可信，见文件头说明）\n")

    def funding_of(symbol: str, market: dict) -> tuple[float, list[float], list[int]]:
        """(current rate, history rates, history timestamps) from the chosen source."""
        if live_client is not None:
            inst = str(market.get("id") or "")
            cur = live_client.get(
                "https://www.okx.com/api/v5/public/funding-rate", params={"instId": inst}
            ).json()
            current = float((cur.get("data") or [{}])[0].get("fundingRate") or 0.0)
            hist = live_client.get(
                "https://www.okx.com/api/v5/public/funding-rate-history",
                params={"instId": inst, "limit": 100},
            ).json()
            data = hist.get("data") or []
            rates = [float(r["fundingRate"]) for r in data if r.get("fundingRate")]
            stamps = [int(r["fundingTime"]) for r in data if r.get("fundingTime")]
            return current, rates, stamps
        rate = venue.ex.fetch_funding_rate(symbol)
        current = float(rate.get("fundingRate") or 0.0)
        history = venue.ex.fetch_funding_rate_history(symbol, since=since, limit=200)
        rates = [float(h["fundingRate"]) for h in history if h.get("fundingRate") is not None]
        stamps = [int(h["timestamp"]) for h in history if h.get("timestamp")]
        return current, rates, stamps

    rows: list[dict] = []
    no_history = 0
    for symbol, market in sorted(markets):
        try:
            current, rates, stamps = funding_of(symbol, market)
        except Exception as exc:  # noqa: BLE001 - a venue hiccup must not stop the sweep
            print(f"  {symbol:<22} fetch failed: {type(exc).__name__}")
            continue
        if rates:
            mean = sum(rates) / len(rates)
        else:
            # No history => the current print is all we have, and we must say so
            # rather than quietly treating it as representative.
            mean = float(current)
            no_history += 1
        per_day, gap_hours = settlements_per_day(stamps)
        daily_bps = mean * per_day * 10_000.0
        annual_pct = daily_bps * 365 / 100.0
        breakeven_days = ROUND_TRIP_BPS / daily_bps if daily_bps > 0 else None
        # The mean is the INCOME (what you actually collect per day). The median
        # and the positive share are the ROBUSTNESS check: measured 2026-10-09,
        # the mean-ranked leaderboard was led by NG (114% annualised, "breakeven
        # 1.4 days") whose 306 prints contain 174 exact ZEROS and only 39%
        # positives - the headline was a handful of spikes. GPRO and ZHONGJI had
        # a median of 0.00 for the same reason.
        median_rate = statistics.median(rates) if rates else float(current)
        median_daily_bps = median_rate * per_day * 10_000.0
        positive_share = (
            sum(1 for r in rates if r > 0) / len(rates) if rates else float(current > 0)
        )
        rows.append(
            {
                "symbol": symbol,
                "inst_id": market.get("id"),
                "funding_rate_now": float(current),
                "funding_rate_mean": round(mean, 8),
                "funding_rate_median": round(median_rate, 8),
                "samples": len(rates),
                "settle_hours": round(gap_hours, 2),
                "daily_bps": round(daily_bps, 3),
                "median_daily_bps": round(median_daily_bps, 3),
                "positive_share": round(positive_share, 3),
                "annual_pct": round(annual_pct, 2),
                "breakeven_days": round(breakeven_days, 1) if breakeven_days else None,
            }
        )
        if args.pause:
            time.sleep(args.pause)

    # Rank by the number that actually decides the trade: bps per day, because
    # the cost side is a fixed one-off and the funding side accrues.
    # Rank only on contracts with enough settlements to mean anything; a 3-sample
    # "mean" is a price spike wearing a funding label.
    thin = [r for r in rows if r["samples"] < MIN_SAMPLES]
    rows = [r for r in rows if r["samples"] >= MIN_SAMPLES]
    rows.sort(key=lambda r: -r["daily_bps"])
    positive = [r for r in rows if r["daily_bps"] > 0]
    print(f"扫到 {len(rows)} 个合约（均值口径 {args.days} 天），"
          f"资金费率为正的 {len(positive)} 个 "
          f"({100.0 * len(positive) / max(1, len(rows)):.0f}%)\n")
    if no_history:
        print(f"注意：{no_history} 个合约没有历史，只能拿瞬时值代替（已在数据里标注 samples=0）\n")
    if thin:
        print(f"已排除 {len(thin)} 个样本不足({MIN_SAMPLES} 条以下)的合约\n")
    for row in rows:
        row["eligible"], row["why_not"] = carry_eligible(
            row["daily_bps"], row["median_daily_bps"], row["positive_share"],
            row["breakeven_days"],
        )
    print(f"{'合约':<22} {'样本':>5s} {'均值bps/天':>11s} {'中位bps/天':>11s} {'正数%':>7s} "
          f"{'年化%':>8s} {'回本天':>8s} {'三道闸门':>10s}")
    shown = 0
    for row in rows:
        if row["annual_pct"] < args.min_annual:
            continue
        be = row["breakeven_days"]
        verdict = "通过" if row["eligible"] else "挡下"
        print(f"{row['symbol']:<22} {row['samples']:>5d} {row['daily_bps']:>11.3f} "
              f"{row['median_daily_bps']:>11.3f} {row['positive_share'] * 100:>6.0f}% "
              f"{row['annual_pct']:>8.2f} {('n/a' if be is None else f'{be:.1f}'):>8s} "
              f"{verdict:>10s}" + ("" if row["eligible"] else f"  ({row['why_not']})"))
        shown += 1
        if shown >= args.top:
            break

    suspect = [r for r in positive if abs(r["funding_rate_mean"]) >= SUSPECT_MEAN]
    # Second gate: only ordinary settlement schedules are eligible. Every
    # implausible row so far has been a 1-hour-settlement listing, and until the
    # semantics of that history is verified the honest answer is "not yet".
    sane = [
        r
        for r in positive
        if abs(r["funding_rate_mean"]) < SUSPECT_MEAN and r["settle_hours"] >= 4.0
    ]
    if suspect:
        print(f"\n⚠️ {len(suspect)} 个合约的均值 ≥ {SUSPECT_MEAN}／次结算，"
              f"几乎肯定是顶在 OKX 费率上限，而不是可持续收益率：")
        for row in suspect[:5]:
            print(f"    {row['symbol']}  均值 {row['funding_rate_mean']}  "
                  f"结算间隔 {row['settle_hours']}h  样本 {row['samples']}")
        print("    必须先把『1 小时结算合约的历史语义』查清再谈，**不构成交易建议**。")
    if sane:
        best = sane[0]
        print(f"\n[旧口径·仅按均值] 首位：{best['symbol']}  日 {best['daily_bps']:.3f} bps  "
              f"年化 {best['annual_pct']:.2f}%  结算间隔 {best['settle_hours']}h  "
              f"回本 {best['breakeven_days']} 天")
        if not best["eligible"]:
            print(f"          **但三道闸门挡下：{best['why_not']}** —— 不要照这条开仓。")

    passed = [r for r in rows if r["eligible"]]
    # SECOND, INDEPENDENT GATE: can we actually get in? The funding gates say the
    # trade is worth doing; the book says whether it can be done. Measured
    # 2026-10-09: ETHW (the symbol the runner kept attempting) could not absorb
    # $2k at all, and NOT would have cost 325 bps.
    impact_cap = 50.0
    for row in passed:
        base = row["symbol"].split("/")[0]
        spot = f"{base}/USDT"
        mark = 0.0
        try:
            mark = float((venue.ex.fetch_ticker(spot) or {}).get("last") or 0.0)
        except Exception:  # noqa: BLE001
            mark = 0.0
        if mark <= 0:
            row["depth_bps"], row["depth_ok"] = None, False
            continue
        qty = args.pair_notional / mark
        try:
            spot_impact = venue.entry_impact_bps(spot, qty, "buy")
        except Exception:  # noqa: BLE001
            spot_impact = None
        # The hedge leg has its own book and its own unit: contracts, not coins.
        ct_val = float(
            (venue.ex.markets.get(row["symbol"]) or {}).get("contractSize") or 1.0
        ) or 1.0
        try:
            perp_impact = venue.entry_impact_bps(row["symbol"], qty / ct_val, "sell")
        except Exception:  # noqa: BLE001
            perp_impact = None
        row["spot_impact_bps"] = None if spot_impact is None else round(spot_impact, 1)
        row["perp_impact_bps"] = None if perp_impact is None else round(perp_impact, 1)
        # BASIS: the other half of the entry cost, and the one with a clean fix.
        try:
            perp_mark = float((venue.ex.fetch_ticker(row["symbol"]) or {}).get("last") or 0.0)
        except Exception:  # noqa: BLE001
            perp_mark = 0.0
        b = basis_bps(mark, perp_mark)
        row["basis_bps"] = None if b is None else round(b, 1)
        row["basis_ok"] = b is not None and b >= -args.max_basis_discount_bps
        if spot_impact is None or perp_impact is None:
            row["depth_bps"], row["depth_ok"] = None, False
            continue
        row["depth_bps"] = round(spot_impact + perp_impact, 1)
        row["depth_ok"] = row["depth_bps"] <= impact_cap
        # The MEASURED round trip, replacing the 44.9 bps constant every earlier
        # carry conclusion rested on (which covered spread+fees but not the depth a
        # market order eats - measured 1.46% on ACT on 2026-10-09). Entry and exit
        # are both charged the same book cost: an assumption, but a transparent one.
        fee_bps = float(paper_fee_bps)
        row["round_trip_bps_measured"] = round(row["depth_bps"] * 2 + fee_bps * 2, 1)
        row["net_hold_bps"] = round(
            row["daily_bps"] * args.hold_days - row["round_trip_bps_measured"], 1
        )
    tradable = [r for r in passed if r["depth_ok"]]
    basis_ok = [r for r in tradable if r.get("basis_ok")]
    print(f"\n=== 三道闸门（均值>0 / 中位>0 且正数占比≥{MIN_POSITIVE_SHARE:.0%} / 回本≤{MAX_BREAKEVEN_DAYS:.0f}天）===")
    print(f"合格 {len(passed)} / {len(rows)} 个合约")
    print(f"\n=== 深度闸门（${args.pair_notional:,.0f}/对，冲击 ≤ {impact_cap:.0f} bps）===")
    print(f"既能做、也做得进去：{len(tradable)} / {len(passed)} 个合格合约")
    bad_basis = [r for r in tradable if not r.get("basis_ok")]
    print(f"\n=== 基差闸门（永续不低于现货 {args.max_basis_discount_bps:.0f} bps）===")
    print(f"四项全过：{len(basis_ok)} / {len(tradable)}")
    if bad_basis:
        sample = ", ".join(
            f"{r['symbol'].split('/')[0]}({r['basis_bps'] if r['basis_bps'] is not None else 'n/a'}bps)"
            for r in bad_basis[:6]
        )
        print(f"  被基差挡下（永续贴水）：{sample}")
    tradable = basis_ok
    blocked = [r for r in passed if not r["depth_ok"]]
    if blocked:
        sample = ", ".join(
            f"{r['symbol'].split('/')[0]}({r['depth_bps'] if r['depth_bps'] is not None else 'n/a'})"
            for r in blocked[:6]
        )
        print(f"  被深度挡下：{sample}")
    if tradable:
        print(f"  {'合约':<22}{'均值bps/天':>11}{'实测往返bps':>12}{'基差bps':>9}"
              f"{f'{args.hold_days:.0f}天净bps':>12}")
        ranked = sorted(tradable, key=lambda r: -r.get("net_hold_bps", -1e9))
        for row in ranked[: args.top]:
            basis_text = "n/a" if row["basis_bps"] is None else f"{row['basis_bps']:+.1f}"
            print(f"  {row['symbol']:<22}{row['daily_bps']:>11.2f}"
                  f"{row['round_trip_bps_measured']:>12.1f}"
                  f"{basis_text:>9}"
                  f"{row['net_hold_bps']:>12.1f}")
        pick = ranked[0]
        print(f"\n  注：『实测往返』= 现货腿冲击 + 永续腿冲击 + 2×{paper_fee_bps:.0f}bps 手续费"
              f"（进出各记一次盘口成本，是一项**明示的假设**）；"
              f"对照旧假设 44.9 bps。")
        print("\n下一步（已在模拟盘验收过）：python3 scripts/carry.py open "
              f"--symbol {pick['symbol'].split('/')[0]}/USDT --notional {args.pair_notional:.0f} --yes")
    if passed:
        print("\n  （仅按资金费三闸门的名单，未过深度闸门 —— 供比较，不构成开仓建议）")
        print(f"  {'合约':<22}{'均值bps/天':>11}{'回本天':>8}{'30天净bps':>11}")
        for row in passed[: args.top]:
            print(f"  {row['symbol']:<22}{row['daily_bps']:>11.2f}"
                  f"{row['breakeven_days']:>8.1f}"
                  f"{row['daily_bps'] * 30 - ROUND_TRIP_BPS:>11.1f}")
    else:
        print("\n没有任何『通过可信度闸门』的候选——不给出开仓建议。")
    print("\n风险（README 26）：资金费率会翻转；空头腿在急涨中会被强平——回测看不到这个尾部。")

    if args.write:
        target = config.path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "generated_at": int(time.time() * 1000),
                    "venue": args.venue,
                    "round_trip_bps": ROUND_TRIP_BPS,
                    "contracts": len(rows),
                    "positive": len(positive),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"written: {target}")
    venue.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
