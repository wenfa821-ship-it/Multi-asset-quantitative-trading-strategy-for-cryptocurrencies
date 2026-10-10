"""Perp strategy: the funding gate must actually gate."""

from __future__ import annotations

import pytest

from ta.models import Bar, Position
from ta.strategies.base import MarketView
from ta.strategies.perp_trend_funding import PerpTrendFunding


class FakeSource:
    def __init__(self, bars, funding=None):
        self._bars = bars
        self._funding = funding

    def bars(self, symbol, timeframe=None):
        return self._bars

    def funding_rate(self, symbol):
        return self._funding


def series(closes, step_ts=3_600_000):
    return [
        Bar(ts=i * step_ts, open=c, high=c * 1.004, low=c * 0.996, close=c, volume=1.0)
        for i, c in enumerate(closes)
    ]


def uptrend_then_pullback():
    closes = [100 + i * 0.6 for i in range(120)]      # steady uptrend
    closes += [closes[-1] - 0.25, closes[-1] - 0.05]  # small pullback into EMA
    return series(closes)


def view(bars, funding=None):
    return MarketView(symbol="SOL-USDT-SWAP", mark=bars[-1].close,
                      source=FakeSource(bars, funding))


def test_not_ready_returns_none():
    strat = PerpTrendFunding()
    bars = series([100, 101, 102])
    assert strat.on_bar(view(bars), None) is None


def test_uptrend_pullback_with_cheap_funding_longs():
    # These tests are about the funding gate, not about pullback geometry, so the
    # pullback window is widened: a steady uptrend already keeps close > fast EMA.
    strat = PerpTrendFunding({"pullback_atr": 99.0})
    bars = uptrend_then_pullback()
    sig = strat.on_bar(view(bars, funding=0.00005), None)   # +0.5 bps, cheap
    assert sig is not None and sig.side == "buy"
    assert sig.stop_price < bars[-1].close
    assert sig.max_holding_bars == 48          # perps get a finite cap
    assert sig.tag == strat.name


def test_expensive_funding_blocks_the_long():
    """+8bps funding is 4x the 3bps default threshold - no long."""
    strat = PerpTrendFunding({"pullback_atr": 99.0})
    bars = uptrend_then_pullback()
    assert strat.on_bar(view(bars, funding=0.0008), None) is None


def test_funding_param_is_used_when_the_feed_has_none():
    strat = PerpTrendFunding({"funding_rate_bps": 50.0, "pullback_atr": 99.0})
    bars = uptrend_then_pullback()
    assert strat.on_bar(view(bars, funding=None), None) is None


def test_downtrend_flip_exits_a_long():
    strat = PerpTrendFunding()
    closes = [200 - i * 0.7 for i in range(120)]
    bars = series(closes)
    held = Position(symbol="SOL-USDT-SWAP", qty=1.0, avg_price=closes[0], opened_ts=0)
    sig = strat.on_bar(view(bars, funding=0.0), held)
    assert sig is not None and sig.exit and sig.side == "sell"


def test_down_trend_with_negative_funding_shorts():
    strat = PerpTrendFunding({"pullback_atr": 99.0})
    closes = [200 - i * 0.7 for i in range(120)]
    closes += [closes[-1] + 0.3, closes[-1] + 0.05]
    bars = series(closes)
    sig = strat.on_bar(view(bars, funding=-0.0002), None)   # -2bps, fine to be short
    assert sig is not None and sig.side == "sell"
    assert sig.stop_price > bars[-1].close
