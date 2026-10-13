"""Snapshot store + event study: does this information actually predict anything?

The design constraint that makes this honest: **forward returns come from the
store itself.** The scanner logs a price for every token at every scan, so the
return after a signal is just the same token's price at a later timestamp.
Nothing is reconstructed from hindsight, and survivorship bias cannot sneak in
because a token that dies simply stops producing later rows (and counts as
missing data rather than being dropped).

What this refuses to do:
  * rank tokens by "how they did" - that is selection, not validation;
  * report a bucket without its coverage (if 90% of signals have no forward
    observation, the remaining 10% prove nothing).
"""

from __future__ import annotations

import json
import statistics as st
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from ..util import now_ms
from .models import ScreenResult, TokenSnapshot


def collapse(rows: list[dict[str, Any]], bucket_seconds: int = 60,
             ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(kept, dropped) for one observation per (token, time bucket), newest wins.

    The single implementation behind `dedupe_rows` and `collapse_stats` — the count
    you print and the rows you study must come from the same rule (2026-10-13).
    """
    if bucket_seconds <= 0:
        return list(rows), []
    span = bucket_seconds * 1000
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    dropped: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("mint")), int(row.get("ts", 0)) // span)
        current = latest.get(key)
        if current is None or int(row.get("ts", 0)) >= int(current.get("ts", 0)):
            if current is not None:
                dropped.append(current)
            latest[key] = row
        else:
            dropped.append(row)
    return sorted(latest.values(), key=lambda r: int(r.get("ts", 0))), dropped


def dedupe_rows(rows: list[dict[str, Any]], bucket_seconds: int = 60) -> list[dict[str, Any]]:
    """Collapse to one observation per (token, time bucket), keeping the newest.

    Measured 2026-09-29: 54% of the store was duplicate writes from 2-4 watcher
    processes appending to the same file. Duplicates are not harmless - they make
    one observation look like several independent samples, inflating n and
    manufacturing zero-length "forward returns" from two writes in the same
    minute. Deduplicating here keeps the study valid however many writers run.
    """
    return collapse(rows, bucket_seconds)[0]


def collapse_stats(rows: list[dict[str, Any]], bucket_seconds: int = 60,
                   now_ms: int | None = None, recent_hours: float = 24.0) -> dict[str, Any]:
    """去重折叠了哪些行、**什么时候**的行 —— 因为"有多少重复"和"现在还重复吗"是两个问题。

    2026-10-13：`ta scan evaluate` 每轮打印「11% 是重复写入，说明有多个扫描器在写同一账本」。
    按天拆开那份账本：7,269 条被折叠的行里 **6,627 条来自 09-30、627 条来自 09-29，
    10-02 之后基本为 0** —— 那是清理多扫描器时期留下的历史，现在只有一个 watcher 在写。
    把历史合计当当前状况念出来，会让人去找不存在的重复进程（而这个项目的 `ps`/`pgrep`
    在这个沙箱里本来就是假阴性，正好凑成一次误报）。
    """
    import datetime as _dt

    kept, dropped = collapse(rows, bucket_seconds)
    now = now_ms or int(_dt.datetime.now(_dt.timezone.utc).timestamp() * 1000)
    cutoff = now - int(recent_hours * 3_600_000)
    by_day: dict[str, int] = {}
    recent = 0
    newest = 0
    for row in dropped:
        ts = int(row.get("ts", 0) or 0)
        day = _dt.datetime.fromtimestamp(ts / 1000, _dt.timezone.utc).date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
        if ts >= cutoff:
            recent += 1
        newest = max(newest, ts)
    return {
        "rows": len(rows),
        "kept": len(kept),
        "dropped": len(dropped),
        "rate": (1 - len(kept) / len(rows)) if rows else 0.0,
        "by_day": by_day,
        "recent_dropped": recent,
        "recent_hours": recent_hours,
        "last_dropped_ts": newest or None,
    }


def collapse_note(stats: dict[str, Any]) -> str:
    """One honest line about the collapse: rate, when, and whether it is still happening."""
    if not stats.get("dropped"):
        return f"去重：{stats['rows']} 条（无同分钟重复采样）"
    top = sorted((stats.get("by_day") or {}).items(), key=lambda kv: -kv[1])[:2]
    when = "、".join(f"{day} 占 {n}" for day, n in top)
    head = (f"去重：{stats['rows']} -> {stats['kept']} 条唯一观测"
            f"（{100 * stats['rate']:.0f}% 被折叠：同一代币同一分钟内的重复采样，"
            f"取最新一条；按天看主要是 {when}）")
    if stats.get("recent_dropped"):
        head += (f"。近 {stats['recent_hours']:g} 小时仍有 {stats['recent_dropped']} 条 ——"
                 f"**这可能是多个扫描器在写同一账本**，去数进程之前先看 "
                 f"`lsof <store>` 与 watcher 的 runs")
    else:
        head += (f"。近 {stats['recent_hours']:g} 小时**没有**新的重复 —— "
                 f"这是历史遗留的折叠，不是现在有多个扫描器")
    return head


def _field_value(row: dict[str, Any], field: str) -> float | None:
    """The dimension being studied.

    Components beat composites when data is thin: six tokens can all land in one
    score bucket while still spanning 1.5x-57x turnover.
    """
    if field == "score":
        return None if row.get("score") is None else float(row["score"])
    if field == "liquidity":
        return float(row.get("liquidity_usd") or 0.0) or None
    if field == "turnover":
        liquidity = float(row.get("liquidity_usd") or 0.0)
        volume = float(row.get("volume_24h_usd") or 0.0)
        return (volume / liquidity) if liquidity > 0 else None
    if field == "boost":
        value = row.get("boost_amount")
        return None if value is None else float(value)
    if field == "age":
        return float(row.get("age_minutes") or 0.0) or None
    if field == "txns_m5":
        value = row.get("txns_m5")
        return None if value is None else float(value)
    return None


def zero_change_ratio(
    rows: list[dict[str, Any]], *, tolerance: float = 1e-9
) -> dict[str, Any]:
    """How much of the store is "the price did not move at all"?

    This is the honesty metric the study needed. Measured 2026-09-30: with 98%
    coverage on the +5m horizon the median forward return was still exactly 0.00%
    and the win rate 0.8% - a distribution that can only come from tokens that are
    not trading, where "the price in 5 minutes" is the same number as now. Bucket
    tables full of those prove nothing, so report the share of frozen observations
    next to them.
    """
    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for row in rows:
        price = row.get("price_usd") or 0.0
        if price > 0:
            series[str(row.get("mint"))].append((int(row.get("ts", 0)), float(price)))

    pairs = 0
    unchanged = 0
    tokens = 0
    frozen_tokens = 0
    for values in series.values():
        if len(values) < 2:
            continue
        tokens += 1
        values.sort()
        token_unchanged = 0
        for (_, before), (_, after) in zip(values, values[1:]):
            pairs += 1
            if abs(after - before) <= abs(before) * tolerance:
                unchanged += 1
                token_unchanged += 1
        if token_unchanged == len(values) - 1:
            frozen_tokens += 1
    return {
        "tokens_with_pairs": tokens,
        "pairs": pairs,
        "unchanged_pairs": unchanged,
        "unchanged_fraction": round(unchanged / pairs, 4) if pairs else 0.0,
        "tokens_frozen": frozen_tokens,
        "tokens_frozen_fraction": round(frozen_tokens / tokens, 4) if tokens else 0.0,
    }


DEFAULT_EDGES: dict[str, tuple[float, ...]] = {
    "score": (0.0, 40.0, 60.0, 80.0, 1000.0),
    "liquidity": (0.0, 25_000.0, 50_000.0, 150_000.0, 1e12),
    "turnover": (0.0, 1.0, 5.0, 20.0, 1e12),
    "boost": (-1.0, 0.5, 50.0, 1e12),
    "age": (0.0, 60.0, 1440.0, 10080.0, 1e12),
    "txns_m5": (0.0, 5.0, 25.0, 100.0, 1e12),
}


class SnapshotStore:
    """Append-only JSONL of (timestamp, token, score) observations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        snapshot: TokenSnapshot,
        screen: ScreenResult | None = None,
        *,
        source: str = "",
        ts: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "ts": ts if ts is not None else now_ms(),
            "mint": snapshot.mint,
            "symbol": snapshot.symbol,
            "price_usd": snapshot.price_usd,
            "pair_address": snapshot.pair_address,
            "liquidity_usd": snapshot.liquidity_usd,
            "volume_24h_usd": snapshot.volume_24h_usd,
            "holder_count": snapshot.holder_count,
            "top10_holder_pct": snapshot.top10_holder_pct,
            "lp_locked": snapshot.lp_locked,
            "lp_locked_pct": snapshot.lp_locked_pct,
            "age_minutes": snapshot.age_minutes,
            "is_honeypot": snapshot.is_honeypot,
            "safety_scan_ok": snapshot.safety_scan_ok,
            "provided": snapshot.provided,
            "sources": snapshot.sources,
            "score": screen.score if screen else None,
            "passed": screen.passed if screen else None,
            "reasons": screen.reasons if screen else [],
            "source": source,
        }
        if extra:
            row.update(extra)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return row

    def rows(self, since_ts: int | None = None, mint: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since_ts is not None and row.get("ts", 0) < since_ts:
                    continue
                if mint is not None and row.get("mint") != mint:
                    continue
                out.append(row)
        return out


def _nearest_after(series: list[tuple[int, float]], target_ts: int) -> tuple[int, float] | None:
    """First observation at or after `target_ts`, as (ts, price, pool)."""
    best: tuple[int, float, str] | None = None
    for ts, price, pool in series:
        if ts >= target_ts and (best is None or ts < best[0]):
            best = (ts, price, pool)
    return best


def attach_forward_returns(
    rows: list[dict[str, Any]],
    horizons_seconds: Iterable[int],
    *,
    tolerance: float = 0.5,
) -> list[dict[str, Any]]:
    """For every snapshot, find the price at ts + horizon from later rows.

    `tolerance` is a fraction of the horizon: a 1h horizon accepts an
    observation up to 30 minutes late. Tighter tolerances mean more missing data.
    """
    series: dict[str, list[tuple[int, float, str]]] = defaultdict(list)
    for row in rows:
        price = row.get("price_usd") or 0.0
        if price > 0:
            series[row["mint"]].append(
                (int(row["ts"]), float(price), str(row.get("pair_address") or ""))
            )
    for values in series.values():
        values.sort()

    horizons = list(horizons_seconds)
    out: list[dict[str, Any]] = []
    pool_switch_dropped = 0
    for row in rows:
        price = row.get("price_usd") or 0.0
        enriched = dict(row)
        enriched["fwd"] = {}
        if price > 0:
            for horizon in horizons:
                target = int(row["ts"]) + horizon * 1000
                found = _nearest_after(series.get(row["mint"], []), target)
                if found is None:
                    continue
                ts, later, later_pool = found
                if ts > target + int(horizon * 1000 * tolerance):
                    continue
                # A price from a DIFFERENT pool is not the same asset's price.
                # Differencing across a pool switch produces the fake +8x/-100%
                # jumps that carried the whole positive mean before this guard.
                here_pool = str(row.get("pair_address") or "")
                if here_pool and later_pool and here_pool != later_pool:
                    pool_switch_dropped += 1
                    continue
                if ts <= target + int(horizon * 1000 * tolerance):
                    enriched["fwd"][horizon] = 100.0 * (later / price - 1.0)
        out.append(enriched)
    for enriched in out:
        enriched["pool_switch_dropped"] = pool_switch_dropped
    return out


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(values),
        "median": round(st.median(values), 3),
        "mean": round(st.fmean(values), 3),
        "win_rate": round(100.0 * sum(1 for v in values if v > 0) / len(values), 1),
        "p10": round(ordered[int(0.10 * (len(ordered) - 1))], 3),
        "p90": round(ordered[int(0.90 * (len(ordered) - 1))], 3),
    }


def bucket_report(
    rows: list[dict[str, Any]],
    horizons_seconds: Iterable[int],
    *,
    bucket_by: str = "score",
    edges: tuple[float, ...] | None = None,
    min_txns_m5: int = 0,
) -> dict[str, Any]:
    """Forward-return distribution per bucket of ONE dimension - the verdict.

    `bucket_by` accepts score / liquidity / turnover / boost / age. Studying a
    component is not a downgrade: with a handful of tokens the composite score
    collapses into a single bucket, while turnover or liquidity still vary.

    `min_txns_m5` keeps only observations whose token traded at least N times in
    the last 5 minutes. Rows that predate the activity fields have no value at all
    for this test, so they are counted and excluded explicitly rather than being
    treated as "0 trades" (which would silently delete the whole older store).
    """
    frozen = zero_change_ratio(rows)
    if min_txns_m5 > 0:
        has_field = [r for r in rows if r.get("txns_m5") is not None]
        kept = [r for r in has_field if int(r.get("txns_m5") or 0) >= min_txns_m5]
        rows = kept
    else:
        has_field, kept = [], []

    horizons = list(horizons_seconds)
    edges = edges or DEFAULT_EDGES.get(bucket_by) or DEFAULT_EDGES["score"]
    report: dict[str, Any] = {
        "horizons": horizons,
        "bucket_by": bucket_by,
        "buckets": {},
        "coverage": {},
        "zero_change": frozen,
    }
    if min_txns_m5 > 0:
        report["activity_filter"] = {
            "min_txns_m5": min_txns_m5,
            "rows_with_activity_field": len(has_field),
            "rows_kept": len(kept),
        }

    valued = [(r, _field_value(r, bucket_by)) for r in rows]
    valued = [(r, v) for r, v in valued if v is not None]
    for horizon in horizons:
        with_data = [(r, v) for r, v in valued if horizon in (r.get("fwd") or {})]
        report["coverage"][horizon] = {
            "scored": len(valued),
            "with_forward": len(with_data),
            "fraction": round(len(with_data) / len(valued), 3) if valued else 0.0,
        }
        buckets: dict[str, Any] = {}
        for low, high in zip(edges, edges[1:]):
            label = _bucket_label(low, high)
            group = [(r, v) for r, v in with_data if low <= v < high]
            stats = _stats([r["fwd"][horizon] for r, _ in group])
            # Observing the same 3 tokens 20 times each is NOT 60 samples.
            # Measured 2026-10-01: a bucket showed n=44 built from 2-3 mints, so
            # its median was really one token's behaviour repeated - the count of
            # DISTINCT tokens is the number that matters, not the row count.
            stats["tokens"] = len({str(r.get("mint")) for r, _ in group})
            buckets[label] = stats
        report["buckets"][horizon] = buckets
        # 全场（pool）参照：2026-10-13 收窄 1.3 判定用 —— 见 format_report 的说明。
        pool = _stats([r["fwd"][horizon] for r, _ in with_data])
        pool["tokens"] = len({str(r.get("mint")) for r, _ in with_data})
        report.setdefault("pool", {})[horizon] = pool

    # the same comparison on the binary gate we already rely on
    for horizon in horizons:
        with_data = [r for r, _ in valued if horizon in (r.get("fwd") or {})]
        report.setdefault("passed_vs_rejected", {})[horizon] = {
            "passed": _stats([r["fwd"][horizon] for r in with_data if r.get("passed")]),
            "rejected": _stats([r["fwd"][horizon] for r in with_data if not r.get("passed")]),
        }
    return report


def _bucket_label(low: float, high: float) -> str:
    if high >= 1e11:
        return f"{low:,.0f}+"
    if low >= 1000:
        return f"{low / 1000:,.0f}k-{high / 1000:,.0f}k"
    return f"{low:g}-{high:g}"


def format_report(report: dict[str, Any], store_rows: int) -> str:
    lines = [f"快照总数 {store_rows} | 分桶维度 {report['bucket_by']}"]

    frozen = report.get("zero_change") or {}
    if frozen.get("pairs"):
        lines.append(
            f"零变动占比 {100 * frozen['unchanged_fraction']:.1f}%"
            f"（{frozen['unchanged_pairs']}/{frozen['pairs']} 组相邻观测价格一字未动）| "
            f"全程不动的代币 {frozen['tokens_frozen']}/{frozen['tokens_with_pairs']}"
            f" ({100 * frozen['tokens_frozen_fraction']:.0f}%)"
        )
        if frozen["unchanged_fraction"] >= 0.5:
            lines.append(
                "  ⚠️ 零变动占比 ≥50%：样本主体是**没人交易的代币**，"
                "中位数必然是 0.00%，下面的分桶不能当结论。"
            )

    activity = report.get("activity_filter")
    if activity:
        lines.append(
            f"活跃度过滤 txns_m5 ≥ {activity['min_txns_m5']}："
            f"有该字段的行 {activity['rows_with_activity_field']} 条，保留 {activity['rows_kept']} 条"
        )
        if activity["rows_kept"] == 0:
            lines.append(
                "  ⚠️ 保留 0 条：本账本的历史行是加字段之前写的。"
                "让扫描器先积累，或不要加这个过滤。"
            )

    verdicts: list[str] = []
    for horizon in report["horizons"]:
        label = f"{horizon // 60}m" if horizon < 3600 else f"{horizon // 3600}h"
        coverage = report["coverage"][horizon]
        lines.append(
            f"\n--- +{label} ---  有前瞻数据的信号 {coverage['with_forward']}/{coverage['scored']}"
            f" ({100 * coverage['fraction']:.0f}%)"
        )
        if coverage["scored"] == 0:
            lines.append("  （还没有打分快照）")
            continue
        if coverage["fraction"] < 0.30:
            lines.append("  ⚠️ 覆盖率 <30%：样本不足，下面的分桶**不能**作为结论")
        lines.append(
            f"  {report['bucket_by']:<10} {'n':>5} {'代币':>4} {'中位%':>9} {'均值%':>9} "
            f"{'胜率%':>7} {'p10%':>9} {'p90%':>9}"
        )
        for label_key, stats in report["buckets"][horizon].items():
            if not stats.get("n"):
                lines.append(
                    f"  {label_key:<10} {0:>5} {0:>4} {'-':>9} {'-':>9} {'-':>7} {'-':>9} {'-':>9}"
                )
                continue
            lines.append(
                f"  {label_key:<10} {stats['n']:>5} {stats.get('tokens', 0):>4} "
                f"{stats['median']:>9.2f} {stats['mean']:>9.2f} "
                f"{stats['win_rate']:>7.1f} {stats['p10']:>9.2f} {stats['p90']:>9.2f}"
            )
        # The trap that fooled this report once: plenty of rows, almost no tokens.
        thin = [
            (label, s)
            for label, s in report["buckets"][horizon].items()
            if s.get("n", 0) >= 20 and s.get("tokens", 0) < 10
        ]
        if thin:
            worst = min(thin, key=lambda kv: kv[1]["tokens"])
            lines.append(
                f"  ⚠️ 观测集中在极少数代币：{worst[0]} 桶 n={worst[1]['n']} 但只有 "
                f"{worst[1]['tokens']} 个代币 —— 同一个代币被反复计数，**不是独立样本**，"
                "该桶的中位数等于那几个代币的行为。"
            )
        buckets = report["buckets"][horizon]
        filled = [(label, stats) for label, stats in buckets.items() if stats.get("n")]
        # 1.3 的**收窄判定**（2026-10-13，用户批准）：原来要求"低换手桶也 ≥20 个代币"，
        # 而本发现层（推广 feed + trending）结构上产不出安静池 —— 实测研究并集 162 个代币里
        # 中位换手比 <1 的只有 3 个、<0.5 的只有 2 个，再等也不会到 20。于是改成拿
        # **全场（pool）**当低侧参照：问的是"高换手侧有没有比一般代币强"，而不是
        # "安静池有没有比高换手强"。判据仍是两侧各 ≥20 个代币、且差额要跨过 1.4% 成本墙。
        pool = (report.get("pool") or {}).get(horizon) or {}
        if len(filled) >= 2:
            low_label, low = filled[0]
            high_label, high = filled[-1]
            edge = high.get("median", 0.0) - low.get("median", 0.0)
            # Both tests: enough rows AND enough distinct tokens behind them.
            enough = (
                high.get("n", 0) >= 20
                and low.get("n", 0) >= 20
                and high.get("tokens", 0) >= 20
                and low.get("tokens", 0) >= 20
            )
            verdicts.append(
                f"+{label}: {high_label} 中位 {high.get('median', 0):+.2f}% vs {low_label} "
                f"{low.get('median', 0):+.2f}% → 差 {edge:+.2f}pp"
                + (
                    ""
                    if enough
                    else f"（n={low.get('n')}/{high.get('n')}，代币={low.get('tokens')}/{high.get('tokens')}，样本仍不足）"
                )
            )
            if pool.get("n"):
                pool_edge = high.get("median", 0.0) - pool.get("median", 0.0)
                pool_enough = (high.get("n", 0) >= 20 and high.get("tokens", 0) >= 20
                               and pool.get("tokens", 0) >= 20)
                verdicts.append(
                    f"+{label}（收窄）：{high_label} 中位 {high.get('median', 0):+.2f}% vs "
                    f"**全场** 中位 {pool.get('median', 0):+.2f}% → 差 {pool_edge:+.2f}pp"
                    f"（全场 n={pool.get('n')}，代币={pool.get('tokens')}）"
                    + ("" if pool_enough
                       else f" ⚠️ 未达判据（需两侧各 ≥20 代币，现 {high.get('tokens')}/{pool.get('tokens')}）")
                )

    lines.append("\n=== 判定 ===")
    if not verdicts:
        lines.append("  样本不足以判定（每个分桶至少需要 20 个有前瞻数据的信号）")
    else:
        for line in verdicts:
            lines.append(f"  {line}")
        lines.append(
            "  只有当高分桶显著优于低分桶、且**扣掉链上往返成本（约 1.4% @ $50k 池）后仍为正**，"
            "这个打分才配叫信号。"
        )
    return "\n".join(lines)
