#!/usr/bin/env python3
"""Record settled funding payments in the ledger.

Why this exists
---------------
Every open swap pays or receives funding three times a day on OKX, and the venue
moves cash by it. The book recorded none of it, so the flow the reconciler adds to
its baseline was permanently short.

Measured 2026-10-08: 11 settlements, +$12.63. Small - and **not** the cause of the
~$1,062 cash residual (that is this account's out-of-ledger activity: 2,404 venue
bills against our 140 fills). Funding is recorded because it is real, it accrues
for as long as a hedge is held, and it costs one line of code to stop guessing.

Idempotent by the venue's own `billId`: a row already in the ledger is never
written twice, so this is safe to run on a schedule.

    python3 scripts/sync_funding.py            # report only
    python3 scripts/sync_funding.py --yes      # append what is missing
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config, load_env_file  # noqa: E402
from ta.portfolio import Ledger  # noqa: E402
from ta.venues import build_venue  # noqa: E402


def symbol_of(inst_id: str) -> str:
    """`BAND-USDT-SWAP` -> `BAND/USDT:USDT`, the convention everything else uses."""
    if inst_id.endswith("-USDT-SWAP"):
        return inst_id[: -len("-USDT-SWAP")] + "/USDT:USDT"
    return inst_id


def new_funding_bills(bills: list[dict], already: set[str]) -> list[dict]:
    """The type=8 bills not yet in the ledger, oldest first.

    Pure, so the idempotency rule is a test rather than a hope: a rerun of the
    sync must add nothing.
    """
    fresh = [
        b
        for b in bills
        if str(b.get("type")) == "8" and str(b.get("billId")) not in already
    ]
    return sorted(fresh, key=lambda b: int(b.get("ts") or 0))


def fetch_funding_bills(venue, limit: int = 100) -> list[dict]:
    out: list[dict] = []
    after: str | None = None
    while True:
        params: dict = {"type": "8", "limit": str(limit)}
        if after:
            params["after"] = after
        page = venue.ex.privateGetAccountBills(params).get("data") or []
        if not page:
            break
        out += page
        if len(page) < limit:
            break
        after = page[-1]["billId"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--env-file", default=".env", help="credentials, read like the runner does")
    ap.add_argument("--yes", action="store_true", help="append the missing rows")
    args = ap.parse_args()

    loaded = load_env_file(PROJECT / args.env_file)
    if loaded:
        print(f"loaded {loaded} value(s) from {args.env_file}")

    config = Config.load(args.config_dir)
    ledger_path = PROJECT / args.ledger
    ledger = Ledger(ledger_path, run_id="funding-sync", venue=args.venue)
    already = ledger.funding_bill_ids()
    print(f"ledger already holds {len(already)} funding settlement(s)")

    venue = build_venue(config, args.venue)
    for attempt in range(4):
        try:
            venue.connect()
            break
        except Exception:  # noqa: BLE001 - the proxy is flaky; retry briefly
            if attempt == 3:
                print("could not reach the venue; nothing written")
                return 2
            time.sleep(3)

    try:
        bills = fetch_funding_bills(venue)
    except Exception as exc:  # noqa: BLE001
        print(f"could not read funding bills: {type(exc).__name__}: {exc}")
        return 2
    finally:
        venue.close()

    fresh = new_funding_bills(bills, already)
    total = sum(float(b.get("pnl") or 0.0) for b in fresh)
    print(f"venue reports {len(bills)} funding settlement(s); {len(fresh)} are new")
    for bill in fresh:
        print(
            f"   {symbol_of(str(bill.get('instId'))):<22} "
            f"{float(bill.get('pnl') or 0):>+12.6f} USDT"
        )
    if not fresh:
        print("nothing to record")
        return 0
    if not args.yes:
        print(f"\nDRY RUN - {total:+.6f} USDT over {len(fresh)} row(s); re-run with --yes")
        return 0

    for bill in fresh:
        ledger.record_funding(
            symbol_of(str(bill.get("instId"))),
            float(bill.get("pnl") or 0.0),
            str(bill.get("billId") or ""),
        )
    book = ledger.rebuild_book(base_ccy="USDT", venue=args.venue)
    print(f"recorded {len(fresh)} row(s), {total:+.6f} USDT")
    print(f"book cash flow is now {book.cash:+.4f} (funding included)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
