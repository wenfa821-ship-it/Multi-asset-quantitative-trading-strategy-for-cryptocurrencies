"""Execute a two-leg pair: spot long + perp short, on two venues.

Why this module exists
---------------------
Everything the runner does today is one leg on one venue. Carry is the one
surviving non-directional idea (README 26/27) and it is *structurally* two legs:
a spot long whose price cancels the perp short, leaving only the funding. The
strategy interface is spot-shaped (`side="buy"` means "go long"), so a strategy
cannot express "also short the perp" - and trading only the short leg is a naked
short, which README 26 explicitly refuses to call carry.

So the pairing lives here, below the strategies and beside the OMS, and it is
dependent only on the OMS *interface* (`submit(OrderRequest) -> Order`), not on
venues or ledgers. That keeps it unit-testable with fakes and keeps the runner's
loop untouched until the config actually asks for a hedge venue.

The invariants that matter
--------------------------
These are the whole reason a book can run carry without waking up net long or
net short, and each one is a test:

  1. BOTH legs carry the same base quantity, so the pair is delta-neutral to
     within the spot fee that is charged in the base asset.
  2. If the SPOT leg fails, nothing else is sent. A short with no long is a
     directional bet, not a carry.
  3. If the PERP leg fails after the spot leg filled, the spot leg is unwound
     IMMEDIATELY. A long with no short is the same directional bet with the
     opposite sign, and it is the failure mode that costs money quietly.
  4. Closing sends `reduce_only=True` on the perp leg, so a stop-out can never
     flip the pair into a brand-new long.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import time
from typing import Any, Protocol

from ..models import Order, OrderRequest
from ..util import safe_div


class PairExecutionError(RuntimeError):
    """The pair could not be established (and any partial leg was unwound)."""


class OrderSink(Protocol):
    """The slice of OMS this module needs - nothing else."""

    def submit(self, request: OrderRequest) -> Order: ...


@dataclass(slots=True)
class PairLegs:
    pair_id: str
    symbol: str
    base_qty: float
    spot_order: Order
    perp_order: Order
    # One perp contract is worth `contract_ct_val` of the base currency (SOL 1,
    # DOGE 1000, BTC 0.01). Fills come back in CONTRACTS, everything else in this
    # module speaks BASE units, and conflating the two is what produced a fake
    # residual of +1688 and a rejected close on 2026-10-08.
    contract_ct_val: float = 1.0

    @property
    def spot_filled(self) -> float:
        return float(self.spot_order.filled_qty or 0.0)

    @property
    def perp_filled(self) -> float:
        """Base units the hedge holds, converted from the venue's contract count."""
        return float(self.perp_order.filled_qty or 0.0) * self.contract_ct_val

    @property
    def perp_contracts(self) -> float:
        """The raw number the venue reports (contracts, not coin)."""
        return float(self.perp_order.filled_qty or 0.0)

    def residual(self) -> float:
        """Unhedged BASE quantity: spot fill minus the hedge, both in base units."""
        return self.spot_filled - self.perp_filled


def perp_symbol(spot_symbol: str) -> str:
    """`SOL/USDT` -> `SOL/USDT:USDT` (the settlement-suffixed swap)."""
    base, _, quote = spot_symbol.partition("/")
    return f"{base}/{quote}:{quote}"


class PairedExecutor:
    """Open and close spot-long / perp-short pairs across two order sinks."""

    def __init__(self, spot: OrderSink, perp: OrderSink, *, stamp: Any = None) -> None:
        self.spot = spot
        self.perp = perp
        # A monotonic counter is enough to keep pair ids unique across restarts
        # because the venue-side client order ids carry the caller's own prefix.
        self._seq = 0
        self._stamp = stamp or (lambda: 0)
        self.fill_timeout_seconds = 15.0

    def _await_fill(self, sink: Any, order: Order, symbol: str) -> Order:
        """Wait for the fill instead of believing the submit response.

        OKX answers a market order with `status=open, filled=0` and fills a beat
        later. Reading that as "not filled" is what left a $2,969 NAKED LONG on
        ACT/USDT on 2026-10-08: `open_pair` raised "spot leg not filled", the
        runner logged and returned, and the order filled a moment afterwards -
        with the hedge never sent and no unwind, because the spot-leg failure
        path assumed no long existed.

        So poll the sink (which is what drains the venue) until the fill shows up
        or the timeout expires, and let the CALLER decide based on the real
        fill rather than the initial ack.
        """
        if float(order.filled_qty or 0) > 0:
            return order
        deadline = time.time() + self.fill_timeout_seconds
        while time.time() < deadline:
            fills: list = []
            try:
                fills = sink.sync([symbol]) or []
            except Exception:  # noqa: BLE001 - a venue hiccup is not a verdict
                pass
            # The OMS records an order once at submit time and does NOT raise its
            # filled_qty afterwards - the fill arrives separately, through sync().
            # Watching only the order record is why the first fix still failed on
            # a real venue (2026-10-08: DOGE filled 1691 in full, the wait saw
            # "still open", and the pair was abandoned).
            seen = sum(
                float(getattr(f, "qty", 0.0) or 0.0)
                for f in fills
                if getattr(f, "client_order_id", "") == order.client_order_id
            )
            if seen > 0:
                order = replace(order, filled_qty=seen, status="filled")
                return order
            current = getattr(sink, "_orders", {}).get(order.client_order_id, order)
            if float(current.filled_qty or 0) > 0:
                return current
            time.sleep(0.5)
        return getattr(sink, "_orders", {}).get(order.client_order_id, order)

    def _next_id(self, tag: str) -> str:
        self._seq += 1
        return f"PAIR{self._stamp()}{self._seq:04d}{tag}"

    def _passive_spot(
        self,
        pair_id: str,
        symbol: str,
        base_qty: float,
        price: float,
        wait_seconds: float,
        tag: str,
    ) -> tuple[Order, str]:
        """Rest a post-only buy at `price`, wait briefly, then cancel it.

        Returns (order, outcome) with outcome in {"filled", "cancelled", "unknown"}.

        "unknown" must NOT be followed by a market fallback: an order whose final
        state cannot be confirmed may still fill, and buying at market on top of
        it doubles the spot leg while the hedge stays single-size - the exact
        state the pair invariants exist to prevent. The caller aborts instead.

        The submit acknowledgement is never used as evidence. Verified against the
        real venue 2026-10-09: a post-only order that would take liquidity comes
        back `status=open` and is killed by the exchange a moment later
        (`cancelSource=31`). So "is it still live?" is answered by the open-order
        list and "did it fill?" by the fills the sink drains.
        """
        client_order_id = f"{pair_id}s"
        order = self.spot.submit(
            OrderRequest(
                client_order_id=client_order_id,
                symbol=symbol,
                side="buy",
                qty=base_qty,
                type="limit",
                price=price,
                post_only=True,
                tag=tag,
            )
        )
        if order.status == "rejected":
            # Nothing was accepted, so nothing can be resting or fill later.
            return order, "cancelled"

        filled = 0.0
        status = "unknown"

        def probe() -> str:
            """Refresh fills + resting state. Returns the order's status."""
            nonlocal filled, status
            # 1) Fills drained through the sink (cumulative deltas, so summing them
            #    across polls is safe - the OMS hands each fill out exactly once).
            try:
                for fill in self.spot.sync([symbol]) or []:
                    if getattr(fill, "client_order_id", "") == client_order_id:
                        filled += float(getattr(fill, "qty", 0.0) or 0.0)
            except Exception:  # noqa: BLE001 - a hiccup is not a verdict
                pass
            # 2) The venue's OWN record for this order. This is the only source
            #    that can tell "cancelled" from "filled" - both leave the
            #    open-order list. Measured 2026-10-10 (LTC): the passive buy filled
            #    at +3.6s, the open list already looked empty, the old code called
            #    it cancelled and sent the market fallback, and the spot leg came
            #    out at 2x the hedge (~$2k unhedged). Reading absence as a verdict
            #    is the bug this replaces.
            record = None
            probe_state = getattr(self.spot, "order_state", None)
            if callable(probe_state):
                try:
                    record = probe_state(client_order_id, symbol)
                except Exception:  # noqa: BLE001
                    record = None
            if record is not None:
                state, qty = record
                filled = max(filled, float(qty or 0.0))
                if filled > 0 or state == "filled":
                    status = "filled"
                    return "filled"
                if state in ("canceled", "rejected"):
                    status = state
                    return state
                status = "open"
                return "open"
            # 3) Sinks that only keep their own order dict (the test doubles).
            order_record = getattr(self.spot, "_orders", {}).get(client_order_id)
            if order_record is not None:
                filled = max(filled, float(getattr(order_record, "filled_qty", 0.0) or 0.0))
                status = str(getattr(order_record, "status", "unknown"))
                if status in ("filled", "canceled", "rejected"):
                    return status
                return status
            status = "unknown"
            return status

        # Leave it resting for the whole window: the goal is the maker fill, and
        # a partial fill that completes inside the window is still a maker fill.
        deadline = time.time() + max(0.0, wait_seconds)
        state = probe()
        while time.time() < deadline and state not in ("canceled", "rejected", "filled"):
            time.sleep(0.5)
            state = probe()
        if state == "filled" and filled > 0:
            return replace(order, filled_qty=filled, status="filled"), "filled"
        if state in ("canceled", "rejected"):
            # Already definitively gone (a killed post-only order, or a rejection):
            # there is nothing to pull, so skip the cancel attempt entirely.
            return order, "cancelled"

        # Out of time (or killed): pull whatever is still resting and CONFIRM it
        # is gone. The market fallback is gated on that confirmation, never on a
        # failed cancel - and this loop is what the whole design hangs on.
        cancel = getattr(self.spot, "cancel", None)
        for _ in range(3):
            if callable(cancel):
                try:
                    cancel(client_order_id, symbol)
                except Exception:  # noqa: BLE001 - re-read the state below
                    pass
            time.sleep(0.5)
            state = probe()
            if state in ("canceled", "rejected"):
                break

        if filled > 0:
            # A partial fill is a real long. Hedging what exists leaves the book
            # closer to neutral than abandoning it would, and the remainder has
            # just been cancelled above rather than left to chance.
            return replace(order, filled_qty=filled, status="filled"), "filled"
        if state in ("canceled", "rejected"):
            return order, "cancelled"
        return order, "unknown"

    def open_pair(
        self,
        symbol: str,
        base_qty: float,
        *,
        tag: str = "carry",
        spot_price: float | None = None,
        passive_price: float | None = None,
        passive_wait_seconds: float = 6.0,
    ) -> PairLegs:
        """Buy `base_qty` on spot, then short the same quantity on the perp.

        With `passive_price` set, the spot leg first tries to be a MAKER: rest a
        post-only buy at that price, wait `passive_wait_seconds`, cancel it, and
        only then buy at market. That ordering is the whole point - the entry
        spread is the largest single cost carry has (measured 12.6-72.6 bps per
        leg against ~2 bps/day of funding), and a maker entry pays none of it.
        """
        if base_qty <= 0:
            raise PairExecutionError(f"non-positive pair size {base_qty}")
        pair_id = self._next_id("O")

        spot_order: Order | None = None
        if passive_price and float(passive_price) > 0:
            spot_order, outcome = self._passive_spot(
                pair_id, symbol, base_qty, float(passive_price),
                float(passive_wait_seconds), tag,
            )
            if outcome == "unknown":
                raise PairExecutionError(
                    "passive spot order could not be confirmed cancelled - market "
                    "fallback refused (it could double the spot leg), no hedge sent"
                )
            if outcome == "cancelled":
                spot_order = None  # confirmed gone; the market path below is safe
        if spot_order is None:
            spot_order = self.spot.submit(
                OrderRequest(
                    # NOT `{pair_id}s`: the OMS is idempotent per client order id
                    # and the passive attempt already claimed that one, so a
                    # fallback reusing it comes back "duplicate_client_order_id"
                    # and the entry silently never happens. (The first version of
                    # this change did exactly that; the fake-sink test that keeps
                    # both orders in one dict is what caught it.)
                    client_order_id=f"{pair_id}m",
                    symbol=symbol,
                    side="buy",
                    qty=base_qty,
                    type="market",
                    tag=tag,
                )
            )
            spot_order = self._await_fill(self.spot, spot_order, symbol)
        if spot_order.status == "rejected" or spot_order.filled_qty <= 0:
            # Invariant 2, with the async case handled: a "rejected" spot leg has
            # no long, so it is safe to stop here. An "open but unfilled" one is
            # NOT safe - it may still fill - so we unwind whatever did land.
            landed = self._filled_base(symbol, float(spot_order.filled_qty or 0.0))
            if landed > 0:
                self.spot.submit(
                    OrderRequest(
                        client_order_id=f"{pair_id}u",
                        symbol=symbol,
                        side="sell",
                        qty=landed,
                        type="market",
                        tag=f"{tag}-unwind",
                    )
                )
            raise PairExecutionError(
                f"spot leg not filled ({spot_order.status}: {spot_order.reason}); "
                f"hedge not sent, {landed} unwound"
            )

        filled = float(spot_order.filled_qty)
        perp_order = self.perp.submit(
            OrderRequest(
                client_order_id=f"{pair_id}p",
                symbol=perp_symbol(symbol),
                side="sell",
                qty=filled,          # invariant 1: the hedge matches what filled
                type="market",
                tag=tag,
            )
        )
        perp_order = self._await_fill(self.perp, perp_order, perp_symbol(symbol))
        if perp_order.status == "rejected" or perp_order.filled_qty <= 0:
            # Invariant 3: unwind the naked long immediately, then fail loudly.
            unwind = self.spot.submit(
                OrderRequest(
                    client_order_id=f"{pair_id}u",
                    symbol=symbol,
                    side="sell",
                    qty=filled,
                    type="market",
                    tag=f"{tag}-unwind",
                )
            )
            raise PairExecutionError(
                f"perp leg not filled ({perp_order.status}: {perp_order.reason}); "
                f"spot unwound ({unwind.status}, {unwind.filled_qty} filled)"
            )

        return PairLegs(
            pair_id=pair_id,
            symbol=symbol,
            base_qty=filled,
            spot_order=spot_order,
            perp_order=perp_order,
            contract_ct_val=self._ct_val(perp_symbol(symbol)),
        )

    def _ct_val(self, symbol: str) -> float:
        """Base-currency value of one contract, straight from the venue's market.

        Returns 1.0 when it cannot be read: that is the identity for venues whose
        contract already IS one unit (or for the fake sinks in tests), and an
        unreadable spec must not silently rescale a hedge.
        """
        venue = getattr(self.perp, "venue", None)
        markets = getattr(venue, "ex", None)
        info = ((getattr(markets, "markets", None) or {}).get(symbol) or {}).get("info") or {}
        try:
            value = float(info.get("ctVal") or 0.0)
        except (TypeError, ValueError):
            return 1.0
        return value if value > 0 else 1.0

    def _filled_base(self, symbol: str, known: float) -> float:
        """How much of `symbol` we actually hold - the honest answer to 'did it fill'.

        Asking the VENUE, not our own order record: after an async fill the record
        can still say 0 while the balance says otherwise, and the balance is what
        determines whether a naked long exists.
        """
        venue = getattr(self.spot, "venue", None)
        reader = getattr(venue, "_free_base", None) or getattr(self.spot, "position_qty", None)
        if callable(reader):
            try:
                return max(known, float(reader(symbol) or 0.0))
            except Exception:  # noqa: BLE001
                return known
        return known

    def close_pair(self, pair: PairLegs, *, tag: str = "carry-exit") -> tuple[Order, Order]:
        """Buy back the perp (reduceOnly) then sell the spot.

        Perp first: if the buy-back fails, the long is still hedged and the money
        is still (approximately) flat. Selling spot first would leave a naked
        short, which is the more dangerous of the two half-states.
        """
        pair_id = self._next_id("C")
        # BASE units for both legs: the venue converts to contracts on the way
        # in, and passing the venue's contract count here is what got the close
        # rejected on 2026-10-08.
        perp_fill = pair.perp_filled or pair.base_qty
        # Tell the venue which side this position is on BEFORE buying it back.
        # OKX is in long/short mode, so `posSide` decides whether "buy" closes the
        # short or OPENS A LONG. The venue defaults to "long", which is why the
        # first controlled run ended with a stray long 1.68 contracts sitting on
        # top of the short it was supposed to close (measured 2026-10-08).
        venue = getattr(self.perp, "venue", None)
        if venue is not None:
            try:
                setattr(venue, "default_pos_side", "short")
            except Exception:  # noqa: BLE001 - a sink without the attribute is fine
                pass
        perp_order = self.perp.submit(
            OrderRequest(
                client_order_id=f"{pair_id}p",
                symbol=perp_symbol(pair.symbol),
                side="buy",
                qty=perp_fill,
                type="market",
                reduce_only=True,     # invariant 4
                tag=tag,
            )
        )
        if perp_order.status == "rejected":
            raise PairExecutionError(
                f"could not close the perp leg ({perp_order.reason}); "
                f"spot left hedged on purpose"
            )

        spot_fill = float(pair.spot_order.filled_qty or pair.base_qty)
        spot_order = self.spot.submit(
            OrderRequest(
                client_order_id=f"{pair_id}s",
                symbol=pair.symbol,
                side="sell",
                qty=spot_fill,
                type="market",
                tag=tag,
            )
        )
        return spot_order, perp_order

    @staticmethod
    def funding_edge_bps(
        daily_bps: float, round_trip_bps: float, hold_days: float
    ) -> float:
        """Net bps from carrying for `hold_days` at `daily_bps` of funding.

        The decision rule for opening a pair, in one line: the funding collected
        over the intended hold has to beat the measured round trip. Anything else
        is a bet on the price, which is the thing the pair is built to remove.
        """
        return daily_bps * hold_days - round_trip_bps
