"""Funding-rate history: load it, look it up point-in-time, price its cost.

Why this exists
---------------
README §22 recorded a perp-strategy backtest that was negative, but with a hole
in it: our OHLCV history carries **no funding**, so the strategy's funding gate
fell back to 0 bps and the gate never closed. That backtest measured the
trend-pullback skeleton and nothing else.

`data/history/funding/<SYMBOL>.jsonl` already holds the real series
(`{"ts": ms, "rate": float}`, one row every 8 hours - OKX settles funding 3x/day).
This module turns those rows into the two things a backtest needs:

  1. **A point-in-time lookup.** `rate_at(ts)` returns the most recent settled
     rate at or before `ts`. It must never look forward: a backtest that peeks at
     a later funding print is describing a trade nobody could have made.
  2. **The cost of holding.** Funding is charged every settlement while a
     position is open, so a 48-bar hold pays it 6 times, not once. `cost_pct`
     sums the settlements actually crossed - the same reasoning as the fee
     accounting, and the reason a perp strategy cannot reuse a spot one.

Sign convention follows OKX: a **positive** rate means longs pay shorts. A long
therefore pays `+rate` per settlement, a short pays `-rate`.

Pure functions over an in-memory series: no network, no state, unit-testable.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from pathlib import Path

FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000  # OKX settles every 8 hours


@dataclass
class FundingSeries:
    symbol: str
    timestamps: list[int] = field(default_factory=list)
    rates: list[float] = field(default_factory=list)

    def add(self, ts: int, rate: float) -> None:
        self.timestamps.append(int(ts))
        self.rates.append(float(rate))

    def finalise(self) -> "FundingSeries":
        order = sorted(range(len(self.timestamps)), key=lambda i: self.timestamps[i])
        self.timestamps = [self.timestamps[i] for i in order]
        self.rates = [self.rates[i] for i in order]
        return self

    def rate_at(self, ts: int) -> float | None:
        """Most recent rate settled at or before `ts`; None if the series starts later.

        No forward fill past the end is the point: `bisect_right` can only ever
        return a settlement that has already happened.
        """
        idx = bisect.bisect_right(self.timestamps, int(ts))
        if idx == 0:
            return None
        return self.rates[idx - 1]

    def settlements_between(self, start_ts: int, end_ts: int) -> int:
        """How many funding settlements land in (start_ts, end_ts]."""
        if end_ts <= start_ts:
            return 0
        return bisect.bisect_right(self.timestamps, int(end_ts)) - bisect.bisect_right(
            self.timestamps, int(start_ts)
        )

    def cost_pct(self, start_ts: int, end_ts: int, side: str) -> float:
        """Total funding paid (+) or received (-) over a hold, in percent of notional.

        Sums every settlement actually crossed, using each settlement's own rate -
        the rate changes, so multiplying one rate by the count would be wrong.
        """
        lo = bisect.bisect_right(self.timestamps, int(start_ts))
        hi = bisect.bisect_right(self.timestamps, int(end_ts))
        if hi <= lo:
            return 0.0
        total = sum(self.rates[lo:hi]) * 100.0
        return total if side.lower() == "buy" else -total


def load_funding(path: str | Path) -> FundingSeries:
    """Load one `<SYMBOL>.jsonl` funding file. Tolerates blank/short lines."""
    file = Path(path)
    series = FundingSeries(symbol=file.stem)
    for line in file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts, rate = row.get("ts"), row.get("rate")
        if ts is None or rate is None:
            continue
        series.add(ts, rate)
    return series.finalise()


def load_funding_dir(directory: str | Path) -> dict[str, FundingSeries]:
    """Load every funding file in a directory, keyed by file stem (e.g. 'SOL-USDT_USDT')."""
    base = Path(directory)
    if not base.is_dir():
        return {}
    return {p.stem: load_funding(p) for p in sorted(base.glob("*.jsonl"))}
