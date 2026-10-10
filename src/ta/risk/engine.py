"""The risk gate. Strategies propose; this module decides. It is never bypassed.

Two entry points:
    pre_trade(...)  -> approve / shrink / reject a single order
    on_tick(...)    -> circuit breakers evaluated on every loop
"""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

from ..config import OnchainConfig, RiskConfig
from ..models import AccountState, OrderRequest
from ..onchain import TokenSnapshot


def leg_of(
    symbol: str,
    members: dict[str, set[str]] | None = None,
    prefixes: dict[str, str] | None = None,
) -> str:
    """Which sleeve of the book a symbol belongs to (README sections 20/28/34).

    Four sleeves since 2026-10-07: spot + derivatives (50% between them),
    TradFi (30%), options (20%). Three of them are structural:

      * ``SOL/USDT:USDT``      -> derivatives (settlement suffix)
      * ``BTC-USD-261008-80000-C`` -> options (strike + right suffix)
      * ``SOL/USDT``           -> spot

    TradFi is NOT structural: a tokenised equity is ``XAAPL/USDT``, shape-identical
    to any spot pair. It is matched by the explicit member list first, then by the
    configured prefix - and XRP is pinned to the spot leg in config so the prefix
    cannot swallow it.
    """
    if members:
        for leg, symbols in members.items():
            if symbol in symbols:
                return leg
    if symbol.count("-") >= 3 and symbol[-1].upper() in ("C", "P"):
        return "options"
    if ":" in symbol:
        return "derivatives"
    if prefixes:
        base = symbol.split("/")[0]
        for leg, prefix in prefixes.items():
            if prefix and base.startswith(prefix):
                return leg
    return "spot"


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    reason: str = ""
    qty: float = 0.0
    adjusted: bool = False

    @property
    def rejected(self) -> bool:
        return not self.approved


class RiskEngine:
    def __init__(
        self,
        limits: RiskConfig,
        kill_switch_path: str | Path = "KILL_SWITCH",
        onchain: OnchainConfig | None = None,
        write_kill_file: bool = True,
        time_fn=time.time,
        symbol_tiers: dict[str, str] | None = None,
        default_tier: str | None = None,
    ) -> None:
        self.limits = limits
        self.onchain = onchain
        self.kill_switch_path = Path(kill_switch_path)
        self.write_kill_file = write_kill_file
        self._time = time_fn
        self.symbol_tiers = dict(symbol_tiers or {})
        self._default_tier = default_tier or limits.default_tier

        self.killed = False
        self.kill_reason = ""
        self.paused_until = 0.0
        self.pause_reason = ""
        self._order_ts: deque[float] = deque(maxlen=1000)
        self.blocks: Counter[str] = Counter()
        # per-symbol cooldown after a protective exit, and daily entry budgets
        self._cooldowns: dict[str, float] = {}
        self._day = ""
        self._entries_today = 0
        self._entries_by_symbol: Counter[str] = Counter()
        self._entries_by_leg: Counter[str] = Counter()
        # leg name -> explicit symbol set (TradFi cannot be inferred from shape)
        self._leg_members: dict[str, set[str]] = {
            name: set(leg.symbols) for name, leg in limits.legs.items() if leg.symbols
        }
        self._leg_prefixes: dict[str, str] = {
            name: leg.symbol_prefix
            for name, leg in limits.legs.items()
            if leg.symbol_prefix
        }
        self._loss_streak: Counter[str] = Counter()

    # ---- state -------------------------------------------------------------
    @property
    def paused(self) -> bool:
        return self.paused_until > self._time()

    def pause(self, minutes: float, reason: str) -> None:
        self.paused_until = max(self.paused_until, self._time() + minutes * 60.0)
        self.pause_reason = reason

    def trigger_kill(self, reason: str) -> None:
        if self.killed:
            return
        self.killed = True
        self.kill_reason = reason
        if self.write_kill_file:
            try:
                self.kill_switch_path.write_text(
                    f"killed_at={self._time():.0f}\nreason={reason}\n", encoding="utf-8"
                )
            except OSError:
                pass

    def clear_kill(self) -> None:
        self.killed = False
        self.kill_reason = ""
        try:
            self.kill_switch_path.unlink()
        except FileNotFoundError:
            pass

    # ---- tiers -------------------------------------------------------------
    def tier_of(self, symbol: str) -> str:
        return self.symbol_tiers.get(symbol) or self._default_tier

    def limits_for(self, symbol: str):
        return self.limits.limits_for(self.symbol_tiers.get(symbol))

    def risk_per_trade_pct(self, symbol: str) -> float:
        return self.limits_for(symbol).risk_per_trade_pct

    def stop_atr_multiple(self, symbol: str) -> float:
        return self.limits_for(symbol).stop_atr_multiple

    def take_profit_r_multiple(self, symbol: str) -> float:
        return self.limits_for(symbol).take_profit_r_multiple

    def atr_target_pct(self, symbol: str) -> float:
        return self.limits_for(symbol).atr_target_pct

    def symbol_allowed(self, symbol: str) -> bool:
        if self.symbol_tiers:
            return symbol in self.symbol_tiers
        whitelist = self.limits.symbols.whitelist
        if not whitelist:
            # fail closed: a missing allowlist must not mean "everything goes".
            # Use ["*"] if permissive behaviour is genuinely intended.
            return False
        return symbol in whitelist or "*" in whitelist

    # ---- cooldowns / daily budgets -----------------------------------------
    def note_stop_out(self, symbol: str, pnl: float | None = None) -> None:
        """Start a per-symbol cooldown, and count a losing streak for that symbol.

        Streaks are tracked per symbol on purpose: with a wide universe, one coin
        going bad must not freeze the whole book.
        """
        minutes = self.limits_for(symbol).cooldown_after_stop_minutes
        if pnl is None or pnl < 0:
            self._loss_streak[symbol] += 1
        else:
            self._loss_streak[symbol] = 0
        streak = self._loss_streak[symbol]
        threshold = self.limits.circuit.consecutive_losses_pause
        if threshold and streak >= threshold:
            minutes = max(minutes, self.limits.circuit.streak_cooldown_minutes)
        if minutes > 0:
            self._cooldowns[symbol] = self._time() + minutes * 60.0

    def note_win(self, symbol: str) -> None:
        self._loss_streak[symbol] = 0

    def cooldown_remaining_minutes(self, symbol: str) -> float:
        until = self._cooldowns.get(symbol, 0.0)
        return max(0.0, (until - self._time()) / 60.0)

    def _roll_day(self) -> None:
        from ..util import day_key

        today = day_key(int(self._time() * 1000))
        if today != self._day:
            self._day = today
            self._entries_today = 0
            self._entries_by_symbol.clear()
            self._entries_by_leg.clear()

    def portfolio_heat_pct(
        self,
        account: AccountState,
        marks: dict[str, float],
        stops: dict[str, float] | None,
        leg: str | None = None,
    ) -> float:
        """Open risk as a percentage of equity: sum(|mark - stop| x qty) / equity.

        This is the one number that captures "how much can I lose if everything
        goes wrong at once", and it is the right brake once frequency goes up.
        """
        equity = account.effective_risk_equity
        if equity <= 0 or not stops:
            return 0.0
        heat = 0.0
        for symbol, position in account.positions.items():
            if position.qty <= 0:
                continue
            if leg is not None and leg_of(symbol, self._leg_members, self._leg_prefixes) != leg:
                continue
            stop = stops.get(symbol)
            if not stop:
                continue
            price = marks.get(symbol) or position.avg_price
            heat += abs(price - stop) * position.qty
        return 100.0 * heat / equity

    # ---- helpers -----------------------------------------------------------
    @staticmethod
    def _position_notional(account: AccountState, mark_of: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for symbol, position in account.positions.items():
            price = mark_of.get(symbol) or position.avg_price
            out[symbol] = position.qty * price
        return out

    def _cluster_notional(self, symbol: str, notionals: dict[str, float]) -> float:
        bucket = self.limits.clusters.bucket_of(symbol)
        if bucket is None:
            return 0.0
        members = self.limits.clusters.buckets.get(bucket, [])
        return sum(notionals.get(m, 0.0) for m in members)

    # ---- pre-trade ---------------------------------------------------------
    def pre_trade(
        self,
        request: OrderRequest,
        account: AccountState,
        mark: float,
        marks: dict[str, float] | None = None,
        *,
        min_notional: float | None = None,
        token: TokenSnapshot | None = None,
        is_onchain: bool = False,
        stops: dict[str, float] | None = None,
        spread_bps: float | None = None,
    ) -> RiskDecision:
        limits = self.limits
        per_trade = limits.per_trade
        tier = self.limits_for(request.symbol)
        marks = dict(marks or {})
        if mark > 0:
            marks.setdefault(request.symbol, mark)

        qty = float(request.qty)
        if qty <= 0 or mark <= 0:
            return self._reject("invalid_qty_or_price")

        reduce_only = bool(request.reduce_only or request.side == "sell")

        if reduce_only:
            # THE rule: this gate may only ever block *adding* risk, never
            # *reducing* it. Shrinking a close to fit an entry cap leaves dust
            # and half-open positions, and blocking it would make a kill-switch
            # flatten impossible. Venue minimums remain the venue's business.
            note = "reduce_only: uncapped exit"
            if self.killed:
                note = "reduce_only exit while killed (flatten)"
            elif self.kill_switch_path.exists() and not self.killed:
                self.trigger_kill("kill switch file present on disk")
            return RiskDecision(approved=True, qty=qty, reason=note)

        if self.killed:
            return self._reject(f"kill_switch: {self.kill_reason or 'active'}")
        if self.kill_switch_path.exists():
            self.trigger_kill("kill switch file present on disk")
            return self._reject("kill_switch: file present")

        if not reduce_only:
            self._roll_day()
            if self.paused:
                wait = max(0.0, (self.paused_until - self._time()) / 60.0)
                return self._reject(f"paused: {self.pause_reason} ({wait:.0f}m left)")
            cooldown = self.cooldown_remaining_minutes(request.symbol)
            if cooldown > 0:
                return self._reject(
                    f"symbol_cooldown: {request.symbol} for {cooldown:.0f}m after a stop-out"
                )
            if spread_bps is not None and spread_bps > tier.max_spread_bps:
                return self._reject(f"spread: {spread_bps:.1f}bps > {tier.max_spread_bps:.1f}bps")
            daily_cap = min(tier.max_trades_per_day, limits.account.max_trades_per_day)
            if self._entries_today >= daily_cap:
                return self._reject(
                    f"daily_trade_cap: {self._entries_today} entries already today"
                )
            symbol_cap = limits.symbols.max_trades_per_symbol_per_day
            used = self._entries_by_symbol.get(request.symbol, 0)
            if symbol_cap and used >= symbol_cap:
                return self._reject(f"symbol_daily_cap: {request.symbol} {used}/{symbol_cap} today")
            cutoff = self._time() - 60.0
            recent = [t for t in self._order_ts if t >= cutoff]
            if len(recent) >= min(tier.max_orders_per_minute, per_trade.max_orders_per_minute):
                return self._reject("rate_limit: too many orders in the last minute")

        if not is_onchain:
            if request.symbol in limits.symbols.blacklist:
                return self._reject("symbol_blacklisted")
            if not self.symbol_allowed(request.symbol):
                if self.symbol_tiers:
                    reason = "symbol_not_in_any_tier"
                elif not self.limits.symbols.whitelist:
                    reason = "no_allowlist_configured: pass symbol_tiers or a whitelist"
                else:
                    reason = "symbol_not_whitelisted"
                return self._reject(reason)

        if request.type == "limit" and request.price:
            deviation = 100.0 * abs(request.price - mark) / mark
            if deviation > per_trade.max_price_deviation_pct:
                return self._reject(
                    f"price_deviation: {deviation:.2f}% > {per_trade.max_price_deviation_pct:.2f}%"
                )

        # size against the capital the bot controls, not the whole account
        equity = account.effective_risk_equity or (account.cash or 1.0)
        notional = qty * mark
        floor = max(min_notional or 0.0, per_trade.min_order_notional_usd)
        # A minimum-notional floor must never trap an open position: exits always
        # pass this gate (the venue is the final judge on dust).
        if notional < floor and not reduce_only:
            return self._reject(f"below_min_notional: {notional:.2f} < {floor:.2f}")

        # The absolute dollar cap is optional: 0 means "not set", so the relative
        # caps govern. Reading 0 as a literal cap would silently block every order
        # (`min(0, ...)`), which is the opposite of what "remove the cap" means.
        caps = [
            equity * per_trade.max_order_notional_pct / 100.0,
            equity * tier.max_order_notional_pct / 100.0,
        ]
        if per_trade.max_order_notional_usd > 0:
            caps.append(per_trade.max_order_notional_usd)
        cap = min(caps)

        if not reduce_only:
            notionals = self._position_notional(account, marks)
            gross = sum(notionals.values())
            if limits.account.count_unmanaged_exposure:
                gross += getattr(account, "unmanaged_notional", 0.0)

            current = notionals.get(request.symbol, 0.0)
            room_symbol = equity * tier.max_position_pct / 100.0 - current
            cap = min(cap, room_symbol)

            room_gross = equity * limits.account.max_gross_exposure_pct / 100.0 - gross
            cap = min(cap, room_gross)

            # Leg ceilings (README sections 20/28): the 50/50 split has to be
            # enforced per leg, not by one shared number, or whichever leg fills
            # first consumes the budget the other one was promised.
            leg = leg_of(request.symbol, self._leg_members, self._leg_prefixes)
            leg_limits = limits.legs.get(leg)
            if leg_limits is not None:
                leg_gross = sum(
                    n for s, n in notionals.items()
                    if leg_of(s, self._leg_members, self._leg_prefixes) == leg
                )
                leg_budget = equity * leg_limits.max_gross_exposure_pct / 100.0
                if leg_limits.max_gross_exposure_pct:
                    if leg_gross >= leg_budget:
                        return self._reject(
                            f"leg_room: {leg} gross {leg_gross:.0f} >= budget {leg_budget:.0f}"
                        )
                    cap = min(cap, leg_budget - leg_gross)
                if leg_limits.max_open_positions:
                    leg_open = sum(
                        1 for s in account.positions
                        if leg_of(s, self._leg_members, self._leg_prefixes) == leg
                    )
                    if (
                        request.symbol not in account.positions
                        and leg_open >= leg_limits.max_open_positions
                    ):
                        return self._reject(
                            f"leg_positions: {leg} {leg_open} >= {leg_limits.max_open_positions}"
                        )
                if leg_limits.max_trades_per_day:
                    used_leg = self._entries_by_leg.get(leg, 0)
                    if used_leg >= leg_limits.max_trades_per_day:
                        return self._reject(
                            f"leg_daily_cap: {leg} {used_leg}/{leg_limits.max_trades_per_day} today"
                        )
                if leg_limits.max_heat_pct:
                    leg_heat = self.portfolio_heat_pct(account, marks, stops, leg=leg)
                    if request.stop_price:
                        leg_risk = abs(mark - float(request.stop_price)) * qty
                    else:
                        leg_risk = equity * tier.risk_per_trade_pct / 100.0
                    leg_after = leg_heat + 100.0 * leg_risk / equity
                    if leg_after > leg_limits.max_heat_pct:
                        return self._reject(
                            f"leg_heat: {leg} {leg_heat:.2f}% + "
                            f"{100.0 * leg_risk / equity:.2f}% > {leg_limits.max_heat_pct:.2f}%"
                        )

            if self.limits.clusters.bucket_of(request.symbol) is not None:
                cluster_now = self._cluster_notional(request.symbol, notionals)
                room_cluster = equity * limits.clusters.max_cluster_exposure_pct / 100.0 - cluster_now
                cap = min(cap, room_cluster)

            # The tier's cap is about ITS OWN positions. Comparing it against the
            # whole book made every tier cap a cap on the entire account: measured
            # 2026-10-10, with three carry pairs open (6 positions) a carry entry
            # came back "6 >= 5 (tier carry)", and any TradFi entry would have been
            # refused by its own cap of 2 - a sleeve that could never start, which
            # is exactly what we saw (0 fills on a live, funded leg).
            tier_name = self.tier_of(request.symbol)
            tier_positions = [s for s in account.positions if self.tier_of(s) == tier_name]
            max_positions = min(tier.max_positions, limits.account.max_open_positions)
            if request.symbol not in account.positions and len(tier_positions) >= max_positions:
                return self._reject(
                    f"max_open_positions: {len(tier_positions)} >= {max_positions} "
                    f"(tier {tier_name})"
                )

            # portfolio heat: risk already on the table plus this order
            heat_now = self.portfolio_heat_pct(account, marks, stops)
            if request.stop_price:
                projected_risk = abs(mark - float(request.stop_price)) * qty
            else:
                projected_risk = equity * tier.risk_per_trade_pct / 100.0
            heat_after = heat_now + 100.0 * projected_risk / equity
            if heat_after > limits.account.max_heat_pct:
                return self._reject(
                    f"heat: {heat_now:.2f}% + {100.0 * projected_risk / equity:.2f}% > "
                    f"{limits.account.max_heat_pct:.2f}%"
                )

            if limits.account.cash_buffer_basis == "equity":
                min_cash = equity * limits.account.min_cash_buffer_pct / 100.0
            else:
                min_cash = account.cash * limits.account.min_cash_buffer_pct / 100.0
            cap = min(cap, max(0.0, account.cash - min_cash))

            if limits.account.max_leverage_pct <= 100.0:  # spot: never spend more than cash
                cap = min(cap, account.cash)

        if is_onchain:
            if self.onchain is None:
                return self._reject("onchain_rules_missing")
            token_decision = self._check_token(token)
            if token_decision is not None:
                return token_decision
            cap = min(cap, equity * self.onchain.sizing.max_position_pct_per_token / 100.0)
            active_tokens = sum(1 for s, p in account.positions.items() if p.qty > 0)
            if request.symbol not in account.positions and active_tokens >= self.onchain.sizing.max_concurrent_tokens:
                return self._reject(f"max_concurrent_tokens: {active_tokens}")

        if cap <= 0:
            return self._reject("no_room: position/exposure/cash limits reached")

        if notional > cap:
            new_qty = cap / mark
            # epsilon: an exact-boundary order (cap == floor) must not fall through
            if new_qty * mark < floor * (1.0 - 1e-9):
                return self._reject(f"room_too_small: {cap:.2f} < min notional {floor:.2f}")
            decision = RiskDecision(
                approved=True,
                reason=f"shrunk {notional:.2f} -> {new_qty * mark:.2f}",
                qty=new_qty,
                adjusted=True,
            )
        else:
            decision = RiskDecision(approved=True, qty=qty, reason="ok")

        if not reduce_only:
            self._order_ts.append(self._time())
            self._entries_today += 1
            self._entries_by_symbol[request.symbol] += 1
            self._entries_by_leg[
                leg_of(request.symbol, self._leg_members, self._leg_prefixes)
            ] += 1
        return decision

    def _check_token(self, token: TokenSnapshot | None) -> RiskDecision | None:
        assert self.onchain is not None
        safety = self.onchain.safety
        if token is None:
            return self._reject("onchain: missing token snapshot")
        if token.mint in self.onchain.blacklist.mints:
            return self._reject("onchain: mint blacklisted")
        if token.dev_wallet and token.dev_wallet in self.onchain.blacklist.dev_wallets:
            return self._reject("onchain: dev wallet blacklisted")
        if safety.reject_honeypot and token.is_honeypot:
            return self._reject("onchain: honeypot")
        if safety.require_lp_locked and not token.lp_locked:
            return self._reject("onchain: LP not locked")
        if token.sell_tax_pct > safety.max_sell_tax_pct:
            return self._reject(f"onchain: sell tax {token.sell_tax_pct:.1f}% too high")
        if token.top10_holder_pct > safety.max_top10_holder_pct:
            return self._reject(f"onchain: top10 {token.top10_holder_pct:.1f}% too concentrated")
        if token.liquidity_usd < safety.min_liquidity_usd:
            return self._reject(f"onchain: liquidity {token.liquidity_usd:,.0f} too thin")
        if token.holder_count < safety.min_holder_count:
            return self._reject(f"onchain: holders {token.holder_count} too few")
        if token.age_minutes < safety.min_age_minutes:
            return self._reject(f"onchain: age {token.age_minutes}m below minimum")
        if token.price_impact_pct > safety.max_price_impact_pct:
            return self._reject(f"onchain: price impact {token.price_impact_pct:.2f}% too high")
        return None

    def _reject(self, reason: str) -> RiskDecision:
        key = reason.split(":")[0]
        self.blocks[key] += 1
        return RiskDecision(approved=False, reason=reason, qty=0.0)

    # ---- periodic circuit breakers -----------------------------------------
    def on_tick(self, account: AccountState) -> list[str]:
        """Return a list of actions the runner must execute: kill | flatten | pause."""
        actions: list[str] = []
        circuit = self.limits.circuit

        if self.kill_switch_path.exists() and not self.killed:
            self.trigger_kill("kill switch file present on disk")
            actions.append("kill")
            return actions

        if account.drawdown_pct >= circuit.max_drawdown_kill_pct:
            self.trigger_kill(
                f"max drawdown {account.drawdown_pct:.2f}% >= {circuit.max_drawdown_kill_pct:.2f}%"
            )
            actions.append("kill")
            return actions

        daily_pnl = account.daily_pnl_pct
        if daily_pnl <= -circuit.daily_loss_flatten_pct:
            actions.append("flatten")
        elif daily_pnl <= -circuit.daily_loss_stop_open_pct and not self.paused:
            self.pause(circuit.cooldown_minutes_after_stop, f"daily loss {daily_pnl:.2f}%")
            actions.append("pause")

        # losing streaks are handled per symbol (see note_stop_out): a wide
        # universe must not be frozen by one bad coin

        return actions

    def stats(self) -> dict:
        return {
            "killed": self.killed,
            "kill_reason": self.kill_reason,
            "paused_until": self.paused_until,
            "paused": self.paused,
            "blocks": dict(self.blocks),
            "entries_today": self._entries_today,
            "entries_by_symbol": dict(self._entries_by_symbol),
            "entries_by_leg": dict(self._entries_by_leg),
            "cooldowns": {
                symbol: round(self.cooldown_remaining_minutes(symbol), 1)
                for symbol in self._cooldowns
                if self.cooldown_remaining_minutes(symbol) > 0
            },
            "loss_streaks": {s: n for s, n in self._loss_streak.items() if n},
            "tiers": {s: self.tier_of(s) for s in self.symbol_tiers},
        }
