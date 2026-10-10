"""Bar-replay backtest that reuses the SAME risk engine and the same exit logic
as live trading, including the multi-timeframe views and scale-outs.

If research and production run different risk code, the backtest is fiction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, pstdev

from .config import RiskConfig
from .execution.sizing import (
    risk_based_qty,
    stop_price_for,
    take_profit_price_for,
    trailing_stop_price,
    vol_target_scale,
)
from .indicators import atr, atr_pct
from .regime import detect
from .marketdata import aggregate, closed_counts
from .models import Bar, Fill, OrderRequest, Position
from .portfolio import PositionBook
from .risk.engine import RiskEngine
from .strategies.base import MarketView, Strategy
from .util import bars_per_year, tf_to_ms


@dataclass
class BacktestResult:
    strategy: str
    symbols: list[str]
    timeframe: str
    bars: int
    initial_cash: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    sortino: float
    calmar: float
    trades: int
    win_rate_pct: float
    profit_factor: float
    expectancy: float
    fees_paid: float
    exposure_pct: float
    blocked_orders: int
    maker_missed: int = 0
    exits_by_reason: dict[str, int] = field(default_factory=dict)
    equity_curve: list[float] = field(default_factory=list)
    realized_events: list[float] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "strategy": self.strategy,
            "symbols": ",".join(self.symbols),
            "timeframe": self.timeframe,
            "bars": self.bars,
            "final_equity": round(self.final_equity, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe": round(self.sharpe, 2),
            "sortino": round(self.sortino, 2),
            "calmar": round(self.calmar, 2),
            "trades": self.trades,
            "win_rate_pct": round(self.win_rate_pct, 1),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy": round(self.expectancy, 4),
            "fees_paid": round(self.fees_paid, 2),
            "exposure_pct": round(self.exposure_pct, 1),
            "blocked_orders": self.blocked_orders,
            "maker_missed": self.maker_missed,
            "exits": self.exits_by_reason,
        }


class _ReplayFeed:
    """Serves any timeframe for any symbol as of the current base bar."""

    simulated = True

    def __init__(
        self,
        series: dict[str, dict[str, list[Bar]]],
        base_tf: str,
        master: list[int],
        funding: dict[str, object] | None = None,
    ) -> None:
        self.base_tf = base_tf
        self.series = series
        self.master = list(master)
        self.base_len = len(self.master)
        self.cursor = 0
        # symbol -> ta.funding.FundingSeries, or empty. Populated only when the
        # caller supplies it; an empty dict means the funding gate stays open,
        # which is exactly the hole README 22 recorded (a perp backtest with no
        # funding data measures the skeleton, never the gate).
        self.funding = dict(funding or {})
        self._counts: dict[tuple[str, str], list[int]] = {}
        base_ms = tf_to_ms(base_tf)
        for symbol, frames in series.items():
            for timeframe, bars in frames.items():
                # How many of THIS symbol's bars in THIS timeframe are closed at
                # each master stamp. Direct from the symbol's own timestamps, so a
                # symbol that listed late contributes 0 on the early stamps instead
                # of forcing the whole replay to start at its listing date.
                target_ms = tf_to_ms(timeframe)
                counts: list[int] = []
                closed = 0
                for ts in self.master:
                    while (
                        closed < len(bars)
                        and bars[closed].ts + target_ms <= ts + base_ms
                    ):
                        closed += 1
                    counts.append(closed)
                self._counts[(symbol, timeframe)] = counts

    def bars(self, symbol: str, timeframe: str | None = None) -> list[Bar]:
        timeframe = timeframe or self.base_tf
        frames = self.series.get(symbol, {})
        data = frames.get(timeframe)
        counts = self._counts.get((symbol, timeframe))
        if not data or not counts:
            return []
        return data[: counts[min(self.cursor, self.base_len - 1)]]

    def mark(self, symbol: str) -> float:
        bars = self.bars(symbol)
        return bars[-1].close if bars else 0.0

    def funding_rate(self, symbol: str) -> float | None:
        """Settled funding rate as of the current base bar - never looks ahead.

        `rate_at` only returns a settlement at or before the given timestamp, so
        a replay can never see a funding print from the future. Strategies read
        this via `view.source.funding_rate`, multiplied by 10_000 for bps.
        """
        series = self.funding.get(symbol)
        if series is None:
            return None
        ts = self.last_bar_ts(symbol, self.base_tf)
        if not ts:
            return None
        rate_at = getattr(series, "rate_at", None)
        return rate_at(ts) if callable(rate_at) else None

    def last_bar_ts(self, symbol: str, timeframe: str | None = None) -> int:
        bars = self.bars(symbol, timeframe)
        return bars[-1].ts if bars else 0

    def truncated(self, symbol: str, timeframe: str | None = None) -> bool:
        return False


class _BoundedSource:
    """Hand a strategy a bounded window instead of the whole series.

    Strategies legitimately only look at the last few hundred bars, but they
    call `closes(bars)` / `ema(...)` on whatever they are given. Feeding them the
    full 17,282-bar history turned a 180-day backtest into an O(n^2) job that had
    not finished after 20 minutes.
    """

    __slots__ = ("_feed", "_window")

    def __init__(self, feed: _ReplayFeed, window: int) -> None:
        self._feed = feed
        self._window = max(60, int(window))

    def bars(self, symbol: str, timeframe: str | None = None) -> list[Bar]:
        bars = self._feed.bars(symbol, timeframe)
        return bars[-self._window:] if len(bars) > self._window else bars

    def mark(self, symbol: str) -> float:
        return self._feed.mark(symbol)

    def funding_rate(self, symbol: str) -> float | None:
        """Pass the feed's point-in-time funding through to the strategy."""
        return self._feed.funding_rate(symbol)

    def last_bar_ts(self, symbol: str, timeframe: str | None = None) -> int:
        return self._feed.last_bar_ts(symbol, timeframe)

    def truncated(self, symbol: str, timeframe: str | None = None) -> bool:
        return self._feed.truncated(symbol, timeframe)

    def regime(self, symbol: str, timeframe: str | None = None, **kwargs: object):
        return detect(self.bars(symbol, timeframe), **kwargs)


def _normalise(
    bars: dict[str, list[Bar]] | dict[str, dict[str, list[Bar]]],
    base_tf: str,
    timeframes: set[str],
) -> tuple[dict[str, dict[str, list[Bar]]], int]:
    """Accept {symbol: bars} or {symbol: {timeframe: bars}} and align everything.

    Real symbols have different listing dates, so the replay must run on the
    timestamps every symbol actually shares. Taking the longest series here (as
    an earlier version did) works on synthetic data where all lengths match, and
    crashes on the first real backtest.
    """
    base_series: dict[str, list[Bar]] = {}
    supplied: dict[str, dict[str, list[Bar]]] = {}
    for symbol, payload in bars.items():
        frames = payload if isinstance(payload, dict) else {base_tf: payload}
        if base_tf not in frames or not frames[base_tf]:
            raise ValueError(f"{symbol}: need {base_tf} bars to anchor the replay")
        base_series[symbol] = list(frames[base_tf])
        supplied[symbol] = {tf: list(v) for tf, v in frames.items() if tf != base_tf}

    # UNION timeline, not intersection (2026-10-07). Intersecting meant every
    # symbol had to share the exact same stamps, so ONE late listing decided the
    # window for the whole pool: the 101-symbol TradFi sleeve (listed across
    # 2022-08 .. 2026-09-07) collapsed to a single daily bar, and a pool with a
    # crypto mixed in collapsed to zero because crypto daily bars open at 00:00
    # UTC while tokenised equities open at 16:00 UTC.
    #
    # A symbol that was not listed yet now simply has no bars until it is - the
    # feed reports "0 closed bars" for it and the strategies skip it, exactly as
    # they skip a symbol that has not warmed up. That is what "trade what is
    # available" has to mean for a pool with mixed listing dates.
    stamps: set[int] = set()
    for series in base_series.values():
        stamps.update(bar.ts for bar in series)
    master = sorted(stamps)
    if len(master) < 20:
        raise ValueError(
            f"the union of the pool holds only {len(master)} {base_tf} timestamps "
            f"- not enough history to replay"
        )

    series: dict[str, dict[str, list[Bar]]] = {}
    for symbol, base_bars in base_series.items():
        resolved: dict[str, list[Bar]] = {base_tf: base_bars}
        for timeframe in timeframes:
            if timeframe == base_tf:
                continue
            provided = supplied[symbol].get(timeframe)
            resolved[timeframe] = (
                provided if provided else aggregate(base_bars, base_tf, timeframe)
            )
        series[symbol] = resolved
    return series, master


def run_backtest(
    strategy: Strategy,
    bars: dict[str, list[Bar]] | dict[str, dict[str, list[Bar]]],
    risk: RiskConfig,
    *,
    initial_cash: float = 10_000.0,
    fee_bps: float = 8.0,
    slippage_bps: float = 5.0,
    maker_fee_bps: float = 8.0,
    timeframe: str = "1h",
    fee_multiplier: float = 1.0,
    symbol_tiers: dict[str, str] | None = None,
    entry_after_ts: int = 0,
    entry_before_ts: int = 0,
    funding: dict[str, object] | None = None,
) -> BacktestResult:
    base_tf = timeframe
    timeframes = {strategy.timeframe}
    if strategy.confirm_timeframe:
        timeframes.add(strategy.confirm_timeframe)
    series, master = _normalise(bars, base_tf, timeframes)
    base_len = len(master)
    symbols = [s for s, frames in series.items() if frames.get(base_tf)]
    if not symbols:
        raise ValueError("no bars supplied")

    feed = _ReplayFeed(series, base_tf, master, funding=funding)
    import sys as _sys

    print(f"[probe] run_backtest funding series = {len(feed.funding)}", file=_sys.stderr)
    warmup = max(1, min(strategy.warmup, base_len - 1))
    fee_rate = fee_bps * fee_multiplier / 10_000.0
    maker_fee_rate = maker_fee_bps * fee_multiplier / 10_000.0
    slip = slippage_bps / 10_000.0
    book = PositionBook(cash=initial_cash)
    book.peak_equity = initial_cash
    book.day_start_equity = initial_cash
    risk_engine = RiskEngine(
        risk,
        kill_switch_path="__backtest_no_kill__",
        write_kill_file=False,
        symbol_tiers=symbol_tiers or {},
    )

    sources = {symbol: _BoundedSource(feed, strategy.history_window) for symbol in symbols}
    plans: dict[str, dict[str, float | bool | None]] = {}
    pending: dict[str, dict] = {}  # resting maker entries, not yet filled
    maker_missed = 0
    equity_curve: list[float] = []
    realized_events: list[float] = []
    exits_by_reason: dict[str, int] = {}
    blocked = 0
    bars_in_market = 0

    def fill(
        symbol: str,
        side: str,
        qty: float,
        price: float,
        ts: int,
        tag: str,
        rate: float | None = None,
    ) -> float:
        notional = qty * price
        record = Fill(
            client_order_id=f"bt_{tag}_{symbol}_{ts}".replace("/", ""),
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            qty=qty,
            price=price,
            fee=notional * (fee_rate if rate is None else rate),
            ts=ts,
            tag=tag,
        )
        entry_ts = 0
        if side == "sell":
            held = book.positions.get(symbol)
            entry_ts = int(getattr(held, "opened_ts", 0) or 0) if held is not None else 0
        realized = book.apply_fill(record)
        if side == "sell":
            # Funding is a real, ongoing cost of holding a perpetual - the one
            # cost the spot-shaped accounting in this engine never charged. Read
            # the entry timestamp BEFORE apply_fill, because a full exit removes
            # the position from the book.
            series = getattr(feed, "funding", {}).get(symbol)
            if entry_ts and series is not None:
                try:
                    # Closing a LONG: the holder PAYS positive funding, so the raw
                    # (un-flipped) sum is the cost. Passing side="sell" would mean
                    # "opening a short", i.e. receiving - which is how the first
                    # attempt accidentally *credited* the cost and flattered both
                    # win-rate and profit factor.
                    pct = series.cost_pct(entry_ts, ts, side="buy")
                except Exception:  # noqa: BLE001 - never let cost accounting kill a run
                    pct = 0.0
                cost = notional * pct / 100.0
                realized -= cost
                # The equity curve comes from the BOOK, not from this return value:
                # book.apply_fill() already settled cash above, so adjusting only
                # `realized` moved the statistics and left final_equity bit-identical.
                book.cash -= cost
            realized_events.append(realized)
        return realized

    for index in range(warmup, base_len):
        feed.cursor = index
        # The clock is the MASTER stamp, not symbols[0]'s bar: on a union timeline
        # the alphabetically-first symbol may not have a bar at every index (a
        # late listing, a market holiday), and indexing its own list with the
        # master index is exactly the IndexError this replaced.
        master_ts = master[index]
        # A fold is defined by BOTH bounds: history before it is only a warm-up
        # runway, and trades must not leak past the end of the window.
        in_window = (not entry_after_ts or master_ts >= entry_after_ts) and (
            not entry_before_ts or master_ts < entry_before_ts
        )
        risk_engine._time = (  # type: ignore[attr-defined]
            lambda idx=index: master[idx] / 1000.0
        )
        marks = {s: feed.mark(s) for s in symbols}
        for symbol, price in marks.items():
            book.mark(symbol, price)

        # ---- resting maker entries ------------------------------------------
        # A limit buy at the bid only fills if price actually trades down to it.
        # That is the whole trade-off: a better price and a maker fee, paid for
        # with missed fills (adverse selection).
        for symbol, order in list(pending.items()):
            seen = feed.bars(symbol, base_tf)
            if not seen:
                continue                      # not listed yet on this stamp
            bar = seen[-1]
            if bar.low <= order["limit"]:
                fill(
                    symbol, "buy", order["qty"], order["limit"], bar.ts,
                    strategy.name, rate=maker_fee_rate,
                )
                plans[symbol] = order["plan"]
                pending.pop(symbol)
            elif index >= order["expires_index"]:
                pending.pop(symbol)
                maker_missed += 1

        # ---- exits first (stops are conservative: checked before targets) ----
        for symbol in list(book.open_positions()):
            plan = plans.get(symbol)
            if plan is None:
                continue
            position = book.positions[symbol]
            seen = feed.bars(symbol, base_tf)
            if not seen:
                continue
            bar = seen[-1]
            stop = float(plan["stop"] or 0.0)
            target = plan["target"]
            partial_target = plan["partial_target"]
            entry = float(plan["entry"] or 0.0)
            risk_per_unit = float(plan["risk_per_unit"] or 0.0)

            if plan["breakeven_at_r"] and not plan["breakeven_moved"] and risk_per_unit > 0:
                if bar.high >= entry + float(plan["breakeven_at_r"]) * risk_per_unit:
                    plan["stop"] = max(stop, entry)
                    plan["breakeven_moved"] = True
                    stop = float(plan["stop"])

            if (
                partial_target
                and not plan["partial_taken"]
                and bar.high >= float(partial_target)
            ):
                qty = position.qty * float(plan["partial_fraction"] or 0.5)
                if qty > 0:
                    realized = fill(
                        symbol, "sell", qty, float(partial_target) * (1 - slip), bar.ts, "partial"
                    )
                    key = "partial_win" if realized > 0 else "partial_loss"
                    exits_by_reason[key] = exits_by_reason.get(key, 0) + 1
                    plan["partial_taken"] = True
                    plan["stop"] = max(stop, entry)
                    stop = float(plan["stop"])
                    position = book.positions[symbol]

            if stop and bar.low <= stop:
                realized = fill(symbol, "sell", position.qty, stop * (1 - slip), bar.ts, "stop")
                # a trailing stop exiting in profit is a different animal from an
                # initial stop taking a loss - conflating them hides the diagnosis
                key = "stop_win" if realized > 0 else "stop_loss"
                exits_by_reason[key] = exits_by_reason.get(key, 0) + 1
                plans.pop(symbol, None)
                continue
            if target and bar.high >= float(target):
                realized = fill(
                    symbol, "sell", position.qty, float(target) * (1 - slip), bar.ts, "target"
                )
                key = "target_win" if realized > 0 else "target_loss"
                exits_by_reason[key] = exits_by_reason.get(key, 0) + 1
                plans.pop(symbol, None)
                continue
            max_holding = plan["max_holding_bars"]
            if max_holding and position.opened_ts:
                held = int((bar.ts - position.opened_ts) // tf_to_ms(base_tf))
                if held >= int(max_holding):
                    price = bar.close * (1 - slip)
                    realized = fill(symbol, "sell", position.qty, price, bar.ts, "time_stop")
                    key = "time_win" if realized > 0 else "time_loss"
                    exits_by_reason[key] = exits_by_reason.get(key, 0) + 1
                    plans.pop(symbol, None)
                    continue
            if risk.positions.trailing_stop_enabled:
                atr_value = atr(feed.bars(symbol, strategy.timeframe), 14) or 0.0
                if atr_value > 0:
                    plan["stop"] = max(
                        float(plan["stop"] or 0.0),
                        trailing_stop_price(
                            float(plan["stop"] or 0.0),
                            marks[symbol],
                            atr_value,
                            risk.positions.trailing_stop_atr_multiple,
                        ),
                    )

        # ---- entries ---------------------------------------------------------
        for symbol in symbols:
            seen = feed.bars(symbol, base_tf)
            if not seen:
                continue                      # no history yet: nothing to decide on
            bar = seen[-1]
            if not in_window:
                continue  # warm-up runway: indicators update, no new risk is taken
            window = feed.bars(symbol, strategy.timeframe)
            position = book.positions.get(symbol)
            view = MarketView(symbol=symbol, mark=bar.close, source=sources[symbol])
            signal = strategy.on_bar(view, position if position and position.qty > 0 else None)
            if signal is None:
                continue

            if signal.exit:
                held = book.positions.get(symbol)
                if not held or held.qty <= 0:
                    continue
                price = bar.close * (1 - slip)
                realized = fill(symbol, "sell", held.qty, price, bar.ts, "signal_exit")
                key = "signal_win" if realized > 0 else "signal_loss"
                exits_by_reason[key] = exits_by_reason.get(key, 0) + 1
                plans.pop(symbol, None)
                continue

            if signal.side != "buy" or (position and position.qty > 0):
                continue
            if symbol in pending:
                continue  # one resting entry per symbol at a time

            tier = risk.limits_for((symbol_tiers or {}).get(symbol))
            atr_period = int(strategy.param("atr_period", 14))
            atr_value = atr(window, atr_period) or 0.0
            if atr_value <= 0:
                continue
            stop = signal.stop_price or stop_price_for(bar.close, atr_value, tier.stop_atr_multiple)
            scale = vol_target_scale(
                atr_pct(window, atr_period),
                tier.atr_target_pct,
                risk.positions.vol_target_min_scale,
                risk.positions.vol_target_max_scale,
            )
            equity = book.cash + sum(
                p.qty * (book.mark_of(s) or p.avg_price) for s, p in book.open_positions().items()
            )
            qty = risk_based_qty(equity, tier.risk_per_trade_pct, bar.close, stop, scale)
            if qty <= 0:
                continue

            account = book.account("backtest")
            account.risk_equity = equity
            account.day_start_equity = book.day_start_equity or equity
            account.peak_equity = max(book.peak_equity, equity)
            request = OrderRequest(
                client_order_id=f"bt_{symbol}_{index}".replace("/", ""),
                symbol=symbol,
                side="buy",
                qty=qty,
                type="market",
                stop_price=stop,
                take_profit=signal.take_profit,
                tag=strategy.name,
            )
            decision = risk_engine.pre_trade(
                request, account, bar.close, marks, stops={s: float(p["stop"] or 0.0) for s, p in plans.items()}
            )
            if decision.rejected:
                blocked += 1
                continue
            qty = decision.qty

            def make_plan(entry: float) -> dict:
                return {
                    "entry": entry,
                    "stop": stop,
                    # No fallback: if the strategy asked for no hard target, the
                    # live runner does not invent one either. Injecting a
                    # tier-based 2R here made research disagree with production.
                    "target": signal.take_profit,
                    "partial_target": signal.partial_target,
                    "partial_fraction": signal.partial_fraction,
                    "breakeven_at_r": signal.breakeven_at_r,
                    "max_holding_bars": signal.max_holding_bars,
                    "risk_per_unit": abs(entry - stop),
                    "partial_taken": False,
                    "breakeven_moved": False,
                }

            if str(strategy.param("entry_style", "market")) == "maker":
                offset = float(strategy.param("maker_offset_bps", 0.5)) / 10_000.0
                limit = bar.close * (1.0 - offset)
                pending[symbol] = {
                    "limit": limit,
                    "qty": qty,
                    "expires_index": index + int(strategy.param("maker_timeout_bars", 3)),
                    "plan": make_plan(limit),
                }
                continue

            price = bar.close * (1 + slip)
            fill(symbol, "buy", qty, price, bar.ts, strategy.name)
            plans[symbol] = make_plan(price)

        book.roll_day(str(index))
        if in_window:
            equity_curve.append(book.equity())
            if book.open_positions():
                bars_in_market += 1

    # ---- metrics -----------------------------------------------------------
    returns = [
        equity_curve[i] / equity_curve[i - 1] - 1.0
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]
    final_equity = equity_curve[-1] if equity_curve else initial_cash
    total_return = 100.0 * (final_equity / initial_cash - 1.0)

    peak = initial_cash
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, 100.0 * (peak - value) / peak)

    periods = bars_per_year(base_tf)
    if len(returns) > 2:
        mu = mean(returns)
        sigma = pstdev(returns)
        downside = [r for r in returns if r < 0]
        dsigma = pstdev(downside) if len(downside) > 1 else 0.0
        sharpe = (mu / sigma) * math.sqrt(periods) if sigma > 0 else 0.0
        sortino = (mu / dsigma) * math.sqrt(periods) if dsigma > 0 else 0.0
    else:
        sharpe = sortino = 0.0

    years = len(returns) / periods if periods else 0.0
    cagr = (
        ((final_equity / initial_cash) ** (1 / years) - 1) * 100
        if years > 0.2 and final_equity > 0
        else total_return
    )
    calmar = cagr / max_dd if max_dd > 0 else 0.0

    wins = [r for r in realized_events if r > 0]
    losses = [r for r in realized_events if r < 0]
    win_rate = 100.0 * len(wins) / len(realized_events) if realized_events else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (
        gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    )
    expectancy = mean(realized_events) if realized_events else 0.0

    return BacktestResult(
        strategy=strategy.name,
        symbols=symbols,
        timeframe=base_tf,
        bars=base_len - warmup,
        initial_cash=initial_cash,
        final_equity=final_equity,
        total_return_pct=total_return,
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        trades=book.trades,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        fees_paid=book.fees_paid,
        exposure_pct=100.0 * bars_in_market / max(1, len(equity_curve)),
        blocked_orders=blocked,
        maker_missed=maker_missed,
        exits_by_reason=exits_by_reason,
        equity_curve=equity_curve,
        realized_events=realized_events,
    )
    fee_bps: float = 8.0,
    slippage_bps: float = 5.0,
    maker_fee_bps: float = 8.0,
