"""Token safety filter. It only ever blocks - it never produces a buy signal.

Fail-closed rule: if a safety-critical field could not be resolved from any data
source, the token is rejected rather than assumed safe.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import OnchainConfig
from .models import SAFETY_CRITICAL_FIELDS, ScreenResult, TokenSnapshot


class TokenScreener:
    """Weighted 0-100 quality score plus hard rejections. Rejections always win."""

    def __init__(self, config: OnchainConfig) -> None:
        self.config = config

    def screen(self, token: TokenSnapshot) -> ScreenResult:
        cfg = self.config
        safety = cfg.safety
        reasons: list[str] = []

        def reject(reason: str) -> None:
            reasons.append(f"REJECT {reason}")

        # ---- hard rules ----------------------------------------------------
        if token.mint in cfg.blacklist.mints:
            reject("mint blacklisted")
        if token.dev_wallet and token.dev_wallet in cfg.blacklist.dev_wallets:
            reject("dev wallet blacklisted")

        # ---- completeness (fail closed) ------------------------------------
        if safety.require_known_safety_fields:
            required = tuple(safety.required_fields) or SAFETY_CRITICAL_FIELDS
            missing = token.missing(required)
            if missing:
                reject(f"unknown safety fields: {', '.join(missing)}")
            if not token.has("is_honeypot") and not token.safety_scan_ok:
                reject("honeypot status unknown (no scanner result)")

        # ---- honeypot / taxes ----------------------------------------------
        if safety.reject_honeypot and token.has("is_honeypot") and token.is_honeypot:
            reject("honeypot")
        if token.has("sell_tax_pct") and token.sell_tax_pct > safety.max_sell_tax_pct:
            reject(f"sell tax {token.sell_tax_pct:.2f}%")
        if token.has("buy_tax_pct") and token.buy_tax_pct > safety.max_sell_tax_pct:
            reject(f"buy tax {token.buy_tax_pct:.2f}%")

        # ---- liquidity lock -------------------------------------------------
        if safety.require_lp_locked:
            if token.has("lp_locked_pct"):
                if token.lp_locked_pct < safety.min_lp_locked_pct:
                    reject(f"lp locked {token.lp_locked_pct:.1f}% < {safety.min_lp_locked_pct:.0f}%")
            elif token.has("lp_locked") and not token.lp_locked:
                reject("liquidity not locked")

        # ---- distribution ---------------------------------------------------
        if token.has("top10_holder_pct") and token.top10_holder_pct > safety.max_top10_holder_pct:
            reject(f"top10 holders {token.top10_holder_pct:.2f}%")
        if token.has("holder_count") and token.holder_count < safety.min_holder_count:
            reject(f"holders {token.holder_count} < {safety.min_holder_count}")

        # ---- market quality -------------------------------------------------
        if token.has("liquidity_usd") and token.liquidity_usd < safety.min_liquidity_usd:
            reject(f"liquidity {token.liquidity_usd:,.0f} < {safety.min_liquidity_usd:,.0f} USD")
        if token.has("age_minutes") and token.age_minutes < safety.min_age_minutes:
            reject(f"age {token.age_minutes}m < {safety.min_age_minutes}m")
        if token.has("price_impact_pct") and token.price_impact_pct > safety.max_price_impact_pct:
            reject(f"price impact {token.price_impact_pct:.2f}%")
        if token.has("liquidity_usd") and token.has("volume_24h_usd") and token.liquidity_usd > 0:
            ratio = token.volume_24h_usd / token.liquidity_usd
            if ratio < safety.min_volume_liquidity_ratio:
                reject(f"volume/liquidity {ratio:.3f} < {safety.min_volume_liquidity_ratio}")

        # ---- quality score (never overrides a rejection) --------------------
        score = 0.0
        if token.has("liquidity_usd"):
            score += min(25.0, 25.0 * token.liquidity_usd / max(safety.min_liquidity_usd * 4, 1))
        if token.has("holder_count"):
            score += min(20.0, 20.0 * token.holder_count / max(safety.min_holder_count * 5, 1))
        if token.has("top10_holder_pct"):
            score += max(0.0, 20.0 * (1 - token.top10_holder_pct / 100.0))
        if token.has("lp_locked_pct"):
            score += 15.0 * min(1.0, token.lp_locked_pct / 100.0)
        elif token.has("lp_locked"):
            score += 15.0 if token.lp_locked else 0.0
        if token.has("sell_tax_pct"):
            score += max(0.0, 10.0 * (1 - token.sell_tax_pct / 10.0))
        elif token.has("safety_scan_ok"):
            score += 8.0 if token.safety_scan_ok else 0.0
        if token.has("liquidity_usd") and token.has("volume_24h_usd") and token.liquidity_usd > 0:
            score += min(10.0, 10.0 * token.volume_24h_usd / token.liquidity_usd)
        if token.verified_source:
            score = min(100.0, score + 5.0)
        if token.freezable or token.mintable:
            score -= 8.0
        score = round(max(0.0, min(100.0, score)), 2)

        passed = not any(r.startswith("REJECT") for r in reasons)
        if passed and score < 60:
            passed = False
            reasons.append(f"REJECT low quality score {score}")
        if passed:
            reasons.append(f"OK score {score}")
        return ScreenResult(token.mint, token.symbol, passed, score, reasons)

    def screen_many(self, tokens: list[TokenSnapshot]) -> list[ScreenResult]:
        results = [self.screen(t) for t in tokens]
        return sorted(results, key=lambda r: (r.passed, r.score), reverse=True)


def load_snapshots(path: str | Path) -> list[TokenSnapshot]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("tokens", [])
    return [TokenSnapshot.from_dict(item) for item in payload]

