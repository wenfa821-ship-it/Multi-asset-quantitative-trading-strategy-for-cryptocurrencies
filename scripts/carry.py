#!/usr/bin/env python3
"""Carry desk: long spot + short perp, on OKX demo. No strategy layer.

Why a separate tool
-------------------
Section 25 killed every *directional* entry we had: a fixed-seed coin flip beat
all four factor strategies, which means the factors select worse-than-random
entries. Carry is the one path that survived, and it survives precisely because
it forecasts nothing - it sells the funding stream against a spot long, so the
price cancels out and only the funding, the basis, and the fees are left.

Section 26 priced it: 34bps round trip, breakeven 17-57 days (median 24),
2.8%-5.75% annualised (median 3.5%), positive on 12 of 13 symbols. That is a
small number that only survives if the legs are cheap and the book stays flat.
So the whole point of this file is the *mechanics*: buy the spot, short the perp
for the same base quantity, and prove the delta is ~0.

Sub-commands
------------
    plan     rank symbols by net annualised funding from data/history/funding
    open     buy spot  + short perp  (dry-run unless --yes)
    status   both legs, delta, funding, liquidation price
    close    buy back perp (reduceOnly) + sell spot (dry-run unless --yes)

Nothing is scheduled and no position is left behind by accident: every venue
call is printed, and `open` verifies the short before it returns.

    set -a; source .env; set +a
    export HTTPS_PROXY=http://127.0.0.1:7897
    python3 scripts/carry.py plan --days 30
    python3 scripts/carry.py open  --symbol SOL/USDT --notional 200 --yes
    python3 scripts/carry.py status
    python3 scripts/carry.py close --symbol SOL/USDT --yes
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.funding import load_funding_dir  # noqa: E402
from ta.models import OrderRequest  # noqa: E402
from ta.venues import build_venue  # noqa: E402
from ta.venues.swap_math import ContractSpec, SwapMathError, contracts_to_base  # noqa: E402


def _hr(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 56 - len(title)))


def _perp_symbol(spot_symbol: str) -> str:
    base, _, quote = spot_symbol.partition("/")
    return f"{base}/{quote}:{quote}"


def _spec(venue, symbol: str) -> ContractSpec | None:
    info = ((venue.ex.markets or {}).get(symbol) or {}).get("info") or {}
    if not info.get("ctVal"):
        return None
    try:
        return ContractSpec.from_okx(info)
    except SwapMathError:
        return None


def _wait_fill(venue, symbol: str, client_order_id: str, timeout: float = 20.0):
    """OKX market orders answer `status=open, filled=0` and fill a moment later."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for row in venue.ex.fetch_closed_orders(symbol, limit=20) or []:
            if row.get("clientOrderId") == client_order_id:
                return row
        time.sleep(1.0)
    return None


def _submit(venue, request: OrderRequest, symbol_for_poll: str):
    order = venue.place_order(request)
    print(f"  submit {request.side:4s} {request.qty:.8g} {request.symbol} "
          f"-> status={order.status} reason={order.reason or '-'}")
    if order.status == "rejected":
        return order, None
    filled = _wait_fill(venue, symbol_for_poll, request.client_order_id)
    if filled is not None:
        info = filled.get("info") or {}
        print(f"    filled={filled.get('filled')} avg={filled.get('average')} "
              f"fee={info.get('fee')} {info.get('feeCcy')} pnl={info.get('pnl')}")
    else:
        print("    (fill not confirmed within timeout)")
    return order, filled


def _hard(x: float) -> float:
    """None-proof float."""
    return float(x or 0.0)


def cmd_plan(args, config: Config) -> int:
    series = load_funding_dir(PROJECT / "data" / "history" / "funding")
    if not series:
        print("no funding history under data/history/funding - run scripts/funding_basis.py first")
        return 1

    now_ms = int(time.time() * 1000)
    window_ms = int(args.days * 86_400_000)
    spot_fee = _hard(config.venue(args.spot_venue).fee_bps)
    perp_fee = _hard(config.venue(args.perp_venue).fee_bps)
    slip = _hard(config.venue(args.perp_venue).slippage_bps)
    # one round trip = 2 spot legs + 2 perp legs + 2 slippage estimates
    round_trip_bps = 2 * spot_fee + 2 * perp_fee + 2 * slip

    print(f"round-trip cost assumption: {round_trip_bps:.1f} bps "
          f"(spot {spot_fee}bps x2 + perp {perp_fee}bps x2 + slippage {slip}bps x2)")
    rows = []
    for name, fs in sorted(series.items()):
        cutoff = now_ms - window_ms
        rates = [r for ts, r in zip(fs.timestamps, fs.rates) if ts >= cutoff]
        if len(rates) < 3:
            continue
        mean = sum(rates) / len(rates)
        daily_bps = mean * 3 * 10_000.0          # 3 settlements per day
        annual_pct = daily_bps * 365 / 100.0
        breakeven_days = (round_trip_bps / daily_bps) if daily_bps > 0 else float("inf")
        rows.append(
            (name, len(rates), daily_bps, annual_pct, breakeven_days)
        )

    rows.sort(key=lambda r: -r[2])
    print(f"\n{'symbol':28s} {'n':>4s} {'bps/day':>9s} {'ann%':>7s} {'breakeven d':>12s}")
    for name, n, daily, ann, be in rows:
        be_s = "n/a" if be == float("inf") else f"{be:.1f}"
        print(f"{name:28s} {n:4d} {daily:9.2f} {ann:7.2f} {be_s:>12s}")
    pos = sum(1 for r in rows if r[2] > 0)
    print(f"\n{pos}/{len(rows)} symbols had positive mean funding over {args.days}d")
    print("carry pays only if you can hold past the breakeven column - and the")
    print("short leg can be liquidated in a violent upside move (not in this table).")
    return 0


def cmd_open(args, config: Config) -> int:
    spot_symbol = args.symbol
    perp_symbol = _perp_symbol(spot_symbol)
    spot = build_venue(config, args.spot_venue)
    perp = build_venue(config, args.perp_venue)
    spot.connect()
    perp.connect()

    price = _hard((perp.ex.fetch_ticker(perp_symbol) or {}).get("last"))
    if price <= 0:
        print("no perp price; aborting")
        return 1
    qty = args.notional / price
    spec = _spec(perp, perp_symbol)
    if spec is not None:
        qty = contracts_to_base(spec, max(spec.min_sz, round(qty / spec.ct_val / spec.lot_sz) * spec.lot_sz))
        print(f"perp spec: 1 contract = {spec.ct_val} {spec.ct_val_ccy}, "
              f"lotSz={spec.lot_sz}, minSz={spec.min_sz}")
    print(f"target: {args.notional:.2f} USD -> {qty:.8g} {spot_symbol.split('/')[0]} "
          f"at {price:.2f}")

    rate = {}
    try:
        rate = perp.ex.fetch_funding_rate(perp_symbol) or {}
        print(f"funding now: {rate.get('fundingRate')} "
              f"(short receives {_hard(rate.get('fundingRate')) * 3 * 100:.4f}%/day)")
    except Exception as exc:  # noqa: BLE001
        print(f"funding unavailable: {exc}")

    if not args.yes:
        print("\nDRY RUN - re-run with --yes to actually open both legs")
        spot.close()
        perp.close()
        return 0

    stamp = int(time.time())
    _hr("1/2 buy spot")
    _, spot_fill = _submit(
        spot,
        OrderRequest(client_order_id=f"TAcarsp{stamp}", symbol=spot_symbol,
                     side="buy", qty=qty, type="market", tag="carry"),
        spot_symbol,
    )
    _hr("2/2 short perp")
    _, perp_fill = _submit(
        perp,
        OrderRequest(client_order_id=f"TAcarpp{stamp}", symbol=perp_symbol,
                     side="sell", qty=qty, type="market", tag="carry"),
        perp_symbol,
    )
    # The spot buy may have been charged a fee in the base asset, so the two legs
    # are only "delta neutral" to within that fee. Report the residual honestly.
    held = _spot_free(spot, spot_symbol)
    short = _open_contracts(perp, perp_symbol)
    short_base = contracts_to_base(spec, short) if spec is not None else short
    print(f"\nspot held={held:.8g}  perp short={short_base:.8g}  "
          f"delta residual={held - short_base:+.8g}")
    cmd_status(args, config)
    spot.close()
    perp.close()
    return 0


def _spot_free(venue, symbol: str) -> float:
    base = symbol.split("/")[0]
    try:
        bal = venue.ex.fetch_balance({"type": "spot"})
    except Exception as exc:  # noqa: BLE001
        print(f"  (balance unavailable: {exc})")
        return 0.0
    return _hard((bal.get(base) or {}).get("free"))


def _open_contracts(venue, symbol: str) -> float:
    total = 0.0
    for row in venue.ex.fetch_positions([symbol]) or []:
        total += abs(_hard(row.get("contracts")))
    return total


def cmd_status(args, config: Config) -> int:
    spot_symbol = args.symbol
    perp_symbol = _perp_symbol(spot_symbol)
    spot = build_venue(config, args.spot_venue)
    perp = build_venue(config, args.perp_venue)
    spot.connect()
    perp.connect()

    price = _hard((perp.ex.fetch_ticker(perp_symbol) or {}).get("last"))
    held = _spot_free(spot, spot_symbol)
    rows = [r for r in (perp.ex.fetch_positions([perp_symbol]) or [])
            if abs(_hard(r.get("contracts"))) > 1e-12]
    spec = _spec(perp, perp_symbol)

    _hr(f"{spot_symbol} carry book")
    print(f"  mark           {price:,.4f}")
    print(f"  spot leg       {held:.8g} {spot_symbol.split('/')[0]} "
          f"= {held * price:,.2f} USD")
    if not rows:
        print("  perp leg       FLAT")
        delta = held
    else:
        row = rows[0]
        # A short perp is NEGATIVE exposure: delta = spot long + perp (signed).
        contracts = _hard(row.get("contracts")) * (-1 if row.get("side") == "short" else 1)
        base_qty = contracts_to_base(spec, contracts) if spec is not None else contracts
        delta = held + base_qty      # a short perp is negative exposure
        print(f"  perp leg       {row.get('side')} {abs(base_qty):.8g} "
              f"({abs(_hard(row.get('contracts'))):g} contracts) "
              f"entry={row.get('entryPrice')} uPnL={row.get('unrealizedPnl')}")
        print(f"  leverage       {row.get('leverage')}x  "
              f"margin={row.get('initialMargin')}  "
              f"LIQUIDATION_PX={row.get('liquidationPrice')}")
    print(f"  DELTA          {delta:+.8g} {spot_symbol.split('/')[0]} "
          f"({delta * price:+,.2f} USD)")
    try:
        rate = perp.ex.fetch_funding_rate(perp_symbol) or {}
        print(f"  funding rate   {rate.get('fundingRate')} "
              f"-> short receives {_hard(rate.get('fundingRate')) * 3 * 100:.4f}%/day "
              f"on {held * price:,.0f} USD = "
              f"{held * price * _hard(rate.get('fundingRate')) * 3:,.3f} USD/day")
    except Exception as exc:  # noqa: BLE001
        print(f"  funding rate   unavailable ({exc})")
    spot.close()
    perp.close()
    return 0


def cmd_close(args, config: Config) -> int:
    spot_symbol = args.symbol
    perp_symbol = _perp_symbol(spot_symbol)
    spot = build_venue(config, args.spot_venue)
    perp = build_venue(config, args.perp_venue)
    spot.connect()
    perp.connect()

    held = _spot_free(spot, spot_symbol)
    short = _open_contracts(perp, perp_symbol)
    spec = _spec(perp, perp_symbol)
    short_base = contracts_to_base(spec, short) if spec is not None else short
    print(f"to unwind: spot {held:.8g} / perp short {short:.8g} contracts "
          f"({short_base:.8g} base)")
    if not args.yes:
        print("DRY RUN - re-run with --yes to unwind")
        spot.close()
        perp.close()
        return 0

    stamp = int(time.time())
    if short > 0:
        _hr("1/2 buy back perp (reduceOnly)")
        # A carry leg is a SHORT, and the venue maps an exit's posSide from
        # default_pos_side - leaving the long default would ask OKX to close a
        # long that does not exist.
        perp.default_pos_side = "short"
        _submit(
            perp,
            OrderRequest(client_order_id=f"TAcarcc{stamp}", symbol=perp_symbol,
                         side="buy", qty=short_base, type="market",
                         reduce_only=True, tag="carry"),
            perp_symbol,
        )
        deadline = time.time() + 20.0
        while time.time() < deadline and _open_contracts(perp, perp_symbol) > 0:
            time.sleep(1.0)
        print(f"  residual contracts: {_open_contracts(perp, perp_symbol)}")

    if held > 0:
        _hr("2/2 sell spot")
        _submit(
            spot,
            OrderRequest(client_order_id=f"TAcarsl{stamp}", symbol=spot_symbol,
                         side="sell", qty=held, type="market", tag="carry"),
            spot_symbol,
        )
        print(f"  spot left: {_spot_free(spot, spot_symbol):.8g}")

    spot.close()
    perp.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config-dir", default="config-exp")
    ap.add_argument("--spot-venue", default="okx_demo")
    ap.add_argument("--perp-venue", default="okx_demo_swap")
    ap.add_argument("--symbol", default="SOL/USDT")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--days", type=int, default=30)

    o = sub.add_parser("open")
    o.add_argument("--notional", type=float, default=200.0)
    o.add_argument("--yes", action="store_true")
    # SUPPRESS so `carry.py open --symbol X` and `carry.py --symbol X open` both
    # work: the sub-parser only overwrites when the flag is actually given.
    o.add_argument("--symbol", default=argparse.SUPPRESS)

    s = sub.add_parser("status")
    s.add_argument("--symbol", default=argparse.SUPPRESS)

    c = sub.add_parser("close")
    c.add_argument("--yes", action="store_true")
    c.add_argument("--symbol", default=argparse.SUPPRESS)

    args = ap.parse_args()
    config = Config.load(args.config_dir)
    for name in (args.spot_venue, args.perp_venue):
        section = config.venue(name)
        if not section.sandbox:
            raise SystemExit(f"refusing: venue {name!r} is not sandbox")
    return {
        "plan": cmd_plan,
        "open": cmd_open,
        "status": cmd_status,
        "close": cmd_close,
    }[args.cmd](args, config)


if __name__ == "__main__":
    raise SystemExit(main())
