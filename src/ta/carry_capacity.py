"""Audit whether carry's full slots are blocking persistent, independent edge.

Capacity blocks are noisy: the runner may emit the same candidate every few
minutes. Counting those rows as "opportunities" overstates demand. This module
collapses them by UTC day and token, then applies the project's two structural
conditions before a slot expansion can be considered:

* at least 20 distinct tokens were blocked in the window;
* at least one token cleared the configured net-edge floor on every observed day.

The output is decision support only. It never changes risk limits or positions.
"""

from __future__ import annotations

import datetime
import statistics
from collections import Counter, defaultdict
from typing import Any


def _date_of(ts_ms: int) -> str:
    return datetime.datetime.fromtimestamp(
        ts_ms / 1000.0, datetime.timezone.utc
    ).date().isoformat()


def carry_capacity_audit(
    rows: list[dict[str, Any]],
    *,
    now_ms: int,
    days: int = 3,
    min_edge_bps: float = 20.0,
    required_tokens: int = 20,
    current_slots: int = 5,
) -> dict[str, Any]:
    days = max(1, int(days))
    end_day = datetime.datetime.fromtimestamp(
        now_ms / 1000.0, datetime.timezone.utc
    ).date()
    start_day = end_day - datetime.timedelta(days=days - 1)
    start_ms = int(datetime.datetime.combine(
        start_day, datetime.time.min, tzinfo=datetime.timezone.utc
    ).timestamp() * 1000)

    by_symbol_day: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    blocks_by_day: Counter[str] = Counter()
    for row in rows:
        if row.get("kind") != "blocked":
            continue
        reason = str(row.get("reason") or "")
        if not reason.startswith("leg_positions: spot"):
            continue
        ts = int(row.get("ts") or 0)
        if not (start_ms <= ts <= now_ms):
            continue
        request = row.get("request") or {}
        if str(request.get("tag") or "") != "carry":
            continue
        symbol = str(request.get("symbol") or row.get("symbol") or "")
        if not symbol:
            continue
        blocks_by_day[_date_of(ts)] += 1
        edge = row.get("expected_net_bps")
        if isinstance(edge, (int, float)):
            by_symbol_day[symbol][_date_of(ts)].append(float(edge))

    candidates: list[dict[str, Any]] = []
    for symbol, dates in by_symbol_day.items():
        daily = {
            day: round(statistics.median(values), 3)
            for day, values in sorted(dates.items())
            if values
        }
        if not daily:
            continue
        daily_values = list(daily.values())
        max_possible_days = min(days, (end_day - start_day).days + 1)
        persistent = (
            len(daily) >= max_possible_days
            and min(daily_values) >= min_edge_bps
        )
        candidates.append({
            "symbol": symbol,
            "days_observed": len(daily),
            "daily_median_expected_net_bps": daily,
            "min_daily_bps": round(min(daily_values), 3),
            "median_daily_bps": round(statistics.median(daily_values), 3),
            "persistent": persistent,
        })
    candidates.sort(
        key=lambda row: (
            not row["persistent"], row["days_observed"],
            row["median_daily_bps"], row["symbol"],
        )
    )

    distinct_tokens = len(by_symbol_day)
    persistent_tokens = sum(1 for row in candidates if row["persistent"])
    sample_ready = distinct_tokens >= required_tokens
    expansion_ready = sample_ready and persistent_tokens > 0
    if not sample_ready:
        verdict = (
            f"keep {current_slots}/{current_slots}: only {distinct_tokens} distinct "
            f"tokens, need {required_tokens}"
        )
    elif not persistent_tokens:
        verdict = (
            f"keep {current_slots}/{current_slots}: no candidate cleared "
            f"{min_edge_bps:g} bps on every day of the {days}-day window"
        )
    else:
        verdict = (
            f"slot review allowed: {persistent_tokens} persistent candidate(s); "
            "requires user approval before changing limits"
        )
    return {
        "generated_at": datetime.datetime.fromtimestamp(
            now_ms / 1000.0, datetime.timezone.utc
        ).isoformat(),
        "window_start": start_day.isoformat(),
        "window_end": end_day.isoformat(),
        "days": days,
        "min_edge_bps": min_edge_bps,
        "required_tokens": required_tokens,
        "current_slots": current_slots,
        "capacity_blocks": sum(blocks_by_day.values()),
        "capacity_blocks_by_day": dict(sorted(blocks_by_day.items())),
        "distinct_tokens": distinct_tokens,
        "persistent_tokens": persistent_tokens,
        "eligible_candidates": [
            row for row in candidates if row["persistent"]
        ],
        "candidates": candidates,
        "sample_ready": sample_ready,
        "expansion_ready": expansion_ready,
        "verdict": verdict,
    }


def markdown(report: dict[str, Any]) -> str:
    out = [
        "# carry 名额价值审计",
        "",
        f"窗口 **{report['window_start']} → {report['window_end']} UTC** · "
        f"{report['days']} 天 · 净边际门槛 {report['min_edge_bps']:g} bps/天",
        "",
        f"- 容量拦截：**{report['capacity_blocks']}** 次 "
        f"（按日：{report['capacity_blocks_by_day']}）",
        f"- 独立代币：**{report['distinct_tokens']}**（扩容审查至少需要 "
        f"{report['required_tokens']}）",
        f"- 连续 {report['days']} 天达标的候选：**{report['persistent_tokens']}**",
        "",
        f"**结论**：{report['verdict']}。",
        "",
        "| 代币 | 观察天数 | 各日中位 bps | 最差日 | 连续达标 |",
        "|---|---:|---|---:|---|",
    ]
    for row in report["candidates"][:20]:
        daily = "；".join(
            f"{day} {bps:+.2f}" for day, bps in row["daily_median_expected_net_bps"].items()
        )
        out.append(
            f"| {row['symbol']} | {row['days_observed']} | {daily} | "
            f"{row['min_daily_bps']:+.2f} | {'是' if row['persistent'] else '否'} |"
        )
    if not report["candidates"]:
        out.append("| — | 0 | 无结构化容量机会成本 | — | 否 |")
    return "\n".join(out) + "\n"
