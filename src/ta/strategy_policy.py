"""Evidence-based admission policy for strategies in the production runner.

Research and production have different jobs. Research may run a strategy with a
small sample to collect evidence. Production should allocate capital only when
the out-of-sample evidence does not contradict the strategy.

This module reads the latest walk-forward report (``data/readiness/edge.json``),
applies the same 30-trade minimum as live-readiness gate 1, and returns a
deterministic decision for every enabled strategy. Missing/stale evidence is
reported explicitly: a stale negative result remains quarantined, while a truly
missing report fails open so a data-job outage cannot silently halt the process.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from .config import Config

MIN_OOS_TRADES = 30
EVIDENCE_STALE_HOURS = 72.0


def _read_edge_report(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"missing {path}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read {path}: {type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return None, f"{path} is not a JSON object"
    return payload, ""


def _evidence_age_hours(payload: dict[str, Any], now_ms: int) -> float | None:
    raw = str(payload.get("generated_at") or "")
    if not raw:
        return None
    try:
        stamp = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=datetime.timezone.utc)
        return max(0.0, (now_ms / 1000.0 - stamp.timestamp()) / 3600.0)
    except ValueError:
        return None


def strategy_admission(
    config: Config,
    *,
    edge_path: str | Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Return production admission decisions for every enabled strategy.

    States:

    * ``admitted``: enough out-of-sample trades and positive return;
    * ``collecting``: too little evidence, so tight-size data collection remains;
    * ``live_evidence``: carry is measured by live-readiness gate 5;
    * ``quarantined``: enough trades exist and the out-of-sample edge is <= 0.
    """
    now_ms = (
        int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        if now_ms is None
        else int(now_ms)
    )
    path = Path(edge_path) if edge_path is not None else (
        Path(config.data_dir) / "readiness" / "edge.json"
    )
    payload, load_error = _read_edge_report(path)
    generated_at = str((payload or {}).get("generated_at") or "")
    age_hours = _evidence_age_hours(payload or {}, now_ms)
    stale = bool(age_hours is not None and age_hours > EVIDENCE_STALE_HOURS)
    edge_rows = {
        str(row.get("strategy")): row
        for row in ((payload or {}).get("rows") or [])
        if isinstance(row, dict) and row.get("strategy")
    }

    decisions: list[dict[str, Any]] = []
    for name, spec in config.strategies.items():
        if not bool(getattr(spec, "enabled", False)):
            continue
        row = edge_rows.get(name)
        if name == "carry_funding":
            state = "live_evidence"
            admitted = True
            reason = "carry is measured by live-readiness gate 5, not OHLCV backtest"
            trades = None
            return_pct = None
        elif row is None:
            state = "collecting"
            admitted = True
            reason = "no out-of-sample row; retain tight-size data collection"
            trades = None
            return_pct = None
        else:
            trades = int(row.get("trades") or 0)
            raw_return = row.get("total_return_pct")
            return_pct = float(raw_return) if isinstance(raw_return, (int, float)) else None
            if trades < MIN_OOS_TRADES or return_pct is None:
                state = "collecting"
                admitted = True
                reason = f"out-of-sample sample is {trades} trades; need {MIN_OOS_TRADES}"
            elif return_pct > 0:
                state = "admitted"
                admitted = True
                reason = f"out-of-sample {return_pct:+.2f}% over {trades} trades"
            else:
                state = "quarantined"
                admitted = False
                reason = f"out-of-sample {return_pct:+.2f}% over {trades} trades"
        decisions.append(
            {
                "strategy": name,
                "admitted": admitted,
                "state": state,
                "reason": reason,
                "trades": trades,
                "total_return_pct": return_pct,
            }
        )

    return {
        "generated_at": datetime.datetime.fromtimestamp(
            now_ms / 1000.0, datetime.timezone.utc
        ).isoformat(),
        "policy": "oos-return-positive-v1",
        "min_oos_trades": MIN_OOS_TRADES,
        "evidence_path": str(path),
        "evidence_available": payload is not None,
        "evidence_generated_at": generated_at,
        "evidence_age_hours": round(age_hours, 3) if age_hours is not None else None,
        "evidence_stale": stale,
        "error": load_error,
        "decisions": decisions,
        "admitted": [row["strategy"] for row in decisions if row["admitted"]],
        "quarantined": [row["strategy"] for row in decisions if not row["admitted"]],
    }
