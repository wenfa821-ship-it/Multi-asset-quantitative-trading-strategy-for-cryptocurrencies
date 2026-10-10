#!/usr/bin/env python3
"""Which open carry pair has stopped paying, and what would replacing it cost?

Why this exists (1.2)
---------------------
Carry's five slots are full (spot 5/5), so rotation is the only way a new name can
ever be entered - and rotation is not free: the measured two-leg round trip is
**44.9 bps**, against a median funding of ~1-2 bps/day. That makes "is it paying
enough to keep" a question with a number attached, and it is deliberately NOT the
same question as "which name has the highest rate today":

  * a pair is flagged only when its funding is below the floor on EVERY one of the
    last N days (a single bad print is exactly the churn that cost 31 fills and
    $284 of fees on the first day of live carry),
  * a replacement has to clear the round trip inside the intended hold, i.e.
    breakeven_days = 44.9 / (candidate_bps - current_bps) must be < 30,
  * a pair younger than `min_hold_hours` is never a candidate, however bad the
    number looks.

Read-only. It decides nothing and sends nothing: it prints (and optionally
records) what the rule WOULD do. Acting on it is a separate, user-approved step.

    python3 scripts/carry_rotation.py
    python3 scripts/carry_rotation.py --days 3 --floor-bps 1.0 --write
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import statistics
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import load_env_file  # noqa: E402

DEFAULT_ROUND_TRIP_BPS = 44.9


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def daily_bps_series(prints: list[dict], days: int, settlements_per_day: float = 3.0,
                     ) -> list[tuple[str, float, int]]:
    """Per-UTC-day median funding in bps/DAY, for the last `days` days with prints.

    Median of the day's prints x prints/day, not the day's mean: one 20x spike is
    exactly what made the first carry day churn, and the venue's own mean is the
    number that lies about it.
    """
    buckets: dict[datetime.date, list[float]] = {}
    for row in prints:
        ts = row.get("ts")
        rate = row.get("rate")
        if ts is None or rate is None:
            continue
        day = datetime.datetime.fromtimestamp(float(ts) / 1000, datetime.UTC).date()
        buckets.setdefault(day, []).append(float(rate))
    ordered = sorted(buckets.items())[-max(1, days):]
    return [
        (day.isoformat(), round(statistics.median(rates) * settlements_per_day * 10000, 3), len(rates))
        for day, rates in ordered
    ]


def is_persistently_below(series: list[tuple[str, float, int]], floor_bps: float, days: int) -> bool:
    """True only when EVERY day in the window is below the floor.

    `len(series) == days` matters: a leg with two days of history has not been
    persistently anything yet.
    """
    return len(series) == days and all(bps < floor_bps for _day, bps, _n in series)


def open_since(fills: list[dict], symbol: str, dust: float = 1e-9) -> int | None:
    """When the CURRENT holding streak for `symbol` started (ms), from fills alone.

    The pair state file records the pair, not its age, and the ledger's fills carry
    no pair id - so age is reconstructed the only way it can be: walk the symbol's
    fills in order and remember the first one after the running position last
    returned to (approximately) zero.
    """
    qty = 0.0
    start: int | None = None
    for fill in sorted((f for f in fills if f.get("symbol") == symbol), key=lambda f: f["ts"]):
        size = float(fill.get("qty") or 0.0)
        qty += size if fill.get("side") == "buy" else -size
        if abs(qty) <= dust:
            start = None
        elif start is None:
            start = int(fill["ts"])
    return start


def breakeven_days(round_trip_bps: float, gain_bps_per_day: float) -> int | None:
    """Days for a switch to pay back its round trip. None when it never would."""
    if gain_bps_per_day <= 0:
        return None
    return int(math.ceil(round_trip_bps / gain_bps_per_day))


def screen_rows(screen: dict) -> list[dict]:
    rows = screen.get("rows") if isinstance(screen, dict) else screen
    out = []
    for row in rows or []:
        symbol = str(row.get("symbol") or "")
        daily = row.get("median_daily_bps", row.get("daily_bps"))
        if symbol and daily is not None:
            out.append({"symbol": symbol, "bps": float(daily)})
    return sorted(out, key=lambda r: -r["bps"])


def plan(held: dict[str, dict], history: Path, candidates: list[dict], fills: list[dict],
         *, days: int, floor_bps: float, round_trip_bps: float, min_hold_hours: float,
         intended_hold_days: float, settlements_per_day: float = 3.0,
         now_ms: int | None = None) -> dict:
    now = now_ms or int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    held_bases = {symbol.split("/")[0] for symbol in held}
    free = [c for c in candidates if c["symbol"].split("/")[0] not in held_bases]
    report = {"generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
              "days": days, "floor_bps": floor_bps, "round_trip_bps": round_trip_bps,
              "pairs": [], "rotations": []}
    for symbol in sorted(held):
        base = symbol.split("/")[0]
        series = daily_bps_series(load_jsonl(history / f"{base}-USDT_USDT.jsonl"), days,
                                  settlements_per_day)
        since = open_since(fills, symbol)
        age_hours = round((now - since) / 3_600_000, 1) if since else None
        low = is_persistently_below(series, floor_bps, days)
        row = {"symbol": symbol, "daily_bps": series, "stale": low,
               "median_bps": round(statistics.median([bps for _d, bps, _n in series]), 3) if series else None,
               "age_hours": age_hours}
        report["pairs"].append(row)
        if not low or (age_hours is not None and age_hours < min_hold_hours):
            continue
        current = row["median_bps"] or 0.0
        for cand in free:
            gain = cand["bps"] - current
            be = breakeven_days(round_trip_bps, gain)
            if be is None or be > intended_hold_days:
                continue
            report["rotations"].append({
                "from": symbol, "to": cand["symbol"], "current_bps": current,
                "candidate_bps": cand["bps"], "gain_bps_per_day": round(gain, 3),
                "breakeven_days": be, "age_hours": age_hours,
            })
            break
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="data/carry/pairs.json")
    ap.add_argument("--screen", default="data/carry/screen_live.json")
    ap.add_argument("--history", default="data/history/funding")
    ap.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    ap.add_argument("--days", type=int, default=3, help="consecutive days below the floor")
    ap.add_argument("--floor-bps", type=float, default=1.0, help="bps/day median that counts as unpaid")
    ap.add_argument("--round-trip-bps", type=float, default=DEFAULT_ROUND_TRIP_BPS)
    ap.add_argument("--min-hold-hours", type=float, default=24.0)
    ap.add_argument("--intended-hold-days", type=float, default=30.0)
    ap.add_argument("--out", default="data/carry/rotation.jsonl")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    load_env_file(PROJECT / ".env")  # the pair/history files need no venue, but keep parity
    held = load_json(PROJECT / args.pairs, {})
    candidates = screen_rows(load_json(PROJECT / args.screen, {}))
    fills = [f for f in load_jsonl(PROJECT / args.ledger) if f.get("kind") == "fill"]
    report = plan(held, PROJECT / args.history, candidates, fills,
                  days=args.days, floor_bps=args.floor_bps,
                  round_trip_bps=args.round_trip_bps, min_hold_hours=args.min_hold_hours,
                  intended_hold_days=args.intended_hold_days)

    print(f"{'pair':12s}{'median':>9s}{'age_h':>8s}  last {args.days}d bps/day")
    for row in report["pairs"]:
        days_txt = " ".join(f"{bps:g}" for _d, bps, _n in row["daily_bps"]) or "-"
        age = f"{row['age_hours']:g}" if row["age_hours"] is not None else "?"
        flag = " STALE" if row["stale"] else ""
        print(f"{row['symbol']:12s}{row['median_bps'] if row['median_bps'] is not None else '-':>9}"
              f"{age:>8s}  {days_txt}{flag}")
    if report["rotations"]:
        print("\nrotation proposals (read-only):")
        for rot in report["rotations"]:
            print(f"  {rot['from']} -> {rot['to']}: {rot['current_bps']:g} -> {rot['candidate_bps']:g} bps/day, "
                  f"breakeven {rot['breakeven_days']}d (round trip {args.round_trip_bps:g} bps)")
    else:
        print("\nno rotation proposed: no pair is below the floor on every day of the window")

    if args.write:
        path = Path(args.out)
        if not path.is_absolute():
            path = PROJECT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False) + "\n")
        print(f"appended -> {path}")
    else:
        print("DRY RUN - re-run with --write to record the sample")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
