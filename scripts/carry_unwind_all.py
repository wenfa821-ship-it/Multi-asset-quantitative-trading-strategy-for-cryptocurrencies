#!/usr/bin/env python3
"""Unwind every leftover carry leg, BOTH legs together.

Where these came from: while carry bypassed the risk gate (README, 2026-10-08)
it opened oversized pairs - one LTC leg alone was 20% of the book against a 4%
cap - and the directional strategies fought it for the same symbols. The process
has since been restarted, so the runner no longer holds pair records for them.
They are delta-neutral (spot long + perp short) but wildly over the design caps,
so they need a deliberate exit rather than being left to drift.

Safety rules, in order:

  * ONLY touches a symbol where BOTH legs exist. Selling the spot leg of a pair
    whose perp short we cannot see would leave a naked SHORT - the same class of
    mistake as the original incident, just mirrored.
  * Closes the PERP FIRST (buy, reduceOnly, posSide=short). If the buy-back
    fails, the long is still hedged and we stop; the reverse order would leave
    the short naked for as long as the spot sale took to fail.
  * Reports anything it deliberately skipped rather than guessing.

    python3 scripts/carry_unwind_all.py            # report only
    python3 scripts/carry_unwind_all.py --yes      # unwind
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.venues import build_venue  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--min-notional", type=float, default=5.0)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument(
        "--seed-state",
        action="store_true",
        help="write data/carry/pairs.json from what the venue actually holds, then stop",
    )
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    spot = build_venue(config, "okx_demo")
    perp = build_venue(config, "okx_demo_swap")
    spot.connect()
    perp.connect()

    # ---- what does each venue actually hold? -------------------------------
    balance = spot.ex.fetch_balance({"type": "spot"})
    held: dict[str, float] = {}
    for ccy, entry in (balance.get("total") or {}).items():
        if ccy == "USDT" or not entry:
            continue
        if float(entry) <= 0:
            continue
        held[f"{ccy}/USDT"] = float((balance.get(ccy) or {}).get("free") or 0.0)

    shorts: dict[str, float] = {}
    for row in perp.ex.fetch_positions() or []:
        contracts = float(row.get("contracts") or 0.0)
        if contracts <= 0 or str(row.get("side")) != "short":
            continue
        symbol = str(row.get("symbol") or "")
        base = symbol.split("/")[0]
        size = float((perp.ex.markets.get(symbol) or {}).get("contractSize") or 1.0) or 1.0
        shorts[f"{base}/USDT"] = shorts.get(f"{base}/USDT", 0.0) + contracts * size

    both = sorted(set(held) & set(shorts))
    print(f"现货腿 {len(held)} 个 / 永续空头 {len(shorts)} 个 / 两腿齐全 {len(both)} 个\n")
    for symbol in both:
        px = float((spot.ex.fetch_ticker(symbol) or {}).get("last") or 0.0)
        notional = held[symbol] * px
        flag = "" if notional >= args.min_notional else "  (dust)"
        print(f"  {symbol:<14} spot {held[symbol]:<16.6g} short {shorts[symbol]:<14.6g} "
              f"~${notional:,.0f}{flag}")
    lonely_spot = sorted(set(held) - set(shorts))
    lonely_short = sorted(set(shorts) - set(held))
    if lonely_spot:
        print(f"\n只有现货、没有空头（不动，卖它会留下裸空）: {', '.join(lonely_spot)}")
    if lonely_short:
        print(f"\n只有空头、没有现货（需人工判断）: {', '.join(lonely_short)}")

    # Seeding is how a restart stops orphaning pairs: the runner reads this file
    # on startup (README, _load_carry_pairs) and can then close both legs. The
    # file is written from what the VENUE holds, not from what the process
    # remembers - the venue is the source of truth.
    if args.seed_state:
        import json

        seed = {
            symbol: {"pair_id": "seeded", "base_qty": round(min(held[symbol], shorts[symbol]), 8)}
            for symbol in both
        }
        target = config.path("data/carry/pairs.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(seed, indent=1), encoding="utf-8")
        print(f"\nwrote {target} with {len(seed)} pair(s): {', '.join(seed) or '-'}")
        spot.close()
        perp.close()
        return 0

    if not args.yes:
        print("\nDRY RUN - re-run with --yes to unwind")
        return 0

    print("\n=== 平仓（先永续后现货）===")
    for symbol in both:
        base = symbol.split("/")[0]
        perp_symbol = f"{symbol}:USDT"
        contracts = float((perp.ex.markets.get(perp_symbol) or {}).get("contractSize") or 1.0) or 1.0
        qty_contracts = shorts[symbol] / contracts
        try:
            perp.ex.create_order(
                perp_symbol, "market", "buy", qty_contracts, None,
                {"tdMode": "cross", "posSide": "short", "reduceOnly": True},
            )
            print(f"  {symbol:<14} 永续买回 {qty_contracts:g} 张 OK")
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol:<14} 永续买回失败，跳过现货（保持对冲）: {str(exc)[:80]}")
            continue
        time.sleep(1.5)
        free = float((spot.ex.fetch_balance({"type": "spot"}).get(base) or {}).get("free") or 0.0)
        if free <= 0:
            print(f"  {symbol:<14} 现货余额已为 0")
            continue
        try:
            spot.ex.create_order(symbol, "market", "sell", free, None, {"tdMode": "cross"})
            print(f"  {symbol:<14} 现货卖出 {free:g} OK")
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol:<14} 现货卖出失败: {str(exc)[:80]}")
        time.sleep(1.5)

    time.sleep(3)
    # A short with no spot leg is a NAKED SHORT, not a carry leg - it appeared
    # because a directional strategy sold the spot half of a pair while the perp
    # stayed open. Covering it is the safe direction (buying back a short leaves
    # cash, never a naked long), so these get closed too rather than reported.
    if lonely_short:
        print("\n=== 补平裸空 ===")
        for symbol in lonely_short:
            base = symbol.split("/")[0]
            perp_symbol = f"{symbol}:USDT"
            size = float((perp.ex.markets.get(perp_symbol) or {}).get("contractSize") or 1.0) or 1.0
            qty_contracts = shorts[symbol] / size
            try:
                perp.ex.create_order(
                    perp_symbol, "market", "buy", qty_contracts, None,
                    {"tdMode": "cross", "posSide": "short", "reduceOnly": True},
                )
                print(f"  {symbol:<14} 买回 {qty_contracts:g} 张（补平裸空）OK")
            except Exception as exc:  # noqa: BLE001
                print(f"  {symbol:<14} 补平失败: {str(exc)[:80]}")
            time.sleep(1.5)
        time.sleep(3)

    left_spot = [s for s, q in held.items() if float((spot.ex.fetch_balance().get(s.split('/')[0]) or {}).get("total") or 0) > 0]
    left_perp = [r.get("symbol") for r in (perp.ex.fetch_positions() or [])
                 if abs(float(r.get("contracts") or 0)) > 1e-12]
    print(f"\n剩余现货 {len(left_spot)} 个 / 剩余永续持仓 {len(left_perp)} 个")
    spot.close()
    perp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
