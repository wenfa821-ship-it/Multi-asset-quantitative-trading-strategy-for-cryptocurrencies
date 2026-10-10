"""Data providers that normalise external token data into provider-agnostic fields.

Design rules:
  * a provider returns ONLY the fields it actually resolved - never a default;
  * missing safety data makes a token fail the filter, it never makes it pass;
  * payload shapes are read defensively via dotted paths, because these are
    third-party endpoints (GMGN in particular is an undocumented internal API).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ..config import DataSourceConfig, OnchainConfig
from ..util import now_ms
from .http import HttpClient, HttpError, JsonFetcher
from .models import TokenSnapshot

TOKEN_FIELDS: frozenset[str] = frozenset(TokenSnapshot.__dataclass_fields__) - {  # type: ignore[attr-defined]
    "provided",
    "sources",
    "notes",
    "mint",
}

MINT_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


class ProviderError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------
# coercion helpers (defensive by design)
# --------------------------------------------------------------------------------------


def dig(data: Any, *paths: str, default: Any = None) -> Any:
    """Return the first dotted path that resolves to a non-None value."""
    for path in paths:
        current = data
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                ok = False
                break
        if ok and current is not None:
            return current
    return default


def as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("%", "").replace("$", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def as_pct(value: Any) -> float | None:
    """For fields explicitly named `*_rate` / `*_ratio`: accept both 0.28 and 28.

    Never use this on a field named `percent` - a genuine 0.8% would become 80%.
    """
    number = as_float(value)
    if number is None:
        return None
    if 0.0 <= abs(number) <= 1.0:
        return number * 100.0
    return number


def as_int(value: Any) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "y"):
            return True
        if lowered in ("0", "false", "no", "n", ""):
            return False
    return None


def as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def extract_mint(text: str) -> str | None:
    matches = MINT_RE.findall(text or "")
    return matches[-1] if matches else None


# --------------------------------------------------------------------------------------
# provider base
# --------------------------------------------------------------------------------------


class Provider(ABC):
    name: str = "provider"

    def __init__(
        self,
        source: DataSourceConfig,
        client: HttpClient,
        now_fn: Callable[[], int] = now_ms,
    ) -> None:
        self.source = source
        self.client = client
        self.now_fn = now_fn

    @property
    def headers(self) -> dict[str, str]:
        headers = dict(self.source.headers)
        return headers

    def _age_minutes(self, created_ms: Any) -> int | None:
        created = as_float(created_ms)
        if not created:
            return None
        if created > 1e11:  # ms
            delta_ms = self.now_fn() - created
        else:  # seconds
            delta_ms = self.now_fn() - created * 1000.0
        return int(max(0.0, delta_ms / 60_000.0))

    @abstractmethod
    def collect(self, mint: str) -> dict[str, Any]:
        """Return only the fields this provider could actually resolve."""


class DexScreenerProvider(Provider):
    """Public, documented: liquidity / volume / price / pair age / txns."""

    name = "dexscreener"

    def collect(self, mint: str) -> dict[str, Any]:
        data = self.client.get_json(self.source.endpoint.format(mint=mint), headers=self.headers)
        pairs = data.get("pairs") or []
        if not isinstance(pairs, list) or not pairs:
            raise ProviderError("no pairs returned")
        pair = max(pairs, key=lambda p: as_float(dig(p, "liquidity.usd")) or 0.0)

        out: dict[str, Any] = {}
        liquidity = as_float(dig(pair, "liquidity.usd"))
        if liquidity is not None:
            out["liquidity_usd"] = liquidity
        volume = as_float(dig(pair, "volume.h24"))
        if volume is not None:
            out["volume_24h_usd"] = volume
        price = as_float(dig(pair, "priceUsd"))
        if price:
            out["price_usd"] = price
        # Record WHICH pool the price came from. The provider picks the deepest
        # pair on every call, and when a mint has several pools the "deepest"
        # flips - which makes the stored price jump by tens of percent between two
        # scans with nothing happening in the market. Measured 2026-10-02: a
        # forward return of +88,183% on a token whose own series never moved more
        # than 15%. Without this field that artefact is indistinguishable from a
        # real moonshot, and it was carrying the entire positive mean.
        pair_address = as_str(dig(pair, "pairAddress"))
        if pair_address:
            out["pair_address"] = pair_address
        age = self._age_minutes(dig(pair, "pairCreatedAt"))
        if age is not None:
            out["age_minutes"] = age
        symbol = as_str(dig(pair, "baseToken.symbol"))
        if symbol:
            out["symbol"] = symbol
        name = as_str(dig(pair, "baseToken.name"))
        if name:
            out["name"] = name
        buys = as_int(dig(pair, "txns.h24.buys"))
        sells = as_int(dig(pair, "txns.h24.sells"))
        if buys is not None or sells is not None:
            out.setdefault("notes", [])
        # DexScreener exposes no tax / honeypot / holder data: we do not invent it.
        return out


class GoPlusProvider(Provider):
    """Public, documented: mint/freeze authority, holder distribution, LP holders."""

    name = "goplus"

    @staticmethod
    def _flag(entry: dict[str, Any], key: str) -> bool | None:
        value = entry.get(key)
        if isinstance(value, dict):
            return as_bool(value.get("status", value.get("value")))
        return as_bool(value)

    def collect(self, mint: str) -> dict[str, Any]:
        data = self.client.get_json(self.source.endpoint.format(mint=mint), headers=self.headers)
        result = data.get("result")
        entry: dict[str, Any] | None = None
        if isinstance(result, dict):
            candidate = result.get(mint)
            if isinstance(candidate, dict):
                entry = candidate
            elif result:
                first = next(iter(result.values()))
                entry = first if isinstance(first, dict) else None
        if entry is None:
            raise ProviderError("unexpected GoPlus payload shape")

        out: dict[str, Any] = {}
        freezable = self._flag(entry, "freezable")
        if freezable is not None:
            out["freezable"] = freezable
        mintable = self._flag(entry, "mintable")
        if mintable is not None:
            out["mintable"] = mintable

        holders = entry.get("holders")
        if isinstance(holders, list) and holders:
            out["holder_count"] = len(holders)
            total_supply = as_float(entry.get("total_supply"))
            top10 = 0.0
            resolved = 0
            for holder in holders[:10]:
                # GoPlus names this field `percent`, so it is already a percentage.
                pct = as_float(holder.get("percent") if isinstance(holder, dict) else None)
                if pct is None and total_supply and isinstance(holder, dict):
                    balance = as_float(holder.get("balance"))
                    if balance is not None and total_supply > 0:
                        pct = 100.0 * balance / total_supply
                if pct is not None:
                    top10 += pct
                    resolved += 1
            if resolved:
                out["top10_holder_pct"] = round(top10, 4)

        lp_holders = entry.get("lp_holders")
        if isinstance(lp_holders, list) and lp_holders:
            locked = False
            for holder in lp_holders:
                if not isinstance(holder, dict):
                    continue
                tag = str(holder.get("tag") or "").lower()
                if tag in ("burned", "locked") or as_bool(holder.get("is_locked")):
                    locked = True
                    break
            out["lp_locked"] = locked

        creator = entry.get("creator")
        if isinstance(creator, list) and creator:
            address = as_str(dig(creator[0], "address")) if isinstance(creator[0], dict) else as_str(creator[0])
            if address:
                out["dev_wallet"] = address
            hold = as_float(dig(creator[0], "pct", "percent")) if isinstance(creator[0], dict) else None
            if hold is not None:
                out["dev_hold_pct"] = hold
        elif isinstance(creator, str) and creator:
            out["dev_wallet"] = creator
        return out


class RugCheckProvider(Provider):
    """Public community scanner: risk list, LP locked %, holder concentration."""

    name = "rugcheck"

    def collect(self, mint: str) -> dict[str, Any]:
        data = self.client.get_json(self.source.endpoint.format(mint=mint), headers=self.headers)
        out: dict[str, Any] = {}

        risks = data.get("risks")
        if isinstance(risks, list):
            names = " ".join(
                str(dig(r, "name", "description") or "").lower() for r in risks if isinstance(r, dict)
            )
            dangerous = [
                r
                for r in risks
                if isinstance(r, dict)
                and str(dig(r, "level", "severity") or "").lower() in ("danger", "critical")
            ]
            if "honeypot" in names or "cannot sell" in names or "sell blocked" in names:
                out["is_honeypot"] = True
            # the scan ran and nothing critical came back
            out["safety_scan_ok"] = not dangerous

        lp_pct = as_float(dig(data, "lpLockedPct", "markets.0.lp.lpLockedPct", "token.lpLockedPct"))
        if lp_pct is not None:
            out["lp_locked_pct"] = round(lp_pct, 4)
            out["lp_locked"] = lp_pct >= 90.0

        holders = as_int(dig(data, "totalHolders", "token.totalHolders"))
        if holders is not None:
            out["holder_count"] = holders

        top_holders = data.get("topHolders")
        if isinstance(top_holders, list) and top_holders:
            total = 0.0
            resolved = 0
            for holder in top_holders[:10]:
                pct = as_float(dig(holder, "pct", "percent"))  # already a percentage
                if pct is not None:
                    total += pct
                    resolved += 1
            if resolved:
                out["top10_holder_pct"] = round(total, 4)

        score = as_float(dig(data, "score_normalised", "score"))
        if score is not None:
            out.setdefault("notes", [])
        return out


class GMGNProvider(Provider):
    """GMGN internal endpoints: token stats + smart-money context.

    There is no public GMGN API contract. The payload shape below is handled
    defensively across the shapes the web app has used; if GMGN changes it, fix
    the endpoint/field mapping in config/onchain.toml rather than patching logic.
    """

    name = "gmgn"

    def _payload(self, data: dict[str, Any]) -> dict[str, Any]:
        for path in ("data.token", "data", "token", "result"):
            candidate = dig(data, path)
            if isinstance(candidate, dict) and candidate:
                return candidate
        return data if isinstance(data, dict) else {}

    def collect(self, mint: str) -> dict[str, Any]:
        data = self.client.get_json(self.source.endpoint.format(mint=mint), headers=self.headers)
        token = self._payload(data)
        if not token:
            raise ProviderError("empty GMGN payload")

        out: dict[str, Any] = {}
        symbol = as_str(dig(token, "symbol", "token_symbol", "base_token.symbol"))
        if symbol:
            out["symbol"] = symbol
        price = as_float(dig(token, "price", "price_usd", "usd_price"))
        if price:
            out["price_usd"] = price
        liquidity = as_float(dig(token, "liquidity", "liquidity_usd", "pool_liquidity"))
        if liquidity is not None:
            out["liquidity_usd"] = liquidity
        volume = as_float(dig(token, "volume_24h", "volume", "volume_24h_usd"))
        if volume is not None:
            out["volume_24h_usd"] = volume
        holders = as_int(dig(token, "holder_count", "holders", "holder"))
        if holders is not None:
            out["holder_count"] = holders
        top10 = as_pct(dig(token, "top_10_holder_rate", "top10_holder_rate", "top_10_holders_rate"))
        if top10 is not None:
            out["top10_holder_pct"] = round(top10, 4)
        dev_hold = as_pct(dig(token, "dev_team_hold_rate", "dev_hold_rate", "creator_hold_rate"))
        if dev_hold is not None:
            out["dev_hold_pct"] = round(dev_hold, 4)
        lp_locked = as_bool(dig(token, "is_lp_locked", "lp_locked", "lp_burned"))
        if lp_locked is not None:
            out["lp_locked"] = lp_locked
        honeypot = as_bool(dig(token, "is_honeypot", "honeypot"))
        if honeypot is not None:
            out["is_honeypot"] = honeypot
        age = self._age_minutes(dig(token, "open_timestamp", "created_at", "pair_created_at", "launch_time"))
        if age is not None:
            out["age_minutes"] = age
        dev = as_str(dig(token, "creator", "dev_address", "owner"))
        if dev:
            out["dev_wallet"] = dev

        smart = {
            "smart_degen": as_int(dig(token, "smart_degen_count")),
            "renowned": as_int(dig(token, "renowned_count")),
            "sniper": as_int(dig(token, "sniper_count")),
            "bundler_rate": as_pct(dig(token, "bundler_rate")),
            "rug_ratio": as_float(dig(token, "rug_ratio")),
            "insider_rate": as_pct(dig(token, "rat_trader_amount_rate")),
        }
        signals = [f"{k}={v}" for k, v in smart.items() if v is not None]
        if signals:
            out["notes"] = ["gmgn: " + ", ".join(signals)]
        return out


class FixtureProvider(Provider):
    """Reads a local JSON file: {"<mint>": {fields...}} - used by tests and offline runs."""

    name = "fixture"

    def __init__(self, path: str | Path, name: str = "fixture") -> None:
        self.path = Path(path)
        self.name = name
        self.source = DataSourceConfig()
        self.client = None  # type: ignore[assignment]
        self.now_fn = now_ms

    @property
    def headers(self) -> dict[str, str]:
        return {}

    def collect(self, mint: str) -> dict[str, Any]:
        if not self.path.exists():
            raise ProviderError(f"fixture not found: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        entry = data.get(mint) if isinstance(data, dict) else None
        if entry is None:
            raise ProviderError(f"no fixture entry for {mint}")
        return dict(entry)


# --------------------------------------------------------------------------------------
# fixture-backed HTTP for fully offline runs
# --------------------------------------------------------------------------------------


HOST_PROVIDER = {
    "api.dexscreener.com": "dexscreener",
    "api.gopluslabs.io": "goplus",
    "api.rugcheck.xyz": "rugcheck",
    "gmgn.ai": "gmgn",
}


class FixtureFetcher:
    """Maps a request URL to recorded JSON, so the whole flow runs offline."""

    def __init__(self, fixture_dir: str | Path) -> None:
        self.fixture_dir = Path(fixture_dir)

    def _fixture_name(self, method: str, url: str, body: dict[str, Any] | None = None) -> str:
        host = urlparse(url).netloc
        if method == "POST" and isinstance(body, dict) and "jsonrpc" in body:
            if body.get("method") == "sendTransaction":
                raise HttpError("offline rehearsal: broadcasting is refused")
            return "rpc.json"
        if host.endswith("jup.ag"):
            return "jupiter_swap.json" if method == "POST" else "jupiter_quote.json"
        return f"{HOST_PROVIDER.get(host, 'unknown')}.json"

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout: float,
    ) -> dict[str, Any]:
        path = self.fixture_dir / self._fixture_name(method, url, body)
        if not path.exists():
            raise HttpError(f"no fixture for {method} {url} (looked for {path})")
        data = json.loads(path.read_text(encoding="utf-8"))
        mint = extract_mint(url) or (extract_mint(json.dumps(body or {})) or None)
        if isinstance(data, dict) and "by_mint" in data and isinstance(data["by_mint"], dict):
            entry = data["by_mint"].get(mint or "", {})
            if not entry:
                raise HttpError(f"fixture {path.name} has no entry for {mint}")
            return dict(entry)
        if isinstance(data, dict) and mint and mint in data and isinstance(data[mint], dict):
            return dict(data[mint])
        return data


# --------------------------------------------------------------------------------------
# composite service
# --------------------------------------------------------------------------------------


FIELD_PRIORITY: dict[str, tuple[str, ...]] = {
    "liquidity_usd": ("dexscreener", "gmgn", "rugcheck"),
    "volume_24h_usd": ("dexscreener", "gmgn"),
    "price_usd": ("dexscreener", "gmgn"),
    "age_minutes": ("dexscreener", "gmgn"),
    "symbol": ("dexscreener", "gmgn"),
    "name": ("dexscreener",),
    "holder_count": ("rugcheck", "goplus", "gmgn"),
    "top10_holder_pct": ("rugcheck", "goplus", "gmgn"),
    "lp_locked_pct": ("rugcheck",),
    "lp_locked": ("rugcheck", "goplus", "gmgn"),
    "sell_tax_pct": ("goplus", "gmgn"),
    "buy_tax_pct": ("goplus", "gmgn"),
    "is_honeypot": ("rugcheck", "goplus", "gmgn"),
    "safety_scan_ok": ("rugcheck",),
    "dev_wallet": ("goplus", "gmgn"),
    "dev_hold_pct": ("gmgn", "goplus"),
    "freezable": ("goplus",),
    "mintable": ("goplus",),
}

DANGER_FLAGS = ("is_honeypot", "freezable", "mintable")  # true wins
CONSERVATIVE_BOOLS = ("lp_locked",)  # false wins


class CompositeTokenService:
    """Merges every provider into one TokenSnapshot, with per-field provenance."""

    def __init__(
        self,
        config: OnchainConfig,
        client: HttpClient,
        providers: list[Provider],
        provider_order: list[str] | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.providers = providers
        self.order = provider_order or [p.name for p in providers]

    @classmethod
    def from_config(
        cls,
        config: OnchainConfig,
        project_dir: Path,
        *,
        client: HttpClient | None = None,
        offline: bool | None = None,
        fixture_dir: str | Path | None = None,
        fetcher: JsonFetcher | None = None,
    ) -> "CompositeTokenService":
        data_cfg = config.data
        use_offline = data_cfg.offline if offline is None else offline
        fixtures = Path(fixture_dir or (project_dir / data_cfg.fixture_dir))
        if use_offline and fetcher is None:
            fetcher = FixtureFetcher(fixtures)

        if client is None:
            client = HttpClient(
                timeout=data_cfg.timeout_seconds,
                retries=data_cfg.retries,
                # an offline run must not read or write the cache: recorded payloads
                # must never leak into a later live run
                cache_dir=None if use_offline else project_dir / data_cfg.cache_dir,
                offline=bool(use_offline),
                fetcher=fetcher,
            )

        provider_classes: dict[str, type[Provider]] = {
            "dexscreener": DexScreenerProvider,
            "goplus": GoPlusProvider,
            "rugcheck": RugCheckProvider,
            "gmgn": GMGNProvider,
        }
        providers: list[Provider] = []
        for name, source in data_cfg.sources.items():
            if not source.enabled:
                continue
            klass = provider_classes.get(name)
            if klass is None:
                continue
            providers.append(klass(source, client))
        if not providers:
            providers = [FixtureProvider(fixtures / "token.json")]
        return cls(config, client, providers)

    # ---- merging -----------------------------------------------------------
    def _rank(self, field: str, provider: str) -> int:
        priority = FIELD_PRIORITY.get(field)
        if priority and provider in priority:
            return priority.index(provider)
        if provider in self.order:
            return len(FIELD_PRIORITY.get(field, ())) + self.order.index(provider)
        return 999

    def snapshot(self, mint: str, chain: str = "solana") -> TokenSnapshot:
        collected: dict[str, tuple[Any, str]] = {}
        notes: list[str] = []
        failures: list[str] = []

        for provider in self.providers:
            try:
                fields = provider.collect(mint)
            except Exception as exc:  # noqa: BLE001 - one dead source must not kill the flow
                failures.append(f"{provider.name}: {exc}")
                continue
            for field, value in fields.items():
                if field == "notes":
                    notes.extend(value if isinstance(value, list) else [str(value)])
                    continue
                if field not in TOKEN_FIELDS or value is None:
                    continue
                existing = collected.get(field)
                if existing is None:
                    collected[field] = (value, provider.name)
                    continue
                if field in DANGER_FLAGS:
                    collected[field] = (bool(existing[0]) or bool(value), existing[1] if existing[0] else provider.name)
                elif field in CONSERVATIVE_BOOLS:
                    collected[field] = (bool(existing[0]) and bool(value), existing[1] if not existing[0] else provider.name)
                elif self._rank(field, provider.name) < self._rank(field, existing[1]):
                    collected[field] = (value, provider.name)

        if failures:
            notes.append("source failures: " + "; ".join(failures))
        snapshot = TokenSnapshot(
            mint=mint,
            chain=chain,
            provided=sorted(collected),
            sources={field: source for field, (_, source) in collected.items()},
            notes=notes,
        )
        for field, (value, _) in collected.items():
            setattr(snapshot, field, value)
        return snapshot

    def trending(self, limit: int = 20) -> list[TokenSnapshot]:
        """GMGN-style discovery list. Returns snapshots, never buy recommendations."""
        source = self.config.data.sources.get("gmgn")
        if not source or not source.enabled or not source.trending_endpoint:
            raise ProviderError("gmgn.trending_endpoint is not configured")
        headers = dict(source.headers)
        data = self.client.get_json(source.trending_endpoint, headers=headers)
        rows = dig(data, "data.rank", "data.tokens", "data", "rank", default=[])
        if not isinstance(rows, list):
            raise ProviderError("unexpected trending payload")
        out: list[TokenSnapshot] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            mint = as_str(dig(row, "address", "token_address", "mint"))
            if not mint:
                continue
            snapshot = TokenSnapshot(mint=mint, chain="solana", symbol=as_str(dig(row, "symbol")) or "")
            for field, path in (
                ("liquidity_usd", "liquidity"),
                ("volume_24h_usd", "volume"),
                ("price_usd", "price"),
                ("holder_count", "holder_count"),
            ):
                value = as_float(dig(row, path))
                if value is not None:
                    snapshot.provided = (snapshot.provided or []) + [field]
                    setattr(snapshot, field, value)
            out.append(snapshot)
        return out
