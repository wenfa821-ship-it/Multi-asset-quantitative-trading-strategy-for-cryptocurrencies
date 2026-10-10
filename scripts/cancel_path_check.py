#!/usr/bin/env python3
"""Prove the two things a passive (post-only) entry depends on, for free.

Why this exists
---------------
`carry-postonly` wants the spot leg to enter with `ordType=post_only` - place a
resting order, wait briefly, and if it has not filled, CANCEL IT and fall back to
market. The venue layer already supports the flag (`OrderRequest.post_only`), but
nothing has ever exercised either half against the real exchange, and both have a
bad failure mode:

  * if `post_only` is not actually honoured, the "passive" order TAKES liquidity
    and we pay the same spread we were trying to avoid;
  * if the CANCEL fails, a live order is left resting that can fill at any time -
    and in carry's case that is an unhedged spot leg, the exact state the pair
    executor exists to prevent.

So this script tests both with orders that cannot fill or cost anything:

  1. **post-only is honoured** - send a buy 1 tick ABOVE the ask. A normal limit
     order would take it; a post-only order must not. Measured 2026-10-09: OKX
     does NOT reject it at submit time (that was the assumption, and it is wrong).
     It accepts the order and then KILLS it a moment later with
     `cancelSource=31` ("would take liquidity"), `accFillSz=0`. So the pass
     condition is the END STATE, not the submit status - which is also why the
     caller must never read `status=open` as "resting".
  2. **cancel works** - send a buy far BELOW the market (default 50% down) where
     it can only rest, confirm it appears on the open-order list, confirm no
     balance moved, then cancel it and confirm it is gone. Retries the cancel;
     if it still cannot cancel, it says so in the loudest terms it has, because
     that is the one outcome that leaves a live order behind.

Read-only against the ledger (it never writes one), like order_path_check.py.

    set -a; source .env; set +a
    export HTTPS_PROXY=http://127.0.0.1:7897
    python3 scripts/cancel_path_check.py                      # dry run
    python3 scripts/cancel_path_check.py --yes --symbol SOL/USDT
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
    except Exception as exc:  # noqa: BLE001 - a read failure must not look like a pass
        print(f"    (balance unavailable: {exc})")
        return -1.0
    return float((balance.get(base) or {}).get("free") or 0.0)


def _open_ids(venue, symbol: str) -> set[str]:
    try:
        rows = venue.ex.fetch_open_orders(symbol) or []
    except Exception as exc:  # noqa: BLE001
        print(f"    (open orders unavailable: {exc})")
        return set()
    return {str((r.get("info") or {}).get("clOrdId") or r.get("clientOrderId") or "") for r in rows}


def _final_state(venue, symbol: str, client_order_id: str, tries: int = 10) -> dict:
    """The exchange's own record of what became of the order.

    This is the only trustworthy answer: `place_order` returns `status=open` for
    a post-only order that the venue is about to kill, so the submit result says
    nothing about whether liquidity was taken.
    """
    last: dict = {}
    for _ in range(tries):
        try:
            rows = (venue.ex.privateGetTradeOrdersHistory(
                {"instType": "SPOT", "instId": symbol.replace("/", "-"), "limit": "20"}
            ).get("data") or [])
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows:
            if str(row.get("clOrdId") or "") == client_order_id:
                last = row
        if last and str(last.get("state")) in ("canceled", "filled"):
            return last
        time.sleep(1.0)
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--symbol", default="SOL/USDT")
    ap.add_argument("--notional", type=float, default=23.0, help="USD per test order")
    ap.add_argument("--offset-pct", type=float, default=50.0, help="how far below market the resting buy sits")
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

    ticker = venue.ex.fetch_ticker(args.symbol) or {}
    last, bid, ask = ticker.get("last"), ticker.get("bid"), ticker.get("ask")
    print(f"{args.symbol} last={last} bid={bid} ask={ask}")
    if not last or not ask:
        raise SystemExit("no usable last/ask; aborting")

    stamp = int(time.time())
    # ---- test 1: a post-only order that would cross must be REJECTED --------
    crossing = venue.ex.price_to_precision(args.symbol, float(ask) * 1.001)
    qty = venue.ex.amount_to_precision(args.symbol, args.notional / float(crossing))
    print(f"\n=== 1/2 post-only must NOT take liquidity (buy 1 tick above ask @ {crossing}) ===")
    print(f"    size {qty} {base} (~${args.notional:.2f} if it had filled)")
    post_only_ok = False
    if not args.yes:
        print("    DRY RUN - re-run with --yes to send it")
    else:
        cid_post = f"TApost{stamp}"
        order = venue.place_order(
            OrderRequest(
                client_order_id=cid_post,
                symbol=args.symbol,
                side="buy",
                qty=float(qty),
                type="limit",
                price=float(crossing),
                post_only=True,
                tag="post-check",
            )
        )
        print(f"    submit -> status={order.status} reason={(order.reason or '-')[:160]}")
        final = _final_state(venue, args.symbol, cid_post)
        filled = float(final.get("accFillSz") or 0.0)
        state = final.get("state")
        source = final.get("cancelSource")
        print(f"    final  -> state={state} accFillSz={filled} cancelSource={source} ordType={final.get('ordType')}")
        if filled > 0:
            print("    !! FAIL: a post-only order TOOK liquidity - passive entry buys nothing")
        elif state == "canceled" and str(source) == "31":
            post_only_ok = True
            print("    PASS: the venue killed it before it could take (cancelSource=31), zero fill")
        elif state == "canceled":
            post_only_ok = True
            print(f"    PASS-ish: ended canceled with zero fill (cancelSource={source})")
        elif state == "rejected" or order.status == "rejected":
            post_only_ok = True
            print("    PASS: the venue refused to let a post-only order take liquidity")
        else:
            print(f"    !! INCONCLUSIVE: still state={state} after 10s while crossing the spread.")
            print("    !! Treat post-only as unverified until this is understood.")
            venue.cancel_order(cid_post, args.symbol)

    # ---- test 2: place far below the market, confirm it rests, then cancel ---
    print(f"\n=== 2/2 rest away from the market, then CANCEL ===")
    target = float(last) * (1.0 - args.offset_pct / 100.0)
    price = venue.ex.price_to_precision(args.symbol, target)
    qty2 = venue.ex.amount_to_precision(args.symbol, args.notional / float(price))
    print(f"    resting buy {qty2} {base} @ {price} ({args.offset_pct:.0f}% below last)")
    if not args.yes:
        print("\nDRY RUN - re-run with --yes to place and cancel for real")
        venue.close()
        return 0

    before = _free(venue, base)
    cid = f"TArst{stamp}"
    order = venue.place_order(
        OrderRequest(
            client_order_id=cid,
            symbol=args.symbol,
            side="buy",
            qty=float(qty2),
            type="limit",
            price=float(price),
            post_only=True,
            tag="rest-check",
        )
    )
    print(f"    submit -> status={order.status} reason={(order.reason or '-')[:160]}")
    if order.status == "rejected":
        print("RESULT: cannot rest an order at all - nothing left to cancel (no risk taken)")
        venue.close()
        return 2

    rested = False
    for _ in range(10):
        if cid in _open_ids(venue, args.symbol):
            rested = True
            break
        time.sleep(1.0)
    print(f"    visible on the open-order list: {rested}")

    cancelled = False
    for attempt in range(1, 4):
        if venue.cancel_order(cid, args.symbol):
            time.sleep(1.0)
            if cid not in _open_ids(venue, args.symbol):
                cancelled = True
                break
        print(f"    cancel attempt {attempt} did not clear it")
        time.sleep(1.0)

    after = _free(venue, base)
    print(f"    free {base}: before={before} after={after}")
    print("\n=== result ===")
    print(f"    post-only kills a crossing order : {post_only_ok}")
    print(f"    rested then removed: {cancelled}")
    print(f"    balance untouched  : {abs(after - before) < 1e-9 if before >= 0 and after >= 0 else 'unknown'}")
    if not cancelled:
        print("    !! A LIVE ORDER MAY STILL BE RESTING. Cancel it by hand before")
        print("    !! wiring post-only into the carry entry - that is the whole point")
        print("    !! of this check.")
    venue.close()
    return 0 if (cancelled and post_only_ok) else 3


if __name__ == "__main__":
    raise SystemExit(main())
