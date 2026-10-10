#!/usr/bin/env python3
"""Temporary watch inspection analysis."""
import json
from collections import Counter
from datetime import datetime, timezone, timedelta

PROJ = "/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"
LAST_OKX = 3002
LAST_PAPER = 6596

# CST boundary for 2026-10-03 00:00:00
CST = timezone(timedelta(hours=8))
today_start = int(datetime(2026, 10, 3, 0, 0, 0, tzinfo=CST).timestamp() * 1000)

def iter_lines(path, start=1):
    with open(path, "r") as f:
        for i, line in enumerate(f, start=1):
            if i < start:
                continue
            if not line.strip():
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue

def classify_reason(reason):
    normal_prefixes = (
        "max_open_positions",
        "daily_trade_cap",
        "symbol_daily_cap",
        "symbol_cooldown",
        "no_room",
        "heat",
    )
    if any(reason.startswith(p) for p in normal_prefixes):
        return "正常拦截"
    if reason in ("no_allowlist_configured",):
        return "配置事故"
    if reason in ("stale data",):
        return "数据管线问题"
    if "spread" in reason.lower():
        return "行情源/流动性问题"
    return "待判定"

okx_events = list(iter_lines(f"{PROJ}/data/ledger.okx_demo.jsonl", LAST_OKX + 1))
paper_events = list(iter_lines(f"{PROJ}/data/ledger.paper.jsonl", LAST_PAPER + 1))

print(f"okx_demo incremental lines: {len(okx_events)}")
print(f"paper incremental lines: {len(paper_events)}")

def summarize(events):
    kinds = Counter()
    blocked_reasons = Counter()
    blocked_today = Counter()
    rejected = 0
    mismatch = 0
    adopted_fills = 0
    for ln, ev in events:
        kind = ev.get("kind")
        ts = ev.get("ts", 0)
        if kind in ("order", "fill", "blocked", "risk"):
            kinds[kind] += 1
        if kind == "fill" and ev.get("tag") == "adopted":
            adopted_fills += 1
        if kind == "order" and ev.get("status") == "rejected":
            rejected += 1
        if kind == "blocked":
            reason = ev.get("reason", "")
            blocked_reasons[reason] += 1
            if ts >= today_start:
                blocked_today[reason] += 1
        if kind == "risk" and "reconciliation" in str(ev.get("action", "")).lower():
            mismatch += 1
        if kind == "order" and "rejected" in str(ev.get("reason", "")).lower():
            rejected += 1
    return kinds, blocked_reasons, blocked_today, rejected, mismatch, adopted_fills

okx_kinds, okx_blocked, okx_blocked_today, okx_rejected, okx_mismatch, okx_adopted = summarize(okx_events)

print("\n=== okx_demo incremental kind counts ===")
for k, v in okx_kinds.most_common():
    print(f"  {k}: {v}")
print(f"  adopted fills excluded: {okx_adopted}")
print(f"  rejected orders: {okx_rejected}")
print(f"  reconciliation mismatch events: {okx_mismatch}")

print("\n=== okx_demo incremental blocked by Top3 ===")
for reason, cnt in okx_blocked.most_common(3):
    print(f"  {reason}: {cnt} -> {classify_reason(reason)}")

print("\n=== okx_demo today (2026-10-03 CST) blocked by Top5 ===")
for reason, cnt in okx_blocked_today.most_common(5):
    print(f"  {reason}: {cnt} -> {classify_reason(reason)}")

# Also count today's total events by kind from full file (not just incremental)
def today_summary(path):
    kinds = Counter()
    blocked_today = Counter()
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = ev.get("ts", 0)
            if ts < today_start:
                continue
            kind = ev.get("kind")
            kinds[kind] += 1
            if kind == "blocked":
                blocked_today[ev.get("reason", "")] += 1
    return kinds, blocked_today

okx_today_kinds, okx_today_blocked = today_summary(f"{PROJ}/data/ledger.okx_demo.jsonl")
print("\n=== okx_demo today (2026-10-03 CST) all kind counts ===")
for k, v in okx_today_kinds.most_common():
    print(f"  {k}: {v}")
print("\n=== okx_demo today blocked by Top5 (cumulative) ===")
for reason, cnt in okx_today_blocked.most_common(5):
    print(f"  {reason}: {cnt} -> {classify_reason(reason)}")
