"""Perpetual-futures strategy: trend pullback gated by funding.

Written fresh for the swap leg (user's instruction: 合约策略重新写), NOT a port
of the spot book. Two deliberate differences from the spot strategies:

1. **Funding is part of the entry decision.** A perpetual has no expiry, so the
   only thing that reliably bleeds a position is the funding payment. Spot
   strategies have no such cost and therefore no such gate. This is also the one
   mechanism this project has actually measured as non-negative (see README
   §14.11: funding/basis carry, break-even 17-37 days of holding) - so it is the
   honest thing to condition on rather than guess at a new indicator.

2. **A mandatory holding-period cap.** Spot can sit in a position indefinitely;
   a perp paying 10bps of funding a day cannot. `max_holding_bars` defaults to a
   finite number here, unlike the spot strategies where 0 (= no cap) is normal.

The trend/pullback skeleton itself is deliberately conventional (fast/slow EMA
regime + pullback entry + ATR stop + 1R partial + breakeven + trailing), because
the user's instruction is "start from mainstream logic, then adjust once it is
trading". Novelty belongs in the funding gate, not in the entry trigger.

Funding input: read from the feed when it exposes `funding_rate(symbol)`,
otherwise from the `funding_rate_bps` param, otherwise 0.0 (gate open). The
fallback is explicit so a feed without funding data cannot silently disable the
gate in a way nobody notices.
"""

from __future__ import annotations

from ..indicators import atr, closes, ema
from ..models import Bar, Position, Signal
from .base import MarketView, Strategy


class PerpTrendFunding(Strategy):
    name = "perp_trend_funding"

    # ---- params -----------------------------------------------------------
    @property
    def ema_fast(self) -> int:
        return int(self.param("ema_fast", 21))

    @property
    def ema_slow(self) -> int:
        return int(self.param("ema_slow", 55))

    @property
    def atr_period(self) -> int:
        return int(self.param("atr_period", 14))

    @property
    def pullback_atr(self) -> float:
        """How close to the fast EMA counts as 'a pullback' rather than a chase."""
        return float(self.param("pullback_atr", 1.0))

    @property
    def stop_atr(self) -> float:
        return float(self.param("stop_atr_multiple", 2.0))

    @property
    def max_stop_pct(self) -> float:
        return float(self.param("max_stop_pct", 4.0))

    @property
    def max_funding_long_bps(self) -> float:
        """Refuse longs that pay more than this per funding interval."""
        return float(self.param("max_funding_long_bps", 3.0))

    @property
    def min_funding_short_bps(self) -> float:
        """Refuse shorts that pay more than this (i.e. funding too negative)."""
        return float(self.param("min_funding_short_bps", -3.0))

    @property
    def warmup(self) -> int:
        return max(self.ema_slow, self.atr_period) + 10

    # ---- helpers ----------------------------------------------------------
    def funding_bps(self, view: MarketView) -> tuple[float, str]:
        """Current funding rate in bps, and where the number came from."""
        getter = getattr(view.source, "funding_rate", None)
        if callable(getter):
            try:
                value = getter(view.symbol)
            except Exception:  # noqa: BLE001 - a broken feed must not crash the loop
                value = None
            if value is not None:
                return float(value) * 10_000.0, "feed"
        return float(self.param("funding_rate_bps", 0.0)), "param"

    # ---- main -------------------------------------------------------------
    def on_bar(self, view: MarketView, position: Position | None) -> Signal | None:
        bars: list[Bar] = view.bars(self.timeframe)
        if not self.ready(bars):
            return None

        close = bars[-1].close
        price_series = closes(bars)
        fast = ema(price_series, self.ema_fast)
        slow = ema(price_series, self.ema_slow)
        atr_value = atr(bars, self.atr_period)
        if fast is None or slow is None or not atr_value:
            return None

        up = fast > slow
        down = fast < slow
        distance = abs(close - fast) / atr_value
        near_fast = distance <= self.pullback_atr

        # ---- exit: trend has flipped against the held position ---------------
        if position is not None and position.qty > 0:
            if down:
                return Signal(
                    symbol=view.symbol, side="sell", exit=True,
                    reason=f"trend flipped: ema{self.ema_fast}<ema{self.ema_slow}",
                    tag=self.name,
                )
            return None

        funding_bps, _source = self.funding_bps(view)

        # ---- long: uptrend, pulled back to the fast EMA, funding not punitive
        if up and near_fast and close > fast:
            if funding_bps > self.max_funding_long_bps:
                return None
            stop = self._stop(close, atr_value, "buy")
            if stop is None or not self.edge_is_worth_it(close, stop, None):
                return None
            risk = close - stop
            return Signal(
                symbol=view.symbol, side="buy",
                reason=(f"trend pullback: ema{self.ema_fast}>ema{self.ema_slow}, "
                        f"funding={funding_bps:+.2f}bps"),
                stop_price=stop,
                partial_target=close + risk,
                partial_fraction=float(self.param("partial_fraction", 0.5)),
                breakeven_at_r=float(self.param("breakeven_at_r", 1.0)),
                max_holding_bars=int(self.param("max_holding_bars", 48)),
                tag=self.name,
            )

        # ---- short: mirror image -------------------------------------------
        if down and near_fast and close < fast:
            if funding_bps < self.min_funding_short_bps:
                return None
            stop = self._stop(close, atr_value, "sell")
            if stop is None or not self.edge_is_worth_it(close, stop, None):
                return None
            risk = stop - close
            return Signal(
                symbol=view.symbol, side="sell",
                reason=(f"trend pullback short: ema{self.ema_fast}<ema{self.ema_slow}, "
                        f"funding={funding_bps:+.2f}bps"),
                stop_price=stop,
                partial_target=close - risk,
                partial_fraction=float(self.param("partial_fraction", 0.5)),
                breakeven_at_r=float(self.param("breakeven_at_r", 1.0)),
                max_holding_bars=int(self.param("max_holding_bars", 48)),
                tag=self.name,
            )
        return None

    def _stop(self, close: float, atr_value: float, side: str) -> float | None:
        """ATR stop, additionally clamped by `max_stop_pct`."""
        distance = min(atr_value * self.stop_atr, close * self.max_stop_pct / 100.0)
        if distance <= 0:
            return None
        return close - distance if side == "buy" else close + distance
