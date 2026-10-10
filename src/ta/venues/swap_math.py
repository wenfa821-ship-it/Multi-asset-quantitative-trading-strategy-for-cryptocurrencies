"""Contract maths for OKX USDT-margined perpetuals (swaps).

Why this exists as its own module
---------------------------------
Everything above the venue layer speaks in **base-currency units** ("sell 42.5
SOL"). OKX swaps do not: an order is placed in **contracts**, and one contract is
worth `ctVal` of the base currency. The three underlyings we care about are not
even the same shape:

    SOL-USDT-SWAP   ctVal = 1      -> 1 contract  =    1 SOL
    BTC-USDT-SWAP   ctVal = 0.01   -> 1 contract  = 0.01 BTC
    DOGE-USDT-SWAP  ctVal = 1000   -> 1 contract  = 1000 DOGE

Getting this wrong is a silent 100x position (DOGE) or a 100x-too-small one
(BTC), so the conversion is isolated here, integer-floored to the contract
`lotSz`, and refused outright when the result is below `minSz`.

Dropping the remainder is deliberate and is the whole point: a swap position
must be a whole number of contracts, so a target of "0.0371 SOL" on a 1-SOL
contract is 0 contracts, not 0.0371 - and the caller has to know that rather
than discover it from a rejected order.

Pure functions, no network, no state: this module is unit-testable without
credentials, which is why it is written before the venue integration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


class SwapMathError(ValueError):
    """A target size cannot be expressed as a legal number of contracts."""


@dataclass(frozen=True)
class ContractSpec:
    inst_id: str
    ct_val: float           # base-currency value of ONE contract
    ct_val_ccy: str         # currency ct_val is expressed in (e.g. "SOL")
    lot_sz: float           # order size step, in contracts
    min_sz: float           # smallest order, in contracts
    tick_sz: float = 0.0
    state: str = "live"
    # OKX options carry the contract multiplier separately: a BTC-USD option has
    # ctVal=1 AND ctMult=0.01, so one contract is 0.01 BTC of underlying. Swaps are
    # ctMult=1, which is why ignoring this field went unnoticed until the 1.1
    # options adapter - where ignoring it would size every order 100x too large (or
    # floor a correct 0.01 BTC target to zero contracts and refuse to trade).
    ct_mult: float = 1.0

    @property
    def base_per_contract(self) -> float:
        """Underlying (base-currency) size of ONE contract."""
        return self.ct_val * self.ct_mult

    @classmethod
    def from_okx(cls, row: dict[str, Any]) -> "ContractSpec":
        """Build from one /public/instruments?instType=SWAP record."""
        def num(key: str, default: float = 0.0) -> float:
            raw = row.get(key)
            if raw in (None, ""):
                return default
            return float(raw)

        ct_val = num("ctVal")
        if ct_val <= 0:
            raise SwapMathError(f"{row.get('instId')}: ctVal missing or zero")
        return cls(
            inst_id=str(row.get("instId") or ""),
            ct_val=ct_val,
            ct_val_ccy=str(row.get("ctValCcy") or ""),
            lot_sz=num("lotSz", 1.0) or 1.0,
            min_sz=num("minSz", 1.0) or 1.0,
            tick_sz=num("tickSz"),
            state=str(row.get("state") or "live"),
            ct_mult=num("ctMult", 1.0) or 1.0,
        )


def contracts_to_base(spec: ContractSpec, contracts: float) -> float:
    """Base-currency size represented by `contracts` perpetual contracts."""
    return contracts * spec.base_per_contract


def base_to_contracts(spec: ContractSpec, base_qty: float) -> float:
    """Convert a base-currency target into a whole number of contracts.

    Floors to `lot_sz`. Raises when the (floored) result is below `minSz`, so a
    too-small target fails loudly at sizing time instead of silently becoming a
    dust position the bot can never exit.
    """
    if base_qty <= 0:
        raise SwapMathError(f"{spec.inst_id}: non-positive size {base_qty}")
    if spec.ct_val <= 0:
        raise SwapMathError(f"{spec.inst_id}: invalid ctVal {spec.ct_val}")

    raw = base_qty / spec.base_per_contract
    lots = math.floor(raw / spec.lot_sz + 1e-9) * spec.lot_sz
    lots = _round_to(lots, spec.lot_sz)

    if lots < spec.min_sz - 1e-12:
        raise SwapMathError(
            f"{spec.inst_id}: {base_qty} {spec.ct_val_ccy} -> {lots} contracts "
            f"is below minSz {spec.min_sz} "
            f"(1 contract = {spec.base_per_contract} {spec.ct_val_ccy})"
        )
    return lots


def contracts_for_notional(spec: ContractSpec, notional_usd: float, price: float) -> float:
    """Largest legal contract count whose notional does not exceed `notional_usd`."""
    if price <= 0:
        raise SwapMathError(f"{spec.inst_id}: non-positive price {price}")
    if notional_usd <= 0:
        raise SwapMathError(f"{spec.inst_id}: non-positive notional {notional_usd}")
    return base_to_contracts(spec, notional_usd / price)


def notional_usd(spec: ContractSpec, contracts: float, price: float) -> float:
    """USD notional of `contracts` at `price`."""
    return contracts_to_base(spec, contracts) * price


def _round_to(value: float, step: float) -> float:
    """Snap to a step grid without float dust (0.1 steps are not exact in binary)."""
    if step <= 0:
        return value
    decimals = max(0, -int(math.floor(math.log10(step))))
    return round(round(value / step) * step, decimals + 2)
