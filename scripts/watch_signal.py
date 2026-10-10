#!/usr/bin/env python3
"""Tail the trading ledger and report the first signal-driven order/fill event.

Usage:
    python3 scripts/watch_signal.py [--timeout 7200]

Exits with code 0 when an order/fill/blocked event is seen, 2 on timeout.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SIGNAL_KINDS = {"order", "fill", "blocked"}
LEDGER = Path(__file__).resolve().parent.parent / "data" / "ledger.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=6 * 3600, help="seconds before giving up")
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    args = ap.parse_args()

    path = args.ledger
    if not path.exists():
        print(f"ledger not found: {path}", file=sys.stderr)
        return 1

    # Read everything already present so we only react to *new* events.
    with path.open("r", encoding="utf-8") as f:
        seen = sum(1 for _ in f)

    deadline = time.time() + args.timeout
    print(f"[watcher] watching {path} (already {seen} lines) for kinds {SIGNAL_KINDS}")
    print(f"[watcher] timeout in {args.timeout}s; hit Ctrl-C to stop early.")
    sys.stdout.flush()

    while time.time() < deadline:
        try:
            with path.open("r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            time.sleep(1)
            continue
        for line in lines[seen:]:
            seen += 1
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("kind")
            # Ignore adoption bookkeeping fills (tag=adopted) - they are not
            # strategy-driven trades, just existing-position registration.
            if kind == "fill" and ev.get("tag") == "adopted":
                continue
            if kind in SIGNAL_KINDS:
                ts = ev.get("ts")
                ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts / 1000)) if isinstance(ts, (int, float)) else ts
                print(f"\n[watcher] === FIRST SIGNAL EVENT DETECTED ===")
                print(f"[watcher] time : {ts_str}")
                print(f"[watcher] kind : {kind}")
                # Print a compact but complete view of the event.
                for k, v in ev.items():
                    if k in ("ts", "kind"):
                        continue
                    print(f"[watcher]   {k}: {v}")
                sys.stdout.flush()
                return 0
        time.sleep(2)

    print(f"\n[watcher] timed out after {args.timeout}s with no signal event.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
