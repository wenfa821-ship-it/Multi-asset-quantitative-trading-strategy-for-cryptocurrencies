#!/usr/bin/env python3
"""Close a pair the rotation detector says has stopped paying - and open nothing.

Why this is "close only"
-----------------------
`scripts/carry_rotation.py` decides WHICH pair has stopped paying (three days
below the floor, breakeven inside the hold, not younger than 24h). Acting on it
means two different risks:

  * closing the old pair - bounded, and it frees a slot;
  * opening the replacement - a two-leg entry that has to be sequenced, and the
    window between the legs is a naked long or a naked short.

The runner already opens pairs (passive spot entry, depth gate, pairing
invariants) and will take the freed slot on its own. So this script does only the
first half: **it never opens anything**, and it says so in its output.

Guardrails (all refusals, no silent adjustments):

  * both legs must exist at the venue - selling a spot leg whose perp short we
    cannot see would leave a NAKED SHORT;
  * the perp is bought back FIRST, so a failure leaves the pair still hedged;
  * notional <= `--max-notional-pct` of equity (default 5%, the answer to the
    rotation question on 2026-10-10);
  * at most `--max-per-day` (default 1) rotation per calendar day, counted from
    this script's own execution log - the first day of live carry churned 31 fills
    and $284 of fees by moving more than once.

    python3 scripts/carry_rotate.py                 # show what it would do
    python3 scripts/carry_rotate.py --yes           # close the flagged pair
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

from ta.config import Config, load_env_file  # noqa: E402
from ta.venues import build_venue  # noqa: E402


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


def latest_proposal(rows: list[dict]) -> dict | None:
    """The newest row that actually proposed a rotation."""
    for row in reversed(rows):
        if row.get("rotations"):
            return row
    return None


def rotations_today(rows: list[dict], today: str) -> int:
    return sum(1 for row in rows if str(row.get("generated_at", "")).startswith(today))


def last_equity(rows: list[dict]) -> float:
    for row in reversed(rows):
        if row.get("kind") == "equity":
            return float(row.get("equity") or 0.0)
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--report", default="data/carry/rotation.jsonl")
    ap.add_argument("--exec-log", default="data/carry/rotation_exec.jsonl")
    ap.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    ap.add_argument("--max-notional-pct", type=float, default=5.0)
    ap.add_argument("--max-per-day", type=int, default=1)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    executed = load_jsonl(PROJECT / args.exec_log)
    done_today = rotations_today(executed, today)
    if done_today >= args.max_per_day:
        print(f"今天已经换过 {done_today} 次（上限 {args.max_per_day}），不再动作")
        return 0

    proposal = latest_proposal(load_jsonl(PROJECT / args.report))
    if proposal is None:
        print("轮换报告里没有任何提案（没有哪一对连续低于阈值）—— 无事可做")
        return 0
    rotation = proposal["rotations"][0]
    symbol = str(rotation["from"])
    print(f"提案：{symbol} 中位 {rotation['current_bps']} bps/天 → 候选 {rotation['to']} "
          f"{rotation['candidate_bps']} bps/天，回本 {rotation['breakeven_days']} 天"
          f"（报告时间 {proposal['generated_at']}）")

    equity = last_equity(load_jsonl(PROJECT / args.ledger))
    config = Config.load(args.config_dir)
    load_env_file(PROJECT / ".env")
    spot = build_venue(config, "okx_demo")
    perp = build_venue(config, "okx_demo_swap")
    spot.connect()
    perp.connect()

    base = symbol.split("/")[0]
    held = float((spot.ex.fetch_balance({"type": "spot"}).get(base) or {}).get("free") or 0.0)
    perp_symbol = f"{symbol}:USDT"
    contracts = 0.0
    for row in perp.ex.fetch_positions() or []:
        if str(row.get("symbol")) == perp_symbol and str(row.get("side")) == "short":
            contracts = float(row.get("contracts") or 0.0)
    price = float((spot.ex.fetch_ticker(symbol) or {}).get("last") or 0.0)
    notional = held * price
    cap = equity * args.max_notional_pct / 100.0
    print(f"现货 {held:g} {base} ~${notional:,.0f} / 永续空头 {contracts:g} 张 / "
          f"权益 ${equity:,.0f}，单次上限 ${cap:,.0f}")
    if held <= 0 or contracts <= 0:
        print("拒绝：两腿不齐（只卖现货会留下裸空）—— 需要人工检查")
        return 2
    if cap > 0 and notional > cap:
        print(f"拒绝：~${notional:,.0f} 超过单次上限 ${cap:,.0f}")
        return 2
    if not args.yes:
        print("\nDRY RUN - 会先买回永续空头、再市价卖掉现货腿；**不会开新对**。"
              " 加 --yes 执行")
        return 0

    print("\n=== 平旧对（先永续后现货）===")
    try:
        perp.ex.create_order(
            perp_symbol, "market", "buy", contracts, None,
            {"tdMode": "cross", "posSide": "short", "reduceOnly": True},
        )
        print("  永续买回 OK（对冲仍在，直到现货卖出）")
    except Exception as exc:  # noqa: BLE001
        print(f"  永续买回失败，**不动现货**（保持对冲）: {str(exc)[:120]}")
        return 3
    time.sleep(1.5)
    free = float((spot.ex.fetch_balance({"type": "spot"}).get(base) or {}).get("free") or 0.0)
    if free <= 0:
        print("  现货余额已为 0")
    else:
        try:
            spot.ex.create_order(symbol, "market", "sell", free, None, {"tdMode": "cross"})
            print(f"  现货卖出 {free:g} OK")
        except Exception as exc:  # noqa: BLE001
            print(f"  现货卖出失败（此刻是裸空，需要立刻人工处理）: {str(exc)[:120]}")
            return 4
    log = PROJECT / args.exec_log
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "symbol": symbol, "spot_sold": free, "perp_bought_back": contracts,
            "notional_usd": round(notional, 2), "equity": equity,
            "proposal": rotation, "opened_new_pair": False,
        }, ensure_ascii=False) + "\n")
    print(f"已记录到 {log}（opened_new_pair=false）")
    print("新对是否开、什么时候开，交给 runner 的 carry 入场逻辑（深度闸门 + 被动挂单）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
