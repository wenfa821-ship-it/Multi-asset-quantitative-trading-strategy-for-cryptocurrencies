"""Market regime detection: the same signal means different things in different tapes.

A breakout in a quiet uptrend and a breakout in a violent downtrend are not the
same trade. This module classifies the tape so strategies can stand down, and so
sizing can shrink, instead of quietly taking worse odds.
"""

from __future__ import annotations

from dataclasses import dataclass

from .indicators import atr, atr_series, closes, ema, percentile_rank
from .models import Bar
from .util import safe_div


@dataclass(slots=True)
class MarketRegime:
    label: str  # trend_up | trend_down | range
    atr_pct_value: float
    vol_percentile: float
    extreme_vol: bool
    quiet: bool
    ema_fast: float | None = None
    ema_slow: float | None = None

    @property
    def trend_up(self) -> bool:
        return self.label == "trend_up"

    @property
    def trend_down(self) -> bool:
        return self.label == "trend_down"

    @property
    def ranging(self) -> bool:
        return self.label == "range"

    def describe(self) -> str:
        return (
            f"{self.label} atr%={self.atr_pct_value:.2f} volpct={self.vol_percentile:.0f}"
            f"{' extreme' if self.extreme_vol else ''}"
        )


def detect(
    bars: list[Bar],
    *,
    atr_period: int = 14,
    ema_fast_period: int = 20,
    ema_slow_period: int = 100,
    vol_lookback: int = 120,
    extreme_percentile: float = 92.0,
    quiet_percentile: float = 55.0,
) -> MarketRegime:
    """Classify the tape from the bars of one symbol/timeframe."""
    series = closes(bars)
    fast = ema(series, ema_fast_period)
    slow = ema(series, ema_slow_period)
    atr_value = atr(bars, atr_period) or 0.0
    price = series[-1] if series else 0.0
    atr_pct_value = 100.0 * safe_div(atr_value, price, 0.0)

    history = atr_series(bars[-vol_lookback:], atr_period) if bars else []
    history_pct = [
        100.0 * safe_div(value, price, 0.0) for value in history
    ]
    vol_pct = percentile_rank(history_pct, atr_pct_value) if history_pct else 50.0
    vol_pct = 50.0 if vol_pct is None else vol_pct

    if fast is None or slow is None:
        label = "range"
    elif fast > slow and price > slow:
        label = "trend_up"
    elif fast < slow and price < slow:
        label = "trend_down"
    else:
        label = "range"

    return MarketRegime(
        label=label,
        atr_pct_value=atr_pct_value,
        vol_percentile=vol_pct,
        extreme_vol=vol_pct >= extreme_percentile,
        quiet=vol_pct <= quiet_percentile,
        ema_fast=fast,
        ema_slow=slow,
    )

