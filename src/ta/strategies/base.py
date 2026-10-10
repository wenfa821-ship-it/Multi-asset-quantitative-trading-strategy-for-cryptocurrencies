"""Strategy interface. A strategy only ever returns proposals (Signals).

A strategy sees a MarketView, which gives it bars for any timeframe the engine
is subscribed to. That is how a 15m entry can require 1h/4h agreement without
the strategy owning any data plumbing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..models import Bar, Position, Signal
from ..regime import MarketRegime, detect


@dataclass(slots=True)
class MarketView:
    """Everything a strategy may look at, for one symbol, at one point in time."""

    symbol: str
    mark: float
    source: Any  # feed / backtest view: bars(symbol, timeframe) -> list[Bar]

    def bars(self, timeframe: str | None = None) -> list[Bar]:
        return self.source.bars(self.symbol, timeframe)

    def regime(self, timeframe: str | None = None, **kwargs: Any) -> MarketRegime:
        detector = getattr(self.source, "regime", None)
        if callable(detector):
            return detector(self.symbol, timeframe, **kwargs)
        return detect(self.bars(timeframe), **kwargs)


class Strategy(ABC):
    name: str = "base"

    def __init__(
        self,
        params: dict[str, Any] | None = None,
        symbols: list[str] | None = None,
        timeframe: str = "1h",
    ) -> None:
        self.params: dict[str, Any] = dict(params or {})
        self.symbols: list[str] = list(symbols or [])
        self.timeframe = timeframe

    def param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    @property
    def warmup(self) -> int:
        return 120

    @property
    def confirm_timeframe(self) -> str | None:
        value = self.param("confirm_timeframe")
        return str(value) if value else None

    @property
    def confirm_warmup(self) -> int:
        return int(self.param("confirm_ema", 50)) + 5

    @property
    def long_ema(self) -> int:
        """Longest smoothing period this strategy uses, wherever it is named."""
        return max(
            int(self.param(key, 0) or 0)
            for key in ("ema_slow", "ema_period", "sma_period", "confirm_ema", "zscore_period")
        )

    @property
    def history_window(self) -> int:
        """How much history a strategy may look at, in bars.

        Bounding this is what makes a 180-day backtest finish in seconds rather
        than ~20 minutes per strategy: without it every indicator call and every
        `closes(bars)` rebuild walked the full 17,282-bar series on every bar.

        The cap matches the live feed's `universe.bar_limit` (300 bars per
        timeframe) on purpose - research and production should see the same
        depth, even if that depth is smaller than an EMA would ideally want.
        """
        explicit = int(self.param("history_window", 0) or 0)
        if explicit > 0:
            return explicit
        live_cap = int(self.param("live_bar_limit", 300) or 300)
        needed = max(self.warmup, self.confirm_warmup, self.long_ema * 8) + 60
        return min(needed, live_cap)

    # ---- shared filters ----------------------------------------------------
    @property
    def costs_pct(self) -> float:
        """Round-trip cost estimate in percent (taker fee + slippage, both sides)."""
        fee = float(self.param("fee_bps", 8.0))
        slip = float(self.param("slippage_bps", 5.0))
        return 2.0 * (fee + slip) / 100.0

    def edge_is_worth_it(self, entry: float, stop: float, target: float | None) -> bool:
        """Reject setups whose expected move is barely bigger than the costs.

        This is the single most effective filter when raising frequency: at 15m
        the average move shrinks while fees stay fixed, so more trades is often
        just more fee churn unless the edge is a multiple of the cost.
        """
        if entry <= 0:
            return False
        risk_pct = abs(entry - stop) / entry * 100.0
        if target is None:
            # no fixed target: judge the setup on the stop distance alone
            return risk_pct >= self.costs_pct * float(self.param("min_stop_cost_multiple", 1.5))
        target_pct = abs((target or entry) - entry) / entry * 100.0
        min_target = self.costs_pct * float(self.param("min_edge_cost_multiple", 3.0))
        min_risk = self.costs_pct * float(self.param("min_stop_cost_multiple", 1.5))
        return target_pct >= min_target and risk_pct >= min_risk

    def position_bars_held(self, view: MarketView, position: Position | None) -> int | None:
        if position is None or not position.opened_ts:
            return None
        bars = view.bars(self.timeframe)
        if not bars:
            return None
        from ..util import tf_to_ms

        elapsed = bars[-1].ts - position.opened_ts
        return int(max(0, elapsed) // tf_to_ms(self.timeframe))

    def ready(self, bars: list[Bar]) -> bool:
        return len(bars) >= self.warmup

    @abstractmethod
    def on_bar(
        self, view: MarketView, position: Position | None
    ) -> Signal | None:
        """Return at most one signal per symbol per bar (keeps the OMS simple)."""

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "timeframe": self.timeframe,
            "confirm_timeframe": self.confirm_timeframe,
            "symbols": self.symbols,
            "warmup": self.warmup,
            "params": self.params,
        }
