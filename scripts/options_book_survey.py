#!/usr/bin/env python3
"""How wide is the option book, really? Public endpoints only.

Why this exists
---------------
1.1 is a *buy-premium* sleeve: a buyer's maximum loss IS the premium, so the entry
cost is the strategy's whole hurdle. On 2026-10-10 a single sampled contract
(`BTC-USD-261030-68000-C`, bid 0.114 / ask 0.182) suggested a spread near half the
price against ~0.8% on the live book - wide enough that the idea could be dead
before anyone writes an order path. One contract is not a distribution.

So this walks several underlyings x several expiries x a fixed RELATIVE strike grid
around each expiry's forward, reads the top of book, and reports the spread. The
convention is stated everywhere because it changes the number by ~30% here:

    spread_pct      = (ask - bid) / mid      <- the round-trip width
    half_spread_pct = (ask - mid) / mid      <- what a buyer pays on entry alone

It also reports how many contracts are two-sided at all: a chain where half the
quotes are one-sided is not tradable no matter how tight the ones that are.

Read-only: public data endpoints, no signature, no orders, no credentials.

    python3 scripts/options_book_survey.py
    python3 scripts/options_book_survey.py --underlyings BTC-USD,ETH-USD --expiries 2 --strikes 3
    python3 scripts/options_book_survey.py --write --out data/options/book_survey.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

DEFAULT_PROXY = "http://127.0.0.1:7897"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/125 Safari/537.36"}

# Relative to the expiry's forward, so expiries and underlyings are comparable.
STRIKE_OFFSETS = (-0.10, -0.05, 0.0, 0.05, 0.10)


def opener(proxy: str | None):
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def get_json(op, path: str, params: dict, timeout: float = 40.0, attempts: int = 3) -> dict:
    """Fetch with a bounded retry: the local proxy drops ~a third of calls under load.

    Measured on the first run of this survey (2026-10-10): 49 quotes came back, one
    whole family (SOL) died on `SSL: UNEXPECTED_EOF_WHILE_READING` and two ETH quotes
    went missing with it. Retrying is what keeps a flaky proxy from looking like a
    liquidity fact.
    """
    url = f"https://www.okx.com{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=UA)
    last: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with op.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - retried below, re-raised at the end
            last = exc
            if attempt < attempts:
                time.sleep(2.0 * attempt)
    raise last if last else RuntimeError("no attempt made")


def dte(inst_id: str, today: datetime.date) -> int:
    """Days to expiry from the instId: BTC-USD-261030-68000-C -> 26/10/30."""
    parts = inst_id.split("-")
    stamp = parts[2] if len(parts) > 2 else ""
    try:
        expiry = datetime.date(2000 + int(stamp[:2]), int(stamp[2:4]), int(stamp[4:6]))
    except (ValueError, IndexError):
        return -999
    return (expiry - today).days


def pick_expiries(inst_ids: list[str], today: datetime.date, count: int,
                  min_dte: int, max_dte: int) -> list[tuple[str, int]]:
    """`count` expiries SPREAD across the curve, not the front three.

    Three consecutive weeklies are three draws from the same liquidity regime; the
    1.1 question is whether the width is a property of the demo book or of the front
    expiry, so the sample has to move along the curve (10d ... 60d by default).
    """
    by_stamp: dict[str, int] = {}
    for inst_id in inst_ids:
        days = dte(inst_id, today)
        if days < min_dte or days > max_dte:
            continue
        by_stamp.setdefault(inst_id.split("-")[2], days)
    ordered = sorted(by_stamp.items(), key=lambda kv: kv[1])
    if count >= len(ordered) or count < 2:
        return ordered[: max(count, 0)]
    step = (len(ordered) - 1) / (count - 1)
    picked = sorted({int(round(i * step)) for i in range(count)})
    return [ordered[i] for i in picked]


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def summarise(rows: list[dict]) -> dict:
    """Counts and width stats over rows that have a mid. One-sided quotes are counted.

    A one-sided quote is not a cheap quote: it cannot be bought. Keeping it out of
    the median and in its own counter is the difference between measuring liquidity
    and measuring optimism.
    """
    usable = [r for r in rows if r.get("mid")]
    spreads = [r["spread_pct"] for r in usable]
    halves = [r["half_spread_pct"] for r in usable]
    by_uly: dict[str, dict] = {}
    for uly in sorted({r["uly"] for r in rows}):
        sub = [r for r in usable if r["uly"] == uly]
        by_uly[uly] = {
            "quotes": len([r for r in rows if r["uly"] == uly]),
            "two_sided": len(sub),
            "median_spread_pct": round(statistics.median([r["spread_pct"] for r in sub]), 3) if sub else None,
            "p90_spread_pct": round(quantile([r["spread_pct"] for r in sub], 0.9) or 0.0, 3) if sub else None,
        }
    return {
        "quotes": len(rows),
        "two_sided": len(usable),
        "one_sided": len(rows) - len(usable),
        "median_spread_pct": round(statistics.median(spreads), 3) if spreads else None,
        "p90_spread_pct": round(quantile(spreads, 0.9) or 0.0, 3) if spreads else None,
        "median_half_spread_pct": round(statistics.median(halves), 3) if halves else None,
        "by_uly": by_uly,
    }


def forward_for(op, uly: str) -> float:
    """The forward the strikes should be measured against (falls back to index)."""
    data = (get_json(op, "/api/v5/public/opt-summary", {"uly": uly}).get("data") or [])
    for row in data:
        for key in ("fwdPx", "idxPx", "ulyPx"):
            if row.get(key):
                return float(row[key])
    return 0.0


def survey(op, uly: str, today: datetime.date, expiries: int, strikes: int,
           min_dte: int, max_dte: int) -> list[dict]:
    instruments = (get_json(op, "/api/v5/public/instruments",
                            {"instType": "OPTION", "uly": uly}).get("data") or [])
    live = [r for r in instruments if str(r.get("state")) == "live"]
    if not live:
        print(f"{uly}: no live option instruments")
        return []
    forward = forward_for(op, uly)
    chosen = pick_expiries([r["instId"] for r in live], today, expiries, min_dte, max_dte)
    if not forward:
        print(f"{uly}: no forward price available; skipping")
        return []
    if not chosen:
        have = sorted({dte(r["instId"], today) for r in live})
        print(f"{uly}: nothing in the {min_dte}-{max_dte}d window "
              f"(chain covers {have[0]}d..{have[-1]}d, {len(live)} live)")
        return []
    print(f"{uly}: {len(live)} live, forward {forward:g}, "
          f"expiries " + ", ".join(f"{stamp}({days}d)" for stamp, days in chosen))

    rows: list[dict] = []
    for stamp, days in chosen:
        same = [r for r in live if r["instId"].split("-")[2] == stamp]
        available = sorted({float(r["stk"]) for r in same})
        if not available:
            continue
        picked: list[float] = []
        for offset in STRIKE_OFFSETS[: max(1, strikes)]:
            target = forward * (1 + offset)
            nearest = min(available, key=lambda s: abs(s - target))
            if nearest not in picked:
                picked.append(nearest)
        for strike in picked:
            for side in ("C", "P"):
                matches = [r for r in same if abs(float(r["stk"]) - strike) < 1e-9
                           and r["instId"].rsplit("-", 1)[-1] == side]
                if not matches:
                    continue
                inst_id = matches[0]["instId"]
                try:
                    book = (get_json(op, "/api/v5/market/books",
                                     {"instId": inst_id, "sz": "5"}).get("data") or [{}])[0]
                except Exception as exc:  # noqa: BLE001 - one dead quote must not kill the survey
                    print(f"  {inst_id}: book failed {type(exc).__name__}: {exc}")
                    continue
                bids = book.get("bids") or []
                asks = book.get("asks") or []
                bid = float(bids[0][0]) if bids else None
                ask = float(asks[0][0]) if asks else None
                row = {
                    "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
                    "uly": uly,
                    "expiry": stamp,
                    "dte": days,
                    "instId": inst_id,
                    "strike": strike,
                    "offset_pct": round((strike / forward - 1) * 100, 2),
                    "side": side,
                    "bid": bid,
                    "ask": ask,
                    "bid_sz": float(bids[0][1]) if bids else None,
                    "ask_sz": float(asks[0][1]) if asks else None,
                }
                if bid and ask and ask > bid:
                    mid = (bid + ask) / 2
                    row["mid"] = mid
                    row["spread_pct"] = (ask - bid) / mid * 100
                    row["half_spread_pct"] = (ask - mid) / mid * 100
                    row["spread_coin"] = ask - bid
                rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlyings", default="BTC-USD,ETH-USD,SOL-USD")
    ap.add_argument("--expiries", type=int, default=3, help="how many expiries per underlying")
    ap.add_argument("--strikes", type=int, default=5, help="strike offsets x calls/puts")
    ap.add_argument("--min-dte", type=int, default=10)
    ap.add_argument("--max-dte", type=int, default=60)
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    ap.add_argument("--out", default="data/options/book_survey.json")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    op = opener(args.proxy or None)
    today = datetime.datetime.now(datetime.UTC).date()
    rows: list[dict] = []
    for uly in [u.strip() for u in args.underlyings.split(",") if u.strip()]:
        try:
            rows.extend(survey(op, uly, today, args.expiries, args.strikes, args.min_dte, args.max_dte))
        except Exception as exc:  # noqa: BLE001 - one family must not kill the rest
            print(f"{uly}: FAILED {type(exc).__name__}: {exc}")

    if not rows:
        print("no quotes collected")
        return 1

    print(f"\n{'instId':32s}{'dte':>5s}{'off%':>7s}{'bid':>10s}{'ask':>10s}"
          f"{'mid':>10s}{'spread%':>9s}{'half%':>8s}")
    for row in rows:
        if not row.get("mid"):
            print(f"{row['instId']:32s}{row['dte']:>5d}{row['offset_pct']:>7.1f}"
                  f"{str(row['bid']):>10s}{str(row['ask']):>10s}{'-':>10s}{'one-sided':>9s}")
            continue
        print(f"{row['instId']:32s}{row['dte']:>5d}{row['offset_pct']:>7.1f}"
              f"{row['bid']:>10.4f}{row['ask']:>10.4f}{row['mid']:>10.4f}"
              f"{row['spread_pct']:>9.2f}{row['half_spread_pct']:>8.2f}")

    stats = summarise(rows)
    print(f"\nquotes {stats['quotes']} | two-sided {stats['two_sided']} | one-sided {stats['one_sided']}")
    print(f"spread/mid: median {stats['median_spread_pct']}%  p90 {stats['p90_spread_pct']}%"
          f" | entry-only half-spread median {stats['median_half_spread_pct']}%")
    for uly, s in stats["by_uly"].items():
        print(f"  {uly:9s} quotes {s['quotes']:>3d} two-sided {s['two_sided']:>3d} "
              f"median {s['median_spread_pct']}% p90 {s['p90_spread_pct']}%")

    payload = {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "convention": "spread_pct=(ask-bid)/mid*100; half_spread_pct=(ask-mid)/mid*100",
        "params": vars(args),
        "stats": stats,
        "rows": rows,
    }
    if args.write:
        path = Path(args.out)
        if not path.is_absolute():
            path = PROJECT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {path}")
    else:
        print("DRY RUN - re-run with --write to store the sample")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
