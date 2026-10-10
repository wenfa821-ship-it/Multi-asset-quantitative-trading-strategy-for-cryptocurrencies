#!/usr/bin/env python3
"""Render the path-plan progress table into README.md.

Why this is generated rather than typed
---------------------------------------
On 2026-10-09 a progress盘点 took the changelog's `status` field at face value and
got the wrong answer - dozens of entries had been fixed without anyone updating
that column. A hand-maintained status table drifts the same way. So the table
lives in ONE machine-readable file (`docs/progress.json`) and is rendered between
markers; `--check` fails when the README no longer matches, which is what the
workflow needs: **read the table at the start of a round, update it at the end.**

    python3 scripts/progress.py            # render into README.md
    python3 scripts/progress.py --check    # exit 1 if out of date (no writes)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- PROGRESS:BEGIN -->"
END = "<!-- PROGRESS:END -->"

STATE_LABEL = {
    "done": "✅ 完成",
    "in_progress": "🔧 进行中",
    "gated": "⏳ 等闸门",
    "waiting_data": "⏳ 等数据",
    "blocked": "⛔ 阻塞",
    "not_started": "⬜ 未开始",
}


def render(payload: dict) -> str:
    rows = payload.get("rows") or []
    out = [
        BEGIN,
        "",
        f"> 生成自 `docs/progress.json`（`python3 scripts/progress.py`），更新于 {payload.get('updated', '?')}。",
        "> **每轮先读这张表，收尾时更新它** —— 它是「下一步做什么」的唯一事实来源。",
        "",
        "| # | 项目 | 状态 | 闸门 / 依据 | 下一步 |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        state = STATE_LABEL.get(str(row.get("state")), str(row.get("state")))
        gate = str(row.get("gate") or "—")
        nxt = str(row.get("next") or "—")
        out.append(
            f"| `{row.get('id')}` | {row.get('item')} | **{state}** | {gate} | {nxt} |"
        )
    counts = {}
    for row in rows:
        counts[str(row.get("state"))] = counts.get(str(row.get("state")), 0) + 1
    summary = " · ".join(f"{STATE_LABEL.get(k, k)} {v}" for k, v in sorted(counts.items()))
    out += ["", f"**合计**：{summary}。", END]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress", default="docs/progress.json")
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    payload = json.loads((PROJECT / args.progress).read_text(encoding="utf-8"))
    block = render(payload)
    readme = PROJECT / args.readme
    text = readme.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        print(f"{args.readme} has no {BEGIN} / {END} markers")
        return 2
    head, _, rest = text.partition(BEGIN)
    _, _, tail = rest.partition(END)
    updated = f"{head}{block}{tail}"
    if updated == text:
        print("README progress table is up to date")
        return 0
    if args.check:
        print("README progress table is STALE - run python3 scripts/progress.py")
        return 1
    readme.write_text(updated, encoding="utf-8")
    print(f"rendered {len(payload.get('rows') or [])} row(s) into {args.readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
