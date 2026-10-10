#!/usr/bin/env python3
"""Options data layer: read the venue's own IV surface, do not invent one.

Why start here
--------------
Options is the last leg with nothing behind it (README 34: no venue adapter, no
strategy, no history). The honest first step is not a pricing model - it is asking
OKX what it already publishes, because a surface built from the venue's own
`markIV/greeks` is a fact, while one built from candles and an assumed model is an
opinion.

OKX exposes `/api/v5/public/opt-summary?uly=<family>` with, per contract:
`markIV`, `bidIV`, `askIV`, `delta`, `gamma`, `theta`, `vega`, plus the underlying
price. Options are coin-margined and quoted per family (BTC-USD, ETH-USD, ...),
which is why the family has to be passed rather than a flat instrument list.

LIVE catalogue, not the demo one - the same lesson as the funding rate (README
36.4). Measured 2026-10-08: the demo venue answers with 654 contracts whose every
IV is ZERO (no market making there), while the live answer carries real quotes
(BTC 261008 80000-C: bid 26.9% / ask 35.8%). A surface built from the demo feed
would be a surface of zeros.

What it reports per family:

  * how many live contracts, expiries and strikes the venue actually lists
  * the ATM implied vol per expiry (nearest strike to spot)
  * the 25-delta put/call skew per expiry - the number an option seller is paid
    for, and the first thing a premium-buying strategy has to beat

Read-only public endpoints, no keys, no orders. Writes one JSON file.

    export HTTPS_PROXY=http://127.0.0.1:7897
    python3 scripts/options_surface.py --families BTC-USD,ETH-USD,SOL-USD

Stdlib `urllib`, not `httpx`, on purpose: this script is also called by
scripts/daily_research.py under the launchd interpreter, which is a bare
python.org build with no third-party packages (measured 2026-10-08:
`import httpx` -> ModuleNotFoundError under that interpreter).
"""

from __future__ import annotations

import argparse
import collections
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

DEFAULT_FAMILIES = ["BTC-USD", "ETH-USD", "SOL-USD"]
DEFAULT_PROXY = "http://127.0.0.1:7897"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/125 Safari/537.36"}


def _opener(proxy: str | None):
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def get_json(opener, path: str, params: dict, timeout: float = 40.0) -> dict:
    url = f"https://www.okx.com{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=UA)
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _iv(row: dict) -> float:
    """The contract's implied vol, whatever this venue calls it.

    Measured 2026-10-08: OKX's opt-summary answers with `markVol` / `bidVol` /
    `askVol`, not the `markIV` / `bidIV` / `askIV` the docs suggest, so both names
    are tried before the midpoint fallback.
    """
    for key in ("markVol", "markIV"):
        value = row.get(key)
        if value not in (None, ""):
            return float(value)
    bid = float(row.get("bidVol") or row.get("bidIV") or 0.0)
    ask = float(row.get("askVol") or row.get("askIV") or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return 0.0


def summarise_family(opener, family: str) -> dict:
    payload = get_json(opener, "/api/v5/public/opt-summary", {"uly": family})
    rows = [r for r in (payload.get("data") or []) if _iv(r) > 0]
    if not rows:
        return {"family": family, "contracts": 0, "expiries": []}

    # Anchor ATM on the FORWARD price. `ulyPx`/`idxPx` are not in the response;
    # `fwdPx` is, and it is the correct anchor anyway (an option is ATM against
    # the forward, not the index).
    spot = 0.0
    for row in rows:
        value = row.get("fwdPx") or row.get("ulyPx") or row.get("idxPx")
        if value not in (None, ""):
            spot = float(value)
            break
    by_expiry: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        inst = str(row.get("instId") or "")
        parts = inst.split("-")
        if len(parts) < 5:
            continue
        expiry, strike, right = parts[2], float(parts[3]), parts[4]
        by_expiry[expiry].append(
            {
                "inst_id": inst,
                "strike": strike,
                "right": right,
                "mark_iv": _iv(row),
                "delta": float(row.get("delta") or 0.0),
                "vega": float(row.get("vega") or 0.0),
            }
        )

    out = []
    for expiry, contracts in sorted(by_expiry.items()):
        atm = min(contracts, key=lambda c: abs(c["strike"] - spot)) if spot else None
        calls = sorted((c for c in contracts if c["right"] == "C"), key=lambda c: -c["delta"])
        puts = sorted((c for c in contracts if c["right"] == "P"), key=lambda c: c["delta"])
        # 25-delta skew: the classic shape read. Calls with a high delta (deep ITM)
        # and puts with a low delta (deep OTM) bracket the wings; the difference
        # says which side the market is paying up for.
        call_25 = min(calls, key=lambda c: abs(abs(c["delta"]) - 0.25)) if calls else None
        put_25 = min(puts, key=lambda c: abs(abs(c["delta"]) - 0.25)) if puts else None
        skew = None
        if call_25 and put_25:
            skew = round((put_25["mark_iv"] - call_25["mark_iv"]) * 100.0, 2)
        out.append(
            {
                "expiry": expiry,
                "contracts": len(contracts),
                "atm_strike": atm["strike"] if atm else None,
                "atm_iv_pct": round(atm["mark_iv"] * 100.0, 2) if atm else None,
                "put25_iv_pct": round(put_25["mark_iv"] * 100.0, 2) if put_25 else None,
                "call25_iv_pct": round(call_25["mark_iv"] * 100.0, 2) if call_25 else None,
                "skew_25d_pp": skew,
            }
        )
    return {
        "family": family,
        "underlying_px": spot,
        "contracts": len(rows),
        "expiries": out,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", default=",".join(DEFAULT_FAMILIES))
    ap.add_argument("--write", default="data/options/surface.json")
    ap.add_argument(
        "--proxy",
        default=os.environ.get("TA_PROXY") or os.environ.get("HTTPS_PROXY") or DEFAULT_PROXY,
    )
    args = ap.parse_args()

    config = Config.load("config")
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    out: dict[str, dict] = {}
    # No simulated-trading header: the IVs live in the live catalogue (see above).
    opener = _opener(args.proxy)
    for family in families:
        # Retry, because the local proxy drops a single request often enough to
        # matter. Measured 2026-10-08: one run lost SOL while BTC/ETH over the
        # same proxy succeeded - and because a failed family is simply skipped,
        # that run quietly wrote a surface file holding one family fewer.
        summary = None
        for attempt in range(1, 4):
            try:
                summary = summarise_family(opener, family)
                break
            except Exception as exc:  # noqa: BLE001
                print(f"{family}: attempt {attempt}/3 failed {type(exc).__name__}: "
                      f"{str(exc)[:60]}")
                if attempt < 3:
                    time.sleep(2.0 * attempt)
        if summary is None:
            print(f"{family}: GIVING UP - family absent from this snapshot")
            continue
        out[family] = summary
        print(f"\n=== {family}  标的={summary.get('underlying_px')}  "
              f"合约={summary['contracts']}  到期={len(summary['expiries'])} ===")
        print(f"  {'到期':<10}{'合约':>6}{'ATM行权':>12}{'ATM IV':>9}"
              f"{'put25 IV':>10}{'call25 IV':>11}{'25d倾斜':>10}")
        for row in summary["expiries"][:8]:
            print(f"  {row['expiry']:<10}{row['contracts']:>6}{row['atm_strike'] or 0:>12,.0f}"
                  f"{(row['atm_iv_pct'] or 0):>8.1f}%{(row['put25_iv_pct'] or 0):>9.1f}%"
                  f"{(row['call25_iv_pct'] or 0):>10.1f}%{(row['skew_25d_pp'] or 0):>9.2f}")

    target = config.path(args.write)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"generated_at": int(time.time() * 1000), "families": out},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"\nwritten: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
