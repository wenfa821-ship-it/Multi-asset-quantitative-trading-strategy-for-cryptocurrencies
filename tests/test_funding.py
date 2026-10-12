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


def test_income_from_rates_pays_longs_and_pays_shorts_the_other_way():
    """公开费率口径：多头付、空头收；缺费率文件进 missing 而不是 0。"""
    from ta.funding import income_from_rates, series_key

    when = 10 * EIGHT
    series = FundingSeries(symbol="AAA-USDT_USDT")
    series.add(when, 0.0001)
    series = series.finalise()
    short = {"AAA/USDT:USDT": -1000.0}
    marks = {"AAA/USDT": 2.0}
    got = income_from_rates(short, marks, {"AAA-USDT_USDT": series}, when - HOUR, when)
    assert got["total"] == pytest_approx(0.2)          # $2,000 名义 × 0.01%
    assert got["partial"] is False
    long = {"AAA/USDT:USDT": 1000.0}
    assert income_from_rates(long, marks, {"AAA-USDT_USDT": series},
                             when - HOUR, when)["total"] == pytest_approx(-0.2)
    # 现货腿不付资金费；缺费率文件的腿进 missing，绝不算成 0
    mixed = income_from_rates(
        {"AAA/USDT": 1000.0, "BBB/USDT:USDT": -5.0}, marks, {}, when - HOUR, when)
    assert mixed["total"] == 0.0
    assert mixed["missing_series"] == ["BBB-USDT_USDT"]
    assert series_key("AAA/USDT:USDT") == "AAA-USDT_USDT"
    assert series_key("AAA/USDT") is None


def test_declared_counters_are_visible_even_before_they_fire():
    """2026-10-12 巡检踩到的盲区：`Counter` 里没有键的计数器**根本不出现**在 /metrics，
    于是「自进程启动以来为 0」和「指标被改名/删了」长得一模一样（那天清单里
    `ta_orders_submitted_total` / `ta_fills_total` / `ta_venue_degraded_total` 全部对不上）。
    声明过的计数器必须永远可读，值就是 0。
    """
    from ta.monitoring import EXPECTED_COUNTERS, Metrics

    metrics = Metrics()
    metrics.inc("ta_ticks_total", 7)          # 真实的计数器照旧
    out = metrics.render()
    for name in EXPECTED_COUNTERS:
        assert f"{name} 0.0" in out, name
        assert f"# TYPE {name} counter" in out
    assert "ta_ticks_total 7" in out
    metrics.inc("ta_fills_total", 3)          # 触发之后显示真值，不是 0
    assert "ta_fills_total 3" in metrics.render()


def test_live_rate_summary_reads_the_ledger_and_the_rate_files(tmp_path):
    """校准输入必须同时带两个口径：账单（事实）与公开费率（结论）。
    这条守的是后者的计算路径 —— 从账本最后一行拿持仓与标记价，乘费率序列。"""
    import json as _json

    from scripts.funding_income import live_rate_summary

    when = 10 * EIGHT
    ledger = tmp_path / "ledger.okx_demo.jsonl"
    ledger.write_text("\n".join([
        _json.dumps({"kind": "equity", "ts": when - HOUR, "equity": 1000.0}),
        _json.dumps({"kind": "equity", "ts": when, "equity": 1000.0,
                     "positions": {"AAA/USDT": 1000.0, "AAA/USDT:USDT": -1000.0},
                     "marks": {"AAA/USDT": 2.0}}),
    ]) + "\n", encoding="utf-8")
    funding = tmp_path / "funding"
    funding.mkdir()
    (funding / "AAA-USDT_USDT.jsonl").write_text(
        _json.dumps({"ts": when, "rate": 0.0001}) + "\n", encoding="utf-8")
    got = live_rate_summary(ledger, when - HOUR, when, funding_dir=funding)
    assert got["available"] is True
    assert got["total"] == pytest_approx(0.2)          # 空头 $2,000 名义 × 0.01%
    assert got["by_symbol"] == {"AAA/USDT:USDT": 0.2}
    assert got["partial"] is False                      # 费率序列覆盖到窗口末尾
    # 缺费率文件：说清楚拿不到，绝不算成 0
    missing = live_rate_summary(ledger, when - HOUR, when, funding_dir=tmp_path / "nope")
    assert missing["available"] is False and missing["total"] is None
    assert "没有费率文件" in missing["reason"]


def pytest_approx(value):
    import pytest

    return pytest.approx(value, abs=1e-9)
