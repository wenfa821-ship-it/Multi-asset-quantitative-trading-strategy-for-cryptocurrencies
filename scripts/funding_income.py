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

Idempotent per UTC day. Read-only against the venue.

    python3 scripts/funding_income.py            # report
    python3 scripts/funding_income.py --write    # append today's row
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.venues import build_venue  # noqa: E402


def load_env_file(path: Path) -> int:
    """The launchd job exports the proxy but not the API keys (README 50.4)."""
    if not path.exists():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


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


def summarise(bills: list[dict], positions: dict[str, float]) -> dict:
    """Per-symbol income and the implied bps/day against the live perp notional."""
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
        entry["bps_per_day"] = round(entry["usdt"] / notional * 10_000.0, 2)
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

    summary = summarise(bills, notional)
    total = sum(v["usdt"] for v in summary.values())
    day = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    print(f"{'contract':<20}{'USDT':>12}{'settles':>9}{'notional':>11}{'bps/day':>10}")
    for inst, entry in sorted(summary.items(), key=lambda kv: -kv[1]["usdt"]):
        notional_text = f"{entry['notional_usd']:,.0f}" if entry["notional_usd"] else "n/a"
        bps = entry["bps_per_day"]
        bps_text = f"{bps:.1f}" if bps is not None else "n/a"
        print(f"{inst:<20}{entry['usdt']:>+12.4f}{entry['settlements']:>9}"
              f"{notional_text:>11}{bps_text:>10}")
    print(f"\ntotal over the last {args.days}d: {total:+.4f} USDT")

    row = {"date": day, "days": args.days, "total_usdt": round(total, 6),
           "by_contract": summary}
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
