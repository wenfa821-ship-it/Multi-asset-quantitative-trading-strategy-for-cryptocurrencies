#!/usr/bin/env python3
"""Can we *pick* new listings like CT? Measure the base rate first.

Motivation
----------
The proposed plan puts 60% into new tokens. The on-chain side has no fill
simulator, but OKX *does* list new tokens, and those have real order books and
real candles - so the "new listing" category is measurable today, with no new
capital and no new venue.

The question this answers is **not** "which parameter lets us pick winners".
It is the question that has to come first: **what does a new listing actually do
after you can trade it?**

Two facts make the naive version wrong, and both are handled here:

  1. OKX spot listings often open with a *call auction*
     (`openType = "call_auction"`), so the first tradable print is not the
     listing price. Every return below is measured from the **first hourly
     close**, i.e. from the price a bot could actually have paid.
  2. `instCategory == "1"` is crypto; `"3"` is a tokenised stock (XAAPL, XMU...).
     Only category 1 is a "new coin".

Read-only: public market data only. No keys, no orders, no signing.

    export TA_PROXY=http://127.0.0.1:7897
    python3 scripts/new_listing_study.py --max-age-days 180
    python3 scripts/new_listing_study.py --insts CT-USDT,VVV-USDT
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://www.okx.com"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    )
}
HOUR = 3_600_000


def _opener():
    proxy = os.environ.get("TA_PROXY") or os.environ.get("HTTPS_PROXY")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


OPENER = _opener()


def get(path: str) -> dict:
    req = urllib.request.Request(BASE + path, headers=UA)
    return json.load(OPENER.open(req, timeout=25))


def instruments() -> list[dict]:
    return get("/api/v5/public/instruments?instType=SPOT")["data"]


def hourly(inst: str, after_ms: int) -> list[tuple]:
    """Hourly bars strictly older than after_ms, oldest first."""
    data = get(f"/api/v5/market/history-candles?instId={inst}&bar=1H&after={after_ms}&limit=100")
    if data.get("code") != "0":
        return []
    rows = [
        (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6]))
        for r in data["data"]
    ]
    rows.sort()
    return rows


def study(inst: str, list_ts: int, now_ms: int, hours: int) -> dict | None:
    after = min(list_ts + hours * HOUR, now_ms + HOUR)
    bars = hourly(inst, after)
    bars = [b for b in bars if b[0] >= list_ts]
    if len(bars) < 3:
        return None
    entry = bars[0][4]
    if entry <= 0:
        return None
    fwd = bars[1:]
    window = [b for b in fwd if b[0] <= bars[0][0] + hours * HOUR] or fwd
    high = max(b[2] for b in window)
    low = min(b[3] for b in window)
    def at(h):
        target = bars[0][0] + h * HOUR
        sel = [b for b in window if b[0] <= target]
        return sel[-1][4] if sel else None
    return {
        "inst": inst,
        "entry_ts": bars[0][0],
        "entry": entry,
        "first_open": bars[0][1],
        "first_close": bars[0][4],
        "first_vol_usd": bars[0][5],
        "first_ret_pct": (bars[0][4] / bars[0][1] - 1) * 100 if bars[0][1] else None,
        "bars": len(bars),
        "run_up_pct": (high / entry - 1) * 100,
        "max_dd_pct": (low / entry - 1) * 100,
        "ret_1h_pct": ((at(1) / entry - 1) * 100) if at(1) else None,
        "ret_4h_pct": ((at(4) / entry - 1) * 100) if at(4) else None,
        "ret_24h_pct": ((at(24) / entry - 1) * 100) if at(24) else None,
        "ret_48h_pct": ((at(48) / entry - 1) * 100) if at(48) else None,
        "hit_20_before_-20": _hit_first(window, entry, 0.20, -0.20),
        "exits": {name: simulate_exit(window, entry, rule, 0.2) for name, rule in EXIT_RULES.items()},
    }


def _hit_first(window: list[tuple], entry: float, up: float, dn: float) -> str:
    for _, _, h, l, _, _ in window:
        if l <= entry * (1 + dn):
            return "down_first"
        if h >= entry * (1 + up):
            return "up_first"
    return "neither"


# Exit policies. The point of this table: 18.3 shows the run-up is real but the
# 48h hold gives it back, so the only question that matters is *how* to bank it.
# Conservative convention: inside one bar the stop is assumed to fire before the
# target (we never award ourselves the optimistic ordering).
EXIT_RULES: dict[str, dict] = {
    "hold48h（基准）": {},
    "TP+20% / SL-20% / 48h": {"tp": 0.20, "sl": 0.20},
    "TP+50% / SL-20% / 48h": {"tp": 0.50, "sl": 0.20},
    "TP+20% 半仓 + 余仓跟踪10%": {"tp": 0.20, "sl": 0.20, "trail": 0.10, "half": True},
    "跟踪止损 10% / SL-20% / 48h": {"sl": 0.20, "trail": 0.10},
    "跟踪止损 20% / SL-20% / 48h": {"sl": 0.20, "trail": 0.20},
}


def simulate_exit(window: list[tuple], entry: float, rule: dict, fee_pct: float) -> float:
    """Return the net % outcome of one exit policy. `window` excludes the entry bar."""
    tp = rule.get("tp")
    sl = rule.get("sl")
    trail = rule.get("trail")
    half = bool(rule.get("half"))
    open_frac = 1.0
    realised = 0.0
    peak = entry
    tp_taken = False
    for _, _, h, l, _, _ in window:
        if sl and l <= entry * (1 - sl):          # conservative: stop fires first
            realised += open_frac * (-sl * 100)
            open_frac = 0.0
            break
        if tp is not None and not tp_taken and h >= entry * (1 + tp):
            if half:
                realised += 0.5 * tp * 100
                open_frac = 0.5
                tp_taken = True
            else:
                realised += open_frac * tp * 100
                open_frac = 0.0
                break
        peak = max(peak, h)
        if trail and l <= peak * (1 - trail):
            realised += open_frac * ((peak * (1 - trail)) / entry - 1) * 100
            open_frac = 0.0
            break
    if open_frac > 0:
        realised += open_frac * (window[-1][4] / entry - 1) * 100
    return realised - fee_pct


def summarize(rows: list[dict], label: str, key: str) -> None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        print(f"  {label:<22} n=0")
        return
    vals_sorted = sorted(vals)
    p90 = vals_sorted[min(len(vals_sorted) - 1, int(0.9 * len(vals_sorted)))]
    print(f"  {label:<22} n={len(vals):<3} 中位={st.median(vals):>8.2f}% "
          f"均值={st.mean(vals):>8.2f}% p90={p90:>8.2f}% 最大={max(vals):>8.2f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=180.0)
    ap.add_argument("--hours", type=int, default=48, help="measurement window after the first bar")
    ap.add_argument("--insts", default="", help="explicit instruments, comma separated")
    ap.add_argument("--pause", type=float, default=0.25)
    ap.add_argument("--out-dir", default="reports")
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    if args.insts:
        targets = [(i.strip(), 0) for i in args.insts.split(",") if i.strip()]
        for inst, _ in targets:
            rows = get(f"/api/v5/public/instruments?instType=SPOT&instId={inst}")["data"]
            if rows:
                targets[targets.index((inst, 0))] = (inst, int(rows[0]["listTime"]))
    else:
        cutoff = now_ms - int(args.max_age_days * 86_400_000)
        targets = [
            (r["instId"], int(r["listTime"]))
            for r in instruments()
            if r["instId"].endswith("-USDT")
            and r.get("instCategory") == "1"
            and int(r["listTime"]) >= cutoff
            and r.get("state") == "live"
        ]
        targets.sort(key=lambda x: -x[1])

    print(f"new crypto listings (instCategory=1, <= {args.max_age_days:.0f}d): {len(targets)}")
    results: list[dict] = []
    for inst, ts in targets:
        age = (now_ms - ts) / 86_400_000
        try:
            row = study(inst, ts, now_ms, args.hours)
        except Exception as exc:  # noqa: BLE001
            print(f"  {inst:<14} error {exc}")
            continue
        if not row:
            print(f"  {inst:<14} age={age:5.1f}d  (not enough bars yet)")
            continue
        row["age_days"] = round(age, 2)
        results.append(row)
        print(f"  {inst:<14} age={age:5.1f}d entry={row['entry']:<11.6g} "
              f"最大上冲={row['run_up_pct']:>7.1f}% 最大回撤={row['max_dd_pct']:>7.1f}% "
              f"+24h={_fmt(row['ret_24h_pct'])} +48h={_fmt(row['ret_48h_pct'])} "
              f"首触={row['hit_20_before_-20']}")
        time.sleep(max(0.0, args.pause))

    print()
    print(f"=== 汇总（入场 = 上市后第一根 1H 收盘价；n={len(results)} 个代币）===")
    summarize(results, "48h 内最大上冲", "run_up_pct")
    summarize(results, "48h 内最大回撤", "max_dd_pct")
    summarize(results, "持有 1h", "ret_1h_pct")
    summarize(results, "持有 24h", "ret_24h_pct")
    summarize(results, "持有 48h", "ret_48h_pct")

    up = sum(1 for r in results if r["run_up_pct"] >= 50)
    up20 = sum(1 for r in results if r["run_up_pct"] >= 20)
    first_up = sum(1 for r in results if r["hit_20_before_-20"] == "up_first")
    first_dn = sum(1 for r in results if r["hit_20_before_-20"] == "down_first")
    n = max(1, len(results))
    print()
    print(f"  48h 内上冲 >= +20%: {up20}/{len(results)} ({100*up20/n:.0f}%)")
    print(f"  48h 内上冲 >= +50%: {up}/{len(results)} ({100*up/n:.0f}%)")
    print(f"  先摸 +20% 而不是先摸 -20%: {first_up}/{len(results)} ({100*first_up/n:.0f}%)"
          f"   反向 {first_dn}")

    fee = 0.2
    net = [r["ret_48h_pct"] - fee for r in results if r.get("ret_48h_pct") is not None]
    if net:
        print(f"  策略「+1h 全部买入，+48h 卖出」扣 {fee}% 费用："
              f"中位 {st.median(net):+.2f}%  均值 {st.mean(net):+.2f}%  "
              f"胜率 {100*sum(1 for x in net if x>0)/len(net):.0f}%")

    # Exit-policy comparison on the tradeable subset only (stablecoins are flat
    # by construction and would flatter every rule).
    live = [r for r in results if not (r["run_up_pct"] < 2 and r["max_dd_pct"] > -2)]
    print()
    print(f"=== 出场规则对比（入场=首根 1H 收盘，扣 0.2% 费用；n={len(live)} 个非稳定币新币）===")
    print(f"  {'规则':<26}{'中位':>9}{'均值':>9}{'胜率':>8}{'最好':>9}{'最差':>9}")
    for name in EXIT_RULES:
        vals = [r["exits"][name] for r in live if r.get("exits")]
        if not vals:
            continue
        wins = 100 * sum(1 for v in vals if v > 0) / len(vals)
        print(f"  {name:<26}{st.median(vals):>8.2f}%{st.mean(vals):>8.2f}%{wins:>7.0f}%"
              f"{max(vals):>8.1f}%{min(vals):>8.1f}%")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "hours": args.hours, "rows": results}
    path = out / f"new-listing-study-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {path}")
    return 0


def _fmt(v) -> str:
    return "-" if v is None else f"{v:>7.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
