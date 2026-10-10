#!/usr/bin/env python3
"""Make the ledger agree with the venue, for positions that were closed BY HAND.

Why this is needed
------------------
The carry clean-up (`scripts/carry_unwind_all.py`) closed the oversized pairs with
raw exchange calls, which do NOT go through the OMS - so those sells were never
written to the ledger. The runner rebuilds its book from the ledger on every
restart, so it kept believing it still held those positions, tried to sell them,
and got `51008 insufficient balance` back from OKX (measured 2026-10-08, NEAR).

That is a bookkeeping gap created by an out-of-band action, not a trading bug, and
the honest repair is the same as the one already applied to ACT: APPEND a
correcting FILL (the ledger is append-only, history is never rewritten) at the
price the venue actually got, so cash and position both come out right.

It is idempotent per symbol (a marker client-order-id), and it only ever corrects
DOWNWARD - if the venue holds more than the ledger says, that is a different
problem and it says so instead of inventing a buy.

    python3 scripts/reconcile_ledger_to_venue.py              # report only
    python3 scripts/reconcile_ledger_to_venue.py --yes        # append the fixes
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

from ta.config import Config  # noqa: E402
from ta.models import Fill  # noqa: E402
from ta.portfolio import Ledger, PositionBook  # noqa: E402
from ta.venues import build_venue  # noqa: E402

# Date-stamped, so a symbol corrected once is not skipped FOREVER. The marker was
# a fixed "…20261008" string, which made `done` a permanent exclusion list: ACT
# was closed by hand on 2026-10-09 and the scanner still reported "nothing to
# correct" because a same-symbol row from the day before existed. A correction
# that already landed is re-checked harmlessly (the comparison then says "ok");
# a NEW out-of-band close is what must not be missed.
MARKER = f"MANUALRECON{datetime.date.today():%Y%m%d}"


def plan_swap_fix(
    symbol: str, local: float, actual: float, avg_price: float
) -> tuple[str, float, float] | None:
    """The correcting fill for one swap symbol, or None when nothing is owed.

    Returns (side, qty, price). Three rules, each of which cost a real incident:

      * only ever REDUCE the book's exposure (a short is closed by buying back,
        a long by selling) - never invent a position the venue has;
      * leave alone the case where the VENUE holds more than the ledger knows,
        because guessing a trade for that is worse than saying so;
      * price at the LEDGER'S OWN entry price, never the market. The position
        being removed never existed at the venue, so the write-off must realise
        nothing. Measured 2026-10-08: pricing the same eleven corrections at the
        market booked **$35,956 of phantom realised PnL** and moved the cash flow
        by +$31,765, which surfaced as a six-figure cash mismatch in the next
        reconciliation.
    """
    gap = local - actual
    if abs(gap) < 1e-9:
        return None
    if abs(local) < abs(actual):
        return None
    if avg_price <= 0:
        return None
    return ("buy" if local < 0 else "sell", abs(gap), float(avg_price))


def _explain_cash(config, rows: list[dict], book: dict[str, float]) -> None:
    """Decompose the cash gap against the venue's OWN bill ledger.

    The runner compares `venue cash at first start + our flow` with the venue's
    cash now. That identity only closes if nothing but this bot ever touched the
    account - and on this demo account it does not: measured 2026-10-08 the venue
    reports **2,404 bills against our 140 fills**, and the residual sits at a
    stable ~$1,062 rather than drifting.

    Two facts this prints instead of guessing:

      * funding, which the book genuinely never records (small: +$12.63 so far);
      * every other bill the account has, which the book cannot see at all.
    """
    venue = build_venue(config, "okx_demo")
    for _ in range(4):
        try:
            venue.connect()
            break
        except Exception:  # noqa: BLE001 - the proxy is flaky; retry briefly
            time.sleep(3)
    try:
        bills: list[dict] = []
        after: str | None = None
        while True:
            params: dict = {"limit": "100"}
            if after:
                params["after"] = after
            page = venue.ex.privateGetAccountBills(params).get("data") or []
            if not page:
                break
            bills += page
            if len(page) < 100:
                break
            after = page[-1]["billId"]
    except Exception as exc:  # noqa: BLE001
        print(f"\ncash explanation unavailable: {type(exc).__name__}: {exc}")
        return
    finally:
        venue.close()

    def total(subset: list[dict]) -> float:
        return sum(float(b.get("pnl") or 0.0) + float(b.get("fee") or 0.0) for b in subset)

    funding = [b for b in bills if str(b.get("type")) == "8"]
    trades = [b for b in bills if str(b.get("type")) == "2"]
    from ta.portfolio import _fill_from_event

    # The book's own cash flow, computed the way the runner computes it - not the
    # sum of notionals, which is a different (and misleading) number for swaps.
    flows = PositionBook.replay(
        [_fill_from_event(r) for r in rows if r.get("kind") == "fill"],
        base_ccy="USDT",
    ).cash
    print("\n--- cash explanation ---")
    print(f"  venue bills           {len(bills):>6}   (our ledger has "
          f"{sum(1 for r in rows if r.get('kind') == 'fill')} fills)")
    print(f"  funding bills (type 8){len(funding):>6}   {total(funding):+12.4f} USDT"
          f"   <- the book records NONE of this")
    print(f"  trade bills   (type 2){len(trades):>6}   {total(trades):+12.4f}")
    print(f"  our book cash flow    {'':>6}   {flows:+12.4f}   <- what the runner adds"
          f" to its baseline")
    print("  A residual that is STABLE is this account's noise floor, not a bug:")
    print("  the identity `baseline + our flow == venue cash` needs the account to")
    print("  be touched by this bot and nothing else.")


def _venue_swap_positions(venue) -> dict[str, float]:
    """Venue swap positions in BASE units, signed (+ long, - short).

    Contracts are NOT base units - contractSize differs per symbol (SOL 1,
    BTC 0.01, DOGE 1000) - so the same conversion `CcxtVenue.account_snapshot`
    uses is applied here. Getting this wrong is the trap `swap_math` exists for.
    """
    out: dict[str, float] = {}
    try:
        rows = venue.ex.fetch_positions()
    except Exception as exc:  # noqa: BLE001 - a venue hiccup must not invent a fix
        print(f"could not read swap positions: {type(exc).__name__}: {exc}")
        return out
    for row in rows or []:
        symbol = str(row.get("symbol") or "")
        contracts = float(row.get("contracts") or 0.0)
        if not symbol or abs(contracts) < 1e-12:
            continue
        size = float((venue.ex.markets.get(symbol) or {}).get("contractSize") or 1.0)
        signed = abs(contracts * size)
        if str(row.get("side") or "").lower() == "short":
            signed = -signed
        out[symbol] = signed
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument(
        "--explain-cash",
        action="store_true",
        help=(
            "decompose the cash gap using the venue's OWN bill ledger. The runner's "
            "cash check is `venue cash at first start + our flow`, which can only "
            "close if nothing but this bot ever touched the account."
        ),
    )
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    ledger_path = PROJECT / args.ledger
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    done = {r.get("symbol") for r in rows if MARKER in str(r.get("client_order_id") or "")}

    book: dict[str, float] = {}
    for row in rows:
        if row.get("kind") != "fill":
            continue
        net = float(row.get("qty") or 0.0) * (1 if row.get("side") == "buy" else -1)
        book[row["symbol"]] = book.get(row["symbol"], 0.0) + net

    if args.explain_cash:
        _explain_cash(config, rows, book)

    venue = build_venue(config, "okx_demo")
    venue.connect()
    balance = venue.ex.fetch_balance({"type": "spot"})

    # (symbol, qty, price, side) - covers both the spot and the swap pass.
    fixes: list[tuple[str, float, float, str]] = []
    print(f"{'symbol':<14}{'ledger':>16}{'venue':>16}{'action':>12}")
    for symbol in sorted(book):
        held = book[symbol]
        # Swaps are NOT spot balances and are handled by the second pass. A perp
        # symbol reaching here compared the perp quantity against the venue's
        # SPOT balance of the same coin - which matched only by coincidence, and
        # produced a bogus "sell 276.868 BAND/USDT:USDT" on 2026-10-08.
        if ":" in symbol or held <= 1e-9 or symbol in done:
            continue
        base = symbol.split("/")[0]
        free = float((balance.get(base) or {}).get("total") or 0.0)
        if free >= held - 1e-9:
            action = "ok"
        else:
            action = "correct"
            price = float((venue.ex.fetch_ticker(symbol) or {}).get("last") or 0.0)
            if price > 0:
                fixes.append((symbol, held - free, price, "sell"))
        print(f"{symbol:<14}{held:>16.6g}{free:>16.6g}{action:>12}")

    # ---- the SWAP side -----------------------------------------------------
    # Until 2026-10-08 this script only ever looked at `fetch_balance(type=spot)`,
    # so a perp leg could sit in the book forever with no counterpart at the
    # venue and no one would notice. That is how the runner came to re-send the
    # BAND hedge on every restart and how NEAR/SUI produced endless 51008s.
    perp_local = {s: q for s, q in book.items() if ":" in s and abs(q) > 1e-9}
    perp_venue = _venue_swap_positions(venue)
    # Entry prices come from the same replay the runner uses, so the write-off is
    # PnL-neutral (see plan_swap_fix).
    entry_price = {
        s: p.avg_price for s, p in PositionBook.replay(
            Ledger.read_fills(ledger_path), base_ccy="USDT"
        ).positions.items()
    }
    print(f"\n{'swap symbol':<22}{'ledger':>16}{'venue':>16}{'action':>12}")
    for symbol in sorted(set(perp_local) | set(perp_venue)):
        local = perp_local.get(symbol, 0.0)
        actual = perp_venue.get(symbol, 0.0)
        if symbol in done:
            action = "already"
            print(f"{symbol:<22}{local:>16.6g}{actual:>16.6g}{action:>12}")
            continue
        plan = plan_swap_fix(symbol, local, actual, entry_price.get(symbol, 0.0))
        if plan is None:
            action = "ok" if abs(local - actual) < 1e-9 else "venue-only"
        else:
            action = "correct"
            side, qty, price = plan
            fixes.append((symbol, qty, price, side))
        print(f"{symbol:<22}{local:>16.6g}{actual:>16.6g}{action:>12}")

    if not fixes:
        print("\nnothing to correct")
        venue.close()
        return 0
    print(f"\n{len(fixes)} symbol(s) to correct:")
    for symbol, qty, price, side in fixes:
        print(f"   {symbol:<22} {side:<4} {qty:<16.6g} @ {price}")
    if not args.yes:
        print("\nDRY RUN - re-run with --yes")
        venue.close()
        return 0

    ledger = Ledger(ledger_path, run_id="manual-reconcile-20261008", venue="okx_demo")
    ts = int(rows[-1].get("ts") or 0)
    for symbol, qty, price, side in fixes:
        ledger.record_fill(
            Fill(
                client_order_id=f"{MARKER}{symbol.replace('/', '').replace(':', '')}",
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                fee=0.0,
                fee_ccy="USDT",
                ts=ts,
                tag="manual-reconcile",
            )
        )
        print(f"   appended {symbol}")
    venue.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
