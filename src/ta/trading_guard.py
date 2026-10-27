"""Hard gates that must pass before an order can leave for a CEX venue."""

from __future__ import annotations

import os
from collections.abc import Mapping

from .config import Config


def trading_safety_problems(
    config: Config,
    venue_name: str,
    *,
    enable_trading: bool,
    allow_live: bool,
    metrics_port: int,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Return reasons order submission is unsafe for this configuration.

    Demo submission remains convenient, but may not run while the application
    says ``live``. Real-money submission needs three independent signals:
    ``app.mode=live``, a non-sandbox venue, and ``--allow-live``.
    """
    if not enable_trading or venue_name == "paper":
        return []

    section = config.venue(venue_name)
    problems: list[str] = []
    if section.sandbox:
        if config.app.mode != "demo":
            problems.append(
                f"venue {venue_name!r} is sandboxed but app.mode={config.app.mode!r}; "
                "sandbox order submission requires app.mode='demo'"
            )
        return problems

    if config.app.mode != "live":
        problems.append(
            f"venue {venue_name!r} is NOT sandboxed but app.mode={config.app.mode!r}; "
            "refusing real-money order submission"
        )
    if not allow_live:
        problems.append(
            f"venue {venue_name!r} is NOT sandboxed; add --allow-live after independently "
            "verifying the endpoint, account and key permissions"
        )
    if metrics_port <= 0:
        problems.append("live order submission requires --metrics-port > 0")

    values = env if env is not None else os.environ
    if not values.get("TA_ALERT_WEBHOOK_URL", "").strip():
        problems.append("live order submission requires TA_ALERT_WEBHOOK_URL")
    if config.kill_switch_path.exists():
        problems.append(f"kill switch is present: {config.kill_switch_path}")
    return problems
