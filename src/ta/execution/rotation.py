"""Which carry pair, if any, the runner may close right now (1.2's decision half).

Why this is separate from the detector
--------------------------------------
`scripts/carry_rotation.py` (the daily job) decides which pair has STOPPED PAYING:
median funding below a floor on every day of a 3-day window, breakeven inside the
hold, older than 24h. That is research: it reads funding history and writes a
report. Acting on the report is a trading decision, and it belongs to the runner -
only the runner holds the pair state, the ledger and the executor, and closing a
pair behind its back leaves `data/carry/pairs.json` describing a position that no
longer exists (the shape that produced the duplicate-hedge incidents).

So this module is the runner-side half: given the detector's report, what the
runner actually holds, its equity and today's action count, return the single pair
it may close - or None. Close only: opening the replacement is what the runner's
normal carry entry already does (passive spot leg, depth gate, pairing invariants),
and doing it here would mean hand-rolling a two-leg sequence.
"""

from __future__ import annotations


def plan_rotation(
    report: dict | None,
    held: set[str],
    equity: float,
    notionals: dict[str, float] | None = None,
    *,
    max_notional_pct: float = 5.0,
    max_per_day: int = 1,
    actions_today: int = 0,
    intended_hold_days: float = 30.0,
    min_net_edge_bps: float = 20.0,
) -> dict | None:
    """The one rotation to perform now, or None.

    Every guardrail is a refusal, never a silent adjustment:
      * only a pair the runner actually holds can be closed;
      * notional <= `max_notional_pct` of equity (`notionals` supplies the size);
      * at most `max_per_day` rotations per calendar day;
      * projected funding improvement must still clear the round trip by
        `min_net_edge_bps` over the intended holding period. Merely breaking even
        on the last day of the hold is not an edge and caused fee-heavy churn.
    """
    if not report or not report.get("rotations"):
        return None
    if actions_today >= max_per_day:
        return None
    for rotation in report["rotations"]:
        symbol = str(rotation.get("from") or "")
        if symbol not in held:
            continue  # the detector may name a pair this process is not holding
        notional = float((notionals or {}).get(symbol) or 0.0)
        cap = equity * max_notional_pct / 100.0
        if cap > 0 and notional > cap:
            return {
                "symbol": symbol, "refused": "notional",
                "notional": notional, "cap": cap, "rotation": rotation,
            }
        round_trip = float(report.get("round_trip_bps") or 44.9)
        gain = rotation.get("gain_bps_per_day")
        if gain is None:
            breakeven = float(rotation.get("breakeven_days") or 0.0)
            gain = round_trip / breakeven if breakeven > 0 else 0.0
        expected_net = float(gain) * max(0.0, float(intended_hold_days)) - round_trip
        if expected_net < float(min_net_edge_bps):
            return {
                "symbol": symbol, "refused": "insufficient_net_edge",
                "notional": notional, "cap": cap, "rotation": rotation,
                "expected_net_bps": round(expected_net, 3),
                "min_net_edge_bps": float(min_net_edge_bps),
            }
        return {"symbol": symbol, "refused": "", "notional": notional,
                "cap": cap, "rotation": rotation,
                "expected_net_bps": round(expected_net, 3)}
    return None
