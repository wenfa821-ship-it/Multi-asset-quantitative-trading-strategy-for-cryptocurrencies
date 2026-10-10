"""Local OHLCV store: fetch real candles once, then backtest offline forever.

Two reasons this exists:

  * a backtest on synthetic data says nothing about edge - only real candles do;
  * a venue request that must succeed before every backtest is a liability, so
    candles land on disk and stay there.

Layout:  data/history/<venue>/<SYMBOL>__<timeframe>.jsonl   (one bar per line)
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .models import Bar
from .util import now_ms, tf_to_ms

MAX_BARS_PER_REQUEST = 300  # OKX rejects anything larger in a single call


def safe_symbol(symbol: str) -> str:
    return symbol.replace("/", "-").replace(":", "_")


def history_dir(root: str | Path, venue: str) -> Path:
    return Path(root) / venue


def history_path(root: str | Path, venue: str, symbol: str, timeframe: str) -> Path:
    return history_dir(root, venue) / f"{safe_symbol(symbol)}__{timeframe}.jsonl"


def write_bars(path: str | Path, bars: list[Bar]) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for bar in bars:
            handle.write(json.dumps(asdict(bar)) + "\n")
    return len(bars)


def merge_bars(path: str | Path, bars: list[Bar]) -> int:
    """Union new bars into the series on disk, newest value winning per timestamp.

    Why this exists as a separate call: `write_bars` OVERWRITES. A daily top-up
    that fetched "the last 60 days" would therefore truncate a 900-bar history to
    60, so nobody tops anything up and the series silently freezes instead.
    Measured 2026-10-09: every 1d file in data/history/okx_demo ended at
    2026-10-05 - four days stale - which made the options sleeve's 30-day realized
    vol a frozen constant (identical on the 10-08 and 10-09 rows) while its IV
    side moved, so the IV-RV gate was reading a premium against a stale base.
    """
    target = Path(path)
    merged: dict[int, Bar] = {bar.ts: bar for bar in read_bars(target)}
    for bar in bars:
        merged[bar.ts] = bar
    ordered = [merged[ts] for ts in sorted(merged)]
    write_bars(target, ordered)
    return len(ordered)


def read_bars(path: str | Path) -> list[Bar]:
    target = Path(path)
    if not target.exists():
        return []
    out: list[Bar] = []
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append(
                Bar(
                    ts=int(payload["ts"]),
                    open=float(payload["open"]),
                    high=float(payload["high"]),
                    low=float(payload["low"]),
                    close=float(payload["close"]),
                    volume=float(payload.get("volume") or 0.0),
                )
            )
    return out


def list_history(root: str | Path, venue: str) -> list[dict[str, Any]]:
    directory = history_dir(root, venue)
    if not directory.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        stem = path.stem
        if "__" not in stem:
            continue
        symbol, timeframe = stem.rsplit("__", 1)
        bars = read_bars(path)
        rows.append(
            {
                "symbol": symbol.replace("-", "/"),
                "timeframe": timeframe,
                "bars": len(bars),
                "first_ts": bars[0].ts if bars else 0,
                "last_ts": bars[-1].ts if bars else 0,
                "path": path,
            }
        )
    return rows


def load_frames(
    root: str | Path,
    venue: str,
    symbols: list[str],
    timeframes: list[str],
) -> dict[str, dict[str, list[Bar]]]:
    frames: dict[str, dict[str, list[Bar]]] = {}
    for symbol in symbols:
        per_symbol: dict[str, list[Bar]] = {}
        for timeframe in timeframes:
            bars = read_bars(history_path(root, venue, symbol, timeframe))
            if bars:
                per_symbol[timeframe] = bars
        if per_symbol:
            frames[symbol] = per_symbol
    return frames


def fetch_symbol(
    exchange: Any,
    symbol: str,
    timeframe: str,
    bars: int,
    *,
    pause: float = 0.05,
    logger: Any = None,
    progress: Callable[[int], None] | None = None,
) -> list[Bar]:
    """Page through public candles until we have `bars` closed ones.

    Pagination uses ccxt's `since` path. Measured against OKX on 2026-09-29:

      * `since=` walks the full history (17,282 x 15m bars = 180 days verified);
      * the native `after=` cursor stops at ~1,440 bars (OKX's depth cap per
        call), so it cannot build a multi-regime sample.

    Both were measured, not assumed: an earlier attempt switched to `after=`
    and silently truncated every symbol to 15 days.
    """
    tf_ms = tf_to_ms(timeframe)
    collected: dict[int, Bar] = {}
    since = now_ms() - (bars + 2) * tf_ms
    guard = 0

    # A `since` older than the symbol's first candle returns NOTHING (not the
    # oldest available candle). Bisect the start toward now until the venue
    # answers, so young listings still yield the history they do have.
    for _ in range(8):
        chunk = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=MAX_BARS_PER_REQUEST)
        if chunk:
            break
        now = now_ms()
        if since >= now - tf_ms:
            return []
        since = since + max(tf_ms, (now - since) // 2)
        if pause:
            time.sleep(pause)

    while len(collected) < bars and guard < 400:
        guard += 1
        chunk = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=MAX_BARS_PER_REQUEST)
        if not chunk:
            break
        added = 0
        for row in chunk:
            ts = int(row[0])
            if ts not in collected:
                added += 1
            collected[ts] = Bar(
                ts=ts,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5] or 0.0),
            )
        newest = max(collected)
        if len(collected) >= bars:
            break
        advance = newest + tf_ms
        if advance <= since or added == 0:
            break
        since = advance
        if len(chunk) < MAX_BARS_PER_REQUEST:
            # the venue has no more history for this symbol (young listing)
            break
        if pause:
            time.sleep(pause)
        if progress:
            progress(len(collected))

    # the newest candle may still be forming: drop it, never backtest on it
    collected.pop((now_ms() // tf_ms) * tf_ms, None)

    # A `since` that predates the symbol's FIRST candle makes OKX answer with the
    # OLDEST window it holds, and the "a short chunk means no more history" guard
    # below then stops the walk immediately. The result is not an error and not a
    # gap - it is a plausible-looking file covering the wrong years. Measured
    # 2026-10-07 while building the daily universe: SUI and PEPE came back as 51
    # bars ending 2023-06-23, TIA as 12 bars ending 2025-07-13, and the daily
    # backtest then found ZERO timestamps common to the universe.
    #
    # So: if the walk did not land near now, throw it away and page BACKWARD from
    # the present instead. Only the young/depth-limited symbols take this path.
    if collected and max(collected) < now_ms() - 3 * tf_ms:
        collected.clear()
        cursor = now_ms()
        for _ in range(80):
            start = cursor - MAX_BARS_PER_REQUEST * tf_ms
            chunk = exchange.fetch_ohlcv(
                symbol, timeframe, since=max(0, start), limit=MAX_BARS_PER_REQUEST
            )
            if not chunk:
                break
            for row in chunk:
                ts = int(row[0])
                collected[ts] = Bar(
                    ts=ts,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5] or 0.0),
                )
            if len(collected) >= bars:
                break
            oldest = min(collected)
            if oldest <= max(0, start):
                break
            cursor = oldest
            if pause:
                time.sleep(pause)
        collected.pop((now_ms() // tf_ms) * tf_ms, None)

    ordered = [collected[key] for key in sorted(collected)]
    return ordered[-bars:] if bars and len(ordered) > bars else ordered
