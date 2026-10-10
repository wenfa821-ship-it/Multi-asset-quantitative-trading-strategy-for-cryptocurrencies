"""Buy the pullback inside an established uptrend - the higher-frequency workhorse.

It fires far more often than a 55-bar breakout because it waits for a temporary
dip instead of a new extreme, while still requiring higher-timeframe agreement.

Structural notes (vs. the version that failed OOS with a 25.6% win rate):
- Require a genuine trend, not a weakly-above-EMA chop. The old gate only
  checked ``fast(21) > slow(55)`` and ``close > slow`` plus 1h ``close > ema50``.
  In a sideways/flat tape that condition still holds, RSI dips to ~42, and a
  reclaim candle fires - but there is no trend to resume, so the trade sits for
  the holding window and bleeds out via time stops. The new gate additionally
  requires the regime detector to call the 15m tape ``trend_up`` (EMA20>EMA100
  and price>EMA100) and the 1h confirming EMA to be *rising* over the last few
  bars, so a flat/higher-timeframe trend no longer admits counter-trend noise.
- Volume on the reclaim bar 1.3x (was 0.8x). 0.8 admits any random candle;
  a real pullback-resumption has demand behind it. Same lesson as trend_breakout.
- RSI must be turning up vs. the prior bar, so the pullback has made a local
  pivot instead of still accelerating down (no falling knives).
- Reclaim candle must close in the upper half of its range, not merely above
  the prior close - filters weak/doji reclaim bars that immediately fail.
- No hard 2R target (unchanged): bank half at 1R, move stop to breakeven, then
  let the ATR trailing stop carry the runner. A fixed cap was reached in ~0% of
  15m trades and only truncated winners.
"""

from __future__ import annotations

from ..indicators import atr, closes, ema, rsi, volume_ratio
from ..models import Position, Signal
from .base import MarketView, Strategy


class TrendPullback(Strategy):
    name = "trend_pullback"

    @property
    def ema_fast(self) -> int:
        return int(self.param("ema_fast", 21))

    @property
    def ema_slow(self) -> int:
        return int(self.param("ema_slow", 55))

    @property
    def rsi_period(self) -> int:
        return int(self.param("rsi_period", 14))

    @property
    def atr_period(self) -> int:
        return int(self.param("atr_period", 14))

    @property
    def swing_lookback(self) -> int:
        return int(self.param("swing_lookback", 10))

    @property
    def warmup(self) -> int:
        return max(self.ema_slow, self.rsi_period, self.atr_period, self.swing_lookback) + 5

    def on_bar(self, view: MarketView, position: Position | None) -> Signal | None:
        bars = view.bars(self.timeframe)
        if not self.ready(bars):
            return None
        series = closes(bars)
        close = bars[-1].close
        fast = ema(series, self.ema_fast)
        slow = ema(series, self.ema_slow)
        if fast is None or slow is None:
            return None

        if position and position.qty > 0:
            held = self.position_bars_held(view, position)
            max_holding = int(self.param("max_holding_bars", 0))
            if close < fast:
                return Signal(
                    symbol=view.symbol, side="sell", reason=f"pullback exit close<{fast:.4f}",
                    exit=True, tag=self.name,
                )
            if max_holding and held is not None and held >= max_holding:
                return Signal(
                    symbol=view.symbol, side="sell", reason=f"time stop {held} bars",
                    exit=True, tag=self.name,
                )
            return None

        # ---- trend agreement, including the higher timeframe -----------------
        if not (fast > slow and close > slow):
            return None
        confirm_tf = self.confirm_timeframe
        if confirm_tf:
            confirm = view.bars(confirm_tf)
            if len(confirm) < self.confirm_warmup:
                return None
            confirm_closes = closes(confirm)
            confirm_period = int(self.param("confirm_ema", 50))
            confirm_ema = ema(confirm_closes, confirm_period)
            if confirm_ema is None or confirm[-1].close < confirm_ema:
                return None  # never buy a pullback while the higher timeframe is below trend
            # the higher timeframe must be *advancing*, not just above a flat EMA.
            # A flat/declining EMA with price above it is a weak trend that does
            # not resume after a pullback - the main source of the old 25.6% win.
            slope_lookback = int(self.param("confirm_slope_lookback", 5))
            if slope_lookback > 0:
                prev_confirm = ema(confirm_closes[:-slope_lookback], confirm_period)
                if prev_confirm is None or confirm_ema <= prev_confirm:
                    return None

        regime = view.regime(self.timeframe)
        if bool(self.param("regime_filter", True)):
            if regime.extreme_vol:
                return None
            # a pullback only works inside a genuine uptrend. In a range a
            # "pullback" is counter-trend mean reversion against you, which is
            # exactly what the old gate admitted. Require the broader 20/100
            # tape to be labelled trend_up, not merely "not extreme".
            if bool(self.param("require_trend_up", True)) and not regime.trend_up:
                return None

        # ---- the pullback itself --------------------------------------------
        level = rsi(series, self.rsi_period)
        if level is None or level > float(self.param("rsi_entry_max", 42.0)):
            return None  # not actually pulled back
        # the pullback must have made a local pivot and started to turn back up,
        # otherwise we are catching a falling knife that keeps falling.
        if bool(self.param("require_rsi_up", True)):
            prev_level = rsi(series[:-1], self.rsi_period)
            if prev_level is None or level <= prev_level:
                return None
        low_n = min(bar.low for bar in bars[-self.swing_lookback:])
        tolerance = float(self.param("swing_tolerance_pct", 0.3)) / 100.0
        if close < low_n * (1.0 - tolerance):
            return None  # still making new lows: falling knife, not a pullback
        # reclaim candle: close above the prior close, and a genuine bullish bar
        # that closes in the upper half of its range (filters weak/doji reclaims
        # that barely closed up and immediately fail).
        if close <= bars[-2].close:
            return None
        if bool(self.param("require_strong_reclaim", True)):
            last_bar = bars[-1]
            if last_bar.high > last_bar.low and close <= (last_bar.high + last_bar.low) / 2.0:
                return None
        ratio = volume_ratio(bars, int(self.param("volume_period", 20)))
        if ratio is not None and ratio < float(self.param("min_volume_multiple", 1.3)):
            return None

        atr_value = atr(bars, self.atr_period)
        if not atr_value:
            return None
        atr_stop = close - atr_value * float(self.param("stop_atr_multiple", 1.8))
        swing_stop = low_n * (1.0 - float(self.param("stop_buffer_pct", 0.25)) / 100.0)
        stop = max(atr_stop, swing_stop) if bool(self.param("stop_at_swing", True)) else atr_stop
        stop = min(stop, close * 0.999)  # never a zero-width stop
        if (close - stop) / close * 100.0 > float(self.param("max_stop_pct", 4.0)):
            return None  # risk too wide for a fast entry
        risk = close - stop
        # rr <= 0 means "no hard target": bank half at 1R, then let the trailing
        # stop decide. On 15m a fixed 1.6R target is rarely reached inside the
        # holding window, so the trail does the work instead.
        rr = float(self.param("take_profit_r_multiple", 1.6))
        take_profit = close + risk * rr if rr > 0 else None
        if not self.edge_is_worth_it(close, stop, take_profit):
            return None

        return Signal(
            symbol=view.symbol,
            side="buy",
            reason=f"pullback rsi={level:.0f} swing={low_n:.6f} {regime.label}",
            stop_price=stop,
            take_profit=take_profit,
            partial_target=close + risk,
            partial_fraction=float(self.param("partial_fraction", 0.5)),
            breakeven_at_r=float(self.param("breakeven_at_r", 1.0)),
            max_holding_bars=int(self.param("max_holding_bars", 64)) or None,
            tag=self.name,
        )
