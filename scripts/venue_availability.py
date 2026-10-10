#!/usr/bin/env python3
"""Record which symbols the venue ACTUALLY serves right now, twice a day.

Why this exists
---------------
The TradFi sleeve's 13 symbols were chosen off the DISK (share of daily bars with
a real print >= 80%). On 2026-10-10 that screen was measured against the venue
itself and 12 of the 13 answered with ZERO bars on every timeframe (1d, 1h, 15m)
while keeping 84+ bars on disk - `code:0, data:[]`, the "zombie" mode README 47
records - and only XMU had both bars and a two-sided book. Disk history says what
used to be available; this says what is available now.

It also cannot be settled in one look: tokenised equities may only be served
during US market hours, and the first measurement was taken while the US market
was closed. So the honest instrument is a TIME SERIES - one row per run - which is
why this is wired into the daily job, whose 21:10 CST slot falls inside the US
session and whose 09:10 slot falls outside it.

Credentials come from `.env`, like `sync_funding.py` and `funding_income.py`: the
launchd unit exports the proxy but not the API keys (README 50.4), so without the
loader this step dies with "API key is not set" on every run. It was written
without it on 2026-10-10 and caught by the daily job's retry summary the same day,
before the 21:10 slot it exists for.

    python3 scripts/venue_availability.py --write     # append one row
    python3 scripts/venue_availability.py --symbols XMU/USDT,XTSM/USDT
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config, load_env_file  # noqa: E402
from ta.venues import build_venue  # noqa: E402

TIMEFRAMES = ("1d", "1h", "15m")


def classify(bars: int, bid: float | None, ask: float | None) -> str:
    """One word for what the venue offers for this symbol right now.

    `tradable` needs BOTH data and a two-sided book: a signal the venue cannot
    price is a signal the depth gate will refuse anyway (README 63).
    """
    if bars <= 0 and not bid and not ask:
        return "absent"
    if bars <= 0:
        return "quote-only"
    if bid and ask:
        return "tradable"
    if bid or ask:
        return "one-sided"
    return "bars-only"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--symbols", default="", help="comma separated; default = the tradfi tier")
    ap.add_argument("--out", default="data/universe/availability.jsonl")
    ap.add_argument("--env-file", default=".env", help="credentials, read like the runner does")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    loaded = load_env_file(PROJECT / args.env_file)
    if loaded:
        print(f"loaded {loaded} value(s) from {args.env_file}")

    config = Config.load(args.config_dir)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        symbols = list(config.universe.symbols_by_tier.get("tradfi") or [])

    venue = build_venue(config, args.venue)
    venue.connect()
    now = datetime.datetime.now(datetime.UTC)
    # US regular session in UTC: 13:30-20:00 (ignore DST - this is a hint, not a gate).
    us_session = 13 <= now.hour < 20
    rows = []
    print(f"{'symbol':14s} {'1d':>4s} {'1h':>4s} {'15m':>4s} {'bid':>10s} {'ask':>10s}  state")
    for symbol in symbols:
        counts = {}
        for tf in TIMEFRAMES:
            try:
                counts[tf] = len(venue.fetch_bars(symbol, tf, 300))
            except Exception as exc:  # noqa: BLE001 - "cannot fetch" is a state too
                counts[tf] = -1
                print(f"  {symbol}: {tf} failed ({type(exc).__name__})")
        try:
            bid, ask = venue.top_of_book(symbol)
        except Exception:  # noqa: BLE001
            bid, ask = None, None
        state = classify(counts["1d"] + counts["1h"] + counts["15m"], bid, ask)
        rows.append({
            "symbol": symbol,
            **{f"bars_{tf}": counts[tf] for tf in TIMEFRAMES},
            "bid": bid,
            "ask": ask,
            "state": state,
        })
        print(f"{symbol:14s} {counts['1d']:>4d} {counts['1h']:>4d} {counts['15m']:>4d} "
              f"{str(bid):>10s} {str(ask):>10s}  {state}")
    counts_by_state: dict[str, int] = {}
    for r in rows:
        counts_by_state[r["state"]] = counts_by_state.get(r["state"], 0) + 1
    summary = f"us_session(UTC 13:30-20:00)={us_session} " + " ".join(
        f"{k}={v}" for k, v in sorted(counts_by_state.items())
    )
    print(f"\n{summary}")

    if not args.write:
        print("DRY RUN - re-run with --write")
        venue.close()
        return 0
    target = Path(args.out)
    if not target.is_absolute():
        target = PROJECT / target
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": int(now.timestamp() * 1000),
        "venue": args.venue,
        "us_session": us_session,
        "summary": counts_by_state,
        "rows": rows,
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"appended -> {target}")
    venue.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
