#!/usr/bin/env python3
"""Minimal OKX-demo SWAP probe for the carry leg (README section 26.4, step 5).

Carry = long spot + short perpetual, and delta neutrality depends entirely on
that short perpetual leg existing. The venue work was written for the trend
strategy that section 25 later falsified, so this probe answers - against the
real demo venue rather than in theory - whether the swap leg actually works:

  1. can we OPEN a short      (tdMode=cross, posSide=short, no reduceOnly)
  2. is the funding rate readable for that same contract
  3. can we CLOSE it          (side=buy, posSide=short, reduceOnly=true)
  4. does the venue report a liquidation price while it is open

It is deliberately NOT a strategy and NOT scheduled: one foreground run, one
minimum-size contract, flattened again by the `finally` block even if step 2 or
4 throws - so the demo account is left exactly as it was found. Nothing here can
reach the live account: the venue comes from --config-dir (which keeps
sandbox=true) and the keys are the demo ones.

    set -a; source .env; set +a
    export HTTPS_PROXY=http://127.0.0.1:7897
    python3 scripts/carry_probe.py --config-dir config-exp
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
from ta.venues.swap_math import (  # noqa: E402
    ContractSpec,
    SwapMathError,
    base_to_contracts,
    contracts_to_base,
)


def _hr(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 56 - len(title)))


def _wait_fill(venue, symbol: str, client_order_id: str, timeout: float = 20.0):
    """OKX market orders come back `status=open, filled=0` and fill asynchronously.

    Treating that first reply as final is what made the first run of this probe
    return 2 and walk away from a live short, so waiting is not politeness - it
    is the difference between "rejected" and "we own a position now".
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        for row in venue.ex.fetch_closed_orders(symbol, limit=20) or []:
            if row.get("clientOrderId") == client_order_id:
                last = row
                break
        if last is not None:
            return last
        time.sleep(1.0)
    return last


def _open_contracts(venue, symbol: str) -> float:
    total = 0.0
    for row in venue.ex.fetch_positions([symbol]) or []:
        total += abs(float(row.get("contracts") or 0.0))
    return total


def _pick_swap_symbol(venue, wanted: str) -> str:
    """Resolve the ccxt symbol for an OKX perp, tolerating the :USDT suffix."""
    markets = venue.ex.markets or {}
    if wanted in markets and markets[wanted].get("contract"):
        return wanted
    base = wanted.split("/")[0].split(":")[0]
    for symbol, market in markets.items():
        if (
            market.get("contract")
            and market.get("linear")
            and market.get("base") == base
            and market.get("quote") == "USDT"
        ):
            return symbol
    raise SystemExit(f"no USDT perpetual found for {wanted!r} (have {len(markets)} markets)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config-exp")
    ap.add_argument("--venue", default="okx_demo_swap")
    ap.add_argument("--symbol", default="SOL/USDT")
    ap.add_argument("--notional", type=float, default=200.0, help="target USD notional")
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    section = config.venue(args.venue)
    print(f"venue={args.venue} exchange={section.exchange_id} "
          f"market_type={section.market_type} sandbox={section.sandbox}")
    if not section.sandbox:
        raise SystemExit("refusing to run: this probe is demo-only")

    venue = build_venue(config, args.venue)
    venue.connect()

    symbol = _pick_swap_symbol(venue, args.symbol)
    market = venue.ex.markets[symbol]
    info = market.get("info") or {}
    print(f"symbol={symbol}  contractSize={market.get('contractSize')}  "
          f"limits.min={((market.get('limits') or {}).get('amount') or {}).get('min')}")

    spec = None
    if info.get("ctVal"):
        try:
            spec = ContractSpec.from_okx(info)
        except SwapMathError as exc:
            print(f"  (ctVal present but unusable: {exc})")
    if spec is not None:
        print(f"  ctVal={spec.ct_val} {spec.ct_val_ccy}  lotSz={spec.lot_sz}  "
              f"minSz={spec.min_sz}  state={spec.state}")

    # ---- 0. account mode (posSide semantics depend on it) -------------------
    _hr("0. account mode")
    try:
        cfg = venue.ex.privateGetAccountConfig()
        row = (cfg.get("data") or [{}])[0]
        print(f"  posMode={row.get('posMode')}  acctLv={row.get('acctLv')}  "
              f"acctStpMode={row.get('acctStpMode')}")
    except Exception as exc:  # noqa: BLE001 - informational only
        print(f"  (account config unavailable: {exc})")

    # ---- 1. funding rate (read-only, before any order) ----------------------
    _hr("1. funding rate")
    funding = {}
    try:
        funding = venue.ex.fetch_funding_rate(symbol) or {}
        rate = funding.get("fundingRate")
        print(f"  fundingRate={rate}  next={funding.get('fundingTimestamp')}  "
              f"interval={funding.get('interval')}")
        if rate is not None:
            # Short perp RECEIVES when the rate is positive: 3 settlements/day.
            print(f"  short receives ~{float(rate) * 3 * 100:.4f}% per day "
                  f"at this instant's rate")
    except Exception as exc:  # noqa: BLE001
        print(f"  FETCH FAILED: {exc}")

    # ---- size: one legal contract, floored to lotSz -------------------------
    price = float((venue.ex.fetch_ticker(symbol) or {}).get("last") or 0.0)
    if price <= 0:
        raise SystemExit("no last price; aborting before any order")
    print(f"  last={price}")

    if spec is not None:
        try:
            contracts = base_to_contracts(spec, args.notional / price)
        except SwapMathError as exc:
            print(f"  {args.notional} USD -> {exc}; falling back to minSz")
            contracts = spec.min_sz
        base_qty = contracts_to_base(spec, contracts)
    else:
        size = float(market.get("contractSize") or 1.0) or 1.0
        contracts = max(1.0, round(args.notional / price / size))
        base_qty = contracts * size
    print(f"  -> {contracts} contracts = {base_qty} {args.symbol.split('/')[0]} "
          f"~ {base_qty * price:.2f} USD")

    # OKX rejects anything but alphanumerics in clOrdId (sCode 51000 "Parameter
    # clOrdId error"), so no hyphens here - the loop already sanitises its own ids
    # via OMS.new_client_order_id, and this probe has to match that.
    stamp = int(time.time())
    opened = False
    rc = 0
    try:
        # ---- 2. open the short ---------------------------------------------
        _hr("2. OPEN short (tdMode=cross, posSide=short)")
        open_id = f"TAprobop{stamp}"
        order = venue.place_order(
            OrderRequest(
                client_order_id=open_id,
                symbol=symbol,
                side="sell",
                qty=base_qty,
                type="market",
                tag="carry-probe",
            )
        )
        print(f"  status={order.status} filled={order.filled_qty} "
              f"avg={order.avg_price} fee={order.fee} {order.fee_ccy} "
              f"venue_id={order.venue_order_id}")
        if order.reason:
            print(f"  reason={order.reason}")
        if order.status == "rejected":
            print("  OPEN REJECTED - nothing to flatten")
            return 2
        filled = _wait_fill(venue, symbol, open_id)
        if filled is None:
            print("  OPEN NOT CONFIRMED within timeout - leaving nothing behind")
            return 2
        info = filled.get("info") or {}
        print(f"  confirmed: state={info.get('state')} filled={filled.get('filled')} "
              f"avg={filled.get('average')} fee={info.get('fee')} {info.get('feeCcy')}")
        opened = _open_contracts(venue, symbol) > 0
        print(f"  open contracts now = {_open_contracts(venue, symbol)}")
        if not opened:
            return 2

        # ---- 3. position + liquidation price --------------------------------
        _hr("3. position as the venue sees it")
        for row in venue.ex.fetch_positions([symbol]) or []:
            if abs(float(row.get("contracts") or 0.0)) < 1e-12:
                continue
            print(f"  side={row.get('side')} contracts={row.get('contracts')} "
                  f"notional={row.get('notional')} entry={row.get('entryPrice')} "
                  f"mark={row.get('markPrice')}")
            print(f"  LEVERAGE={row.get('leverage')} "
                  f"LIQUIDATION_PX={row.get('liquidationPrice')} "
                  f"margin={row.get('initialMargin')} "
                  f"unrealized={row.get('unrealizedPnl')}")
        acct = venue.get_account()
        print(f"  get_account -> equity={acct.equity:.2f} cash={acct.cash:.2f} "
              f"positions={ {s: round(p.qty, 6) for s, p in acct.positions.items()} }")
    finally:
        if opened:
            # ---- 4. close it, reduceOnly, same posSide ----------------------
            _hr("4. CLOSE short (reduceOnly, posSide=short)")
            # The venue maps an exit's posSide from default_pos_side; a carry leg
            # is short, and leaving the "long" default would ask OKX to close a
            # long that does not exist.
            venue.default_pos_side = "short"
            live_contracts = _open_contracts(venue, symbol)
            close_qty = (
                contracts_to_base(spec, live_contracts) if spec is not None else base_qty
            ) or base_qty
            print(f"  closing {live_contracts} contracts = {close_qty} base units")
            close = venue.place_order(
                OrderRequest(
                    client_order_id=f"TAprobcl{stamp}",
                    symbol=symbol,
                    side="buy",
                    qty=close_qty,
                    type="market",
                    reduce_only=True,
                    tag="carry-probe",
                )
            )
            print(f"  status={close.status} filled={close.filled_qty} "
                  f"avg={close.avg_price} reason={close.reason}")
            deadline = time.time() + 20.0
            while time.time() < deadline and _open_contracts(venue, symbol) > 0:
                time.sleep(1.0)
            residual = _open_contracts(venue, symbol)
            print(f"  residual contracts: {residual}")
            if residual > 0:
                rc = 3
                print("  !! STILL OPEN - flatten manually")

    venue.close()
    _hr("result")
    print(f"  short-leg usable={opened and rc == 0} "
          f"funding_readable={bool(funding.get('fundingRate') is not None)}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
