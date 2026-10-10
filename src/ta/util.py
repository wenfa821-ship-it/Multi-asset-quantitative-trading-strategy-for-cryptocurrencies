"""Small standard-library helpers shared across the package."""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}

BARS_PER_YEAR: dict[str, float] = {
    "1m": 525_600,
    "3m": 175_200,
    "5m": 105_120,
    "15m": 35_040,
    "30m": 17_520,
    "1h": 8_760,
    "2h": 4_380,
    "4h": 2_190,
    "6h": 1_460,
    "12h": 730,
    "1d": 365,
    "1w": 52,
}

_ID_SAFE = re.compile(r"[^0-9A-Za-z]")


def now_ms() -> int:
    return int(time.time() * 1000)


def tf_to_ms(timeframe: str) -> int:
    try:
        return TIMEFRAME_MS[timeframe]
    except KeyError as exc:  # pragma: no cover - guarded by config validation
        raise ValueError(f"unsupported timeframe {timeframe!r}") from exc


def bars_per_year(timeframe: str) -> float:
    return BARS_PER_YEAR.get(timeframe, 8_760)


def sanitize_id(raw: str, maxlen: int = 32) -> str:
    """Venue client-order-ids must be alphanumeric (OKX: <=32 chars, no dashes)."""
    return _ID_SAFE.sub("", raw)[:maxlen]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator in (0, 0.0) or not math.isfinite(denominator):
        return default
    return numerator / denominator


def pct_of(part: float, whole: float, default: float = 0.0) -> float:
    return 100.0 * safe_div(part, whole, default)


def tz(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def day_key(ts_ms: int | None = None, tz_name: str = "UTC") -> str:
    ts = (ts_ms if ts_ms is not None else now_ms()) / 1000.0
    return datetime.fromtimestamp(ts, tz(tz_name)).strftime("%Y-%m-%d")


def iso(ts_ms: int | None = None) -> str:
    ts = (ts_ms if ts_ms is not None else now_ms()) / 1000.0
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def fmt_usd(value: float) -> str:
    return f"{value:,.2f}"


def fmt_pct(value: float) -> str:
    return f"{value:+.2f}%"


class VirtualClock:
    """Deterministic clock for simulated runs: one tick == one bar.

    Without it, a fast local paper run compresses 500 hourly bars into a few
    milliseconds and every time-based rule (order rate limits, daily loss reset,
    cooldowns) behaves as if the whole run happened inside one minute.
    """

    def __init__(self, start_ms: int | None = None, step_ms: int = 3_600_000) -> None:
        self.now_ms = start_ms if start_ms is not None else now_ms()
        self.step_ms = step_ms

    def __call__(self) -> float:
        return self.now_ms / 1000.0

    def advance(self, steps: int = 1) -> None:
        self.now_ms += self.step_ms * steps
