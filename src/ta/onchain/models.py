"""On-chain domain models: token snapshots, quotes, swap plans, execution results."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from typing import Any

# Fields that decide whether a token is safe to touch. If one of these cannot be
# resolved from any data source, the screener rejects the token (fail-closed).
# Fields a provider must resolve for the screener to make a decision. Anything in
# here that is unknown => reject. price_impact and taxes are intentionally NOT
# required: price impact only exists once you have a quote, and Solana tax data is
# not reliably exposed. They are still checked whenever a source reports them.
SAFETY_CRITICAL_FIELDS: tuple[str, ...] = (
    "liquidity_usd",
    "lp_locked",
    "holder_count",
    "top10_holder_pct",
    "age_minutes",
)

# Merging rules for fields where sources can disagree.
DANGER_FLAGS = ("is_honeypot", "freezable", "mintable")  # true wins
CONSERVATIVE_BOOLS = ("lp_locked",)  # false wins (if anyone says unlocked, treat as unlocked)


@dataclass(slots=True)
class TokenSnapshot:
    """Everything the safety filter needs, merged from as many sources as possible.

    `provided` records which fields were actually resolved. `None` means the object
    was constructed by hand and every field is trusted as given.
    """

    mint: str
    symbol: str = ""
    name: str = ""
    chain: str = "solana"
    age_minutes: int = 0
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    holder_count: int = 0
    top10_holder_pct: float = 0.0
    lp_locked: bool = False
    lp_locked_pct: float = 0.0
    buy_tax_pct: float = 0.0
    sell_tax_pct: float = 0.0
    is_honeypot: bool = False
    safety_scan_ok: bool = False
    price_impact_pct: float = 0.0
    price_usd: float = 0.0
    # Which pool `price_usd` came from. Two snapshots of the same mint priced from
    # DIFFERENT pools are not a time series, and differencing them manufactures
    # fake jumps (see the study's pool-switch guard).
    pair_address: str = ""
    dev_wallet: str = ""
    dev_hold_pct: float = 0.0
    freezable: bool = False
    mintable: bool = False
    verified_source: bool = False
    provided: list[str] | None = None
    sources: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    # ---- provenance --------------------------------------------------------
    def has(self, key: str) -> bool:
        return self.provided is None or key in self.provided

    def missing(self, keys: tuple[str, ...] = SAFETY_CRITICAL_FIELDS) -> list[str]:
        if self.provided is None:
            return []
        return [k for k in keys if k not in self.provided]

    def source_of(self, key: str) -> str:
        return self.sources.get(key, "unknown")

    # ---- serialisation -----------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenSnapshot":
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        snapshot = cls(**payload)
        if snapshot.provided is None:
            snapshot.provided = sorted(k for k in payload if k not in ("provided", "sources", "notes"))
        return snapshot

    def dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        return (
            f"{self.symbol or self.mint[:8]} liq={self.liquidity_usd:,.0f} "
            f"holders={self.holder_count} top10={self.top10_holder_pct:.1f}% "
            f"lp_locked={self.lp_locked} age={self.age_minutes}m"
        )


@dataclass(slots=True)
class ScreenResult:
    mint: str
    symbol: str
    passed: bool
    score: float
    reasons: list[str]


@dataclass(slots=True)
class Quote:
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    price_impact_pct: float
    slippage_bps: int
    other_amount_threshold: int = 0
    route_labels: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_price(self) -> float:
        return self.out_amount / self.in_amount if self.in_amount else 0.0

    def dict(self) -> dict[str, Any]:
        return {
            "input_mint": self.input_mint,
            "output_mint": self.output_mint,
            "in_amount": self.in_amount,
            "out_amount": self.out_amount,
            "price_impact_pct": round(self.price_impact_pct, 6),
            "slippage_bps": self.slippage_bps,
            "route": ",".join(self.route_labels),
        }


@dataclass(slots=True)
class SwapPlan:
    transaction_b64: str
    last_valid_block_height: int = 0
    priority_fee_lamports: int = 0
    simulated_placeholder: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def transaction_bytes(self) -> bytes:
        return base64.b64decode(self.transaction_b64)

    def dict(self) -> dict[str, Any]:
        return {
            "tx_bytes": len(self.transaction_b64) * 3 // 4,
            "last_valid_block_height": self.last_valid_block_height,
            "priority_fee_lamports": self.priority_fee_lamports,
            "placeholder": self.simulated_placeholder,
        }


@dataclass(slots=True)
class SimulationResult:
    ok: bool
    err: Any = None
    units_consumed: int = 0
    logs: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "err": self.err,
            "units_consumed": self.units_consumed,
            "logs_tail": self.logs[-3:],
        }


@dataclass(slots=True)
class ExecutionResult:
    status: str  # blocked | rejected | planned | simulated | signed | broadcast | failed
    stage: str
    mint: str
    reason: str = ""
    symbol: str = ""
    notional_usd: float = 0.0
    amount_lamports: int = 0
    screen: dict[str, Any] | None = None
    quote: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    simulation: dict[str, Any] | None = None
    signature: str = ""
    signer: str = ""
    simulated: bool = True
    broadcast_allowed: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("planned", "simulated", "signed", "broadcast")

    def dict(self) -> dict[str, Any]:
        return asdict(self)

    def lines(self) -> list[str]:
        rows = [
            f"status            {self.status.upper()}  (stage: {self.stage})",
            f"token             {self.symbol or self.mint}",
            f"notional          ${self.notional_usd:,.2f}",
            f"signer            {self.signer or '-'}   simulated={self.simulated}"
            f"   broadcast_allowed={self.broadcast_allowed}",
        ]
        if self.reason:
            rows.append(f"reason            {self.reason}")
        if self.quote:
            rows.append(
                f"quote             impact={self.quote.get('price_impact_pct')}%  "
                f"route={self.quote.get('route') or '-'}"
            )
        if self.plan:
            rows.append(
                f"swap tx           {self.plan.get('tx_bytes')} bytes  "
                f"priority_fee={self.plan.get('priority_fee_lamports')} lamports"
            )
        if self.simulation:
            rows.append(f"simulation        ok={self.simulation.get('ok')} "
                        f"units={self.simulation.get('units_consumed')}")
        if self.signature:
            rows.append(f"signature         {self.signature}")
        return rows
