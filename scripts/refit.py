#!/usr/bin/env python3
"""Disciplined parameter iteration: ACCEPT or REJECT, with the receipts.

The problem this solves: tuning by eye. You change a parameter, look at the
backtest, keep the change if the curve looks nicer, and repeat. On this project
that produced two "improvements" that evaporated the moment the sample changed
(30-day +0.18% became 180-day -0.60%). This script replaces the eye with a rule.

Usage
-----
    # is a stricter pullback actually better out of sample?
    python3 scripts/refit.py --strategy trend_pullback \
        --set rsi_entry_max=35 --folds 3

    # several changes at once are allowed but discouraged (see README)
    python3 scripts/refit.py --strategy mean_reversion \
        --set z_entry=-2.5 --set max_vol_percentile=80

Decision rule (all must hold, otherwise REJECT):
  1. the candidate beats the incumbent on the objective in >= --min-folds folds
  2. the mean improvement across folds is >= --min-improvement percentage points
     (default 0.5pp: bigger than cost noise)
  3. every fold produced >= --min-trades out-of-sample trades
  4. no fold's max drawdown exceeds --max-dd

Every trial - accepted or rejected - is appended to the changelog so you can see
the treadmill you have been running on.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.backtest import BacktestResult, run_backtest  # noqa: E402
from ta.config import Config  # noqa: E402
from ta.history import load_frames  # noqa: E402
from ta.strategies import build_strategy  # noqa: E402
from ta.util import now_ms  # noqa: E402


def parse_overrides(raw: list[str]) -> dict[str, float | int | bool | str]:
    out: dict[str, float | int | bool | str] = {}
    for item in raw:
        if "=" not in item:
            raise SystemExit(f"--set expects key=value, got {item!r}")
        key, value = item.split("=", 1)
        text = value.strip()
        if text.lower() in ("true", "false"):
            out[key.strip()] = text.lower() == "true"
        elif text.lower() in ("none", "null"):
            out[key.strip()] = 0.0
        else:
            try:
                out[key.strip()] = float(text) if "." in text or "e" in text.lower() else int(text)
            except ValueError:
                out[key.strip()] = text
    return out


def objective(result: BacktestResult, name: str) -> float:
    if name == "calmar":
        return result.calmar
    if name == "expectancy":
        return result.expectancy
    return result.total_return_pct


def evaluate(
    config: Config,
    spec_name: str,
    overrides: dict,
    frames: dict,
    symbols: list[str],
    windows: list[tuple[int, int]],
    objective_name: str,
) -> tuple[list[dict], str]:
    spec = config.strategy(spec_name)
    params = dict(spec.params)
    params.update(overrides)
    strategy = build_strategy(
        spec,
        config.risk,
        symbols,
        costs={"fee_bps": config.venue("paper").fee_bps,
               "slippage_bps": config.venue("paper").slippage_bps},
        new_symbols=config.universe.symbols_by_tier.get("new", []),
    )
    strategy.params.update(overrides)

    rows: list[dict] = []
    for index, (start_ts, end_ts) in enumerate(windows):
        result = run_backtest(
            strategy,
            frames,
            config.risk,
            timeframe=config.universe.timeframe,
            fee_bps=config.venue("paper").fee_bps,
            slippage_bps=config.venue("paper").slippage_bps,
            symbol_tiers=config.symbol_tiers,
            entry_after_ts=start_ts,
            entry_before_ts=end_ts,
        )
        rows.append(
            {
                "fold": index + 1,
                "return_pct": round(result.total_return_pct, 4),
                "max_drawdown_pct": round(result.max_drawdown_pct, 4),
                "trades": result.trades,
                "profit_factor": round(result.profit_factor, 3) if math.isfinite(result.profit_factor) else 999.0,
                "objective": round(objective(result, objective_name), 4),
            }
        )
    if not rows:
        return [], "no windows evaluated"
    return rows, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--venue", default="okx_demo")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--folds", type=int, default=3, help="contiguous out-of-sample windows")
    ap.add_argument("--objective", default="return", choices=("return", "calmar", "expectancy"))
    ap.add_argument("--min-folds", type=int, default=0, help="folds that must improve (default 2/3)")
    ap.add_argument("--min-improvement", type=float, default=0.5, help="mean improvement, percentage points")
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--max-dd", type=float, default=15.0)
    ap.add_argument("--symbols", default="", help="limit the universe to keep it fast")
    ap.add_argument("--changelog", default="reports/refit-changelog.jsonl")
    args = ap.parse_args()

    overrides = parse_overrides(args.sets)
    if not overrides:
        raise SystemExit("nothing to test: pass at least one --set key=value")
    if len(overrides) > 4:
        print("!! more than 4 parameters at once: you are fitting noise, not finding edge")

    config = Config.load(args.config_dir)
    spec = config.strategy(args.strategy)
    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()]
        or config.resolve_symbols(spec)
    )
    timeframes = list(
        dict.fromkeys(
            [config.universe.timeframe, spec.timeframe, spec.params.get("confirm_timeframe")]
        )
    )
    timeframes = [t for t in timeframes if t]
    frames = load_frames(config.data_dir / "history", args.venue, symbols, timeframes)
    if not frames:
        print(f"no local history under {config.data_dir / 'history' / args.venue}")
        return 2
    symbols = sorted(frames)

    base = frames[symbols[0]][config.universe.timeframe]
    total = len(base)
    edges = [int(total * i / args.folds) for i in range(args.folds + 1)]
    windows = [
        (base[edges[i]].ts, base[min(edges[i + 1], total - 1)].ts)
        for i in range(args.folds)
        if edges[i + 1] > edges[i]
    ]
    required = args.min_folds or max(1, math.ceil(len(windows) * 2 / 3))

    print(f"\nstrategy {args.strategy} | objective {args.objective} | folds {len(windows)}"
          f" | symbols {len(symbols)} | base bars {total}")
    print(f"candidate: {overrides}")
    print(f"rule: improve >= {required}/{len(windows)} folds, mean >= {args.min_improvement}pp,"
          f" trades >= {args.min_trades}/fold, maxDD <= {args.max_dd}%\n")

    base_rows, _ = evaluate(config, args.strategy, {}, frames, symbols, windows, args.objective)
    cand_rows, _ = evaluate(config, args.strategy, overrides, frames, symbols, windows, args.objective)

    print(f"{'fold':<6} {'incumbent':>26} {'candidate':>26} {'delta':>9}")
    deltas: list[float] = []
    for b, c in zip(base_rows, cand_rows):
        delta = c["objective"] - b["objective"]
        deltas.append(delta)
        print(
            f"{b['fold']:<6} "
            f"{b['objective']:>11.3f} n={b['trades']:<4} dd={b['max_drawdown_pct']:<6.2f} "
            f"{c['objective']:>11.3f} n={c['trades']:<4} dd={c['max_drawdown_pct']:<6.2f} "
            f"{delta:>+9.3f}"
        )

    improved = sum(1 for d in deltas if d > 0)
    mean_delta = st.mean(deltas) if deltas else 0.0
    thin = [c["fold"] for c in cand_rows if c["trades"] < args.min_trades]
    too_deep = [c["fold"] for c in cand_rows if c["max_drawdown_pct"] > args.max_dd]

    reasons: list[str] = []
    if thin:
        reasons.append(f"fold(s) {thin} have < {args.min_trades} out-of-sample trades -> inconclusive")
    if too_deep:
        reasons.append(f"fold(s) {too_deep} exceed the {args.max_dd}% drawdown budget")
    if improved < required:
        reasons.append(f"only {improved}/{len(windows)} folds improved (need {required})")
    if mean_delta < args.min_improvement:
        reasons.append(f"mean improvement {mean_delta:+.3f}pp < {args.min_improvement}pp")

    accepted = not reasons
    verdict = "ACCEPT" if accepted else "REJECT"
    print(f"\n{verdict}: mean delta {mean_delta:+.3f}pp, improved {improved}/{len(windows)} folds")
    for reason in reasons:
        print(f"  - {reason}")
    if accepted:
        print(f"  -> to apply: add {overrides} to config/strategies.toml [{args.strategy}]")
    else:
        print("  -> do not edit the config; this is noise, not edge")

    entry = {
        "ts": now_ms(),
        "strategy": args.strategy,
        "overrides": overrides,
        "objective": args.objective,
        "folds": {"incumbent": base_rows, "candidate": cand_rows},
        "mean_delta_pp": round(mean_delta, 4),
        "improved_folds": improved,
        "decision": verdict,
        "reasons": reasons,
        "symbols": symbols,
    }
    path = Path(args.changelog)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"\nlogged to {path} (every trial is kept, accepted or not)")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())

