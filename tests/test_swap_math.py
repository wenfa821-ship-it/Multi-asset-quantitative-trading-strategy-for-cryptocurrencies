"""Contract-maths tests: the size bugs that would be silent and expensive."""

from __future__ import annotations

import pytest

from ta.venues.swap_math import (
    ContractSpec,
    SwapMathError,
    base_to_contracts,
    contracts_for_notional,
    contracts_to_base,
    notional_usd,
)


def spec(inst="SOL-USDT-SWAP", ct_val=1.0, ccy="SOL", lot=1.0, minimum=1.0, tick=0.01):
    return ContractSpec(
        inst_id=inst, ct_val=ct_val, ct_val_ccy=ccy,
        lot_sz=lot, min_sz=minimum, tick_sz=tick,
    )


def test_sol_one_contract_is_one_sol():
    s = spec()
    assert contracts_to_base(s, 5) == pytest.approx(5.0)
    assert base_to_contracts(s, 42.5) == pytest.approx(42.0)


def test_btc_contract_is_a_hundredth():
    """0.01 BTC per contract is where a naive 1:1 assumption becomes a 100x error."""
    s = spec("BTC-USDT-SWAP", ct_val=0.01, ccy="BTC", lot=1.0, minimum=1.0)
    assert base_to_contracts(s, 0.05) == pytest.approx(5.0)
    # 0.05 BTC is 5 contracts, NOT 0.05 contracts.
    assert contracts_to_base(s, 5) == pytest.approx(0.05)


def test_doge_contract_is_a_thousand():
    s = spec("DOGE-USDT-SWAP", ct_val=1000.0, ccy="DOGE", lot=1.0, minimum=1.0)
    assert base_to_contracts(s, 5000) == pytest.approx(5.0)
    assert contracts_to_base(s, 5) == pytest.approx(5000.0)


def test_floors_to_lot_size():
    s = spec(lot=0.5, minimum=0.5)
    # 3.7 contracts worth -> floor to the 0.5 grid.
    assert base_to_contracts(s, 3.7) == pytest.approx(3.5)


def test_below_min_sz_raises_rather_than_creating_dust():
    s = spec(lot=1.0, minimum=3.0)
    with pytest.raises(SwapMathError) as exc:
        base_to_contracts(s, 2.0)
    assert "minSz" in str(exc.value)


def test_zero_and_negative_are_refused():
    s = spec()
    for bad in (0.0, -1.0):
        with pytest.raises(SwapMathError):
            base_to_contracts(s, bad)


def test_notional_round_trip_respects_the_cap():
    s = spec("BTC-USDT-SWAP", ct_val=0.01, ccy="BTC")
    price = 86_000.0
    contracts = contracts_for_notional(s, notional_usd=5_000.0, price=price)
    assert contracts == pytest.approx(5.0)          # 5 x 0.01 x 86000 = 4300 <= 5000
    assert notional_usd(s, contracts, price) <= 5_000.0
    # and the NEXT contract would have breached the cap
    assert notional_usd(s, contracts + 1, price) > 5_000.0


def test_from_okx_record_parses_ctval_and_rejects_missing():
    s = ContractSpec.from_okx({
        "instId": "SOL-USDT-SWAP", "ctVal": "1", "ctValCcy": "SOL",
        "lotSz": "1", "minSz": "1", "tickSz": "0.01", "state": "live",
    })
    assert (s.ct_val, s.ct_val_ccy) == (1.0, "SOL")
    with pytest.raises(SwapMathError):
        ContractSpec.from_okx({"instId": "X", "ctVal": "", "ctValCcy": "X"})


def test_float_dust_does_not_leak_into_sizes():
    s = spec(lot=0.1, minimum=0.1)
    assert base_to_contracts(s, 0.3) == pytest.approx(0.3)
    assert base_to_contracts(s, 29.999) == pytest.approx(29.9)
