#!/usr/bin/env python3
"""Measure the ON-CHAIN round-trip cost with real quotes, before designing signals.

On CEX the wall was 22bps round trip against a 4.7bps median 1m move. On-chain the
costs are a different animal and they are *size-dependent*: price impact grows
with size/liquidity, so "is this tradable" has to be answered per trade size.

This script quotes a real round trip (buy with SOL, then sell the tokens you
received) and reports what the whole trip cost. That single number decides
whether any on-chain signal is worth building at all:

    required move  >  round-trip cost   (otherwise the signal is irrelevant)

    export TA_PROXY=http://127.0.0.1:7897
    python3 scripts/onchain_cost_wall.py --sizes 50,100,250,500,1000

Notes:
  * read-only: quotes only, never signs or broadcasts (see ta/onchain/signers.py);
  * liquidity comes from DexScreener so impact can be expressed per unit of pool;
  * a 50bps slippage setting is used for the quote itself; actual fills vary.
"""

from __future__ import annotations

import argparse
import os
import statistics as st
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.onchain.http import HttpClient  # noqa: E402
from ta.onchain.jupiter import JupiterRouter  # noqa: E402

# Mints I am confident exist on Solana mainnet; a bad address just fails cleanly.
TOKENS: dict[str, str] = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP": "JUPyiwrYJFskUPiWaip6sbHJSE2LMxM2nCGWRLuXQp",
    "PYTH": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "JTO": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
}

DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens/{mint}"


def liquidity_usd(client: HttpClient, mint: str) -> float | None:
    try:
        data = client.get_json(DEXSCREENER.format(mint=mint))
    except Exception:  # noqa: BLE001 - liquidity is context, not essential
        return None
    pairs = data.get("pairs") or []
    best = 0.0
    for pair in pairs:
        value = ((pair.get("liquidity") or {}).get("usd")) or 0.0
        best = max(best, float(value))
    return best or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--sizes", default="50,100,250,500,1000", help="trade size in USD")
    ap.add_argument("--slippage-bps", type=int, default=50)
    ap.add_argument("--priority-fee-sol", type=float, default=0.0005,
                    help="Jito tip / priority fee per transaction, in SOL")
    ap.add_argument("--sol-price", type=float, default=0.0, help="0 = look it up")
    ap.add_argument("--pause", type=float, default=0.6)
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    onchain = config.onchain
    client = HttpClient(
        timeout=onchain.data.timeout_seconds,
        retries=onchain.data.retries,
        cache_dir=None,          # quotes must never be served from cache
    )
    router = JupiterRouter(onchain, client)
    sizes = [float(v) for v in args.sizes.split(",") if v.strip()]

    # SOL price for size conversion (and to express fees in percent)
    try:
        anchor = router.quote(router.SOL_MINT, router.USDC_MINT, 1_000_000_000, slippage_bps=50)
        sol_price = args.sol_price or (anchor.out_amount / 1e6)
    except Exception as exc:  # noqa: BLE001
        print(f"could not get a SOL price: {exc}")
        return 2
    print(f"\nSOL = ${sol_price:,.2f} | slippage setting {args.slippage_bps}bps"
          f" | priority fee {args.priority_fee_sol} SOL = "
          f"{100 * args.priority_fee_sol * sol_price / max(sizes[0], 1):.2f}% of a ${sizes[0]:.0f} trade")
    print(f"round-trip = buy SOL->token, then sell token->SOL with what you received\n")
    print(f"{'token':<6} {'liquidity':>12} {'size$':>7} {'impact(buy)':>11} "
          f"{'impact(sell)':>12} {'fees+impact':>11} {'+priority':>10}")

    rows: list[dict] = []
    for name, mint in TOKENS.items():
        liq = liquidity_usd(client, mint)
        for size in sizes:
            lamports = int(size / sol_price * 1_000_000_000)
            try:
                buy = router.quote(router.SOL_MINT, mint, lamports, args.slippage_bps)
                time.sleep(args.pause)
                sell = router.quote(mint, router.SOL_MINT, buy.out_amount, args.slippage_bps)
                time.sleep(args.pause)
            except Exception as exc:  # noqa: BLE001
                print(f"{name:<6} {'-':>12} {size:>7.0f}   quote failed: {str(exc)[:60]}")
                continue
            # % of the SOL we started with that we got back
            cost_pct = 100.0 * (1.0 - sell.out_amount / lamports)
            priority_pct = 100.0 * (2 * args.priority_fee_sol * sol_price) / size
            total_pct = cost_pct + priority_pct
            impact_buy = buy.price_impact_pct
            impact_sell = sell.price_impact_pct
            rows.append({
                "token": name, "liquidity": liq, "size": size, "impact_buy": impact_buy,
                "impact_sell": impact_sell, "cost_pct": cost_pct,
                "priority_pct": priority_pct, "total_pct": total_pct,
            })
            print(f"{name:<6} {(f'{liq:,.0f}' if liq else '-'):>12} {size:>7.0f} "
                  f"{impact_buy:>10.3f}% {impact_sell:>11.3f}% {cost_pct:>10.3f}% "
                  f"{total_pct:>9.3f}%")

    if not rows:
        print("\nno successful round trips; nothing to conclude")
        return 1

    print("\n=== 怎么读 ===")
    deep = [r for r in rows if (r["liquidity"] or 0) > 1_000_000]
    thin = [r for r in rows if (r["liquidity"] or 0) <= 1_000_000]
    for label, group in (("深度池 (>$1M)", deep), ("薄池 (<=$1M)", thin)):
        if not group:
            continue
        by_size = st.median([r["total_pct"] for r in group])
        print(f"  {label:<14} 中位往返成本 {by_size:6.2f}%  (n={len(group)})")

    # The number that decides whether on-chain signals are even worth building:
    thin_rows = [r for r in thin if r["liquidity"]]
    if thin_rows:
        slope = st.median([
            r["total_pct"] / (r["size"] / r["liquidity"]) for r in thin_rows
        ])
        gate = onchain.safety.min_liquidity_usd
        for size in (200.0, 500.0, 1000.0):
            projected = slope * (size / gate) + 2 * st.median([r["priority_pct"] for r in thin_rows])
            print(f"  投影到 ${gate:,.0f} 流动性（我们的安全下限）: ${size:>6.0f} 交易 → "
                  f"往返约 {projected:5.2f}%")

    print("\n  对照：CEX 15m 中位波动 15.7bps(0.157%)、1m 4.7bps(0.047%)，"
          "链上往返成本通常高 1–2 个数量级。")
    print("  结论口径：只有当「预期波动 > 往返成本 × k」时，链上信号才值得设计；"
          "先看这张表，再谈打分规则。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

