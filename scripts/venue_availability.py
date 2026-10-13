#!/usr/bin/env python3
"""Record which symbols the venue ACTUALLY serves right now, twice a day.

Why this exists
---------------
The TradFi sleeve's 13 symbols were chosen off the DISK (share of daily bars with
a real print >= 80%). On 2026-10-10 that screen was measured against the venue
itself and 12 of the 13 answered with ZERO bars on every timeframe (1d, 1h, 15m)
while keeping 84+ bars on disk - `code:0, data:[]`, the "zombie" mode README 47
records - and only XMU had both bars and a two-sided book. Disk history says what
used to be available; this says what is available now.

It also cannot be settled in one look: tokenised equities may only be served
during US market hours, and the first measurement was taken while the US market
was closed. So the honest instrument is a TIME SERIES - one row per run - which is
why this is wired into the daily job, whose 21:10 CST slot falls inside the US
session and whose 09:10 slot falls outside it.

Credentials come from `.env`, like `sync_funding.py` and `funding_income.py`: the
launchd unit exports the proxy but not the API keys (README 50.4), so without the
loader this step dies with "API key is not set" on every run. It was written
without it on 2026-10-10 and caught by the daily job's retry summary the same day,
before the 21:10 slot it exists for.

    python3 scripts/venue_availability.py --write     # append one row
    python3 scripts/venue_availability.py --symbols XMU/USDT,XTSM/USDT
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config, load_env_file  # noqa: E402
from ta.venues import build_venue  # noqa: E402

TIMEFRAMES = ("1d", "1h", "15m")


def classify(bars: int, bid: float | None, ask: float | None) -> str:
    """One word for what the venue offers for this symbol right now.

    `tradable` needs BOTH data and a two-sided book: a signal the venue cannot
    price is a signal the depth gate will refuse anyway (README 63).
    """
    if bars <= 0 and not bid and not ask:
        return "absent"
    if bars <= 0:
        return "quote-only"
    if bid and ask:
        return "tradable"
    if bid or ask:
        return "one-sided"
    return "bars-only"


def _new_listing_symbols(listings_path: Path, *, cap: int = 20) -> list[str]:
    """`data/new_listings/tradfi_records.jsonl` → 场馆格式的 symbol（XBRKB-USDT → XBRKB/USDT）。

    2026-10-13：这条腿的池子 10-10 收窄到 XMU 之后，「场馆哪天开始供数据」这个问题**只对那 13 个成立** ——
    而 OKX 每天都在上新股票代币（实测近 8 天 8 个成熟、全 cat=3，一个都没被任何地方记录）。
    所以把**最近** N 个新上市也纳入采样：它们才是池子长回来的可能来源。
    取最近 N 个（按上市时刻倒序）而不是全部，避免采样列表无限增长。
    """
    try:
        rows = [json.loads(line) for line in
                listings_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    # **先筛后截**（2026-10-13 测试抓到的）：先取 rows[:cap] 再过滤，会把非 USDT 的行算进配额，
    # 结果拿到的比 cap 少 —— 极端情况下 cap 个全是无效行、一个新标的都进不来。
    usable = [r for r in rows if str(r.get("inst") or "").endswith("-USDT")]
    usable.sort(key=lambda r: -int(r.get("entry_ts") or 0))
    return [str(r["inst"])[:-5] + "/USDT" for r in usable[:cap]]


def watch_symbols(tradfi_tier: list[str], report_path: Path, *,
                  listings_path: Path | None = None, listings_cap: int = 20) -> list[str]:
    """The tier PLUS the symbols the tracker has already ruled out.

    Narrowing the tier also narrowed what this sampler looked at: measured
    2026-10-10, the first run after `tradfi` went from 13 symbols to 1 sampled
    exactly one symbol - which quietly deleted the only evidence that could ever
    bring a symbol back ("the venue serves it again"). The excluded list already
    exists in `data/universe/tradfi_tier.json`; this reads it instead of hardcoding
    names in a plist.
    """
    out = list(tradfi_tier)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 报告缺失只该让「被排除过的那批」缺席，**不该提前返回** ——
        # 否则新上市那一段会被静默跳过（2026-10-13 测试抓到的第二个 early-return 坑）。
        report = {}
    for symbol in (report.get("symbols") or {}):
        if symbol not in out:
            out.append(str(symbol))
    # 新上市的股票代币（见 `_new_listing_symbols` 的说明）
    if listings_path is not None:
        for symbol in _new_listing_symbols(listings_path, cap=listings_cap):
            if symbol not in out:
                out.append(symbol)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--symbols", default="", help="comma separated; default = the tradfi tier")
    ap.add_argument("--tier-report", default="data/universe/tradfi_tier.json",
                    help="adds the symbols that report has ruled out, so a comeback is visible")
    ap.add_argument("--tradfi-listings", default="data/new_listings/tradfi_records.jsonl",
                    help="新上市的股票代币（cat=3）那本账 —— 把最近 N 个也纳入采样，"
                         "否则「场馆哪天开始供数据」只对 10-10 那 13 个成立")
    ap.add_argument("--listings-cap", type=int, default=20,
                    help="新上市最多纳入多少个（按上市时刻倒序）")
    ap.add_argument("--out", default="data/universe/availability.jsonl")
    ap.add_argument("--env-file", default=".env", help="credentials, read like the runner does")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    loaded = load_env_file(PROJECT / args.env_file)
    if loaded:
        print(f"loaded {loaded} value(s) from {args.env_file}")

    config = Config.load(args.config_dir)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        symbols = watch_symbols(
            list(config.universe.symbols_by_tier.get("tradfi") or []),
            PROJECT / args.tier_report,
            # 新上市的股票代币也要采（默认读那本新账；文件不存在就退化成原行为）
            listings_path=PROJECT / args.tradfi_listings,
            listings_cap=args.listings_cap,
        )

    venue = build_venue(config, args.venue)
    venue.connect()
    now = datetime.datetime.now(datetime.UTC)
    # US regular session in UTC: 13:30-20:00 (ignore DST - this is a hint, not a gate).
    # The half hour matters: the daily job's own 21:10 CST slot is 13:10 UTC, i.e.
    # 20 minutes BEFORE the open, and the first version of this line called that
    # "in session" (13 <= hour). That would have labelled the very measurement the
    # TradFi decision rests on as an in-session one when the market had not opened.
    us_session = (now.hour, now.minute) >= (13, 30) and now.hour < 20
    rows = []
    print(f"{'symbol':14s} {'1d':>4s} {'1h':>4s} {'15m':>4s} {'bid':>10s} {'ask':>10s}  state")
    for symbol in symbols:
        counts = {}
        for tf in TIMEFRAMES:
            try:
                counts[tf] = len(venue.fetch_bars(symbol, tf, 300))
            except Exception as exc:  # noqa: BLE001 - "cannot fetch" is a state too
                counts[tf] = -1
                print(f"  {symbol}: {tf} failed ({type(exc).__name__})")
        try:
            bid, ask = venue.top_of_book(symbol)
        except Exception:  # noqa: BLE001
            bid, ask = None, None
        state = classify(counts["1d"] + counts["1h"] + counts["15m"], bid, ask)
        rows.append({
            "symbol": symbol,
            **{f"bars_{tf}": counts[tf] for tf in TIMEFRAMES},
            "bid": bid,
            "ask": ask,
            "state": state,
        })
        print(f"{symbol:14s} {counts['1d']:>4d} {counts['1h']:>4d} {counts['15m']:>4d} "
              f"{str(bid):>10s} {str(ask):>10s}  {state}")
    counts_by_state: dict[str, int] = {}
    for r in rows:
        counts_by_state[r["state"]] = counts_by_state.get(r["state"], 0) + 1
    summary = f"us_session(UTC 13:30-20:00)={us_session} " + " ".join(
        f"{k}={v}" for k, v in sorted(counts_by_state.items())
    )
    print(f"\n{summary}")

    if not args.write:
        print("DRY RUN - re-run with --write")
        venue.close()
        return 0
    target = Path(args.out)
    if not target.is_absolute():
        target = PROJECT / target
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": int(now.timestamp() * 1000),
        "venue": args.venue,
        "us_session": us_session,
        "summary": counts_by_state,
        "rows": rows,
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"appended -> {target}")
    venue.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
