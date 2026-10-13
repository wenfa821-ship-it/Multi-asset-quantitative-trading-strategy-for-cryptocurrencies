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


def load_store(path: Path | None = None) -> dict[str, dict]:
    target = path or STORE
    if not target.exists():
        return {}
    out: dict[str, dict] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
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


def pending_listings(rows: list[dict], store: dict, *, categories: set[str],
                     cutoff_ms: int, mature_ms: int) -> list[tuple[str, int]]:
    """该记录的 USDT 新上市（纯函数，便于测试）。

    2026-10-13：把这个筛选抽出来，并给 `--categories` 一个出口 —— 原来写死 `instCategory == "1"`，
    于是近 8 天上线的 32 个交易对里 **cat=3（股票代币）那 8 个已成熟的一个都不记**，
    而日志只写「added 0」，读的人分不清「真的没有」和「被口径滤掉了」。
    """
    out = []
    for row in rows:
        inst = str(row.get("instId") or "")
        listed = int(row.get("listTime") or 0)
        if not (inst.endswith("-USDT") and row.get("state") == "live"):
            continue
        if str(row.get("instCategory")) not in categories:
            continue
        if not (cutoff_ms <= listed <= mature_ms):
            continue
        if inst in store:
            continue
        out.append((inst, listed))
    out.sort(key=lambda x: x[1])
    return out


def filtered_by_category(rows: list[dict], *, categories: set[str],
                         cutoff_ms: int, mature_ms: int) -> dict[str, int]:
    """同一时间窗里，**被口径挡掉**的有多少、属于哪一类 —— 让「added 0」不再沉默。"""
    out: dict[str, int] = {}
    for row in rows:
        inst = str(row.get("instId") or "")
        listed = int(row.get("listTime") or 0)
        if not (inst.endswith("-USDT") and row.get("state") == "live"):
            continue
        if not (cutoff_ms <= listed <= mature_ms):
            continue
        cat = str(row.get("instCategory"))
        if cat in categories:
            continue
        out[cat] = out.get(cat, 0) + 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=400.0)
    ap.add_argument("--mature-days", type=float, default=2.5,
                    help="only record once this many days have passed (48h window complete)")
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--status", action="store_true", help="print the scoreboard and exit")
    ap.add_argument("--categories", default="1",
                    help='只记这些 instCategory（逗号分隔）。默认 "1" = 真·加密新币；'
                         '"3" = 股票代币（TradFi 腿的池子成长，用 --store 分开放）')
    ap.add_argument("--store", default=None,
                    help="记录落哪个文件（默认 data/new_listings/records.jsonl）")
    args = ap.parse_args()

    store_path = Path(args.store) if args.store else STORE
    store = load_store(store_path)
    if args.status:
        print(f"store {store_path}  records={len(store)}")
        scoreboard(list(store.values()))
        return 0

    now_ms = int(time.time() * 1000)
    cutoff = now_ms - int(args.max_age_days * 86_400_000)
    mature = now_ms - int(args.mature_days * 86_400_000)
    rows = instruments()
    wanted = {c.strip() for c in str(args.categories).split(",") if c.strip()}
    pending = pending_listings(rows, store, categories=wanted,
                               cutoff_ms=cutoff, mature_ms=mature)
    skipped = filtered_by_category(rows, categories=wanted,
                                   cutoff_ms=cutoff, mature_ms=mature)

    added = 0
    store_path.parent.mkdir(parents=True, exist_ok=True)
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
        with store_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        store[inst] = row
        added += 1
        print(f"  + {inst:<14} age={row['age_days']:6.1f}d 上冲={row['run_up_pct']:>7.1f}% "
              f"48h={row['ret_48h_pct'] if row['ret_48h_pct'] is not None else float('nan'):>7.1f}%")
        time.sleep(0.3)

    print(f"\nadded {added} record(s); store now {len(store)}")
    if skipped:
        # 「added 0」必须在说了这些之后才算说清楚：哪一类被口径挡了、各多少条。
        detail = "、".join(f"cat={cat} {n} 条" for cat, n in sorted(skipped.items()))
        print(f"本轮被口径（--categories {','.join(sorted(wanted))}）挡下的：{detail}"
              " —— 这些不是「没有新上市」，是**不在本口径里**")
    scoreboard(list(store.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
