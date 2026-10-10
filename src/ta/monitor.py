"""Portfolio-level market monitoring: a crypto translation of a six-dimension
macro dashboard, and the position-size brake that hangs off it.

What this is
------------
`regime.detect` classifies ONE symbol's tape. This module scores the WHOLE
environment and its only output is a multiplier on position size. It never picks
a symbol and never times an entry: it answers "how much room should the book
take right now", which is the "能不能做" layer, not the "做什么" layer.

Why it is built as a brake and not a signal
-------------------------------------------
README section 25 established that our directional entries carry negative
information - a fixed-seed coin flip beat all four factor strategies. Adding a
sixth, seventh and eighth way to guess direction would only add ways to overfit.
A brake makes a weaker claim: it does not need to know where price goes, only
whether the current tape is the kind of tape where losses are more likely. If it
is wrong, the cost is a smaller position, which is recoverable.

Six dimensions, and what each is standing in for
------------------------------------------------
The stock-market original reads GDP/PMI/CPI, 货币政策, 北向资金/两融, PE/PB 分位,
均线系统, 换手率/涨停家数. None of those exist in crypto, so each dimension has to
be *replaced by its closest observable*, not merely renamed:

    trend      均线系统        -> breadth: share of the universe above EMA(50)
    momentum   经济周期位置    -> median 1-day return across the universe
    volatility 风险偏好        -> realized-vol percentile vs its own history
    funding    资金面(利率)    -> mean perp funding: crowded longs pay, and pay
    crowding   情绪/拥挤度     -> median RSI: extremes are where the crowd is
    volume     成交量能        -> median volume vs its own 20-bar average

Funding is the one input that is genuinely available and genuinely forward
looking, and it is optional: a caller with no funding series passes None and that
dimension scores neutral rather than silently passing.

Every threshold below is provisional and untuned. `scripts/monitor_study.py`
measures whether the score actually separates forward returns; until that
measurement says otherwise, treat this as an unproven brake.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .indicators import closes, ema, percentile_rank, realized_vol, rsi, volume_ratio
from .models import Bar

# The ladder the framework describes ("六维向好 -> 仓位80%；六维恶化 -> 20%"),
# expressed as multipliers so the caller keeps its own risk sizing and only
# scales it. Reds are dimensions scoring <= 2; three or more means risk-off.
DIMENSION_MAX = 5


@dataclass(slots=True)
class Dimension:
    name: str
    score: int
    value: float | None
    detail: str

    @property
    def red(self) -> bool:
        return self.score <= 2


@dataclass(slots=True)
class MonitorState:
    dimensions: list[Dimension] = field(default_factory=list)
    score: int = 0
    reds: int = 0
    multiplier: float = 1.0
    label: str = "unknown"
    symbols: int = 0

    @property
    def risk_off(self) -> bool:
        return self.label == "risk_off"

    def describe(self) -> str:
        dims = " ".join(f"{d.name}={d.score}" for d in self.dimensions)
        return f"{self.label} score={self.score} reds={self.reds} x{self.multiplier:.2f} [{dims}]"


def _band(value: float, cuts: list[float]) -> int:
    """Score 5..1 by falling through descending cuts (each cut = the floor of a score)."""
    for i, cut in enumerate(cuts):
        if value >= cut:
            return DIMENSION_MAX - i
    return 1


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def multiplier_for(score: int, reds: int) -> tuple[float, str]:
    """The position-size ladder, and the label that goes with it."""
    if score >= 25:
        base, label = 1.00, "risk_on"
    elif score >= 20:
        base, label = 0.75, "neutral"
    elif score >= 15:
        base, label = 0.50, "cautious"
    else:
        base, label = 0.25, "defensive"
    # The framework's explicit override: three or more red lights means cut,
    # whatever the total says - a portfolio can score decently while three
    # separate things are individually broken, which is exactly the systemic
    # case the multi-dimension design exists to catch.
    if reds >= 3:
        return min(base, 0.25), "risk_off"
    return base, label


def detect(
    bars_by_symbol: dict[str, list[Bar]],
    *,
    funding_rate: float | None = None,
    ema_period: int = 50,
    momentum_bars: int = 24,
    vol_period: int = 24,
    vol_lookback: int = 120,
    volume_period: int = 20,
) -> MonitorState:
    """Score the environment from a snapshot of the universe's bars.

    Callers pass whatever history they have; symbols with too little history are
    skipped rather than scored as neutral, and the count that survived is
    reported so a thin reading is visible instead of implicit.
    """
    breadth: list[float] = []
    momentum: list[float] = []
    vol_ranks: list[float] = []
    rsis: list[float] = []
    volumes: list[float] = []

    for symbol, bars in sorted(bars_by_symbol.items()):
        series = closes(bars)
        if len(series) < max(ema_period + 1, momentum_bars + 1, vol_lookback // 2):
            continue
        fast = ema(series, ema_period)
        if fast:
            breadth.append(1.0 if series[-1] > fast else 0.0)
        base = series[-1 - momentum_bars]
        if base > 0:
            momentum.append(100.0 * (series[-1] / base - 1.0))
        rv = realized_vol(series, vol_period)
        history = [
            realized_vol(series[: i + 1], vol_period)
            for i in range(len(series) - 1, max(vol_period, len(series) - vol_lookback) - 1, -1)
        ]
        clean = [v for v in history if v]
        if rv is not None and len(clean) >= 10:
            rank = percentile_rank(clean, rv)
            if rank is not None:
                vol_ranks.append(rank)
        value = rsi(series, 14)
        if value is not None:
            rsis.append(value)
        ratio = volume_ratio(bars, volume_period)
        if ratio is not None:
            volumes.append(ratio)

    dims: list[Dimension] = []

    share = sum(breadth) / len(breadth) if breadth else None
    dims.append(
        Dimension(
            "trend",
            _band(share, [0.70, 0.55, 0.45, 0.30]) if share is not None else 3,
            round(share, 3) if share is not None else None,
            f"{sum(breadth):.0f}/{len(breadth)} above EMA{ema_period}",
        )
    )

    med = _median(momentum)
    dims.append(
        Dimension(
            "momentum",
            _band(med, [3.0, 1.0, -1.0, -3.0]) if med is not None else 3,
            round(med, 2) if med is not None else None,
            f"median {momentum_bars}-bar move",
        )
    )

    vol_rank = _median(vol_ranks)
    # Inverted: a HIGH volatility percentile is bad, so the cuts descend and the
    # band is mirrored via 6 - score.
    vol_score = 6 - _band(vol_rank, [90.0, 75.0, 60.0, 40.0]) if vol_rank is not None else 3
    dims.append(
        Dimension(
            "volatility",
            vol_score,
            round(vol_rank, 1) if vol_rank is not None else None,
            "realized-vol percentile (lower is calmer)",
        )
    )

    if funding_rate is None:
        dims.append(Dimension("funding", 3, None, "no funding series supplied (neutral)"))
    else:
        dims.append(
            Dimension(
                "funding",
                _band(-funding_rate, [0.0006, 0.0003, 0.0001, 0.0]),
                funding_rate,
                "mean 8h funding: crowded longs pay it",
            )
        )

    med_rsi = _median(rsis)
    if med_rsi is None:
        crowd_score = 3
    elif 45.0 <= med_rsi <= 60.0:
        crowd_score = 5
    elif 40.0 <= med_rsi < 45.0 or 60.0 < med_rsi <= 68.0:
        crowd_score = 4
    elif 35.0 <= med_rsi < 40.0 or 68.0 < med_rsi <= 75.0:
        crowd_score = 3
    elif 30.0 <= med_rsi < 35.0 or 75.0 < med_rsi <= 82.0:
        crowd_score = 2
    else:
        crowd_score = 1
    dims.append(
        Dimension(
            "crowding",
            crowd_score,
            round(med_rsi, 1) if med_rsi is not None else None,
            "median RSI: 45-60 is the uncrowded middle",
        )
    )

    med_vol = _median(volumes)
    dims.append(
        Dimension(
            "volume",
            _band(med_vol, [1.10, 0.95, 0.80, 0.60]) if med_vol is not None else 3,
            round(med_vol, 2) if med_vol is not None else None,
            f"volume / {volume_period}-bar average",
        )
    )

    score = sum(d.score for d in dims)
    reds = sum(1 for d in dims if d.red)
    multiplier, label = multiplier_for(score, reds)
    return MonitorState(
        dimensions=dims,
        score=score,
        reds=reds,
        multiplier=multiplier,
        label=label,
        symbols=len(breadth),
    )
