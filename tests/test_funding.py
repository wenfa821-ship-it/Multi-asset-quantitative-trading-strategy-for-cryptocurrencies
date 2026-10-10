"""Funding lookup: no peeking forward, and count every settlement crossed."""

from __future__ import annotations

import json

from ta.funding import FundingSeries, load_funding, load_funding_dir

HOUR = 3_600_000
EIGHT = 8 * HOUR


def series():
    s = FundingSeries(symbol="SOL-USDT_USDT")
    # 3 settlements, 8h apart, deliberately out of order in one case
    s.add(10 * EIGHT, 0.0001)     # +1.0 bps
    s.add(11 * EIGHT, -0.0002)    # -2.0 bps
    s.add(12 * EIGHT, 0.0003)     # +3.0 bps
    return s.finalise()


def test_rate_at_never_looks_forward():
    s = series()
    assert s.rate_at(9 * EIGHT) is None          # before the series starts
    assert s.rate_at(10 * EIGHT) == 0.0001       # exactly on a settlement
    assert s.rate_at(11 * EIGHT - 1) == 0.0001   # just before the next one
    assert s.rate_at(11 * EIGHT) == -0.0002
    assert s.rate_at(99 * EIGHT) == 0.0003       # after the end: last known, not extrapolated


def test_settlements_between_counts_only_the_window():
    s = series()
    assert s.settlements_between(10 * EIGHT, 12 * EIGHT) == 2   # (10,12] -> 11,12
    assert s.settlements_between(10 * EIGHT, 10 * EIGHT) == 0
    assert s.settlements_between(9 * EIGHT, 10 * EIGHT) == 1


def test_long_pays_positive_funding_and_short_receives_it():
    s = series()
    # holding from settlement 10 through settlement 12 crosses 11 (+ -2bps) and 12 (+3bps)
    long_pct = s.cost_pct(10 * EIGHT, 12 * EIGHT, "buy")
    assert long_pct == pytest_approx(0.01)       # (-2 + 3) bps = +1 bps = 0.01%
    assert s.cost_pct(10 * EIGHT, 12 * EIGHT, "sell") == pytest_approx(-0.01)


def test_no_settlement_means_no_cost():
    s = series()
    assert s.cost_pct(10 * EIGHT + 1, 10 * EIGHT + HOUR, "buy") == 0.0


def test_load_from_disk(tmp_path):
    p = tmp_path / "SOL-USDT_USDT.jsonl"
    p.write_text(
        json.dumps({"ts": 1000, "rate": 0.0001}) + "\n"
        + "\n"                                    # blank line tolerated
        + json.dumps({"ts": 2000, "rate": -0.0001}) + "\n"
        + "{not json}\n",
        encoding="utf-8",
    )
    s = load_funding(p)
    assert s.symbol == "SOL-USDT_USDT"
    assert s.rate_at(1500) == 0.0001
    assert s.rate_at(2000) == -0.0001


def test_load_dir_is_empty_when_missing(tmp_path):
    assert load_funding_dir(tmp_path / "nope") == {}
    assert set(load_funding_dir(tmp_path)) == set()


def pytest_approx(value):
    import pytest

    return pytest.approx(value, abs=1e-9)
