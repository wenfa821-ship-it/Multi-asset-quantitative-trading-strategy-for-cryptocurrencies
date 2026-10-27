"""Core domain objects. Deliberately dependency-free (stdlib dataclasses)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
OrderStatus = Literal["new", "open", "partially_filled", "filled", "canceled", "rejected", "dry_run"]


@dataclass(slots=True)
class Bar:
    ts: int  # bar open time, ms epoch
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Signal:
    """A strategy's *proposal*. It is never an order."""

    symbol: str
    side: Side
    reason: str
    strength: float = 1.0
    stop_price: float | None = None
    take_profit: float | None = None
    exit: bool = False  # exit signals become reduce-only orders
    tag: str = ""
    # exit plan: scale out part of the position, then protect the rest
    partial_target: float | None = None
    partial_fraction: float = 0.5
    breakeven_at_r: float | None = None
    max_holding_bars: int | None = None
    # Structured strategy evidence for blocks that never become orders. Carry
    # uses this to preserve the daily funding edge when a capacity gate refuses
    # the trade; a human-readable `reason` alone cannot be aggregated.
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: Side
    qty: float
    type: OrderType = "market"
    price: float | None = None
    reduce_only: bool = False
    # Ask the venue to REJECT the order if it would take liquidity (OKX
    # `ordType=post_only`). Used by the carry entry to try to buy the spot leg
    # passively: measured 2026-10-09, market entries paid 12-73 bps round trip and
    # the worst single leg 133 bps, against ~2 bps/day of funding.
    post_only: bool = False
    stop_price: float | None = None
    take_profit: float | None = None
    tag: str = ""

    def dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Order:
    client_order_id: str
    symbol: str
    side: Side
    qty: float
    type: OrderType = "market"
    price: float | None = None
    status: OrderStatus = "new"
    filled_qty: float = 0.0
    avg_price: float = 0.0
    venue_order_id: str | None = None
    reason: str = ""
    ts: int = 0
    # Cumulative fee as reported by the venue, and the currency it was actually
    # charged in. Both are needed: OKX can deduct a spot fee in the BASE asset
    # ("pay fees in OKB"), so a fee of 0.004979 OKB is not 0.004979 USDT.
    fee: float = 0.0
    fee_ccy: str = ""
    # Intent carried from OrderRequest. Venue normalization must preserve this
    # or every fill collapses to the transport label "ccxt" and strategy PnL
    # cannot be attributed.
    tag: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in ("new", "open", "partially_filled")

    def dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Fill:
    client_order_id: str
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float = 0.0
    fee_ccy: str = "USDT"
    ts: int = 0
    tag: str = ""

    @property
    def notional(self) -> float:
        return self.qty * self.price

    def dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Position:
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0
    stop_price: float | None = None
    take_profit: float | None = None
    opened_ts: int = 0
    atr_at_entry: float = 0.0
    tag: str = ""
    # Contracts are not coins: an OKX swap position is stored in 张, and its P&L
    # is (mark - avg) * qty * ctVal. Measured 2026-10-10: ACT's ctVal is 10, so the
    # book understated that hedge leg's P&L tenfold (CRV/LTC read correctly only
    # because their ctVal happens to be 1). Defaults to 1.0 so spot - and any
    # caller that does not know the contract size - keeps the old behaviour.
    ct_val: float = 1.0

    def notional(self, mark: float) -> float:
        return self.qty * mark

    def unrealized(self, mark: float) -> float:
        return (mark - self.avg_price) * self.qty * self.ct_val

    def dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AccountState:
    venue: str
    base_ccy: str = "USDT"
    cash: float = 0.0
    # OKX's `cashBal` - cash WITHOUT the open positions' unrealised PnL. ccxt's
    # `free`/`total` both return `eq` (= cashBab + upl), so a reconciliation that
    # compares against them drifts with the mark price of every open swap
    # (measured 2026-10-09: upl +$225.69 on the CRV hedge, residual swinging
    # +/-$500). This is the figure the book's cash model actually mirrors.
    # 0.0 means "venue did not report it" - callers fall back to `cash`.
    cash_bal: float = 0.0
    equity: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    # Manual holdings listed in config/reserved_inventory.json: still not the bot's,
    # but KNOWN - so they are counted here instead of in `unmanaged_notional`, whose
    # whole job is to mean "nobody can say where this came from".
    reserved_notional: float = 0.0
    reserved_positions: dict[str, Position] = field(default_factory=dict)
    # Assets that exist in the account but were NOT opened by this bot (deposits,
    # manual trades, or the pre-funded bundle an exchange demo account ships with).
    # They count towards equity, but must not be treated as strategy exposure.
    unmanaged_positions: dict[str, Position] = field(default_factory=dict)
    unmanaged_notional: float = 0.0
    # Rounding residue that the venue would refuse to sell (below its minimum
    # order size or minimum notional). OKX charges spot fees in the base asset,
    # so a full exit always leaves a sub-lot remainder. Dust is not a position:
    # counting it burned a `max_open_positions` slot after a few round trips.
    dust_positions: dict[str, float] = field(default_factory=dict)
    dust_notional: float = 0.0
    realized_pnl_total: float = 0.0
    realized_pnl_today: float = 0.0
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    open_order_count: int = 0
    consecutive_losses: int = 0
    fees_paid: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    stale: bool = False
    # Capital the bot actually controls (cash + managed positions), set by the
    # runner using live marks. Risk percentages are relative to THIS, not to the
    # whole account: a demo account pre-funded with 1 BTC would otherwise make
    # every position look like 95% of equity and block all trading.
    risk_equity: float = 0.0

    @property
    def gross_exposure(self) -> float:
        """Managed exposure only - what the risk engine is allowed to act on."""
        return sum(p.qty * p.avg_price for p in self.positions.values())

    @property
    def effective_risk_equity(self) -> float:
        return self.risk_equity or (self.cash + self.gross_exposure) or self.equity

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return 100.0 * (self.peak_equity - self.effective_risk_equity) / self.peak_equity

    @property
    def daily_pnl_pct(self) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return 100.0 * (self.effective_risk_equity - self.day_start_equity) / self.day_start_equity

    def dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data
