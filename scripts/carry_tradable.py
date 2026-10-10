#!/usr/bin/env python3
"""Which carry candidates can this account actually trade?

WLFI was the highest-funding candidate and got rejected with `RestrictedLocation`
on every attempt (2026-10-08), which left the carry book unable to open anything -
the strategy kept picking the top name and the top name is geo-blocked.

The probe is deliberately FILL-FREE: it places a LIMIT buy far below the market
(half the last price), so an allowed symbol simply rests until we cancel it, and a
restricted one is rejected outright. No fill, no fee, no position - we are asking
one question, "does OKX accept an order for this instrument from here", and a
market order would answer it by actually buying something.

    set -a; source .env; set +a
    export HTTPS_PROXY=http://127.0.0.1:7897
    python3 scripts/carry_tradable.py --symbols WLFI/USDT,SLX/USDT
    python3 scripts/carry_tradable.py --from-tier carry --write data/carry/tradable.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.models import OrderRequest  # noqa: E402
from ta.venues import build_venue  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--from-tier", default="")
    ap.add_argument("--pause", type=float, default=0.25)
    ap.add_argument("--write", default="")
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    if not config.venue(args.venue).sandbox:
        raise SystemExit("refusing: not a sandbox venue")
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols and args.from_tier:
        symbols = list(config.universe.symbols_by_tier.get(args.from_tier) or [])
    if not symbols:
        raise SystemExit("nothing to probe: pass --symbols or --from-tier")

    venue = build_venue(config, args.venue)
    venue.connect()
    print(f"probing {len(symbols)} symbols on {args.venue} (limit orders, no fills)\n")

    rows: list[dict] = []
    for symbol in symbols:
        market = (venue.ex.markets or {}).get(symbol)
        if not market:
            rows.append({"symbol": symbol, "tradable": False, "reason": "not listed"})
            print(f"  {symbol:<14} NOT LISTED")
            continue
        min_sz = float(((market.get("limits") or {}).get("amount") or {}).get("min") or 0.0)
        if min_sz <= 0:
            rows.append({"symbol": symbol, "tradable": False, "reason": "no min size"})
            print(f"  {symbol:<14} NO MIN SIZE")
            continue
        price = float((venue.ex.fetch_ticker(symbol) or {}).get("last") or 0.0)
        if price <= 0:
            rows.append({"symbol": symbol, "tradable": False, "reason": "no price"})
            print(f"  {symbol:<14} NO PRICE")
            continue
        # far below the market: an accepted order just rests, it cannot fill
        limit = venue.ex.price_to_precision(symbol, price * 0.5)
        request = OrderRequest(
            client_order_id=f"TAtprobe{int(time.time() * 1000) % 10_000_000}",
            symbol=symbol,
            side="buy",
            qty=min_sz,
            type="limit",
            price=float(limit),
            tag="tradable-probe",
        )
        order = venue.place_order(request)
        tradable = order.status not in ("rejected",)
        reason = order.reason or ""
        if tradable and order.venue_order_id:
            try:
                venue.ex.cancel_order(order.venue_order_id, symbol)
            except Exception as exc:  # noqa: BLE001 - a resting order is still tradable
                reason = f"(cancel failed: {type(exc).__name__})"
        rows.append(
            {
                "symbol": symbol,
                "tradable": tradable,
                "status": order.status,
                "reason": reason[:200],
            }
        )
        flag = "OK " if tradable else "BLOCKED"
        print(f"  {symbol:<14} {flag} {order.status:<8} {reason[:70]}")
        if args.pause:
            time.sleep(args.pause)

    ok = [r["symbol"] for r in rows if r["tradable"]]
    blocked = [r["symbol"] for r in rows if not r["tradable"]]
    print(f"\n可交易 {len(ok)}：{', '.join(ok) if ok else '-'}")
    print(f"受限/不可用 {len(blocked)}：{', '.join(blocked) if blocked else '-'}")

    if args.write:
        target = config.path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {"generated_at": int(time.time() * 1000), "venue": args.venue,
                 "tradable": ok, "blocked": blocked, "rows": rows},
                ensure_ascii=False, indent=1,
            ),
            encoding="utf-8",
        )
        print(f"written: {target}")
    venue.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
