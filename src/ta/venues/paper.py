"""Local paper-trading venue: full order lifecycle, fees, slippage, no network."""

from __future__ import annotations

import time

from ..models import AccountState, Bar, Fill, Order, OrderRequest, Position
from ..portfolio import PositionBook
from ..util import day_key
from .base import Venue


class PaperVenue(Venue):
    simulated = True

    def __init__(
        self,
        initial_cash: float = 10_000.0,
        fee_bps: float = 8.0,
        slippage_bps: float = 5.0,
        base_ccy: str = "USDT",
        name: str = "paper",
        timezone: str = "UTC",
        min_notional_usd: float = 10.0,
        clock=None,
    ) -> None:
        self.name = name
        self.base_ccy = base_ccy
        self.fee_bps = float(fee_bps)
        self.slippage_bps = float(slippage_bps)
        self.min_notional_usd = float(min_notional_usd)
        self.timezone = timezone
        self._clock = clock or time.time

        self.book = PositionBook(base_ccy=base_ccy, cash=float(initial_cash))
        self.book.peak_equity = float(initial_cash)
        self.book.day_start_equity = float(initial_cash)
        self.book.day = self._day_key()

        self._orders: dict[str, Order] = {}
        self._open: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._bars: dict[str, list[Bar]] = {}
        self._rejects: list[str] = []

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    def _day_key(self) -> str:
        return day_key(self._now_ms(), tz_name=self.timezone)

    # ---- market data -------------------------------------------------------
    def seed(self, cash: float, positions: dict[str, Position]) -> None:
        """Continue a previous paper run instead of starting from a blank book.

        The ledger persists across runs (it is the audit trail), so a fresh
        simulator would disagree with it: the book says we hold BTC, the venue
        says we hold nothing, and reconciliation fails forever.
        """
        self.book.cash = float(cash)
        for symbol, position in positions.items():
            if position.qty > 0:
                self.book.positions[symbol] = Position(
                    symbol=symbol,
                    qty=position.qty,
                    avg_price=position.avg_price,
                    opened_ts=position.opened_ts,
                )

    def set_series(self, symbol: str, bars: list[Bar]) -> None:
        self._bars[symbol] = bars

    def set_mark(self, symbol: str, price: float) -> None:
        self.book.mark(symbol, price)
        self._try_resting_orders(symbol)

    def fetch_bars(self, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        return list(self._bars.get(symbol, []))[-limit:]

    def refresh_marks(self, symbols: list[str]) -> dict[str, float]:
        return {s: self.book.mark_of(s) for s in symbols if self.book.mark_of(s) > 0}

    def min_notional(self, symbol: str) -> float:
        return self.min_notional_usd

    def spread_bps(self, symbol: str) -> float | None:
        # the simulator charges half the configured slippage on each side
        return float(self.slippage_bps)

    # ---- account -----------------------------------------------------------
    def get_account(self) -> AccountState:
        self.book.roll_day(self._day_key())
        self.book.snapshot_extremes()
        account = self.book.account(self.name)
        account.open_order_count = len(self._open)
        return account

    # ---- trading -----------------------------------------------------------
    def place_order(self, request: OrderRequest) -> Order:
        order = Order(
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            type=request.type,
            price=request.price,
            ts=self._now_ms(),
        )
        self._orders[request.client_order_id] = order

        mark = self.book.mark_of(request.symbol)
        if mark <= 0:
            order.status = "rejected"
            order.reason = "no mark price for symbol"
            return order

        if request.type == "market":
            return self._execute(order, request, mark)

        # limit order
        price = request.price or mark
        crossing = (request.side == "buy" and price >= mark) or (request.side == "sell" and price <= mark)
        if crossing:
            return self._execute(order, request, price)
        order.status = "open"
        self._open[order.client_order_id] = order
        return order

    def _try_resting_orders(self, symbol: str) -> None:
        for cid, order in list(self._open.items()):
            if order.symbol != symbol:
                continue
            mark = self.book.mark_of(symbol)
            price = order.price or mark
            if (order.side == "buy" and mark <= price) or (order.side == "sell" and mark >= price):
                request = OrderRequest(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    qty=order.qty,
                    type="limit",
                    price=price,
                )
                self._execute(order, request, price)
                self._open.pop(cid, None)

    def _execute(self, order: Order, request: OrderRequest, price: float) -> Order:
        book = self.book
        slippage = price * self.slippage_bps / 10_000.0
        fill_price = price
        if request.type == "market":
            fill_price = price + slippage if request.side == "buy" else price - slippage
        qty = float(request.qty)

        if qty <= 0:
            order.status = "rejected"
            order.reason = "qty <= 0"
            return order

        notional = qty * fill_price
        if notional < self.min_notional_usd:
            order.status = "rejected"
            order.reason = f"notional {notional:.2f} below venue minimum {self.min_notional_usd:.2f}"
            self._rejects.append(order.reason)
            return order

        fee = notional * self.fee_bps / 10_000.0
        position = book.positions.get(request.symbol, Position(symbol=request.symbol))

        if request.side == "buy":
            if book.cash < notional + fee:
                order.status = "rejected"
                order.reason = "insufficient cash"
                self._rejects.append(order.reason)
                return order
        else:
            if position.qty < qty:
                order.status = "rejected"
                order.reason = f"insufficient position ({position.qty:.8f} < {qty:.8f})"
                self._rejects.append(order.reason)
                return order

        fill = Fill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            qty=qty,
            price=round(fill_price, 10),
            fee=round(fee, 10),
            fee_ccy=self.base_ccy,
            ts=self._now_ms(),
            tag=request.tag,
        )
        book.mark(order.symbol, fill.price)
        realized = book.apply_fill(fill)
        self._fills.append(fill)

        if request.side == "buy":
            position = book.positions.setdefault(request.symbol, Position(symbol=request.symbol))
            if request.stop_price:
                position.stop_price = float(request.stop_price)
            if request.take_profit:
                position.take_profit = float(request.take_profit)
            position.tag = request.tag or position.tag

        order.status = "filled"
        order.filled_qty = qty
        order.avg_price = fill.price
        order.reason = f"realized={realized:.4f}" if request.side == "sell" else ""
        return order

    def cancel_order(self, client_order_id: str, symbol: str) -> bool:
        order = self._open.pop(client_order_id, None)
        if order is None:
            return False
        order.status = "canceled"
        return True

    def cancel_all(self, symbols: list[str]) -> int:
        count = 0
        for cid, order in list(self._open.items()):
            if not symbols or order.symbol in symbols:
                order.status = "canceled"
                self._open.pop(cid, None)
                count += 1
        return count

    def fetch_open_orders(self, symbols: list[str]) -> list[Order]:
        return [o for o in self._open.values() if not symbols or o.symbol in symbols]

    def drain_fills(self) -> list[Fill]:
        fills, self._fills = self._fills, []
        return fills

    def account_snapshot(self) -> dict:
        snapshot = super().account_snapshot()
        snapshot["open_orders"] = sorted(self._open)
        return snapshot

    def rejected_reasons(self) -> list[str]:
        return list(self._rejects)
