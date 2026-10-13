#!/usr/bin/env python3
"""前置/实验的**执行闸门**：能不能做这一步，由 `config/preconditions.json` 说了算。

为什么要有它（2026-10-12 的 $0.27）：`docs/options-sleeve-spec.md` §6.1 原本只在**文档里**
被划掉（~~已做、已撤、已结案~~），代码侧没有任何拦截 —— 于是那一件事又被重跑了一次。
**写进文档的「已结案」不是判据，能被脚本读到的才是。**

用法：

    python3 scripts/preconditions.py                  # 列出全部（表格）
    python3 scripts/preconditions.py --json           # 给别的工具读
    python3 scripts/preconditions.py --check <id>     # 能否执行：能 → 0，不能 → 1（并说明原因）

判据（唯一实现，`is_executable`）：

* `status` 必须是 `open`（`closed` = 已结案不许重跑；`blocked` = 外部条件不具备）；
* `expires_at` 若写了，必须**还没到**（过期即拒，哪怕 status 还是 open）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT / "config" / "preconditions.json"

STATUS_MEANING = {
    "open": "可以执行",
    "closed": "已结案，不许重跑",
    "blocked": "外部条件不具备",
}


def _parse(value) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:                      # 不带时区的一律按 CST 理解（本项目账本口径）
        parsed = parsed.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    return parsed


def is_executable(item: dict, now: datetime.datetime | None = None) -> tuple[bool, str]:
    """这条前置现在能不能做？返回 (能, 原因)。原因在**不能**时是给执行者看的。"""
    now = now or datetime.datetime.now(datetime.UTC)
    status = str(item.get("status") or "").lower()
    if status != "open":
        label = STATUS_MEANING.get(status, f"未知状态 {status!r}")
        return False, (f"status={status}（{label}）"
                       + (f"；{item['why']}" if item.get("why") else "")
                       + (f"；重跑政策：{item['repeat_policy']}" if item.get("repeat_policy") else ""))
    expires = _parse(item.get("expires_at"))
    if expires and now >= expires:
        return False, f"已过期（expires_at = {item['expires_at']}，现在 {now.isoformat()}）"
    return True, "可以执行"


def load(path: Path | None = None) -> list[dict]:
    payload = json.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    items = payload.get("items") or []
    unknown = [i for i in items if str(i.get("status") or "").lower() not in STATUS_MEANING]
    if unknown:                                    # 写错状态就吵，不要默默当成"不可以"
        raise SystemExit("preconditions.json 里有未知 status："
                         + ", ".join(f"{i.get('id')}={i.get('status')!r}" for i in unknown))
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None)
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--check", default=None, metavar="ID", help="能否执行这条前置")
    ap.add_argument("--expired", action="store_true", help="只列「open 但已过期」的条目")
    args = ap.parse_args()

    items = load(Path(args.path) if args.path else None)
    now = datetime.datetime.now(datetime.UTC)
    checked = {i["id"]: is_executable(i, now) for i in items}
    if args.json:
        print(json.dumps({"generated_at": now.replace(microsecond=0).isoformat(),
                          "items": [{**i, "executable": checked[i["id"]][0],
                                     "reason": checked[i["id"]][1]} for i in items]},
                         ensure_ascii=False, indent=1))
        return 0

    if args.expired:
        stale = [i for i in items if str(i.get("status")) == "open"
                 and not checked[i["id"]][0]]
        for item in stale:
            print(f"{item['id']}: {checked[item['id']][1]}")
        print(f"open 但已过期：{len(stale)} 条")
        return 1 if stale else 0

    if args.check:
        item = next((i for i in items if i.get("id") == args.check), None)
        if item is None:
            print(f"没有这条前置：{args.check}（已知：{', '.join(i['id'] for i in items)}）",
                  file=sys.stderr)
            return 2
        ok, reason = checked[item["id"]]
        print(f"{item['id']}｜{'可以执行' if ok else '**不要执行**'}：{reason}")
        if item.get("spec"):
            print(f"设计出处：{item['spec']}")
        return 0 if ok else 1

    print(f"{'id':<28}{'状态':<12}{'有效期':<22}说明")
    for item in items:
        ok, _ = checked[item["id"]]
        expires = item.get("expires_at") or "—"
        mark = "✅" if ok else ("⛔" if item["status"] == "closed" else "🚧")
        print(f"{item['id']:<28}{mark + item['status']:<12}{str(expires)[:19]:<22}"
              f"{str(item.get('title'))[:40]}")
    print("\n执行任何前置之前：python3 scripts/preconditions.py --check <id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
