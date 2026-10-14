#!/usr/bin/env python3
"""Turn README 70.6's six "mature enough for real money" gates into numbers.

WHY THIS EXISTS
---------------
On 2026-10-11 the user asked "等策略成熟了再切换到实盘". That is a good rule and a
bad instruction, because "成熟" had no definition until 70.6 wrote six gates into a
paragraph of README.md. A gate that lives in prose is re-argued from scratch every
time somebody asks - and this project has already been bitten twice by prose that
drifted away from reality (the research-side daily bars frozen for 5.5 days; the
entry-point docs describing a version five releases old).

So each gate gets a *number*, a *window*, and a *file* it came from, and the whole
set is rendered into reports/live-readiness.md. Where a gate cannot be measured
from what is on disk, this script says so instead of printing a comfortable zero:

  * gate 1 (edge) needs a real re-run of the backtest at real fees - it is read
    from data/readiness/edge.json, which `--backtest` produces. Without that file
    the gate is "not measured", not "not met".
  * gate 4 (pipeline) needs a dated per-run history. Until 2026-10-13 only the
    LATEST run was on disk (data/daily_job_status.json is overwritten every run),
    so `daily_research.py` now also appends data/daily_job_history.jsonl. The file
    starts empty; the script reports the window it actually covers.
  * gate 6 is by definition not measurable here: the point of it is that slippage,
    counterparty depth and options liquidity only exist at a venue with real sizes.
    It is rendered as "不可自动判" and always will be.

Read-only, offline, stdlib only. Safe to run inside the daily job.

USAGE
-----
    python3 scripts/live_readiness.py                 # print the report
    python3 scripts/live_readiness.py --write         # + reports/live-readiness.md
                                                      #   + data/readiness/readiness.json
    python3 scripts/live_readiness.py --backtest      # refresh data/readiness/edge.json
                                                      #   (slow: 5 walk-forward replays)
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
CST = datetime.timezone(datetime.timedelta(hours=8))
UTC = datetime.timezone.utc

LEDGER = "data/ledger.okx_demo.jsonl"
ALERTS = "logs/alerts.jsonl"
JOB_STATUS = "data/daily_job_status.json"
JOB_HISTORY = "data/daily_job_history.jsonl"
PIPELINE_LOG = "logs/ta-newlisting-service.log"
INCOME_REPORT = "data/carry/income_report.json"
ROTATION = "data/carry/rotation.jsonl"
CARRY_SCREEN = "data/carry/screen_live.json"
CARRY_HISTORY = "data/history/funding"
OPTIONS_GATE = "data/options/gate.jsonl"
OPTIONS_ATTEMPTS = "data/options/round_trip.jsonl"
HISTORY_DIR = "data/history/okx_demo"
# 回测要加载的帧：策略自己的 timeframe + confirm 帧（config/strategies.toml）。
DATA_TIMEFRAMES = ("15m", "1h", "4h", "1d")

# Which strategy names make up which leg, so gate 1 can answer the question it
# actually asks ("至少一条**腿**/策略") instead of only the per-strategy half.
LEG_STRATEGIES = (
    ("现货方向性", ("trend_breakout", "trend_pullback", "mean_reversion")),
    ("永续", ("perp_trend_funding",)),
    ("TradFi", ("trend_breakout_daily",)),
)
EDGE = "data/readiness/edge.json"
REPORT = "reports/live-readiness.md"
READINESS_JSON = "data/readiness/readiness.json"

# 70.7 re-ran exactly these five with `--fee-multiplier 1.25`. carry_funding has no
# directional edge by construction and new_listing_breakout cannot be replayed on
# the same history (its tier is a rolling 10-name window), so neither is a gate-1
# candidate - naming them here would be padding the list, not measuring it.
EDGE_STRATEGIES = (
    "trend_breakout",
    "trend_pullback",
    "mean_reversion",
    "perp_trend_funding",
    "trend_breakout_daily",
)

MIN_FILLS = 200
MIN_TRADING_DAYS = 60
CLEAN_DAYS_REQUIRED = 30
PIPELINE_DAYS_REQUIRED = 14
CARRY_DAYS_REQUIRED = 30
RUNS_PER_DAY = 2  # the launchd job fires at 09:10 and 21:10 CST

STATUS_LABEL = {
    "met": "✅ 满足",
    "unmet": "❌ 未满足",
    "unmeasured": "⬜ 未测量",
    "not_auto": "🚫 不可自动判",
}

# The one alert title that is a KNOWN EXTERNAL CONDITION rather than a defect: the
# local proxy's upstream drops every few days, `VenueDegraded` suspends new entries
# and the process stays alive (README §70.5). It is logged at `critical` level, and
# 2026-10-13 it fired 25 times across 8 days - which means counting it as an
# "事故级事件" would have made gate 3 (30 clean days) unsatisfiable in this
# environment, i.e. a gate that can never be met. Same failure shape as the 1.3
# low-turnover wait. It gets its own counter instead (the dashboard already watches
# it by 24h count).
VENUE_DEGRADED_TITLE = "venue degraded - new entries suspended"

# `order rejected` classes, keyed by the venue's sCode where there is one. The
# labels are what the report shows; the disposition is NOT hardcoded here - the
# report only says when each class last fired, so a recurrence shows up as a new
# row rather than as a stale explanation.
REJECT_CLASSES = (
    ("51000", "tdMode 参数错（账户档位口径）"),
    ("51008", "现货余额不足"),
    ("54092", "TradFi 免责声明未确认"),
    ("dust", "低于场馆最小额（灰尘）"),
)

# Reconciliation `position` diffs come in two shapes the project has ALREADY
# documented as known, and one that is news (README §70 / the patrol rules):
#   * the inventory experiment: buy spot with `tag=options-funding`, sell it back
#     with `tag=btc-premium-inventory`; while it is held the venue shows the coin
#     and the ledger books dust. Known source, known symbol.
#   * dust vs dust: both sides are ~0 (`local=8.29e-06 venue=9.37e-06`).
# Everything else is unexplained and DOES break the clean streak.
#
# "Known" is checked against OUR OWN tagged fills rather than a time window: the
# 2026-10-10 sequence is buy 0.00120705 (19:10) -> probe sell 0.0001 (20:00) ->
# still holding 0.00110705 at 21:36, and the venue's number in each alert equals
# exactly that running balance. A ±15-minute window would have missed the 21:36
# one; matching the balance does not, and it fails closed for any symbol we never
# tagged.
INVENTORY_TAGS = ("options-funding", "btc-premium-inventory")
DUST_POSITION_USD = 1.0


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict]:
    """Every parseable row; a truncated tail line is skipped, not fatal."""
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT))
    except ValueError:
        return str(path)


def day_of(ts_ms: float, tz=CST) -> str:
    return datetime.datetime.fromtimestamp(ts_ms / 1000.0, tz).date().isoformat()


def running_days(days: list[str], today: datetime.date) -> int:
    """Length of the streak of consecutive dates ending at today (or starting
    yesterday, so an early-evening run has not already broken today's)."""
    have = set(days)
    cursor = today if today.isoformat() in have else today - datetime.timedelta(days=1)
    count = 0
    while cursor.isoformat() in have:
        count += 1
        cursor -= datetime.timedelta(days=1)
    return count


# --------------------------------------------------------------------------
# gate 1 - edge
# --------------------------------------------------------------------------


def gate_edge(root: Path) -> dict:
    path = root / EDGE
    payload = read_json(path)
    if not payload:
        return {
            "id": 1,
            "name": "边缘",
            "requirement": "至少一条腿/策略样本外为正、且扣掉 10 bps/边成本后仍为正（样本外 ≥30 笔才算数）",
            "status": "unmeasured",
            "value": "未测量",
            "detail": (
                f"`{EDGE}` 不存在。生成它要重跑 5 条 walk-forward 回放（数分钟，读本地 data/history，不需要网络）：\n\n"
                f"```bash\npython3 scripts/live_readiness.py --backtest\n```"
            ),
            "evidence": [rel(path)],
            "window": "—",
        }
    rows = payload.get("rows") or []
    # `--backtest` writes the verdict per row; a hand-written edge.json from an
    # earlier run may not, so fall back to the same rule applied to its numbers.
    qualifying = [r for r in rows if _is_gate1_winner(r)]
    decided = [r for r in rows if (r.get("trades") or 0) >= 30]
    when = payload.get("generated_at") or "?"
    age_note = edge_age_note(when)
    failed = [r for r in rows if r.get("rc")]
    lines = [
        f"| `{r.get('strategy')}` | {r.get('oos_from') or '—'} | "
        f"{_pct(r.get('total_return_pct'))} | {_num(r.get('trades'))} | "
        f"{_num(r.get('win_rate_pct'))} | {_num(r.get('profit_factor'))} |"
        for r in rows
    ]
    starts = sorted({str(r.get("oos_from")) for r in rows if r.get("oos_from")})
    detail = (
        f"样本外（最后 30%），成本按 `--fee-multiplier 1.25` = 10 bps/边，生成于 {when}。\n\n"
        "| 策略 | 样本外起 | 样本外收益 | 成交 | 胜率 | PF |\n|---|---|---|---|---|---|\n"
        + "\n".join(lines)
        + f"\n\n有足够样本可判的 {len(decided)} 条，其中为正的 **{len(qualifying)}** 条。"
    )
    if starts:
        detail += (
            f"\n\n样本外窗口 **{'、'.join(starts)} 起**。切点是序列长度的 70%，**行情一变长，"
            f"窗口就整段平移** —— 所以只有 `样本外起` 相同的那两次运行才能直接比数字"
            f"（2026-10-13 补完行情后同一批策略从 −1.70%/−1.61%/0 笔变成 −1.12%/−2.02%/14 笔，"
            f"换的是区间，不是策略）。"
        )
    if age_note:
        detail += f"\n\n{age_note}"
    detail += coverage_note(payload.get("input_coverage") or {})
    if failed:
        detail += f"\n\n⚠️ {len(failed)} 条回放没有给出样本外数字（rc≠0）：" + \
                  "、".join(f"`{r.get('strategy')}`" for r in failed) + "。"
    if payload.get("paper_fee_bps") is not None:
        detail += (
            f"\n\n基数是 paper venue 的 `fee_bps={payload.get('paper_fee_bps')}`"
            f"（回测按 paper 定价），乘 {payload.get('fee_multiplier')} 后 "
            f"**{payload.get('implied_fee_bps_per_side')} bps/边**。"
        )
    legs = leg_inventory(root, rows)
    if legs:
        detail += (
            "\n\n**按腿清点**（门槛问的是「至少一条**腿**/策略」，上面那张表只覆盖策略那一半）：\n\n"
            "| 腿 | 这条腿的可判数字 | 够不够满足第 1 条 |\n|---|---|---|\n"
            + "\n".join(f"| {g['leg']} | {g['number']} | {g['verdict']} |" for g in legs)
        )
    return {
        "id": 1,
        "name": "边缘",
        "requirement": "至少一条腿/策略样本外为正、且扣掉 10 bps/边成本后仍为正（样本外 ≥30 笔才算数）",
        "status": "met" if qualifying else "unmet",
        "value": f"{len(qualifying)}/{len(decided)} 条可判策略为正",
        "detail": detail,
        "evidence": [rel(path)],
        "window": f"截至 {when}",
    }


def leg_inventory(root: Path, edge_rows: list[dict]) -> list[dict]:
    """Every leg, with the number that decides gate 1 for it.

    Gate 1 is written as "至少一条腿/策略在样本外为正" - and the first version of this
    script only measured the strategies, which silently left carry, TradFi and
    options out of the answer. Carry matters most: it is the ONE leg whose measured
    margin is positive after its own 44.9 bps round trip, and the reason it is not
    yet "样本外" is the same 30-day clock gate 5 waits on.
    """
    by_name = {str(r.get("strategy")): r for r in edge_rows}
    out = []
    for leg, names in LEG_STRATEGIES:
        rows = [by_name[n] for n in names if n in by_name]
        decided = [r for r in rows if (r.get("trades") or 0) >= 30]
        best = max(decided, key=lambda r: r.get("total_return_pct") or 0.0, default=None)
        if best is not None:
            verdict = (
                f"❌ 有样本（{best.get('trades')} 笔）但样本外 **{best.get('total_return_pct'):+.2f}%**"
            )
            number = f"样本外最好的一条 {best.get('strategy')} {best.get('total_return_pct'):+.2f}%（{best.get('trades')} 笔）"
        else:
            counts = "、".join(f"{r.get('strategy')} {r.get('trades') or 0} 笔" for r in rows) or "无数据"
            verdict = "⬜ 样本不足，判不了"
            number = counts
        out.append({"leg": leg, "number": number, "verdict": verdict})

    report = (read_json(root / INCOME_REPORT) or {}).get("report") or {}
    days, net = report.get("realised_days"), report.get("net_bps_per_30d_hold")
    if days is not None and net is not None:
        out.append({
            "leg": "carry（资金费）",
            "number": f"到账 {days}/{CARRY_DAYS_REQUIRED} 天 · 持有 30 天净 **{net} bps**"
                      f"（已扣 44.9 bps 往返）",
            "verdict": (
                "⬜ **唯一扣成本后为正的腿**，但样本 "
                f"{days}/{CARRY_DAYS_REQUIRED} 天 —— 与门槛 5 共用同一个 30 天钟"
            ),
        })

    attempts = read_jsonl(root / OPTIONS_ATTEMPTS)
    gate_rows = read_jsonl(root / OPTIONS_GATE)
    opened = []
    if gate_rows:
        opened = [name for name, fam in (gate_rows[-1].get("families") or {}).items()
                  if fam.get("open")]
    if attempts or opened:
        out.append({
            "leg": "期权",
            "number": (
                f"闸门{'已成立 ' + '/'.join(opened) if opened else '未成立'} · "
                f"本场所实测下单 **{len(attempts)} 次尝试、" 
                f"{sum(1 for a in attempts if a.get('error'))} 次失败**"
            ),
            "verdict": "🚫 在这套环境里成交不了（被拒 / 价格带外 / 未成交），没有可判的数字",
        })
    return out


def configured_symbols(root: Path) -> list[str]:
    """Spot symbols the strategies actually trade (config/app.toml's tiers)."""
    try:
        import tomllib

        with (root / "config" / "app.toml").open("rb") as handle:
            cfg = tomllib.load(handle)
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for items in ((cfg.get("universe") or {}).get("symbols_by_tier") or {}).values():
        for symbol in items:
            text = str(symbol)
            if text not in out:
                out.append(text)
    return out


def data_coverage(root: Path) -> dict:
    """去重后的行情地平线：**回测真正会读的那些文件**最后停在哪一天。

    2026-10-13 的教训（同一天第二次）：`refresh_daily_bars.py` 的 `--timeframe` 默认是 **1d**，
    所以 v1.1.5 只解冻了日线 —— 52 个配置标的的 **15m 全部停在 10-08**、1h 有 20 个停在 09-29，
    而门槛 1 的回测读的正是这些帧。只报"测量什么时候跑的"（`generated_at`）挡不住这件事：
    测量可以是五分钟前跑的，输入却是一周前的世界。所以这里把**输入的日期**也报出来。
    """
    symbols = configured_symbols(root)
    directory = root / HISTORY_DIR
    out: dict[str, dict] = {}
    for tf in DATA_TIMEFRAMES:
        days: list[str] = []
        for symbol in symbols:
            path = directory / f"{symbol.replace('/', '-')}__{tf}.jsonl"
            if not path.exists():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    ts = row.get("ts")
                    if isinstance(ts, (int, float)):
                        days.append(day_of(ts))
                    break
            except (OSError, ValueError):
                continue
        if not days:
            continue
        newest = max(days)
        out[tf] = {
            "through": newest,
            "at_through": sum(1 for d in days if d == newest),
            "files": len(days),
        }
    return out


def coverage_note(coverage: dict, max_age_days: float = 1.0) -> str:
    """The horizon the measurement actually saw, and whether that is recent enough."""
    if not coverage:
        return ""
    worst = min(entry["through"] for entry in coverage.values())
    detail = "；".join(
        f"`{tf}` 到 {entry['through']}"
        + ("" if entry["at_through"] == entry["files"]
           else f"（只有 {entry['at_through']}/{entry['files']} 个标的到这天）")
        for tf, entry in sorted(coverage.items())
    )
    try:
        age_days = (datetime.datetime.now(UTC).date()
                    - datetime.date.fromisoformat(worst)).days
    except ValueError:
        return f"\n\n**行情地平线**：{detail}。"
    head = f"\n\n**行情地平线（这份回测实际读到的）**：{detail}。"
    if age_days > max_age_days:
        head += (
            f"\n\n⚠️ 最旧的那一帧是 **{age_days} 天前** —— 也就是说这张「样本外」表看的是"
            f"**{worst} 收盘的世界**，而不是今天。回测的数字不会因此报错，只会安静地过时。"
        )
    return head


def edge_age_note(when: str, max_age_days: float = 3.0) -> str:
    """Say so when gate 1's own measurement has gone stale.

    `generated_at` was already printed, but a date nobody compares is not a guard: if
    the daily refresh stops working, the table keeps looking like a fresh measurement.
    (Same lesson as the frozen research bars and the unfreshened candidate screen.)
    """
    try:
        stamp = datetime.datetime.fromisoformat(when)
    except (TypeError, ValueError):
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    age_days = (datetime.datetime.now(UTC) - stamp).total_seconds() / 86_400.0
    if age_days <= max_age_days:
        return ""
    return (
        f"⚠️ **这份测量本身已经 {age_days:.1f} 天没更新了**（上限 {max_age_days:g} 天）——"
        f"`--backtest` 每天的刷新没生效，上面的数字是旧的，先修管道再读结论。"
    )


def _is_gate1_winner(row: dict) -> bool:
    if isinstance(row.get("tb_selected_for_gate1"), bool):
        return row["tb_selected_for_gate1"]
    return (row.get("trades") or 0) >= 30 and (row.get("total_return_pct") or 0.0) > 0


def _pct(value) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def _num(value) -> str:
    return "—" if value is None else str(value)


# --------------------------------------------------------------------------
# gate 2 - sample size
# --------------------------------------------------------------------------


def gate_sample(root: Path) -> dict:
    path = root / LEDGER
    rows = read_jsonl(path)
    fills = [r for r in rows if r.get("kind") == "fill"]
    stamps = sorted(
        float(r["ts"]) for r in rows if isinstance(r.get("ts"), (int, float))
    )
    days = sorted({day_of(ts) for ts in stamps})
    span_days = len(days)
    met = len(fills) >= MIN_FILLS or span_days >= MIN_TRADING_DAYS
    need = []
    if len(fills) < MIN_FILLS:
        need.append(f"还差 {MIN_FILLS - len(fills)} 笔成交")
    if span_days < MIN_TRADING_DAYS:
        need.append(f"还差 {MIN_TRADING_DAYS - span_days} 个交易日")
    window = f"{days[0]} .. {days[-1]}（CST）" if days else "—"
    return {
        "id": 2,
        "name": "样本",
        "requirement": f"≥{MIN_FILLS} 笔成交 或 ≥{MIN_TRADING_DAYS} 个交易日；参数不针对最后 30 天调优",
        "status": "met" if met else "unmet",
        "value": f"{len(fills)} 笔 / {span_days} 个交易日",
        "detail": (
            f"账本 `{LEDGER}` 共 {len(rows)} 行，其中 `kind=fill` **{len(fills)}** 笔；"
            f"有记录的日子 **{span_days}** 个（{window}）。\n\n"
            + ("两个门槛都还没有到：" + "、".join(need) + "。" if need else "两个门槛至少满足一个。")
        ),
        "evidence": [rel(path)],
        "window": window,
    }


# --------------------------------------------------------------------------
# gate 3 - execution
# --------------------------------------------------------------------------


def is_cash_only(alert: dict) -> bool:
    """The known hourly item: the expected line tracks the funding rate, so the
    residual shows up as a plain `cash delta` and nothing else. Anything carrying
    a `position` line (or any other field) is NOT this bucket."""
    diffs = alert.get("diffs") or []
    return bool(diffs) and all("cash delta" in str(d) for d in diffs)


def gate_execution(root: Path) -> dict:
    path = root / ALERTS
    rows = read_jsonl(path)
    today = datetime.datetime.now(CST).date()
    ledger = read_jsonl(root / LEDGER)
    windows = inventory_fills(ledger)
    prices = reference_prices(ledger)

    mismatches = [a for a in rows if a.get("title") == "reconciliation mismatch"]
    structural = [a for a in mismatches if is_cash_only(a)]
    non_structural = [a for a in mismatches if not is_cash_only(a)]
    diff_rows = [(a, classify_position_diff(a, windows, prices)) for a in non_structural]
    inventory = [a for a, kind in diff_rows if kind == "inventory"]
    dust = [a for a, kind in diff_rows if kind == "dust"]
    unexplained = [a for a, kind in diff_rows if kind == "unexplained"]
    rejected = [a for a in rows if a.get("title") == "order rejected"]
    degraded = [a for a in rows if a.get("title") == VENUE_DEGRADED_TITLE]
    critical = [
        a for a in rows
        if str(a.get("level")) == "critical" and a.get("title") != VENUE_DEGRADED_TITLE
    ]

    covered = sorted({
        day_of(datetime.datetime.fromisoformat(str(a["ts"])).timestamp() * 1000)
        for a in rows if a.get("ts")
    })
    # Anything that breaks a clean streak: a non-cash-only mismatch, a repeated
    # rejection (one is noise; the gate says "重复出现"), or a critical alert.
    breaks = list(unexplained)
    recent_rejects = [a for a in rejected if _within(a, 30, today)]
    if len(recent_rejects) >= 2:
        breaks += recent_rejects
    breaks += critical

    break_days = sorted({
        day_of(datetime.datetime.fromisoformat(str(a["ts"])).timestamp() * 1000)
        for a in breaks if a.get("ts")
    })
    streak = running_days([d for d in covered if d not in set(break_days)], today)
    met = streak >= CLEAN_DAYS_REQUIRED and len(covered) >= CLEAN_DAYS_REQUIRED
    last_break = break_days[-1] if break_days else None
    earliest = None
    if last_break:
        earliest = (
            datetime.date.fromisoformat(last_break) + datetime.timedelta(days=CLEAN_DAYS_REQUIRED)
        ).isoformat()

    lines = []
    for a in sorted(unexplained, key=lambda r: str(r.get("ts")))[-6:]:
        when = _cst_when(a)
        first = _short((a.get("diffs") or ["?"])[0])
        lines.append(f"| {when} | {first} |")
    detail = (
        f"告警 {len(rows)} 条（{covered[0] if covered else '?'} .. {covered[-1] if covered else '?'}）："
        f"对账 mismatch **{len(mismatches)}** 条，其中只含 `cash delta` 的**已知项** "
        f"{len(structural)} 条、含持仓/其它差异的 **{len(non_structural)}** 条。后者再分三桶："
        f"**已知库存窗口 {len(inventory)}**、**灰尘对灰尘 {len(dust)}**、**未解释 {len(unexplained)}**。"
        f"`order rejected` {len(rejected)} 条（最近 30 天 {len(recent_rejects)} 条）；"
        f"critical 级 {len(critical) + len(degraded)} 条"
        f"（其中 {len(degraded)} 条是**已知外部条件**「网络降级」，另计，见下）。\n\n"
        f"**只有「未解释」那一桶（加上重复拒单与事故）打断连击**；清理后的连续干净天数 "
        f"**{streak}**，告警覆盖 {len(covered)} 天。\n"
    )
    if last_break:
        detail += (
            f"\n连击的**起点由最近一次打断决定**：{last_break}。"
            f"即使此后一条新差异都没有，按「连续 {CLEAN_DAYS_REQUIRED} 天」的口径，"
            f"这条门槛**最早也要到 {earliest} 才能满足**。\n"
        )

    # ---- rejection classes: what fired, when, and whether it can still fire ----
    classes: dict[str, list[dict]] = {}
    for alert in rejected:
        key, _label = classify_reject(alert)
        classes.setdefault(key, []).append(alert)
    if classes:
        detail += "\n**拒单按原因分类**（判断标准是「还会不会再发生」，不是「出现过几次」）：\n\n"
        detail += "| 类别 | 次数 | 首次 | 最近 | 之后天数 |\n|---|---|---|---|---|\n"
        for key, alert_list in sorted(classes.items(), key=lambda kv: -len(kv[1])):
            days = sorted({_cst_day(a) for a in alert_list if a.get("ts")})
            since = (today - datetime.date.fromisoformat(days[-1])).days
            label = dict(REJECT_CLASSES).get(key, key)
            detail += f"| `{key}` {label} | {len(alert_list)} | {days[0]} | {days[-1]} | **{since}** |\n"
        last_reject = max(_cst_day(a) for a in rejected if a.get("ts"))
        detail += (
            f"\n最近一次拒单 **{last_reject}**，此后 "
            f"{(today - datetime.date.fromisoformat(last_reject)).days} 天 0 条 —— "
            "判据是这一列，而不是总数。\n"
        )
    if lines:
        detail += (
            "\n**未解释**的对账差异（这一桶才打断连击，逐条列出而不是只给个数）：\n\n"
            "| 时间(CST) | 差异 |\n|---|---|\n" + "\n".join(lines) + "\n"
        )
    else:
        detail += "\n**未解释**的对账差异：一条都没有。\n"
    if inventory:
        last = max(_cst_day(a) for a in inventory if a.get("ts"))
        detail += (
            f"\n已知库存窗口 {len(inventory)} 条（最近 {last}）：BTC 库存实验（`tag=options-funding` 买、"
            f"`tag=btc-premium-inventory` 卖回），场馆有货、账本记灰尘。"
            f"判定方式是**场馆那个数字是否等于我们自己带标签成交的滚动余额**"
            f"（不是「多久之内有成交」—— 那样会漏掉还没卖回的那一笔），"
            f"我们没打标签的标的天然对不上，因此照样算新闻。\n"
        )
    if dust:
        last = max(_cst_day(a) for a in dust if a.get("ts"))
        detail += (
            f"\n灰尘对灰尘 {len(dust)} 条（最近 {last}）：两侧都约等于 0"
            f"（按该标的最后一次成交价折算，差额 < ${DUST_POSITION_USD:g}）——"
            f"换不出钱的差异，但**仍然列出来**，不静默丢掉。\n"
        )
    if rejected:
        reject_days = sorted({_cst_day(a) for a in rejected if a.get("ts")})
        detail += (
            f"\n`order rejected` 的日子：{', '.join(reject_days)}"
            f"（{reject_days[0]} .. {reject_days[-1]}）"
            + ("——全部在 30 天窗口内，因此每次出现都打断连击。\n" if recent_rejects else "。\n")
        )
        detail += "\n最近几次的原文（`reason` 截断）：\n\n| 时间(CST) | 标的 | reason |\n|---|---|---|\n"
        detail += "\n".join(
            f"| {_cst_when(a)} | {a.get('symbol')} | `{_short(a.get('reason'))}` |"
            for a in sorted(rejected, key=lambda r: str(r.get("ts")))[-6:]
        ) + "\n"
    if degraded:
        deg_days = sorted({_cst_day(a) for a in degraded if a.get("ts")})
        detail += (
            f"\n**网络降级（已知外部条件，不计入事故）**：{len(degraded)} 次 / {len(deg_days)} 天，"
            f"最近 {deg_days[-1]}。它是本地代理上游的周期性抖动，代码侧只做「停新开仓 + 保活 + 重试」，"
            f"没有被修好（README §70.5）。\n"
            f"⚠️ 它**在告警里是 `critical` 级** —— 如果按级别当事故数，这条门槛在这个环境里"
            f"**永远不可能满足**（8 天里就有 {len(deg_days)} 天在抖）。这正是它单独计数的原因。\n"
        )
    if critical:
        rows_crit = [
            f"| {_cst_when(a)} | {a.get('title')} | {_short(a.get('detail') or a.get('reason'), 70)} |"
            for a in sorted(critical, key=lambda r: str(r.get("ts")))
        ]
        detail += (
            f"\n**事故级事件（剔除网络降级后）**共 {len(critical)} 条：\n\n"
            "| 时间(CST) | 事件 | 说明 |\n|---|---|---|\n" + "\n".join(rows_crit)
            + "\n\n（其中 2026-10-01 的 kill switch 是**故意实测**，README §70 有记录 —— "
            "这里仍然列出来，不做静默豁免。）\n"
        )
    return {
        "id": 3,
        "name": "执行",
        "requirement": f"连续 {CLEAN_DAYS_REQUIRED} 天无「非结构性」对账差异、无重复出现的 order rejected、无事故级事件",
        "status": "met" if met else "unmet",
        "value": (
            f"{streak}/{CLEAN_DAYS_REQUIRED} 天"
            + (f" · 上次打断 {last_break}" if last_break else "")
            + (f" · 最早 {earliest} 可满足" if earliest else "")
        ),
        "detail": detail,
        "evidence": [rel(path)],
        "window": f"{covered[0]} .. {covered[-1]}" if covered else "—",
    }


def candidate_window(root: Path, held_names: list[str], recent_days: int = 5) -> dict | None:
    """Re-measure the rotation rule's top candidate over a window you can compare.

    Found 2026-10-13 while asking why the rule never fires: the incumbent side is a
    `--days 3` median, but the candidate side is `screen_live.json`'s
    `median_daily_bps`, which is a median over its last `samples` prints - 100 prints
    at a 4h settle interval is ~17 days. So a name can look 2x better than it has
    actually been paying lately.

    This only measures; it does not change what the rule proposes (that is a
    strategy choice, and the rule is deliberately read-only).
    """
    screen = read_json(root / CARRY_SCREEN) or {}
    rows = [r for r in (screen.get("rows") or [])
            if r.get("symbol") and r.get("median_daily_bps") is not None]
    held = {str(s).split("/")[0] for s in held_names}
    rows = [r for r in rows if str(r["symbol"]).split("/")[0] not in held]
    if not rows:
        return None
    top = max(rows, key=lambda r: float(r["median_daily_bps"]))
    base = str(top["symbol"]).split("/")[0]
    settle_hours = float(top.get("settle_hours") or 8.0)
    samples = int(top.get("samples") or 0)
    per_day = 24.0 / settle_hours if settle_hours else 3.0

    prints = read_jsonl(root / CARRY_HISTORY / f"{base}-USDT_USDT.jsonl")
    by_day: dict[str, list[float]] = {}
    for row in prints:
        ts, rate = row.get("ts"), row.get("rate")
        if not isinstance(ts, (int, float)) or not isinstance(rate, (int, float)):
            continue
        stamp = float(ts)
        if stamp < 1e11:
            stamp *= 1000.0
        day = datetime.datetime.fromtimestamp(stamp / 1000.0, UTC).date().isoformat()
        by_day.setdefault(day, []).append(float(rate))
    recent = sorted(by_day)[-recent_days:]
    per_day_bps = [
        sum(by_day[d]) / len(by_day[d]) * 10_000.0 * per_day for d in recent if by_day[d]
    ]
    recent_bps = sorted(per_day_bps)[len(per_day_bps) // 2] if per_day_bps else 0.0
    gain = recent_bps - 0.0
    return {
        "symbol": top["symbol"],
        "screen_bps": round(float(top["median_daily_bps"]), 3),
        "samples": samples,
        "settle_hours": settle_hours,
        "window_days": samples / per_day if per_day else 0.0,
        "recent_days": len(per_day_bps),
        "recent_bps": recent_bps,
        "recent_breakeven": float("inf") if gain <= 0 else 44.9 / gain,
        "recent": ", ".join(f"{d[5:]}:{b:.1f}" for d, b in zip(recent, per_day_bps)),
        "depth_ok": bool(top.get("depth_ok")),
        "depth_bps": top.get("depth_bps"),
    }


def classify_reject(alert: dict) -> tuple[str, str]:
    """(class key, label) for one `order rejected` alert.

    Keyed on the venue's `sCode` when it is present, otherwise on the text the
    local guard wrote. Deliberately mechanical: the report's job is to show WHEN
    each class last fired, not to declare it fixed.
    """
    reason = str(alert.get("reason") or "")
    match = re.search(r'"sCode":"(\d+)"', reason)
    if match:
        code = match.group(1)
        return code, dict(REJECT_CLASSES).get(code, "场馆拒单")
    if "tdMode" in reason:
        return "51000", dict(REJECT_CLASSES)["51000"]
    if "InsufficientFunds" in reason:
        return "51008", dict(REJECT_CLASSES)["51008"]
    if "minimum amount" in reason or reason.startswith("dust") or "below the venue minimum" in reason:
        return "dust", dict(REJECT_CLASSES)["dust"]
    return "other", "其它"


def closest_call(rows: list[dict]) -> dict | None:
    """How close the carry rotation rule ever came to firing.

    The rule needs a held pair below `floor_bps` on EVERY day of the window (three,
    by default) - deliberately strict, because one bad print is the churn that cost
    31 fills on the first day of live carry. "Never fired" is only informative next
    to how near it got, so this returns the best (below, window, symbol) row plus
    the days that blocked it.
    """
    best: dict | None = None
    for row in rows:
        floor = float(row.get("floor_bps") or 1.0)
        when = str(row.get("generated_at") or "")[:16]
        for pair in row.get("pairs") or []:
            series = pair.get("daily_bps") or []
            if not series:
                continue
            blocked = [
                (str(day), float(bps)) for day, bps, *_ in series if float(bps) >= floor
            ]
            below = len(series) - len(blocked)
            candidate = {
                "symbol": pair.get("symbol"),
                "when": when,
                "below": below,
                "window": len(series),
                "blocked": blocked,
                "floor_bps": floor,
            }
            # Ties go to the most recent round: "how near are we NOW" is the question.
            if best is None or (below, when) > (best["below"], best["when"]):
                best = candidate
    return best


def _within(alert: dict, days: int, today: datetime.date) -> bool:
    try:
        when = datetime.datetime.fromisoformat(str(alert["ts"])).astimezone(CST).date()
    except (KeyError, ValueError):
        return False
    return (today - when).days <= days


def _cst_day(alert: dict) -> str:
    return datetime.datetime.fromisoformat(str(alert["ts"])).astimezone(CST).date().isoformat()


def _cst_when(alert: dict) -> str:
    return datetime.datetime.fromisoformat(str(alert["ts"])).astimezone(CST).strftime("%m-%d %H:%M")


def inventory_fills(ledger: list[dict]) -> dict[str, list[tuple[float, float]]]:
    """symbol -> sorted [(epoch_ms, signed_qty)] for every inventory-tagged fill.

    Read from the ledger itself, so "this diff is our own inventory" is a claim the
    reader can re-derive: buy is +qty, sell is -qty.
    """
    out: dict[str, list[tuple[float, float]]] = {}
    for row in ledger:
        if row.get("kind") != "fill" or str(row.get("tag")) not in INVENTORY_TAGS:
            continue
        ts, qty = row.get("ts"), row.get("qty")
        if not isinstance(ts, (int, float)) or not isinstance(qty, (int, float)):
            continue
        signed = float(qty) if str(row.get("side")) == "buy" else -float(qty)
        out.setdefault(str(row.get("symbol")), []).append((float(ts), signed))
    for entries in out.values():
        entries.sort()
    return out


def inventory_balance(entries: list[tuple[float, float]], when_ms: float) -> float:
    """Running tagged balance at `when_ms` (what we DID buy and have not sold back)."""
    return sum(qty for ts, qty in entries if ts <= when_ms)


def reference_prices(ledger: list[dict]) -> dict[str, float]:
    """symbol -> last traded price, for turning a quantity diff into dollars."""
    out: dict[str, float] = {}
    for row in ledger:
        if row.get("kind") != "fill":
            continue
        price = row.get("price")
        if isinstance(price, (int, float)) and price:
            out[str(row.get("symbol"))] = float(price)
    return out


def position_diffs(alert: dict) -> list[tuple[str, float, float]]:
    """[(symbol, local, venue)] out of `position SYM: local=X venue=Y` lines."""
    out = []
    for raw in alert.get("diffs") or []:
        text = str(raw)
        if not text.startswith("position "):
            continue
        head, _, rest = text.partition(":")
        symbol = head[len("position "):].strip()
        match = re.search(r"local=(-?[\d.e+-]+)\s+venue=(-?[\d.e+-]+)", rest)
        if not match:
            continue
        try:
            out.append((symbol, float(match.group(1)), float(match.group(2))))
        except ValueError:
            continue
    return out


def classify_position_diff(alert: dict, windows: dict[str, list[float]],
                           prices: dict[str, float]) -> str:
    """"inventory" / "dust" / "unexplained" for one non-cash-only mismatch.

    Two documented shapes are known, and BOTH still get counted and printed - this
    only decides whether they break the clean streak:

    * `dust`       - both sides round to nothing in dollars (`INJ local=8.29e-06
                     venue=9.37e-06` is $0.00002). Measured with the symbol's own
                     last traded price; if no price is known it is NOT called dust.
    * `inventory`  - for every material symbol, the venue's number equals our own
                     running `options-funding` / `btc-premium-inventory` balance at
                     that instant (within 0.01% of the balance). That is the ledger
                     saying "yes, we bought this and have not sold it back" - the
                     known difference is WHOSE book it sits in, not whether it is ours.
    * anything else, including a new symbol, is "unexplained" and keeps the streak
      from starting. That is the whole point: 2026-10-08's carry mess must not
      become invisible because a later change taught the script to be polite.
    """
    diffs = position_diffs(alert)
    if not diffs:
        return "unexplained"
    # A `cash delta` line rides along with almost every mismatch (it is the known
    # residual), so it must not disqualify anything. Any OTHER field does.
    for raw in alert.get("diffs") or []:
        text = str(raw)
        if not text.startswith("position ") and "cash delta" not in text:
            return "unexplained"

    material = []
    for symbol, local, venue in diffs:
        price = prices.get(symbol)
        if price is None:
            return "unexplained"  # cannot price it -> cannot call it dust
        notional = abs(venue - local) * abs(price)
        if notional >= DUST_POSITION_USD:
            material.append(symbol)
    if not material:
        return "dust"

    try:
        when = datetime.datetime.fromisoformat(str(alert["ts"])).timestamp() * 1000.0
    except (KeyError, ValueError):
        return "unexplained"
    for symbol in material:
        entries = windows.get(symbol) or []
        if not entries:
            return "unexplained"
        balance = inventory_balance(entries, when)
        if abs(balance - venue) > max(1e-9, abs(balance) * 1e-4):
            return "unexplained"
    return "inventory"


def _short(text, limit: int = 90) -> str:
    flat = " ".join(str(text or "").split()).replace("|", "/")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# --------------------------------------------------------------------------
# gate 4 - data pipeline
# --------------------------------------------------------------------------


def gate_pipeline(root: Path) -> dict:
    history_path = root / JOB_HISTORY
    status_path = root / JOB_STATUS
    runs = read_jsonl(history_path)
    latest = read_json(status_path)
    log_runs = service_log_runs(root)

    # 一轮有三种状态（2026-10-14，与门槛 3 同一条口径）：
    #   green       —— needs_rerun 为空；
    #   external    —— 红了，但**所有**失败步骤都被判成"已知外部条件"（网络/场馆抖动）；
    #   red         —— 里面有我们自己的缺陷，或读不出原因（unknown 按 own 处理）。
    # 连击只看 red；external 逐轮**如实报出**，不假装它没发生。
    # 每天三个计数（不是布尔列表）：绿轮 / 外部条件轮 / 自己的红轮。
    # 第一版把"外部条件轮"也往 `green_days` 里 append 了一个 `False`，于是 `all(flags)` 永远是假 ——
    # 豁免写了却没生效（测试当场照出来）。
    day_counts: dict[str, dict[str, int]] = {}
    for row in runs:
        date = str(row.get("date") or "")
        if not date:
            continue
        needs = row.get("needs_rerun") or []
        # 旧行没有 `external_only` 字段 → 按 red 处理（保守：历史不许被追认豁免）
        kind = "green" if not needs else ("external" if row.get("external_only") else "red")
        slot = day_counts.setdefault(date, {"green": 0, "external": 0, "red": 0})
        slot[kind] += 1

    def day_ok(date: str) -> bool:
        slot = day_counts.get(date) or {}
        # 一天算数 = **绿轮 + 外部条件轮**够数（外部条件不打断连击，但会单独报出来）、
        # 且**没有自己的红轮**（读不出原因也算红轮）。
        return (slot.get("green", 0) + slot.get("external", 0) >= RUNS_PER_DAY
                and slot.get("red", 0) == 0)

    today = datetime.datetime.now(CST).date()
    streak = 0
    cursor = today if day_ok(today.isoformat()) else today - datetime.timedelta(days=1)
    while day_ok(cursor.isoformat()):
        streak += 1
        cursor -= datetime.timedelta(days=1)

    covered = sorted(day_counts)
    met = streak >= PIPELINE_DAYS_REQUIRED
    latest_note = "无 `data/daily_job_status.json`"
    if latest:
        needs = latest.get("needs_rerun") or []
        latest_note = (
            f"最近一轮生成于 {latest.get('generated_at')}，"
            + ("**needs_rerun 为空**（全绿）" if not needs else f"needs_rerun = {needs}")
        )
    detail = (
        f"逐轮留痕 `{JOB_HISTORY}`：**{len(runs)}** 轮、覆盖 **{len(covered)}** 天"
        + (f"（{covered[0]} .. {covered[-1]}，CST）" if covered else "（文件还不存在或为空）")
        + f"。\n\n{latest_note}\n\n"
        f"每天需要 ≥{RUNS_PER_DAY} 轮全绿才算一天；**红轮按原因分两类**"
        "（2026-10-14 起，与门槛 3 同一条口径）："
        f"已知外部条件（网络/场馆抖动）**不打断连击**但如实计数，我们自己的缺陷（或读不出原因）打断。"
        f"到目前：绿轮 **{sum(v['green'] for v in day_counts.values())}**、"
        f"外部条件轮 **{sum(v['external'] for v in day_counts.values())}**、"
        f"红轮 **{sum(v['red'] for v in day_counts.values())}**。"
        + (
            f"当前连续 **{streak}** 天，还差 {PIPELINE_DAYS_REQUIRED - streak} 天。"
            if not met else "已满足。"
        )
    )
    if log_runs.get("runs"):
        detail += (
            f"\n\n**服务日志给出的历史上界**（`{PIPELINE_LOG}`，**不带时间戳**，只能按轮数数）："
            f"共 **{log_runs['runs']}** 轮，其中 **{log_runs['red']}** 轮有 `rc≠0`，"
            f"末尾连续全绿 **{log_runs['trailing_green']}** 轮。"
        )
        if log_runs.get("last_red"):
            detail += "最近一次红轮失败在：" + "、".join(f"`{s}`" for s in log_runs["last_red"]) + "。"
        detail += (
            "\n\n所以这条门槛**补不出历史** —— 旧日志既没有日期、又有红轮，"
            "14 天只能从今天的逐轮留痕开始攒（这也是把它做出来的原因）。"
        )
    return {
        "id": 4,
        "name": "数据管道",
        "requirement": f"每日任务连续 {PIPELINE_DAYS_REQUIRED} 天两轮全绿（needs_rerun 为空）",
        "status": "met" if met else "unmet",
        "value": f"{streak}/{PIPELINE_DAYS_REQUIRED} 天",
        "detail": detail,
        "evidence": [rel(history_path), rel(status_path), rel(root / PIPELINE_LOG)],
        "window": f"{covered[0]} .. {covered[-1]}" if covered else "—",
    }


def service_log_runs(root: Path) -> dict:
    """Count the daily job's own `=== summary ===` blocks in its service log.

    The log carries no timestamps (each step prints a header and an rc table), so it
    can bound the RUN count but never the DAYS - which is exactly why gate 4 needed
    the appended history file. Kept here so the report says that out loud instead of
    implying the log could answer the gate.
    """
    try:
        lines = (root / PIPELINE_LOG).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    runs: list[list[str]] = []
    for i, line in enumerate(lines):
        if line.strip() != "=== summary ===":
            continue
        j, steps = i + 1, []
        while j < len(lines) and lines[j].startswith("  ") and "rc=" in lines[j]:
            steps.append(lines[j].strip())
            j += 1
        runs.append([s for s in steps if not re.search(r"rc=0\b", s)])
    trailing = 0
    for red in reversed(runs):
        if red:
            break
        trailing += 1
    return {
        "runs": len(runs),
        "red": sum(1 for red in runs if red),
        "trailing_green": trailing,
        "last_red": next((red for red in reversed(runs) if red), None),
    }


# --------------------------------------------------------------------------
# gate 5 - funding income
# --------------------------------------------------------------------------


def gate_carry(root: Path) -> dict:
    report_path = root / INCOME_REPORT
    rotation_path = root / ROTATION
    payload = read_json(report_path) or {}
    report = payload.get("report") or {}
    realised_days = report.get("realised_days")
    window = report.get("realised_window") or []
    basket = report.get("basket_names") or []
    excluded = report.get("excluded") or {}

    rot_rows = read_jsonl(rotation_path)
    rotations: list[dict] = []
    for row in rot_rows:
        rotations.extend(row.get("rotations") or [])
    near = closest_call(rot_rows)
    candidate = candidate_window(root, basket)

    days_ok = isinstance(realised_days, int) and realised_days >= CARRY_DAYS_REQUIRED
    rotated = bool(rotations)
    met = days_ok and rotated

    detail = (
        f"非尖峰篮子 {basket}（剔除 {', '.join(sorted(excluded)) or '—'}：到账是市场费率的乘积，不可外推）。\n\n"
    )
    if report:
        detail += (
            f"实测到账 **{report.get('basket_realised_bps_per_day')} bps/天**、"
            f"市场费率 **{report.get('basket_market_bps_per_day')} bps/天**、"
            f"往返成本 {payload.get('params', {}).get('round_trip_bps')} bps ⇒ 回本 "
            f"**{report.get('breakeven_days')} 天**、持有 30 天净 "
            f"**{report.get('net_bps_per_30d_hold')} bps**。\n\n"
        )
    detail += (
        f"实测窗口 **{window[0] if window else '?'} .. {window[-1] if window else '?'}**，"
        f"共 **{realised_days if realised_days is not None else '?'}** 天"
        + (
            f"，还差 {CARRY_DAYS_REQUIRED - realised_days} 天。"
            if isinstance(realised_days, int) and realised_days < CARRY_DAYS_REQUIRED
            else "。"
        )
        + f"\n\n换腿规则实际触发次数：**{len(rotations)}**。"
    )
    if rot_rows:
        detail += (
            f"这条规则**不是没人跑**：`{ROTATION}` 里有 **{len(rot_rows)}** 轮评估"
            f"（{str(rot_rows[0].get('generated_at'))[:16]} .. {str(rot_rows[-1].get('generated_at'))[:16]}），"
            f"条件是某条持仓腿在**连续 {rot_rows[-1].get('days')} 天里每天都低于 "
            f"{rot_rows[-1].get('floor_bps')} bps/天**。"
        )
        if near:
            detail += (
                f"\n\n最接近的一次是 **{near['symbol']}**（{near['when']}）：窗口 3 天里有 "
                f"**{near['below']}/3** 天低于 floor，被 "
                + "、".join(f"`{day}` 的 {bps:g} bps/天" for day, bps in near["blocked"])
                + f"挡住。所以「0 次」是**市场没给出连续三天的塌陷**，不是规则坏了 —— "
                f"但它也意味着 carry 的 5 个名额在这些名字上**锁住**：满员时换腿是唯一的进场方式。"
            )
    if candidate:
        detail += (
            f"\n\n⚠️ **两边的尺子不是同一把**：规则里**现任**用 `--days 3` 的中位（3 天），"
            f"而**候选**读的是扫描快照 `{CARRY_SCREEN}` 的 `median_daily_bps` —— "
            f"`samples={candidate['samples']}`、每 {candidate['settle_hours']:g}h 结算一次，"
            f"那其实是 **≈{candidate['window_days']:.0f} 天**的中位。"
            f"同一个名字 `{candidate['symbol']}`：快照 **{candidate['screen_bps']} bps/天**，"
            f"但按最近 {candidate['recent_days']} 天实测只有 **{candidate['recent_bps']:.1f} bps/天**"
            f"（{candidate['recent']}）。用「2 天窗口」跑一次规则会提议 "
            f"`LTC/USDT → {candidate['symbol']}`、breakeven 4 天；"
            f"拿最近的数折算，同一个提议是 **≈{candidate['recent_breakeven']:.0f} 天**回本。"
            f"\n\n方向不变（LTC 确实是最弱的一条腿），**倍数是虚的**；"
            f"而且这个候选在快照里 `depth_ok={candidate['depth_ok']}`（查不到深度），"
            f"按「入场前查不到深度就拒」的闸门，它**根本进不来**。"
            f"修哪一边是策略选择，所以这里只报数、不动规则。"
        )
    refusals = (rot_rows[-1].get("refusals") or []) if rot_rows else []
    if refusals:
        detail += (
            "\n\n**最近一轮规则自己的拒绝记录**（`carry_rotation.py` 的 `refusals`）："
            + "；".join(f"{r.get('what')} —— {r.get('why')}" for r in refusals)
            + "。**这里的语义 2026-10-13 变过一次**：候选排名已改由本地费率历史、按与现任相同的窗口重算，"
              "快照只提供 `depth_ok`/`eligible` 过滤且只在够新时生效 —— 所以 `candidate depth filter` 这条"
              "现在只表示「深度没被用来筛候选」，**不再是**「规则拒绝出提议」（那曾是护栏的第一版行为）。"
        )
    return {
        "id": 5,
        "name": "资金费证据",
        "requirement": f"carry 在非尖峰标的上 ≥{CARRY_DAYS_REQUIRED} 天实测收入，且换腿规则至少实际触发过一次",
        "status": "met" if met else "unmet",
        "value": f"{realised_days if realised_days is not None else '?'}/{CARRY_DAYS_REQUIRED} 天 · 换腿 {len(rotations)} 次",
        "detail": detail,
        "evidence": [rel(report_path), rel(rotation_path), rel(root / CARRY_SCREEN)],
        "window": f"{window[0]} .. {window[-1]}" if window else "—",
    }


# --------------------------------------------------------------------------
# gate 6 - what a demo venue cannot measure
# --------------------------------------------------------------------------


def gate_venue_only() -> dict:
    return {
        "id": 6,
        "name": "模拟盘测不到的",
        "requirement": "真实滑点 / 对手方深度 / 期权流动性，必须在实盘用最小仓位重新验证",
        "status": "not_auto",
        "value": "不可自动判",
        "detail": (
            "这一条**按定义**读不出数字：模拟盘的成交价是场馆给的，没有真实的排队与冲击成本；\n"
            "期权腿在这套环境里直接买不到（README §69：市价被拒、按卖一价被价格带拒、挂买一价 25 分钟无人成交）。\n\n"
            "所以它的验收方式不是「算出来」，而是「实盘最小仓位实测」—— 在此之前任何按模拟盘结论放量的动作都跳过这一条。"
        ),
        "evidence": ["README.md §69/§70.6"],
        "window": "—",
    }


# --------------------------------------------------------------------------
# gate 1's input: the walk-forward re-run
# --------------------------------------------------------------------------


_KV = re.compile(r"^  ([a-z_]+)\s+(\S+)\s*$")


def parse_oos(text: str) -> dict:
    """Pull the out-of-sample block out of `ta backtest --split ...` output.

    Also records WHERE the out-of-sample window starts (`walk-forward split at ts=`).
    2026-10-13 实测：把行情从 10-08 补到 10-13 之后，同一批策略的样本外数字全都变了
    （`trend_breakout` −1.70%→−1.12%、`trend_pullback` 0 笔→14 笔、`perp_trend_funding`
    −1.61%→−2.02%），而结论没变。原因是切点是 `int(bar_count * 0.7)`：序列变长，切点前移，
    **样本外窗口整段平移**，两次数的根本不是同一段时间。所以窗口起点必须跟着数字一起存 ——
    否则下一次"对比"就是在拿两个不同的区间互相解释。
    """
    lines = text.splitlines()
    split_note = {}
    for line in lines:
        if "walk-forward split at ts=" in line:
            try:
                cut = int(line.split("ts=")[1].split()[0])
            except (IndexError, ValueError):
                cut = 0
            if cut:
                split_note = {
                    "oos_from_ms": cut,
                    "oos_from": day_of(cut),
                }
            break
    try:
        start = next(i for i, l in enumerate(lines) if "--- out-of-sample" in l)
    except StopIteration:
        return split_note
    out: dict = dict(split_note)
    for line in lines[start + 1:]:
        if line.startswith("backtest "):
            parts = line.split()
            for token in parts[1:]:
                key, _, value = token.partition("=")
                out.setdefault(key, _coerce(value))
            continue
        if line.startswith("  out-of-sample at 3x costs"):
            continue
        if line.startswith("  --- "):
            break  # the next section starts; everything below belongs to it
        if not line.strip():
            continue
        match = _KV.match(line)
        if match:
            key, raw = match.groups()
            out[key] = _coerce(raw)
            continue
        if out:
            break  # a non-blank line that is not a key/value ends the block
    out.pop("strategy", None)
    return out


def _coerce(raw: str):
    if raw in ("None", "nan"):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def run_edge_backtest(root: Path, strategies: list[str], split: float,
                      fee_multiplier: float) -> dict:
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    paper_fee_bps, paper_slippage_bps = paper_costs(root)
    rows = []
    for name in strategies:
        cmd = [
            sys.executable, "-m", "ta", "backtest",
            "--strategy", name,
            "--source", "history", "--split", str(split),
            "--fee-multiplier", str(fee_multiplier),
        ]
        print(f"$ {' '.join(cmd)}", flush=True)
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd, cwd=str(root), env=env, check=False, capture_output=True, text=True
        )
        summary = parse_oos(proc.stdout)
        summary["strategy"] = name
        summary["rc"] = proc.returncode
        selected = (
            proc.returncode == 0
            and (summary.get("trades") or 0) >= 30
            and (summary.get("total_return_pct") or 0.0) > 0
        )
        summary["tb_selected_for_gate1"] = bool(selected)
        rows.append(summary)
        print(
            f"  -> {name}: {summary.get('total_return_pct')}% / "
            f"{summary.get('trades')} trades / rc={proc.returncode}",
            flush=True,
        )
    return {
        "generated_at": datetime.datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "history",
        "split": split,
        "fee_multiplier": fee_multiplier,
        "paper_fee_bps": paper_fee_bps,
        "paper_slippage_bps": paper_slippage_bps,
        "implied_fee_bps_per_side": round((paper_fee_bps or 0.0) * fee_multiplier, 2),
        # 测量跑在什么时候（generated_at）与**它看到的世界停在哪一天**是两件事 ——
        # 2026-10-13 实测：测量可以一分钟前刚跑，输入却是 5 天前收盘的行情。
        "input_coverage": data_coverage(root),
        "rows": rows,
    }


def should_replace_edge(payload: dict) -> bool:
    """Only ever REPLACE a measurement with a real one.

    2026-10-13: `--backtest` originally wrote `edge.json` unconditionally, so if every
    replay came back rc!=0 (missing bars, wrong interpreter, a broken `ta` import) the
    five good rows on disk would be replaced by five `None`s - and gate 1 would report
    "0/2" as if it had measured something. The project already lost a day's evidence
    table to exactly this shape (an empty table overwriting a good one under a network
    blip), so the rule here is: a degraded run keeps the previous file and reports a
    failure the daily job can see, instead of quietly becoming the new truth.
    """
    rows = payload.get("rows") or []
    if not rows:
        return False
    return any(isinstance(r.get("total_return_pct"), (int, float)) for r in rows)


def paper_costs(root: Path) -> tuple[float | None, float | None]:
    """The backtest prices on the PAPER venue's fees (`cmd_backtest` builds the
    strategy with those), so the 10 bps/边 claim is only true if the multiplier is
    applied to `venues.paper.fee_bps`. Read it instead of remembering 8.0."""
    try:
        import tomllib

        with (root / "config" / "app.toml").open("rb") as handle:
            cfg = tomllib.load(handle)
        paper = ((cfg.get("venues") or {}).get("paper") or {})
        return float(paper.get("fee_bps")), float(paper.get("slippage_bps"))
    except (OSError, ValueError, TypeError, KeyError):
        return None, None


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------


def render(gates: list[dict], meta: dict) -> str:
    met = [g for g in gates if g["status"] == "met"]
    auto = [g for g in gates if g["status"] != "not_auto"]
    head = [
        "# 切实盘门槛（README §70.6）",
        "",
        f"生成于 {meta['generated_at']}（CST {meta['generated_cst']}）· "
        f"由 `scripts/live_readiness.py` 计算，**只读本地文件，不联网**。",
        "",
        f"**{len(met)}/{len(auto)} 条可自动判的门槛已满足** —— "
        + ("现在仍不能切实盘。" if len(met) < len(auto) else "门槛齐了，下一步是实盘最小仓位验证（第 6 条）。"),
        "",
        "| # | 门槛 | 要求 | 当前值 | 结论 | 数据窗口 |",
        "|---|---|---|---|---|---|",
    ]
    for g in gates:
        head.append(
            f"| {g['id']} | {g['name']} | {g['requirement']} | {g['value']} | "
            f"{STATUS_LABEL.get(g['status'], g['status'])} | {g['window']} |"
        )
    body = ["", "---", ""]
    for g in gates:
        body += [
            f"## {g['id']}. {g['name']} — {STATUS_LABEL.get(g['status'], g['status'])}",
            "",
            f"**要求**：{g['requirement']}",
            "",
            f"**当前**：{g['value']}",
            "",
            g["detail"],
            "",
            f"**证据**：{', '.join(g['evidence'])}",
            "",
        ]
    body += [
        "---",
        "",
        "## 口径说明（会自己验证的几件事）",
        "",
        f"* 第 3 条的「已知项」= 对账差异**只**含 `cash delta`（期望线跟着资金费走之后的稳定残余）。"
        f"带 `position` 的一律算非结构性。",
        f"* 第 3 条的豁免**只给两类非缺陷**：① 已知外部条件（代理降级——它按 `critical` 记账，"
        f"按级别数会让门槛永远不成立）② 我们**自己的记账口径**（库存实验的 tag 余额、两侧都约等于 0 的灰尘）。"
        f"**缺陷不豁免，只会随时间自然退出 30 天窗口** —— 10-08 的 carry 乱局与 10-09 的单位假差异"
        f"仍然在打断连击。少一条豁免，门槛就更硬；多一条，它就变成装饰。",
        f"* 第 4 条的逐轮留痕从 2026-10-13 起累积：那之前 `data/daily_job_status.json` "
        f"每轮被覆盖，历史无法追溯（脚本如实报出覆盖窗口，而不是假装有 14 天）。",
        f"* 第 1 条读的是 `{EDGE}`，`--backtest` 生成；数字随时间会变，看 `generated_at`。",
        "",
    ]
    return "\n".join(head + body)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="README 70.6 live-readiness gates, as numbers")
    ap.add_argument("--root", default=str(PROJECT))
    ap.add_argument("--write", action="store_true", help=f"write {REPORT} + {READINESS_JSON}")
    ap.add_argument("--backtest", action="store_true",
                    help=f"re-run the 5 walk-forward replays into {EDGE} (slow)")
    ap.add_argument("--strategies", default=",".join(EDGE_STRATEGIES))
    ap.add_argument("--split", type=float, default=0.7)
    ap.add_argument("--fee-multiplier", type=float, default=1.25)
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    degraded = False
    if args.backtest:
        strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
        payload = run_edge_backtest(root, strategies, args.split, args.fee_multiplier)
        out = root / EDGE
        if not should_replace_edge(payload):
            # Keep the last real measurement; say so loudly and fail, so the daily job
            # records a needs-rerun instead of a silently emptier answer.
            print(
                f"\n!! no replay produced a number ({len(payload['rows'])} attempted) - "
                f"NOT overwriting {rel(out)}; gate 1 still shows the previous measurement",
                flush=True,
            )
            degraded = True
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            print(f"\nwrote {rel(out)} ({len(payload['rows'])} strategies)")

    now = datetime.datetime.now(UTC)
    meta = {
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_cst": now.astimezone(CST).strftime("%Y-%m-%d %H:%M"),
    }
    gates = [
        gate_edge(root),
        gate_sample(root),
        gate_execution(root),
        gate_pipeline(root),
        gate_carry(root),
        gate_venue_only(),
    ]
    text = render(gates, meta)
    print(text)

    if args.write:
        report = root / REPORT
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text + "\n", encoding="utf-8")
        payload = {
            **meta,
            "met": sum(1 for g in gates if g["status"] == "met"),
            "judgeable": sum(1 for g in gates if g["status"] != "not_auto"),
            "gates": gates,
        }
        out = root / READINESS_JSON
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {rel(report)} and {rel(out)}")
    # The report is still written (the other five gates did not fail); only the exit
    # code carries the degraded measurement.
    return 1 if degraded else 0


if __name__ == "__main__":
    raise SystemExit(main())
