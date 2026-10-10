"""Venue construction + credential resolution."""

from __future__ import annotations

import os

from ..config import Config
from .base import Venue, VenueError
from .ccxt_venue import CcxtVenue
from .paper import PaperVenue

__all__ = ["Venue", "VenueError", "PaperVenue", "CcxtVenue", "build_venue", "credential_status"]


def credential_status(config: Config, name: str) -> dict[str, bool | str]:
    if name == "paper":
        return {"venue": name, "requires_keys": False, "ready": True}
    section = config.venue(name)
    envs = [section.api_key_env, section.secret_env, section.password_env]
    present = {env: bool(os.environ.get(env)) for env in envs if env}
    return {
        "venue": name,
        "exchange_id": section.exchange_id,
        "sandbox": section.sandbox,
        "requires_keys": True,
        "vars": present,
        "ready": all(present.values()) if present else False,
    }


def _secret(env_name: str, label: str, required: bool = True) -> str:
    value = os.environ.get(env_name, "")
    if required and not value:
        raise VenueError(
            f"{label} is not set. Export {env_name} before running (see .env.example)."
        )
    return value


def build_venue(config: Config, name: str, *, dry_run: bool = True, clock=None) -> Venue:
    section = config.venue(name)
    if name == "paper":
        return PaperVenue(
            initial_cash=section.initial_cash,
            fee_bps=section.fee_bps,
            slippage_bps=section.slippage_bps,
            base_ccy=config.app.base_currency,
            timezone=config.app.timezone,
            min_notional_usd=config.risk.per_trade.min_order_notional_usd,
            clock=clock,
        )
    if dry_run:
        # Reserved for a "live credentials, no orders" mode. Currently the runner
        # enforces dry-run above the venue layer, so we still build the client.
        pass
    return CcxtVenue(
        exchange_id=section.exchange_id,
        api_key=_secret(section.api_key_env, "API key"),
        secret=_secret(section.secret_env, "API secret"),
        password=_secret(section.password_env, "API passphrase", required=False) or None,
        sandbox=section.sandbox,
        market_type=section.market_type,
        name=name,
        base_ccy=config.app.base_currency,
        fee_bps=section.fee_bps,
        slippage_bps=section.slippage_bps,
    )
