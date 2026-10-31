#!/usr/bin/env python3
"""Write the strategy admission decisions used by the runner and dashboard."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.strategy_policy import strategy_admission  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--edge", default="data/readiness/edge.json")
    parser.add_argument("--output", default="data/readiness/strategy-admission.json")
    parser.add_argument("--write", action="store_true", help="write the JSON decision report")
    args = parser.parse_args()

    config = Config.load(args.config_dir)
    result = strategy_admission(config, edge_path=args.edge)
    print("strategy admission policy:", result["policy"])
    print("evidence:", result["evidence_generated_at"] or result["error"] or "unknown")
    for row in result["decisions"]:
        print(f"{row['state']:<12} {row['strategy']:<24} {row['reason']}")
    print("admitted:", ", ".join(result["admitted"]) or "(none)")
    print("quarantined:", ", ".join(result["quarantined"]) or "(none)")

    if args.write:
        path = PROJECT / args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
