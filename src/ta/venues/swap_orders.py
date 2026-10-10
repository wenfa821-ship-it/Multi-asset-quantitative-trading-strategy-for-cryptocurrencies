"""Translate our intent language into OKX swap order params, and back.

Second slice of the perpetuals work (the first was swap_math.py). Like that one
it is pure: no network, no credentials, no state - so the rules can be tested
without touching the running loop.

Three things about OKX swaps break the spot assumptions we currently rely on:

  1. **Size is in contracts, not coin.** Handled by swap_math; here we only make
     sure the contract count is what actually goes on the wire.
  2. **`posSide` is mandatory** in `long_short_mode` (which this account uses:
     `/account/config` -> posMode=long_short_mode). Spot has no such notion, and
     an exit is not simply "sell" - it is "close the long", i.e. side=sell with
     posSide=long and reduceOnly.
  3. **`reduceOnly` must be true on exits.** Without it a stop-out can flip the
     book into a brand-new short position, which is the single most dangerous
     spot->swap translation bug: the stop would *open* risk instead of closing
     it.

The helpers below make those three explicit rather than incidental.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .swap_math import ContractSpec, SwapMathError, contracts_to_base


class SwapOrderError(ValueError):
    """An intent cannot be expressed as a legal OKX swap order."""


@dataclass(frozen=True)
class SwapPosition:
    inst_id: str
    pos_side: str          # "long" | "short" | "net"
    contracts: float
    avg_px: float
    base_qty: float        # contracts * ctVal, in the base currency
    notional_usd: float
    unrealized_usd: float
    liq_px: float
    leverage: float

    @property
    def is_flat(self) -> bool:
        return abs(self.contracts) < 1e-12


def pos_side_for(side: str, *, reduce_only: bool, current: str = "long") -> str:
    """Which `posSide` an intent maps to, given the direction convention.

    In long_short_mode an exit keeps the position's own posSide and only flips
    `side` + sets reduceOnly. Passing `current` therefore matters: closing a
    long must not be expressed as opening a short.
    """
    side = side.lower()
    if side not in ("buy", "sell"):
        raise SwapOrderError(f"unsupported side {side!r}")
    if reduce_only:
        if current not in ("long", "short"):
            raise SwapOrderError(f"cannot close a {current!r} position")
        return current
    return "long" if side == "buy" else "short"


def build_order_params(
    spec: ContractSpec,
    *,
    side: str,
    contracts: float,
    td_mode: str = "cross",
    price: float | None = None,
    reduce_only: bool = False,
    current_pos_side: str = "long",
) -> dict[str, str]:
    """Build the POST /trade/order body for one swap order."""
    if contracts <= 0:
        raise SwapOrderError("contracts must be positive")
    if contracts < spec.min_sz - 1e-12:
        raise SwapOrderError(f"{spec.inst_id}: {contracts} < minSz {spec.min_sz}")
    if td_mode not in ("cross", "isolated"):
        raise SwapOrderError(f"unsupported tdMode {td_mode!r}")

    params = {
        "instId": spec.inst_id,
        "tdMode": td_mode,
        "side": side.lower(),
        "posSide": pos_side_for(side, reduce_only=reduce_only, current=current_pos_side),
        "ordType": "limit" if price is not None else "market",
        "sz": _fmt(contracts),
    }
    if price is not None:
        params["px"] = _fmt(price)
    if reduce_only:
        # The whole point: an exit may shrink the position, never flip it.
        params["reduceOnly"] = "true"
    return params


def parse_position(row: dict[str, Any], spec: ContractSpec) -> SwapPosition:
    """Parse one /account/positions row into base-currency terms."""
    def num(key: str) -> float:
        raw = row.get(key)
        if raw in (None, ""):
            return 0.0
        return float(raw)

    contracts = num("pos")
    return SwapPosition(
        inst_id=str(row.get("instId") or spec.inst_id),
        pos_side=str(row.get("posSide") or "net"),
        contracts=contracts,
        avg_px=num("avgPx"),
        base_qty=contracts_to_base(spec, contracts),
        notional_usd=abs(num("notionalUsd")),
        unrealized_usd=num("upl"),
        liq_px=num("liqPx"),
        leverage=num("lever"),
    )


def assert_exit_cannot_flip(position: SwapPosition, params: dict[str, str]) -> None:
    """Guard for the caller: closing must never be able to open.

    Cheap assertion kept next to the rules it protects, because this is the bug
    that turns a stop-loss into a new position.
    """
    if position.is_flat:
        raise SwapOrderError(f"{position.inst_id}: nothing to close")
    if params.get("reduceOnly") != "true":
        raise SwapOrderError(f"{position.inst_id}: exit without reduceOnly")
    if params.get("posSide") != position.pos_side:
        raise SwapOrderError(
            f"{position.inst_id}: exit posSide {params.get('posSide')} "
            f"does not match held {position.pos_side}"
        )
    if params.get("side") == ("buy" if position.pos_side == "long" else "sell"):
        raise SwapOrderError(f"{position.inst_id}: exit side would add to the position")


def _fmt(value: float) -> str:
    """OKX takes numbers as strings; avoid exponent notation and float dust."""
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text or "0"
