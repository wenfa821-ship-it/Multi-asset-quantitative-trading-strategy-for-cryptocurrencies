#!/usr/bin/env python3
"""Is the 1.1 buy-premium gate open? One number per day, from the daily IV-RV rows.

The spec (docs/options-sleeve-spec.md 4.1) puts the whole decision on one
inequality: a buyer is paid when REALISED vol exceeds IMPLIED vol by more than the
round trip costs, and that cost is ~1.3 vol points at sigma=60%. So the gate is

    RV(30d) - IV(same expiry) >= 1.5 pp, for 5 consecutive trading days

`data/options/cost_history.jsonl` already stores `variance_premium_pp = IV - RV`
per family per day, so the gate is `-variance_premium_pp >= 1.5`. This script is
the readable half: it reports the run length per family every day instead of
leaving the answer to whoever re-reads the series by hand, and records one row so
the day the gate opens is a timestamp rather than a memory.

Read-only. It trades nothing and cannot: the runner has no option leg wiring yet.

    python3 scripts/options_gate.py
    python3 scripts/options_gate.py --days 5 --min-edge-pp 1.5 --write
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

# SOL is excluded from 1.1 on purpose: its OKX chain stops at 6 days and only the
# USD-settled _UM family exists there, with no asks (changelog 2026-10-10T15:55).
DEFAULT_FAMILIES = ("BTC-USD", "ETH-USD")


def load_rows(path: Path) -> list[dict]:
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


def rv_minus_iv(row: dict) -> float | None:
    """Realised minus implied, in vol points. Positive means premium is cheap.

    The stored field is `variance_premium_pp = IV - RV`, so the sign is flipped
    here once rather than at every call site - the old dashboard bug in this
    project was exactly a unit/sign convention living in two places.
    """
    premium = row.get("variance_premium_pp")
    if premium is None:
        return None
    return -float(premium)


def gate_runs(rows: list[dict], families=DEFAULT_FAMILIES, *, days: int = 5,
              min_edge_pp: float = 1.5) -> dict:
    """Per family: the CURRENT run of consecutive days that clear the threshold.

    "Current" means ending at that family's latest row: four good days followed by
    one bad day is not a 4-day run, it is a 0-day run. That is the whole point of
    asking for a run rather than a count.
    """
    out: dict[str, dict] = {}
    for family in families:
        series = sorted(
            ((str(r.get("date")), rv_minus_iv(r)) for r in rows if r.get("family") == family),
            key=lambda item: item[0],
        )
        series = [(date, value) for date, value in series if value is not None]
        run = 0
        for _date, value in reversed(series):
            if value >= min_edge_pp:
                run += 1
            else:
                break
        latest_date, latest_value = series[-1] if series else (None, None)
        out[family] = {
            "latest_date": latest_date,
            "latest_rv_minus_iv_pp": None if latest_value is None else round(latest_value, 2),
            "run_days": run,
            "sample_days": len(series),
            "edge": round(latest_value, 2) if latest_value is not None else None,
            "open": run >= days,
            "series": [[date, None if value is None else round(value, 2)] for date, value in series[-8:]],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="data/options/cost_history.jsonl")
    ap.add_argument("--families", default=",".join(DEFAULT_FAMILIES))
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--min-edge-pp", type=float, default=1.5)
    ap.add_argument("--out", default="data/options/gate.jsonl")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    families = [f.strip() for f in args.families.split(",") if f.strip()]
    rows = load_rows(PROJECT / args.history)
    if not rows:
        print(f"no rows in {args.history} - nothing to judge")
        return 1
    runs = gate_runs(rows, families, days=args.days, min_edge_pp=args.min_edge_pp)

    print(f"1.1 买权利金闸门：RV(30d) − IV ≥ {args.min_edge_pp:g}pp 连续 {args.days} 天")
    for family, info in runs.items():
        state = "OPEN" if info["open"] else "closed"
        latest = info["latest_rv_minus_iv_pp"]
        print(f"  {family:9s} {state:6s} 连{info['run_days']}/{args.days} 天 · "
              f"最新 {info['latest_date']} RV−IV = {latest} pp · 样本 {info['sample_days']} 天")
        print("      " + "  ".join(f"{d}:{v}" for d, v in info["series"]))
    opened = [f for f, i in runs.items() if i["open"]]
    print("\n" + (f"闸门开启：{', '.join(opened)} —— 可按说明书 §4 开门"
                  if opened else "闸门关闭：不接策略，1.1 只交付适配+数据"))

    if args.write:
        path = Path(args.out)
        if not path.is_absolute():
            path = PROJECT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "days": args.days,
            "min_edge_pp": args.min_edge_pp,
            "families": runs,
            "open": opened,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        print(f"appended -> {path}")
    else:
        print("DRY RUN - re-run with --write to record the verdict")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
