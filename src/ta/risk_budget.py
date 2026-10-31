"""Read-only leg-level risk-budget utilisation.

The account has four capital sleeves, each with its own position, gross-notional
and heat limits. The dashboard historically showed the limits and the aggregate
account state, while the daily review showed refusals. Neither answered the
operator's practical question: *which budget is occupied, by what, and what has
to be released before the next trade can enter?*

This module is reporting-only. It never changes a limit or order. Derivatives
are converted through the active carry pair's base quantity and spot mark; if a
derivative has no matching pair, its notional is reported as unknown rather than
silently treated as zero.
"""

from __future__ import annotations

import datetime
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Config
from .risk.engine import leg_of


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def latest_equity(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(rows):
        if row.get("kind") == "equity":
            return row
    return None


def build_risk_budget(
    config: Config,
    ledger_rows: list[dict[str, Any]],
    *,
    pairs: dict[str, Any] | None = None,
    now_ms: int | None = None,
    total_heat_pct: float | None = None,
) -> dict[str, Any]:
    """Aggregate current exposure and 24h blocks into the four configured legs."""
    snapshot = latest_equity(ledger_rows)
    if snapshot is None:
        return {
            "available": False,
            "error": "ledger has no equity snapshot",
            "legs": [],
        }

    now_ms = int(snapshot.get("ts") or 0) if now_ms is None else int(now_ms)
    cutoff = now_ms - 24 * 60 * 60 * 1000
    positions = {
        str(symbol): float(qty or 0.0)
        for symbol, qty in (snapshot.get("positions") or {}).items()
        if abs(float(qty or 0.0)) > 1e-12
    }
    marks = {
        str(symbol): float(price or 0.0)
        for symbol, price in (snapshot.get("marks") or {}).items()
        if float(price or 0.0) > 0
    }
    stops = {
        str(symbol): float(price or 0.0)
        for symbol, price in (snapshot.get("stops") or {}).items()
        if float(price or 0.0) > 0
    }
    heat_known = "stops" in snapshot
    pairs = pairs or {}
    equity = float(snapshot.get("equity") or 0.0)
    members = {
        name: set(leg.symbols) for name, leg in config.risk.legs.items() if leg.symbols
    }
    prefixes = {
        name: leg.symbol_prefix
        for name, leg in config.risk.legs.items()
        if leg.symbol_prefix
    }

    recent_blocks = [
        row
        for row in ledger_rows
        if row.get("kind") == "blocked"
        and cutoff <= int(row.get("ts") or 0) <= now_ms
    ]

    out_legs: list[dict[str, Any]] = []
    for name, limit in config.risk.legs.items():
        leg_symbols = [
            symbol for symbol in positions
            if leg_of(symbol, members, prefixes) == name
        ]
        gross = 0.0
        missing: list[str] = []
        heat_usd = 0.0
        heat_symbols: list[str] = []
        for symbol in leg_symbols:
            if name == "derivatives":
                base = symbol.split(":", 1)[0]
                pair = pairs.get(base) or {}
                base_qty = float(pair.get("base_qty") or 0.0)
                mark = marks.get(base) or marks.get(symbol) or 0.0
                if base_qty > 0 and mark > 0:
                    gross += abs(base_qty) * mark
                else:
                    missing.append(symbol)
                continue
            mark = marks.get(symbol) or 0.0
            qty = abs(positions[symbol])
            if mark > 0:
                gross += qty * mark
            else:
                missing.append(symbol)
            stop = stops.get(symbol)
            if positions[symbol] > 0 and stop and mark > 0:
                heat_usd += abs(mark - stop) * positions[symbol]
                heat_symbols.append(symbol)

        blocked = []
        for row in recent_blocks:
            request = row.get("request") or {}
            symbol = str(request.get("symbol") or row.get("symbol") or "")
            if symbol and leg_of(symbol, members, prefixes) == name:
                blocked.append(str(row.get("reason") or "unknown"))
        reason_counts = Counter(blocked)

        max_gross_usd = round(equity * limit.max_gross_exposure_pct / 100.0, 2)
        gross_used_pct = (
            round(100.0 * gross / max_gross_usd, 2) if max_gross_usd > 0 else None
        )
        max_heat_usd = round(equity * limit.max_heat_pct / 100.0, 2)
        heat_used_pct = (
            round(100.0 * heat_usd / max_heat_usd, 2)
            if heat_known and max_heat_usd > 0 else None
        )
        positions_used = len(leg_symbols)
        if positions_used >= limit.max_open_positions:
            binding = "positions"
            release = f"平掉/移出一条仓位后才能再进（{positions_used}/{limit.max_open_positions}）"
        elif max_gross_usd > 0 and gross >= max_gross_usd * 0.95:
            binding = "gross"
            release = f"名义已用 {gross_used_pct:.1f}%，需先降到 95% 以下"
        else:
            binding = "none"
            remaining = max(0, limit.max_open_positions - positions_used)
            release = f"尚有 {remaining} 个仓位名额"

        out_legs.append({
            "leg": name,
            "positions": positions_used,
            "symbols": sorted(leg_symbols),
            "max_positions": limit.max_open_positions,
            "position_headroom": max(0, limit.max_open_positions - positions_used),
            "gross_usd": round(gross, 2),
            "gross_known": not missing,
            "gross_missing_symbols": sorted(missing),
            "max_gross_usd": max_gross_usd,
            "gross_used_pct": gross_used_pct,
            "gross_headroom_usd": round(max(0.0, max_gross_usd - gross), 2),
            "max_heat_pct": limit.max_heat_pct,
            "heat_usd": round(heat_usd, 2) if heat_known else None,
            "max_heat_usd": max_heat_usd,
            "heat_used_pct": heat_used_pct,
            "heat_headroom_usd": (
                round(max(0.0, max_heat_usd - heat_usd), 2) if heat_known else None
            ),
            "heat_known": heat_known,
            "heat_stop_symbols": sorted(heat_symbols),
            "blocked_24h": len(blocked),
            "blocked_reasons": [
                {"reason": reason, "count": count}
                for reason, count in reason_counts.most_common(8)
            ],
            "binding": binding,
            "release_condition": release,
        })

    return {
        "available": True,
        "generated_at": datetime.datetime.fromtimestamp(
            now_ms / 1000.0, datetime.timezone.utc
        ).isoformat(),
        "as_of": int(snapshot.get("ts") or 0),
        "equity": round(equity, 2),
        "positions": len(positions),
        "max_positions": config.risk.account.max_open_positions,
        "total_heat_pct": total_heat_pct,
        "heat_scope": (
            "per-leg from persisted protective stops"
            if heat_known
            else "legacy snapshot has no stops; per-leg heat unavailable"
        ),
        "legs": out_legs,
    }


def default_inputs(config: Config) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = _read_jsonl(Path(config.data_dir) / "ledger.okx_demo.jsonl")
    pairs = _read_json(Path(config.data_dir) / "carry" / "pairs.json", {})
    return rows, pairs


def markdown(report: dict[str, Any]) -> str:
    if not report.get("available"):
        return "# 风险预算\n\n无法生成：" + str(report.get("error") or "unknown") + "\n"
    as_of = datetime.datetime.fromtimestamp(
        int(report["as_of"]) / 1000.0,
        datetime.timezone(datetime.timedelta(hours=8)),
    ).strftime("%Y-%m-%d %H:%M:%S CST")
    heat = report.get("total_heat_pct")
    heat_text = "不可用" if heat is None else f"{float(heat):.3f}%"
    out = [
        "# 风险预算占用",
        "",
        f"数据时点 **{as_of}** · 权益 ${report['equity']:,.2f} · "
        f"账户 heat **{heat_text}**",
        "",
        f"> heat 口径：{report.get('heat_scope', 'unknown')}。",
        "",
        "| 腿 | 仓位 | 名义/上限 | heat/上限 | 24h 阻塞 | 当前约束 | 释放条件 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for leg in report["legs"]:
        gross = (
            f"${leg['gross_usd']:,.0f} / ${leg['max_gross_usd']:,.0f}"
            if leg["gross_known"]
            else f"未知 / ${leg['max_gross_usd']:,.0f}"
        )
        heat_cell = (
            f"${leg['heat_usd']:,.0f} / ${leg['max_heat_usd']:,.0f}"
            if leg.get("heat_known")
            else f"未知 / ${leg['max_heat_usd']:,.0f}"
        )
        heat_used = (
            f"{leg['heat_used_pct']:.1f}%"
            if leg.get("heat_used_pct") is not None else "未知"
        )
        out.append(
            f"| {leg['leg']} | {leg['positions']}/{leg['max_positions']} | {gross} | "
            f"{heat_cell}（{heat_used}） | {leg['blocked_24h']} | {leg['binding']} | "
            f"{leg['release_condition']} |"
        )
    out += ["", "## 最近 24h 阻塞原因", ""]
    for leg in report["legs"]:
        if not leg["blocked_reasons"]:
            continue
        reasons = "；".join(
            f"{row['reason']} ×{row['count']}" for row in leg["blocked_reasons"]
        )
        out.append(f"- **{leg['leg']}**：{reasons}")
    if not any(leg["blocked_reasons"] for leg in report["legs"]):
        out.append("- 无结构化 blocked 记录。")
    missing = [
        f"{leg['leg']}: {', '.join(leg['gross_missing_symbols'])}"
        for leg in report["legs"] if leg["gross_missing_symbols"]
    ]
    if missing:
        out += ["", "## 名义未知项", ""] + [f"- {row}" for row in missing]
    return "\n".join(out) + "\n"
