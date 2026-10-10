"""Indicator primitives. Pure functions over lists of bars/closes (stdlib only).

These recompute over the whole window on each call, which is fine for low/mid
frequency. If you move to second-level trading, switch to incremental state.
"""

from __future__ import annotations

import math

from .models import Bar


def sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def ema(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    # An EMA's memory decays geometrically: after 8x the period the seed has
    # ~1e-7 influence. Iterating the whole series instead makes every call O(n)
    # and turns a 180-day backtest into an O(n^2) job (~20 minutes per strategy).
    span = min(len(values), max(period * 8, 60))
    values = values[-span:]
    k = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period
    for v in values[period:]:
        value = v * k + value * (1 - k)
    return value


def stdev(values: list[float], period: int) -> float | None:
    if period < 2 or len(values) < period:
        return None
    window = values[-period:]
    mean = sum(window) / period
    var = sum((v - mean) ** 2 for v in window) / (period - 1)
    return math.sqrt(var)


def zscore(values: list[float], period: int) -> float | None:
    sd = stdev(values, period)
    mean = sma(values, period)
    if sd is None or mean is None or sd == 0:
        return None
    return (values[-1] - mean) / sd


def true_range(prev_close: float, bar: Bar) -> float:
    return max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))


def atr(bars: list[Bar], period: int = 14) -> float | None:
    """Wilder's ATR."""
    if period <= 0 or len(bars) < period + 1:
        return None
    bars = bars[-(period + 1):]  # identical maths, O(period) instead of O(n)
    trs = [true_range(bars[i - 1].close, bars[i]) for i in range(1, len(bars))]
    window = trs[-period:]
    value = sum(window) / period
    for tr in trs[len(trs) - period:][1:]:  # smooth over the same window (skeleton-grade)
        value = (value * (period - 1) + tr) / period
    return value


def atr_pct(bars: list[Bar], period: int = 14) -> float | None:
    value = atr(bars, period)
    if value is None or not bars:
        return None
    price = bars[-1].close
    if price <= 0:
        return None
    return 100.0 * value / price


def donchian_high(bars: list[Bar], lookback: int) -> float | None:
    """Highest high of the *previous* `lookback` bars (excludes the current bar)."""
    if lookback <= 0 or len(bars) < lookback + 1:
        return None
    window = bars[-lookback - 1: -1]
    return max(b.high for b in window)


def donchian_low(bars: list[Bar], lookback: int) -> float | None:
    if lookback <= 0 or len(bars) < lookback + 1:
        return None
    window = bars[-lookback - 1: -1]
    return min(b.low for b in window)


def closes(bars: list[Bar]) -> list[float]:
    return [b.close for b in bars]


def volumes(bars: list[Bar]) -> list[float]:
    return [b.volume for b in bars]


def rsi(values: list[float], period: int = 14) -> float | None:
    """Wilder-style RSI. Returns 0-100, or None when there is not enough history."""
    if period <= 0 or len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    start = len(values) - period
    for index in range(start, len(values)):
        change = values[index] - values[index - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


def percentile_rank(values: list[float], current: float | None = None) -> float | None:
    """Where `current` sits within `values`, 0-100. None if it cannot be judged."""
    if not values:
        return None
    reference = values[-1] if current is None else current
    below = sum(1 for v in values if v <= reference)
    return 100.0 * below / len(values)


def realized_vol(values: list[float], period: int = 30) -> float | None:
    """Standard deviation of simple returns over the window, in percent."""
    if len(values) < period + 1:
        return None
    window = values[-(period + 1):]
    returns = [
        window[i] / window[i - 1] - 1.0
        for i in range(1, len(window))
        if window[i - 1] > 0
    ]
    if len(returns) < 2:
        return None
    deviation = stdev(returns, len(returns))
    return None if deviation is None else 100.0 * deviation


def slope_pct(values: list[float], period: int = 50) -> float | None:
    if len(values) < period or values[-period] <= 0:
        return None
    return 100.0 * (values[-1] / values[-period] - 1.0)


def volume_ratio(bars: list[Bar], period: int = 20) -> float | None:
    """Newest bar's volume divided by the average of the previous `period` bars."""
    if period <= 0 or len(bars) < period + 1:
        return None
    average = sma([bar.volume for bar in bars[-period - 1: -1]], period)
    if not average:
        return None
    return bars[-1].volume / average


def atr_series(bars: list[Bar], period: int = 14) -> list[float]:
    """ATR at each point in history - used to rank current volatility."""
    out: list[float] = []
    for index in range(len(bars)):
        window = bars[: index + 1]
        value = atr(window, period)
        if value is not None:
            out.append(value)
    return out
