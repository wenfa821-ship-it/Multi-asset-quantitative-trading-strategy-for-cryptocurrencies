#!/usr/bin/env python3
"""What does buying premium actually cost, against what the underlying delivers?

README 20.3 said, before any options work existed, that with no directional edge
"buying premium is paying IV continuously". This turns that sentence into numbers
using the venue's own surface (scripts/options_surface.py) plus the daily history
we already hold.

For each family it reports, at the expiry closest to 30 days:

  * ATM implied vol (the price of the option)
  * realized vol over the same window (what the underlying actually delivered)
  * the difference - the variance risk premium, which is the number a premium
    BUYER pays and a seller collects
  * the ATM straddle cost in % of spot, and the move it needs to break even

Read-only and offline: it reads data/options/surface.json and the local daily
bars. No keys, no orders.

    python3 scripts/options_surface.py          # refresh the surface first
    python3 scripts/options_cost.py
"""

from __future__ import annotations

import json
import math
import statistics as st
import sys
import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.history import read_bars  # noqa: E402

# family -> the spot symbol whose history measures the same underlying
FAMILY_SYMBOL = {"BTC-USD": "BTC/USDT", "ETH-USD": "ETH/USDT", "SOL-USD": "SOL/USDT"}


def realized_vol(daily_closes: list[float], window: int = 30) -> float | None:
    """Annualised close-to-close realized vol over the last `window` days."""
    if len(daily_closes) < window + 1:
        return None
    recent = daily_closes[-(window + 1):]
    rets = [math.log(b / a) for a, b in zip(recent, recent[1:]) if a > 0 and b > 0]
    if len(rets) < 5:
        return None
    return st.pstdev(rets) * math.sqrt(365) * 100.0


def main() -> int:
    surface_path = PROJECT / "data" / "options" / "surface.json"
    if not surface_path.exists():
        print("no surface yet - run scripts/options_surface.py first")
        return 1
    payload = json.loads(surface_path.read_text(encoding="utf-8"))
    history_dir = PROJECT / "data" / "history" / "okx_demo"

    print(f"{'家族':<10}{'到期':<9}{'DTE':>5}{'ATM IV':>9}{'已实现RV':>10}"
          f"{'IV-RV':>8}{'RV30参考':>10}{'跨式成本':>10}{'盈亏平衡':>10}")
    rows_out = []
    # A family that failed to fetch upstream is simply absent from the surface
    # file, which would otherwise just mean one fewer row and no complaint. Say
    # so out loud instead: the whole point of the daily append is the sample.
    absent = sorted(set(FAMILY_SYMBOL) - set(payload.get("families") or {}))
    if absent:
        print(f"WARNING surface is missing {', '.join(absent)} - "
              f"no row written for it today (see options_surface.py log)")
    for family, summary in (payload.get("families") or {}).items():
        spot = float(summary.get("underlying_px") or 0.0)
        symbol = FAMILY_SYMBOL.get(family)
        if not spot or not symbol:
            continue
        path = history_dir / f"{symbol.replace('/', '-')}__1d.jsonl"
        if not path.exists():
            continue
        closes = [b.close for b in read_bars(path)]
        # Kept only as a reference column: the premium below is measured against
        # the realized vol of the SAME tenor as the contract being priced.
        rv30 = realized_vol(closes, 30)
        # The expiry closest to ~30 days. This has to use the REAL days to
        # expiry: an earlier version compared the day-of-month against 9 and so
        # picked the contract expiring TOMORROW, then compared its 1-day IV with a
        # 30-day realized vol. Different horizons, meaningless difference.
        today = datetime.datetime.now(datetime.UTC).date()

        def dte_of(expiry: str) -> int:
            year = 2000 + int(expiry[:2])
            month, day = int(expiry[2:4]), int(expiry[4:6])
            return (datetime.date(year, month, day) - today).days

        expiries = [
            e for e in summary.get("expiries") or []
            if e.get("atm_iv_pct") and dte_of(str(e["expiry"])) >= 1
        ]
        if not expiries:
            continue
        target = min(expiries, key=lambda e: abs(dte_of(str(e["expiry"])) - 30))
        iv = float(target["atm_iv_pct"]) / 100.0
        dte = float(dte_of(str(target["expiry"])))
        # HORIZON ALIGNMENT (README 71). IV is quoted for the contract's tenor, so
        # the realized vol it is compared against must cover the same window - a
        # variance premium is IV minus what the underlying actually delivered OVER
        # THAT PERIOD. Measured 2026-10-09: SOL has no ~30-day listing, so the
        # chosen contract was DTE 7 and its "-13.6pp premium" was really a 7-day IV
        # minus a 30-day RV. This file's own notes record the same mistake once
        # before (a 1-day IV against a 30-day RV), which is why it is now computed
        # rather than assumed.
        rv_window = max(2, int(round(dte)))
        rv_dte = realized_vol(closes, rv_window)
        rv = rv_dte if rv_dte is not None else rv30
        if rv is None:
            print(f"  {family}: no usable realized vol (need {rv_window + 1} daily closes)")
            continue
        # ATM straddle ~ 0.8 x sigma x sqrt(T) of spot; breakeven move is the same
        # number, because you need the underlying to travel that far to get the
        # premium back.
        straddle_pct = 0.8 * iv * math.sqrt(dte / 365.0) * 100.0
        row = {
            "family": family,
            "expiry": target["expiry"],
            "atm_iv_pct": round(iv * 100, 2),
            "dte_days": int(dte),
            "rv_window_days": rv_window,
            "realized_dte_pct": round(rv, 2),
            # The old field name is kept so the existing history stays readable;
            # it is now the matched-tenor number, not the 30-day one.
            "realized_30d_pct": round(rv, 2),
            "realized_30d_reference_pct": None if rv30 is None else round(rv30, 2),
            "variance_premium_pp": round(iv * 100 - rv, 2),
            "variance_premium_30d_pp": None if rv30 is None else round(iv * 100 - rv30, 2),
            "straddle_cost_pct": round(straddle_pct, 2),
            "breakeven_move_pct": round(straddle_pct, 2),
        }
        rows_out.append(row)
        rv30_text = f"{rv30:.1f}%" if rv30 is not None else "n/a"
        print(f"{family:<10}{target['expiry']:<9}{dte:>5.0f}{iv * 100:>8.1f}%"
              f"{rv:>9.1f}%{iv * 100 - rv:>+7.1f}{rv30_text:>10}"
              f"{straddle_pct:>9.2f}%{straddle_pct:>9.2f}%")

    if rows_out:
        prem = [r["variance_premium_pp"] for r in rows_out]
        print(f"\n方差风险溢价（IV − RV）: 中位 {st.median(prem):+.2f} pp，"
              f"{sum(1 for p in prem if p > 0)}/{len(prem)} 为正")
        print("为正 = 买权利金在为波动率付费；这就是『买期权』要先打败的那条线。")

    target = PROJECT / "data" / "options" / "cost.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"rows": rows_out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"written: {target}")

    # A single snapshot cannot answer "is BTC systematically cheaper than ETH?"
    # - that needs the same measurement repeated over days, so every run appends
    # one row per family to a time series (idempotent per UTC day).
    history = PROJECT / "data" / "options" / "cost_history.jsonl"
    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    existing = []
    if history.exists():
        existing = [json.loads(l) for l in history.read_text(encoding="utf-8").splitlines() if l.strip()]
    existing = [r for r in existing if not (r.get("date") == today)]
    for row in rows_out:
        existing.append({"date": today, **row})
    history.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in existing) + "\n",
        encoding="utf-8",
    )
    days = len({r.get("date") for r in existing})
    print(f"appended to {history}  ({days} day(s) of history)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
