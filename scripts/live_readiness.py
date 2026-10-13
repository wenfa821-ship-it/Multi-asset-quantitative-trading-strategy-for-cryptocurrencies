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
INCOME_REPORT = "data/carry/income_report.json"
ROTATION = "data/carry/rotation.jsonl"
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
            "requirement": f"至少一条策略样本外为正、且扣掉 10 bps/边成本后仍为正（样本外 ≥30 笔才算数）",
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
    failed = [r for r in rows if r.get("rc")]
    lines = [
        f"| `{r.get('strategy')}` | {_pct(r.get('total_return_pct'))} | {_num(r.get('trades'))} | "
        f"{_num(r.get('win_rate_pct'))} | {_num(r.get('profit_factor'))} |"
        for r in rows
    ]
    detail = (
        f"样本外（最后 30%），成本按 `--fee-multiplier 1.25` = 10 bps/边，生成于 {when}。\n\n"
        "| 策略 | 样本外收益 | 成交 | 胜率 | PF |\n|---|---|---|---|---|\n" + "\n".join(lines)
        + f"\n\n有足够样本可判的 {len(decided)} 条，其中为正的 **{len(qualifying)}** 条。"
    )
    if failed:
        detail += f"\n\n⚠️ {len(failed)} 条回放没有给出样本外数字（rc≠0）：" + \
                  "、".join(f"`{r.get('strategy')}`" for r in failed) + "。"
    if payload.get("paper_fee_bps") is not None:
        detail += (
            f"\n\n基数是 paper venue 的 `fee_bps={payload.get('paper_fee_bps')}`"
            f"（回测按 paper 定价），乘 {payload.get('fee_multiplier')} 后 "
            f"**{payload.get('implied_fee_bps_per_side')} bps/边**。"
        )
    return {
        "id": 1,
        "name": "边缘",
        "requirement": "至少一条策略样本外为正、且扣掉 10 bps/边成本后仍为正（样本外 ≥30 笔才算数）",
        "status": "met" if qualifying else "unmet",
        "value": f"{len(qualifying)}/{len(decided)} 条可判策略为正",
        "detail": detail,
        "evidence": [rel(path)],
        "window": f"截至 {when}",
    }


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

    mismatches = [a for a in rows if a.get("title") == "reconciliation mismatch"]
    structural = [a for a in mismatches if is_cash_only(a)]
    non_structural = [a for a in mismatches if not is_cash_only(a)]
    rejected = [a for a in rows if a.get("title") == "order rejected"]
    critical = [a for a in rows if str(a.get("level")) == "critical"]

    covered = sorted({
        day_of(datetime.datetime.fromisoformat(str(a["ts"])).timestamp() * 1000)
        for a in rows if a.get("ts")
    })
    # Anything that breaks a clean streak: a non-cash-only mismatch, a repeated
    # rejection (one is noise; the gate says "重复出现"), or a critical alert.
    breaks = list(non_structural)
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

    lines = []
    for a in sorted(non_structural, key=lambda r: str(r.get("ts")))[-6:]:
        when = datetime.datetime.fromisoformat(str(a["ts"])).astimezone(CST).strftime("%m-%d %H:%M")
        first = (a.get("diffs") or ["?"])[0]
        lines.append(f"| {when} | {first} |")
    detail = (
        f"告警 {len(rows)} 条（{covered[0] if covered else '?'} .. {covered[-1] if covered else '?'}）："
        f"对账 mismatch **{len(mismatches)}** 条，其中只含 `cash delta` 的**已知项** "
        f"{len(structural)} 条、含持仓/其它差异的 **{len(non_structural)}** 条；"
        f"`order rejected` {len(rejected)} 条（最近 30 天 {len(recent_rejects)} 条）；"
        f"critical 级 {len(critical)} 条。\n\n"
        f"清理后的连续干净天数 **{streak}**，账本覆盖 {len(covered)} 天。\n"
    )
    if lines:
        detail += "\n最近的**非结构性**对账差异：\n\n| 时间(CST) | 差异 |\n|---|---|\n" + "\n".join(lines) + "\n"
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
    if critical:
        titles = sorted({str(a.get("title")) for a in critical})
        last = max(_cst_day(a) for a in critical if a.get("ts"))
        detail += f"\ncritical 级告警共 {len(critical)} 条（{', '.join(titles)}），最近一次 {last}。\n"
    return {
        "id": 3,
        "name": "执行",
        "requirement": f"连续 {CLEAN_DAYS_REQUIRED} 天无「非结构性」对账差异、无重复出现的 order rejected、无事故级事件",
        "status": "met" if met else "unmet",
        "value": f"{streak}/{CLEAN_DAYS_REQUIRED} 天（覆盖 {len(covered)} 天）",
        "detail": detail,
        "evidence": [rel(path)],
        "window": f"{covered[0]} .. {covered[-1]}" if covered else "—",
    }


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

    green_days: dict[str, list[bool]] = {}
    for row in runs:
        date = str(row.get("date") or "")
        if not date:
            continue
        green_days.setdefault(date, []).append(not (row.get("needs_rerun") or []))

    def day_ok(date: str) -> bool:
        flags = green_days.get(date) or []
        return len(flags) >= RUNS_PER_DAY and all(flags)

    today = datetime.datetime.now(CST).date()
    streak = 0
    cursor = today if day_ok(today.isoformat()) else today - datetime.timedelta(days=1)
    while day_ok(cursor.isoformat()):
        streak += 1
        cursor -= datetime.timedelta(days=1)

    covered = sorted(green_days)
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
        "每天需要 ≥2 轮全绿才算一天。"
        + (
            f"当前连续 **{streak}** 天，还差 {PIPELINE_DAYS_REQUIRED - streak} 天。"
            if not met else "已满足。"
        )
    )
    return {
        "id": 4,
        "name": "数据管道",
        "requirement": f"每日任务连续 {PIPELINE_DAYS_REQUIRED} 天两轮全绿（needs_rerun 为空）",
        "status": "met" if met else "unmet",
        "value": f"{streak}/{PIPELINE_DAYS_REQUIRED} 天",
        "detail": detail,
        "evidence": [rel(history_path), rel(status_path)],
        "window": f"{covered[0]} .. {covered[-1]}" if covered else "—",
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

    rotations: list[dict] = []
    for row in read_jsonl(rotation_path):
        rotations.extend(row.get("rotations") or [])

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
        + f"\n\n换腿规则实际触发次数：**{len(rotations)}**（`{ROTATION}` 里每轮的 `rotations` 全为空）。"
    )
    return {
        "id": 5,
        "name": "资金费证据",
        "requirement": f"carry 在非尖峰标的上 ≥{CARRY_DAYS_REQUIRED} 天实测收入，且换腿规则至少实际触发过一次",
        "status": "met" if met else "unmet",
        "value": f"{realised_days if realised_days is not None else '?'}/{CARRY_DAYS_REQUIRED} 天 · 换腿 {len(rotations)} 次",
        "detail": detail,
        "evidence": [rel(report_path), rel(rotation_path)],
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
    """Pull the out-of-sample block out of `ta backtest --split ...` output."""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if "--- out-of-sample" in l)
    except StopIteration:
        return {}
    out: dict = {}
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
        "rows": rows,
    }


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
        f"带 `position` 的一律算非结构性，哪怕它来自已知的 BTC 库存实验窗口。",
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

    if args.backtest:
        strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
        payload = run_edge_backtest(root, strategies, args.split, args.fee_multiplier)
        out = root / EDGE
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
