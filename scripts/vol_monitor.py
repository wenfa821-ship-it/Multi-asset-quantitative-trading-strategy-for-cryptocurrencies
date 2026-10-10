#!/usr/bin/env python3
"""Realised vol vs implied vol, daily. Decides WHICH SIDE of an option to be on.

Why this exists
---------------
Options were proposed as a tool for the portfolio. The question that decides
whether that helps or hurts is not "are options useful" but **is implied vol
above or below what the market actually realises**:

    IV > RV  -> selling premium is paid; buying premium is a tax
    IV < RV  -> buying premium is (theoretically) cheap; selling is a trap

At the first measurement (2026-10-05) IV sat *at or slightly below* 30d realised
for BTC/ETH/SOL, which is the opposite of the usual "options are too expensive"
intuition. That is a single snapshot, so this script records it every day.

It also records the cost wall: what an ATM straddle costs as a % of the
underlying, and how far the underlying must move to break even. On 2026-10-05
that was 7.1% / 9.8% of notional for a 24-day BTC/ETH straddle - roughly 5x the
on-chain round-trip cost and ~35x the spot round trip.

Read-only: public market data. No keys, no orders. Writes data/vol/rv_iv.jsonl.

    export TA_PROXY=http://127.0.0.1:7897
    python3 scripts/vol_monitor.py
    python3 scripts/vol_monitor.py --status
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
import time
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
STORE = PROJECT / "data" / "vol" / "rv_iv.jsonl"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36"}
UNDERLYINGS = ("BTC", "ETH", "SOL")


def _opener():
    import os

    proxy = os.environ.get("TA_PROXY") or os.environ.get("HTTPS_PROXY")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


OPENER = _opener()


def get(path: str) -> dict:
    return json.load(OPENER.open(urllib.request.Request("https://www.okx.com" + path, headers=UA), timeout=25))


def realised_vol(symbol: str) -> dict[str, float | None]:
    """Annualised close-to-close vol from daily candles."""
    data = get(f"/api/v5/market/candles?instId={symbol}-USDT&bar=1D&limit=200")
    rows = sorted((int(r[0]), float(r[4])) for r in data["data"])
    closes = [c for _, c in rows]

    def vol(window: int):
        c = closes[-window:]
        if len(c) < 8:
            return None
        rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
        return st.pstdev(rets) * math.sqrt(365) * 100

    return {"rv7": vol(8), "rv30": vol(31), "rv90": vol(91)}


def implied_vol(uly: str) -> dict | None:
    """ATM mark vol and the straddle cost wall, for the expiry nearest 30 days."""
    tk = [r for r in get(f"/api/v5/market/tickers?instType=OPTION&uly={uly}")["data"] if "_UM" in r["instId"]]
    summ = {r["instId"]: r for r in get(f"/api/v5/public/opt-summary?uly={uly}")["data"] if "_UM" in r["instId"]}
    by_exp: dict[str, list[dict]] = {}
    for r in tk:
        parts = r["instId"].split("-")
        if len(parts) >= 5 and float(r.get("bidPx") or 0) > 0:
            by_exp.setdefault(parts[2], []).append(r)
    if not by_exp:
        return None

    def days(code: str) -> int:
        t = time.strptime("20" + code, "%Y%m%d")
        return int((time.mktime(t) - time.time()) / 86400)

    exp = min(by_exp, key=lambda c: abs(days(c) - 30))
    rows = by_exp[exp]
    fwd = float(summ[rows[0]["instId"]]["fwdPx"])

    def atm(kind: str) -> dict:
        cand = [r for r in rows if r["instId"].split("-")[4] == kind]
        return min(cand, key=lambda r: abs(float(r["instId"].split("-")[3]) - fwd))

    call, put = atm("C"), atm("P")
    ca, cb, pa = float(call["askPx"]), float(call["bidPx"]), float(put["askPx"])
    greeks = summ[call["instId"]]
    premium_pct = (ca + pa) / fwd * 100
    return {
        "expiry": exp,
        "days": days(exp),
        "fwd": fwd,
        "iv": float(greeks["markVol"]) * 100,
        "iv_bid": float(greeks["bidVol"]) * 100,
        "iv_ask": float(greeks["askVol"]) * 100,
        "straddle_pct": premium_pct,
        "breakeven_move_pct": premium_pct / 2,
        "call_spread_pct": (ca - cb) / ((ca + cb) / 2) * 100,
        "two_sided": len(rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        if not STORE.exists():
            print("no records yet")
            return 0
        rows = [json.loads(l) for l in STORE.read_text().splitlines() if l.strip()]
        for r in rows[-9:]:
            print(f"  {r['date']} {r['symbol']:<4} RV30={r.get('rv30') or 0:5.1f}% "
                  f"IV={r.get('iv') or 0:5.1f}%  差={r.get('iv_minus_rv30') or 0:+5.1f}pt  "
                  f"跨式={r.get('straddle_pct') or 0:4.1f}%")
        return 0

    today = time.strftime("%Y-%m-%d")
    STORE.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if STORE.exists():
        for line in STORE.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                seen.add((row.get("date"), row.get("symbol")))

    print(f"{'标的':<5}{'RV7':>7}{'RV30':>7}{'RV90':>7}{'IV':>7}{'IV-RV30':>9}{'跨式%':>8}{'回本±%':>8}")
    added = 0
    for symbol in UNDERLYINGS:
        try:
            rec = {"date": today, "symbol": symbol, "ts": int(time.time())}
            rec.update(realised_vol(symbol))
            iv = implied_vol(f"{symbol}-USD")
            if iv:
                rec.update(iv)
                rec["iv_minus_rv30"] = round(rec["iv"] - (rec.get("rv30") or 0), 2)
            f = lambda k, w=7, p=1: f"{rec[k]:>{w}.{p}f}" if rec.get(k) is not None else f"{'-':>{w}}"
            print(f"{symbol:<5}{f('rv7')}{f('rv30')}{f('rv90')}{f('iv')}"
                  f"{f('iv_minus_rv30',9)}{f('straddle_pct')}{f('breakeven_move_pct')}")
            if (today, symbol) not in seen:
                with STORE.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                added += 1
        except Exception as exc:  # noqa: BLE001
            print(f"{symbol:<5} error {exc}")
        time.sleep(0.3)

    print(f"\nadded {added} record(s) -> {STORE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
