"""Carry: propose the SPOT leg, and let the pair take the hedge.

The trade is "collect funding with a delta-neutral pair" (README 26). This class
owns only the *decision*: given the settled funding rate, is there enough funding
over the intended hold to pay for the round trip? It proposes a `<base>/USDT`
buy - the spot leg - and the runner is expected to recognise the tag and open the
matching perp short through `execution.paired` (README 37). It deliberately does
NOT propose the short itself: a strategy that shorted a perp without the long is
the naked directional bet README 26 explicitly refuses to call carry.

Fail-closed on missing data: `funding_rate` returns None when the feed has no
funding series, and this class then proposes nothing at all. The perp-trend
strategy's fallback ("no funding data => gate stays open") was right for a gate
and is exactly wrong here, where funding IS the edge.
"""

from __future__ import annotations

from ..models import Position, Signal
from .base import MarketView, Strategy


class CarryFunding(Strategy):
    name = "carry_funding"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # symbol -> funding prints already seen. Kept on the instance (which lives
        # for the whole run) so the smoothed figure is available from the first
        # bar instead of needing the venue to expose a mean endpoint.
        self._rate_history: dict[str, list[float]] = {}

    @property
    def settlements_per_day(self) -> float:
        # Measured 2026-10-07: OKX is not uniform - BTC settles every 8h while
        # some younger contracts settle hourly. The runner's screener infers the
        # interval per contract (scripts/carry_live.py); here it is a parameter so
        # the same class can price both.
        return float(self.param("settlements_per_day", 3.0))

    @property
    def round_trip_bps(self) -> float:
        """Measured two-leg round trip (README 27.3), not a fee-table guess."""
        return float(self.param("round_trip_bps", 44.9))

    @property
    def intended_hold_days(self) -> float:
        return float(self.param("intended_hold_days", 30.0))

    @property
    def min_edge_bps(self) -> float:
        """Required margin over the round trip before opening."""
        return float(self.param("min_edge_bps", 20.0))

    @property
    def exit_funding_bps(self) -> float:
        """Leave when the daily funding falls below this (negative = paying)."""
        return float(self.param("exit_funding_bps", -2.0))

    @property
    def warmup(self) -> int:
        return int(self.param("warmup_bars", 3))

    @property
    def lookback_samples(self) -> int:
        """How many past funding prints to average before deciding.

        The screener ranks by the 30-day MEAN (README 41) because a single print
        is a spike, not a rate. The strategy first read the INSTANTANEOUS rate and
        churned: measured 2026-10-08 it opened six pairs, sold three of them eight
        minutes later, re-opened them, and sold them again - 31 fills and $284 of
        fees in one afternoon, against $11 for the whole previous day. Carry only
        pays if it HOLDS, so the decision has to use the same smoothed number the
        screening did. 8h funding x 90 samples ~ 30 days.
        """
        return int(self.param("lookback_samples", 90))

    @property
    def min_hold_hours(self) -> float:
        """Do not exit before this, however the rate wiggles."""
        return float(self.param("min_hold_hours", 24.0))

    def _smoothed_daily_bps(self, symbol: str, rate: float) -> float:
        history = self._rate_history.setdefault(symbol, [])
        history.append(float(rate))
        del history[: max(0, len(history) - self.lookback_samples)]
        mean = sum(history) / len(history)
        return mean * self.settlements_per_day * 10_000.0

    def daily_bps(self, view: MarketView) -> float | None:
        reader = getattr(view.source, "funding_rate", None)
        if not callable(reader):
            return None
        rate = reader(view.symbol)
        if rate is None:
            return None
        # Prefer the venue's real multi-day mean. The local buffer exists only as
        # a fallback (a feed without history, or during tests), and it needs a
        # minimum number of prints before it is allowed to decide - otherwise the
        # first bar after a restart is judged on a single spike, which is exactly
        # the churn this fixes.
        mean_reader = getattr(view.source, "funding_mean", None)
        if callable(mean_reader):
            try:
                venue_mean = mean_reader(view.symbol, self.lookback_samples)
            except Exception:  # noqa: BLE001
                venue_mean = None
            if venue_mean is not None:
                return float(venue_mean) * self.settlements_per_day * 10_000.0
        history = self._rate_history.get(view.symbol) or []
        if len(history) < self.min_samples:
            return None              # not enough evidence yet: propose nothing
        return self._smoothed_daily_bps(view.symbol, float(rate))

    @property
    def min_samples(self) -> int:
        return int(self.param("min_samples", 6))

    def _age_hours(self, view: MarketView, position: Position) -> float:
        """Hours since the position opened, from the BAR clock (deterministic)."""
        opened = float(getattr(position, "opened_ts", 0) or 0)
        bars = view.bars(self.timeframe)
        now = float(bars[-1].ts) if bars else opened
        if not opened:
            return 0.0
        return max(0.0, (now - opened) / 3_600_000.0)

    def on_bar(self, view: MarketView, position: Position | None) -> Signal | None:
        daily = self.daily_bps(view)
        if daily is None:
            return None                      # no funding series -> propose nothing

        holding = position is not None and position.qty > 0
        if not holding:
            edge = daily * self.intended_hold_days - self.round_trip_bps
            if edge < self.min_edge_bps:
                return None
            return Signal(
                symbol=view.symbol,
                side="buy",
                reason=(
                    f"carry: {daily:.1f} bps/day x {self.intended_hold_days:.0f}d "
                    f"= {daily * self.intended_hold_days:.0f} bps vs "
                    f"{self.round_trip_bps:.0f} bps cost"
                ),
                tag="carry",
                max_holding_bars=int(self.param("max_holding_bars", 0) or 0) or None,
                diagnostics={
                    "daily_funding_bps": round(daily, 4),
                    "intended_hold_days": self.intended_hold_days,
                    "round_trip_bps": self.round_trip_bps,
                    "expected_net_bps": round(edge, 4),
                    "min_edge_bps": self.min_edge_bps,
                },
            )

        if daily <= self.exit_funding_bps:
            if self._age_hours(view, position) < self.min_hold_hours:
                return None          # too early to judge: carry needs to hold
            return Signal(
                symbol=view.symbol,
                side="sell",
                reason=f"carry exit: funding turned to {daily:.1f} bps/day",
                exit=True,
                tag="carry",
            )
        return None
