"""第二个项目的面板数据：**只读它自己发布的 JSON**。

2026-10-11 用户要求「在运营看板上把多资产组合策略与新币主观量化策略分开展示」。
新币项目（`../newcoin-subjective-quant`）与本项目是**独立仓库、独立证据标准**
（README §71 / 该项目 docs/EVIDENCE.md），所以看板不 import 它的 Python 模块 ——
那是跨仓库耦合：它重命名一个函数就会把这里的看板弄坏。分工是：

    新币项目自己把状态写成 data/reports/latest.json  →  本项目只读这个文件

于是「读不到」也只是一个空面板，而不是看板 500。文件缺失/过期都要**说出来原因**，
不显示成 0 —— 0 和"没采集"是两件事（这条纪律在父项目里也是同一条）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = PROJECT.parent / "newcoin-subjective-quant"
REPORT_RELATIVE = Path("data") / "reports" / "latest.json"

# 采集是每天一次，所以超过这个年龄基本就是"某天没跑成"。
STALE_AFTER_SECONDS = 26 * 3600


def project_dir() -> Path:
    override = os.environ.get("NCQ_PROJECT_DIR")
    return Path(override).expanduser() if override else DEFAULT_DIR


def _age_seconds(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        import datetime
        parsed = datetime.datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return round(time.time() - parsed.timestamp(), 1)


def view(directory: Path | None = None) -> dict:
    """新项目的面板数据。永远返回一个 dict，永远不抛异常。"""
    target = directory or project_dir()
    path = target / REPORT_RELATIVE
    base = {"project_dir": str(target), "report_path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {**base, "available": False,
                "reason": f"还没有报告文件（{REPORT_RELATIVE}）——"
                          "在该项目里跑 python3 scripts/build_report.py --write"}
    except (OSError, ValueError) as exc:
        return {**base, "available": False,
                "reason": f"报告文件读不了：{type(exc).__name__}: {exc}"}

    age = _age_seconds(payload.get("generated_at"))
    stale = age is not None and age > STALE_AFTER_SECONDS
    return {**base, "available": True, "age_seconds": age, "stale": stale, **payload}
