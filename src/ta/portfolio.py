"""Position book + append-only ledger.

The ledger is the local book of record. Reconciliation = compare this book
against the venue's own statement. Never trust only one of them.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import AccountState, Fill, Position
from .util import day_key, now_ms


def _fill_from_event(event: dict[str, Any]) -> Fill:
    return Fill(
        client_order_id=str(event.get("client_order_id", "")),
        symbol=str(event.get("symbol", "")),
        side=str(event.get("side", "buy")),  # type: ignore[arg-type]
        qty=float(event.get("qty", 0.0)),
        price=float(event.get("price", 0.0)),
        fee=float(event.get("fee", 0.0)),
        fee_ccy=str(event.get("fee_ccy", "USDT")),
        ts=int(event.get("ts", 0)),
        tag=str(event.get("tag", "")),
    )


def _is_perp(symbol: str) -> bool:
    """Perpetuals carry the settlement suffix (`BTC/USDT:USDT`).

    The same shape the risk engine's `leg_of` keys on, so a symbol is classified
    identically everywhere.
    """
    return ":" in symbol


def marked_value(symbol: str, position: "Position", price: float) -> float:
    """How ONE position contributes to equity, in one place.

    Spot contributes its notional; a perp contributes only its UNREALISED PnL
    (its notional is ~34x this account's equity on the historical legs).

    This is a shared function on purpose. The book has always valued swaps this
    way, but the runner's risk basis valued EVERY position as `qty * price`, so a
    short hedge was subtracted at full notional there and at PnL here. Measured
    2026-10-09: the two calendars differed by $4,823.79 - exactly the sum of the
    three hedge legs - and since the daily-loss breaker compares the runner's
    figure against its own day-start baseline, OPENING A NEW PAIR read as a ~2%
    daily loss and paused every entry (a bigger pair would have crossed -3% and
    flattened the book). One definition, used twice, is the fix.
    """
    if _is_perp(symbol):
        return position.unrealized(price)
    return position.qty * price


@dataclass
class PositionBook:
    """Average-cost spot position accounting, shared by simulator and ledger replay."""

    base_ccy: str = "USDT"
    cash: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    marks: dict[str, float] = field(default_factory=dict)
    realized_total: float = 0.0
    realized_today: float = 0.0
    fees_paid: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    day: str = ""
    day_start_equity: float = 0.0
    peak_equity: float = 0.0

    # ---- marks -------------------------------------------------------------
    def mark(self, symbol: str, price: float) -> None:
        if price and price > 0:
            self.marks[symbol] = float(price)

    def mark_of(self, symbol: str) -> float:
        return self.marks.get(symbol, 0.0)

    # ---- valuation ---------------------------------------------------------
    def equity(self) -> float:
        value = self.cash
        for symbol, position in self.positions.items():
            if position.qty == 0:
                continue
            price = self.mark_of(symbol) or position.avg_price
            # A swap contributes its UNREALISED PnL, not its notional - see
            # marked_value(), which the runner's risk basis now shares so the two
            # cannot drift apart again.
            value += marked_value(symbol, position, price)
        return value

    def unrealized(self) -> float:
        return sum(
            p.unrealized(self.mark_of(s) or p.avg_price)
            for s, p in self.positions.items()
            if p.qty != 0
        )

    def gross_exposure(self) -> float:
        return sum(
            abs(p.qty) * (self.mark_of(s) or p.avg_price)
            for s, p in self.positions.items()
            if p.qty != 0
        )

    def open_positions(self) -> dict[str, Position]:
        """Every non-zero position, LONG OR SHORT.

        While a short was impossible (see `apply_fill`) `qty > 0` and `qty != 0`
        were the same filter; they are not any more, and keeping the signed one
        would hide exactly the hedge leg this book exists to track.
        """
        return {s: p for s, p in self.positions.items() if p.qty != 0}

    # ---- day handling ------------------------------------------------------
    def roll_day(self, today: str | None = None) -> bool:
        key = today or day_key()
        if not self.day:
            self.day = key
            self.day_start_equity = self.equity()
            return False
        if key != self.day:
            self.day = key
            self.realized_today = 0.0
            self.day_start_equity = self.equity()
            self.consecutive_losses = 0
            return True
        return False

    def snapshot_extremes(self) -> None:
        equity = self.equity()
        if equity > self.peak_equity:
            self.peak_equity = equity
        if not self.day:
            self.roll_day()

    # ---- fills -------------------------------------------------------------
    def apply_funding(self, amount: float) -> float:
        """Funding is realised cash, not a trade.

        OKX settles funding three times a day on every open swap and moves cash by
        it - verified 2026-10-08: consecutive swap bills' `bal` delta equals exactly
        (pnl + fee), funding included. The book recorded none of it, so the number
        the reconciler compares against the venue was permanently short (measured:
        +$12.63 across 11 settlements, small but it accrues for as long as a hedge
        is held).
        """
        value = float(amount)
        if not value:
            return 0.0
        self.cash += value
        self.realized_total += value
        self.realized_today += value
        return value

    def apply_fill(self, fill: Fill) -> float:
        """Return realized PnL (net of fee) produced by this fill.

        SPOT and PERPETUAL fills move cash differently on purpose. A spot trade
        moves its full notional; a swap does NOT - no notional changes hands, only
        realised PnL and the fee do, and the exposure lives in the position. The
        book's swap legs are the carry hedge, so pricing them like spot is how
        every short got dropped (below) and, once shorts were allowed, would have
        subtracted ~$3.4M of notional from a $101k account.
        """
        price = float(fill.price)
        qty = float(fill.qty)
        fee = float(fill.fee)
        if qty <= 0 or price <= 0:
            return 0.0

        # Adoption entries represent pre-existing positions the agent inherited
        # (e.g. a demo-account seed balance). They register the position for
        # risk/exit management WITHOUT touching cash, fees, or trade stats,
        # because the cash for those holdings was never part of the bot's book.
        if fill.tag == "adopted":
            position = self.positions.setdefault(fill.symbol, Position(symbol=fill.symbol))
            if position.qty <= 0:
                position.qty = qty
                position.avg_price = price
                position.tag = fill.tag
                if position.opened_ts == 0:
                    position.opened_ts = fill.ts or now_ms()
            return 0.0

        self.fees_paid += fee
        position = self.positions.setdefault(fill.symbol, Position(symbol=fill.symbol))
        realized = 0.0

        if fill.side == "buy":
            perp = _is_perp(fill.symbol)
            new_qty = position.qty + qty
            if perp:
                # Buying back a SHORT realises the difference. Booking that as
                # zero would make a round-tripped hedge look free. An opening or
                # adding long costs only its fee.
                covered = min(qty, max(-position.qty, 0.0))
                realized = (position.avg_price - price) * covered - fee
                self.cash += realized
                if position.qty <= 0 < new_qty:
                    position.avg_price = price
                elif position.qty > 0:
                    position.avg_price = (
                        (position.avg_price * position.qty + price * qty) / new_qty
                    )
            else:
                notional = qty * price
                self.cash -= notional + fee
                if new_qty > 0:
                    position.avg_price = (
                        (position.avg_price * position.qty + price * qty) / new_qty
                    )
            if realized:
                # Buying back a SHORT realises the difference. Booking that as
                # zero would make a round-tripped hedge look free.
                self.realized_total += realized
                self.realized_today += realized
                self._score_trade(realized)
            position.qty = new_qty
            if position.opened_ts == 0:
                position.opened_ts = fill.ts or now_ms()
            if fill.tag and not position.tag:
                position.tag = fill.tag
        else:
            # A sell on a PERPETUAL is how a SHORT is opened, so it has to be able
            # to take the position below zero. Clamping every sell to what is held
            # is right for spot ("you cannot sell what you do not have") and
            # silently wrong for the swap leg. Measured 2026-10-08: the BAND carry
            # hedge (sell 4431 BAND-USDT-SWAP) became `min(4431, 0) = 0`, so the
            # pair never registered a hedge leg, the runner re-sent it on every
            # restart (six orders, ~$1,600 notional) and reconciliation mismatched
            # every 52 minutes - while the book showed a position that was simply
            # the running sum of the BUY legs.
            perp = _is_perp(fill.symbol)
            sell_qty = qty if perp else min(qty, position.qty)
            closing = min(sell_qty, max(position.qty, 0.0))
            opening = sell_qty - closing
            notional = sell_qty * price
            realized = (price - position.avg_price) * closing - fee
            self.cash += realized if perp else notional - fee
            position.qty -= sell_qty
            if opening > 0:
                # Entry price of the short: weighted over what is held short now.
                held = max(-position.qty - opening, 0.0)
                total = held + opening
                position.avg_price = (
                    (position.avg_price * held + price * opening) / total if total > 0 else price
                )
            self.realized_total += realized
            self.realized_today += realized
            if abs(position.qty) <= 1e-12:
                position.qty = 0.0
                position.stop_price = None
                position.take_profit = None
            self._score_trade(realized)

        self.trades += 1
        return realized

    def _score_trade(self, realized: float) -> None:
        if realized > 0:
            self.wins += 1
            self.consecutive_losses = 0
        elif realized < 0:
            self.losses += 1
            self.consecutive_losses += 1

    # ---- view --------------------------------------------------------------
    def account(self, venue_name: str) -> AccountState:
        positions = {s: p for s, p in self.positions.items() if p.qty > 0}
        return AccountState(
            venue=venue_name,
            base_ccy=self.base_ccy,
            cash=self.cash,
            equity=self.equity(),
            positions=positions,
            realized_pnl_total=self.realized_total,
            realized_pnl_today=self.realized_today,
            day_start_equity=self.day_start_equity,
            peak_equity=self.peak_equity,
            consecutive_losses=self.consecutive_losses,
            fees_paid=self.fees_paid,
            trades=self.trades,
            wins=self.wins,
            losses=self.losses,
        )

    @classmethod
    def replay(
        cls, fills: Iterable[Fill], base_ccy: str = "USDT", initial_cash: float = 0.0
    ) -> "PositionBook":
        book = cls(base_ccy=base_ccy, cash=initial_cash)
        for fill in fills:
            book.mark(fill.symbol, fill.price)
            book.apply_fill(fill)
        return book


class Ledger:
    """Append-only JSONL event log. Treat it as the audit trail: never rewrite history.

    History is replayed on construction, so a restart still refuses to resend a
    client order id that was already used, and stats stay continuous.

    Every event is stamped with `run_id` and `venue`. Without that, a paper run on
    a simulated clock and a demo run on the real clock end up in one file and the
    reports/reconciliation silently mix two different accounts.
    """

    def __init__(self, path: str | Path, *, run_id: str | None = None, venue: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.venue = venue or "unknown"
        self.events: list[dict[str, Any]] = []
        self._order_ids: set[str] = set()
        self._blocked: Counter[str] = Counter()
        self._fills: list[Fill] = []
        self._fees = 0.0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.events.append(event)
                kind = event.get("kind")
                if kind == "order":
                    self._order_ids.add(str(event.get("client_order_id", "")))
                elif kind == "fill":
                    fill = _fill_from_event(event)
                    self._fills.append(fill)
                    self._fees += fill.fee
                elif kind == "blocked":
                    self._blocked[str(event.get("reason", "")).split(":")[0]] += 1

    def reload(self) -> int:
        """Re-read the file, keeping this process's own state consistent.

        2026-10-12 (the reconciliation residual, finally): the ledger is APPEND-ONLY
        and **several processes write to it** - the runner, `sync_funding.py` (hourly
        launchd job), the daily job. `__init__` reads it once, so a long-running
        runner could not see the funding rows written by anyone else: its
        `rebuild_book()` flow stayed frozen at startup while the venue kept receiving
        funding, and every bill looked like a fresh "cash residual". That is why the
        watchdog flipped between info and warn for days, and why the residual kept
        "drifting" without a trade in sight.

        Reloading gives a **superset** of what we had (our own writes are in the file
        too), so it is safe to call before any read-only derivation. Returns the
        number of events now held.
        """
        self.events = []
        self._order_ids = set()
        self._blocked = Counter()
        self._fills = []
        self._fees = 0.0
        self._load()
        return len(self.events)

    # ---- writing -----------------------------------------------------------
    def record(self, kind: str, **payload: Any) -> dict[str, Any]:
        event = {"ts": now_ms(), "kind": kind, "run_id": self.run_id, "venue": self.venue, **payload}
        self.events.append(event)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return event

    def record_order(self, order, extra: dict[str, Any] | None = None) -> None:
        self._order_ids.add(order.client_order_id)
        self.record("order", **order.dict(), **(extra or {}))

    def record_fill(self, fill: Fill) -> None:
        self._fills.append(fill)
        self._fees += fill.fee
        self.record("fill", **fill.dict())

    def record_funding(
        self, symbol: str, amount: float, bill_id: str = "", ccy: str = "USDT",
        bill_ts: int | None = None,
    ) -> None:
        """One settled funding payment, identified by the venue's own bill id so a
        re-run of the sync can never double-count it.

        `bill_ts` is the venue's own settlement time, which is NOT the same as the
        row's `ts` (the moment we wrote it). 2026-10-12: without it a batch sync looks
        like income earned on the day it was written - the 10-11 review counted
        $233.24 settled days earlier as that day's funding. Rows written before this
        field existed simply have no `bill_ts`; readers must treat that as unknown.
        """
        extra = {"bill_ts": int(bill_ts)} if bill_ts else {}
        self.record(
            "funding", symbol=symbol, amount=float(amount), ccy=ccy, bill_id=bill_id,
            **extra,
        )

    def funding_bill_ids(self) -> set[str]:
        return {
            str(e.get("bill_id"))
            for e in self.events
            if e.get("kind") == "funding" and e.get("bill_id")
        }

    @property
    def fees_paid(self) -> float:
        """Lifetime fees recorded for this venue (all runs, straight from fills).

        `ta_fees_total` used to read `AccountState.fees_paid`, which the live venue
        never populates - so the metric reported 0.00 forever while fees were the
        single biggest cost (measured 2026-09-30: $28.89 of a $63.50 loss, 45%).
        The ledger is the authoritative record, so the metric reads it instead.
        """
        return self._fees

    def record_block(self, request, reason: str, **extra: Any) -> None:
        self._blocked[reason.split(":")[0]] += 1
        self.record("blocked", request=request.dict(), reason=reason, **extra)

    def record_risk(self, action: str, **payload: Any) -> None:
        self.record("risk", action=action, **payload)

    def record_equity(self, account: AccountState, marks: dict[str, float] | None = None) -> None:
        self.record(
            "equity",
            equity=round(account.equity, 6),
            cash=round(account.cash, 6),
            # The venue's own `cashBal`, recorded from 2026-10-11 because `cash`
            # (ccxt's `eq`) is cashBal PLUS the open swaps' unrealised PnL - so a
            # cash-residual series built on it moves with the mark price and cannot
            # answer "did cash actually change". The reconciler already compares
            # against cashBal; without this field there was no history to compare to.
            cash_bal=round(float(getattr(account, "cash_bal", 0.0) or 0.0), 6),
            realized_today=round(account.realized_pnl_today, 6),
            drawdown_pct=round(account.drawdown_pct, 4),
            positions={s: round(p.qty, 10) for s, p in account.positions.items()},
            marks={k: round(v, 8) for k, v in (marks or {}).items()},
        )

    # ---- reading -----------------------------------------------------------
    def has_order(self, client_order_id: str) -> bool:
        return client_order_id in self._order_ids

    @property
    def fills(self) -> list[Fill]:
        return list(self._fills)

    def fills_for(
        self,
        run_id: str | None = None,
        venue: str | None = None,
        exclude_tags: tuple[str, ...] = (),
    ) -> list[Fill]:
        if run_id is None and venue is None and not exclude_tags:
            return list(self._fills)
        return [
            _fill_from_event(e)
            for e in self.events_for(run_id, venue, exclude_tags)
            if e.get("kind") == "fill"
        ]

    def events_for(
        self,
        run_id: str | None = None,
        venue: str | None = None,
        exclude_tags: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        """Fill and funding events, filtered the way `fills_for` filters fills.

        Kept separate so `fills_for` keeps returning `Fill` objects for its
        existing callers while the book rebuild can also see funding.
        """
        out: list[dict[str, Any]] = []
        for event in self.events:
            if event.get("kind") not in ("fill", "funding"):
                continue
            if run_id is not None and event.get("run_id") != run_id:
                continue
            if venue is not None and event.get("venue") != venue:
                continue
            if exclude_tags and str(event.get("tag", "")) in exclude_tags:
                continue
            out.append(event)
        return out

    def cash_flow(self, run_id: str | None = None, venue: str | None = None) -> float:
        """Signed cash effect of our fills (buys leave cash, sells bring it back)."""
        flow = 0.0
        for fill in self.fills_for(run_id, venue):
            if fill.side == "buy":
                flow -= fill.notional + fill.fee
            else:
                flow += fill.notional - fill.fee
        return flow

    def stats(self, run_id: str | None = None) -> dict[str, Any]:
        def selected(event: dict[str, Any]) -> bool:
            return run_id is None or event.get("run_id") == run_id

        events = [e for e in self.events if selected(e)]
        equity_points = [e["equity"] for e in events if e["kind"] == "equity"]
        peak = max(equity_points) if equity_points else 0.0
        last = equity_points[-1] if equity_points else 0.0
        dd = 100.0 * (peak - last) / peak if peak > 0 else 0.0
        fills = self.fills_for(run_id=run_id)
        blocked = Counter(
            str(e.get("reason", "")).split(":")[0] for e in events if e["kind"] == "blocked"
        )
        return {
            "events": len(events),
            "orders_submitted": len([e for e in events if e["kind"] == "order"]),
            "fills": len(fills),
            "fees_paid": round(sum(f.fee for f in fills), 6),
            "blocked_total": sum(blocked.values()),
            "blocked_by_reason": dict(blocked),
            "equity_last": round(last, 6),
            "equity_peak": round(peak, 6),
            "drawdown_pct": round(dd, 4),
            "risk_actions": [e.get("action") for e in events if e["kind"] == "risk"],
            "run_id": run_id or "all",
        }

    def rebuild_book(
        self,
        base_ccy: str = "USDT",
        initial_cash: float = 0.0,
        run_id: str | None = None,
        venue: str | None = None,
        exclude_tags: tuple[str, ...] = (),
    ) -> PositionBook:
        """Reproduce local positions from the ledger - used for reconciliation.

        Replays fills AND funding. Funding is cash with no position attached, so a
        fills-only replay quietly under-reports the flow the reconciler compares
        against the venue.
        """
        book = PositionBook(base_ccy=base_ccy, cash=initial_cash)
        for event in self.events_for(run_id, venue, exclude_tags):
            if event.get("kind") == "funding":
                book.apply_funding(float(event.get("amount") or 0.0))
                continue
            fill = _fill_from_event(event)
            book.mark(fill.symbol, fill.price)
            book.apply_fill(fill)
        return book

    @staticmethod
    def read_fills(path: str | Path) -> list[Fill]:
        """Replay the JSONL ledger into Fill objects (reconcile / tax export / audit)."""
        target = Path(path)
        if not target.exists():
            return []
        fills: list[Fill] = []
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("kind") != "fill":
                    continue
                fills.append(_fill_from_event(event))
        return fills

    def export_trades_csv(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        header = "ts_iso,symbol,side,qty,price,notional,fee,fee_ccy,client_order_id,tag\n"
        lines = [header]
        for fill in self._fills:
            from .util import iso

            lines.append(
                f"{iso(fill.ts)},{fill.symbol},{fill.side},{fill.qty},{fill.price},"
                f"{fill.notional:.8f},{fill.fee:.8f},{fill.fee_ccy},"
                f"{fill.client_order_id},{fill.tag}\n"
            )
        target.write_text("".join(lines), encoding="utf-8")
        return target
