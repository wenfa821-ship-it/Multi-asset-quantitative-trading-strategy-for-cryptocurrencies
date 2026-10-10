#!/usr/bin/env python3
"""Prove the PRODUCTION order path works, with one minimum-size demo order.

Why this exists
---------------
Between 2026-10-06 (account moved to 跨币种保证金模式, acctLv=3) and 2026-10-07 the
runner could not open anything: OKX refused every spot order with `51000 Parameter
tdMode error` because `tdMode` was pinned to `cash`, which is only legal on a
Simple-mode account. The fix (read acctLv at connect, use `cross` when >=2, and
state `tgtCcy=base_ccy` on market buys so the size is not silently read as USDT)
is in the code, but **no real order has exercised it yet** - the loop restarted
and then produced no entry signal, so the chain remained unconfirmed.

This script closes that gap deliberately: it builds the venue from the SAME
config the running runner uses (config/, venue okx_demo), buys the smallest
sensible size on a symbol the runner actually trades, checks that the quantity it
received matches the quantity it asked for, then sells it straight back.

What it does NOT do
-------------------
* It never touches the production ledger, so reconciliation is unaffected (a
  manual fill written into the ledger would be a lie about who opened it).
* It does not prove that a strategy can produce a signal - only that a signal
  that does appear can reach OKX and come back as a fill.
* It refuses to run against a non-sandbox venue.

    set -a; source .env; set +a
    export HTTPS_PROXY=http://127.0.0.1:7897
    python3 scripts/order_path_check.py                     # dry run
    python3 scripts/order_path_check.py --yes --notional 23 # really do it
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.models import OrderRequest  # noqa: E402
from ta.venues import build_venue  # noqa: E402


def _free(venue, base: str) -> float:
    try:
        balance = venue.ex.fetch_balance({"type": venue.market_type})
    except Exception as exc:  # noqa: BLE001
        print(f"    (balance unavailable: {exc})")
        return 0.0
    entry = balance.get(base) or {}
    return float(entry.get("free") or 0.0)


def _wait(venue, symbol: str, client_order_id: str, timeout: float = 25.0):
    """OKX answers a market order with status=open, filled=0 and fills a beat later."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for row in venue.ex.fetch_closed_orders(symbol, limit=20) or []:
            if row.get("clientOrderId") == client_order_id:
                return row
        time.sleep(1.0)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config", help="PRODUCTION config by default")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--symbol", default="SOL/USDT")
    ap.add_argument("--notional", type=float, default=23.0, help="USD to spend")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    section = config.venue(args.venue)
    if not section.sandbox:
        raise SystemExit("refusing to run: this probe is demo-only")
    base = args.symbol.split("/")[0]
    venue = build_venue(config, args.venue)
    venue.connect()
    print(f"config={args.config_dir} venue={args.venue} market_type={venue.market_type}")
    level = None
    try:
        level = (venue.ex.privateGetAccountConfig().get("data") or [{}])[0].get("acctLv")
    except Exception:  # noqa: BLE001
        pass
    print(f"acctLv={level}  resolved tdMode={venue.spot_td_mode}"
          f"{'   <-- the value that was wrong before 2026-10-07 14:12' if venue.spot_td_mode != 'cross' else ''}")

    price = float((venue.ex.fetch_ticker(args.symbol) or {}).get("last") or 0.0)
    if price <= 0:
        raise SystemExit("no price; aborting")
    qty = round(args.notional / price, 6)
    print(f"{args.symbol} last={price}  target={args.notional:.2f} USD -> {qty} {base}")
    before = _free(venue, base)
    print(f"free {base} before = {before}")
    if not args.yes:
        print("\nDRY RUN - re-run with --yes to place the round trip")
        venue.close()
        return 0

    stamp = int(time.time())
    print("\n=== 1/2 BUY (min size, market) ===")
    buy = venue.place_order(
        OrderRequest(
            client_order_id=f"TApathb{stamp}",
            symbol=args.symbol,
            side="buy",
            qty=qty,
            type="market",
            tag="path-check",
        )
    )
    print(f"  submit -> status={buy.status} reason={buy.reason or '-'}")
    if buy.status == "rejected":
        venue.close()
        return 2
    fill = _wait(venue, args.symbol, f"TApathb{stamp}")
    if fill is None:
        print("  fill not confirmed within timeout")
        venue.close()
        return 2
    info = fill.get("info") or {}
    got = float(fill.get("filled") or 0.0)
    print(f"  confirmed: filled={got} avg={fill.get('average')} "
          f"fee={info.get('fee')} {info.get('feeCcy')}")
    after = _free(venue, base)
    print(f"  free {base} after  = {after}  (delta {after - before:+.6f})")
    # The unit trap: with tdMode=cross ccxt stops sending tgtCcy, OKX then reads a
    # market buy's size as QUOTE currency and "23 SOL" would arrive as 23 USDT
    # worth. Compare what arrived against what was asked.
    ratio = got / qty if qty else 0.0
    print(f"  quantity check: asked {qty} {base}, venue filled {got} {base} "
          f"({ratio:.4f}x)")
    unit_ok = 0.97 <= ratio <= 1.0
    print(f"  UNIT {'OK' if unit_ok else 'WRONG - size was read as quote currency'}")

    print("\n=== 2/2 SELL back (flatten) ===")
    sell_qty = _free(venue, base)
    if sell_qty <= 0:
        print("  nothing to sell - aborting")
        venue.close()
        return 3
    sell = venue.place_order(
        OrderRequest(
            client_order_id=f"TApaths{stamp}",
            symbol=args.symbol,
            side="sell",
            qty=sell_qty,
            type="market",
            tag="path-check",
        )
    )
    print(f"  submit {sell_qty} -> status={sell.status} reason={sell.reason or '-'}")
    sf = _wait(venue, args.symbol, f"TApaths{stamp}")
    if sf is not None:
        sinfo = sf.get("info") or {}
        print(f"  confirmed: filled={sf.get('filled')} avg={sf.get('average')} "
              f"fee={sinfo.get('fee')} {sinfo.get('feeCcy')}")
    leftover = _free(venue, base)
    print(f"  free {base} after sell = {leftover}")

    # "Flat" means back to where we started, not back to zero: the account already
    # held 9.03e-07 SOL of dust before this probe ran, and calling that a failed
    # flatten would be a false alarm about our own order.
    flat = leftover <= before + 1e-9
    print("\n=== result ===")
    print(f"  order accepted      : yes (no 51000 tdMode error)")
    print(f"  quantity semantics  : {'base units, correct' if unit_ok else 'BROKEN'}")
    print(f"  flattened           : {'yes (back to the starting balance)' if flat else f'no, {leftover} left'}")
    print(f"  production ledger   : untouched (this probe never writes it)")
    venue.close()
    return 0 if (unit_ok and flat) else 4


if __name__ == "__main__":
    raise SystemExit(main())
