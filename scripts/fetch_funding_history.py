#!/usr/bin/env python3
"""Backfill settled funding-rate history for every USDT perpetual.

Why this exists
---------------
Carry is the one return source in this project that is not price direction, and
its whole economics is the MEAN funding rate over weeks (README 41). Until now
only 13 of the 147 perps had a history file, so any full-sample carry evaluation
was impossible - the same "looks complete, is not" shape as the 4h/1h gap.

OKX settles funding three times a day and the public endpoint pages 100 prints at
a time, so a ~100-day history is 3 requests per symbol (~7 minutes for the whole
perp universe). Idempotent: rows already on disk are merged by timestamp, never
duplicated, and existing files are extended rather than replaced.

    python3 scripts/fetch_funding_history.py            # report only
    python3 scripts/fetch_funding_history.py --yes      # write
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.history import safe_symbol  # noqa: E402

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/125 Safari/537.36"
}


def _opener(proxy: str | None):
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def fetch_all(opener, inst_id: str, pages: int = 4, pause: float = 0.1) -> list[dict]:
    """Newest-first pages walked backwards, returned oldest-first.

    `after` is OKX's cursor to OLDER prints, which is the direction a backfill
    needs; `before` walks toward now and would re-fetch what we already have.
    """
    rows: dict[int, float] = {}
    cursor: str | None = None
    for _ in range(pages):
        params = {"instId": inst_id, "limit": "100"}
        if cursor:
            params["after"] = cursor
        url = "https://www.okx.com/api/v5/public/funding-rate-history?" + urllib.parse.urlencode(params)
        try:
            with opener.open(urllib.request.Request(url, headers=UA), timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        page = payload.get("data") or []
        if not page:
            break
        for row in page:
            ts, rate = row.get("fundingTime"), row.get("fundingRate")
            if ts and rate not in (None, ""):
                rows[int(ts)] = float(rate)
        cursor = str(page[-1].get("fundingTime") or "")
        if not cursor or len(page) < 100:
            break
        if pause:
            time.sleep(pause)
    return [{"ts": ts, "rate": rate} for ts, rate in sorted(rows.items())]


def merge(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """Union by timestamp - a re-run must not double a symbol's history."""
    merged = {int(r["ts"]): float(r["rate"]) for r in existing}
    for row in fresh:
        merged[int(row["ts"])] = float(row["rate"])
    return [{"ts": ts, "rate": rate} for ts, rate in sorted(merged.items())]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--inventory", default="data/universe/inventory.json")
    ap.add_argument("--out", default="data/history/funding")
    ap.add_argument("--pages", type=int, default=4, help="100 prints per page, 3/day")
    ap.add_argument("--pause", type=float, default=0.1)
    ap.add_argument("--proxy", default="http://127.0.0.1:7897")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    inventory = json.loads((PROJECT / args.inventory).read_text(encoding="utf-8"))
    swap_ids = (inventory.get("classes") or {}).get("SWAP", {}).get("ids_usdt") or []
    out_dir = PROJECT / args.out
    opener = _opener(args.proxy)

    print(f"perps to cover: {len(swap_ids)} | pages/symbol: {args.pages}")
    written = skipped = failed = 0
    for inst_id in swap_ids:
        symbol = inst_id[: -len("-USDT-SWAP")] + "/USDT:USDT"
        path = out_dir / f"{safe_symbol(symbol)}.jsonl"
        existing: list[dict] = []
        if path.exists():
            existing = [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
        try:
            fresh = fetch_all(opener, inst_id, pages=args.pages, pause=args.pause)
        except Exception as exc:  # noqa: BLE001 - one symbol must not stop the sweep
            # The local proxy drops connections under sustained load: a measured
            # single pass over 155 symbols lost 100 of them to
            # SSL UNEXPECTED_EOF_WHILE_READING. Retry before declaring a symbol
            # empty, or the backfill silently covers two thirds of the universe -
            # the same shape as every other gap this project has found.
            fresh = []
            last = exc
            for attempt in range(2, 4):
                time.sleep(1.5 * attempt)
                try:
                    fresh = fetch_all(opener, inst_id, pages=args.pages, pause=args.pause)
                    break
                except Exception as retry_exc:  # noqa: BLE001
                    last = retry_exc
            if not fresh:
                print(f"  {inst_id:<22} FAILED {type(last).__name__}: {str(last)[:44]}")
                failed += 1
                continue
        if not fresh:
            skipped += 1
            continue
        merged = merge(existing, fresh)
        if len(merged) == len(existing):
            skipped += 1
            continue
        if args.yes:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n",
                encoding="utf-8",
            )
        written += 1
        print(f"  {inst_id:<22} {len(existing):>5} -> {len(merged):>5} rows")

    print(f"\nupdated {written}, unchanged {skipped}, failed {failed}")
    if not args.yes and written:
        print("DRY RUN - re-run with --yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
