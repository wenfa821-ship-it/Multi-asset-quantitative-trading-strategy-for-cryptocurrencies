#!/usr/bin/env python3
"""Buy the coin that pays a coin-margined option premium (1.1's last prerequisite).

Why this exists
---------------
OKX's BTC options are coin-margined: the premium is paid in BTC, and the smallest
order is ~0.0006 BTC (~$50). The account holds only USDT and the carry legs' coins,
so the sleeve cannot buy a single contract until some USDT becomes BTC. The one
alternative was measured and closed on 2026-10-10: the USD-settled `BTC-USD_UM`
family has 634 live contracts and an empty book (no asks in 6 of 6 sampled
strikes). Converting is the only path.

This is inventory, not a view on BTC - the same way the carry legs hold coins to be
short the perp against. It buys once and stops.

Why `--record` exists
---------------------
A manual fill left out of the ledger is "unmanaged exposure" to the runner, and the
dashboard watchdog warns above $1. That warning is right for a deposit somebody
forgot; it is noise for inventory we bought on purpose, so `--record` writes the
same fill into the ledger through `Ledger.record_fill` with an explicit tag. It is
safe to do so because adoption is OFF (`[risk.account] adopt_existing_positions =
false`): the runner ignores `adopted` fills and never registers an exit plan for a
position no strategy opened, so the coin sits still instead of being sold by a
stop it never asked for.

    set -a; source .env; set +a
    export HTTPS_PROXY=http://127.0.0.1:7897
    python3 scripts/options_fund_premium.py                          # dry run
    python3 scripts/options_fund_premium.py --yes --record --notional 100
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config, load_env_file  # noqa: E402
from ta.models import Fill, OrderRequest  # noqa: E402
from ta.portfolio import Ledger  # noqa: E402
from ta.venues import build_venue  # noqa: E402

TAG = "options-funding"


def wait_for_fill(venue, symbol: str, client_order_id: str, timeout: float = 25.0):
    """OKX answers a market order with status=open, filled=0 and fills a beat later."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for row in venue.ex.fetch_closed_orders(symbol, limit=20) or []:
            if row.get("clientOrderId") == client_order_id:
                return row
        time.sleep(1.0)
    return None


def fill_fields(row: dict, fallback_ccy: str) -> tuple[float, float, float, str]:
    """(filled, average, fee, fee_ccy) from a ccxt order row.

    `fetch_closed_orders` returns `fee` as a DICT ({cost, currency}) while
    `fetch_my_trades` returns a number. The first version of this script assumed the
    number and crashed on the dict - after the order had already filled, so the coin
    was bought and nothing was recorded. Hence this helper, and hence `--recover`.
    """
    fee_raw = row.get("fee")
    if isinstance(fee_raw, dict):
        fee, fee_ccy = float(fee_raw.get("cost") or 0.0), str(fee_raw.get("currency") or fallback_ccy)
    else:
        fee = float(fee_raw or 0.0)
        fee_ccy = str((row.get("info") or {}).get("feeCcy") or fallback_ccy)
    return (float(row.get("filled") or 0.0), float(row.get("average") or 0.0), fee, fee_ccy)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--notional", type=float, default=100.0, help="USDT to convert")
    ap.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--record", action="store_true", help="write the fill into the ledger")
    ap.add_argument("--recover", action="store_true",
                    help="record the newest unrecorded TAoptfn* fill instead of buying")
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    # Same trap the daily job hit on 2026-10-10 (README 50.4): the shell that runs
    # this on a schedule exports the proxy but not the keys, so the venue would be
    # built without credentials and fail before anything else could be explained.
    load_env_file(PROJECT / ".env")
    if not config.venue(args.venue).sandbox:
        raise SystemExit("refusing to run: this is demo-only")
    base = args.symbol.split("/")[0]
    venue = build_venue(config, args.venue)
    venue.connect()

    if args.recover:
        ledger = Ledger(PROJECT / args.ledger, run_id="options-funding", venue=args.venue)
        known = {f.client_order_id for f in Ledger.read_fills(PROJECT / args.ledger)}
        rows = [r for r in (venue.ex.fetch_closed_orders(args.symbol, limit=50) or [])
                if str(r.get("clientOrderId") or "").startswith("TAoptfn")]
        fresh = [r for r in rows if str(r.get("clientOrderId")) not in known]
        if not fresh:
            print(f"nothing to recover: {len(rows)} TAoptfn fill(s) already in the ledger")
            venue.close()
            return 0
        row = max(fresh, key=lambda r: int(r.get("timestamp") or 0))
        filled, average, fee, fee_ccy = fill_fields(row, config.app.base_currency)
        print(f"recovering {row.get('clientOrderId')}: {filled} {base} @ {average:,.2f} fee={fee} {fee_ccy}")
        ledger.record_fill(
            Fill(
                client_order_id=str(row.get("clientOrderId")),
                symbol=args.symbol,
                side="buy",
                qty=filled,
                price=average,
                fee=fee,
                fee_ccy=fee_ccy,
                ts=int(row.get("timestamp") or time.time() * 1000),
                tag=TAG,
            )
        )
        print(f"recorded in {args.ledger} with tag={TAG}")
        venue.close()
        return 0

    price = float((venue.ex.fetch_ticker(args.symbol) or {}).get("last") or 0.0)
    if price <= 0:
        raise SystemExit("no price; aborting")
    qty = float(venue.ex.amount_to_precision(args.symbol, args.notional / price))
    balance = venue.ex.fetch_balance({"type": "spot"})
    held = float((balance.get(base) or {}).get("total") or 0.0)
    print(f"{args.symbol} last={price:,.2f}  convert {args.notional:.2f} USDT -> {qty} {base}")
    print(f"{base} held before = {held}")
    if not args.yes:
        print("\nDRY RUN - re-run with --yes (and --record to put it in the ledger)")
        venue.close()
        return 0

    client_order_id = f"TAoptfn{int(time.time())}"
    order = venue.place_order(
        OrderRequest(
            client_order_id=client_order_id,
            symbol=args.symbol,
            side="buy",
            qty=qty,
            type="market",
            tag=TAG,
        )
    )
    print(f"submit -> status={order.status} reason={order.reason or '-'}")
    if order.status == "rejected":
        venue.close()
        return 2
    row = wait_for_fill(venue, args.symbol, client_order_id)
    if row is None:
        print("fill not confirmed within timeout - NOT recording anything")
        venue.close()
        return 2
    filled, average, fee, fee_ccy = fill_fields(row, config.app.base_currency)
    ts = int(row.get("timestamp") or time.time() * 1000)
    print(f"filled {filled} {base} @ {average:,.2f}  fee={fee} {fee_ccy}")
    after = float((venue.ex.fetch_balance({"type": "spot"}).get(base) or {}).get("total") or 0.0)
    print(f"{base} held after  = {after}  (delta {after - held:+.8f})")
    venue.close()

    if not args.record:
        print("\nNOT recorded: the position will show up as unmanaged exposure "
              "(watchdog warns above $1). Re-run with --record, or record it yourself.")
        return 0
    ledger = Ledger(PROJECT / args.ledger, run_id="options-funding", venue=args.venue)
    ledger.record_fill(
        Fill(
            client_order_id=client_order_id,
            symbol=args.symbol,
            side="buy",
            qty=filled,
            price=average,
            fee=fee,
            fee_ccy=fee_ccy,
            ts=ts,
            tag=TAG,
        )
    )
    print(f"recorded in {args.ledger} with tag={TAG} - the book owns it from the next tick")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
