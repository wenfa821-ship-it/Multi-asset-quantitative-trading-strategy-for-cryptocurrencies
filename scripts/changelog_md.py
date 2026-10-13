#!/usr/bin/env python3
"""Render CHANGELOG.md from the frozen versions plus what has happened since.

The JSONL is the append-only trace and `archive/versions/<v>/` is the immutable
snapshot, but neither is a RELEASE NOTE: one is 200+ machine lines, the other is a
directory. This joins them - each frozen version gets its date, test count, equity
and the prose its NOTES.md already carries, and everything logged SINCE the last
freeze is listed as "Unreleased" so the next release is assembled, not remembered.

    python3 scripts/changelog_md.py --write      # refresh CHANGELOG.md
    python3 scripts/changelog_md.py --check      # exit 1 when CHANGELOG.md is stale
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
VERSIONS = PROJECT / "archive" / "versions"
CHANGELOG_JSONL = PROJECT / "docs" / "changelog.jsonl"
TARGET = PROJECT / "CHANGELOG.md"


def version_key(name: str) -> tuple:
    parts = name.lstrip("v").split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)


def parse_ts(value) -> datetime.datetime | None:
    """把 `ts` 解析成**带时区**的时刻；解析不了返回 None（调用方退回字符串比较）。

    2026-10-12：这里原来是 `str(row["ts"]) <= iso_after` —— **纯字符串比较**。
    本项目今天恰好两边都写 `+08:00` 所以看不出问题，但兄弟项目（新币）的快照写 UTC、
    留痕写本地，字符串比较直接把「冻结之后的改动」算错（54 条 vs 真实 45 条）。
    这种 bug 不会报错，只会让时间顺序悄悄错掉。
    """
    try:
        return datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def is_future(value, now: datetime.datetime) -> bool:
    parsed = parse_ts(value)
    return bool(parsed) and parsed.astimezone(datetime.UTC) > now


def load_versions() -> list[dict]:
    out = []
    if not VERSIONS.exists():
        return out
    for path in sorted(VERSIONS.iterdir(), key=lambda p: version_key(p.name)):
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        notes = ""
        notes_path = path / "NOTES.md"
        if notes_path.exists():
            # Keep the prose, drop the lines the snapshot tool writes itself
            # (`# vX` title, "Frozen ...", the trailing compare-against line) -
            # those are already reconstructable from manifest.json.
            text = notes_path.read_text(encoding="utf-8").strip().splitlines()
            # 2026-10-13：快照 NOTES 的收尾是一句两句的样板（"Strategy changes…" 后面
            # 还有一行 "its config/ and manifest.json rather than against memory."）——
            # 过滤只写了前半句，于是每个版本的说明后面都挂着一行断句残骸（CHANGELOG 里 21 处）。
            boilerplate = ("Frozen ", "Strategy changes after this point",
                           "its config/ and manifest.json rather than against memory")
            keep = [
                line for line in text[2:]
                if line.strip() and not line.startswith(boilerplate)
            ]
            notes = "\n".join(keep)
        out.append({
            "name": path.name,
            "created_at": manifest.get("created_at", ""),
            "tests": manifest.get("tests_passed"),
            "equity": (manifest.get("account") or {}).get("equity"),
            "package_version": manifest.get("package_version", ""),
            "notes": notes,
        })
    return out


def load_since(iso_after: str) -> list[dict]:
    if not CHANGELOG_JSONL.exists():
        return []
    cutoff = parse_ts(iso_after) if iso_after else None
    rows = []
    for line in CHANGELOG_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if iso_after:
            row_ts = parse_ts(row.get("ts"))
            if cutoff and row_ts:
                # 两边都解析得出来 → 按时区比较；否则退回原来的字符串比较（宁可保守）
                if row_ts <= cutoff:
                    continue
            elif str(row.get("ts", "")) <= iso_after:
                continue
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("ts")))
    return rows


def render() -> str:
    versions = load_versions()
    current = (PROJECT / "VERSION").read_text(encoding="utf-8").strip() if (PROJECT / "VERSION").exists() else "?"
    last = versions[-1]["created_at"] if versions else ""
    unreleased = load_since(last)
    # 2026-10-12：留痕的 `ts` 是**手写**的，兄弟项目实测会写进未来（70 行里 45 行，
    # 其中 42 行超前 24~39 小时）。这里点名，让 `--check` 与人都看得见。
    now = datetime.datetime.now(datetime.UTC)
    future = [r for r in unreleased if is_future(r.get("ts"), now)]
    lines = [
        "# Changelog",
        "",
        f"当前版本：**v{current}**（`VERSION` 是唯一事实来源；`pyproject.toml` 与 "
        "`ta.__version__` 由 `scripts/version_sync.py --check` 强制一致）。",
        "",
        "版本规则见 [`docs/RELEASING.md`](docs/RELEASING.md)：**1.0.0 = 第一阶段封板**"
        "（四腿系统接入并运行 + 风控/对账/数据管道/发布管理就位）；1.0 之后按语义化版本 —— "
        "MAJOR 表示架构或配置模式不兼容，MINOR 表示新增能力（**1.1 = 期权策略与期权腿**），PATCH 表示修复。"
        "每个版本冻结在 `archive/versions/<v>/`（config + changelog + progress + manifest + NOTES）。",
        "",
    ]
    if unreleased:
        tail = f"（⚠ 其中 {len(future)} 条 ts 在未来）" if future else ""
        lines += [f"## Unreleased（自 {last[:16] or '最初'} 起，{len(unreleased)} 条留痕{tail}）", ""]
        for row in unreleased:
            mark = "⚠ " if is_future(row.get("ts"), now) else ""
            lines.append(f"- {mark}`{str(row.get('ts'))[:16]}` **{row.get('kind','?')}** {row.get('title','')}")
        lines.append("")
    for v in reversed(versions):
        equity = f"${v['equity']:,.2f}" if isinstance(v["equity"], (int, float)) else "?"
        lines += [
            f"## {v['name']} — {str(v['created_at'])[:16]}",
            "",
            f"- 测试 **{v['tests']}** 绿 · 权益 {equity} · 记录时的包版本 {v['package_version'] or '?'}",
            "",
        ]
        if v["notes"]:
            lines += [v["notes"], ""]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    # 任何模式下都点名"时间戳在未来" —— 这个缺陷安静过一次（兄弟项目），不该安静第二次。
    now = datetime.datetime.now(datetime.UTC)
    if CHANGELOG_JSONL.exists():
        rows = []
        for line in CHANGELOG_JSONL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        bad = [r for r in rows if is_future(r.get("ts"), now)]
        if bad:
            print(f"warning: {len(bad)}/{len(rows)} 条留痕的 ts 在未来（最大 "
                  f"{max(str(r.get('ts')) for r in bad)}）—— ts 写的是**当时的钟**；"
                  "正文不改，发现即点名", file=sys.stderr)

    text = render()
    if args.check:
        existing = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if existing != text:
            print("CHANGELOG.md is stale - run: python3 scripts/changelog_md.py --write")
            return 1
        print("CHANGELOG.md is up to date")
        return 0
    if args.write:
        TARGET.write_text(text, encoding="utf-8")
        print(f"wrote {TARGET} ({len(text.splitlines())} lines)")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
