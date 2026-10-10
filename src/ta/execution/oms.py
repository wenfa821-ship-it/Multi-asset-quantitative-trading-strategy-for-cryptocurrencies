"""Order management: idempotent submission, state tracking, reconciliation."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Callable

from ..models import AccountState, Fill, Order, OrderRequest
from ..portfolio import Ledger
from ..util import sanitize_id
from ..venues.base import Venue


@dataclass(slots=True)
class ReconciliationReport:
    ok: bool
    diffs: list[str]
    venue: dict
    local: dict


class OMS:
    def __init__(
        self,
        venue: Venue,
        ledger: Ledger,
        *,
        dry_run: bool = True,
        on_fill: Callable[[Fill], None] | None = None,
    ) -> None:
        self.venue = venue
        self.ledger = ledger
        self.dry_run = dry_run
        self.on_fill = on_fill
        self.submitted = 0
        self.rejected = 0
        self.dry_runs = 0

    # ---- ids ---------------------------------------------------------------
    @staticmethod
    def new_client_order_id(symbol: str, tag: str = "ta") -> str:
        base, _, quote = symbol.partition("/")
        raw = f"TA{sanitize_id(tag, 6)}{sanitize_id(base, 6)}{sanitize_id(quote, 5)}{uuid.uuid4().hex[:10]}"
        return sanitize_id(raw, 32)

    # ---- submission --------------------------------------------------------
    def submit(self, request: OrderRequest) -> Order:
        """Idempotent: the same client_order_id can never be sent twice."""
        if self.ledger.has_order(request.client_order_id):
            return Order(
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                status="rejected",
                reason="duplicate_client_order_id",
            )

        if self.dry_run:
            self.dry_runs += 1
            order = Order(
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                type=request.type,
                price=request.price,
                status="dry_run",
                reason="dry_run: not sent to venue",
                ts=int(time.time() * 1000),
            )
            self.ledger.record_order(order)
            return order

        order = self.venue.place_order(request)
        self.ledger.record_order(order, extra={"request": request.dict()})
        if order.status == "rejected":
            self.rejected += 1
        else:
            self.submitted += 1
        self.collect_fills()
        return order

    def collect_fills(self) -> list[Fill]:
        """Single funnel: every fill is recorded once, then handed to the callback."""
        fills = self.venue.drain_fills()
        for fill in fills:
            self.ledger.record_fill(fill)
            if self.on_fill is not None:
                self.on_fill(fill)
        return fills

    def sync(self, symbols: list[str]) -> list[Fill]:
        self.venue.sync(symbols)
        return self.collect_fills()

    def cancel_all(self, symbols: list[str]) -> int:
        if self.dry_run:
            return 0
        return self.venue.cancel_all(symbols)

    def order_state(self, client_order_id: str, symbol: str) -> tuple[str, float] | None:
        """The venue's own record for one order: (status, filled) or None.

        The passive carry entry gates its market fallback on this. None means
        "cannot tell", and it must NOT be read as "cancelled": a FILLED order is
        absent from the open-order list as well, so only the order's own record
        can distinguish the two (measured 2026-10-10, LTC: absence was read as
        cancelled while the order had filled, and the spot leg came out at 2x).
        """
        if self.dry_run:
            return None
        probe = getattr(self.venue, "order_state", None)
        if not callable(probe):
            return None
        return probe(client_order_id, symbol)

    def open_order_ids(self, symbols: list[str]) -> set[str] | None:
        """Client order ids the venue still has resting, or None if unreadable.

        The passive carry entry asks this before falling back to a market buy:
        None means "cannot prove the resting order is gone", which must refuse the
        fallback rather than risk buying twice.
        """
        if self.dry_run:
            return set()
        probe = getattr(self.venue, "open_order_ids", None)
        if not callable(probe):
            return None
        return probe(symbols)

    def cancel(self, client_order_id: str, symbol: str) -> bool:
        """Pull ONE resting order.

        The passive carry entry needs this: an unfilled post-only order must be
        confirmed gone before any market fallback, because a fallback sent on top
        of a still-live resting order doubles the spot leg (and therefore leaves
        the perp hedge half-size). `cancel_all` is not usable for it - it would
        also pull every other order resting on that symbol.
        """
        if self.dry_run:
            return True
        return bool(self.venue.cancel_order(client_order_id, symbol))

    # ---- reconciliation ----------------------------------------------------
    def reconcile(
        self,
        account: AccountState,
        local_book,
        *,
        cash_baseline: float | None = None,
        cash_tolerance: float = 1.0,
    ) -> ReconciliationReport:
        """Compare our managed book with the venue.

        Cash is compared on a DELTA basis against a frozen baseline, because the
        venue reports an absolute balance that also contains deposits, manual
        trades, or a pre-funded demo bundle. Comparing absolutes produced a
        permanent false "cash mismatch" alert before this.
        """
        venue_snapshot = self.venue.account_snapshot()
        local_snapshot = {
            "venue": self.venue.name,
            "cash": round(local_book.cash, 8),
            "equity": round(local_book.equity(), 8),
            "positions": {s: round(p.qty, 10) for s, p in local_book.open_positions().items()},
            "unmanaged_notional": round(getattr(account, "unmanaged_notional", 0.0), 4),
        }
        diffs: list[str] = []
        # A discrepancy worth less than a cent is not actionable - it is rounding
        # on a fee the venue charged in the base asset. Comparing such residues
        # with a 1e-6 ABSOLUTE tolerance produced a false "reconciliation mismatch"
        # on INJ dust (local 5.35e-06 vs venue 6.43e-06 = $0.000008) on
        # 2026-10-02. Judge drift by what it is WORTH, not by base-unit precision:
        # prices range over nine orders of magnitude across this universe.
        trivial_usd = 0.10
        for symbol, qty in local_snapshot["positions"].items():
            venue_qty = venue_snapshot.get("positions", {}).get(symbol, 0.0)
            mark = float(getattr(local_book, "marks", {}).get(symbol, 0.0) or 0.0)
            gap = abs(venue_qty - qty)
            if mark > 0 and gap * mark < trivial_usd:
                continue
            if gap > max(1e-6, abs(qty) * 1e-4):
                diffs.append(f"position {symbol}: local={qty} venue={venue_qty}")

        if cash_baseline is None:
            local_snapshot["cash_check"] = "skipped (no baseline)"
        else:
            expected = round(cash_baseline + local_snapshot["cash"], 8)
            # Compare against cash WITHOUT the open positions' unrealised PnL.
            # `cash` is ccxt's `free`/`total`, which on OKX is `eq` = cashBal + upl,
            # so using it made the residual drift with the mark price of every open
            # swap (measured 2026-10-09: upl +$225.69 on the CRV hedge, residual
            # swinging +/-$500 over a few hours). `cash_bal` is the venue's own
            # `cashBal`, which is what the book's cash model mirrors.
            actual = round(
                float(venue_snapshot.get("cash_bal", venue_snapshot.get("cash", 0.0))), 8
            )
            local_snapshot["cash_expected"] = expected
            local_snapshot["cash_actual"] = actual
            if abs(actual - expected) > cash_tolerance:
                diffs.append(
                    f"cash delta: expected {expected} (baseline {cash_baseline} + our flow "
                    f"{local_snapshot['cash']}) but venue shows {actual}"
                )
        return ReconciliationReport(
            ok=not diffs, diffs=diffs, venue=venue_snapshot, local=local_snapshot
        )
