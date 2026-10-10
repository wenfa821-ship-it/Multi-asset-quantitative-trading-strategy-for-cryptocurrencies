"""Random-entry control: does any of the four factor sets carry information?

Why this exists
---------------
Ten rounds of "improve the edge/cost filter" produced a very specific shape:
all four spot strategies improved monotonically as the filter tightened, then
**converged to slightly below zero** (-0.08% is the best case, at 33 trades).
That shape has two possible causes and the existing data cannot tell them apart:

    (a) the entry factors carry information, just not enough to pay the cost;
    (b) the entry factors carry **no** directional information, so tightening the
        filter merely removes the trades that were clearly worse than a coin
        flip, leaving something that nets to ~0 after costs.

If (b) is true, every further "factor optimisation" is wasted effort - the same
conclusion this project already reached twice (frequency, retest confirmation).
The only way to distinguish (a) from (b) is a **control**: same exits, same
frequency, entries decided by a seeded coin.

This strategy reuses the exact exit machinery (ATR stop, partial, breakeven,
optional time cap) and replaces only the entry trigger. Compare its
out-of-sample result with the real strategies:

    random ~= real   -> the factors carry no information (case b)
    random << real   -> the factors do carry information (case a)

Seeded and deterministic: the same seed always produces the same run, so a
comparison is reproducible rather than luck.
"""

from __future__ import annotations

import random

from ..indicators import atr
from ..models import Position, Signal
from .base import MarketView, Strategy


class RandomControl(Strategy):
    name = "random_control"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rng = random.Random(int(self.param("seed", 20261007)))

    @property
    def atr_period(self) -> int:
        return int(self.param("atr_period", 14))

    @property
    def entry_prob(self) -> float:
        """Per-bar entry probability. Tune so trade counts match the real book."""
        return float(self.param("entry_prob", 0.02))

    @property
    def long_bias(self) -> float:
        """Share of entries that are long. 0.5 = symmetric coin."""
        return float(self.param("long_bias", 0.5))

    @property
    def warmup(self) -> int:
        return self.atr_period + 5

    def on_bar(self, view: MarketView, position: Position | None) -> Signal | None:
        bars = view.bars(self.timeframe)
        if not self.ready(bars):
            return None
        if position is not None and position.qty > 0:
            return None                       # exits are the engine's/stop's job

        atr_value = atr(bars, self.atr_period)
        if not atr_value:
            return None

        if self._rng.random() >= self.entry_prob:
            return None

        close = bars[-1].close
        long_side = self._rng.random() < self.long_bias
        distance = min(atr_value * float(self.param("stop_atr_multiple", 2.0)),
                       close * float(self.param("max_stop_pct", 3.0)) / 100.0)
        if distance <= 0:
            return None
        stop = close - distance if long_side else close + distance
        risk = abs(close - stop)
        return Signal(
            symbol=view.symbol,
            side="buy" if long_side else "sell",
            reason="random control entry (seeded coin)",
            stop_price=stop,
            partial_target=close + risk if long_side else close - risk,
            partial_fraction=float(self.param("partial_fraction", 0.5)),
            breakeven_at_r=float(self.param("breakeven_at_r", 1.0)),
            max_holding_bars=int(self.param("max_holding_bars", 0)),
            tag=self.name,
        )
