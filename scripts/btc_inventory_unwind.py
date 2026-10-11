#!/usr/bin/env python3
"""Sell the BTC that was bought as option-premium inventory, and close the books.

Why this exists (2026-10-11, user decision)
-------------------------------------------
On 2026-10-10 ~$100 of USDT was converted to BTC so the options sleeve could pay a
coin-margined premium, and the coin was deliberately kept OUT of the ledger as
`reserved_inventory` (recording it made the runner treat it as its own position and
try to sell it - changelog 2026-10-10T19:30). The sleeve then turned out to be
unexecutable in this venue, so the user decided to sell the inventory back.

Selling it raises venue cash by ~$92 with no modelled flow, and the residual is
already judged by drift - so this script does the accounting too: it sells the
balance, then records the WHOLE round trip (the original buy, the 2026-10-11 probe
sell, and today's sell) from the venue's own order records, all with one tag. The
book then nets to ~0 and the cash identity closes.

Refuses a non-sandbox venue; prints the plan first; `--yes` trades.

    python3 scripts/btc_inventory_unwind.py            # plan
    python3 scripts/btc_inventory_unwind.py --yes      # sell + record
"""

from __future__ import annotations

import argparse
import datetime
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config, load_env_file  # noqa: E402
from ta.models import Fill, OrderRequest  # noqa: E402
from ta.portfolio import Ledger, PositionBook  # noqa: E402
from ta.venues import build_venue  # noqa: E402

TAG = "btc-premium-inventory"
# The manual orders that moved this inventory, so the round trip can be booked whole.
# client id -> what it did (recorded with the venue's own fill numbers).
PRIOR_ORDERS = ("TAoptfn1791630605", "TAprobe1791633619")


def fill_fields(row: dict, fallback_ccy: str) -> tuple[float, float, float, str]:
    fee_raw = row.get("fee")
    if isinstance(fee_raw, dict):
        return (float(row.get("filled") or 0.0), float(row.get("average") or 0.0),
                float(fee_raw.get("cost") or 0.0), str(fee_raw.get("currency") or fallback_ccy))
    return (float(row.get("filled") or 0.0), float(row.get("average") or 0.0),
            float(fee_raw or 0.0), fallback_ccy)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    if not config.venue(args.venue).sandbox:
        raise SystemExit("refusing to run: this is demo-only")
    load_env_file(PROJECT / ".env")
    venue = build_venue(config, args.venue)
    venue.connect()
    base = args.symbol.split("/")[0]

    balance = venue.ex.fetch_balance({"type": "spot"})
    held = float((balance.get(base) or {}).get("free") or 0.0)
    minimum = venue.min_qty(args.symbol)
    price = float((venue.ex.fetch_ticker(args.symbol) or {}).get("last") or 0.0)
    print(f"{base} free={held}  (venue minimum {minimum})  last={price:,.2f}  "
          f"~${held * price:,.2f}")
    if held < minimum:
        print("nothing sellable (balance at or below the venue minimum)")
        venue.close()
        return 0
    if not args.yes:
        print("\nDRY RUN - re-run with --yes to sell it back and book the round trip")
        venue.close()
        return 0

    stamp = int(time.time())
    cid = f"TAbtcunw{stamp}"
    order = venue.place_order(OrderRequest(
        client_order_id=cid, symbol=args.symbol, side="sell", qty=held,
        type="market", tag=TAG))
    print(f"submit -> status={order.status} reason={(order.reason or '-')[:200]}")
    if order.status == "rejected":
        venue.close()
        return 2
    row = None
    deadline = time.time() + 30
    while time.time() < deadline and row is None:
        for candidate in venue.ex.fetch_closed_orders(args.symbol, limit=30) or []:
            if candidate.get("clientOrderId") == cid:
                row = candidate
        time.sleep(1.0)
    if row is None:
        print("fill not confirmed - NOT booking anything yet; check the account")
        venue.close()
        return 2
    sold, avg, fee, fee_ccy = fill_fields(row, config.app.base_currency)
    print(f"sold {sold} {base} @ {avg:,.2f}  fee={fee} {fee_ccy}")
    venue.close()

    ledger = Ledger(PROJECT / args.ledger, run_id="btc-inventory-unwind", venue=args.venue)
    known = {f.client_order_id for f in Ledger.read_fills(PROJECT / args.ledger)}
    history: list[tuple[str, str, float, float, float, str, int]] = []
    for prior in PRIOR_ORDERS:
        if prior in known:
            continue
        found = None
        venue2 = build_venue(config, args.venue)
        venue2.connect()
        for candidate in venue2.ex.fetch_closed_orders(args.symbol, limit=50) or []:
            if candidate.get("clientOrderId") == prior:
                found = candidate
                break
        venue2.close()
        if found:
            qty, px, f, ccy = fill_fields(found, config.app.base_currency)
            history.append((prior, str(found.get("side")), qty, px, f, ccy,
                            int(found.get("timestamp") or time.time() * 1000)))
    history.append((cid, "sell", sold, avg, fee, fee_ccy,
                    int(row.get("timestamp") or time.time() * 1000)))
    for cid_i, side, qty, px, f, ccy, ts in history:
        ledger.record_fill(Fill(client_order_id=cid_i, symbol=args.symbol, side=side,
                                qty=qty, price=px, fee=f, fee_ccy=ccy, ts=ts, tag=TAG))
        print(f"  recorded {side} {qty} @ {px} (fee {f} {ccy})  {cid_i}")

    book = PositionBook.replay(Ledger.read_fills(PROJECT / args.ledger), base_ccy="USDT")
    position = book.positions.get(args.symbol)
    print(f"book {args.symbol} position now: {position.qty if position else 0.0}")
    print("note: remove BTC/USDT from config/reserved_inventory.json - the holding is gone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
