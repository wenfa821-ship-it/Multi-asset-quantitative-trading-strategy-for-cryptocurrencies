#!/usr/bin/env python3
"""The NUMBERS half of a daily review, for any date, out of the ledger.

Why this exists
---------------
The daily reviews in this README (56, 61, 65, 68) were written by hand, and the
first version of this script was a one-off with the date and the project path
hardcoded into it (2026-10-06) - so every morning the arithmetic was redone by
hand, which is exactly where a review becomes wrong without anyone noticing.

This does the part that must never be wrong: equity start/end/peak/trough, fills
and fees, funding by symbol, gate refusals by reason, risk events by title, and
the closing position set. The prose stays a judgement call - it prints a Markdown
block to paste, it does not rewrite the README.

    python3 scripts/daily_review.py                      # yesterday, CST
    python3 scripts/daily_review.py --date 2026-10-10
    python3 scripts/daily_review.py --date 2026-10-10 --write   # docs/reviews/
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
CST = datetime.timezone(datetime.timedelta(hours=8))


def day_window(date: str) -> tuple[int, int]:
    """[start, end) in epoch milliseconds for a CST calendar day."""
    start = datetime.datetime.fromisoformat(f"{date} 00:00:00").replace(tzinfo=CST)
    return (int(start.timestamp() * 1000),
            int((start + datetime.timedelta(days=1)).timestamp() * 1000))


def load_jsonl(path: Path) -> list[dict]:
    rows = []
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


def backfilled_funding(rows: list[dict], start_ms: int, end_ms: int) -> dict:
    """今日**写入**、但账单时刻在今日之外的结算 = 补记（2026-10-12）。

    为什么要有这一栏：10-11 那天 09:10 一分钟内入账 6 条 CRV 账单（$233.24），
    它们是之前几次结算的欠账（`funding-sync` 改成每小时之前的批量同步），
    却整笔算进了当日 +0.700%。`bill_ts` 是账单自己的时刻，`ts` 是写入时刻 ——
    只有前者能回答「这笔钱是哪天挣的」。旧行没有 `bill_ts`，报 unknown，不猜。
    """
    total = 0.0
    count = 0
    unknown = 0
    for row in rows:
        if row.get("kind") != "funding":
            continue
        if not (start_ms <= int(row.get("ts") or 0) < end_ms):
            continue
        bill_ts = row.get("bill_ts")
        if not bill_ts:
            unknown += 1
            continue
        if not (start_ms <= int(bill_ts) < end_ms):
            total += float(row.get("amount") or 0.0)
            count += 1
    return {"total": total, "count": count, "unknown_bill_ts": unknown}


def _funding_series(directory: Path) -> dict:
    """费率序列（`ta.funding`）；从 scripts/ 直接跑时 src 不在 sys.path 上。"""
    try:
        from ta.funding import income_from_rates, load_funding_dir
    except ImportError:
        sys.path.insert(0, str(PROJECT / "src"))
        from ta.funding import income_from_rates, load_funding_dir  # noqa: PLC0415
    return load_funding_dir(directory), income_from_rates


def live_rate_funding(
    rows: list[dict], start_ms: int, end_ms: int, funding_dir: Path | None = None
) -> dict:
    """**实盘费率口径**的当日资金费（结论口径，2026-10-12 起）。

    场馆账单口径（`funding_total`）实测是模拟盘自己的费率：CRV 一天 $555 vs 实盘
    费率只值 $0.85（248 倍），结算频率也不一样，甚至实盘为负的日子照样付钱。
    所以复盘必须同时给出「按公开费率、按我们真实持仓名义」应该是多少。

    只读本地费率文件（`scripts/refresh_daily_bars.py` 每天刷新）；缺文件就报
    `available=False` 与原因，**不猜**。持仓名义取当日最后一条 equity 的
    `|qty × mark|`；空头收正费率、多头付 —— 方向按 `positions` 的符号定。
    """
    directory = Path(funding_dir) if funding_dir else PROJECT / "data" / "history" / "funding"
    series_by_symbol, income_from_rates = _funding_series(directory)
    if not series_by_symbol:
        return {"available": False, "total": None, "by_symbol": {},
                "reason": f"没有费率文件（{directory}）"}

    day = [r for r in rows if start_ms <= int(r.get("ts") or 0) < end_ms]
    equity_rows = [r for r in day if r.get("kind") == "equity"]
    if not equity_rows:
        return {"available": False, "total": None, "by_symbol": {},
                "reason": "当日没有 equity 记录，拿不到持仓名义"}

    last = equity_rows[-1]
    # 口径计算只有一份实现（`ta.funding.income_from_rates`）—— 复盘与看板共用，
    # 免得两处各写一遍然后在某天悄悄漂开。
    return {"available": True,
            **income_from_rates(last.get("positions") or {}, last.get("marks") or {},
                                series_by_symbol, start_ms, end_ms)}


def summarise(rows: list[dict], start_ms: int, end_ms: int) -> dict:
    """Everything the review quotes, in one place so it cannot drift from itself."""
    day = [r for r in rows if start_ms <= int(r.get("ts") or 0) < end_ms]
    kinds = collections.Counter(r.get("kind") for r in day)
    equity_rows = [r for r in day if r.get("kind") == "equity"]
    equities = [float(r.get("equity") or 0.0) for r in equity_rows]
    fills = [r for r in day if r.get("kind") == "fill"]
    funding = [r for r in day if r.get("kind") == "funding"]
    funding_by_symbol: dict[str, float] = {}
    for row in funding:
        key = str(row.get("symbol") or "?")
        funding_by_symbol[key] = funding_by_symbol.get(key, 0.0) + float(row.get("amount") or 0.0)
    funding_total = sum(funding_by_symbol.values())
    top_symbol, top_amount = ("-", 0.0)
    if funding_by_symbol:
        top_symbol, top_amount = max(funding_by_symbol.items(), key=lambda kv: kv[1])
    blocked = collections.Counter(str(r.get("reason"))[:60] for r in day if r.get("kind") == "blocked")
    risk = collections.Counter(str(r.get("action"))[:60] for r in day if r.get("kind") == "risk")
    positions = dict(equity_rows[-1].get("positions") or {}) if equity_rows else {}
    spot = {s: q for s, q in positions.items() if ":" not in s and abs(q) > 1e-9}
    perp = {s: q for s, q in positions.items() if ":" in s and abs(q) > 1e-9}
    return {
        "kinds": dict(kinds),
        "equity_start": equities[0] if equities else None,
        "equity_end": equities[-1] if equities else None,
        "equity_peak": max(equities) if equities else None,
        "equity_trough": min(equities) if equities else None,
        "equity_rows": len(equities),
        "fills": fills,
        "fill_fees": sum(float(r.get("fee") or 0.0) for r in fills
                         if str(r.get("fee_ccy") or "USDT").upper() == "USDT"),
        "funding_total": funding_total,
        "funding_rows": len(funding),
        "funding_by_symbol": funding_by_symbol,
        "funding_top": (top_symbol, top_amount),
        # 2026-10-12：账单口径是**事实**，但要和两个修正量一起看 ——
        # 补记（哪天才算挣到）与实盘费率口径（这笔钱实盘值多少）。
        "funding_backfilled": backfilled_funding(rows, start_ms, end_ms),
        "funding_live_rate": live_rate_funding(rows, start_ms, end_ms),
        "blocked": blocked.most_common(5),
        "blocked_total": sum(blocked.values()),
        "risk": risk.most_common(6),
        "positions": positions,
        "legs": (len(spot), len(perp)),
    }


def markdown(date: str, stats: dict) -> str:
    out: list[str] = []
    add = out.append
    start, end = stats["equity_start"], stats["equity_end"]
    delta = (end - start) if (start is not None and end is not None) else None
    pct = (delta / start * 100) if (delta is not None and start) else None
    add(f"### {date} 数字（`scripts/daily_review.py` 生成）\n")
    add("| | 值 |")
    add("|---|---|")
    if delta is not None:
        add(f"| 权益 | {start:,.2f} → **{end:,.2f} = {delta:+,.2f}（{pct:+.3f}%）** |")
        add(f"| 峰 / 谷 | {stats['equity_peak']:,.2f} / {stats['equity_trough']:,.2f} |")
    add(f"| 成交 | **{len(stats['fills'])} 笔**，手续费 **${stats['fill_fees']:,.4f}** |")
    add(f"| 资金费 | **+${stats['funding_total']:,.4f} / {stats['funding_rows']} 次结算**"
        f"（最大 {stats['funding_top'][0]} +${stats['funding_top'][1]:,.2f}） |")
    back = stats.get("funding_backfilled") or {}
    if back.get("count"):
        unknown = (f"；另有 {back['unknown_bill_ts']} 条没有账单时刻（旧行，按未知处理）"
                   if back.get("unknown_bill_ts") else "")
        add(f"| ↳ 其中**补记** | **+${back['total']:,.4f} / {back['count']} 次**"
            f"（账单时刻不在当日，是之前几次结算的欠账）{unknown} |")
    elif back.get("unknown_bill_ts"):
        add(f"| ↳ 补记 | **无法判定**：{back['unknown_bill_ts']} 条结算没有账单时刻"
            f"（`bill_ts` 是 2026-10-12 才加进账本的，旧行按未知处理） |")
    live = stats.get("funding_live_rate") or {}
    if live.get("available"):
        missing = live.get("missing_series") or []
        note = f"；缺费率文件：{'、'.join(missing)}" if missing else ""
        if live.get("partial") and live.get("series_through"):
            through = datetime.datetime.fromtimestamp(
                int(live["series_through"]) / 1000, CST).strftime("%m-%d %H:%M")
            note += f"；**费率序列只到 {through}**，窗口未完全覆盖（数字是下界）"
        add(f"| ↳ **实盘费率口径** | **{live['total']:+,.4f}**"
            f"（按公开费率 × 当日持仓名义算；**这一栏才是可以引用的结论**，"
            f"上面那行是模拟盘到账）{note} |")
        # 2026-10-13（10-12 复盘建议 ②）：把「可复制部分」单独给一行 —— 否则
        # 「账面 +0.129%」会被单独引用，而其中大头是模拟盘费率买不到的钱。
        if delta is not None:
            other = delta - stats["funding_total"] + stats["fill_fees"]
            add(f"| ↳ **可复制部分（下界）** | **{live['total'] + other:+,.4f}**"
                f"（= 实盘费率口径资金费 {live['total']:+,.4f} + 其余 {other:+,.4f}）"
                f"；其余 = 权益变化 − 账单资金费 + 手续费，含**价差与标记价变动**，"
                f"无法逐笔核对；**账面百分比不要单独引用** |")
    elif live.get("reason"):
        add(f"| ↳ 实盘费率口径 | 拿不到：{live['reason']} |")
    add(f"| 期末持仓 | {stats['legs'][0]} 条现货腿 + {stats['legs'][1]} 条永续腿 |")
    add(f"| 拦下 | {stats['blocked_total']} 次"
        + (f"，主因：{'；'.join(f'{k}×{v}' for k, v in stats['blocked'])}" if stats["blocked"] else "") + " |")
    add(f"| 风险事件 | {sum(v for _, v in stats['risk'])} 条"
        + (f"：{'；'.join(f'{k}×{v}' for k, v in stats['risk'])}" if stats["risk"] else "") + " |")
    if stats["funding_by_symbol"]:
        add("\n资金费分布：" + "、".join(
            f"{s} +${a:,.2f}" for s, a in sorted(stats["funding_by_symbol"].items(),
                                                 key=lambda kv: -kv[1])))
    if stats["fills"]:
        add("\n成交明细：")
        for row in stats["fills"]:
            when = datetime.datetime.fromtimestamp(int(row["ts"]) / 1000, CST).strftime("%H:%M")
            add(f"- {when} {row.get('symbol')} {row.get('side')} {row.get('qty')} "
                f"@{row.get('price')} fee={row.get('fee')} {row.get('fee_ccy')} tag={row.get('tag')}")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="",
                    help="CST calendar day (default: yesterday)")
    ap.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    ap.add_argument("--write", action="store_true", help="also save docs/reviews/<date>.md")
    args = ap.parse_args()

    if args.date:
        date = args.date
    else:
        date = (datetime.datetime.now(CST) - datetime.timedelta(days=1)).date().isoformat()
    start_ms, end_ms = day_window(date)
    rows = load_jsonl(PROJECT / args.ledger)
    if not rows:
        print(f"no ledger at {args.ledger}")
        return 1
    stats = summarise(rows, start_ms, end_ms)
    body = markdown(date, stats)
    print(body)
    if args.write:
        path = PROJECT / "docs" / "reviews" / f"{date}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
