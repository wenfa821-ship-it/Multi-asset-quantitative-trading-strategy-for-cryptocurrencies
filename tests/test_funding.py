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


def test_venue_funding_rates_outside_the_live_range_get_flagged():
    """2026-10-12: the demo venue pays CRV ~248x the live rate, hourly instead of
    every 8h, and paid us on a day the live rate was negative. `bps_per_day` comes
    from the venue bills, so anything above the plausible live range has to be
    flagged in the output - otherwise it reads as a return and gets calibrated into
    `carry_notional_pct`.

    The threshold is generous on purpose (live funding is ~1-3 bps/day; 50 bps/day
    would already be ~180% annualised), so a real market rate never trips it while
    the demo's 954-1400 bps/day always does.
    """
    from scripts.funding_income import PLAUSIBLE_BPS_PER_DAY, implausible_rates

    summary = {
        "CRV-USDT-SWAP": {"usdt": 370.29, "notional_usd": 2644.46, "bps_per_day": 1400.24},
        "ACT-USDT-SWAP": {"usdt": 5.85, "notional_usd": 2298.82, "bps_per_day": 25.47},
        "NEAR-USDT-SWAP": {"usdt": 0.28, "notional_usd": 2031.61, "bps_per_day": 1.36},
        "BAND-USDT-SWAP": {"usdt": 5.59, "notional_usd": None, "bps_per_day": None},
    }
    assert set(implausible_rates(summary)) == {"CRV-USDT-SWAP"}
    assert implausible_rates(summary)["CRV-USDT-SWAP"] > PLAUSIBLE_BPS_PER_DAY
    # 25 bps/day is already 8x a typical live rate but still inside the guard's
    # deliberately wide band - the flag is for "venue rule", not "unusual day".
    assert "ACT-USDT-SWAP" not in implausible_rates(summary)
    assert implausible_rates({}) == {}


def pytest_approx(value):
    import pytest

    return pytest.approx(value, abs=1e-9)
