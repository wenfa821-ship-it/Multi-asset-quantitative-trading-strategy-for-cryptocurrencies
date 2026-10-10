#!/usr/bin/env python3
"""What would it actually take to BUY one option? Public endpoints only.

1.1 starts with the venue adapter, and the adapter cannot be written from the docs:
OKX options are coin-margined, so the premium, the tick and the minimum order are
all quoted in the underlying's coin, not in USDT. Before writing any order path
this answers, from public data:

  * which contracts exist for a family near a target expiry,
  * each contract's spec (ctVal / ctMult / minSz / lotSz / tickSz / settleCcy),
  * the live quote and book (can we buy at all, and at what spread),
  * the cost of the SMALLEST possible order, in coin and in USD.

Read-only: it never signs, never orders.

    python3 scripts/options_probe.py --family BTC-USD
    python3 scripts/options_probe.py --family BTC-USD --inst BTC-USD-261127-80000-C
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

DEFAULT_PROXY = "http://127.0.0.1:7897"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/125 Safari/537.36"}


def opener(proxy: str | None):
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def get_json(op, path: str, params: dict, timeout: float = 40.0) -> dict:
    url = f"https://www.okx.com{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=UA)
    with op.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def dte(inst_id: str, today: datetime.date) -> int:
    """Days to expiry from the instId: BTC-USD-260108-80000-C -> 26/01/08."""
    parts = inst_id.split("-")
    stamp = parts[2] if len(parts) > 2 else ""
    try:
        expiry = datetime.date(2000 + int(stamp[:2]), int(stamp[2:4]), int(stamp[4:6]))
    except (ValueError, IndexError):
        return -999
    return (expiry - today).days


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="BTC-USD", help="uly, e.g. BTC-USD")
    ap.add_argument("--inst", default="", help="probe one contract instead of listing the family")
    ap.add_argument("--target-dte", type=int, default=30)
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    args = ap.parse_args()

    op = opener(args.proxy or None)
    today = datetime.datetime.now(datetime.UTC).date()
    if args.inst:
        rows = [r for r in (get_json(op, "/api/v5/public/instruments",
                                     {"instType": "OPTION", "instId": args.inst}).get("data") or [])]
    else:
        rows = get_json(op, "/api/v5/public/instruments",
                        {"instType": "OPTION", "uly": args.family}).get("data") or []
    if not rows:
        print(f"no option instruments for {args.family or args.inst} (demo catalogue?)")
        return 1

    live = [r for r in rows if str(r.get("state")) == "live"]
    print(f"{args.family}: {len(rows)} instruments ({len(live)} live); "
          f"strike range {min(float(r['stk']) for r in live):g}-{max(float(r['stk']) for r in live):g}")
    # The nearest expiry at or beyond the target DTE - what a 30-day structure uses.
    expiries = sorted({r["instId"].split("-")[2] for r in live}, key=lambda s: dte(f"{args.family}-{s}-0-C", today))
    chosen_expiry = next((e for e in expiries if dte(f"{args.family}-{e}-0-C", today) >= args.target_dte),
                         expiries[-1] if expiries else "")
    print(f"expiries: {', '.join(f'{e}({dte(f"{args.family}-{e}-0-C", today)}d)' for e in expiries[:6])}")
    print(f"chosen expiry for ~{args.target_dte}d: {chosen_expiry} "
          f"({dte(f'{args.family}-{chosen_expiry}-0-C', today)}d)")

    # Anchor on the forward: the strike nearest the index price, via opt-summary.
    summary = get_json(op, "/api/v5/public/opt-summary", {"uly": args.family, "expTime": ""})
    fwd = 0.0
    for row in summary.get("data") or []:
        if str(row.get("instId", "")).endswith(chosen_expiry) or True:
            for key in ("fwdPx", "idxPx", "ulyPx"):
                if row.get(key):
                    fwd = float(row[key]); break
            if fwd:
                break

    same = [r for r in live if r["instId"].split("-")[2] == chosen_expiry]
    strikes = sorted({float(r["stk"]) for r in same})
    if not strikes:
        print("no strikes for the chosen expiry")
        return 1
    atm = min(strikes, key=lambda s: abs(s - fwd)) if fwd else strikes[len(strikes) // 2]
    print(f"\n{'instId':32s}{'ctVal':>8s}{'ctMult':>8s}{'minSz':>8s}{'lotSz':>9s}{'tickSz':>9s} settle")
    probe: dict = {}
    for r in same:
        if abs(float(r["stk"]) - atm) > 1e-9:
            continue
        print(f"{r['instId']:32s}{str(r.get('ctVal')):>8s}{str(r.get('ctMult')):>8s}"
              f"{str(r.get('minSz')):>8s}{str(r.get('lotSz')):>9s}{str(r.get('tickSz')):>9s} {r.get('settleCcy')}")
        probe[r["instId"]] = r

    for inst_id, spec in probe.items():
        try:
            ticker = (get_json(op, "/api/v5/market/ticker", {"instId": inst_id}).get("data") or [{}])[0]
            book = (get_json(op, "/api/v5/market/books", {"instId": inst_id, "sz": "5"}).get("data") or [{}])[0]
        except Exception as exc:  # noqa: BLE001 - an unreadable book is a finding
            print(f"  {inst_id}: quote failed {type(exc).__name__}")
            continue
        bids, asks = book.get("bids") or [], book.get("asks") or []
        ask = float(asks[0][0]) if asks else None
        bid = float(bids[0][0]) if bids else None
        mark = float(ticker.get("last") or 0.0)
        ct_val = float(spec.get("ctVal") or 1.0)
        ct_mult = float(spec.get("ctMult") or 1.0)
        min_sz = float(spec.get("minSz") or 0.0)
        print(f"  mark={mark} last={ticker.get('last')} bid={bid} ask={ask} "
              f"bids={len(bids)} asks={len(asks)}")
        if ask:
            # The contract multiplier is ctMult, NOT ctVal: measured 2026-10-10,
            # BTC options return ctVal=1 with ctMult=0.01, and the premium quoted
            # (0.059 "BTC") is per ctVal x ctMult of underlying. Using ctVal alone
            # overstated the smallest order 100x ($4,952 instead of $49.5) - and the
            # sanity check is the model: an ATM 30d call on 0.01 BTC at ~50% vol
            # costs ~0.4 * sigma * sqrt(T) * notional ≈ $50, not $5k.
            units = ct_val * ct_mult * min_sz
            coin = ask * units
            usd = coin * fwd if (fwd and str(spec.get("settleCcy", "")).upper() != "USD") else coin
            print(f"  smallest order: {min_sz} contract(s) x ctVal {ct_val:g} x ctMult {ct_mult:g} "
                  f"= {units:.6g} underlying -> {coin:.8f} {spec.get('ctValCcy')} "
                  f"(≈ ${usd:,.2f})")
        else:
            print("  no ask: cannot BUY this contract right now (README 63's one-sided-book trap)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
