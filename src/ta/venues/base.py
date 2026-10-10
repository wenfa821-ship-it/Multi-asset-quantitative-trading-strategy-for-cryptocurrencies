"""Venue abstraction. Everything above this line is venue-agnostic."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import AccountState, Bar, Fill, Order, OrderRequest


class VenueError(RuntimeError):
    pass


class Venue(ABC):
    name: str = "base"
    simulated: bool = False
    base_ccy: str = "USDT"

    # ---- lifecycle ---------------------------------------------------------
    def connect(self) -> None:  # pragma: no cover - trivial default
        return None

    def close(self) -> None:  # pragma: no cover - trivial default
        return None

    # ---- market data -------------------------------------------------------
    @abstractmethod
    def fetch_bars(self, symbol: str, timeframe: str, limit: int) -> list[Bar]: ...

    @abstractmethod
    def refresh_marks(self, symbols: list[str]) -> dict[str, float]: ...

    # ---- account -----------------------------------------------------------
    @abstractmethod
    def get_account(self) -> AccountState: ...

    # ---- trading -----------------------------------------------------------
    @abstractmethod
    def place_order(self, request: OrderRequest) -> Order: ...

    @abstractmethod
    def cancel_order(self, client_order_id: str, symbol: str) -> bool: ...

    @abstractmethod
    def cancel_all(self, symbols: list[str]) -> int: ...

    @abstractmethod
    def fetch_open_orders(self, symbols: list[str]) -> list[Order]: ...

    @abstractmethod
    def drain_fills(self) -> list[Fill]:
        """Return fills observed since the previous call (and clear the buffer)."""

    # ---- optional ----------------------------------------------------------
    def sync(self, symbols: list[str]) -> None:
        """Poll the venue so that fills of resting orders are detected."""
        return None

    def min_notional(self, symbol: str) -> float:
        return 10.0

    def min_qty(self, symbol: str) -> float:
        """Smallest base-currency order the venue accepts (0.0 = unknown).

        A holding below this can never be sold, so it is rounding residue rather
        than a position the bot can manage.
        """
        return 0.0

    def spread_bps(self, symbol: str) -> float | None:
        """Best bid/ask spread in basis points, or None when unavailable.

        Used as an entry gate: paying 25bps of spread on a 15m signal destroys
        an edge that looked fine in a backtest with flat slippage assumptions.
        """
        return None

    def account_snapshot(self) -> dict[str, Any]:
        account = self.get_account()
        return {
            "venue": self.name,
            "cash": round(account.cash, 8),
            # Cash without the open positions' unrealised PnL (OKX `cashBal`).
            # Falls back to `cash` when the venue does not report it, so every
            # caller can use this key unconditionally.
            "cash_bal": round(account.cash_bal or account.cash, 8),
            "equity": round(account.equity, 8),
            "positions": {s: round(p.qty, 10) for s, p in account.positions.items()},
        }
