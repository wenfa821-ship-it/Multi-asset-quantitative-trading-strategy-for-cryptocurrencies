#!/usr/bin/env python3
"""Track what the carry legs actually COLLECT, from the venue's own bill ledger.

Why this exists
---------------
Funding research so far has been built on *rates* (`data/history/funding/*.jsonl`):
the median says ~3 bps/day, the mean is spike-inflated. What it never had was the
realised side - what the account was actually paid. That only exists in the
venue's bills (`type=8`), and on 2026-10-09 it read **+$298.94 in 24 hours**,
~8.8% of the perp notional, far above the median estimate because funding was in
a spike.

That produced a falsifiable prediction worth recording daily: **when the spike
fades, the daily income should fall back to the $2-3 level per pair.** This script
snapshots the answer once a day so the prediction can be checked against a series
instead of memory - and so `carry_notional_pct` can later be calibrated from
realised income rather than from the rate history.

2026-10-11 correction (README 71): the window here is ROLLING, so consecutive
rows overlap and **summing them double-counts settlements** - and when the account
has fewer than 100 funding bills the page comes back whole, which made a
`--days 2` row silently cover three days. Two consequences, both fixed here:
each row now records the window it actually covers (`window_from`/`window_to`/
`window_days`), and `bps_per_day` divides by that real window instead of
pretending the sum is one day. Anything that wants a per-day series should read
`scripts/carry_income_report.py`, which re-buckets the raw bills by UTC day.

2026-10-12 conclusion: **the "spike" prediction above is FALSIFIED.** That
+$298.94 day was called a spike, and the prediction was that income would fall
back to the $2-3 per pair per day level when it faded. It did not fade - five days
on, the level is still two orders of magnitude above the live rate (CRV: bills
**$986.26** over 10-08..10-12 vs **$3.97** implied by the live 8h rate = **248x**).
Three measurements say the demo's funding is not the live market's funding in
level *or* in sign:

* **cadence**: the demo settles CRV **hourly** and ACT **every 4h**; the live
  venue settles both every 8h;
* **level**: CRV's per-settlement implied rate is 0.20-0.30%/hour (median 0.30%),
  against a live rate of 0.01% per 8 hours;
* **sign**: on 10-09, when the live rates were NEGATIVE (we should have PAID), the
  demo paid us **+$323.07**.

Therefore **`bps_per_day` in this file may not be quoted as a return, and may not
be used to calibrate `carry_notional_pct`** - use the rate series instead
(`scripts/carry_rotation.py` reads the live rate). `implausible_rates()` flags
anything above `PLAUSIBLE_BPS_PER_DAY`, so the anomaly shows up in the output
instead of depending on whoever reads it remembering this paragraph.

Idempotent per UTC day. Read-only against the venue.

    python3 scripts/funding_income.py            # report
    python3 scripts/funding_income.py --write    # append today's row
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

# Live perpetual funding almost never exceeds ~10 bps/day (that would be ~36%
# annualised), so 50 is already generous: anything above it is a **venue-rule
# difference**, not a market rate (see the 2026-10-12 conclusion in the docstring).
PLAUSIBLE_BPS_PER_DAY = 50.0


def implausible_rates(summary: dict, limit: float = PLAUSIBLE_BPS_PER_DAY) -> dict:
    """Which contracts' bps/day are outside the plausible live range (pure).

    Exists because '8.8% of notional in one day' was written down as a spike and
    then never re-examined: a flag in the output is cheaper than a paragraph
    nobody re-reads.
    """
    return {
        inst: entry.get("bps_per_day")
        for inst, entry in summary.items()
        if entry.get("bps_per_day") is not None and entry["bps_per_day"] > limit
    }
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config, load_env_file  # noqa: E402
from ta.venues import build_venue  # noqa: E402


def funding_bills(venue, days: int = 7) -> list[dict]:
    out: list[dict] = []
    after: str | None = None
    while True:
        params: dict = {"type": "8", "limit": "100"}
        if after:
            params["after"] = after
        page = venue.ex.privateGetAccountBills(params).get("data") or []
        if not page:
            break
        out += page
        oldest = int(page[-1]["ts"])
        if len(page) < 100 or oldest < (time.time() - days * 86_400) * 1000:
            break
        after = page[-1]["billId"]
    return out


def window_days(bills: list[dict], default: float = 1.0) -> float:
    """How many days the bills actually span - never assume the requested window.

    The bill page comes back whole when the account has fewer than `limit` rows,
    so `--days 2` can hand back three days of history. Dividing by the requested
    window would then overstate the rate by 50%.
    """
    stamps = [float(b["ts"]) for b in bills if b.get("ts")]
    if len(stamps) < 2:
        return default
    return max((max(stamps) - min(stamps)) / 86_400_000.0, 1.0 / 24.0)


def summarise(bills: list[dict], positions: dict[str, float],
              window: float | None = None) -> dict:
    """Per-symbol income and the implied bps/day against the live perp notional.

    `window` is the number of days the bills span; `None` means "treat the sample
    as one day", which is what the pre-2026-10-11 callers did.
    """
    span = 1.0 if window is None else max(float(window), 1.0 / 24.0)
    by_symbol: dict[str, dict] = collections.defaultdict(lambda: {"usdt": 0.0, "settlements": 0})
    for bill in bills:
        inst = str(bill.get("instId") or "")
        entry = by_symbol[inst]
        entry["usdt"] = round(entry["usdt"] + float(bill.get("pnl") or 0.0), 6)
        entry["settlements"] += 1
    for inst, entry in by_symbol.items():
        notional = positions.get(inst.replace("-SWAP", "").replace("-", "/", 1) + ":USDT")
        if not notional:
            entry["notional_usd"] = None
            entry["bps_per_day"] = None
            continue
        entry["notional_usd"] = round(notional, 2)
        entry["bps_per_day"] = round(entry["usdt"] / notional / span * 10_000.0, 2)
    return dict(by_symbol)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--out", default="data/funding/income.jsonl")
    ap.add_argument("--days", type=int, default=2, help="how much bill history to summarise")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    load_env_file(PROJECT / args.env_file)
    config = Config.load(args.config_dir)
    venue = build_venue(config, args.venue)
    for attempt in range(4):
        try:
            venue.connect()
            break
        except Exception:  # noqa: BLE001 - the proxy is flaky
            if attempt == 3:
                print("could not reach the venue; nothing written")
                return 2
            time.sleep(3)

    try:
        bills = funding_bills(venue, days=args.days)
        notional: dict[str, float] = {}
        for row in venue.ex.fetch_positions() or []:
            contracts = abs(float(row.get("contracts") or 0.0))
            if contracts:
                notional[str(row.get("symbol"))] = float(row.get("notional") or 0.0)
    finally:
        venue.close()

    span = window_days(bills)
    summary = summarise(bills, notional, window=span)
    total = sum(v["usdt"] for v in summary.values())
    day = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    print(f"{'contract':<20}{'USDT':>12}{'settles':>9}{'notional':>11}{'bps/day':>10}")
    for inst, entry in sorted(summary.items(), key=lambda kv: -kv[1]["usdt"]):
        notional_text = f"{entry['notional_usd']:,.0f}" if entry["notional_usd"] else "n/a"
        bps = entry["bps_per_day"]
        bps_text = f"{bps:.1f}" if bps is not None else "n/a"
        print(f"{inst:<20}{entry['usdt']:>+12.4f}{entry['settlements']:>9}"
              f"{notional_text:>11}{bps_text:>10}")
    stamps = [float(b["ts"]) for b in bills if b.get("ts")]
    first = datetime.datetime.fromtimestamp(min(stamps) / 1000, datetime.UTC).strftime("%m-%d %H:%M") if stamps else "n/a"
    last = datetime.datetime.fromtimestamp(max(stamps) / 1000, datetime.UTC).strftime("%m-%d %H:%M") if stamps else "n/a"
    print(f"\ntotal {total:+.4f} USDT over {span:.2f}d (requested {args.days}d; "
          f"bills span {first} .. {last} UTC)")

    # 2026-10-12：这套数来自**场馆账单**，而模拟盘的资金费与实盘既不同幅也不同向
    # （CRV 实测 248 倍，且实盘为负的日子模拟盘照样付钱）。超区间就必须当场标出来，
    # 否则它很容易被当成"收益率"，或者被拿去校准 carry 额度。
    weird = implausible_rates(summary)
    if weird:
        print(f"\n⚠ 疑似模拟盘口径（bps/day > {PLAUSIBLE_BPS_PER_DAY:g}）："
              + "、".join(f"{k} {v:.0f}" for k, v in sorted(weird.items(),
                                                          key=lambda kv: -kv[1])))
        print("  这些数**不能**当作收益率，也不能用来校准 carry 额度 —— "
              "要费率就读公开费率序列（scripts/carry_rotation.py）。")

    row = {"date": day, "days": args.days, "window_days": round(span, 3),
           "window_from": min(stamps) if stamps else None,
           "window_to": max(stamps) if stamps else None,
           "total_usdt": round(total, 6), "by_contract": summary}
    target = PROJECT / args.out
    if not args.write:
        print("DRY RUN - re-run with --write")
        return 0
    existing: list[dict] = []
    if target.exists():
        existing = [json.loads(l) for l in target.read_text(encoding="utf-8").splitlines() if l.strip()]
    existing = [r for r in existing if r.get("date") != day]
    existing.append(row)
    existing.sort(key=lambda r: str(r.get("date")))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in existing) + "\n", encoding="utf-8"
    )
    print(f"appended {day} -> {target}  ({len(existing)} day(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
