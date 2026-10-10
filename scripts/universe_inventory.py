#!/usr/bin/env python3
"""Inventory the WHOLE venue catalogue across all four instrument classes.

The question that keeps coming back is "how many symbols are we actually
analysing". There are three different answers and they are easy to confuse:

  * what the EXCHANGE lists   - thousands of instruments across four classes
  * what we hold HISTORY for  - data/history/okx_demo
  * what the STRATEGIES trade - config/*/app.toml symbols_by_tier

This script prints all three side by side, per class, so the gap is explicit
instead of implied. It is read-only against the venue (public endpoints, the
same simulated-trading header the demo venue uses) and writes one JSON file.

    python3 scripts/universe_inventory.py --write data/universe/inventory.json
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.history import safe_symbol  # noqa: E402

OPTION_UNDERLYINGS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "XRP-USD",
    "ADA-USD", "AVAX-USD", "LINK-USD", "SUI-USD", "PEPE-USD",
]


def _get(client, path: str, params: dict) -> dict:
    url = f"https://www.okx.com{path}"
    response = client.request("GET", url, params=params, headers={"x-simulated-trading": "1"})
    response.raise_for_status()
    return response.json()


def _to_symbol(inst_id: str, inst_type: str) -> str:
    """OKX instId -> the symbol convention the rest of the project uses.

    Getting this wrong is not cosmetic: an earlier version mapped
    `BTC-USDT-SWAP` to `BTC/USDT`, which is the SPOT symbol, so the perp row
    reported "we have history for 14 swaps" when we have none - it was matching
    spot files. Perps carry the settlement suffix (`BTC/USDT:USDT`), exactly as
    CcxtVenue and the risk engine's `leg_of` see them.
    """
    if inst_type == "SWAP" and inst_id.endswith("-USDT-SWAP"):
        base = inst_id[: -len("-USDT-SWAP")]
        return f"{base}/USDT:USDT"
    if inst_id.endswith("-USDT"):
        return f"{inst_id[:-len('-USDT')]}/USDT"
    return inst_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", default="")
    ap.add_argument("--config-dir", default="config")
    args = ap.parse_args()

    import httpx  # ccxt ships httpx; used here for a plain public GET

    proxy = "http://127.0.0.1:7897"
    config = Config.load(args.config_dir)
    configured = sorted(config.traded_symbols)

    history_dir = config.data_dir / "history" / "okx_demo"
    # Key by the file stem and compare against `safe_symbol(symbol)`. Reversing
    # the file name back into a symbol cannot work: perp files are
    # `BTC-USDT_USDT__1h.jsonl` because safe_symbol writes ':' as '_', so the old
    # `replace("-", "/", 1)` reverse map produced `BTC/USDT_USDT`, never matched
    # `BTC/USDT:USDT`, and this report printed "SWAP 我们有历史 0" while 147 perp
    # series were sitting on disk.
    have: dict[str, set[str]] = collections.defaultdict(set)
    for path in history_dir.glob("*.jsonl"):
        have[path.name.split("__")[0]].add(path.name.split("__")[1].replace(".jsonl", ""))

    out: dict[str, dict] = {}
    with httpx.Client(proxy=proxy, timeout=60.0, headers={"x-simulated-trading": "1"}) as client:
        for inst_type in ("SPOT", "SWAP", "FUTURES"):
            payload = _get(client, "/api/v5/public/instruments", {"instType": inst_type})
            rows = payload.get("data") or []
            live = [r for r in rows if r.get("state") == "live"]
            usdt = [r for r in live if r.get("instId", "").endswith("-USDT")
                    or r.get("settleCcy") == "USDT"]
            out[inst_type] = {
                "listed": len(rows),
                "live": len(live),
                "usdt_lines": len(usdt),
                "ids_usdt": sorted(r["instId"] for r in usdt),
            }

        # Options are quoted per underlying family, not as one flat list.
        option_ids: list[str] = []
        families: dict[str, int] = {}
        for uly in OPTION_UNDERLYINGS:
            payload = _get(client, "/api/v5/public/instruments",
                           {"instType": "OPTION", "uly": uly})
            rows = [r for r in (payload.get("data") or []) if r.get("state") == "live"]
            if rows:
                families[uly] = len(rows)
                option_ids.extend(r["instId"] for r in rows)
        out["OPTION"] = {
            "listed": len(option_ids),
            "live": len(option_ids),
            "usdt_lines": 0,          # options are coin-margined on OKX
            "families": families,
        }

    print(f"{'类别':10s} {'交易所 live':>12s} {'USDT 线':>9s} "
          f"{'我们有历史':>11s} {'配置纳入':>9s}")
    total_live = 0
    for inst_type, info in out.items():
        if inst_type == "OPTION":
            with_hist = 0
            conf = 0
        else:
            symbols = [_to_symbol(i, inst_type) for i in (info.get("ids_usdt") or [])]
            with_hist = sum(1 for s in symbols if have.get(safe_symbol(s)))
            conf = sum(1 for s in symbols if s in configured)
        total_live += info["live"]
        print(f"{inst_type:10s} {info['live']:12d} {info['usdt_lines']:9d} "
              f"{with_hist:11d} {conf:9d}")
    print(f"{'合计':10s} {total_live:12d}")

    trad_hist = sorted(s for s in have if s.split("-")[0].startswith("X"))
    print(f"\n历史目录 {history_dir}: {len(have)} 个标的，其中 TradFi(X 开头) {len(trad_hist)} 个")
    print(f"配置 {args.config_dir} 的交易池: {len(configured)} 个"
          f"（tiers: { {k: len(v) for k, v in config.universe.symbols_by_tier.items()} }）")
    print("\n期权族：" + ", ".join(f"{k}={v}" for k, v in out["OPTION"]["families"].items()))

    if args.write:
        target = config.path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "venue": "okx_demo",
            "classes": out,
            "history_symbols": sorted(have),
            "history_timeframes": {s: sorted(t) for s, t in have.items()},
            "configured_pool": configured,
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwritten: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
