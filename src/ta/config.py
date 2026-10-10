"""Typed configuration loading (TOML via stdlib tomllib) with validation.

Everything the bot is allowed to do lives in config/*.toml. Nothing is hardcoded.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

from .util import TIMEFRAME_MS

# --------------------------------------------------------------------------------------
# generic dataclass builder
# --------------------------------------------------------------------------------------


def _coerce(target: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = typing.get_origin(target)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(target) if a is not type(None)]
        return _coerce(args[0], value) if args else value
    if dataclasses.is_dataclass(target) and isinstance(value, dict):
        return build(target, value)
    if origin is list:
        args = typing.get_args(target)
        item_type = args[0] if args else Any
        return [_coerce(item_type, v) for v in value]
    if origin is dict:
        args = typing.get_args(target)
        value_type = args[1] if len(args) == 2 else Any
        return {str(k): _coerce(value_type, v) for k, v in value.items()}
    if target is bool:
        return bool(value)
    if target is int:
        return int(value)
    if target is float:
        return float(value)
    if target is str:
        return str(value)
    return value


def build(cls: type, data: dict[str, Any]) -> Any:
    """Build a dataclass from a dict, ignoring unknown keys, keeping defaults."""
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            kwargs[f.name] = _coerce(hints[f.name], data[f.name])
    return cls(**kwargs)


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


# --------------------------------------------------------------------------------------
# app.toml
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class AppSection:
    mode: str = "paper"
    base_currency: str = "USDT"
    data_dir: str = "data"
    log_dir: str = "logs"
    timezone: str = "Asia/Shanghai"
    kill_switch_file: str = "KILL_SWITCH"
    # Second venue for the hedge leg of a carry pair (README 37). Empty = no
    # hedge venue, and a carry-tagged signal then does NOT trade at all: opening
    # the spot leg alone would be a naked long, which is not carry. Kept out of
    # the strategy config on purpose - this is an execution concern, not a
    # strategy parameter.
    hedge_venue: str = ""


@dataclass(slots=True)
class UniverseSection:
    symbols: list[str] = field(default_factory=list)
    timeframe: str = "1h"
    bar_limit: int = 400
    # extra timeframes the feed should keep warm, so a 15m strategy can confirm
    # against 1h/4h without a second data pipeline
    timeframes: list[str] = field(default_factory=list)
    # symbol -> tier ("majors" | "alts" | "new" | anything you define in risk.toml)
    symbols_by_tier: dict[str, list[str]] = field(default_factory=dict)

    @property
    def all_symbols(self) -> list[str]:
        """Every traded symbol: the explicit list plus anything declared in a tier."""
        out = list(self.symbols)
        for group in self.symbols_by_tier.values():
            for symbol in group:
                if symbol not in out:
                    out.append(symbol)
        return out

    def tier_of(self, symbol: str) -> str | None:
        for tier, group in self.symbols_by_tier.items():
            if symbol in group:
                return tier
        return None

    def symbol_tiers(self) -> dict[str, str]:
        return {s: t for t, group in self.symbols_by_tier.items() for s in group}

    @property
    def all_timeframes(self) -> list[str]:
        return list(dict.fromkeys([self.timeframe, *self.timeframes]))


@dataclass(slots=True)
class LoopSection:
    poll_seconds: float = 30.0
    max_iterations: int = 0


@dataclass(slots=True)
class VenueSection:
    exchange_id: str = "okx"
    market_type: str = "spot"
    sandbox: bool = True
    api_key_env: str = ""
    secret_env: str = ""
    password_env: str = ""
    fee_bps: float = 8.0
    # Maker fee is a separate rate: entering with a resting limit order instead
    # of crossing the spread. On OKX Lv1 that is 8bps vs 10bps taker - a 10%
    # saving per round trip, NOT the 4x a VIP-tier assumption would suggest.
    maker_fee_bps: float = 8.0
    slippage_bps: float = 5.0
    initial_cash: float = 10_000.0


# --------------------------------------------------------------------------------------
# risk.toml
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class AccountLimits:
    max_leverage_pct: float = 100.0
    max_gross_exposure_pct: float = 60.0
    max_open_positions: int = 3
    min_cash_buffer_pct: float = 10.0
    # sum of open risk (distance from mark to stop x qty) / equity
    max_heat_pct: float = 4.0
    max_trades_per_day: int = 12
    # assets the bot did not open (deposits, manual trades, a pre-funded demo
    # bundle) count towards equity but not towards exposure limits by default
    count_unmanaged_exposure: bool = False
    # is min_cash_buffer_pct a share of available cash or of total equity?
    cash_buffer_basis: str = "cash"
    # Should the bot take ownership of positions it did NOT open (deposits,
    # manual trades, an exchange demo account's seed holdings)? When true it
    # arms stops/targets on them and WILL SELL THEM. Fail-safe default: false.
    adopt_existing_positions: bool = False


@dataclass(slots=True)
class PerTradeLimits:
    risk_per_trade_pct: float = 0.4
    max_order_notional_pct: float = 10.0
    max_order_notional_usd: float = 1500.0
    min_order_notional_usd: float = 20.0
    max_price_deviation_pct: float = 0.8
    max_orders_per_minute: int = 6
    max_spread_bps: float = 20.0
    # Refuse an entry whose own size would move the book more than this, in bps of
    # mid. Measured 2026-10-09: a carry entry bought $2.2k of ACT spot 1.33% above
    # the market minute's high (the hedge leg filled at market) - that one leg cost
    # 3x the 44.9 bps round trip every carry conclusion rested on, and nothing in
    # the pipeline looked at depth. A spread check cannot catch this: the spread was
    # fine, the DEPTH was not. 0 disables the gate.
    max_entry_impact_bps: float = 50.0
    # Refuse a carry pair whose perp is more than this far BELOW spot at entry.
    # The pair realises P - S on convergence, so a perp discount is a cost paid up
    # front - measured 2026-10-09: ACT opened 130.6 bps below spot (~$29), and
    # CRV's premium drifted 1.96% against us for -$54. 0 disables the gate.
    max_basis_discount_bps: float = 10.0


@dataclass(slots=True)
class PositionLimits:
    max_position_pct_per_symbol: float = 15.0
    stop_atr_multiple: float = 2.5
    take_profit_r_multiple: float = 2.0
    trailing_stop_enabled: bool = True
    trailing_stop_atr_multiple: float = 3.0
    atr_target_pct: float = 1.5
    vol_target_min_scale: float = 0.30
    vol_target_max_scale: float = 1.50


@dataclass(slots=True)
class CircuitBreakers:
    daily_loss_stop_open_pct: float = 2.0
    daily_loss_flatten_pct: float = 4.0
    max_drawdown_kill_pct: float = 15.0
    consecutive_losses_pause: int = 5
    streak_cooldown_minutes: int = 360
    cooldown_minutes_after_stop: int = 120
    # per-symbol cooldown after a protective (stop) exit
    cooldown_after_stop_minutes: int = 90
    # refuse new entries when the newest bar is older than this many bars
    stale_data_bars: int = 2
    # consecutive unexpected tick errors before the loop gives up
    max_tick_errors: int = 10


@dataclass(slots=True)
class SymbolRules:
    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)
    max_trades_per_symbol_per_day: int = 3
    min_quote_volume_24h: float = 5_000_000.0


@dataclass(slots=True)
class TierLimits:
    """Per-tier limits. `new` should always be the tightest bucket."""

    risk_per_trade_pct: float = 0.25
    max_position_pct: float = 10.0
    max_order_notional_pct: float = 5.0
    max_orders_per_minute: int = 4
    max_trades_per_day: int = 8
    max_positions: int = 2
    max_spread_bps: float = 15.0
    cooldown_after_stop_minutes: int = 90
    stop_atr_multiple: float = 2.0
    take_profit_r_multiple: float = 1.6
    atr_target_pct: float = 1.5
    max_stop_pct: float = 3.5
    # Carry pairs are delta-neutral: their risk is funding flipping or the basis
    # blowing out, NOT price direction - so sizing them by "distance to a price
    # stop" makes the notional a function of the symbol's volatility instead of
    # its funding economics. Measured 2026-10-09 on ACT: risk_based_qty produced
    # 402,735 ACT (~$3.93k) purely because its stop sat 5.1% below entry, and the
    # only thing that actually bounded it was max_order_notional_pct.
    # When >0, a carry entry is sized to this % of equity in PAIR NOTIONAL.
    # 0 = fall back to max_order_notional_pct (today's effective behaviour).
    carry_notional_pct: float = 0.0


@dataclass(slots=True)
class ClusterRules:
    """Arbitrary named buckets; every key except the cap is treated as a bucket."""

    buckets: dict[str, list[str]] = field(default_factory=dict)
    max_cluster_exposure_pct: float = 30.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClusterRules":
        cap = float(data.get("max_cluster_exposure_pct", 30.0))
        buckets = {
            key: [str(s) for s in value]
            for key, value in data.items()
            if key != "max_cluster_exposure_pct" and isinstance(value, list)
        }
        return cls(buckets=buckets, max_cluster_exposure_pct=cap)

    def bucket_of(self, symbol: str) -> str | None:
        for name, members in self.buckets.items():
            if symbol in members:
                return name
        return None


@dataclass(slots=True)
class LegLimits:
    """One leg of the split book (README sections 20/28).

    A symbol carrying a ":" (``SOL/USDT:USDT``) is a derivative; everything else
    is spot. Each leg gets its own ceiling ON TOP of the account-level limits, so
    "half in spot, half in derivatives" cannot silently become "spot uses its
    half and derivatives add their half again".

    Deliberately no `max_leverage_x` here: the engine never sees margin, only
    notional, and this project has been bitten before by a config key that looked
    wired and was not read (README section 25, `min_edge_cost_multiple`). The
    notional ceiling IS the leverage control - a 50% notional leg at 3x is a
    ~17% margin commitment - and leverage itself stays a venue-layer parameter.
    """

    max_gross_exposure_pct: float = 0.0   # 0 = no extra cap from this leg
    max_open_positions: int = 0           # 0 = no extra cap
    max_heat_pct: float = 0.0             # 0 = no extra cap
    max_trades_per_day: int = 0           # 0 = no extra cap
    # Explicit membership for legs that cannot be recognised from the symbol
    # alone. Options carry a strike/right suffix and perps a ":settle" suffix, so
    # those two are structural; TradFi tokenised equities look exactly like any
    # other spot pair (`XAAPL/USDT`), and a "starts with X" guess would swallow
    # XRP the moment somebody added it. So the sleeve is listed.
    symbols: list[str] = field(default_factory=list)
    # For a sleeve that DOES have a naming convention (TradFi = "XAAPL/USDT"), a
    # prefix beats listing 100+ symbols. Membership above is checked first, so a
    # crypto sharing the letter can be pinned to its own leg.
    symbol_prefix: str = ""


@dataclass(slots=True)
class RiskConfig:
    account: AccountLimits = field(default_factory=AccountLimits)
    per_trade: PerTradeLimits = field(default_factory=PerTradeLimits)
    positions: PositionLimits = field(default_factory=PositionLimits)
    circuit: CircuitBreakers = field(default_factory=CircuitBreakers)
    symbols: SymbolRules = field(default_factory=SymbolRules)
    clusters: ClusterRules = field(default_factory=ClusterRules)
    tiers: dict[str, TierLimits] = field(default_factory=dict)
    legs: dict[str, LegLimits] = field(default_factory=dict)
    default_tier: str = "alts"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RiskConfig":
        tiers_raw = data.get("tiers", {}) or {}
        legs_raw = data.get("legs", {}) or {}
        return cls(
            account=build(AccountLimits, data.get("account", {})),
            per_trade=build(PerTradeLimits, data.get("per_trade", {})),
            positions=build(PositionLimits, data.get("positions", {})),
            circuit=build(CircuitBreakers, data.get("circuit_breakers", {})),
            symbols=build(SymbolRules, data.get("symbols", {})),
            clusters=ClusterRules.from_dict(data.get("clusters", {})),
            tiers={name: build(TierLimits, section) for name, section in tiers_raw.items()},
            legs={name: build(LegLimits, section) for name, section in legs_raw.items()},
            default_tier=str(data.get("default_tier", "alts")),
        )

    def limits_for(self, tier: str | None) -> TierLimits:
        """Tier limits, falling back to the default tier, then to global settings."""
        if tier and tier in self.tiers:
            return self.tiers[tier]
        if self.default_tier in self.tiers:
            return self.tiers[self.default_tier]
        return TierLimits(
            risk_per_trade_pct=self.per_trade.risk_per_trade_pct,
            max_position_pct=self.positions.max_position_pct_per_symbol,
            max_order_notional_pct=self.per_trade.max_order_notional_pct,
            max_orders_per_minute=self.per_trade.max_orders_per_minute,
            max_trades_per_day=self.account.max_trades_per_day,
            max_positions=self.account.max_open_positions,
            max_spread_bps=self.per_trade.max_spread_bps,
            cooldown_after_stop_minutes=self.circuit.cooldown_after_stop_minutes,
            stop_atr_multiple=self.positions.stop_atr_multiple,
            take_profit_r_multiple=self.positions.take_profit_r_multiple,
            atr_target_pct=self.positions.atr_target_pct,
        )

    @property
    def tier_names(self) -> list[str]:
        return sorted(self.tiers)


# --------------------------------------------------------------------------------------
# onchain.toml
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class OnchainSafety:
    reject_honeypot: bool = True
    require_lp_locked: bool = True
    max_sell_tax_pct: float = 5.0
    max_top10_holder_pct: float = 40.0
    min_holder_count: int = 300
    min_liquidity_usd: float = 50_000.0
    min_age_minutes: int = 30
    max_price_impact_pct: float = 3.0
    max_slippage_bps: int = 100
    min_volume_liquidity_ratio: float = 0.05
    require_known_safety_fields: bool = True
    min_lp_locked_pct: float = 90.0
    required_fields: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OnchainSizing:
    max_position_pct_per_token: float = 2.0
    risk_per_trade_pct: float = 0.2
    max_concurrent_tokens: int = 2
    equity_usd: float = 1000.0  # reference equity for the risk gate (wire to your wallet balance)
    sol_price_usd: float = 150.0  # wire to a price source before believing any notional


@dataclass(slots=True)
class OnchainExecution:
    slippage_bps: int = 80
    max_priority_fee_sol: float = 0.002
    use_jito: bool = True
    dry_run: bool = True
    signer: str = "mock"           # refuse | mock | local-keypair | kms | hardware
    allow_broadcast: bool = False  # second consent; broadcast is refused unless true


@dataclass(slots=True)
class DataSourceConfig:
    enabled: bool = True
    endpoint: str = ""
    trending_endpoint: str = ""
    chain: str = "sol"
    headers: dict[str, str] = field(default_factory=dict)
    cookie_env: str = ""


@dataclass(slots=True)
class OnchainData:
    cache_dir: str = "data/onchain_cache"
    fixture_dir: str = "fixtures/onchain"
    timeout_seconds: float = 8.0
    retries: int = 2
    offline: bool = False
    sources: dict[str, DataSourceConfig] = field(default_factory=dict)


@dataclass(slots=True)
class OnchainRpc:
    url_env: str = "SOLANA_RPC_URL"
    commitment: str = "processed"
    simulate: bool = True


@dataclass(slots=True)
class OnchainJupiter:
    quote_endpoint: str = "https://lite-api.jup.ag/swap/v1/quote"
    swap_endpoint: str = "https://lite-api.jup.ag/swap/v1/swap"
    priority_level: str = "high"
    max_priority_fee_lamports: int = 200_000
    wrapped_sol: bool = True


@dataclass(slots=True)
class OnchainBlacklist:
    mints: list[str] = field(default_factory=list)
    dev_wallets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OnchainConfig:
    safety: OnchainSafety = field(default_factory=OnchainSafety)
    sizing: OnchainSizing = field(default_factory=OnchainSizing)
    execution: OnchainExecution = field(default_factory=OnchainExecution)
    blacklist: OnchainBlacklist = field(default_factory=OnchainBlacklist)
    data: OnchainData = field(default_factory=OnchainData)
    rpc: OnchainRpc = field(default_factory=OnchainRpc)
    jupiter: OnchainJupiter = field(default_factory=OnchainJupiter)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OnchainConfig":
        return cls(
            safety=build(OnchainSafety, data.get("safety", {})),
            sizing=build(OnchainSizing, data.get("sizing", {})),
            execution=build(OnchainExecution, data.get("execution", {})),
            blacklist=build(OnchainBlacklist, data.get("blacklist", {})),
            data=build(OnchainData, data.get("data", {})),
            rpc=build(OnchainRpc, data.get("rpc", {})),
            jupiter=build(OnchainJupiter, data.get("jupiter", {})),
        )


# --------------------------------------------------------------------------------------
# aggregate
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class StrategySpec:
    name: str
    enabled: bool = True
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    symbols: list[str] = field(default_factory=list)
    timeframe: str = "1h"


@dataclass(slots=True)
class Config:
    project_dir: Path
    config_dir: Path
    app: AppSection
    universe: UniverseSection
    loop: LoopSection
    venues: dict[str, VenueSection]
    risk: RiskConfig
    onchain: OnchainConfig
    strategies: dict[str, StrategySpec]

    # ---- paths -------------------------------------------------------------
    def path(self, relative: str) -> Path:
        candidate = Path(relative)
        return candidate if candidate.is_absolute() else self.project_dir / candidate

    @property
    def data_dir(self) -> Path:
        return self.path(self.app.data_dir)

    @property
    def log_dir(self) -> Path:
        return self.path(self.app.log_dir)

    @property
    def ledger_path(self) -> Path:
        return self.data_dir / "ledger.jsonl"

    @property
    def kill_switch_path(self) -> Path:
        return self.project_dir / self.app.kill_switch_file

    def venue(self, name: str) -> VenueSection:
        if name not in self.venues:
            raise KeyError(f"venue {name!r} not defined in app.toml (have: {sorted(self.venues)})")
        return self.venues[name]

    def strategy(self, name: str) -> StrategySpec:
        if name not in self.strategies:
            raise KeyError(f"strategy {name!r} not defined in strategies.toml")
        return self.strategies[name]

    def resolve_symbols(self, spec: "StrategySpec") -> list[str]:
        """Expand `tier:majors` style entries so symbol lists are not duplicated."""
        out: list[str] = []
        for entry in spec.symbols or []:
            if entry.startswith("tier:"):
                out.extend(self.universe.symbols_by_tier.get(entry.split(":", 1)[1], []))
            else:
                out.append(entry)
        return list(dict.fromkeys(out)) or list(self.traded_symbols)

    # ---- universe helpers --------------------------------------------------
    @property
    def traded_symbols(self) -> list[str]:
        return self.universe.all_symbols

    @property
    def symbol_tiers(self) -> dict[str, str]:
        return self.universe.symbol_tiers()

    @property
    def allowed_symbols(self) -> set[str]:
        """Tier declarations are the whitelist when they exist."""
        tiers = set(self.symbol_tiers)
        return tiers if tiers else set(self.risk.symbols.whitelist)

    # ---- loading -----------------------------------------------------------
    @classmethod
    def load(cls, config_dir: str | Path = "config") -> "Config":
        cfg_dir = Path(config_dir).resolve()
        app_raw = _read(cfg_dir / "app.toml")
        risk_raw = _read(cfg_dir / "risk.toml")
        strat_raw = _read(cfg_dir / "strategies.toml")

        onchain_path = cfg_dir / "onchain.toml"
        onchain_raw = _read(onchain_path) if onchain_path.exists() else {}

        venues = {
            name: build(VenueSection, section)
            for name, section in app_raw.get("venues", {}).items()
        }

        strategies: dict[str, StrategySpec] = {}
        for name, section in strat_raw.get("strategies", {}).items():
            section = dict(section)
            enabled = bool(section.pop("enabled", True))
            description = str(section.pop("description", ""))
            symbols = [str(s) for s in section.pop("symbols", [])]
            timeframe = str(section.pop("timeframe", "1h"))
            strategies[name] = StrategySpec(
                name=name,
                enabled=enabled,
                description=description,
                params=section,
                symbols=symbols,
                timeframe=timeframe,
            )

        config = cls(
            project_dir=cfg_dir.parent,
            config_dir=cfg_dir,
            app=build(AppSection, app_raw.get("app", {})),
            universe=build(UniverseSection, app_raw.get("universe", {})),
            loop=build(LoopSection, app_raw.get("loop", {})),
            venues=venues,
            risk=RiskConfig.from_dict(risk_raw),
            onchain=OnchainConfig.from_dict(onchain_raw),
            strategies=strategies,
        )
        return config

    # ---- validation --------------------------------------------------------
    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.traded_symbols:
            problems.append("universe.symbols (and symbols_by_tier) is empty")
        if self.universe.timeframe not in TIMEFRAME_MS:
            problems.append(f"universe.timeframe {self.universe.timeframe!r} unsupported")
        for timeframe in self.universe.all_timeframes:
            if timeframe not in TIMEFRAME_MS:
                problems.append(f"universe.timeframes contains unsupported {timeframe!r}")
        if self.universe.bar_limit < 120:
            problems.append("universe.bar_limit < 120, strategies will never warm up")
        if self.loop.poll_seconds <= 0:
            problems.append("loop.poll_seconds must be > 0")

        # ---- tiers ---------------------------------------------------------
        for symbol, tier in self.symbol_tiers.items():
            if tier not in self.risk.tiers:
                problems.append(
                    f"symbol {symbol} is in tier {tier!r} but risk.tiers has no [{tier}] section"
                )
        tiers = self.risk.tiers
        if tiers:
            if "new" in tiers and "alts" in tiers:
                if tiers["new"].max_position_pct > tiers["alts"].max_position_pct:
                    problems.append("tier new allows a larger position than tier alts")
                if tiers["new"].risk_per_trade_pct > tiers["alts"].risk_per_trade_pct:
                    problems.append("tier new risks more per trade than tier alts")
            for name, tier in tiers.items():
                if tier.risk_per_trade_pct <= 0 or tier.risk_per_trade_pct > 2.0:
                    problems.append(f"tier {name}: risk_per_trade_pct must be in (0, 2.0]")
                if tier.max_position_pct <= 0:
                    problems.append(f"tier {name}: max_position_pct must be > 0")
        if self.risk.account.max_heat_pct <= 0:
            problems.append("risk.account.max_heat_pct must be > 0")
        if self.risk.account.cash_buffer_basis not in ("cash", "equity"):
            problems.append("risk.account.cash_buffer_basis must be cash|equity")
        if self.risk.circuit.stale_data_bars < 1:
            problems.append("risk.circuit_breaker.stale_data_bars must be >= 1")

        if self.app.mode not in ("paper", "demo", "live"):
            problems.append(f"app.mode {self.app.mode!r} must be paper|demo|live")

        per_trade = self.risk.per_trade
        if per_trade.risk_per_trade_pct <= 0 or per_trade.risk_per_trade_pct > 2.0:
            problems.append("risk.per_trade.risk_per_trade_pct must be in (0, 2.0]")
        if per_trade.max_orders_per_minute <= 0:
            problems.append("risk.per_trade.max_orders_per_minute must be > 0")
        if self.risk.positions.max_position_pct_per_symbol <= 0:
            problems.append("risk.positions.max_position_pct_per_symbol must be > 0")
        if self.risk.account.max_gross_exposure_pct > 100 and self.risk.account.max_leverage_pct <= 100:
            problems.append("max_gross_exposure_pct > 100 while leverage is spot-only")
        # Directional strategies sharing a universe is BY DESIGN (they simply
        # never pyramid into an open position). Carry is different, and that
        # difference is a live hazard: measured 2026-10-08, ADA/DOGE/LINK/LTC/
        # NEAR/PEPE/SUI sat in both `alts` and `carry`, so carry bought the spot
        # leg and shorted the perp while trend_pullback sold "its" ADA - the SPOT
        # LEG ONLY - leaving the perp short naked. It also looked exactly like
        # carry churning. So the rule is narrow and specific: no other strategy
        # may touch a carry symbol.
        carry_symbols = set(self.universe.symbols_by_tier.get("carry") or [])
        if carry_symbols:
            for name, spec in self.strategies.items():
                if not spec.enabled or "carry" in name:
                    continue
                clash = sorted(carry_symbols.intersection(self.resolve_symbols(spec)))
                if clash:
                    problems.append(
                        f"{name} also trades carry symbol(s) {', '.join(clash[:4])}"
                        f"{' ...' if len(clash) > 4 else ''} - a directional exit "
                        f"there would leave the perp leg naked"
                    )
        # The legs are additive ceilings, so the account level has to be at least
        # their sum - otherwise the split silently becomes "first leg wins, second
        # leg gets nothing", which is exactly the failure the legs exist to stop.
        legs = self.risk.legs
        if legs:
            leg_gross = sum(leg.max_gross_exposure_pct for leg in legs.values())
            if leg_gross > self.risk.account.max_gross_exposure_pct + 1e-9:
                problems.append(
                    f"legs gross ({leg_gross:.1f}%) exceeds the account ceiling "
                    f"({self.risk.account.max_gross_exposure_pct:.1f}%)"
                )
            leg_heat = sum(leg.max_heat_pct for leg in legs.values())
            if leg_heat > self.risk.account.max_heat_pct + 1e-9:
                problems.append(
                    f"legs heat ({leg_heat:.2f}%) exceeds the account ceiling "
                    f"({self.risk.account.max_heat_pct:.2f}%)"
                )
        if self.risk.circuit.daily_loss_flatten_pct < self.risk.circuit.daily_loss_stop_open_pct:
            problems.append("daily_loss_flatten_pct must be >= daily_loss_stop_open_pct")
        if self.risk.circuit.max_drawdown_kill_pct <= self.risk.circuit.daily_loss_flatten_pct:
            problems.append("max_drawdown_kill_pct must exceed daily_loss_flatten_pct")

        allowed = self.allowed_symbols
        for symbol in self.traded_symbols:
            if symbol not in allowed:
                problems.append(f"universe symbol {symbol} is not in the whitelist")

        known_timeframes = set(self.universe.all_timeframes)
        for name, spec in self.strategies.items():
            for entry in spec.symbols or []:
                if entry.startswith("tier:"):
                    tier = entry.split(":", 1)[1]
                    if tier not in self.universe.symbols_by_tier:
                        problems.append(f"strategy {name} references unknown tier {tier!r}")
                    continue
                if entry not in allowed:
                    problems.append(f"strategy {name} trades {entry} which is not whitelisted")
            if spec.timeframe and spec.timeframe not in known_timeframes:
                problems.append(
                    f"strategy {name} timeframe {spec.timeframe} is not in universe.timeframes "
                    f"{sorted(known_timeframes)} - add it so the feed keeps it warm"
                )
            confirm = spec.params.get("confirm_timeframe")
            if confirm and confirm not in known_timeframes:
                problems.append(
                    f"strategy {name} confirm_timeframe {confirm} is not in universe.timeframes"
                )
            needed = max(
                int(spec.params.get("ema_slow", 0) or 0),
                int(spec.params.get("entry_lookback", 0) or 0),
                int(spec.params.get("ema_period", 0) or 0),
                int(spec.params.get("sma_period", 0) or 0),
                int(spec.params.get("confirm_ema", 0) or 0),
            ) + 60
            if needed > self.universe.bar_limit:
                problems.append(
                    f"strategy {name} needs about {needed} bars per timeframe but "
                    f"universe.bar_limit is {self.universe.bar_limit}"
                )

        for name, venue in self.venues.items():
            # DELIBERATE POLICY CHANGE, 2026-10-07 (was: reject everything non-spot
            # with "this skeleton is spot-only"). Relaxed for the CARRY leg only -
            # see README 26: carry = long spot + short perp, so it needs a swap
            # venue, and CcxtVenue now handles swap order params (tdMode/posSide),
            # swap positions (fetch_positions) and contract sizing. The spot path
            # is untouched: every swap branch is guarded by market_type/`contract`
            # checks and 158 tests (including the spot ones) stay green.
            # 2026-10-10 (1.1): "option" is accepted too. OKX options are
            # coin-margined contracts: CcxtVenue already takes the contract qty
            # branch, and the swap order params were narrowed so an option order
            # carries tdMode but neither posSide nor reduceOnly (both are refused on
            # a one-way option book). The measured facts behind the decision live in
            # scripts/options_probe.py; nothing trades this venue yet, the strategy
            # still waits on the IV/RV gate.
            # Anything else is still rejected - the guard narrows, it does not
            # disappear.
            if venue.market_type not in ("spot", "swap", "option"):
                problems.append(
                    f"venue {name} market_type={venue.market_type}; "
                    f"only 'spot', 'swap' and 'option' are supported"
                )
            if name != "paper":
                missing = [
                    label
                    for label, env in (
                        ("api_key_env", venue.api_key_env),
                        ("secret_env", venue.secret_env),
                    )
                    if not env
                ]
                if missing:
                    problems.append(f"venue {name} missing {', '.join(missing)}")

        # ---- on-chain wiring -------------------------------------------------
        onchain = self.onchain
        if onchain.sizing.equity_usd <= 0:
            problems.append("onchain.sizing.equity_usd must be > 0")
        allowed_signers = {"refuse", "mock", "local-keypair", "kms", "hardware"}
        if onchain.execution.signer not in allowed_signers:
            problems.append(
                f"onchain.execution.signer {onchain.execution.signer!r} must be one of "
                f"{sorted(allowed_signers)}"
            )
        if not onchain.execution.dry_run and onchain.execution.signer == "refuse":
            problems.append(
                "onchain.execution.dry_run=false with signer=refuse: nothing could ever be "
                "signed - use signer=mock to rehearse, or configure a real signer"
            )
        if onchain.execution.allow_broadcast and onchain.execution.signer in ("refuse", "mock"):
            problems.append(
                f"onchain.execution.allow_broadcast=true with signer={onchain.execution.signer}: "
                "a mock signature can never be broadcast"
            )
        if not onchain.data.offline:
            for name, source in onchain.data.sources.items():
                if source.enabled and not source.endpoint and not source.trending_endpoint:
                    problems.append(f"onchain.data.sources.{name} has no endpoint")
        if not onchain.jupiter.quote_endpoint or not onchain.jupiter.swap_endpoint:
            problems.append("onchain.jupiter endpoints must be set")

        return problems
