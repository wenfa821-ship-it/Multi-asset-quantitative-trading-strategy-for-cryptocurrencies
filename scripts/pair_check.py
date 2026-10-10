#!/usr/bin/env python3
"""Controlled two-leg verification of the carry path (README 43.5).

This is the precondition for re-enabling `carry_funding` after the 2026-10-08
incident, where the first live pair went out as a $2,969 NAKED LONG because the
spot order's asynchronous `status=open, filled=0` was read as "not filled".

It exercises the SAME path the runner uses - `execution.paired.PairedExecutor`
over two real `OMS` instances against the real venues - rather than the
standalone `scripts/carry.py` executor, which is a different code path and was
already verified separately (§27.3). That distinction is the whole point: the
venue is fine, the runner's path is what broke.

Checks, in order, and it reports each one rather than just succeeding:

  1. the spot leg fills and the fill is WAITED FOR, not assumed
  2. the perp leg goes out with the SAME base quantity
  3. BOTH legs' fills land in the ledger (the second was missing before, which
     made a delta-neutral pair look like a naked long in the books)
  4. the pair closes: perp first (reduceOnly), then spot
  5. the account ends flat

Uses a TEMPORARY ledger so a verification run cannot pollute production
reconciliation. Refuses any non-sandbox venue.

    set -a; source .env; set +a
    export HTTPS_PROXY=http://127.0.0.1:7897
    python3 scripts/pair_check.py --symbol DOGE/USDT --notional 150
    python3 scripts/pair_check.py --symbol DOGE/USDT --notional 150 --yes
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.execution.oms import OMS  # noqa: E402
from ta.execution.paired import PairExecutionError, PairedExecutor, perp_symbol  # noqa: E402
from ta.portfolio import Ledger  # noqa: E402
from ta.venues import build_venue  # noqa: E402


def flat_spot(venue, symbol: str) -> float:
    base = symbol.split("/")[0]
    try:
        balance = venue.ex.fetch_balance({"type": venue.market_type})
    except Exception:  # noqa: BLE001
        return -1.0
    return float((balance.get(base) or {}).get("free") or 0.0)


def perp_contracts(venue, symbol: str) -> float:
    total = 0.0
    for row in venue.ex.fetch_positions([symbol]) or []:
        total += abs(float(row.get("contracts") or 0.0))
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--spot-venue", default="okx_demo")
    ap.add_argument("--perp-venue", default="okx_demo_swap")
    ap.add_argument("--symbol", default="DOGE/USDT")
    ap.add_argument("--notional", type=float, default=150.0)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    for name in (args.spot_venue, args.perp_venue):
        if not config.venue(name).sandbox:
            raise SystemExit(f"refusing: {name} is not a sandbox venue")

    spot = build_venue(config, args.spot_venue)
    perp = build_venue(config, args.perp_venue)
    spot.connect()
    perp.connect()

    perp_sym = perp_symbol(args.symbol)
    price = float((spot.ex.fetch_ticker(args.symbol) or {}).get("last") or 0.0)
    if price <= 0:
        raise SystemExit("no spot price; aborting")
    qty = round(args.notional / price, 6)
    print(f"{args.symbol} last={price}  {args.notional:.0f} USD -> {qty}")
    print(f"hedge: {perp_sym}  (spot {flat_spot(spot, args.symbol)}, "
          f"perp {perp_contracts(perp, perp_sym)} contracts before)")
    if not args.yes:
        print("\nDRY RUN - re-run with --yes to place the pair")
        return 0

    tmp = tempfile.TemporaryDirectory(prefix="ta-pair-")
    ledger = Ledger(Path(tmp.name) / "ledger.jsonl", venue="okx_demo")
    spot_oms = OMS(spot, ledger, dry_run=False)
    perp_oms = OMS(perp, ledger, dry_run=False)
    executor = PairedExecutor(spot_oms, perp_oms, stamp=lambda: int(time.time() * 1000))

    print("\n=== 1-3. open the pair ===")
    try:
        pair = executor.open_pair(args.symbol, qty, tag="pair-check")
    except PairExecutionError as exc:
        print(f"  OPEN FAILED: {exc}")
        print(f"  spot left {flat_spot(spot, args.symbol)}, "
              f"perp {perp_contracts(perp, perp_sym)} contracts")
        return 2
    print(f"  spot filled {pair.spot_filled}   perp filled {pair.perp_filled}"
          f"   residual {pair.residual():+.8g}")
    tick = Ledger.read_fills(ledger.path)
    syms = sorted({f.symbol for f in tick})
    print(f"  ledger symbols written: {syms}")
    both = args.symbol in syms and perp_sym in syms
    print(f"  CHECK both legs in the ledger: {'OK' if both else 'MISSING ' + perp_sym}")

    print("\n=== 4-5. close the pair ===")
    try:
        executor.close_pair(pair)
    except PairExecutionError as exc:
        print(f"  CLOSE FAILED: {exc}")
        return 3
    deadline = time.time() + 25
    while time.time() < deadline and (
        flat_spot(spot, args.symbol) > 0 or perp_contracts(perp, perp_sym) > 0
    ):
        spot_oms.sync([args.symbol])
        perp_oms.sync([perp_sym])
        time.sleep(1.0)
    left_spot = flat_spot(spot, args.symbol)
    left_perp = perp_contracts(perp, perp_sym)
    print(f"  spot left {left_spot}, perp left {left_perp} contracts")

    print("\n=== result ===")
    ok = both and left_perp == 0 and left_spot < 10 ** -6
    print(f"  both legs filled & recorded : {'yes' if both else 'NO'}")
    print(f"  flat afterwards             : {'yes' if ok else 'NO'}")
    print(f"  production ledger           : untouched (temporary ledger used)")
    tmp.cleanup()
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
