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
            keep = [
                line for line in text[2:]
                if line.strip()
                and not line.startswith("Frozen ")
                and not line.startswith("Strategy changes after this point")
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
    rows = []
    for line in CHANGELOG_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if iso_after and str(row.get("ts", "")) <= iso_after:
            continue
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("ts")))
    return rows


def render() -> str:
    versions = load_versions()
    current = (PROJECT / "VERSION").read_text(encoding="utf-8").strip() if (PROJECT / "VERSION").exists() else "?"
    last = versions[-1]["created_at"] if versions else ""
    unreleased = load_since(last)
    lines = [
        "# Changelog",
        "",
        f"当前版本：**v{current}**（`VERSION` 是唯一事实来源；`pyproject.toml` 与 "
        "`ta.__version__` 由 `scripts/version_sync.py --check` 强制一致）。",
        "",
        "版本规则见 [`docs/RELEASING.md`](docs/RELEASING.md)：**1.0.0 = 第一阶段（五步路径）完成**；"
        "1.0 之后按语义化版本 —— MAJOR 表示架构/配置模式不兼容，MINOR 表示新增能力（新腿、新策略），"
        "PATCH 表示修复。每个版本冻结在 `archive/versions/<v>/`（config + changelog + progress + manifest + NOTES）。",
        "",
    ]
    if unreleased:
        lines += [f"## Unreleased（自 {last[:16] or '最初'} 起，{len(unreleased)} 条留痕）", ""]
        for row in unreleased:
            lines.append(f"- `{str(row.get('ts'))[:16]}` **{row.get('kind','?')}** {row.get('title','')}")
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
