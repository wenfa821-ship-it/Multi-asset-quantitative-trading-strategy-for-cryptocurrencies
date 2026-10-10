#!/usr/bin/env python3
"""The pre-trade funnel for the on-chain sleeve: does anything ever get through?

Why this exists
---------------
The proposed new-token sleeve spends 60% of the plan. Before simulating *entries*
on it, we have to answer a cheaper question first: **how many candidates survive
the safety filter at all?** If the filter rejects 100% of what pump.fun/Raydium
actually produces, then the sleeve is structurally unfillable and no signal
research is needed to say so.

This runs the exact same gate the executor uses (`TokenScreener`), over the
current watchlist, and reports the rejection histogram. It is the same filter
that guards live execution - not a reimplementation.

Read-only: it fetches public token data and screens it. It never signs, never
quotes a swap, never broadcasts (see ta/onchain/signers.py).

    export TA_PROXY=http://127.0.0.1:7897     # if the network needs the tunnel
    python3 scripts/onchain_funnel.py --limit 20
    python3 scripts/onchain_funnel.py --mints <mint1>,<mint2>
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.log import setup  # noqa: E402
from ta.onchain.providers import CompositeTokenService  # noqa: E402
from ta.onchain.screener import TokenScreener  # noqa: E402

_NUM = re.compile(r"-?\d[\d,\.]*")


def bucket(reason: str) -> str:
    """Collapse 'REJECT liquidity 3,136 < 50,000 USD' into a countable bucket."""
    text = _NUM.sub("N", reason)
    return text.strip()


def load_mints(path: Path, limit: int) -> list[tuple[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cands = payload.get("candidates", payload if isinstance(payload, list) else [])
    out: list[tuple[str, str]] = []
    for item in cands[:limit]:
        mint = item.get("mint") or item.get("address")
        if mint:
            out.append((mint, item.get("symbol") or "?"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--watchlist", default="data/onchain/watchlist.json")
    ap.add_argument("--limit", type=int, default=20, help="how many candidates to screen")
    ap.add_argument("--mints", default="", help="explicit comma-separated mints (overrides --watchlist)")
    ap.add_argument("--pause", type=float, default=0.4, help="seconds between candidates")
    ap.add_argument("--out-dir", default="reports")
    args = ap.parse_args()

    config = Config.load(args.config_dir)
    logger = setup(config.log_dir, level="WARNING")

    if args.mints:
        targets = [(m.strip(), "?") for m in args.mints.split(",") if m.strip()]
        source = "cli"
    else:
        targets = load_mints(config.path(args.watchlist), args.limit)
        source = args.watchlist
    if not targets:
        print("no candidates to screen")
        return 2

    service = CompositeTokenService.from_config(config.onchain, config.project_dir)
    screener = TokenScreener(config.onchain)

    rows: list[dict] = []
    for i, (mint, symbol) in enumerate(targets, 1):
        row: dict = {"mint": mint, "symbol": symbol, "source": source}
        try:
            snap = service.snapshot(mint)
            result = screener.screen(snap)
            row.update(
                symbol=snap.symbol or symbol,
                age_minutes=snap.age_minutes,
                liquidity_usd=snap.liquidity_usd,
                volume_24h_usd=snap.volume_24h_usd,
                holder_count=snap.holder_count,
                top10_holder_pct=snap.top10_holder_pct,
                price_usd=snap.price_usd,
                passed=bool(result.passed),
                score=float(result.score),
                reasons=list(result.reasons),
            )
            status = "PASS" if result.passed else "reject"
            liq = row["liquidity_usd"] or 0.0
            print(f"[{i:>2}/{len(targets)}] {status:<6} {row['symbol'][:14]:<14} "
                  f"liq=${liq:>12,.0f}  score={row['score']:>6.2f}  reasons={len(row['reasons'])}")
        except Exception as exc:  # noqa: BLE001 - a bad mint must not kill the sweep
            row.update(passed=False, score=0.0, reasons=[f"ERROR {type(exc).__name__}"], error=str(exc))
            print(f"[{i:>2}/{len(targets)}] error  {symbol[:14]:<14} {exc}")
        rows.append(row)
        time.sleep(max(0.0, args.pause))

    passed = [r for r in rows if r.get("passed")]
    hist: collections.Counter[str] = collections.Counter()
    for row in rows:
        for reason in row.get("reasons") or []:
            if not reason.startswith("REJECT"):
                continue
            hist[bucket(reason)] += 1

    print()
    print(f"funnel: {len(passed)} / {len(rows)} passed the safety filter "
          f"({100.0 * len(passed) / len(rows):.1f}%)")
    print()
    print("rejection reasons (bucketed):")
    for reason, count in hist.most_common(20):
        print(f"  {count:>4}  {reason}")

    out_dir = config.path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    payload = {
        "generated_ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": source,
        "screened": len(rows),
        "passed": len(passed),
        "reasons": dict(hist),
        "rows": rows,
    }
    json_path = out_dir / f"onchain-funnel-{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print(f"wrote {json_path}")
    if not passed:
        print("note: zero candidates cleared the gate - the sleeve has nothing to buy today.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
