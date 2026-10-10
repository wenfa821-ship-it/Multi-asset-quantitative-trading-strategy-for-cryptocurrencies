#!/usr/bin/env python3
"""Which X-prefixed spot lines are really TradFi? Ask the venue, not the prefix.

The TradFi leg is matched by `symbol_prefix = "X"`, and that rule is wrong in both
directions. Everything in OKX's crypto book that merely starts with X (XRP, XLM,
XMR, XTZ, XAUT, XEM, XPL, ...) is captured by it, and OKX keeps listing new ones
(XDP-USDT, 2026-09-28). On 2026-10-09 three candidate discriminators were tried:

  * `contTdSwTime`             - falsified: non-empty for 122 instruments,
                                 including obvious crypto (2Z/ACT/ANIME...).
  * "equities are demo-only"   - falsified: the live catalogue lists them too.
  * `instCategory`             - REAL (1 crypto / 3 tokenised equity / 4 commodity
                                 token) but only on the LIVE catalogue; the demo
                                 copy disagrees on 8 ids and calls XRP an equity.

So this script asks the live catalogue (authoritative) and the live swap list (for
commodity perps) about every X-prefixed USDT line the DEMO venue offers - the
venue we actually trade - and fails when a line would draw on the TradFi quota
without positive evidence that it belongs there. Read-only against the venue; it
writes one JSON snapshot so the answer is auditable later.

    python3 scripts/leg_identity.py [--write data/universe/leg_identity.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.risk.engine import leg_of  # noqa: E402

OKX = "https://www.okx.com"


def _proxy() -> str:
    for key in ("TA_PROXY", "HTTPS_PROXY", "https_proxy"):
        if os.environ.get(key):
            return str(os.environ[key])
    # The host's egress rule; a direct connection to okx.com times out here.
    return "http://127.0.0.1:7897"


def fetch(inst_type: str, simulated: bool, timeout: float = 30.0, attempts: int = 3) -> list[dict]:
    params = urllib.parse.urlencode({"instType": inst_type})
    request = urllib.request.Request(f"{OKX}/api/v5/public/instruments?{params}")
    # okx.com answers 403 to urllib's default agent; it wants a normal one.
    request.add_header("User-Agent", "Mozilla/5.0 (compatible; ta-leg-identity/1.0)")
    if simulated:
        request.add_header("x-simulated-trading", "1")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": _proxy(), "http": _proxy()})
    )
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with opener.open(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("code") not in ("0", 0):
                raise RuntimeError(f"okx {inst_type} simulated={simulated}: {payload.get('msg')}")
            return payload.get("data") or []
        except Exception as exc:  # noqa: BLE001 - the proxy drops ~1/3 of calls
            last = exc
            print(f"  {inst_type} simulated={simulated} attempt {attempt} failed: {exc}")
            time.sleep(2.0 * attempt)
    raise RuntimeError(f"okx {inst_type} simulated={simulated}: {last}")


def classify(inst_id: str, live_spot: dict, live_swap: dict) -> str:
    """crypto | equity | commodity | unknown - evidence, never the name."""
    spot = live_spot.get(inst_id) or {}
    swap = live_swap.get(f"{inst_id[:-len('-USDT')]}-USDT-SWAP") or {}
    cats = {str(spot.get("instCategory") or ""), str(swap.get("instCategory") or "")}
    if "3" in cats:
        return "equity"
    if "4" in cats:
        return "commodity"
    if "1" in cats:
        return "crypto"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", default="")
    args = parser.parse_args()

    config = Config.load(str(PROJECT / "config"))
    members = {n: set(getattr(l, "symbols", []) or []) for n, l in config.risk.legs.items()}
    prefixes = {
        n: getattr(l, "symbol_prefix", "")
        for n, l in config.risk.legs.items()
        if getattr(l, "symbol_prefix", "")
    }
    pins = members.get("spot", set())

    try:
        live_spot = {r["instId"]: r for r in fetch("SPOT", simulated=False)}
        demo_spot = fetch("SPOT", simulated=True)
        live_swap = {r["instId"]: r for r in fetch("SWAP", simulated=False)}
    except Exception as exc:  # noqa: BLE001 - a dead proxy is not a bad leg config
        print(f"venue unreachable, cannot check leg identity: {exc}")
        return 2

    rows: list[dict] = []
    problems: list[str] = []
    for inst in sorted(demo_spot, key=lambda r: r["instId"]):
        inst_id = inst["instId"]
        if not inst_id.startswith("X") or inst.get("quoteCcy") != "USDT":
            continue
        symbol = f"{inst_id[:-len('-USDT')]}/USDT"
        verdict = classify(inst_id, live_spot, live_swap)
        leg = leg_of(symbol, members, prefixes)
        pinned = symbol in pins
        row = {
            "symbol": symbol,
            "demo_state": inst.get("state"),
            "demo_category": inst.get("instCategory"),
            "live_category": (live_spot.get(inst_id) or {}).get("instCategory", ""),
            "listed": inst.get("listTime", ""),
            "verdict": verdict,
            "pinned_to_spot": pinned,
            "routed_leg": leg,
        }
        # The failure this script exists for: something lands on the 30% TradFi
        # quota with no evidence it is an equity or a commodity.
        if leg == "tradfi" and verdict in ("crypto", "unknown"):
            problems.append(f"{symbol} -> tradfi but venue says {verdict}")
            row["ok"] = False
        elif leg != "tradfi" and verdict in ("equity", "commodity") and not pinned:
            problems.append(f"{symbol} -> {leg} but venue says {verdict}")
            row["ok"] = False
        elif pinned and verdict == "equity":
            problems.append(f"{symbol} pinned to spot but venue says equity")
            row["ok"] = False
        else:
            row["ok"] = True
        rows.append(row)

    width = max(len(r["symbol"]) for r in rows) if rows else 10
    print(f"{'symbol':{width}s}  live  demo  verdict     pinned  leg")
    for r in rows:
        flag = " " if r["ok"] else "!"
        print(
            f"{flag}{r['symbol']:{width}s}  {str(r['live_category']):>4s}  "
            f"{str(r['demo_category']):>4s}  {r['verdict']:<10s}  "
            f"{str(r['pinned_to_spot']):<6s}  {r['routed_leg']}"
        )
    print(f"\n{len(rows)} X-prefixed USDT lines on the demo venue; {len(problems)} problem(s)")
    for problem in problems:
        print(f"  ! {problem}")

    if args.write:
        out = Path(args.write)
        if not out.is_absolute():
            out = PROJECT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"rows": rows, "problems": problems}, indent=1, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
