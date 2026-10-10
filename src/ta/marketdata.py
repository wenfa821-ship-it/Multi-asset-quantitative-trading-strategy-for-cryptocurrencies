"""Market data feeds.

LiveFeed     - pulls closed candles from the venue, never repaints (drops the
               still-forming bar) and marks to the venue ticker. Serves several
               timeframes per symbol so a strategy can confirm on a higher one.
SyntheticFeed- deterministic random-walk market so the whole pipeline can be
               exercised offline, with regime switching so strategies actually trade.
               Generated at the fastest timeframe and aggregated upward, so every
               timeframe is guaranteed consistent with the others.
"""

from __future__ import annotations

import random

from .models import Bar
from .regime import MarketRegime, detect
from .resilience import VenueDegraded
from .util import now_ms, tf_to_ms
from .venues.base import Venue


def aggregate(bars: list[Bar], base_tf: str, target_tf: str) -> list[Bar]:
    """Roll base bars up to target_tf.

    Only *complete* buckets are emitted: the bucket containing the newest base
    bar is dropped, because using it would mean a strategy sees a partly formed
    higher-timeframe candle (a classic look-ahead bug).
    """
    base_ms = tf_to_ms(base_tf)
    target_ms = tf_to_ms(target_tf)
    if target_ms <= base_ms or target_ms % base_ms != 0 or not bars:
        return [Bar(b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars]

    out: list[Bar] = []
    bucket: int | None = None
    open_ = high = low = close = volume = 0.0
    start_ts = 0

    def flush() -> None:
        if bucket is None:
            return
        out.append(Bar(start_ts, open_, high, low, close, volume))

    for bar in bars:
        current = bar.ts // target_ms
        if bucket is None:
            bucket = current
            start_ts = bar.ts
            open_, high, low, close, volume = bar.open, bar.high, bar.low, bar.close, bar.volume
            continue
        if current != bucket:
            flush()
            bucket = current
            start_ts = bar.ts
            open_, high, low, close, volume = bar.open, bar.high, bar.low, bar.close, bar.volume
            continue
        high = max(high, bar.high)
        low = min(low, bar.low)
        close = bar.close
        volume += bar.volume
    # deliberately do NOT flush the final bucket: it may still be forming
    return out


def closed_counts(base_bars: list[Bar], agg_bars: list[Bar], base_tf: str, target_tf: str) -> list[int]:
    """For each base index i, how many aggregated bars are fully closed by then.

    O(n) once, so a backtest can slice an aggregated series in O(1) per step
    instead of re-aggregating the prefix every bar.
    """
    base_ms = tf_to_ms(base_tf)
    target_ms = tf_to_ms(target_tf)
    counts: list[int] = []
    closed = 0
    for bar in base_bars:
        while closed < len(agg_bars) and agg_bars[closed].ts + target_ms <= bar.ts + base_ms:
            closed += 1
        counts.append(closed)
    return counts


class LiveFeed:
    simulated = False

    def __init__(
        self,
        venue: Venue,
        timeframe: str,
        bar_limit: int,
        drop_unclosed: bool = True,
        timeframes: list[str] | None = None,
    ) -> None:
        self.venue = venue
        self.timeframe = timeframe  # primary / decision timeframe
        self.timeframes = list(dict.fromkeys([timeframe, *(timeframes or [])]))
        self.bar_limit = bar_limit
        self.drop_unclosed = drop_unclosed
        self._bars: dict[tuple[str, str], list[Bar]] = {}
        self._marks: dict[str, float] = {}
        self._regime_cache: dict[tuple, MarketRegime] = {}
        self._funding: dict[str, float | None] = {}
        self._funding_mean: dict[tuple, float | None] = {}
        # Symbols whose bars could not be fetched on the last refresh. They are
        # skipped rather than retried forever, and the loop still runs for every
        # other symbol (see refresh()).
        self.last_failed_symbols: list[str] = []
        # Where funding is READ from. For a carry pair the strategy sees the SPOT
        # symbol (`WLFI/USDT`) but the funding lives on the PERP, i.e. on the hedge
        # venue - and a spot venue answers `fetch_funding_rate` with None, which
        # makes the strategy fail closed and never trade. Measured 2026-10-08:
        # with 48 qualifying candidates and the pool live, positions stayed at 0
        # purely because of this. Set by the runner when a hedge venue exists.
        self.funding_venue = None
        # How many symbols IN A ROW may fail to fetch before the whole pass is
        # declared degraded. One bad symbol is normal and must not stop the tick;
        # every symbol failing means the venue is unreachable and continuing only
        # burns the pass (see refresh()).
        self.consecutive_failure_limit: int = 5

    def funding_mean(self, symbol: str, samples: int = 90) -> float | None:
        """Mean settled funding over the last `samples` prints, or None.

        This is the number carry must decide on (the screener ranks by the 30-day
        mean for exactly this reason). Read once per symbol and cached, because a
        history call per symbol per tick would be 37 extra requests every cycle.
        """
        cache_key = (symbol, samples)
        if cache_key in self._funding_mean:
            return self._funding_mean[cache_key]
        source = self.funding_venue or self.venue
        reader = getattr(source, "fetch_funding_history", None)
        value: float | None = None
        if callable(reader):
            try:
                base, _, quote = symbol.partition("/")
                target = f"{base}/{quote}:{quote}" if self.funding_venue else symbol
                rates = reader(target, samples) or []
                if rates:
                    value = sum(rates) / len(rates)
            except Exception:  # noqa: BLE001 - a venue hiccup must not stop the loop
                value = None
        self._funding_mean[cache_key] = value
        return value

    def funding_rate(self, symbol: str) -> float | None:
        """Settled perp funding for `symbol`, or None.

        Carry needs this (README 37): without a funding source the strategy is
        fail-closed, so enabling it against a feed that cannot answer would look
        wired while doing nothing. Returns None for anything the venue cannot
        price - spot symbols included - rather than a default of 0.0, because 0.0
        reads as "funding is exactly neutral", which is a statement, not a gap.
        """
        if symbol in self._funding:
            return self._funding[symbol]
        source = self.funding_venue or self.venue
        reader = getattr(source, "fetch_funding_rate", None)
        value: float | None = None
        if callable(reader):
            try:
                # Spot symbol -> its settlement-suffixed perp (`SOL/USDT` ->
                # `SOL/USDT:USDT`), the same mapping execution.paired uses.
                base, _, quote = symbol.partition("/")
                target = f"{base}/{quote}:{quote}" if self.funding_venue else symbol
                value = reader(target)
            except Exception:  # noqa: BLE001 - a venue hiccup must not stop the loop
                value = None
        self._funding[symbol] = value
        return value

    def prime(self, symbols: list[str]) -> None:
        failed: list[str] = []
        consecutive = 0
        for symbol in symbols:
            symbol_ok = True
            for timeframe in self.timeframes:
                try:
                    self._fetch(symbol, timeframe)
                except Exception:  # noqa: BLE001
                    # Same isolation as refresh(): startup must not be held
                    # hostage either. Measured 2026-10-08 - with the guard only in
                    # refresh(), the runner never got past priming because the
                    # first symbol raised, so the process looked alive with no
                    # metrics and no ticks at all.
                    failed.append(symbol)
                    symbol_ok = False
                    break
            if symbol_ok:
                consecutive = 0
                continue
            consecutive += 1
            if consecutive >= self.consecutive_failure_limit:
                # Stop early, but do NOT raise: prime() runs inside
                # TradingRunner.build(), and an exception there exits the process,
                # which launchd then relaunches every ~10s - a crash loop is worse
                # than a degraded start (measured 2026-10-10: 142 startup banners
                # and a growing .err.log while the proxy's upstream was dead).
                # Returning leaves a partially primed feed: the loop starts, every
                # tick fails fast in refresh(), and the venue repairs itself on the
                # first call that succeeds.
                self.last_failed_symbols = sorted(set(failed))
                return
        self.last_failed_symbols = sorted(set(failed))

    def _fetch(self, symbol: str, timeframe: str) -> None:
        bars = self.venue.fetch_bars(symbol, timeframe, self.bar_limit)
        if not bars:
            return
        if self.drop_unclosed:
            bars = bars[:-1]
        self._bars[(symbol, timeframe)] = bars

    def refresh(self, symbols: list[str], force: bool = False) -> None:
        now = now_ms()
        failed: list[str] = []
        consecutive = 0
        for symbol in symbols:
            symbol_ok = True
            for timeframe in self.timeframes:
                bars = self._bars.get((symbol, timeframe)) or []
                last_ts = bars[-1].ts if bars else 0
                if force or now >= last_ts + tf_to_ms(timeframe):
                    try:
                        self._fetch(symbol, timeframe)
                    except Exception:  # noqa: BLE001
                        # ONE bad symbol must not take the tick down with it.
                        # Measured 2026-10-08: A/USDT failed at the head of the
                        # list every tick, which aborted the whole refresh, so no
                        # other symbol was fetched, the venue's *global*
                        # consecutive-failure counter climbed to 42, and the loop
                        # suspended ALL new entries - one symbol holding 61
                        # hostage. Skip it, keep the rest, and report the count.
                        failed.append(symbol)
                        symbol_ok = False
                        break
            if symbol_ok:
                consecutive = 0
                continue
            # ...but when EVERY symbol is failing, it is not one bad symbol, it is
            # the venue (or the network in front of it). Carrying on costs the whole
            # pass: measured 2026-10-10, the local proxy's upstream died and one
            # refresh ground through 64 symbols x 4 timeframes x 3 retries, so the
            # runner completed no tick for 42 minutes while every market-data
            # number on the dashboard still looked calm. Failing the PASS after a
            # few consecutive symbol failures puts the loop back on its ~1 minute
            # cadence, which also means it notices the recovery in ~1 minute
            # instead of ~40. A scattered failure (the 2026-10-08 case) never
            # reaches this limit because one success resets the counter.
            consecutive += 1
            if consecutive >= self.consecutive_failure_limit:
                self.last_failed_symbols = sorted(set(failed))
                raise VenueDegraded(
                    f"{consecutive} consecutive symbols failed to fetch bars - "
                    f"treating the whole pass as degraded"
                )
        try:
            self._marks.update(self.venue.refresh_marks(symbols))
        except Exception:  # noqa: BLE001 - marks are an optimisation, not a gate
            pass
        if failed:
            self.last_failed_symbols = sorted(set(failed))

    def bars(self, symbol: str, timeframe: str | None = None) -> list[Bar]:
        return self._bars.get((symbol, timeframe or self.timeframe), [])

    def last_bar_ts(self, symbol: str, timeframe: str | None = None) -> int:
        bars = self.bars(symbol, timeframe)
        return bars[-1].ts if bars else 0

    def truncated(self, symbol: str, timeframe: str | None = None) -> bool:
        """True when the venue returned far less history than we asked for.

        That is the practical signature of a young listing: there simply is no
        more history to return.
        """
        bars = self.bars(symbol, timeframe)
        return len(bars) < int(self.bar_limit * 0.9)

    def regime(self, symbol: str, timeframe: str | None = None, **kwargs) -> MarketRegime:
        """Regime detection memoised per closed bar - this is the hot path."""
        bars = self.bars(symbol, timeframe)
        key = ("live", symbol, timeframe or self.timeframe, bars[-1].ts if bars else 0)
        cached = self._regime_cache.get(key)
        if cached is not None and not kwargs:
            return cached
        value = detect(bars, **kwargs)
        self._regime_cache = {key: value}
        return value

    def mark(self, symbol: str) -> float:
        mark = self._marks.get(symbol)
        if mark:
            return mark
        bars = self.bars(symbol)
        return bars[-1].close if bars else 0.0

    def advance(self, steps: int = 1) -> None:  # pragma: no cover - live markets advance themselves
        return None


class SyntheticFeed:
    """Deterministic offline market. `advance()` moves every symbol one bar forward."""

    simulated = True

    def __init__(
        self,
        symbols: list[str],
        timeframe: str = "1h",
        warmup_bars: int = 200,
        total_bars: int = 1200,
        seed: int = 7,
        start_prices: dict[str, float] | None = None,
        base_vol: float = 0.012,
        drift: float = 0.00025,
        timeframes: list[str] | None = None,
        start_ms: int | None = None,
    ) -> None:
        defaults = {"BTC/USDT": 60000.0, "ETH/USDT": 3000.0, "SOL/USDT": 150.0}
        self.timeframe = timeframe
        self.timeframes = list(dict.fromkeys([timeframe, *(timeframes or [])]))
        self.warmup_bars = warmup_bars
        self.total_bars = max(total_bars, warmup_bars + 10)
        self.cursor = warmup_bars
        self._series: dict[str, list[Bar]] = {}
        self._agg: dict[tuple[str, str], list[Bar]] = {}
        self._counts: dict[tuple[str, str], list[int]] = {}
        self._regime_cache: dict[tuple, MarketRegime] = {}
        tf_ms = tf_to_ms(timeframe)
        # Normalise volatility to an HOURLY basis, then scale by this bar's
        # duration. A 15m bar must not carry the same sigma as a 1h bar, or the
        # synthetic market becomes absurdly volatile and no strategy ever clears
        # its own cost/stop filters.
        hours = tf_ms / 3_600_000.0
        base_vol *= hours ** 0.5
        drift *= hours
        # Align the newest synthesised bar with the caller's clock start, so the
        # staleness guard sees fresh data instead of a bar `total-warmup` behind.
        start_ts = (start_ms if start_ms is not None else now_ms()) - self.warmup_bars * tf_ms
        for index, symbol in enumerate(symbols):
            price = (start_prices or {}).get(symbol) or defaults.get(symbol, 100.0)
            self._series[symbol] = self._generate(
                bars=self.total_bars,
                seed=seed + index * 977,
                start_price=price,
                start_ts=start_ts,
                tf_ms=tf_ms,
                base_vol=base_vol,
                drift=drift,
            )
            for other in self.timeframes:
                if other == timeframe:
                    continue
                rolled = aggregate(self._series[symbol], timeframe, other)
                self._agg[(symbol, other)] = rolled
                self._counts[(symbol, other)] = closed_counts(
                    self._series[symbol], rolled, timeframe, other
                )

    @staticmethod
    def _generate(
        bars: int,
        seed: int,
        start_price: float,
        start_ts: int,
        tf_ms: int,
        base_vol: float,
        drift: float,
    ) -> list[Bar]:
        rng = random.Random(seed)
        price = start_price
        out: list[Bar] = []
        regime = 1
        for i in range(bars):
            if i % 90 == 0:
                regime = rng.choice([1, 1, 0, -1, -1])
            sigma = base_vol * rng.uniform(0.6, 1.9)
            ret = rng.gauss(drift * 8.4 * regime, sigma)
            open_price = price
            close = max(0.01, price * (1.0 + ret))
            wick = abs(rng.gauss(0.0, sigma * 0.45))
            high = max(open_price, close) * (1.0 + wick)
            low = min(open_price, close) * (1.0 - wick)
            # Volume must correlate with the move: in real markets a large bar
            # carries above-average volume, and breakouts are large bars.
            # Generating volume as independent noise made every volume-gated
            # strategy untestable offline (and made the synthetic market lie).
            relative_move = abs(ret) / sigma if sigma > 0 else 0.0
            volume = abs(rng.gauss(1_000.0, 250.0)) * (0.6 + 0.7 * min(relative_move, 3.0))
            out.append(
                Bar(
                    ts=start_ts + i * tf_ms,
                    open=round(open_price, 10),
                    high=round(high, 10),
                    low=round(low, 10),
                    close=round(close, 10),
                    volume=round(volume, 4),
                )
            )
            price = close
        return out

    def prime(self, symbols: list[str]) -> None:
        return None

    def refresh(self, symbols: list[str], force: bool = False) -> None:
        return None

    def advance(self, steps: int = 1) -> None:
        self.cursor = min(self.cursor + steps, self.total_bars - 1)

    def bars(self, symbol: str, timeframe: str | None = None) -> list[Bar]:
        tf = timeframe or self.timeframe
        if tf == self.timeframe:
            return self._series.get(symbol, [])[: self.cursor + 1]
        series = self._series.get(symbol) or []
        counts = self._counts.get((symbol, tf))
        if not series or counts is None:
            return []
        index = min(self.cursor, len(counts) - 1)
        return self._agg[(symbol, tf)][: counts[index]]

    def last_bar_ts(self, symbol: str, timeframe: str | None = None) -> int:
        bars = self.bars(symbol, timeframe)
        return bars[-1].ts if bars else 0

    def truncated(self, symbol: str, timeframe: str | None = None) -> bool:
        return False

    def regime(self, symbol: str, timeframe: str | None = None, **kwargs) -> MarketRegime:
        bars = self.bars(symbol, timeframe)
        key = ("synth", symbol, timeframe or self.timeframe, bars[-1].ts if bars else 0)
        cached = self._regime_cache.get(key)
        if cached is not None and not kwargs:
            return cached
        value = detect(bars, **kwargs)
        self._regime_cache = {key: value}
        return value

    def full(self, symbol: str, timeframe: str | None = None) -> list[Bar]:
        """The complete series, ignoring the cursor - used to drive backtests."""
        tf = timeframe or self.timeframe
        if tf == self.timeframe:
            return list(self._series.get(symbol, []))
        return list(self._agg.get((symbol, tf), []))

    def mark(self, symbol: str) -> float:
        series = self._series.get(symbol) or []
        if not series:
            return 0.0
        return series[min(self.cursor, len(series) - 1)].close

    @property
    def remaining(self) -> int:
        return self.total_bars - 1 - self.cursor
