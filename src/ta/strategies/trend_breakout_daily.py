"""Daily-bar variant of the Donchian breakout, for the TradFi (tokenised equity) sleeve.

Why a separate registered name rather than a new implementation
---------------------------------------------------------------
`config/strategies.toml` is keyed by strategy name, so a strategy cannot have two
blocks (the live 15m crypto block and a 1d TradFi block). Registering this thin
subclass gives the daily TradFi sleeve its own name and therefore its own
timeframe/symbol block, while the behaviour stays byte-identical to the strategy
it inherits from - which is the point: README 23 asks "does the existing logic
have an edge at daily cadence on equities?", not "can we invent a new one?".

It inherits the parent's cost filter (`edge_is_worth_it`) unchanged, which matters
more here than anywhere else: README 23.5 measured that at 1H the tokenised
equities sit *below* the cost line (XAAPL 1.15x, XSPY 0.66x) and only clear it at
daily cadence. The filter is what keeps a daily run from degenerating into churn.
"""

from __future__ import annotations

from .trend_breakout import TrendBreakout


class TrendBreakoutDaily(TrendBreakout):
    """Donchian breakout on daily bars (TradFi sleeve, disabled by default)."""

    name = "trend_breakout_daily"
