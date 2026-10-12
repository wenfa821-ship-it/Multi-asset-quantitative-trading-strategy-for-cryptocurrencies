#!/usr/bin/env python3
"""Rest ONE option bid inside the venue's band and see if anybody sells. Zero-cost.

Why this exists (2026-10-11)
----------------------------
Measured today, in this order: market orders on options are refused (`50016
instId and ordType don't match`); a limit buy at the DISPLAYED ask is refused by
the price band (`51006 max buy price: 0.0245, min sell price: 0.0001`); and a
limit buy at the band's maximum was ACCEPTED but did not fill in 30 seconds. So
the demo book shows a bid you can hit and an ask you cannot reach, which means the
only way to ACQUIRE an option here is to rest a bid and wait for a seller.

That is a question only waiting can answer, and waiting costs nothing:

  * rest 1 contract at the band's maximum buy price,
  * poll for `--minutes` (default 25),
  * if it fills, the position is real and small (~$24 premium) - leave it and say so,
  * if it does not, cancel it and report "no counterparty in N minutes".

Either outcome is a fact 1.1 needs: "we can buy by resting" or "we cannot buy at
all here". Read-only apart from that one resting order.

    python3 scripts/options_rest_probe.py --minutes 25 --yes
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT / "scripts"))

from ta.config import Config, load_env_file  # noqa: E402
from ta.models import OrderRequest  # noqa: E402
from ta.venues import build_venue  # noqa: E402

from options_round_trip import rank_candidates  # noqa: E402


def band_from(message: str) -> tuple[float | None, float | None]:
    high = re.search(r"max buy price: ([0-9.]+)", message)
    low = re.search(r"min sell price: ([0-9.]+)", message)
    return (float(high.group(1)) if high else None, float(low.group(1)) if low else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo_option")
    ap.add_argument("--family", default="BTC-USD")
    ap.add_argument("--side", default="C", choices=["C", "P"])
    ap.add_argument("--min-dte", type=int, default=5)
    ap.add_argument("--max-dte", type=int, default=70)
    ap.add_argument("--target-dte", type=int, default=20)
    ap.add_argument("--contracts", type=int, default=1)
    ap.add_argument("--minutes", type=float, default=25.0)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    if not config.venue(args.venue).sandbox:
        raise SystemExit("refusing to run: this is demo-only")
    load_env_file(PROJECT / ".env")
    venue = build_venue(config, args.venue)
    venue.connect()
    if not venue.ensure_markets():                # 同上：没有目录就没法选合约
        print(f"no option catalogue - the venue did not answer "
              f"({getattr(venue, 'last_connect_error', '') or 'unknown'})")
        return 1

    base_ccy = args.family.split("-")[0]
    spot = float((venue.ex.publicGetMarketTicker(
        {"instId": f"{base_ccy}-USDT"}).get("data") or [{}])[0].get("last") or 0.0)
    today = datetime.datetime.now(datetime.UTC).date()
    ranked = rank_candidates(list(venue.ex.markets.values()), args.family, today,
                             args.target_dte, args.min_dte, args.max_dte, args.side, spot)
    candidate = None
    for row in ranked[:30]:
        bid, ask = venue.top_of_book(row["symbol"])
        if bid and ask:
            candidate, book_bid, book_ask = row, bid, ask
            break
    if candidate is None:
        print("no contract with a two-sided book")
        return 1
    from ta.venues.swap_math import ContractSpec

    base_per_contract = ContractSpec.from_okx(candidate["info"]).base_per_contract
    print(f"{candidate['inst_id']}  ({candidate['dte']}d, strike {candidate['strike']:g})  "
          f"book {book_bid}/{book_ask}")

    stamp = int(time.time())
    cid = f"TAoptrst{stamp}"
    price = book_bid  # start AT the bid: a maker price that is inside any sane band
    order = venue.place_order(OrderRequest(
        client_order_id=cid, symbol=candidate["symbol"], side="buy",
        qty=base_per_contract * args.contracts, type="limit", price=price,
        tag="options-rest-probe"))
    if order.status == "rejected" and "51006" in str(order.reason):
        max_buy, _ = band_from(str(order.reason))
        if max_buy:
            print(f"bid {price} outside the band; resting at the band maximum {max_buy}")
            price = max_buy
            order = venue.place_order(OrderRequest(
                client_order_id=cid + "b", symbol=candidate["symbol"], side="buy",
                qty=base_per_contract * args.contracts, type="limit", price=max_buy,
                tag="options-rest-probe"))
            cid = cid + "b"
    print(f"submit -> status={order.status} price={price} reason={(order.reason or '-')[:200]}")
    if order.status == "rejected":
        venue.close()
        return 2

    deadline = time.time() + args.minutes * 60
    filled = 0.0
    while time.time() < deadline:
        state = venue.order_state(cid, candidate["symbol"])
        status, filled = state if state else ("unknown", 0.0)
        if filled > 0:
            print(f"\nFILLED after {args.minutes * 60 - (deadline - time.time()):.0f}s: "
                  f"{filled} contract(s) at {price} - the demo DOES sell into a resting bid")
            print(f"position is real and small (~${price * base_per_contract * spot:,.2f}); "
                  f"it is NOT auto-closed by anything")
            venue.close()
            return 0
        if status in ("canceled", "rejected"):
            print(f"\norder became {status} without filling")
            venue.close()
            return 1
        time.sleep(30.0)

    print(f"\nno counterparty in {args.minutes:g} minutes - cancelling")
    print("cancel ->", venue.cancel_order(cid, candidate["symbol"]))
    time.sleep(2.0)
    print("open now:", venue.open_order_ids([candidate["symbol"]]))
    print("state:", venue.order_state(cid, candidate["symbol"]))
    venue.close()
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
