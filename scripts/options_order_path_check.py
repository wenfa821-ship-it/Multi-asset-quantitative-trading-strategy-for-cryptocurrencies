#!/usr/bin/env python3
"""Prove an option order can be RESTED and CANCELLED on this account. Costs nothing.

Why this exists (2026-10-11)
----------------------------
The 1.1 sleeve has seven prerequisites, a written structure and a gate at 4/5 - and
the adapter has **never sent a real option order**. Two questions remain about the
entry path, and only one of them costs money:

  * does OKX accept our option order parameters (tdMode=cross, no posSide, no
    reduceOnly, 1 contract = 0.01 BTC), and can we cancel what we rest?
  * what does a market order actually fill at on the DEMO book, whose bid sits
    45-50% away from its ask?

This script answers the FIRST one for free, the same way `cancel_path_check.py`
settled the passive spot entry: rest a LIMIT BUY far below the market (it cannot
fill there), read the order's own state, cancel it, and confirm it is gone.
Nothing is bought, nothing is sold, no premium is paid.

The second question needs the paid round trip (`options_round_trip.py --yes`) and
is deliberately not part of this script.

    python3 scripts/options_order_path_check.py            # plan only
    python3 scripts/options_order_path_check.py --yes      # rest + cancel one order
"""

from __future__ import annotations

import argparse
import datetime
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


def pick_liquid(venue, family: str, side: str, reference: float, min_dte: int,
                max_dte: int, target_dte: int):
    """The first contract in the window that actually has a two-sided book."""
    today = datetime.datetime.now(datetime.UTC).date()
    ranked = rank_candidates(list(venue.ex.markets.values()), family, today,
                             target_dte, min_dte, max_dte, side, reference)
    for candidate in ranked[:30]:
        bid, ask = venue.top_of_book(candidate["symbol"])
        if bid and ask:
            return candidate, bid, ask, len(ranked)
    return None, None, None, len(ranked)


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
    ap.add_argument("--below-bid-pct", type=float, default=50.0,
                    help="rest this far BELOW the best bid, so it cannot fill")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    if not config.venue(args.venue).sandbox:
        raise SystemExit("refusing to run: this is demo-only")
    load_env_file(PROJECT / ".env")
    venue = build_venue(config, args.venue)
    venue.connect()

    base_ccy = args.family.split("-")[0]
    spot = float((venue.ex.publicGetMarketTicker(
        {"instId": f"{base_ccy}-USDT"}).get("data") or [{}])[0].get("last") or 0.0)
    contract, bid, ask, listed = pick_liquid(venue, args.family, args.side, spot,
                                             args.min_dte, args.max_dte, args.target_dte)
    if contract is None:
        print(f"no {args.family} {args.side} contract with a two-sided book "
              f"({listed} listed in window) - nothing to check")
        return 1

    from ta.venues.swap_math import ContractSpec

    base_per_contract = ContractSpec.from_okx(contract["info"]).base_per_contract
    tick = float(contract["info"].get("tickSz") or 0.0001)
    price = round(bid * (1 - args.below_bid_pct / 100.0), 6)
    price = max(tick, price)
    print(f"{contract['inst_id']}  ({contract['dte']}d, strike {contract['strike']:g})")
    print(f"book bid={bid} ask={ask}  -> resting BUY at {price} "
          f"({args.below_bid_pct:g}% below the bid; cannot fill there)")
    print(f"1 contract = {base_per_contract} BTC; notional at the ask ≈ "
          f"${ask * base_per_contract * spot:,.2f}")
    if not args.yes:
        print("\nDRY RUN - re-run with --yes to rest and cancel one order")
        venue.close()
        return 0

    stamp = int(time.time())
    cid = f"TAoptpc{stamp}"
    order = venue.place_order(OrderRequest(
        client_order_id=cid, symbol=contract["symbol"], side="buy",
        qty=base_per_contract * args.contracts, type="limit", price=price,
        tag="options-path-check"))
    print(f"\nsubmit -> status={order.status} price={price} "
          f"reason={(order.reason or '-')[:160]}")
    if order.status == "rejected":
        print("REJECTED - the entry path does not accept these parameters yet")
        venue.close()
        return 2
    time.sleep(2.0)
    state = venue.order_state(cid, contract["symbol"])
    status, filled = state if state else ("unknown", 0.0)
    print(f"order state after 2s: status={status} filled={filled}")
    if filled > 0:
        print("UNEXPECTED FILL - not cancelling; this position is real (small) and "
              "should be closed by hand or with options_round_trip.py")
        venue.close()
        return 3
    canceled = venue.cancel_order(cid, contract["symbol"])
    print(f"cancel -> {canceled}")
    time.sleep(1.5)
    after = venue.order_state(cid, contract["symbol"])
    final_status, final_filled = after if after else ("unknown", 0.0)
    open_ids = venue.open_order_ids([contract["symbol"]])
    still_open = None if open_ids is None else (cid in open_ids)
    print(f"after cancel: status={final_status} filled={final_filled} "
          f"still open? {still_open}")
    ok = final_status in ("canceled", "rejected") and still_open is False
    print(f"\nRESULT: {'entry path OK (accepted, rested, cancelled)' if ok else 'CHECK - state unexpected'}")
    venue.close()
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
