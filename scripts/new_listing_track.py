#!/usr/bin/env python3
"""Daily accumulator for the new-listing study.

README 18.5 says the candidate factor ("first hour closed up AND first-hour
turnover above median") is a *hypothesis* resting on 5 positive samples out of
23. The only honest way to test it is to keep recording new listings as they
appear - OKX adds roughly 2-5 real crypto listings a month, so this has to run
unattended for months, not once.

This script appends ONE record per new listing, once its 48h window is complete,
to data/new_listings/records.jsonl. It is idempotent: a listing already in the
store is never recorded twice, so it is safe to run every day (or twice a day).

It also prints the running scoreboard, which is the thing worth watching:
how many listings ever ran >= +20%, and whether the factor still separates them.

Read-only with respect to the exchange: public market data only. No keys, no
orders, no signing. It writes one JSONL file inside the project.

    export TA_PROXY=http://127.0.0.1:7897
    python3 scripts/new_listing_track.py
    python3 scripts/new_listing_track.py --status     # print without fetching
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PROJECT / "src"))

from new_listing_study import instruments, study  # noqa: E402

STORE = PROJECT / "data" / "new_listings" / "records.jsonl"


def load_store() -> dict[str, dict]:
    if not STORE.exists():
        return {}
    out: dict[str, dict] = {}
    for line in STORE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("inst"):
            out[row["inst"]] = row
    return out


def is_flat(row: dict) -> bool:
    """Stablecoins move ~0 by construction and would flatter every statistic."""
    return row["run_up_pct"] < 2 and row["max_dd_pct"] > -2


def scoreboard(rows: list[dict]) -> None:
    live = [r for r in rows if not is_flat(r)]
    if not live:
        print("  (no non-stablecoin records yet)")
        return
    vols = sorted(r["first_vol_usd"] for r in live)
    median_vol = vols[len(vols) // 2]
    hits = [r for r in live if r["run_up_pct"] >= 20]
    sel = [r for r in live if (r.get("first_ret_pct") or 0) > 0 and r["first_vol_usd"] >= median_vol]
    print(f"  样本：{len(live)} 个非稳定币新币（另 {len(rows) - len(live)} 个稳定币）")
    print(f"  48h 内上冲 >= +20%：{len(hits)}/{len(live)}  ({100*len(hits)/len(live):.0f}%)"
          f"   [README 18.3 基准 19%]")
    if sel:
        print(f"  因子命中（首小时收涨 + 成交额 >= ${median_vol:,.0f}）："
              f"{len(sel)} 个，其中 >=+20% 的 {sum(1 for r in sel if r['run_up_pct']>=20)} 个")
        outside = [r for r in live if r not in sel]
        print(f"  因子之外：{len(outside)} 个，其中 >=+20% 的 {sum(1 for r in outside if r['run_up_pct']>=20)} 个"
              f"   <- 这一列还是 0 这条因子才活着")
    ru = [r["run_up_pct"] for r in live]
    print(f"  上冲分布：中位 {st.median(ru):+.2f}%  均值 {st.mean(ru):+.2f}%  最大 {max(ru):+.2f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=400.0)
    ap.add_argument("--mature-days", type=float, default=2.5,
                    help="only record once this many days have passed (48h window complete)")
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--status", action="store_true", help="print the scoreboard and exit")
    args = ap.parse_args()

    store = load_store()
    if args.status:
        print(f"store {STORE}  records={len(store)}")
        scoreboard(list(store.values()))
        return 0

    now_ms = int(time.time() * 1000)
    cutoff = now_ms - int(args.max_age_days * 86_400_000)
    mature = now_ms - int(args.mature_days * 86_400_000)
    pending = [
        (r["instId"], int(r["listTime"]))
        for r in instruments()
        if r["instId"].endswith("-USDT")
        and r.get("instCategory") == "1"
        and r.get("state") == "live"
        and cutoff <= int(r["listTime"]) <= mature
        and r["instId"] not in store
    ]
    pending.sort(key=lambda x: x[1])

    added = 0
    STORE.parent.mkdir(parents=True, exist_ok=True)
    for inst, ts in pending:
        try:
            row = study(inst, ts, now_ms, args.hours)
        except Exception as exc:  # noqa: BLE001 - a single bad listing must not stop the job
            print(f"  {inst}: error {exc}")
            continue
        if not row:
            print(f"  {inst}: not enough bars yet")
            continue
        row["age_days"] = round((now_ms - ts) / 86_400_000, 2)
        row["recorded_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with STORE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        store[inst] = row
        added += 1
        print(f"  + {inst:<14} age={row['age_days']:6.1f}d 上冲={row['run_up_pct']:>7.1f}% "
              f"48h={row['ret_48h_pct'] if row['ret_48h_pct'] is not None else float('nan'):>7.1f}%")
        time.sleep(0.3)

    print(f"\nadded {added} record(s); store now {len(store)}")
    scoreboard(list(store.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
