#!/usr/bin/env python3
"""Generate the read-only carry slot-capacity audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.carry_capacity import carry_capacity_audit, markdown  # noqa: E402
from ta.config import Config  # noqa: E402
from ta.util import now_ms  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--min-edge-bps", type=float, default=20.0)
    parser.add_argument("--required-tokens", type=int, default=20)
    parser.add_argument("--json-out", default="data/carry/capacity_audit.json")
    parser.add_argument("--md-out", default="reports/carry-capacity.md")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    config = Config.load(args.config_dir)
    report = carry_capacity_audit(
        load_jsonl(PROJECT / args.ledger),
        now_ms=now_ms(),
        days=args.days,
        min_edge_bps=args.min_edge_bps,
        required_tokens=args.required_tokens,
        current_slots=config.risk.legs["spot"].max_open_positions,
    )
    text = markdown(report)
    print(text)
    if args.write:
        json_path = PROJECT / args.json_out
        md_path = PROJECT / args.md_out
        json_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        md_path.write_text(text, encoding="utf-8")
        print("wrote", json_path)
        print("wrote", md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
