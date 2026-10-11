#!/usr/bin/env python3
"""Where does the cash residual actually come from? Ask the venue's own bills.

Why this exists (2026-10-11)
----------------------------
The reconciler compares `venue cash at first start + our flow` with the venue's
cash now. That identity can only close if this bot is the only thing touching the
account, and it is not: the demo account has ~2,100 venue bills against our ~150
fills. The project has always read the residual that way - "not zero is normal,
DRIFTING is not" - and on 2026-10-10 the watchdog finally said drifting: -$86 in
eight hours with zero fills of ours.

My first explanation ("unsynced funding, it will snap back after the 09:10 sync")
was **wrong**: the sync booked +$240 of funding and the residual kept falling. So
this measures instead of guessing, in one window:

  * what the VENUE says moved, bill by bill, grouped by bill type;
  * what OUR LEDGER recorded, fills and funding;
  * the difference, per type - which is the leak, named.

Read-only: private GET on the bills endpoint plus local files. No orders.

    python3 scripts/cash_residual_attribution.py --hours 24
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
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config, load_env_file  # noqa: E402
from ta.venues import build_venue  # noqa: E402

# OKX bill types. Only the ones this account actually produces matter, but naming
# them is the difference between "type 8" and "the venue paid funding".
BILL_TYPES = {
    "1": "transfer", "2": "trade", "3": "delivery", "4": "forced repayment",
    "5": "exercise", "6": "instant", "7": "savings", "8": "funding fee",
    "9": "ADL", "11": "system token conversion", "12": "unified transfer",
    "13": "isolated margin transfer", "14": "auto earn", "22": "rebate",
    "24": "liquidation", "26": "spread trade",
}


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def our_cash_flow(rows: list[dict], start_ms: int) -> dict:
    """What OUR book says moved, in USDT: fills (net of fees) and funding.

    A fill moves cash by -qty*price when buying and +qty*price when selling, and
    the fee in the quote currency moves it again. Funding is cash with no position
    attached. This is exactly the flow the runner adds to its baseline.
    """
    fills = [r for r in rows if r.get("kind") == "fill" and r.get("ts", 0) >= start_ms]
    funding = [r for r in rows if r.get("kind") == "funding" and r.get("ts", 0) >= start_ms]
    trade = 0.0
    fees = 0.0
    for row in fills:
        qty = float(row.get("qty") or 0.0)
        price = float(row.get("price") or 0.0)
        trade += (-qty * price) if row.get("side") == "buy" else (qty * price)
        if str(row.get("fee_ccy") or "USDT").upper() == "USDT":
            fees -= float(row.get("fee") or 0.0)
    fund = sum(float(r.get("amount") or 0.0) for r in funding)
    return {
        "fills": len(fills), "trade": trade, "fees": fees, "funding_rows": len(funding),
        "funding": fund, "total": trade + fees + fund,
    }


def summarise_bills(bills: list[dict]) -> dict:
    """Venue bills grouped by (type, subType) with the money each moved."""
    out: dict[tuple[str, str], dict] = {}
    for bill in bills:
        key = (str(bill.get("type") or "?"), str(bill.get("subType") or ""))
        entry = out.setdefault(key, {"count": 0, "amount": 0.0, "ccy": bill.get("ccy")})
        entry["count"] += 1
        entry["amount"] += float(bill.get("pnl") or 0.0) + float(bill.get("fee") or 0.0)
    return out


def fetch_bills(venue, start_ms: int, end_ms: int, max_pages: int = 20) -> list[dict]:
    """Every bill in the window, paging with `after` (one page is 100 bills)."""
    out: list[dict] = []
    seen: set[str] = set()
    cursor = None
    for _ in range(max_pages):
        params: dict = {"limit": "100", "begin": str(start_ms), "end": str(end_ms)}
        if cursor:
            params["after"] = str(cursor)
        rows = venue.ex.privateGetAccountBills(params).get("data") or []
        new = [r for r in rows if str(r.get("billId")) not in seen]
        if not new:
            break
        for row in new:
            seen.add(str(row.get("billId")))
        out.extend(new)
        cursor = min(int(r.get("ts") or 0) for r in new) - 1
        if len(rows) < 100:
            break
        time.sleep(0.3)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    ap.add_argument("--hours", type=float, default=24.0)
    args = ap.parse_args()

    now = datetime.datetime.now(datetime.UTC)
    start = now - datetime.timedelta(hours=args.hours)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    print(f"window {start.isoformat(timespec='seconds')} .. {now.isoformat(timespec='seconds')}")

    rows = load_jsonl(PROJECT / args.ledger)
    ours = our_cash_flow(rows, start_ms)
    equity = [r for r in rows if r.get("kind") == "equity" and r.get("ts", 0) >= start_ms]
    if equity:
        print(f"ledger cash (venue figure, incl. upl): "
              f"{float(equity[0].get('cash') or 0):,.2f} -> {float(equity[-1].get('cash') or 0):,.2f} "
              f"({float(equity[-1].get('cash') or 0) - float(equity[0].get('cash') or 0):+,.2f})")

    config = Config.load(args.config_dir)
    load_env_file(PROJECT / ".env")
    venue = build_venue(config, args.venue)
    venue.connect()
    bills = fetch_bills(venue, start_ms, end_ms)
    balance = venue.ex.fetch_balance({"type": "spot"})
    usdt = balance.get("USDT") or {}
    print(f"venue USDT now: total {float(usdt.get('total') or 0):,.2f} "
          f"(free {float(usdt.get('free') or 0):,.2f})   bills fetched: {len(bills)}")
    venue.close()

    print("\n--- what OUR ledger recorded in this window ---")
    print(f"  fills {ours['fills']}: trade {ours['trade']:+,.2f}, fees {ours['fees']:+,.2f}")
    print(f"  funding {ours['funding_rows']} rows: {ours['funding']:+,.2f}")
    print(f"  our flow total: {ours['total']:+,.2f}")

    print("\n--- what the VENUE says moved (its own bills) ---")
    grouped = summarise_bills(bills)
    total = sum(v["amount"] for v in grouped.values())
    for (btype, subtype), entry in sorted(grouped.items(), key=lambda kv: -abs(kv[1]["amount"])):
        name = BILL_TYPES.get(btype, f"type {btype}")
        print(f"  {name:26s} sub {subtype or '-':<6s} n={entry['count']:>4d} "
              f"amount={entry['amount']:+12.4f} {entry['ccy']}")
    print(f"  venue bills total: {total:+,.2f}")

    print("\n--- the leak ---")
    venue_funding = sum(v["amount"] for (t, _), v in grouped.items() if t == "8")
    venue_trade = sum(v["amount"] for (t, _), v in grouped.items() if t == "2")
    kept = total - venue_funding - venue_trade
    print(f"  funding: venue {venue_funding:+,.2f} vs ours {ours['funding']:+,.2f} "
          f"-> unrecorded {venue_funding - ours['funding']:+,.2f}")
    print(f"  trade:   venue {venue_trade:+,.2f} vs ours {ours['trade'] + ours['fees']:+,.2f} "
          f"-> unrecorded {venue_trade - (ours['trade'] + ours['fees']):+,.2f}")
    print(f"  other bill types: {kept:+,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
