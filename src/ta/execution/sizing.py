"""Position sizing. Risk-first: the stop distance decides the quantity."""

from __future__ import annotations

import math

from ..util import clamp


def basis_bps(spot: float, perp: float) -> float | None:
    """Perp premium over spot, in bps: POSITIVE means the hedge pays US.

    A carry pair buys spot at S and shorts the perp at P, so convergence realises
    exactly P - S: a perp ABOVE spot is income, a discount is paid up front.
    Measured 2026-10-09: ACT opened with the perp 130.6 bps BELOW spot (~$29 on
    top of fees) and CRV's premium drifted from -0.16% to +1.96% against us for
    -$54; those two (-$82) exceeded either pair's monthly steady-state funding.
    """
    if spot <= 0 or perp <= 0:
        return None
    return (perp / spot - 1.0) * 10_000.0


def book_impact_bps(
    levels: list[tuple[float, float]], qty: float, mid: float, side: str
) -> float | None:
    """Volume-weighted impact of taking `qty` out of `levels`, in bps of mid.

    `levels` are (price, size) in the order they would be consumed (asks ascending
    for a buy, bids descending for a sell). Returns None when the book cannot fill
    the whole quantity - "not enough depth" must never be read as "no impact".

    Why this exists: on 2026-10-09 a carry entry bought $2.2k of ACT spot at
    1.33% above the market minute's high, while the hedge leg filled at market.
    That single leg's impact was **3x the 44.9 bps round-trip cost every carry
    conclusion rested on**, and nothing in the pipeline looked at depth.
    """
    if qty <= 0 or mid <= 0 or not levels:
        return None
    remaining = float(qty)
    notional = 0.0
    filled = 0.0
    for price, size in levels:
        if price <= 0 or size <= 0:
            continue
        take = min(remaining, float(size))
        notional += take * float(price)
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if filled < qty or filled <= 0:
        return None                      # too thin to judge == too thin to trade
    vwap = notional / filled
    if str(side).lower() == "buy":
        return (vwap / mid - 1.0) * 10_000.0
    return (1.0 - vwap / mid) * 10_000.0


def carry_pair_qty(equity: float, notional_pct: float, mark: float) -> float:
    """Pair quantity for a delta-neutral carry entry, sized by NOTIONAL.

    A carry pair's risk is funding flipping or the basis blowing out - not price
    direction - so "distance to a price stop" is not a risk measure for it.
    Sizing on that distance made the position a function of the symbol's
    volatility: measured 2026-10-09, ACT opened at 402,735 (~$3.93k) purely
    because its stop sat 5.1% below entry, and the only real bound was
    `max_order_notional_pct`. This makes the size deliberate instead.
    """
    if equity <= 0 or notional_pct <= 0 or mark <= 0:
        return 0.0
    return equity * notional_pct / 100.0 / mark


def risk_based_qty(equity: float, risk_pct: float, entry: float, stop: float, scale: float = 1.0) -> float:
    """Quantity such that (entry - stop) * qty == equity * risk_pct%."""
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0 or equity <= 0 or entry <= 0:
        return 0.0
    risk_amount = equity * risk_pct / 100.0 * scale
    return risk_amount / risk_per_unit


def vol_target_scale(
    atr_pct_value: float | None,
    target_atr_pct: float,
    min_scale: float = 0.30,
    max_scale: float = 1.50,
) -> float:
    """Shrink size when the market is more volatile than the target, and vice versa."""
    if not atr_pct_value or atr_pct_value <= 0 or target_atr_pct <= 0:
        return 1.0
    return clamp(target_atr_pct / atr_pct_value, min_scale, max_scale)


def kelly_fraction(win_rate: float, payoff_ratio: float, *, cap: float = 0.25) -> float:
    """Kelly stake as a fraction of capital, clamped to [0, cap].

    The third layer of the borrowed framework asks "how much to put on" and
    answers with the Kelly criterion. Applied to OUR OWN measured numbers it is
    not a sizing method, it is a verdict: with a 40% win rate and a profit factor
    of 0.4 the payoff ratio is ~0.6, f* = W - (1-W)/R comes out negative, and the
    only correct stake is zero.

    Negative and zero edges are returned as 0.0 rather than a negative number,
    because "short your own strategy" is not an order this module can place; the
    caller is expected to stand down. `cap` exists because full Kelly is
    famously too violent for a real book, and because the inputs here are
    estimates with error bars of their own.
    """
    if win_rate <= 0.0 or payoff_ratio <= 0.0:
        return 0.0
    if win_rate >= 1.0:
        return cap
    edge = win_rate - (1.0 - win_rate) / payoff_ratio
    return max(0.0, min(cap, edge))


def payoff_ratio(win_rate_pct: float, profit_factor: float) -> float:
    """Average win / average loss, from the two numbers a backtest reports.

    PF = (W x avg_win) / ((1-W) x avg_loss), so R = PF x (1-W) / W.
    """
    win = win_rate_pct / 100.0
    if win <= 0.0 or win >= 1.0 or profit_factor <= 0.0:
        return 0.0
    return profit_factor * (1.0 - win) / win


def round_to_step(qty: float, step: float | None) -> float:
    if not step or step <= 0:
        return qty
    return math.floor(qty / step) * step


def stop_price_for(entry: float, atr_value: float, multiple: float, side: str = "buy") -> float:
    distance = atr_value * multiple
    return entry - distance if side == "buy" else entry + distance


def take_profit_price_for(entry: float, stop: float, r_multiple: float) -> float:
    risk = abs(entry - stop)
    return entry + risk * r_multiple if entry > stop else entry - risk * r_multiple


def trailing_stop_price(current_stop: float | None, mark: float, atr_value: float, multiple: float) -> float:
    """Ratchet the stop up only; never loosen it."""
    candidate = mark - atr_value * multiple
    if current_stop is None or current_stop <= 0:
        return candidate
    return max(current_stop, candidate)


def depth_refusal(impact_bps: float | None, cap_bps: float) -> str | None:
    """Why this entry must not be sent, or None when the book can absorb it.

    README 59.9 added the entry-impact gate for CARRY only, because carry is what
    produced the measured 1.33% entry (a $2.2k spot buy 1.33% above the market's
    high for that minute while the hedge leg filled at market). It belongs on
    every entry, because `None` is not "no impact" - it is "cannot tell", and the
    risk engine's own spread check cannot catch that case either: it only fires
    when a spread is measurable (`spread_bps is not None`), so a one-sided book
    (bids but no asks) sailed through as fine. Measured 2026-10-09 on the demo
    venue: 17 of the 20 tokenised-equity lines had no ask at all.

    A cap of 0 disables the gate, which is what the paper/simulated venue and the
    backtester rely on (they have no order book to probe).
    """
    if cap_bps <= 0:
        return None
    if impact_bps is None:
        return "cannot tell (no book, or too thin to fill)"
    if impact_bps > cap_bps:
        return f"impact {impact_bps:.1f}bps > {cap_bps:.1f}bps"
    return None
