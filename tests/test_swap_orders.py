"""Swap order construction: the reduceOnly / posSide traps."""

from __future__ import annotations

import pytest

from ta.venues.swap_math import ContractSpec
from ta.venues.swap_orders import (
    SwapOrderError,
    assert_exit_cannot_flip,
    build_order_params,
    parse_position,
    pos_side_for,
)


def spec(inst="SOL-USDT-SWAP", ct_val=1.0, minimum=1.0, tick=0.01):
    return ContractSpec(
        inst_id=inst, ct_val=ct_val, ct_val_ccy="SOL",
        lot_sz=1.0, min_sz=minimum, tick_sz=tick,
    )


def test_entry_side_maps_to_pos_side():
    assert pos_side_for("buy", reduce_only=False) == "long"
    assert pos_side_for("sell", reduce_only=False) == "short"


def test_exit_keeps_the_held_side():
    """Closing a long is side=sell posSide=long - NOT opening a short."""
    assert pos_side_for("sell", reduce_only=True, current="long") == "long"
    assert pos_side_for("buy", reduce_only=True, current="short") == "short"


def test_exit_without_a_position_is_refused():
    with pytest.raises(SwapOrderError):
        pos_side_for("sell", reduce_only=True, current="net")


def test_market_entry_params():
    p = build_order_params(spec(), side="buy", contracts=42, td_mode="cross")
    assert p == {
        "instId": "SOL-USDT-SWAP", "tdMode": "cross", "side": "buy",
        "posSide": "long", "ordType": "market", "sz": "42",
    }
    assert "reduceOnly" not in p


def test_exit_carries_reduce_only_and_same_pos_side():
    p = build_order_params(
        spec(), side="sell", contracts=42, reduce_only=True, current_pos_side="long"
    )
    assert p["posSide"] == "long"
    assert p["reduceOnly"] == "true"


def test_limit_order_includes_price():
    p = build_order_params(spec(), side="buy", contracts=1, price=121.19)
    assert p["ordType"] == "limit" and p["px"] == "121.19"


def test_bad_inputs_are_refused():
    with pytest.raises(SwapOrderError):
        build_order_params(spec(), side="buy", contracts=0)
    with pytest.raises(SwapOrderError):
        build_order_params(spec(minimum=5.0), side="buy", contracts=1)
    with pytest.raises(SwapOrderError):
        build_order_params(spec(), side="buy", contracts=1, td_mode="cash")
    with pytest.raises(SwapOrderError):
        build_order_params(spec(), side="hold", contracts=1)


def test_parse_position_converts_contracts_to_base():
    s = spec("BTC-USDT-SWAP", ct_val=0.01)
    pos = parse_position(
        {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "5", "avgPx": "86000",
         "notionalUsd": "4300", "upl": "12.5", "liqPx": "50000", "lever": "3"},
        s,
    )
    assert pos.base_qty == pytest.approx(0.05)     # 5 contracts x 0.01 BTC
    assert pos.notional_usd == pytest.approx(4300.0)
    assert pos.unrealized_usd == pytest.approx(12.5)
    assert not pos.is_flat


def test_parse_flat_position_by_instid_only():
    pos = parse_position({"instId": "SOL-USDT-SWAP", "pos": "0"}, spec())
    assert pos.is_flat and pos.pos_side == "net"


def test_exit_guard_blocks_the_flip_bug():
    s = spec()
    held = parse_position({"instId": "SOL-USDT-SWAP", "posSide": "long", "pos": "10"}, s)
    good = build_order_params(s, side="sell", contracts=10, reduce_only=True)
    assert_exit_cannot_flip(held, good)          # fine

    flipped = build_order_params(s, side="buy", contracts=10, reduce_only=True)
    with pytest.raises(SwapOrderError):
        assert_exit_cannot_flip(held, flipped)

    with pytest.raises(SwapOrderError):
        assert_exit_cannot_flip(held, {"side": "sell", "posSide": "long"})

    with pytest.raises(SwapOrderError):
        assert_exit_cannot_flip(parse_position({"instId": "X", "pos": "0"}, s), good)
