"""The orchestrator.

One tick:
    venue health -> market data -> circuit breakers -> stops/exits -> signals
    -> risk gate -> orders -> fills -> ledger

Two rules are enforced here and nowhere else:

  * nothing reaches the venue unless the risk engine approved it;
  * the loop does not die on a transient venue failure - it degrades (stop
    opening risk, keep trying, alert) and keeps the process alive.
"""

from __future__ import annotations

import logging
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .baseline import VenueBaseline, load_or_create
from .config import Config
from .execution.oms import OMS
from .execution.sizing import (
    carry_pair_qty,
    risk_based_qty,
    trailing_stop_price,
    vol_target_scale,
)
from .indicators import atr, atr_pct
from .marketdata import LiveFeed, SyntheticFeed
from .models import AccountState, Bar, OrderRequest, Position, Signal
from .monitoring import Alerts, Metrics
from .portfolio import Ledger
from .resilience import ResilientVenue, VenueDegraded
from .risk.engine import RiskEngine
from .strategies import build_strategy
from .strategies.base import MarketView, Strategy
from .util import VirtualClock, now_ms, tf_to_ms
from .venues import build_venue
from .venues.base import Venue


class KillSwitchTriggered(RuntimeError):
    """Raised to unwind the loop after a kill-switch flatten."""


@dataclass
class ExitPlan:
    """How an open position will be managed: stop, targets, scale-out, time stop."""

    symbol: str
    entry: float
    stop: float
    target: float | None = None
    partial_target: float | None = None
    partial_fraction: float = 0.5
    breakeven_at_r: float | None = None
    max_holding_bars: int | None = None
    risk_per_unit: float = 0.0
    partial_taken: bool = False
    breakeven_moved: bool = False
    trailing_multiple: float = 3.0
    bars_held_entry: int = 0

    def dict(self) -> dict[str, Any]:
        return {
            "entry": round(self.entry, 8),
            "stop": round(self.stop, 8),
            "target": self.target,
            "partial_taken": self.partial_taken,
            "breakeven_moved": self.breakeven_moved,
        }


@dataclass
class RunReport:
    iterations: int
    equity: float
    risk_equity: float
    cash: float
    return_pct: float
    peak_drawdown_pct: float
    drawdown_pct: float
    daily_pnl_pct: float
    open_positions: int
    orders_submitted: int
    orders_blocked: int
    fills: int
    fees_paid: float
    trades: int
    win_rate_pct: float
    killed: bool
    kill_reason: str
    run_id: str = ""
    venue: str = ""
    managed_notional: float = 0.0
    unmanaged_notional: float = 0.0
    heat_pct: float = 0.0
    degraded_ticks: int = 0
    venue_retries: int = 0
    reconciled: bool = False
    reconciliation_diffs: list[str] = field(default_factory=list)
    block_reasons: dict[str, int] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def lines(self) -> list[str]:
        rows = [
            f"run id            {self.run_id}   venue {self.venue}",
            f"iterations        {self.iterations}",
            f"equity            {self.equity:,.2f}   cash {self.cash:,.2f}",
            f"risk equity       {self.risk_equity:,.2f}   (capital the bot controls)",
            f"return            {self.return_pct:+.2f}%",
            f"peak drawdown     {self.peak_drawdown_pct:.2f}%",
            f"managed notional  {self.managed_notional:,.2f}   "
            f"unmanaged {self.unmanaged_notional:,.2f} (excluded from caps)",
            f"open heat         {self.heat_pct:.2f}%",
            f"open positions    {self.open_positions}",
            f"orders submitted  {self.orders_submitted}",
            f"orders blocked    {self.orders_blocked}",
            f"fills             {self.fills}",
            f"fees paid         {self.fees_paid:,.2f}",
            f"closed trades     {self.trades}   win rate {self.win_rate_pct:.1f}%",
            f"venue degraded    {self.degraded_ticks} tick(s), {self.venue_retries} retry(ies)",
            f"reconciled        {self.reconciled}",
        ]
        if self.reconciliation_diffs:
            for diff in self.reconciliation_diffs[:5]:
                rows.append(f"  ! {diff}")
        if self.block_reasons:
            top = sorted(self.block_reasons.items(), key=lambda kv: -kv[1])[:6]
            rows.append("blocked by        " + ", ".join(f"{k}={v}" for k, v in top))
        rows.append(f"killed            {self.killed} {self.kill_reason}")
        return rows


class TradingRunner:
    def __init__(
        self,
        config: Config,
        venue: Venue,
        feed,
        strategies: list[Strategy],
        risk: RiskEngine,
        oms: OMS,
        ledger: Ledger,
        logger: logging.Logger,
        metrics: Metrics | None = None,
        alerts: Alerts | None = None,
        interval: float = 0.0,
        max_iterations: int = 0,
        dry_run: bool = True,
        clock: VirtualClock | None = None,
        baseline: VenueBaseline | None = None,
        baseline_path: Any = None,
        hedge_venue: Venue | None = None,
        hedge_oms: OMS | None = None,
    ) -> None:
        self.config = config
        self.venue = venue
        self.feed = feed
        self.strategies = strategies
        self.risk = risk
        self.oms = oms
        self.ledger = ledger
        self.log = logger
        self.metrics = metrics or Metrics()
        self.alerts = alerts
        self.interval = interval
        self.max_iterations = max_iterations
        self.dry_run = dry_run
        self.clock = clock
        self.baseline = baseline
        self.baseline_path = baseline_path
        # Carry's second leg (README 37). None unless [app] hedge_venue is set.
        self.hedge_venue = hedge_venue
        self.hedge_oms = hedge_oms

        self.symbols = sorted(
            {s for strat in strategies for s in strat.symbols} or set(config.traded_symbols)
        )
        self.exits: dict[str, ExitPlan] = {}
        # symbol -> PairLegs for carry positions. A carry position is two venue
        # legs behind one symbol, so the exit path needs the pair record to close
        # both (README 37).
        self._carry_pairs: dict[str, object] = {}
        # Pair state is written to disk, not just kept in memory. Measured
        # 2026-10-08: every restart orphaned the open pairs - their pair record
        # vanished, so the exit path could no longer close both legs (it refuses
        # to sell the spot half alone) and those positions could only be closed by
        # hand. Restarts are routine here, so the state has to survive them.
        self._carry_state_path = config.path("data/carry/pairs.json")
        self.equity_start: float | None = None
        self.equity_peak = 0.0
        self.day_start_equity = 0.0
        self._day = ""
        self._flatten_day = ""
        self.iterations = 0
        self.signals_seen = 0
        self.session_orders = 0
        self.session_fills = 0
        self.session_blocked = 0
        self.session_fees = 0.0
        self.degraded_ticks = 0
        self.tick_errors = 0
        self._last_account: AccountState | None = None
        self._last_raw_cash: float | None = None
        self._spread_cache: dict[str, tuple[int, float | None]] = {}
        self._last_eval: dict[tuple[str, str], int] = {}
        # Book-vs-venue reconciliation only used to run in the shutdown path, so a
        # long-running (or SIGKILLed) loop never compared itself to the exchange.
        # Measured 2026-09-30: the book overstated OKB by 0.1% for over an hour,
        # the exit order was rejected with `51008 insufficient balance`, and no
        # "reconciliation mismatch" alert ever fired. Check periodically instead.
        self.reconcile_every_ticks = int(
            os.environ.get("TA_RECONCILE_EVERY_TICKS") or 30
        )
        # Start one tick SHORT of the interval, so the first successful tick after
        # a restart reconciles instead of waiting the full period. Measured
        # 2026-10-08: the runner restarted 4 times inside 85 minutes, and at ~103s
        # per tick the period is ~52 minutes - a restart cadence shorter than that
        # meant the periodic reconciliation never ran at all, silently skipping the
        # one check that exists to catch a stale book.
        self._ticks_since_reconcile = self.reconcile_every_ticks - 1
        self._reconcile_signature = ""
        self.oms.on_fill = self._on_fill
        if self.hedge_oms is not None:
            # The hedge leg's fills land in the same ledger; without this its
            # fills would be recorded but never applied to the book.
            self.hedge_oms.on_fill = self._on_fill
        # A restart must not forget which pairs are open (see _load_carry_pairs).
        if self.hedge_oms is not None:
            self._load_carry_pairs()

    # ---- construction ------------------------------------------------------
    @classmethod
    def build(
        cls,
        config: Config,
        logger: logging.Logger,
        venue_name: str = "paper",
        strategy_names: list[str] | None = None,
        symbols: list[str] | None = None,
        iterations: int = 0,
        interval: float | None = None,
        dry_run: bool = True,
        run_id: str | None = None,
    ) -> "TradingRunner":
        import uuid
        from pathlib import Path

        names = strategy_names or [n for n, s in config.strategies.items() if s.enabled]
        if not names:
            raise ValueError("no strategy selected")

        section = config.venue(venue_name)
        costs = {
            "fee_bps": section.fee_bps,
            "slippage_bps": section.slippage_bps,
            # keep the strategy's view depth equal to what the live feed serves
            "live_bar_limit": config.universe.bar_limit,
        }
        new_symbols = config.universe.symbols_by_tier.get("new", [])

        strategies: list[Strategy] = []
        for name in names:
            spec = config.strategy(name)
            picked = symbols or config.resolve_symbols(spec)
            strategies.append(
                build_strategy(
                    spec, config.risk, picked, costs=costs, new_symbols=new_symbols
                )
            )

        raw_venue = build_venue(config, venue_name, dry_run=dry_run)
        raw_venue.connect()
        # A spot venue whose tdMode probe failed would reject EVERY spot order it
        # sends (sCode 51000) while looking perfectly healthy - measured
        # 2026-10-10: three such rejections in one day, two of them entries. Say so
        # at startup instead of letting it be discovered by reading the log later.
        if (getattr(raw_venue, "market_type", "") == "spot"
                and not getattr(raw_venue, "td_mode_resolved", True)):
            logger.warning(
                "spot tdMode was NOT resolved - every spot order will be rejected "
                "until the account level can be read",
                extra={"extra_fields": {
                    "spot_td_mode": getattr(raw_venue, "spot_td_mode", ""),
                    "error": getattr(raw_venue, "last_td_mode_error", ""),
                }},
            )
        venue: Venue = raw_venue
        if not raw_venue.simulated:
            venue = ResilientVenue(raw_venue, logger=logger)

        # Optional hedge leg for carry pairs (README 37). Left unbuilt unless the
        # config names a venue, so nothing about today's single-venue behaviour
        # changes: with no hedge venue a carry-tagged signal is refused outright
        # (below) rather than half-executed as a naked long.
        hedge_venue: Venue | None = None
        hedge_oms: OMS | None = None
        hedge_venue: Venue | None = None
        hedge_oms: OMS | None = None

        base_timeframe = config.universe.timeframe
        base_ms = tf_to_ms(base_timeframe)
        timeframes = set(config.universe.all_timeframes)
        for strategy in strategies:
            timeframes.add(strategy.timeframe)
            if strategy.confirm_timeframe:
                timeframes.add(strategy.confirm_timeframe)
        timeframes = [base_timeframe, *sorted(timeframes - {base_timeframe})]

        # warmup has to be expressed in base-timeframe bars
        warmup_bars = 120
        for strategy in strategies:
            ratio = max(1, tf_to_ms(strategy.timeframe) // base_ms)
            warmup_bars = max(warmup_bars, strategy.warmup * ratio + 10)
            if strategy.confirm_timeframe:
                confirm_ratio = max(1, tf_to_ms(strategy.confirm_timeframe) // base_ms)
                warmup_bars = max(warmup_bars, strategy.confirm_warmup * confirm_ratio + 10)

        clock = VirtualClock(step_ms=base_ms) if venue_name == "paper" else None
        # only subscribe to what a strategy actually trades
        traded = sorted({s for strategy in strategies for s in strategy.symbols}) or list(
            config.traded_symbols
        )
        if venue.simulated:
            feed = SyntheticFeed(
                symbols=traded,
                timeframe=base_timeframe,
                timeframes=timeframes,
                warmup_bars=warmup_bars,
                total_bars=warmup_bars + max(iterations, 600) + 20,
                start_ms=clock.now_ms if clock else None,
            )
        else:
            # each timeframe is fetched in its own units, so bar_limit is a
            # per-timeframe requirement (OKX caps one request at ~300 candles)
            feed = LiveFeed(
                venue,
                base_timeframe,
                max(config.universe.bar_limit, 150),
                timeframes=timeframes,
            )
        feed.prime(traded)

        resolved_run_id = run_id or uuid.uuid4().hex[:12]
        ledger = Ledger(
            Path(config.data_dir) / f"ledger.{venue_name}.jsonl",
            run_id=resolved_run_id,
            venue=venue_name,
        )
        risk = RiskEngine(
            config.risk,
            config.kill_switch_path,
            config.onchain,
            time_fn=clock if clock is not None else time.time,
            symbol_tiers=config.symbol_tiers,
        )
        # A simulated venue must continue from the ledger's book, or the two
        # disagree the moment you run twice (the ledger persists, the simulator
        # used to reset).
        if venue.simulated:
            adopted_tags = (
                () if config.risk.account.adopt_existing_positions else ("adopted",)
            )
            carried = ledger.rebuild_book(
                base_ccy=config.app.base_currency,
                initial_cash=venue.book.cash,  # type: ignore[attr-defined]
                venue=venue_name,
                exclude_tags=adopted_tags,
            )
            venue.seed(carried.cash, carried.open_positions())  # type: ignore[attr-defined]

        effective_dry_run = dry_run and not venue.simulated
        oms = OMS(venue, ledger, dry_run=effective_dry_run)

        # The hedge leg needs the ledger (line above) and the same dry-run flag,
        # so it is built HERE rather than next to the primary venue. A hedge venue
        # that cannot be built must NOT take the loop down with it: the spot
        # strategies have nothing to do with carry, and "no swap venue" is a normal
        # state (no keys, venue down, wrong name). The consequence is deliberate -
        # hedge_oms stays None and a carry signal is refused rather than
        # half-executed as a naked long.
        hedge_name = str(getattr(config.app, "hedge_venue", "") or "")
        if hedge_name:
            try:
                raw_hedge = build_venue(config, hedge_name, dry_run=dry_run)
                raw_hedge.connect()
                hedge_venue = raw_hedge
                if not raw_hedge.simulated:
                    hedge_venue = ResilientVenue(raw_hedge, logger=logger)
                hedge_oms = OMS(hedge_venue, ledger, dry_run=effective_dry_run)
                # Carry reads funding for the SPOT symbol, so the feed has to
                # fetch it from the hedge venue (README 42.5). Without this the
                # strategy fail-closes and the pair never opens - a wiring bug
                # that leaves no error, only 0 trades.
                if hasattr(feed, "funding_venue"):
                    feed.funding_venue = hedge_venue
            except Exception as exc:  # noqa: BLE001 - never fatal for the main loop
                logger.warning(
                    "hedge venue unavailable; carry is disabled this run",
                    extra={"extra_fields": {
                        "hedge_venue": hedge_name,
                        "error": f"{type(exc).__name__}: {exc}",
                    }},
                )
                hedge_venue, hedge_oms = None, None

        return cls(
            config=config,
            venue=venue,
            feed=feed,
            strategies=strategies,
            risk=risk,
            oms=oms,
            ledger=ledger,
            logger=logger,
            alerts=Alerts(config.log_dir / "alerts.jsonl"),
            interval=config.loop.poll_seconds if interval is None else interval,
            max_iterations=iterations or config.loop.max_iterations,
            dry_run=effective_dry_run,
            clock=clock,
            baseline_path=Path(config.data_dir) / f"baseline.{venue_name}.json",
            hedge_venue=hedge_venue,
            hedge_oms=hedge_oms,
        )

    # ---- carry pair state --------------------------------------------------
    def _save_carry_pairs(self) -> None:
        """Persist which symbols currently hold a carry pair, and at what size."""
        try:
            self._carry_state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                symbol: {
                    "pair_id": getattr(pair, "pair_id", ""),
                    "base_qty": float(getattr(pair, "base_qty", 0.0) or 0.0),
                }
                for symbol, pair in self._carry_pairs.items()
            }
            self._carry_state_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - never let bookkeeping stop the loop
            self.log.warning(
                "could not persist carry pair state",
                extra={"extra_fields": {"error": f"{type(exc).__name__}: {exc}"}},
            )

    def _load_carry_pairs(self) -> None:
        """Rebuild the pair records a restart would otherwise forget.

        Only the size and the id are needed to close a pair: the executor rebuilds
        `PairLegs` from them, and the venue is the source of truth for what is
        actually open.
        """
        try:
            if not self._carry_state_path.exists():
                return
            payload = json.loads(self._carry_state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        from .execution.paired import PairLegs
        from .models import Order

        for symbol, row in (payload or {}).items():
            qty = float((row or {}).get("base_qty") or 0.0)
            if qty <= 0:
                continue
            self._carry_pairs[symbol] = PairLegs(
                pair_id=str((row or {}).get("pair_id") or "restored"),
                symbol=symbol,
                base_qty=qty,
                spot_order=Order(f"{symbol}-spot", symbol, "buy", qty,
                                 status="filled", filled_qty=qty),
                perp_order=Order(f"{symbol}-perp", f"{symbol}:USDT", "sell", qty,
                                 status="filled", filled_qty=qty),
                contract_ct_val=1.0,
            )
        if self._carry_pairs:
            self.log.info(
                "restored carry pair state",
                extra={"extra_fields": {"pairs": sorted(self._carry_pairs)}},
            )

    # ---- main loop ---------------------------------------------------------
    def run(self) -> RunReport:
        self.log.info(
            "runner start",
            extra={"extra_fields": {
                "run_id": self.ledger.run_id,
                "venue": self.venue.name,
                "simulated": self.venue.simulated,
                "dry_run": self.dry_run,
                "strategies": [s.name for s in self.strategies],
                "symbols": self.symbols,
                "tiers": self.risk.symbol_tiers,
            }},
        )
        max_errors = self.config.risk.circuit.max_tick_errors
        interrupted = False
        try:
            while True:
                if self.max_iterations and self.iterations >= self.max_iterations:
                    break
                if getattr(self.feed, "simulated", False) and self.feed.remaining <= 0:
                    self.log.info("synthetic market exhausted")
                    break
                try:
                    # Per-tick indicator, not a latch: cleared first, set again by
                    # _degrade() if a venue call fails during this tick. It used to
                    # be set to 1 and never cleared, so it read "degraded" forever
                    # after the first network blip.
                    self.metrics.set("ta_venue_degraded", 0.0)
                    self.tick()
                except KillSwitchTriggered:
                    raise
                except VenueDegraded as exc:  # pragma: no cover - tick() handles this
                    self._degrade("tick", exc)
                except Exception as exc:  # noqa: BLE001 - keep a 24/7 loop alive
                    self.tick_errors += 1
                    self.metrics.inc("ta_tick_errors_total")
                    self.log.exception(
                        "tick failed",
                        extra={"extra_fields": {
                            "error": f"{type(exc).__name__}: {exc}",
                            "consecutive": self.tick_errors,
                        }},
                    )
                    if self.tick_errors in (1, 3) or self.tick_errors >= max_errors:
                        self._alert(
                            "critical", "tick failed",
                            error=f"{type(exc).__name__}: {exc}",
                            consecutive=self.tick_errors,
                        )
                    if self.tick_errors >= max_errors:
                        self.log.error(
                            "giving up after %s consecutive tick failures", self.tick_errors
                        )
                        break
                else:
                    self.tick_errors = 0
                    self._ticks_since_reconcile += 1
                    if self._ticks_since_reconcile >= self.reconcile_every_ticks:
                        self._ticks_since_reconcile = 0
                        self.check_reconciliation()
                self.iterations += 1
                if self.interval > 0:
                    time.sleep(self.interval)
        except KeyboardInterrupt:
            interrupted = True
            self.log.warning("interrupted by user")
        except KillSwitchTriggered as exc:
            self.log.warning("kill switch: %s", exc)
        finally:
            report = self.reconcile_and_report()
            if self.alerts:
                self.alerts.send("info", "runner finished", **report.dict())
            if interrupted:
                self.log.info(
                    "stopped cleanly; open positions were left alone (no auto-flatten on Ctrl-C)"
                )
        return report

    # ---- one cycle ---------------------------------------------------------
    def tick(self) -> None:
        feed = self.feed
        if getattr(feed, "simulated", False):
            if self.clock is not None:
                self.clock.advance()
            feed.advance()

        try:
            feed.refresh(self.symbols)
        except VenueDegraded as exc:
            self._degrade("market data", exc)
            return

        marks = {symbol: feed.mark(symbol) for symbol in self.symbols}
        if self.venue.simulated:
            for symbol, price in marks.items():
                if price > 0:
                    self.venue.set_mark(symbol, price)  # type: ignore[attr-defined]
        else:
            try:
                marks.update(self.venue.refresh_marks(self.symbols))
            except VenueDegraded as exc:
                self._degrade("ticker", exc)
                return

        try:
            raw_account = self.venue.get_account()
        except VenueDegraded as exc:
            self._degrade("account", exc)
            return

        self._last_raw_cash = raw_account.cash
        if self.baseline is None and self.baseline_path is not None:
            self.baseline, created = load_or_create(
                self.baseline_path, self.venue.name, raw_account
            )
            if created:
                self.log.info(
                    "venue baseline created",
                    extra={"extra_fields": {
                        "venue": self.venue.name,
                        "cash": raw_account.cash,
                        "positions": {s: round(p.qty, 8) for s, p in raw_account.positions.items()},
                        "file": str(self.baseline_path),
                    }},
                )

        account = self._prepare_risk_account(self._managed_account(raw_account), marks)
        self._last_account = account
        if self.equity_start is None:
            self.equity_start = account.effective_risk_equity
        self._record_metrics(account)

        actions = self.risk.on_tick(account)
        if "kill" in actions:
            self._alert("critical", "kill switch", reason=self.risk.kill_reason)
            self.flatten_all(account, marks, reason="kill switch")
            raise KillSwitchTriggered(self.risk.kill_reason)
        if "flatten" in actions:
            # Once per day: repeating an idle flatten every tick produced ~80
            # critical alerts per 2 hours and kept the loop permanently frozen.
            if self._flatten_day != self._day:
                self._flatten_day = self._day
                self._alert("critical", "daily loss flatten", daily_pnl_pct=account.daily_pnl_pct)
                self.flatten_all(account, marks, reason="daily loss limit")
            else:
                self.metrics.inc("ta_flatten_skipped_total")
            self.ledger.record_equity(account, marks)
            return
        if "pause" in actions:
            self._alert("warning", "trading paused", reason=self.risk.pause_reason)

        self._adopt_positions(account, marks)
        self._manage_exits(account, marks)

        for strategy in self.strategies:
            for symbol in strategy.symbols:
                if self._is_stale(symbol, strategy.timeframe):
                    self.metrics.inc("ta_stale_data_skips_total")
                    continue
                # one decision per closed bar: a 60s poll on 15m candles must not
                # re-decide fifteen times on the same bar
                last_ts = feed.last_bar_ts(symbol, strategy.timeframe)
                key = (strategy.name, symbol)
                if last_ts and self._last_eval.get(key) == last_ts:
                    continue
                self._last_eval[key] = last_ts
                view = MarketView(symbol=symbol, mark=marks.get(symbol, 0.0), source=feed)
                position = account.positions.get(symbol)
                # Count evaluations, not just signals. Without this, "0 signals in
                # 9 hours" is indistinguishable from "the strategy loop silently
                # stopped running" - both produce the same observable state
                # (ta_signals_total absent). `stale_data_skips` covers the one
                # silent-block path we know about; this covers the rest.
                self.metrics.inc("ta_bars_evaluated_total")
                signal = strategy.on_bar(
                    view, position if position and position.qty > 0 else None
                )
                if signal is None:
                    continue
                self.signals_seen += 1
                self.metrics.inc("ta_signals_total")
                if self._handle_signal(strategy, signal, account, marks, view):
                    account = self._managed_account(self.venue.get_account())

        fills = self.oms.sync(self.symbols)
        # The hedge OMS needs the same polling, or its fills NEVER reach the
        # ledger: OKX market orders answer `status=open, filled=0` and settle a
        # moment later, so the fill is only picked up by a later drain - and the
        # hedge OMS was never drained at all. Measured 2026-10-08 on the first
        # live carry pair (ACT): the spot leg was in the ledger, the perp leg was
        # not, which made a delta-neutral pair look like a naked long.
        if self.hedge_oms is not None and self._carry_pairs:
            from .execution.paired import perp_symbol

            hedge_fills = self.hedge_oms.sync(
                [perp_symbol(s) for s in self._carry_pairs]
            )
            fills = list(fills) + list(hedge_fills)
        if fills:
            account = self._managed_account(self.venue.get_account())

        self.ledger.record_equity(account, marks)

    # ---- helpers -----------------------------------------------------------
    def _now_ms(self) -> int:
        return int(self.clock() * 1000) if self.clock is not None else now_ms()

    def _degrade(self, call: str, exc: Exception) -> None:
        """A venue call failed after retries: stop opening risk, stay alive."""
        self.degraded_ticks += 1
        self.metrics.inc("ta_venue_degraded_total")
        self.metrics.set("ta_venue_degraded", 1.0)
        if self.degraded_ticks in (1, 5, 25) or self.degraded_ticks % 50 == 0:
            self._alert(
                "critical",
                "venue degraded - new entries suspended",
                call=call,
                error=str(exc),
                degraded_ticks=self.degraded_ticks,
            )
        cooldown = max(2.0, float(self.config.loop.poll_seconds) / 60.0 * 2.0)
        self.risk.pause(cooldown, f"venue degraded ({call})")

    def _is_stale(self, symbol: str, timeframe: str) -> bool:
        last = self.feed.last_bar_ts(symbol, timeframe)
        if not last:
            return True
        tf_ms = tf_to_ms(timeframe)
        allowed = self.config.risk.circuit.stale_data_bars * tf_ms
        return (self._now_ms() - (last + tf_ms)) > allowed

    def _spread_for(self, symbol: str) -> float | None:
        now = self._now_ms()
        cached = self._spread_cache.get(symbol)
        if cached and now - cached[0] < 60_000:
            return cached[1]
        try:
            value = self.venue.spread_bps(symbol)
        except Exception:  # noqa: BLE001 - a missing spread must not block trading logic
            value = None
        if value is not None:
            self._spread_cache[symbol] = (now, value)
        return value

    def _managed_account(self, raw: AccountState) -> AccountState:
        """Split the account into what the bot opened vs what was already there.

        An OKX demo account ships with 1 BTC / 100 OKB / 1 ETH / 5000 USDT. If
        those count as strategy exposure, every limit is instantly breached and
        the bot can never trade - which is exactly what happened before this.
        """
        # Historical `adopted` fills (from a previous run that took ownership of
        # manual holdings) must NOT make those holdings look managed now. Ignoring
        # them here is what stops the bot from selling coins it never bought.
        adopted_tags = () if self.config.risk.account.adopt_existing_positions else ("adopted",)
        book = self.ledger.rebuild_book(
            base_ccy=self.config.app.base_currency,
            venue=self.ledger.venue,
            exclude_tags=adopted_tags,
        )
        # The ledger stores a swap position in CONTRACTS (张), so its P&L is
        # (mark - avg) * qty * ctVal. Without the contract size the book understates
        # ACT's hedge leg tenfold (its ctVal is 10) and reads correctly for CRV/LTC
        # only because theirs is 1 - measured 2026-10-10. The venue has the
        # catalogue, so ask it; anything unknown stays at 1.0 (the old behaviour).
        for symbol, position in book.positions.items():
            if ":" in symbol:
                position.ct_val = self._contract_size(symbol)
        managed: dict[str, Position] = {}
        unmanaged: dict[str, Position] = {}
        unmanaged_notional = 0.0

        for symbol, position in raw.positions.items():
            ours = book.positions.get(symbol)
            # SIGNED: a hedge leg is negative, and forcing it to 0 here made the
            # runner treat its own carry short as somebody else's position.
            managed_qty = ours.qty if ours else 0.0
            tolerance = max(1e-9, abs(position.qty) * 1e-6)
            if abs(position.qty - managed_qty) <= tolerance:
                managed[symbol] = position
                continue
            if abs(managed_qty) > tolerance:
                managed[symbol] = Position(
                    symbol=symbol,
                    qty=managed_qty,
                    avg_price=(ours.avg_price if ours else position.avg_price) or position.avg_price,
                    opened_ts=(ours.opened_ts if ours else position.opened_ts),
                )
            rest = position.qty - managed_qty
            if rest > tolerance:
                unmanaged[symbol] = Position(
                    symbol=symbol, qty=rest, avg_price=position.avg_price
                )
                unmanaged_notional += rest * (position.avg_price or 0.0)

        for symbol, position in book.open_positions().items():
            if symbol not in raw.positions:
                managed[symbol] = position

        # Rounding residue is not a position. OKX charges spot fees in the base
        # asset, so a "full" exit always leaves a sub-lot remainder (measured:
        # 4.5e-07 OKB against a 0.01 OKB venue minimum). Such a remainder can
        # never be sold, yet `len(account.positions)` is what enforces
        # `max_open_positions` - a few round trips would have quietly filled the
        # position budget and stopped every new entry.
        dust: dict[str, float] = {}
        dust_notional = 0.0
        for symbol in list(managed):
            position = managed[symbol]
            if self._is_unsellable(symbol, position):
                mark = self.feed.mark(symbol) or position.avg_price or 0.0
                dust[symbol] = position.qty
                dust_notional += position.qty * mark
                del managed[symbol]

        raw.positions = managed
        raw.unmanaged_positions = unmanaged
        raw.unmanaged_notional = unmanaged_notional
        raw.dust_positions = dust
        raw.dust_notional = dust_notional
        return raw

    def _is_unsellable(self, symbol: str, position: Position) -> bool:
        """True when the venue would refuse to sell this remainder.

        Deliberately conservative - a false positive would drop a real position
        out of stop management, which is worse than keeping dust. So this only
        fires on the venue's own minimum order size, or on a notional a quarter of
        the bot's own minimum order (a bot position can never be that small).
        """
        # Magnitude, not sign: a SHORT perp leg is a real position (see
        # PositionBook.apply_fill). Reading the signed value here would classify
        # every hedge leg as dust and delete it from managed risk.
        qty = abs(position.qty)
        if qty <= 0:
            return True
        min_qty = self.venue.min_qty(symbol)
        if min_qty > 0 and qty < min_qty:
            return True
        trivial = self.config.risk.per_trade.min_order_notional_usd / 4.0
        mark = self.feed.mark(symbol) or position.avg_price or 0.0
        if mark > 0 and trivial > 0 and qty * mark < trivial:
            return True
        # Last resort when neither a mark nor a venue minimum is available: a
        # remainder this small cannot be worth an order under any price, and
        # leaving it in the book is what produced rejected "sell 6.9e-08" orders.
        if mark <= 0 and qty < 1e-5:
            return True
        return False

    def _prepare_risk_account(
        self, account: AccountState, marks: dict[str, float]
    ) -> AccountState:
        """Define the risk basis: the capital the bot controls, plus the breakers.

        `risk_equity = cash + managed positions marked to market`. Using the raw
        account equity instead means a pre-funded demo balance (or a coin the
        user holds manually) silently scales every position limit.
        """
        from .util import day_key
        from .portfolio import marked_value

        # ONE valuation rule for both the book and the risk basis (see
        # marked_value). This used to be `qty * price` for every position, which
        # subtracted each perp SHORT at full notional: opening a new carry pair
        # moved the daily baseline by ~$2k, the daily-loss breaker read that as a
        # -2% loss and paused all entries (measured 2026-10-09 15:05:56Z), and a
        # slightly larger pair would have crossed -3% into flatten_all.
        exposure = sum(
            marked_value(symbol, position, marks.get(symbol) or position.avg_price)
            for symbol, position in account.positions.items()
        )
        account.risk_equity = max(0.0, account.cash + exposure)
        today = day_key(self._now_ms(), tz_name=self.config.app.timezone)
        if today != self._day or self.day_start_equity <= 0:
            self._day = today
            self.day_start_equity = account.risk_equity
        self.equity_peak = max(self.equity_peak, account.risk_equity)
        account.day_start_equity = self.day_start_equity
        account.peak_equity = self.equity_peak
        return account

    def _handle_signal(
        self,
        strategy: Strategy,
        signal: Signal,
        account: AccountState,
        marks: dict[str, float],
        view: MarketView,
    ) -> bool:
        bars = view.bars(strategy.timeframe)
        mark = marks.get(signal.symbol) or (bars[-1].close if bars else 0.0)
        if mark <= 0:
            return False

        atr_period = int(strategy.param("atr_period", 14))
        atr_value = atr(bars, atr_period) or 0.0
        stop = signal.stop_price
        if stop is None and atr_value > 0:
            stop = mark - atr_value * self.risk.stop_atr_multiple(signal.symbol)
        position = account.positions.get(signal.symbol)

        if signal.exit:
            if not position or position.qty <= 0:
                return False
            # Closing a carry pair is two orders, not one (README 37).
            if signal.tag.startswith("carry") and signal.symbol not in self._carry_pairs:
                # A carry exit with no pair record: the process restarted since the
                # pair opened (the records live in memory only), so closing the
                # spot leg here would leave the perp SHORT naked. Refuse loudly and
                # leave the book alone - a wrong half-trade is worse than a delay.
                self.log.error(
                    "carry exit refused: no pair record for this symbol "
                    "(restart orphans in-memory pair state)",
                    extra={"extra_fields": {"symbol": signal.symbol}},
                )
                self.metrics.inc("ta_carry_orphan_exits_total")
                return False
            if signal.tag.startswith("carry"):
                from .execution.paired import PairedExecutor, PairExecutionError

                if self.hedge_oms is None:
                    return False
                # A FRESH executor is built per call, so its internal counter
                # restarts at 1 - without a real stamp every pair after the first
                # reuses the same client order id and the OMS's duplicate guard
                # rejects it. Measured: 1 pair opened, then 12 consecutive
                # "rejected: duplicate_client_order_id".
                executor = PairedExecutor(
                    self.oms, self.hedge_oms, stamp=lambda: int(self._now_ms())
                )
                pair = self._carry_pairs[signal.symbol]
                try:
                    executor.close_pair(pair)
                except PairExecutionError as exc:
                    self.log.error(
                        "carry close failed",
                        extra={"extra_fields": {"symbol": signal.symbol, "error": str(exc)}},
                    )
                    self.metrics.inc("ta_carry_pair_failed_total")
                    return False
                self._carry_pairs.pop(signal.symbol, None)
                self._save_carry_pairs()
                self.exits.pop(signal.symbol, None)
                self.metrics.inc("ta_carry_pairs_closed_total")
                return True
            request = OrderRequest(
                client_order_id=OMS.new_client_order_id(signal.symbol, f"x{strategy.name}"),
                symbol=signal.symbol,
                side="sell",
                qty=position.qty,
                type="market",
                reduce_only=True,
                tag=f"{strategy.name}:exit",
            )
            order = self._submit(request, account, mark, marks)
            if order is None:
                # blocked: keep the stop armed instead of losing protection
                self.metrics.inc("ta_blocked_exits_total")
                return False
            self.exits.pop(signal.symbol, None)
            return True

        if position and position.qty > 0:
            return False  # long-only spot: no pyramiding

        tier = self.risk.limits_for(signal.symbol)
        scale = vol_target_scale(
            atr_pct(bars, atr_period),
            self.risk.atr_target_pct(signal.symbol),
            self.config.risk.positions.vol_target_min_scale,
            self.config.risk.positions.vol_target_max_scale,
        )
        stop = stop or mark * (1.0 - tier.max_stop_pct / 100.0)
        if signal.tag == "carry":
            # Size the PAIR by notional, not by distance to a price stop. A carry
            # pair is delta-neutral, so "how far is the stop" says nothing about
            # its risk; using it made ACT open at 402,735 (~$3.93k) purely because
            # its stop happened to sit 5.1% below entry. See TierLimits.
            pct = tier.carry_notional_pct or tier.max_order_notional_pct
            qty = carry_pair_qty(account.effective_risk_equity, pct, mark)
        else:
            qty = risk_based_qty(
                account.effective_risk_equity, tier.risk_per_trade_pct, mark, stop, scale
            )
        if qty <= 0:
            return False

        request = OrderRequest(
            client_order_id=OMS.new_client_order_id(signal.symbol, f"e{strategy.name}"),
            symbol=signal.symbol,
            side="buy",
            qty=qty,
            type="market",
            stop_price=stop,
            take_profit=signal.take_profit,
            tag=strategy.name,
        )
        # A carry signal is ONE half of a two-leg trade. Without a configured
        # hedge venue, executing it would leave a naked long - refuse instead of
        # quietly doing the wrong half (README 37.3).
        if signal.tag == "carry":
            if self.hedge_oms is None:
                self.log.warning(
                    "carry signal refused: no hedge venue configured",
                    extra={"extra_fields": {"symbol": signal.symbol}},
                )
                return False
            from .execution.paired import PairedExecutor, PairExecutionError

            # THE RISK GATE COMES FIRST. Opening the pair directly with the
            # sizing number bypassed pre_trade entirely - no
            # max_order_notional_pct, no max_position_pct, no leg ceilings, no
            # position count. Measured 2026-10-08: one carry leg (LTC) went out at
            # $20,889 = 20% of the book against a 4% single-order cap, and the spot
            # side reached ~$75k of notional. `pre_trade` also books the entry
            # against the daily/leg budgets, so it has to be the thing that sets
            # the size.
            #
            # Concurrent-PAIR cap, counted HERE rather than by the risk engine:
            # the engine counts account.positions, and the book only learns about
            # a fill on the NEXT tick's sync, so inside a single tick every
            # qualifying symbol passed the check and the tier's max_positions = 5
            # was overshot (measured: 6 pairs, later 9). The pairs this process
            # knows it opened are the honest count.
            pair_cap = int(getattr(self.risk.limits_for(signal.symbol), "max_positions", 0) or 0)
            if pair_cap and len(self._carry_pairs) >= pair_cap:
                self.log.info(
                    "carry pair cap reached",
                    extra={"extra_fields": {
                        "open_pairs": len(self._carry_pairs), "cap": pair_cap,
                    }},
                )
                return False
            # DEPTH GATE (README 59.9). See _entry_depth_refused - it is shared
            # with every other entry now, not just carry's two legs.
            if self._entry_depth_refused(signal.symbol, qty, "carry"):
                return False
            # BASIS GATE (README 59.14). The pair realises P - S on convergence:
            # a perp BELOW spot is money paid at entry. Measured 2026-10-09: ACT
            # opened 130.6 bps below spot (~$29 on top of fees) and is still
            # climbing out of it. Unlike depth, "cannot assess" does NOT refuse -
            # an unreadable perp quote also means the hedge leg itself cannot be
            # placed, so the pair executor will fail on its own terms.
            basis_cap = float(self.config.risk.per_trade.max_basis_discount_bps or 0.0)
            if basis_cap > 0 and self.hedge_venue is not None:
                from .execution.paired import perp_symbol
                from .execution.sizing import basis_bps

                perp_mark = 0.0
                try:
                    ticker = self.hedge_venue.ex.fetch_ticker(perp_symbol(signal.symbol))
                    perp_mark = float((ticker or {}).get("last") or 0.0)
                except Exception:  # noqa: BLE001 - see note above
                    perp_mark = 0.0
                if perp_mark > 0 and mark > 0:
                    basis = basis_bps(mark, perp_mark)
                    if basis is not None and basis < -basis_cap:
                        self.log.warning(
                            "carry entry refused: perp at a discount to spot",
                            extra={"extra_fields": {
                                "symbol": signal.symbol,
                                "basis_bps": round(basis, 1),
                                "cap_bps": basis_cap,
                            }},
                        )
                        return False
            carry_request = OrderRequest(
                client_order_id=request.client_order_id,
                symbol=signal.symbol,
                side="buy",
                qty=qty,
                type="market",
                stop_price=stop,
                tag="carry",
            )
            # Same call shape as _submit(): min_notional, spread and the armed
            # stops all feed the decision, so the carry leg is measured against
            # exactly what a normal entry would be.
            decision = self.risk.pre_trade(
                carry_request,
                account,
                mark,
                marks,
                min_notional=self.venue.min_notional(signal.symbol),
                stops={s: plan.stop for s, plan in self.exits.items()},
                spread_bps=self._spread_for(signal.symbol),
            )
            if not decision.approved:
                self.metrics.inc("ta_orders_blocked_total")
                self.session_blocked += 1
                self.ledger.record_block(carry_request, decision.reason)
                self.log.info(
                    "carry entry blocked by risk",
                    extra={"extra_fields": {"symbol": signal.symbol, "reason": decision.reason}},
                )
                return False
            qty = decision.qty
            executor = PairedExecutor(
                self.oms, self.hedge_oms, stamp=lambda: int(self._now_ms())
            )
            try:
                passive = self._passive_entry_price(strategy, signal.symbol)
                pair = executor.open_pair(
                    signal.symbol,
                    qty,
                    tag="carry",
                    passive_price=passive,
                    passive_wait_seconds=float(strategy.param("passive_wait_seconds", 6.0)),
                )
            except PairExecutionError as exc:
                self.log.error(
                    "carry pair failed",
                    extra={"extra_fields": {"symbol": signal.symbol, "error": str(exc)}},
                )
                self.metrics.inc("ta_carry_pair_failed_total")
                return False
            self._carry_pairs[signal.symbol] = pair
            self._save_carry_pairs()
            self.metrics.inc("ta_carry_pairs_opened_total")
            return True
        # DEPTH GATE, now on EVERY entry (README 65). It used to sit inside the
        # carry branch only, which left the directional legs unprotected exactly
        # where it matters most: the 2026-10-09 TradFi sleeve trades tokenised
        # equities whose book is one-sided or empty most of the day (17 of 20
        # lines had no ask when measured), and the risk engine's spread check
        # passes when the spread cannot be measured. "Cannot tell" is a refusal.
        if self._entry_depth_refused(signal.symbol, qty, strategy.name):
            return False
        order = self._submit(request, account, mark, marks)
        if order is None or order.status not in ("filled", "open"):
            return False

        entry = order.avg_price or mark
        self.exits[signal.symbol] = ExitPlan(
            symbol=signal.symbol,
            entry=entry,
            stop=stop,
            target=signal.take_profit,
            partial_target=signal.partial_target,
            partial_fraction=signal.partial_fraction,
            breakeven_at_r=signal.breakeven_at_r,
            max_holding_bars=signal.max_holding_bars,
            risk_per_unit=abs(entry - stop),
            trailing_multiple=self.config.risk.positions.trailing_stop_atr_multiple,
        )
        return True

    def _contract_size(self, symbol: str) -> float:
        """One contract of `symbol`, in base units (OKX `contractSize`).

        Spot is 1.0 by definition; a swap leg can be anything (ACT is 10), and the
        ledger stores swaps in contracts, so anything computing P&L needs this.
        Read from whichever venue actually lists the symbol - the hedge venue for
        perps, the spot venue otherwise - and fall back to 1.0 when unknown, which
        is the pre-2026-10-10 behaviour.
        """
        for venue in (self.hedge_venue, self.venue):
            markets = getattr(getattr(venue, "ex", None), "markets", None) or {}
            market = markets.get(symbol) or {}
            try:
                size = float(market.get("contractSize") or 0.0)
            except (TypeError, ValueError):
                size = 0.0
            if size > 0:
                return size
        return 1.0

    def _passive_entry_price(self, strategy: Strategy, symbol: str) -> float | None:
        """The maker price to rest the carry spot leg on, or None to buy at market.

        Off unless `passive_entry` is set on the strategy block: this changes real
        execution, so it is opt-in per strategy rather than a global default.

        The price is the TOUCH (best bid), deliberately not an improvement on it.
        Verified against the venue 2026-10-09 (`scripts/cancel_path_check.py`): a
        post-only order that would take liquidity is accepted and then killed
        (`cancelSource=31`), so bidding through the book does not buy a better
        fill - it buys nothing, and burns the wait.
        """
        if not bool(strategy.param("passive_entry", False)):
            return None
        top = getattr(self.venue, "top_of_book", None)
        if not callable(top):
            return None
        try:
            bid, ask = top(symbol)
        except Exception:  # noqa: BLE001 - no readable book means no maker price
            return None
        if not bid or float(bid) <= 0:
            return None
        if ask and float(ask) > 0 and float(bid) >= float(ask):
            return None  # locked/crossed book: there is no maker price to join
        return float(bid)

    def _entry_depth_refused(self, symbol: str, qty: float, kind: str) -> bool:
        """Refuse an entry the book cannot absorb at an acceptable cost.

        Thin or one-sided books are the normal case for the TradFi sleeve, and a
        market order into one either fails at the venue or fills far from the
        mid - so the check runs before every entry, not only carry's.
        """
        from .execution.sizing import depth_refusal

        cap = float(self.config.risk.per_trade.max_entry_impact_bps or 0.0)
        probe = getattr(self.venue, "entry_impact_bps", None)
        if cap <= 0 or not callable(probe):
            return False
        impact: float | None
        try:
            impact = probe(symbol, qty, "buy")
        except Exception:  # noqa: BLE001 - unknown depth is not a pass
            impact = None
        reason = depth_refusal(impact, cap)
        if reason is None:
            return False
        self.log.warning(
            f"{kind} entry refused: not enough depth",
            extra={"extra_fields": {
                "symbol": symbol,
                "qty": qty,
                "impact_bps": None if impact is None else round(impact, 1),
                "cap_bps": cap,
                "why": reason,
            }},
        )
        return True

    def _submit(self, request: OrderRequest, account: AccountState, mark: float, marks: dict[str, float]):
        stops = {symbol: plan.stop for symbol, plan in self.exits.items()}
        try:
            decision = self.risk.pre_trade(
                request,
                account,
                mark,
                marks,
                min_notional=self.venue.min_notional(request.symbol),
                stops=stops,
                spread_bps=None if request.reduce_only else self._spread_for(request.symbol),
            )
        except VenueDegraded as exc:
            self._degrade("pre-trade data", exc)
            return None
        if decision.rejected:
            self.metrics.inc("ta_orders_blocked_total")
            self.session_blocked += 1
            self.ledger.record_block(request, decision.reason)
            self.log.info(
                "blocked",
                extra={"extra_fields": {"symbol": request.symbol, "reason": decision.reason}},
            )
            return None

        if decision.adjusted and not request.reduce_only:
            request.qty = decision.qty
            self.log.info(
                "size adjusted",
                extra={"extra_fields": {"symbol": request.symbol, "reason": decision.reason}},
            )

        try:
            order = self.oms.submit(request)
        except VenueDegraded as exc:
            self._degrade("submit", exc)
            return None
        self.metrics.inc("ta_orders_submitted_total")
        self.session_orders += 1
        self.log.info(
            "order",
            extra={"extra_fields": {
                "symbol": request.symbol,
                "side": request.side,
                "qty": round(request.qty, 8),
                "status": order.status,
                "tag": request.tag,
                "tier": self.risk.tier_of(request.symbol),
                "note": order.reason or decision.reason,
            }},
        )
        if order.status == "rejected":
            self._alert("warning", "order rejected", symbol=request.symbol, reason=order.reason)
        return order

    def _adopt_positions(self, account: AccountState, marks: dict[str, float]) -> None:
        """Adopt venue positions the agent did not open: give them ATR stops + 2R targets.

        Existing positions (e.g. demo-account seed holdings) live in
        ``account.unmanaged_positions`` because they are not in the local ledger.
        For every whitelisted holding that has no exit plan yet we seed the same
        stop/target geometry used on agent-opened entries, and (for unmanaged
        ones) record an ``adopted`` ledger entry so the position becomes
        "managed" on the next tick and is protected by ``_manage_exits``.

        OFF BY DEFAULT. Adoption means the bot will eventually SELL that holding
        when the stop or target is hit - fine if you deliberately hand it your
        bag to protect, dangerous if it is a deposit you forgot about. Turn it on
        with ``[account] adopt_existing_positions = true``.
        """
        if not self.config.risk.account.adopt_existing_positions:
            return
        from .models import Fill

        limits = self.config.risk.positions
        whitelist = set(getattr(self.config.risk.symbols, "whitelist", []) or [])
        candidates: dict[str, object] = {}
        candidates.update(getattr(account, "positions", {}) or {})
        candidates.update(getattr(account, "unmanaged_positions", {}) or {})

        for symbol, position in candidates.items():
            if getattr(position, "qty", 0) <= 0:
                continue
            if whitelist and symbol not in whitelist:
                continue
            if symbol in self.exits:
                continue
            mark = marks.get(symbol) or 0.0
            if mark <= 0:
                continue
            bars = self.feed.bars(symbol)
            atr_value = atr(bars, 14) or 0.0
            if atr_value <= 0:
                continue
            stop = mark - atr_value * float(limits.stop_atr_multiple)
            risk = mark - stop
            if risk <= 0:
                continue
            target = mark + risk * float(limits.take_profit_r_multiple)
            entry = float(getattr(position, "avg_price", 0.0) or mark)
            self.exits[symbol] = ExitPlan(
                symbol=symbol,
                entry=entry,
                stop=stop,
                target=target,
                risk_per_unit=risk,
                trailing_multiple=limits.trailing_stop_atr_multiple,
            )
            # Register pre-existing holdings in the local book so they are
            # treated as managed (and reconciled cleanly) from the next tick.
            is_unmanaged = symbol in (getattr(account, "unmanaged_positions", {}) or {})
            if is_unmanaged:
                self.ledger.record_fill(
                    Fill(
                        client_order_id=f"adopt-{symbol}-{int(self._now_ms())}",
                        symbol=symbol,
                        side="buy",
                        qty=float(position.qty),
                        price=entry,
                        fee=0.0,
                        fee_ccy=self.config.app.base_currency,
                        ts=int(self._now_ms()),
                        tag="adopted",
                    )
                )
            self.log.info(
                "adopted position",
                extra={"extra_fields": {
                    "symbol": symbol,
                    "qty": round(float(position.qty), 8),
                    "entry": round(entry, 6),
                    "stop": round(stop, 6),
                    "target": round(target, 6),
                    "unmanaged": is_unmanaged,
                }},
            )

    def _manage_exits(self, account: AccountState, marks: dict[str, float]) -> None:
        limits = self.config.risk.positions
        for symbol, position in list(account.positions.items()):
            if position.qty <= 0:
                continue
            mark = marks.get(symbol) or 0.0
            if mark <= 0:
                continue
            plan = self.exits.get(symbol)
            if plan is None:
                continue
            bars = self.feed.bars(symbol, self._timeframe_for(symbol))
            atr_value = atr(bars, 14) or 0.0

            # 1. move the stop to breakeven once the trade has paid for itself
            if (
                plan.breakeven_at_r
                and not plan.breakeven_moved
                and plan.risk_per_unit > 0
                and mark >= plan.entry + plan.breakeven_at_r * plan.risk_per_unit
            ):
                plan.stop = max(plan.stop, plan.entry)
                plan.breakeven_moved = True
                self._alert(
                    "info", "stop moved to breakeven", symbol=symbol, stop=round(plan.stop, 8)
                )

            # 2. scale out part of the position at the first target
            if (
                plan.partial_target
                and not plan.partial_taken
                and mark >= plan.partial_target
                and position.qty * plan.partial_fraction > 0
            ):
                qty = position.qty * plan.partial_fraction
                # never scale out into dust the venue will refuse to sell later
                if (position.qty - qty) * mark < self.venue.min_notional(symbol):
                    qty = position.qty
                request = OrderRequest(
                    client_order_id=OMS.new_client_order_id(symbol, "part"),
                    symbol=symbol,
                    side="sell",
                    qty=qty,
                    type="market",
                    reduce_only=True,
                    tag="partial",
                )
                if self._submit(request, account, mark, marks) is not None:
                    plan.partial_taken = True
                    plan.stop = max(plan.stop, plan.entry)
                    self._alert(
                        "info", "partial take profit",
                        symbol=symbol, qty=round(qty, 8), price=round(mark, 8),
                    )

            # 3. trail the remainder
            if limits.trailing_stop_enabled and atr_value > 0:
                plan.stop = max(
                    plan.stop,
                    trailing_stop_price(plan.stop, mark, atr_value, plan.trailing_multiple),
                )

            # 4. exit conditions
            reason = ""
            stop_hit = mark <= plan.stop
            if stop_hit:
                reason = f"stop {plan.stop:.6f}"
            elif plan.target and mark >= plan.target:
                reason = f"target {plan.target:.6f}"
            elif plan.max_holding_bars:
                held = self._bars_held(symbol, position)
                if held is not None and held >= plan.max_holding_bars:
                    reason = f"time stop {held} bars"
            if not reason:
                continue

            request = OrderRequest(
                client_order_id=OMS.new_client_order_id(symbol, "exit"),
                symbol=symbol,
                side="sell",
                qty=position.qty,
                type="market",
                reduce_only=True,
                tag="stop" if stop_hit else "exit",
            )
            order = self._submit(request, account, mark, marks)
            if order is None:
                # the exit was blocked: keep the plan so protection stays armed
                self.metrics.inc("ta_blocked_exits_total")
                continue
            self.exits.pop(symbol, None)
            if stop_hit:
                self.risk.note_stop_out(symbol)
                self.metrics.inc("ta_stop_outs_total")
            elif plan.target and mark >= plan.target:
                self.risk.note_win(symbol)
            self._alert("warning", "protective exit", symbol=symbol, reason=reason)

    def _timeframe_for(self, symbol: str) -> str:
        for strategy in self.strategies:
            if symbol in strategy.symbols:
                return strategy.timeframe
        return self.config.universe.timeframe

    def _bars_held(self, symbol: str, position: Position) -> int | None:
        if not position.opened_ts:
            return None
        timeframe = self._timeframe_for(symbol)
        bars = self.feed.bars(symbol, timeframe)
        if not bars:
            return None
        return int(max(0, bars[-1].ts - position.opened_ts) // tf_to_ms(timeframe))

    def flatten_all(self, account: AccountState, marks: dict[str, float], reason: str) -> None:
        try:
            self.oms.cancel_all(self.symbols)
        except VenueDegraded as exc:
            self._degrade("cancel_all", exc)
            return
        for symbol, position in list(account.positions.items()):
            if position.qty <= 0:
                continue
            mark = marks.get(symbol) or self.feed.mark(symbol)
            if mark <= 0:
                continue
            request = OrderRequest(
                client_order_id=OMS.new_client_order_id(symbol, "flat"),
                symbol=symbol,
                side="sell",
                qty=position.qty,
                type="market",
                reduce_only=True,
                tag=f"flatten:{reason}",
            )
            self._submit(request, account, mark, marks)
            self.exits.pop(symbol, None)
        if self.alerts:
            self.alerts.send("critical", "flattened", reason=reason)
        self.ledger.record_risk("flatten_all", reason=reason)

    def _on_fill(self, fill) -> None:
        self.session_fills += 1
        self.session_fees += fill.fee
        self.metrics.inc("ta_fills_total")
        plan = self.exits.get(fill.symbol)
        if plan is not None and fill.side == "buy":
            plan.entry = fill.price or plan.entry
            plan.risk_per_unit = abs(plan.entry - plan.stop)
        self.log.info(
            "fill",
            extra={"extra_fields": {
                "symbol": fill.symbol,
                "side": fill.side,
                "qty": round(fill.qty, 8),
                "price": round(fill.price, 6),
                "fee": round(fill.fee, 6),
                "tag": fill.tag,
            }},
        )

    def _record_metrics(self, account: AccountState) -> None:
        self.metrics.set("ta_equity", account.equity)
        self.metrics.set("ta_cash", account.cash)
        self.metrics.set("ta_drawdown_pct", account.drawdown_pct)
        self.metrics.set("ta_daily_pnl_pct", account.daily_pnl_pct)
        self.metrics.set("ta_open_positions", len(account.positions))
        # Straight from the ledger: the live venue never fills in
        # AccountState.fees_paid, so this metric used to read 0.00 while fees were
        # 45% of the day's loss.
        self.metrics.set("ta_fees_total", self.ledger.fees_paid)
        # The honest answer to "is anything blocking new entries right now".
        self.metrics.set(
            "ta_entries_paused", 1.0 if getattr(self.risk, "paused", False) else 0.0
        )
        self.metrics.set("ta_dust_notional", account.dust_notional)
        self.metrics.set("ta_kill_switch", 1.0 if self.risk.killed else 0.0)
        self.metrics.set("ta_unmanaged_notional", account.unmanaged_notional)
        self.metrics.set(
            "ta_heat_pct",
            self.risk.portfolio_heat_pct(
                account, self._marks_for_heat(account), {s: p.stop for s, p in self.exits.items()}
            ),
        )
        self.metrics.set("ta_entries_today", self.risk.stats()["entries_today"])
        self.metrics.inc("ta_ticks_total")

    def _marks_for_heat(self, account: AccountState) -> dict[str, float]:
        return {s: self.feed.mark(s) for s in account.positions}

    def _alert(self, level: str, title: str, **fields: Any) -> None:
        if self.alerts:
            self.alerts.send(level, title, **fields)
        log = self.log.warning if level in ("warning", "critical") else self.log.info
        log("%s", title, extra={"extra_fields": fields})
        self.ledger.record_risk(title, **fields)

    def check_reconciliation(self) -> bool:
        """Compare the managed book to the venue WHILE the loop is running.

        Returns True when the two agree. A mismatch that repeats unchanged is
        counted but not re-alerted, so a persistent drift cannot recreate the
        alert-spam failure that the flatten breaker had.
        """
        try:
            raw = self.venue.get_account()
            local_book = self.ledger.rebuild_book(
                base_ccy=self.config.app.base_currency, venue=self.ledger.venue
            )
            report = self.oms.reconcile(
                raw,
                local_book,
                # Same basis as the comparison: cashBal when the baseline has it,
                # otherwise the legacy `cash` (identical on this account, whose
                # baseline predates any open swap).
                cash_baseline=(
                    (self.baseline.cash_bal or self.baseline.cash) if self.baseline else None
                ),
            )
        except Exception as exc:  # noqa: BLE001 - never let the watchdog kill the loop
            self.metrics.inc("ta_reconcile_skipped_total")
            self.log.warning(
                "periodic reconciliation skipped",
                extra={"extra_fields": {"error": f"{type(exc).__name__}: {exc}"}},
            )
            return True

        if report.ok:
            self._reconcile_signature = ""
            return True

        signature = " | ".join(report.diffs)
        if signature == self._reconcile_signature:
            self.metrics.inc("ta_reconcile_repeat_total")
            return False
        self._reconcile_signature = signature
        self.metrics.inc("ta_reconcile_mismatch_total")
        self._alert("warning", "reconciliation mismatch", source="periodic", diffs=report.diffs)
        return False

    # ---- reporting ---------------------------------------------------------
    def reconcile_and_report(self) -> RunReport:
        """Never lose the report to a second failure in the shutdown path."""
        account = self._last_account
        reconciliation = None
        try:
            raw = self.venue.get_account()
            self._last_raw_cash = raw.cash
            account = self._managed_account(raw)
        except Exception as exc:  # noqa: BLE001 - a dead venue must not hide the report
            self.log.error(
                "could not read the venue at shutdown, reporting last known state",
                extra={"extra_fields": {"error": f"{type(exc).__name__}: {exc}"}},
            )
            if account is None:
                account = AccountState(
                    venue=self.venue.name,
                    base_ccy=self.config.app.base_currency,
                    cash=self.equity_start or 0.0,
                    equity=self.equity_start or 0.0,
                    stale=True,
                )

        marks = {s: self.feed.mark(s) for s in self.symbols}
        try:
            self.ledger.record_equity(account, marks)
        except Exception:  # noqa: BLE001
            pass

        try:
            local_book = self.ledger.rebuild_book(
                base_ccy=self.config.app.base_currency, venue=self.ledger.venue
            )
            reconciliation = self.oms.reconcile(
                account,
                local_book,
                cash_baseline=self.baseline.cash if self.baseline else None,
            )
        except Exception as exc:  # noqa: BLE001
            self.log.warning(
                "reconciliation skipped",
                extra={"extra_fields": {"error": f"{type(exc).__name__}: {exc}"}},
            )

        if reconciliation is not None and not reconciliation.ok:
            self._alert("warning", "reconciliation mismatch", diffs=reconciliation.diffs)

        stats = self.ledger.stats(run_id=self.ledger.run_id)
        # Return and drawdown must be measured on the SAME basis: the capital the
        # bot controls (cash + managed positions). Mixing "risk equity" for the
        # denominator with total account equity for the numerator reported
        # +1986% on a demo account holding 1 BTC the bot never bought.
        risk_now = account.effective_risk_equity or account.equity or 1.0
        start = self.equity_start or risk_now
        win_rate = 100.0 * account.wins / account.trades if account.trades else 0.0
        peak_dd = (
            100.0 * (self.equity_peak - risk_now) / self.equity_peak
            if self.equity_peak > 0
            else 0.0
        )
        heat = self.risk.portfolio_heat_pct(
            account, marks, {s: p.stop for s, p in self.exits.items()}
        )
        report = RunReport(
            iterations=self.iterations,
            equity=account.equity,
            risk_equity=risk_now,
            cash=account.cash,
            return_pct=100.0 * (risk_now / start - 1.0),
            peak_drawdown_pct=peak_dd,
            drawdown_pct=account.drawdown_pct,
            daily_pnl_pct=account.daily_pnl_pct,
            open_positions=len(account.positions),
            orders_submitted=self.session_orders,
            orders_blocked=self.session_blocked,
            fills=self.session_fills,
            fees_paid=round(self.session_fees, 8),
            trades=account.trades,
            win_rate_pct=win_rate,
            killed=self.risk.killed,
            kill_reason=self.risk.kill_reason,
            run_id=self.ledger.run_id,
            venue=self.venue.name,
            managed_notional=round(account.gross_exposure, 4),
            unmanaged_notional=round(account.unmanaged_notional, 4),
            heat_pct=round(heat, 4),
            degraded_ticks=self.degraded_ticks,
            venue_retries=getattr(self.venue, "retries", 0),
            reconciled=bool(reconciliation and reconciliation.ok),
            reconciliation_diffs=list(reconciliation.diffs) if reconciliation else [],
            block_reasons=dict(stats.get("blocked_by_reason", {})),
        )
        self.log.info("run report", extra={"extra_fields": report.dict()})
        return report

    def close(self) -> None:
        try:
            self.venue.close()
        except Exception:  # noqa: BLE001
            pass
