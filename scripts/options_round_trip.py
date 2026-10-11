#!/usr/bin/env python3
"""Buy one option, sell it straight back. The smallest order that proves the path.

Why this exists (1.1, 2026-10-10)
---------------------------------
Seven prerequisites for the options sleeve are in place - units, minimum size,
one-way order params, instId<->ccxt mapping, the premium coin, the book survey,
the structure spec, the daily gate - and **not one option order has ever been
sent**. Everything above was measured with public endpoints or unit tests, so the
claim "we can buy one" is still an inference.

This turns it into a fact, at the smallest possible size, without touching the
runner or any strategy:

  * pick the contract the spec would pick (ATM +/-2.5%, 25-45 DTE),
  * buy ONE contract at market,
  * read the position back,
  * sell it straight back at market,
  * report what it actually cost: fill vs mid on both sides, the venue's fee, and
    the round trip as a percentage of the premium.

The number it produces is the one the spec's cost model needs: the survey put the
median spread at ~2.16% of mid, but that is the BOOK's width, not what a market
order pays. This is what a market order pays.

Refuses a non-sandbox venue and prints the plan before doing anything; `--yes` is
required. One contract, both legs, no strategy, no runner.

    python3 scripts/options_round_trip.py                      # plan only
    python3 scripts/options_round_trip.py --yes                # buy and sell 1
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config, load_env_file  # noqa: E402
from ta.models import OrderRequest  # noqa: E402
from ta.venues import build_venue  # noqa: E402


def dte_of(inst_id: str, today: datetime.date) -> int:
    parts = inst_id.split("-")
    stamp = parts[2] if len(parts) > 2 else ""
    try:
        expiry = datetime.date(2000 + int(stamp[:2]), int(stamp[2:4]), int(stamp[4:6]))
    except (ValueError, IndexError):
        return -999
    return (expiry - today).days


def pick_contract(markets: list[dict], family: str, today: datetime.date,
                  target_dte: int, min_dte: int, max_dte: int,
                  side: str, forward: float) -> dict | None:
    """The contract the spec's selection rules would choose.

    Split out from the network code so the choice is testable: options chains change
    every day, the rule does not.
    """
    candidates = rank_candidates(markets, family, today, target_dte, min_dte, max_dte,
                                 side, forward)
    return candidates[0] if candidates else None


def rank_candidates(markets: list[dict], family: str, today: datetime.date,
                    target_dte: int, min_dte: int, max_dte: int,
                    side: str, reference: float) -> list[dict]:
    """Every contract in the window, best first.

    Returned as a LIST because the first choice is not always tradable: measured
    2026-10-10, the demo catalogue lists contracts whose book is simply empty
    (`BTC-USD-261106-72000-C`: 0 bids, 0 asks) while others have four levels a side.
    Picking by expiry and moneyness is necessary but not sufficient - the caller has
    to check that a market exists before ordering into it.
    """
    candidates = []
    for market in markets:
        info = market.get("info") or {}
        inst_id = str(info.get("instId") or "")
        if not inst_id.startswith(f"{family}-") or str(info.get("uly")) != family:
            continue
        if not inst_id.endswith(f"-{side}"):
            continue
        days = dte_of(inst_id, today)
        if days < min_dte or days > max_dte:
            continue
        try:
            strike = float(info.get("stk"))
        except (TypeError, ValueError):
            continue
        candidates.append({
            "symbol": market.get("symbol"), "inst_id": inst_id, "dte": days,
            "strike": strike, "info": info,
            "offset_pct": (strike / reference - 1) * 100 if reference else 0.0,
        })
    # Nearest the target expiry, then nearest the money - the spec's order of
    # preference (price width falls with both).
    candidates.sort(key=lambda row: (abs(row["dte"] - target_dte), abs(row["offset_pct"])))
    return candidates


def wait_for_fill(venue, symbol: str, client_order_id: str, timeout: float = 30.0):
    """OKX answers a market order with status=open, filled=0 and fills a beat later."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for row in venue.ex.fetch_closed_orders(symbol, limit=20) or []:
            if row.get("clientOrderId") == client_order_id:
                return row
        for row in venue.ex.fetch_open_orders(symbol, limit=20) or []:
            if row.get("clientOrderId") == client_order_id and float(row.get("filled") or 0) > 0:
                return row
        time.sleep(1.0)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo_option")
    ap.add_argument("--family", default="BTC-USD")
    ap.add_argument("--side", default="C", choices=["C", "P"])
    ap.add_argument("--target-dte", type=int, default=30)
    ap.add_argument("--min-dte", type=int, default=25)
    ap.add_argument("--max-dte", type=int, default=45)
    ap.add_argument("--contracts", type=int, default=1)
    ap.add_argument("--out", default="data/options/round_trip.jsonl")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    section = config.venue(args.venue)
    if not section.sandbox:
        raise SystemExit("refusing to run: this is demo-only")
    load_env_file(PROJECT / ".env")
    venue = build_venue(config, args.venue)
    venue.connect()
    if not venue.ex.markets:
        print("no option catalogue - the venue did not answer")
        return 1

    today = datetime.datetime.now(datetime.UTC).date()
    summary = (venue.ex.publicGetPublicOptSummary({"uly": args.family}).get("data") or [{}])
    forward = 0.0
    for row in summary:
        for key in ("fwdPx", "idxPx", "ulyPx"):
            if row.get(key):
                forward = float(row[key])
                break
        if forward:
            break
    if not forward:
        raise SystemExit("no forward price for that family; aborting")

    # The demo catalogue's own `fwdPx` is not the market: on 2026-10-10 it read
    # 72,592 while spot was ~82,800, so "ATM" by that number points at strikes whose
    # books are empty. Moneyness is measured against the real underlying instead,
    # the stale figure is kept in the report for the record.
    spot = 0.0
    try:
        base_ccy = args.family.split("-")[0]
        row = (venue.ex.publicGetMarketTicker({"instId": f"{base_ccy}-USDT"}).get("data") or [{}])[0]
        spot = float(row.get("last") or 0.0)
    except Exception:  # noqa: BLE001 - a missing spot price just falls back
        spot = 0.0
    reference = spot or forward

    all_markets = list(venue.ex.markets.values())
    ranked = rank_candidates(all_markets, args.family, today, args.target_dte,
                             args.min_dte, args.max_dte, args.side, reference)
    contract = symbol = inst_id = None
    bid = ask = 0.0
    outside_spec_band = False
    for window in ((args.min_dte, args.max_dte), (5, 70)):
        if window != (args.min_dte, args.max_dte):
            outside_spec_band = True
            ranked = rank_candidates(all_markets, args.family, today, args.target_dte,
                                     window[0], window[1], args.side, reference)
            if ranked:
                print(f"NOTE: nothing in the spec's {args.min_dte}-{args.max_dte}d band has a "
                      f"two-sided book; widening to {window[0]}-{window[1]}d (measured "
                      f"2026-10-10: the demo venue quotes 6/20/48d only)")
        for candidate in ranked[:30]:
            candidate_bid, candidate_ask = venue.top_of_book(candidate["symbol"])
            if not (candidate_bid and candidate_ask):
                continue      # listed, but no market to trade into
            contract, symbol, inst_id = candidate, candidate["symbol"], candidate["inst_id"]
            bid, ask = candidate_bid, candidate_ask
            break
        if contract is not None:
            break
    if contract is None:
        print(f"No {args.family} {args.side} contract between {args.min_dte} and "
              f"{args.max_dte} DTE has a two-sided book "
              f"({len(ranked)} listed in the window) - nothing to trade today.")
        return 1
    # Base units per contract from the VENUE's own spec (ctVal x ctMult = 0.01 BTC),
    # not ccxt's `contractSize`, which reports 1.0 for these and would make a
    # 1-contract order look like 1 BTC of notional.
    from ta.venues.swap_math import ContractSpec

    base_per_contract = ContractSpec.from_okx(contract["info"]).base_per_contract
    mid = (bid + ask) / 2 if bid and ask else 0.0
    print(f"family {args.family}  spot {spot:,.2f}  (venue fwdPx {forward:,.2f})")
    print(f"chosen {inst_id}  ({contract['dte']}d, strike {contract['strike']:g}, "
          f"offset {contract['offset_pct']:+.1f}%)")
    print(f"  (of {len(ranked)} listed in window; first with a two-sided book)")
    print(f"ccxt symbol {symbol}  1 contract = {base_per_contract} BTC")
    print(f"book bid={bid} ask={ask} mid={mid}"
          + (f"  spread/mid={(ask - bid) / mid * 100:.2f}%" if mid else ""))
    if not ask:
        print("no ask to buy into; aborting")
        return 1
    qty_base = base_per_contract * args.contracts
    premium_btc = ask * base_per_contract * args.contracts
    print(f"plan: BUY {args.contracts} contract ({qty_base} BTC base) at market ≈ "
          f"{premium_btc:.6f} BTC ≈ ${premium_btc * reference:,.2f}, then SELL it back")
    if not args.yes:
        print("\nDRY RUN - re-run with --yes to place both legs")
        venue.close()
        return 0

    stamp = int(time.time())
    result: dict = {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "venue": args.venue, "inst_id": inst_id, "contracts": args.contracts,
        "forward": forward, "spot": spot, "dte": contract["dte"], "strike": contract["strike"],
        "outside_spec_dte_band": outside_spec_band,
        "book_bid": bid, "book_ask": ask, "book_mid": mid,
    }
    # LIMIT, crossing the ask - not market. Measured 2026-10-11: OKX refuses
    # `ordType=market` on options outright with sCode 50016 "instId and ordType
    # don't match", so the taker has to name a price. A buy at the ask is the
    # crossing limit that means "pay the ask".
    def band_from(message: str) -> tuple[float | None, float | None]:
        """OKX puts the executable band inside a 51006 rejection:
        'max buy price: 0.0245, min sell price: 0.0001'. The displayed ask is NOT
        the price you may pay - measured 2026-10-11 on this contract, the ask sat at
        0.038 while the maximum buyable price was 0.0245, which is why a market
        order is refused (50016) and a limit at the ask is refused too (51006).
        """
        import re as _re

        high = _re.search(r"max buy price: ([0-9.]+)", message)
        low = _re.search(r"min sell price: ([0-9.]+)", message)
        return (float(high.group(1)) if high else None,
                float(low.group(1)) if low else None)

    print(f"\n=== BUY (limit @ ask {ask}) ===")
    buy = venue.place_order(OrderRequest(
        client_order_id=f"TAoptrb{stamp}", symbol=symbol, side="buy",
        qty=qty_base, type="limit", price=ask, tag="options-roundtrip:buy"))
    if buy.status == "rejected" and "51006" in str(buy.reason):
        max_buy, _min_sell = band_from(str(buy.reason))
        if max_buy:
            print(f"  ask {ask} is outside the venue's band; retrying at the "
                  f"maximum buyable price {max_buy}")
            result["ask_was_outside_band"] = True
            result["max_buy_price"] = max_buy
            ask = max_buy
            buy = venue.place_order(OrderRequest(
                client_order_id=f"TAoptrb{stamp}b", symbol=symbol, side="buy",
                qty=qty_base, type="limit", price=max_buy, tag="options-roundtrip:buy"))
            stamp = stamp  # client id for the fill lookup is chosen below
            buy_id = f"TAoptrb{stamp}b"
        else:
            buy_id = f"TAoptrb{stamp}"
    else:
        buy_id = f"TAoptrb{stamp}"
    # Print the venue's whole message: OKX puts the executable band inside a
    # rejection ("max buy price ..."), and that number is the actionable part.
    print(f"  submit -> status={buy.status} reason={(buy.reason or '-')[:400]}")
    if buy.status == "rejected":
        result["error"] = f"buy rejected: {buy.reason}"
        _record(args, result)
        venue.close()
        return 2
    buy_row = wait_for_fill(venue, symbol, buy_id)
    if buy_row is None:
        print("  fill not confirmed - NOT selling (an option is a position, not a mistake)")
        result["error"] = "buy fill unconfirmed"
        _record(args, result)
        venue.close()
        return 2
    buy_filled = float(buy_row.get("filled") or 0.0)
    buy_avg = float(buy_row.get("average") or 0.0)
    buy_fee = float((buy_row.get("fee") or {}).get("cost") or 0.0)
    buy_fee_ccy = str((buy_row.get("fee") or {}).get("currency") or "BTC")
    print(f"  filled {buy_filled} contract @ {buy_avg}  fee={buy_fee} {buy_fee_ccy}")
    result.update({"buy_contracts": buy_filled, "buy_price": buy_avg,
                   "buy_fee": buy_fee, "buy_fee_ccy": buy_fee_ccy})

    print(f"=== SELL (limit @ bid {bid}, closing the same size immediately) ===")
    sell = venue.place_order(OrderRequest(
        client_order_id=f"TAoptrs{stamp}", symbol=symbol, side="sell",
        qty=buy_filled * base_per_contract, type="limit", price=bid,
        tag="options-roundtrip:sell"))
    print(f"  submit -> status={sell.status} reason={(sell.reason or '-')[:160]}")
    sell_row = None if sell.status == "rejected" else wait_for_fill(venue, symbol, f"TAoptrs{stamp}")
    if sell_row is None:
        # A limit sell that does not fill is a fact about the demo book, not a code
        # bug: pull it so nothing is left resting, and leave the long position on.
        pulled = venue.cancel_order(f"TAoptrs{stamp}", symbol) if sell.status != "rejected" else False
        print(f"  SELL DID NOT FILL at the bid ({bid}) - cancelled={pulled}. "
              f"That means the demo bid is not executable; the long option is still open "
              f"(expires worthless at worst).")
        result["error"] = f"sell unfilled at bid {bid}: {sell.reason}"
        result["sell_unfilled_at_bid"] = True
        _record(args, result)
        venue.close()
        return 3
    sell_filled = float(sell_row.get("filled") or 0.0)
    sell_avg = float(sell_row.get("average") or 0.0)
    sell_fee = float((sell_row.get("fee") or {}).get("cost") or 0.0)
    print(f"  filled {sell_filled} contract @ {sell_avg}  fee={sell_fee}")

    cost_btc = (buy_avg - sell_avg) * buy_filled * base_per_contract + buy_fee + sell_fee
    premium_btc = buy_avg * buy_filled * base_per_contract
    result.update({
        "sell_contracts": sell_filled, "sell_price": sell_avg, "sell_fee": sell_fee,
        "cost_btc": cost_btc, "cost_usd": cost_btc * reference,
        "cost_pct_of_premium": (cost_btc / premium_btc * 100) if premium_btc else None,
        "buy_vs_mid_pct": ((buy_avg - mid) / mid * 100) if mid else None,
        "sell_vs_mid_pct": ((sell_avg - mid) / mid * 100) if mid else None,
    })
    print(f"\nround trip cost {cost_btc:.8f} BTC ≈ ${cost_btc * reference:,.2f} "
          f"= {(cost_btc / premium_btc * 100) if premium_btc else 0:.2f}% of the premium")
    print(f"buy filled {(buy_avg - mid) / mid * 100 if mid else 0:+.2f}% vs mid, "
          f"sell filled {(sell_avg - mid) / mid * 100 if mid else 0:+.2f}% vs mid")
    _record(args, result)
    venue.close()
    return 0


def _record(args, result: dict) -> None:
    path = Path(args.out)
    if not path.is_absolute():
        path = PROJECT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"recorded -> {path}")


if __name__ == "__main__":
    raise SystemExit(main())
