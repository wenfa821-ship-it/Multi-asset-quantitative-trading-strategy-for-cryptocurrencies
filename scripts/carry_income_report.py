#!/usr/bin/env python3
"""Does carry pay for itself on names whose funding is NORMAL?

Why this exists (2026-10-11, out of the portfolio review, README 70/71)
---------------------------------------------------------------------
The 1.2/1.3 question is whether carry clears its own entry cost
(`round_trip_bps = 44.9`, README 27.3) on names whose funding is not a
simulation spike. Two measurements exist and they are NOT the same thing:

  A. REALISED - what the demo venue actually credited (`type=8` bills, pulled
     fresh from the venue). Ground truth for the demo, useless as a live
     forecast: the demo's credits run from under 1x to over 100x the market
     funding rate for the same name and day, so the ratio is printed, not hidden.
  B. RATE-IMPLIED - the public `funding-rate-history` prints on disk, summed per
     UTC day. That is what a live account would pay, so that is the number a
     sizing decision may use.

The trap this script exists to avoid: `data/funding/income.jsonl` keeps a
ROLLING window per day. Adding its rows up double-counts settlements (BAND's
10-08 credit appears in all three rows), and dividing that sum by the number of
rows invents a rate. So realised income is re-derived here from the raw bills,
bucketed by UTC day, which cannot overlap by construction.

    python3 scripts/carry_income_report.py              # both panels (needs network)
    python3 scripts/carry_income_report.py --offline     # rate-implied panel only
    python3 scripts/carry_income_report.py --days 30 --write
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import statistics
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

# Module level on purpose: `daily_research.py`'s regression test walks its own
# step list and requires every step that builds a venue to import the shared
# `.env` loader here, so a missing loader is a red test rather than a silent
# "no credentials" run at 09:10 (README 61.2 #9).
from ta.config import Config, load_env_file  # noqa: E402
from ta.venues import build_venue  # noqa: E402

DEFAULT_ROUND_TRIP_BPS = 44.9
DEFAULT_ARTEFACT_RATIO = 3.0


def load_jsonl(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def utc_day(ts_ms: float) -> str:
    return datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.UTC).strftime("%Y-%m-%d")


def carried_names(path: Path) -> list[str]:
    try:
        pairs = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(str(k).split("/")[0] for k in pairs)


def contract_name(inst: str) -> str:
    """`CRV-USDT-SWAP` -> `CRV` (the funding-history file naming)."""
    return inst.split("-")[0]


def lookup_notional(inst: str, notionals: dict[str, float]) -> float | None:
    key = inst.replace("-SWAP", "").replace("-", "/", 1) + ":USDT"
    value = notionals.get(key)
    return float(value) if value else None


# ------------------------------------------------------------------- panel B


def rate_daily_bps(rows: list[dict]) -> dict[str, float]:
    """Sum each UTC day's funding prints into bps/day (1 bp = 0.0001)."""
    out: dict[str, float] = collections.defaultdict(float)
    for row in rows:
        out[utc_day(float(row["ts"]))] += float(row["rate"]) * 1e4
    return dict(out)


def rate_summary(rate_dir: Path, name: str, days: int) -> dict | None:
    rows = load_jsonl(rate_dir / f"{name}-USDT_USDT.jsonl")
    if not rows:
        return None
    rows.sort(key=lambda r: int(r["ts"]))
    daily = rate_daily_bps(rows)
    ordered = sorted(daily)
    window = ordered[-days:] if days > 0 else ordered
    if not window:
        return None
    values = [daily[d] for d in window]
    spacing = (
        statistics.median(
            [(int(rows[i + 1]["ts"]) - int(rows[i]["ts"])) / 1000 for i in range(len(rows) - 1)]
        )
        if len(rows) > 1
        else 0.0
    )
    return {
        "name": name,
        "days": len(window),
        "median_bps": round(statistics.median(values), 3),
        "mean_bps": round(statistics.mean(values), 3),
        "last_day_bps": round(daily[ordered[-1]], 3),
        "negative_days": sum(1 for v in values if v < 0),
        "settle_hours": round(spacing / 3600.0, 2) if spacing else None,
        "series": {d: round(daily[d], 3) for d in window},
    }


# ------------------------------------------------------------------- panel A


def funding_bills(venue, oldest_ms: float) -> list[dict]:
    """Every type=8 bill back to `oldest_ms` (newest-first pages)."""
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
        if len(page) < 100 or int(page[-1]["ts"]) < oldest_ms:
            break
        after = page[-1]["billId"]
    return out


def realised_by_day(bills: list[dict]) -> dict[str, dict[str, dict]]:
    """{utc_day: {contract: {usdt, settles}}} - one bucket per day, never overlapping."""
    out: dict[str, dict[str, dict]] = collections.defaultdict(
        lambda: collections.defaultdict(lambda: {"usdt": 0.0, "settles": 0})
    )
    for bill in bills:
        inst = str(bill.get("instId") or "?")
        entry = out[utc_day(float(bill["ts"]))][inst]
        entry["usdt"] = round(entry["usdt"] + float(bill.get("pnl") or 0.0), 6)
        entry["settles"] += 1
    return {day: dict(contracts) for day, contracts in out.items()}


def position_notionals(venue) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in venue.ex.fetch_positions() or []:
        if abs(float(row.get("contracts") or 0.0)):
            out[str(row.get("symbol"))] = float(row.get("notional") or 0.0)
    return out


def fetch_realised(config_dir: str, venue_name: str, env_file: str, bill_days: int) -> tuple:
    """Pull the venue's own funding bills. Read-only; returns (bills, notionals)."""
    load_env_file(PROJECT / env_file)
    config = Config.load(config_dir)
    venue = build_venue(config, venue_name)
    for attempt in range(4):
        try:
            venue.connect()
            break
        except Exception:  # noqa: BLE001 - the proxy is flaky by design
            if attempt == 3:
                raise
            time.sleep(3)
    try:
        oldest = (time.time() - bill_days * 86_400) * 1000
        bills = funding_bills(venue, oldest)
        notionals = position_notionals(venue)
    finally:
        venue.close()
    return bills, notionals


# ------------------------------------------------------------------- panel C


def build_report(
    *,
    realised: dict[str, dict[str, dict]],
    notionals: dict[str, float],
    rate_dir: Path,
    names: list[str],
    days: int,
    artefact_ratio: float,
    round_trip_bps: float,
    min_days: int,
) -> dict:
    days_seen = sorted(realised)
    per_contract: dict[str, dict] = {}
    for day in days_seen:
        for inst, entry in realised[day].items():
            acc = per_contract.setdefault(
                contract_name(inst),
                {"contract": inst, "usdt": 0.0, "days": 0, "settles": 0, "per_day": {}},
            )
            acc["usdt"] = round(acc["usdt"] + entry["usdt"], 6)
            acc["settles"] += entry["settles"]
            acc["days"] += 1
            acc["per_day"][day] = entry["usdt"]
    for name in names:
        per_contract.setdefault(
            name,
            {"contract": f"{name}-USDT-SWAP", "usdt": 0.0, "days": 0,
             "settles": 0, "per_day": {}},
        )

    for acc in per_contract.values():
        notional = lookup_notional(acc["contract"], notionals)
        acc["notional_usd"] = round(notional, 2) if notional else None
        acc["realised_bps_per_day"] = (
            round(acc["usdt"] / notional / acc["days"] * 1e4, 2)
            if notional and acc["days"] else None
        )
        rate = rate_summary(rate_dir, contract_name(acc["contract"]), days)
        acc["market_median_bps"] = rate["median_bps"] if rate else None
        acc["market_mean_bps"] = rate["mean_bps"] if rate else None
        acc["market_days"] = rate["days"] if rate else 0
        acc["market_negative_days"] = rate["negative_days"] if rate else None
        acc["settle_hours"] = rate["settle_hours"] if rate else None
        if acc["realised_bps_per_day"] is not None and rate and rate["median_bps"]:
            acc["realised_over_market"] = round(
                acc["realised_bps_per_day"] / rate["median_bps"], 2
            )
        else:
            acc["realised_over_market"] = None

    # The basket is what a LIVE account would collect on these names, taken from
    # the market's own prints. A name is dropped when its demo credits cannot be
    # trusted (realised/market above the ratio) - never because its rate merely
    # looked unattractive.
    basket_names: list[str] = []
    excluded: dict[str, str] = {}
    for name, acc in sorted(per_contract.items()):
        ratio = acc["realised_over_market"]
        if ratio is not None and ratio > artefact_ratio:
            excluded[name] = f"模拟盘到账是市场费率的 {ratio:g}x（产物，不可外推）"
            continue
        if acc["market_median_bps"] is None:
            excluded[name] = "缺市场费率历史"
            continue
        basket_names.append(name)

    rates = [per_contract[n]["market_median_bps"] for n in basket_names]
    basket_bps = round(statistics.median(rates), 3) if rates else None
    realised_rates = [per_contract[n]["realised_bps_per_day"] for n in basket_names
                      if per_contract[n]["realised_bps_per_day"] is not None]
    return {
        "basket_names": basket_names,
        "excluded": excluded,
        "basket_market_bps_per_day": basket_bps,
        "basket_realised_bps_per_day": (round(statistics.median(realised_rates), 3)
                                        if realised_rates else None),
        "breakeven_days": (round(round_trip_bps / basket_bps, 1)
                           if basket_bps and basket_bps > 0 else None),
        "net_bps_per_30d_hold": (round(basket_bps * 30 - round_trip_bps, 1)
                                 if basket_bps is not None else None),
        "realised_days": len(days_seen),
        "realised_window": [days_seen[0], days_seen[-1]] if days_seen else None,
        "sample_ok": len(days_seen) >= min_days,
        "per_contract": per_contract,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate-dir", default="data/history/funding")
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--days", type=int, default=30, help="rate-history window, in days")
    ap.add_argument("--bill-days", type=int, default=14, help="how far back to pull bills")
    ap.add_argument("--artefact-ratio", type=float, default=DEFAULT_ARTEFACT_RATIO,
                    help="realised/market above this marks a demo artefact")
    ap.add_argument("--round-trip-bps", type=float, default=DEFAULT_ROUND_TRIP_BPS)
    ap.add_argument("--min-days", type=int, default=5)
    ap.add_argument("--offline", action="store_true", help="skip the realised panel")
    ap.add_argument("--out", default="data/carry/income_report.json")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    rate_dir = PROJECT / args.rate_dir
    names = carried_names(PROJECT / "data/carry/pairs.json")
    realised: dict[str, dict[str, dict]] = {}
    notionals: dict[str, float] = {}
    bills: list[dict] = []
    if not args.offline:
        try:
            bills, notionals = fetch_realised(
                args.config_dir, args.venue, args.env_file, args.bill_days
            )
            realised = realised_by_day(bills)
        except Exception as exc:  # noqa: BLE001 - offline is a supported mode
            print(f"!! 读取场馆失败（{type(exc).__name__}: {exc}）")
            print("   已跳过已实现面板；可加 --offline 只跑费率面板\n")
        else:
            for bill in bills:
                name = contract_name(str(bill.get("instId")))
                if name not in names:
                    names.append(name)

    if realised:
        print(f"A. 已实现（模拟盘 type=8 账单，按 UTC 日，共 {len(bills)} 条）")
        for day in sorted(realised):
            print(f"   {day}")
            for inst, entry in sorted(realised[day].items(), key=lambda kv: -kv[1]["usdt"]):
                notional = lookup_notional(inst, notionals)
                bps = entry["usdt"] / notional * 1e4 if notional else None
                notional_text = f"{notional:,.0f}" if notional else "n/a"
                bps_text = f"{bps:.2f}" if bps is not None else "n/a"
                print(f"     {inst:20s}{entry['usdt']:>+12.4f} USDT"
                      f"{entry['settles']:>4d} settles"
                      f"   notional {notional_text:>9}   {bps_text:>6} bps/day")
        print()

    report = build_report(
        realised=realised, notionals=notionals, rate_dir=rate_dir, names=names,
        days=args.days, artefact_ratio=args.artefact_ratio,
        round_trip_bps=args.round_trip_bps, min_days=args.min_days,
    )

    print(f"B. 市场费率隐含（公共 funding-rate-history，窗口 {args.days} 天）")
    for name in sorted(report["per_contract"],
                       key=lambda n: -(report["per_contract"][n]["usdt"] or 0)):
        acc = report["per_contract"][name]
        med = "n/a" if acc["market_median_bps"] is None else f"{acc['market_median_bps']:.3f}"
        mean = "n/a" if acc["market_mean_bps"] is None else f"{acc['market_mean_bps']:.3f}"
        neg = "-" if acc["market_negative_days"] is None else str(acc["market_negative_days"])
        interval = "n/a" if not acc["settle_hours"] else f"{acc['settle_hours']:g}h"
        ratio = "n/a" if acc["realised_over_market"] is None else f"{acc['realised_over_market']:g}x"
        print(f"   {name:10s}days {acc['market_days']:>3}   median {med:>8} bps/day"
              f"   mean {mean:>8}   negative {neg:>3}d   {interval:>6}"
              f"   到账/市场 {ratio:>8}")
    print()

    print("C. 非峰篮子（按市场费率；到账不可信的名字不进篮子）")
    if args.offline:
        print("   （--offline：没取场馆账单，就没有『到账/市场』比值，产物剔除这一步做不了）")
    for name, why in sorted(report["excluded"].items()):
        print(f"   剔除 {name}: {why}")
    print(f"   篮子 = {', '.join(report['basket_names']) or '（空）'}")
    realised_basket = ("n/a" if report["basket_realised_bps_per_day"] is None
                       else f"{report['basket_realised_bps_per_day']} bps/天")
    print(f"   市场费率中位 {report['basket_market_bps_per_day']} bps/天"
          f"｜实测到账中位 {realised_basket}")
    if report["breakeven_days"] is not None:
        verdict = "可行" if report["breakeven_days"] <= 30 else "不可行"
        print(f"   往返 {args.round_trip_bps:g} bps 的回本天数 ≈ {report['breakeven_days']} 天"
              f"（持有上限 30 天：{verdict}）"
              f"｜持有 30 天的净额 {report['net_bps_per_30d_hold']} bps")
    window = report["realised_window"]
    window_text = f"（{' .. '.join(window)}）" if window else ""
    sample = "足够" if report["sample_ok"] else f"不足（<{args.min_days} 天）—— 只作趋势观察"
    print(f"   样本：费率历史 {args.days} 天；实测到账 {report['realised_days']} 天"
          f"{window_text} → {sample}")

    if args.write:
        path = PROJECT / args.out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"params": vars(args), "report": report},
                       ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
