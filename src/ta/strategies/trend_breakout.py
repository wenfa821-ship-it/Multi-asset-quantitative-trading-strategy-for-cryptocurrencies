"""Donchian breakout with higher-timeframe agreement, volume and cost filters.

Structural notes (vs. the original "enter on the breakout bar" version):
- No hard 2R target. Bank half at 1R, move the stop to breakeven, then let the
  ATR trailing stop carry the runner. A 2R cap was reached in <8% of trades and
  only truncated the winners that did work.
- Retest confirmation. After a breakout registers, the strategy waits for price
  to pull back to the breakout level and hold, then enters with a stop below the
  pullback low. This filters false breakouts (the main source of stop-outs) and
  tightens risk per trade, which lifts R-multiples on the winners that do work.
  Set ``retest_window_bars = 0`` to restore the original "buy the breakout bar"
  behavior.
"""

from __future__ import annotations

from ..indicators import atr, closes, donchian_high, donchian_low, ema, volume_ratio
from ..models import Position, Signal
from ..util import fmt_usd, tf_to_ms
from .base import MarketView, Strategy


class TrendBreakout(Strategy):
    name = "trend_breakout"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # retest confirmation state: symbol -> {"level": float, "registered_ts": int}
        self._pending: dict[str, dict] = {}
        # walk-forward reset detection: symbol -> last seen bar ts. When the
        # replay cursor jumps backward (a new in-sample/OOS/stress run starts),
        # any armed retest is stale and must be cleared so it cannot leak.
        self._last_ts: dict[str, int] = {}

    @property
    def entry_lookback(self) -> int:
        return int(self.param("entry_lookback", 55))

    @property
    def exit_lookback(self) -> int:
        return int(self.param("exit_lookback", 20))

    @property
    def ema_fast(self) -> int:
        return int(self.param("ema_fast", 20))

    @property
    def ema_slow(self) -> int:
        return int(self.param("ema_slow", 100))

    @property
    def atr_period(self) -> int:
        return int(self.param("atr_period", 14))

    @property
    def volume_period(self) -> int:
        return int(self.param("volume_period", 20))

    @property
    def warmup(self) -> int:
        return max(self.entry_lookback, self.ema_slow, self.atr_period, self.volume_period) + 5

    def on_bar(self, view: MarketView, position: Position | None) -> Signal | None:
        bars = view.bars(self.timeframe)
        if not self.ready(bars):
            return None

        symbol = view.symbol
        close = bars[-1].close
        current_ts = bars[-1].ts
        atr_value = atr(bars, self.atr_period)
        if not atr_value:
            return None

        # walk-forward reset: a fresh run replays bars from the start, so the
        # cursor moves backward. Clear stale retest state so it cannot leak
        # across in-sample / out-of-sample / stress runs (the same strategy
        # instance is reused for all three).
        if current_ts < self._last_ts.get(symbol, 0):
            self._pending.pop(symbol, None)
        self._last_ts[symbol] = current_ts

        # ---- manage an open position ---------------------------------------
        if position and position.qty > 0:
            # we are already in a trade: a previously armed retest is now stale
            self._pending.pop(symbol, None)
            lower = donchian_low(bars, self.exit_lookback)
            if lower is not None and close < lower:
                return Signal(
                    symbol=symbol,
                    side="sell",
                    reason=f"breakout exit close<{fmt_usd(lower)}",
                    exit=True,
                    tag=self.name,
                )
            return None

        # ---- higher timeframe agreement -------------------------------------
        confirm_tf = self.confirm_timeframe
        if confirm_tf:
            confirm = view.bars(confirm_tf)
            if len(confirm) < self.confirm_warmup:
                return None
            confirm_ema = ema(closes(confirm), int(self.param("confirm_ema", 50)))
            if confirm_ema is None or confirm[-1].close < confirm_ema:
                return None  # never buy a breakout while the higher timeframe is below trend

        # ---- volatility / regime gate ---------------------------------------
        if bool(self.param("regime_filter", True)):
            regime = view.regime(self.timeframe)
            if regime.extreme_vol or regime.trend_down:
                return None

        # ---- breakout + confirmation ----------------------------------------
        upper = donchian_high(bars, self.entry_lookback)
        series = closes(bars)
        fast = ema(series, self.ema_fast)
        slow = ema(series, self.ema_slow)
        ema_ok = fast is not None and slow is not None and fast > slow

        ratio = volume_ratio(bars, self.volume_period)
        min_vol = float(self.param("min_volume_multiple", 1.1))
        volume_ok = ratio is None or ratio >= min_vol

        retest_window = int(self.param("retest_window_bars", 0))

        # ---- retest confirmation mode ---------------------------------------
        # Instead of buying the breakout bar, register the level and wait for
        # price to pull back and hold. This filters false breakouts (the main
        # source of stop-outs) and lets us enter with a tighter stop below the
        # retest low, which raises R-multiples on the winners that do work.
        if retest_window > 0:
            return self._retest_entry(
                symbol=symbol,
                bars=bars,
                close=close,
                current_ts=current_ts,
                atr_value=atr_value,
                upper=upper,
                ema_ok=ema_ok,
                volume_ok=volume_ok,
                ratio=ratio,
                retest_window=retest_window,
            )

        # ---- original path: enter on the breakout bar -----------------------
        if upper is None or close <= upper:
            return None
        if not ema_ok or not volume_ok:
            return None
        return self._breakout_signal(symbol, close, atr_value, ratio, upper)

    # ------------------------------------------------------------------
    # entry helpers
    # ------------------------------------------------------------------
    def _breakout_signal(
        self,
        symbol: str,
        close: float,
        atr_value: float,
        ratio: float | None,
        upper: float | None,
    ) -> Signal | None:
        """Original path: enter on the breakout bar with a wide ATR stop."""
        stop = close - atr_value * float(self.param("stop_atr_multiple", 2.5))
        risk = close - stop
        rr = float(self.param("take_profit_r_multiple", 2.0))
        take_profit = close + risk * rr if rr > 0 else None
        if not self.edge_is_worth_it(close, stop, take_profit):
            return None
        return Signal(
            symbol=symbol,
            side="buy",
            reason=(
                f"breakout close>{fmt_usd(upper)} volx{ratio if ratio is None else round(ratio, 2)}"
                + (f" {self.confirm_timeframe} ok" if self.confirm_timeframe else "")
            ),
            stop_price=stop,
            take_profit=take_profit,
            partial_target=close + risk,
            partial_fraction=float(self.param("partial_fraction", 0.5)),
            breakeven_at_r=float(self.param("breakeven_at_r", 1.0)),
            tag=self.name,
        )

    def _retest_entry(
        self,
        *,
        symbol: str,
        bars: list,
        close: float,
        current_ts: int,
        atr_value: float,
        upper: float | None,
        ema_ok: bool,
        volume_ok: bool,
        ratio: float | None,
        retest_window: int,
    ) -> Signal | None:
        """Wait for a pullback to the breakout level, then enter with a tight stop."""
        pending = self._pending.get(symbol)

        # 1. evaluate an already-armed retest
        if pending:
            level = float(pending["level"])
            reg_ts = int(pending["registered_ts"])
            tf_ms = tf_to_ms(self.timeframe)
            bars_since = (current_ts - reg_ts) // tf_ms

            tolerance_pct = float(self.param("retest_tolerance_pct", 0.5)) / 100.0
            failed_threshold = 1.0 - tolerance_pct * 2.0  # clearly back below the level

            # window expired or breakout failed: disarm and fall through to re-arm
            if bars_since > retest_window or close < level * failed_threshold:
                self._pending.pop(symbol, None)
                pending = None
            elif (
                ema_ok  # trend still up at the retest bar
                and len(bars) >= 2
                and bars_since > 0  # at least one bar must have closed after the breakout
                and abs(close - level) / level <= tolerance_pct  # price is back at the level
                and close > bars[-2].close  # bullish reclaim candle
                and close > level * (1.0 - tolerance_pct)  # still holding the breakout
            ):
                # retest confirmed: enter with a stop below the pullback low
                lookback = min(len(bars) - 1, int(bars_since) + 1)
                retest_low = min(b.low for b in bars[-lookback:])
                stop_buffer_atr = float(self.param("retest_stop_atr_multiple", 0.5))
                stop = retest_low - atr_value * stop_buffer_atr
                # cap the stop distance so a wide pullback doesn't blow risk up
                max_stop_pct = float(self.param("max_stop_pct", 0.0))
                if max_stop_pct > 0:
                    max_stop_distance = close * max_stop_pct / 100.0
                    stop = max(stop, close - max_stop_distance)
                risk = close - stop
                if risk <= 0:
                    self._pending.pop(symbol, None)
                    return None
                rr = float(self.param("take_profit_r_multiple", 2.0))
                take_profit = close + risk * rr if rr > 0 else None
                if not self.edge_is_worth_it(close, stop, take_profit):
                    self._pending.pop(symbol, None)
                    return None
                self._pending.pop(symbol, None)
                return Signal(
                    symbol=symbol,
                    side="buy",
                    reason=(
                        f"retest volx{ratio if ratio is None else round(ratio, 2)}"
                        f" bars_since={bars_since}"
                        + (f" {self.confirm_timeframe} ok" if self.confirm_timeframe else "")
                    ),
                    stop_price=stop,
                    take_profit=take_profit,
                    partial_target=close + risk,
                    partial_fraction=float(self.param("partial_fraction", 0.5)),
                    breakeven_at_r=float(self.param("breakeven_at_r", 1.0)),
                    tag=self.name,
                )

        # 2. arm a new retest if a valid breakout just printed
        if upper is not None and close > upper and ema_ok and volume_ok:
            self._pending[symbol] = {
                "level": upper,
                "registered_ts": current_ts,
            }
        return None
