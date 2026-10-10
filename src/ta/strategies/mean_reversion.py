"""Z-score mean reversion, only when the tape is actually ranging."""

from __future__ import annotations

from ..indicators import atr, closes, ema, zscore
from ..models import Position, Signal
from .base import MarketView, Strategy


class MeanReversion(Strategy):
    name = "mean_reversion"

    @property
    def sma_period(self) -> int:
        return int(self.param("sma_period", 50))

    @property
    def z_entry(self) -> float:
        return float(self.param("z_entry", -2.0))

    @property
    def z_exit(self) -> float:
        return float(self.param("z_exit", 0.0))

    @property
    def atr_period(self) -> int:
        return int(self.param("atr_period", 14))

    @property
    def warmup(self) -> int:
        return max(self.sma_period, self.atr_period) + 5

    def on_bar(self, view: MarketView, position: Position | None) -> Signal | None:
        bars = view.bars(self.timeframe)
        if not self.ready(bars):
            return None
        series = closes(bars)
        z = zscore(series, self.sma_period)
        if z is None:
            return None
        regime = view.regime(self.timeframe)

        if position and position.qty > 0:
            # bank the reversion, and bail early if the tape turns down
            if z >= self.z_exit or (regime.trend_down and bool(self.param("regime_filter", True))):
                return Signal(
                    symbol=view.symbol,
                    side="sell",
                    reason=f"reverted z={z:.2f} ({regime.label})",
                    exit=True,
                    tag=self.name,
                )
            return None

        # ---- regime gate: range, not violent ---------------------------------
        if bool(self.param("regime_filter", True)):
            if regime.trend_down or regime.extreme_vol:
                return None
            if regime.vol_percentile > float(self.param("max_vol_percentile", 75.0)):
                return None

        confirm_tf = self.confirm_timeframe
        if confirm_tf:
            confirm = view.bars(confirm_tf)
            if len(confirm) < self.confirm_warmup:
                return None
            confirm_ema = ema(closes(confirm), int(self.param("confirm_ema", 50)))
            if confirm_ema is None or confirm[-1].close < confirm_ema:
                return None

        if z > self.z_entry:
            return None
        close = bars[-1].close
        atr_value = atr(bars, self.atr_period)
        if not atr_value:
            return None
        stop = close - atr_value * float(self.param("stop_atr_multiple", 2.5))
        risk = close - stop
        # rr <= 0 means "no hard target" - the same convention every other
        # strategy uses. Without this guard a config of 0 makes the target equal
        # the entry price, the cost gate then rejects every single setup, and the
        # strategy silently stops trading altogether.
        rr = float(self.param("take_profit_r_multiple", 1.5))
        take_profit = close + risk * rr if rr > 0 else None
        if not self.edge_is_worth_it(close, stop, take_profit):
            return None
        return Signal(
            symbol=view.symbol,
            side="buy",
            reason=f"dip z={z:.2f} <= {self.z_entry:.2f} volpct={regime.vol_percentile:.0f}",
            stop_price=stop,
            take_profit=take_profit,
            partial_target=close + risk * 0.75,
            partial_fraction=float(self.param("partial_fraction", 0.5)),
            breakeven_at_r=float(self.param("breakeven_at_r", 0.8)),
            max_holding_bars=int(self.param("max_holding_bars", 0)) or None,
            tag=self.name,
        )
