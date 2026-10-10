#!/usr/bin/env python3
"""Append the ledger record for the manual ACT unwind (2026-10-08).

Why this exists
---------------
The first live carry pair (ACT/USDT) filled its spot leg and never established the
hedge, leaving a $2,969 naked long that was closed BY HAND with
`scripts/carry.py close` (README 43). Those manual sells did not go through the
OMS, so they were never written to the ledger - and the runner's book therefore
still believes it holds 289,693.836 ACT that the venue does not have. That is
what the periodic reconciliation has been reporting every cycle since.

The ledger is append-only and stays that way: this appends a correcting FILL with
the venue's own aggregated numbers (42 fills, 289,693.836 ACT, $2,955.7273
proceeds, $2.9557 fees, average $0.01020293) rather than editing history.

It refuses to run twice and refuses to run if the ledger's ACT position is
already flat.

    python3 scripts/reconcile_manual_act.py --dry-run
    python3 scripts/reconcile_manual_act.py --yes
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.models import Fill  # noqa: E402
from ta.portfolio import Ledger  # noqa: E402

SYMBOL = "ACT/USDT"
CLIENT_ORDER_ID = "MANUALACTUNWIND20261008"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    ap.add_argument("--qty", type=float, default=289_693.8360)
    ap.add_argument("--price", type=float, default=0.01020293)
    ap.add_argument("--fee", type=float, default=2.9557)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    path = PROJECT / args.ledger
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    fills = [r for r in rows if r.get("kind") == "fill"]
    if any(r.get("client_order_id") == CLIENT_ORDER_ID for r in fills):
        print("already applied; nothing to do")
        return 0

    net = 0.0
    for row in fills:
        if row.get("symbol") != SYMBOL:
            continue
        net += float(row.get("qty") or 0.0) * (1 if row.get("side") == "buy" else -1)
    print(f"ledger {SYMBOL} net position: {net}")
    if abs(net) < 1e-9:
        print("already flat in the ledger; nothing to do")
        return 0

    fill = Fill(
        client_order_id=CLIENT_ORDER_ID,
        symbol=SYMBOL,
        side="sell",
        qty=net,
        price=args.price,
        fee=args.fee,
        fee_ccy="USDT",
        ts=int(rows[-1].get("ts") or 0),
        tag="manual-reconcile",
    )
    print(f"will append: sell {net} {SYMBOL} @ {args.price} fee {args.fee} "
          f"(tag manual-reconcile)")
    if not args.yes:
        print("DRY RUN - re-run with --yes")
        return 0
    # A dedicated run_id keeps this record visibly separate from the loop's runs.
    Ledger(path, run_id="manual-reconcile-20261008", venue="okx_demo").record_fill(fill)
    print(f"appended to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
