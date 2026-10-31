#!/usr/bin/env python3
"""Generate the read-only risk-budget utilisation report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import Config  # noqa: E402
from ta.dashboard import parse_metrics  # noqa: E402
from ta.risk_budget import build_risk_budget, default_inputs, markdown  # noqa: E402


def heat_pct(url: str) -> float | None:
    try:
        with urlopen(url, timeout=2) as response:  # noqa: S310 - loopback
            return parse_metrics(response.read().decode("utf-8", "replace")).get(
                "ta_heat_pct"
            )
    except Exception:  # noqa: BLE001 - report must survive a stopped metrics server
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--metrics-url", default="http://127.0.0.1:9101/metrics")
    parser.add_argument("--json-out", default="data/risk-budget.json")
    parser.add_argument("--md-out", default="reports/risk-budget.md")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    config = Config.load(args.config_dir)
    rows, pairs = default_inputs(config)
    report = build_risk_budget(
        config, rows, pairs=pairs, total_heat_pct=heat_pct(args.metrics_url)
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
    return 0 if report.get("available") else 1


if __name__ == "__main__":
    raise SystemExit(main())
