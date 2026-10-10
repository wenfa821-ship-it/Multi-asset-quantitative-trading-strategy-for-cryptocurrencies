"""Per-venue baseline so reconciliation compares like with like.

Without a baseline, cash reconciliation is impossible: the local ledger only
knows our own cash *flow*, while the venue reports an absolute balance that may
already contain deposits, manual trades, or (on OKX demo) a pre-funded bundle
like 1 BTC / 100 OKB / 1 ETH / 5000 USDT. Freezing the starting state once per
venue turns "cash mismatch" into a meaningful signal:

    venue_cash_now - baseline.cash  ==  local_book.cash   (our net flow)

Anything else is a real discrepancy worth investigating (deposit, withdrawal,
manual trade, or a bug).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import AccountState
from .util import iso


@dataclass(slots=True)
class VenueBaseline:
    venue: str
    cash: float
    # Cash without open-position unrealised PnL (OKX `cashBal`). Preferred for the
    # reconciliation comparison; 0.0 on baselines written before this existed, in
    # which case the caller falls back to `cash`.
    cash_bal: float = 0.0
    positions: dict[str, float] = field(default_factory=dict)
    created_at: str = ""
    note: str = ""

    def dict(self) -> dict[str, Any]:
        return asdict(self)


def load(path: str | Path) -> VenueBaseline | None:
    target = Path(path)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return VenueBaseline(
        venue=str(payload.get("venue", "")),
        cash=float(payload.get("cash", 0.0)),
        cash_bal=float(payload.get("cash_bal", 0.0) or 0.0),
        positions={k: float(v) for k, v in (payload.get("positions") or {}).items()},
        created_at=str(payload.get("created_at", "")),
        note=str(payload.get("note", "")),
    )


def save(path: str | Path, baseline: VenueBaseline) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(baseline.dict(), indent=2), encoding="utf-8")
    return target


def from_account(venue: str, account: AccountState, note: str = "") -> VenueBaseline:
    return VenueBaseline(
        venue=venue,
        cash=account.cash,
        cash_bal=account.cash_bal or 0.0,
        positions={s: round(p.qty, 12) for s, p in account.positions.items()},
        created_at=iso(),
        note=note or "created automatically on first successful account read",
    )


def load_or_create(path: str | Path, venue: str, account: AccountState) -> tuple[VenueBaseline, bool]:
    """Return (baseline, created). Creates it on first use."""
    existing = load(path)
    if existing is not None:
        return existing, False
    baseline = from_account(venue, account)
    save(path, baseline)
    return baseline, True
