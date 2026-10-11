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
