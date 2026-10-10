"""Token discovery: produce the candidate list the watcher scans.

Sources are pluggable and each candidate carries its provenance, because "why is
this token on my list" is itself a hypothesis you will later test.

What is used, and why:
  * DexScreener `token-profiles/latest` + `token-boosts/top` - PUBLIC endpoints
    that surface tokens someone is actively promoting. That is exactly the
    population a new-token strategy trades, and it needs no credentials.
  * DexScreener `latest/dex/tokens/{mint}` - enrichment: real liquidity, price,
    24h volume, so the list can be pre-filtered by our own cost-wall rule.
  * GMGN - already wired as a provider in `config/onchain.toml`, but its
    endpoints are internal and need a browser session cookie (GMGN_COOKIE).
    Absent a cookie it is simply skipped; nothing breaks.

Deliberately NOT done: hammering any site. Requests are cached, retried with
backoff, and rate-limited by the shared HttpClient - a discovery list is not
worth getting an IP banned, and their terms decide what is acceptable anyway.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..config import OnchainConfig
from ..util import now_ms
from .http import HttpClient

PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
BOOSTS_URL = "https://api.dexscreener.com/token-boosts/top/v1"
TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
# Second, independent source. The promotion feeds only ever carry ~12-17 live
# tokens at a time (measured 2026-10-01: --limit 200 with a $1k floor still
# returned 17), while a bucketed forward-return study needs 60-80 distinct
# tokens. GeckoTerminal's trending pools are public, keyless and paginated.
TRENDING_URL = "https://api.geckoterminal.com/api/v2/networks/{chain}/trending_pools?page={page}"


@dataclass(slots=True)
class Candidate:
    mint: str
    chain: str = "solana"
    symbol: str = ""
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    price_usd: float = 0.0
    sources: list[str] = field(default_factory=list)
    boost_amount: float = 0.0
    # RECENT activity, not 24h totals. A promoted token whose pool stopped
    # trading an hour ago has a static price: watching it yields snapshots with
    # identical prices and a forward-return study that measures nothing.
    volume_m5_usd: float = 0.0
    volume_h1_usd: float = 0.0
    txns_m5: int = 0
    txns_h1: int = 0

    def dict(self) -> dict[str, Any]:
        return {
            "mint": self.mint,
            "chain": self.chain,
            "symbol": self.symbol,
            "liquidity_usd": round(self.liquidity_usd, 2),
            "volume_24h_usd": round(self.volume_24h_usd, 2),
            "price_usd": self.price_usd,
            "sources": self.sources,
            "boost_amount": self.boost_amount,
            "volume_m5_usd": round(self.volume_m5_usd, 2),
            "volume_h1_usd": round(self.volume_h1_usd, 2),
            "txns_m5": self.txns_m5,
            "txns_h1": self.txns_h1,
        }

    @property
    def turnover(self) -> float:
        return self.volume_24h_usd / self.liquidity_usd if self.liquidity_usd else 0.0

    @property
    def recent_turnover(self) -> float:
        """Hourly volume / liquidity - what is actually trading right now."""
        return self.volume_h1_usd / self.liquidity_usd if self.liquidity_usd else 0.0

    @property
    def is_active(self) -> bool:
        return self.txns_m5 > 0 or self.txns_h1 > 0


def _promoted(client: HttpClient, url: str, source: str, chain: str) -> list[Candidate]:
    """DexScreener promotion feeds: who is being pushed right now."""
    payload = client.get_json(url)
    if not isinstance(payload, list):
        return []
    out: list[Candidate] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        if str(row.get("chainId", "")).lower() != chain:
            continue
        mint = row.get("tokenAddress") or ""
        if not mint:
            continue
        out.append(
            Candidate(
                mint=str(mint),
                chain=chain,
                sources=[source],
                boost_amount=float(row.get("totalAmount") or 0.0),
            )
        )
    return out


def _enrich(client: HttpClient, candidate: Candidate) -> Candidate:
    """Fill in real market data; keep the candidate even if enrichment fails."""
    try:
        data = client.get_json(TOKEN_URL.format(mint=candidate.mint))
    except Exception:  # noqa: BLE001
        return candidate
    pairs = [p for p in (data.get("pairs") or []) if isinstance(p, dict)]
    if not pairs:
        return candidate
    best = max(pairs, key=lambda p: ((p.get("liquidity") or {}).get("usd") or 0.0))
    candidate.liquidity_usd = float((best.get("liquidity") or {}).get("usd") or 0.0)
    candidate.volume_24h_usd = float((best.get("volume") or {}).get("h24") or 0.0)
    candidate.price_usd = float(best.get("priceUsd") or 0.0)
    candidate.symbol = str(((best.get("baseToken") or {}).get("symbol")) or "")
    volume = best.get("volume") or {}
    txns = best.get("txns") or {}
    candidate.volume_m5_usd = float(volume.get("m5") or 0.0)
    candidate.volume_h1_usd = float(volume.get("h1") or 0.0)
    m5 = txns.get("m5") or {}
    h1 = txns.get("h1") or {}
    candidate.txns_m5 = int((m5.get("buys") or 0) + (m5.get("sells") or 0))
    candidate.txns_h1 = int((h1.get("buys") or 0) + (h1.get("sells") or 0))
    return candidate


def _trending(client: HttpClient, chain: str, *, pages: int = 5) -> list[Candidate]:
    """GeckoTerminal trending pools: the trading universe, not the promoted one.

    Whatever is trending is, by construction, being traded right now - which is
    the property the event study needs and the promotion feeds cannot supply in
    enough quantity.
    """
    out: list[Candidate] = []
    for page in range(1, pages + 1):
        try:
            payload = client.get_json(TRENDING_URL.format(chain=chain, page=page))
        except Exception:  # noqa: BLE001 - one dead page must not kill discovery
            break
        rows = (payload or {}).get("data") or []
        if not rows:
            break
        for row in rows:
            attrs = row.get("attributes") or {}
            base = ((row.get("relationships") or {}).get("base_token") or {}).get("data") or {}
            token_id = str(base.get("id") or "")
            mint = token_id.split("_", 1)[1] if "_" in token_id else ""
            if not mint:
                continue
            name = str(attrs.get("name") or "")
            symbol = name.split("/")[0].strip() if "/" in name else name
            volume = attrs.get("volume_usd") or {}
            txns = attrs.get("transactions") or {}

            def _txn(window: str) -> int:
                entry = txns.get(window) or {}
                return int(entry.get("buys") or 0) + int(entry.get("sells") or 0)

            out.append(
                Candidate(
                    mint=mint,
                    chain=chain,
                    symbol=symbol,
                    liquidity_usd=float(attrs.get("reserve_in_usd") or 0.0),
                    volume_24h_usd=float(volume.get("h24") or 0.0),
                    price_usd=float(attrs.get("base_token_price_usd") or 0.0),
                    sources=["geckoterminal:trending"],
                    volume_m5_usd=float(volume.get("m5") or 0.0),
                    volume_h1_usd=float(volume.get("h1") or 0.0),
                    txns_m5=_txn("m5"),
                    txns_h1=_txn("h1"),
                )
            )
    return out


def discover(
    config: OnchainConfig,
    client: HttpClient,
    *,
    limit: int = 25,
    chain: str = "solana",
    min_liquidity_usd: float | None = None,
    enrich: bool = True,
    require_active: bool = True,
    min_txns_h1: int = 10,
    trending_pages: int = 5,
) -> list[Candidate]:
    """Merge the promotion feeds, enrich, pre-filter by our own cost rule, rank."""
    floor = (
        config.safety.min_liquidity_usd if min_liquidity_usd is None else min_liquidity_usd
    )
    merged: dict[str, Candidate] = {}
    for url, source in ((PROFILES_URL, "dexscreener:profiles"), (BOOSTS_URL, "dexscreener:boosts")):
        try:
            for candidate in _promoted(client, url, source, chain):
                existing = merged.get(candidate.mint)
                if existing is None:
                    merged[candidate.mint] = candidate
                else:
                    existing.sources = sorted(set(existing.sources + candidate.sources))
                    existing.boost_amount = max(existing.boost_amount, candidate.boost_amount)
        except Exception:  # noqa: BLE001 - one dead feed must not kill discovery
            continue

    # Trending pools carry price/liquidity/activity already, so they skip the
    # per-token enrichment call that the promotion feeds need.
    if trending_pages > 0:
        try:
            for candidate in _trending(client, chain, pages=trending_pages):
                existing = merged.get(candidate.mint)
                if existing is None:
                    merged[candidate.mint] = candidate
                else:
                    # Prefer the trending feed's fresher numbers for the fields
                    # the activity filter and the study depend on.
                    existing.sources = sorted(set(existing.sources + candidate.sources))
                    for field_name in (
                        "liquidity_usd",
                        "volume_24h_usd",
                        "volume_m5_usd",
                        "volume_h1_usd",
                        "txns_m5",
                        "txns_h1",
                    ):
                        if getattr(candidate, field_name):
                            setattr(existing, field_name, getattr(candidate, field_name))
                    if candidate.price_usd > 0:
                        existing.price_usd = candidate.price_usd
        except Exception:  # noqa: BLE001 - a dead source must not kill discovery
            pass

    candidates = list(merged.values())
    if enrich:
        # Only the promotion feeds need enriching; skip tokens that already have
        # price and liquidity so a 100-token universe does not cost 100 API calls.
        candidates = [
            c if (c.price_usd > 0 and c.liquidity_usd > 0) else _enrich(client, c)
            for c in candidates
        ]

    # Our own rule decides what is even watchable: too thin to trade = not a
    # candidate, no matter how loudly it is being promoted.
    keep = [c for c in candidates if c.liquidity_usd >= floor and c.price_usd > 0]
    if require_active:
        # Ranking by 24h turnover ranks yesterday's pump. A token that has not
        # traded in the last hour cannot produce a measurable forward return,
        # so it is not a usable research subject.
        keep = [c for c in keep if c.txns_h1 >= min_txns_h1]
    keep.sort(key=lambda c: (c.recent_turnover, c.turnover, c.boost_amount), reverse=True)
    return keep[:limit]


def save_watchlist(path: str | Path, candidates: Iterable[Candidate], *, note: str = "") -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": now_ms(),
        "note": note or "generated by ta scan discover; feeds scan watch",
        "candidates": [c.dict() for c in candidates],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_watchlist(path: str | Path) -> list[Candidate]:
    target = Path(path)
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    rows = payload.get("candidates") if isinstance(payload, dict) else payload
    out: list[Candidate] = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("mint"):
            out.append(
                Candidate(
                    mint=str(row["mint"]),
                    chain=str(row.get("chain", "solana")),
                    symbol=str(row.get("symbol", "")),
                    liquidity_usd=float(row.get("liquidity_usd") or 0.0),
                    volume_24h_usd=float(row.get("volume_24h_usd") or 0.0),
                    price_usd=float(row.get("price_usd") or 0.0),
                    sources=list(row.get("sources") or []),
                    boost_amount=float(row.get("boost_amount") or 0.0),
                    volume_m5_usd=float(row.get("volume_m5_usd") or 0.0),
                    volume_h1_usd=float(row.get("volume_h1_usd") or 0.0),
                    txns_m5=int(row.get("txns_m5") or 0),
                    txns_h1=int(row.get("txns_h1") or 0),
                )
            )
    return out
