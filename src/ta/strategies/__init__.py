"""Strategy registry. Add a new strategy by subclassing Strategy and registering it."""

from __future__ import annotations

from ..config import RiskConfig, StrategySpec
from .base import Strategy
from .mean_reversion import MeanReversion
from .new_listing_breakout import NewListingBreakout
from .perp_trend_funding import PerpTrendFunding
from .trend_pullback import TrendPullback
from .trend_breakout import TrendBreakout
from .trend_breakout_daily import TrendBreakoutDaily
from .random_control import RandomControl
from .carry_funding import CarryFunding

REGISTRY: dict[str, type[Strategy]] = {
    TrendBreakout.name: TrendBreakout,
    MeanReversion.name: MeanReversion,
    TrendPullback.name: TrendPullback,
    NewListingBreakout.name: NewListingBreakout,
    PerpTrendFunding.name: PerpTrendFunding,
    TrendBreakoutDaily.name: TrendBreakoutDaily,
    RandomControl.name: RandomControl,
    CarryFunding.name: CarryFunding,
}

__all__ = [
    "Strategy",
    "TrendBreakout",
    "MeanReversion",
    "TrendPullback",
    "NewListingBreakout",
    "PerpTrendFunding",
    "TrendBreakoutDaily",
    "REGISTRY",
    "build_strategy",
    "available",
]


def available() -> list[str]:
    return sorted(REGISTRY)


def build_strategy(
    spec: StrategySpec,
    risk: RiskConfig | None = None,
    symbols: list[str] | None = None,
    *,
    costs: dict[str, float] | None = None,
    new_symbols: list[str] | None = None,
    timeframe: str | None = None,
) -> Strategy:
    """`timeframe` overrides the spec's declared cadence, for research only.

    The live runner never passes it: a strategy's frame is part of what was
    validated. A backtest does, because "does this signal work at a different
    cadence" is a question worth asking, and answering it should not require
    editing config. Measured 2026-10-08: the three 15m strategies cannot be tested
    at their own cadence on the full sample (15m history is 9 days), so they were
    probed at 1h where 150 days exist.
    """
    if spec.name not in REGISTRY:
        raise KeyError(f"unknown strategy {spec.name!r}; available: {available()}")
    params = dict(spec.params)
    if risk is not None:
        params.setdefault("stop_atr_multiple", risk.positions.stop_atr_multiple)
        params.setdefault("take_profit_r_multiple", risk.positions.take_profit_r_multiple)
    for key, value in (costs or {}).items():
        params.setdefault(key, value)
    if new_symbols:
        params.setdefault("new_symbols", list(new_symbols))
    return REGISTRY[spec.name](
        params=params,
        symbols=list(symbols or spec.symbols),
        timeframe=timeframe or spec.timeframe,
    )
