"""New-listing momentum: smaller size, tighter stop, hard time stop.

Only touches symbols declared in the `new` tier of the universe (or flagged as
young because the venue returned less history than requested). Everything here
is intentionally tighter than the other strategies, and the matching risk tier
enforces an even smaller position cap.
"""

from __future__ import annotations

from ..indicators import atr, closes, donchian_high, ema, rsi, volume_ratio
from ..models import Position, Signal
from .base import MarketView, Strategy


class NewListingBreakout(Strategy):
    name = "new_listing_breakout"

    @property
    def entry_lookback(self) -> int:
        return int(self.param("entry_lookback", 20))

    @property
    def exit_lookback(self) -> int:
        return int(self.param("exit_lookback", 10))

    @property
    def atr_period(self) -> int:
        return int(self.param("atr_period", 14))

    @property
    def ema_period(self) -> int:
        return int(self.param("ema_period", 20))

    @property
    def warmup(self) -> int:
        return max(self.entry_lookback, self.atr_period, self.ema_period, 14) + 5

    def _is_new(self, view: MarketView) -> bool:
        if view.symbol in set(self.param("new_symbols", []) or []):
            return True
        if bool(self.param("trust_venue_history", True)):
            truncated = getattr(view.source, "truncated", None)
            if callable(truncated) and truncated(view.symbol, self.timeframe):
                return True
        return False

    def on_bar(self, view: MarketView, position: Position | None) -> Signal | None:
        bars = view.bars(self.timeframe)
        if not self.ready(bars) or not self._is_new(view):
            return None
        close = bars[-1].close
        atr_value = atr(bars, self.atr_period)
        if not atr_value:
            return None

        if position and position.qty > 0:
            held = self.position_bars_held(view, position)
            max_holding = int(self.param("max_holding_bars", 96))
            lower = min(bar.low for bar in bars[-self.exit_lookback:])
            if max_holding and held is not None and held >= max_holding:
                return Signal(
                    symbol=view.symbol, side="sell", reason=f"time stop {held} bars",
                    exit=True, tag=self.name,
                )
            if close < lower:
                return Signal(
                    symbol=view.symbol, side="sell", reason="new-listing trail exit",
                    exit=True, tag=self.name,
                )
            return None

        upper = donchian_high(bars, self.entry_lookback)
        if upper is None or close <= upper:
            return None
        series = closes(bars)
        trend = ema(series, self.ema_period)
        if trend is None or close < trend:
            return None
        level = rsi(series, int(self.param("rsi_period", 14)))
        if level is not None and level > float(self.param("rsi_max", 88.0)):
            return None  # already vertical: no edge left
        ratio = volume_ratio(bars, int(self.param("volume_period", 20)))
        if ratio is not None and ratio < float(self.param("min_volume_multiple", 1.5)):
            return None
        if view.regime(self.timeframe).extreme_vol:
            return None

        stop = close - atr_value * float(self.param("stop_atr_multiple", 1.2))
        stop = max(stop, close * (1.0 - float(self.param("max_stop_pct", 4.0)) / 100.0))
        risk = close - stop
        if risk <= 0:
            return None
        rr = float(self.param("take_profit_r_multiple", 0.0))
        take_profit = close + risk * rr if rr > 0 else None
        if not self.edge_is_worth_it(close, stop, take_profit):
            return None

        return Signal(
            symbol=view.symbol,
            side="buy",
            reason=f"new listing breakout volx{ratio if ratio is None else round(ratio, 2)}",
            stop_price=stop,
            take_profit=take_profit,
            partial_target=close + risk,
            partial_fraction=float(self.param("partial_fraction", 0.6)),
            breakeven_at_r=float(self.param("breakeven_at_r", 1.0)),
            max_holding_bars=int(self.param("max_holding_bars", 64)),
            tag=self.name,
        )

