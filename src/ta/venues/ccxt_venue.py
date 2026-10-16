"""OKX Demo Trading / Binance Testnet adapter built on ccxt.

Install with:  pip install "trading-agent[live]"   (adds ccxt)

Both venues are used in *sandbox* mode:
  * OKX    -> ccxt adds the `x-simulated-trading: 1` header. Create the API key
              inside the Demo Trading environment and pass the API passphrase.
  * Binance-> ccxt points the client at testnet.binance.vision (spot) or
              testnet.binancefuture.com when exchange_id="binanceusdm".

Nothing in this file writes to the live network unless you point the config at a
non-sandbox venue. The runner additionally refuses to trade without
--enable-trading.
"""

from __future__ import annotations

import os
import time
from typing import Any

from ..models import AccountState, Bar, Fill, Order, OrderRequest, Position
from ..util import now_ms
from .base import Venue, VenueError

_STATUS_MAP = {
    "open": "open",
    "closed": "filled",
    "canceled": "canceled",
    "cancelled": "canceled",
    "expired": "rejected",
    "rejected": "rejected",
}


class CcxtVenue(Venue):
    simulated = False

    def __init__(
        self,
        exchange_id: str,
        api_key: str,
        secret: str,
        password: str | None = None,
        sandbox: bool = True,
        market_type: str = "spot",
        name: str | None = None,
        base_ccy: str = "USDT",
        fee_bps: float = 8.0,
        slippage_bps: float = 5.0,
    ) -> None:
        try:
            import ccxt  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise VenueError(
                "ccxt is not installed. Run: pip install \"trading-agent[live]\" "
                "(or: pip install ccxt) to use okx_demo / binance_testnet."
            ) from exc

        self.name = name or exchange_id
        self.base_ccy = base_ccy
        self.market_type = market_type
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

        params: dict[str, Any] = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": market_type},
        }
        if password:
            params["password"] = password

        try:
            klass = getattr(ccxt, exchange_id)
        except AttributeError as exc:
            raise VenueError(f"unknown ccxt exchange id {exchange_id!r}") from exc

        self.ex: Any = klass(params)
        if sandbox:
            self.ex.set_sandbox_mode(True)

        # ccxt builds its requests session with trust_env=False, so the standard
        # HTTPS_PROXY / HTTP_PROXY variables are ignored unless wired explicitly.
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        if proxy:
            self.ex.proxies = {"http": proxy, "https": proxy}

        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._filled_tracker: dict[str, float] = {}
        # Cumulative fee already accounted for, per client_order_id. Mirrors
        # _filled_tracker so partial fills do not double-count the fee.
        self._fee_tracker: dict[str, float] = {}
        # client_order_id -> (symbol, submitted_at). OKX spot market orders often
        # return `status=open, filled=0` and fill asynchronously; once such an
        # order leaves the open-orders list we would never look at it again and
        # the fill would be lost forever (the position then looks unmanaged: no
        # stop, outside every limit, invisible to flatten).
        self._pending_orders: dict[str, tuple[str, float]] = {}
        self.order_poll_ttl_seconds = 1800.0
        # Spot orders on OKX carry an explicit `tdMode`, and the legal value depends
        # on the ACCOUNT level rather than on the market: acctLv<=1 (Simple) accepts
        # only "cash", while acctLv>=2 (single-currency / multi-currency / portfolio
        # margin) REFUSES "cash" with sCode 51000 and needs "cross". This account was
        # moved to 跨币种保证金模式 on 2026-10-06, and every spot entry has been
        # rejected with 51000 since - the 14:12 change that hard-coded "cash" wrote
        # the very value being refused, so it changed nothing. connect() now reads
        # the real level off the venue; "cash" remains the fallback so a Simple-mode
        # account keeps behaving exactly as before.
        self.spot_td_mode = "cash"
        # Whether the level was actually READ (as opposed to falling back to the
        # default above). A failed probe used to be invisible; three rejections on
        # 2026-10-10 came from exactly that, so the state is now explicit.
        self.td_mode_resolved = False
        self.account_level = 0
        self.last_td_mode_error = ""
        self.last_connect_error = ""

    def resolve_spot_td_mode(self) -> str:
        """Read `acctLv` and set the spot `tdMode` it requires.

        Returns the mode in force. `td_mode_resolved` says whether the probe
        SUCCEEDED: without that bit, "the account is Simple mode" and "the probe
        failed" both look like `cash`, and the second one rejects every spot order
        the process will ever send (see `_create_order`).
        """
        try:
            config = self.ex.privateGetAccountConfig()
            level = int(((config.get("data") or [{}])[0]).get("acctLv") or 0)
        except Exception as exc:  # noqa: BLE001 - never block startup on a probe
            self.td_mode_resolved = False
            self.last_td_mode_error = f"{type(exc).__name__}: {exc}"
            return str(getattr(self, "spot_td_mode", "") or "")
        self.account_level = level
        self.spot_td_mode = "cross" if level >= 2 else "cash"
        self.td_mode_resolved = True
        self.last_td_mode_error = ""
        return self.spot_td_mode

    # ---- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        # NEVER let a dead network become a crash-restart loop. Measured
        # 2026-10-10: the proxy's upstream died and `load_markets()` raised a ccxt
        # NetworkError straight out of connect(), so the process exited before its
        # first tick and launchd's KeepAlive relaunched it every ~10 seconds (142
        # startup banners, a growing .err.log) - the loop was not "degraded", it
        # was gone. Everything downstream already tolerates a missing catalogue
        # (min_notional/min_qty fall back, and ccxt reloads markets lazily inside
        # its own fetch_* calls), so a failed connect is a degraded start, not a
        # fatal one: the venue heals on the first call that succeeds.
        try:
            self.ex.load_markets()
        except Exception as exc:  # noqa: BLE001 - start degraded, do not exit
            self.last_connect_error = f"{type(exc).__name__}: {exc}"
        if self.market_type == "spot":
            self.resolve_spot_td_mode()

    def ensure_markets(self) -> bool:
        """Explicitly load the full catalogue; False = the venue did not answer.

        `connect()` deliberately tolerates a failed `load_markets()` (a degraded
        start beats a crash-restart loop), so `self.ex.markets` can legitimately be
        None. Scripts that NEED the catalogue (picking a contract, converting by
        `contractSize`) must therefore ask for it — measured 2026-10-12:
        `options_order_path_check.py` walked straight into
        `AttributeError: 'NoneType' object has no attribute 'values'`, which reads
        like a broken script when it is really a missing catalogue.
        """
        if self.ex.markets:
            return True
        try:
            self.ex.load_markets()
        except Exception as exc:  # noqa: BLE001 - caller reports, we do not raise
            self.last_connect_error = f"{type(exc).__name__}: {exc}"
            return False
        return bool(self.ex.markets)

    @property
    def _markets_loaded(self) -> bool:
        """Whether ccxt has a market catalogue - derived, never tracked.

        Derived from ccxt itself because ccxt loads markets lazily on demand: a
        venue that could not connect during an outage repairs itself on the next
        successful call, and a separate boolean would still say "no".
        """
        return bool(getattr(self.ex, "markets", None))

    def close(self) -> None:
        try:
            self.ex.close()
        except Exception:  # pragma: no cover - best effort
            pass

    # ---- market data -------------------------------------------------------
    def fetch_bars(self, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        rows = self.ex.fetch_ohlcv(self._venue_symbol(symbol), timeframe=timeframe, limit=limit)
        return [
            Bar(ts=int(row[0]), open=float(row[1]), high=float(row[2]),
                low=float(row[3]), close=float(row[4]), volume=float(row[5]))
            for row in rows
        ]

    def refresh_marks(self, symbols: list[str]) -> dict[str, float]:
        marks: dict[str, float] = {}
        try:
            tickers = self.ex.fetch_tickers([self._venue_symbol(s) for s in symbols])
            for symbol, ticker in tickers.items():
                price = ticker.get("last") or ticker.get("close")
                if price:
                    marks[symbol] = float(price)
        except Exception:
            for symbol in symbols:
                ticker = self.ex.fetch_ticker(self._venue_symbol(symbol))
                price = ticker.get("last") or ticker.get("close")
                if price:
                    marks[symbol] = float(price)
        return marks

    def contract_size(self, symbol: str) -> float:
        """One contract of `symbol` in base units (OKX `contractSize`), else 1.0.

        The ledger stores a swap position in 张, so its P&L is
        `(mark - avg) * qty * contract_size`. Measured 2026-10-10: ACT's is 10, so
        the book understated that hedge leg tenfold; CRV/LTC read correctly only
        because theirs is 1. Unknown symbols keep the old behaviour (1.0).
        """
        market = (getattr(self.ex, "markets", None) or {}).get(symbol) or {}
        try:
            size = float(market.get("contractSize") or 0.0)
        except (TypeError, ValueError):
            size = 0.0
        return size if size > 0 else 1.0

    def _venue_symbol(self, symbol: str) -> str:
        """Our symbol -> the CCXT client's symbol, for THIS market type.

        Identity everywhere except options. We keep OKX's instId
        (`BTC-USD-261127-82000-C`) because `leg_of` classifies options by that shape
        and every public-API probe speaks instIds, while ccxt names the same contract
        `BTC/USD:BTC-261127-82000-C`. The mapping is resolved through the catalogue
        (`id` -> `symbol`) rather than by string surgery, so a new expiry or a
        different underlying needs no code change. Measured 2026-10-10: handing the
        instId straight to ccxt answers `BadSymbol 51001`.
        """
        if self.market_type != "option":
            return symbol
        markets = getattr(self.ex, "markets", None) or {}
        if symbol in markets:
            return symbol
        for market in markets.values():
            if str(market.get("id") or "") == symbol:
                return str(market.get("symbol") or symbol)
        return symbol

    def min_notional(self, symbol: str) -> float:
        market = self.ex.markets.get(self._venue_symbol(symbol)) if self._markets_loaded else None
        if not market:
            return 10.0
        limits = (market.get("limits") or {}).get("cost") or {}
        minimum = limits.get("min")
        return float(minimum) if minimum else 10.0

    def min_qty(self, symbol: str) -> float:
        market = self.ex.markets.get(self._venue_symbol(symbol)) if self._markets_loaded else None
        if not market:
            return 0.0
        limits = (market.get("limits") or {}).get("amount") or {}
        minimum = limits.get("min")
        # Fall back to the venue's amount PRECISION when minSz is not reported.
        # Measured 2026-10-01: the bot twice tried to sell a remainder of
        # 3.36e-07 SOL / 6.9e-08 OKB and OKX rejected it with "must be greater
        # than minimum amount precision of 0.000001". The dust filter missed it
        # because its only other gate needs a mark (which can be 0 early in a
        # tick), while this quantity-only gate was returning 0.
        precision = (market.get("precision") or {}).get("amount")
        candidates = [float(v) for v in (minimum, precision) if v]
        return max(candidates) if candidates else 0.0

    def spread_bps(self, symbol: str) -> float | None:
        try:
            book = self.ex.fetch_order_book(self._venue_symbol(symbol), 5)
        except Exception:
            return None
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return None
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0:
            return None
        return 10_000.0 * (best_ask - best_bid) / mid

    def fetch_funding_history(self, symbol: str, limit: int = 90) -> list[float]:
        """Settled funding rates, newest first, for smoothing.

        A single print is a spike - carry's whole economics is the MEAN over
        weeks (README 41) - so the strategy needs real history from the first
        tick rather than a buffer that starts empty after every restart.
        Returns [] when the venue cannot answer; the caller then falls back.
        """
        market = (self.ex.markets or {}).get(symbol) or {}
        if not market.get("contract") and self.market_type != "swap":
            return []
        try:
            rows = self.ex.fetch_funding_rate_history(symbol, limit=limit)
        except Exception:  # noqa: BLE001 - absent history is not an error here
            return []
        out: list[float] = []
        for row in rows or []:
            value = (row or {}).get("fundingRate")
            if value is not None:
                out.append(float(value))
        return out

    def fetch_funding_rate(self, symbol: str) -> float | None:
        """Settled perp funding rate for `symbol`, or None if this venue has none.

        Spot markets have no funding, and OKX answers a spot symbol with an error
        rather than an empty result - so the market is checked first and a venue
        error is reported as "no funding" instead of raising into the loop.
        """
        market = (self.ex.markets or {}).get(symbol) or {}
        if not market.get("contract") and self.market_type != "swap":
            return None
        try:
            payload = self.ex.fetch_funding_rate(symbol)
        except Exception:  # noqa: BLE001 - absent funding is not an error here
            return None
        value = (payload or {}).get("fundingRate")
        return float(value) if value is not None else None

    # ---- account -----------------------------------------------------------
    def get_account(self) -> AccountState:
        balance = self.ex.fetch_balance()
        cash = float((balance.get(self.base_ccy) or {}).get("free") or 0.0)
        total_quote = float((balance.get(self.base_ccy) or {}).get("total") or 0.0)

        # ccxt maps OKX's `free` AND `total` to `eq`, which includes the open
        # positions' unrealised PnL. The raw `cashBal` does not, and it is what the
        # book's cash model mirrors - take it from the raw payload.
        cash_bal = 0.0
        info = balance.get("info")
        rows = info if isinstance(info, list) else (info or {}).get("data") if isinstance(info, dict) else []
        for account_row in rows or []:
            for detail in (account_row or {}).get("details") or []:
                if str(detail.get("ccy")) == self.base_ccy and detail.get("cashBal") not in (None, ""):
                    cash_bal = float(detail.get("cashBal"))
                    break
            if cash_bal:
                break

        positions: dict[str, Position] = {}
        totals = balance.get("total") or {}
        for currency, amount in totals.items():
            if currency == self.base_ccy or not amount or float(amount) <= 0:
                continue
            symbol = f"{currency}/{self.base_ccy}"
            if symbol not in self.ex.markets:
                continue
            mark = self._mark_for(symbol)
            positions[symbol] = Position(symbol=symbol, qty=float(amount), avg_price=mark)

        equity = total_quote + sum(p.qty * p.avg_price for p in positions.values())

        # A perp "position" is NOT a coin balance, so the loop above cannot see it
        # (it finds nothing, and in swap mode equity would silently equal cash).
        # Read positions from the venue in CONTRACTS - the unit every perp fill
        # quotes - so the book and the venue can be compared like for like.
        #
        # This runs for a SPOT venue too, and that is the point: on 2026-10-08 the
        # runner's own reconciliation reported `CRV/USDT:USDT local=-7387
        # venue=0.0` forever, because the venue side of the comparison was reading
        # spot balances and could not see the hedge leg it was being asked about.
        # Any account can hold a swap position.
        swap_positions: dict[str, Position] = {}
        unrealized = 0.0
        try:
            raw_positions = self.ex.fetch_positions()
        except Exception:  # noqa: BLE001 - a venue hiccup must not kill the loop
            raw_positions = []
        for row in raw_positions or []:
            symbol = str(row.get("symbol") or "")
            contracts = float(row.get("contracts") or 0.0)
            if not symbol or abs(contracts) < 1e-12:
                continue
            size = float(
                (self.ex.markets.get(self._venue_symbol(symbol)) or {}).get("contractSize") or 1.0
            )
            # CONTRACTS, not base units: the book stores whatever the fills
            # reported, and every perp fill quotes contracts (`perp_filled` in
            # paired.py multiplies by ctVal only where base units are needed).
            # Reporting base here made the reconciler compare two different units
            # - measured 2026-10-09 on ACT (ctVal 10): local -22,912 contracts vs
            # "venue" -229,120, a 10x artefact that looked like a broken book.
            # CRV/BAND agreed only because their ctVal happens to be 1.
            del size  # kept for the docstring's sake; the unit is contracts
            mark = float(row.get("markPrice") or 0.0) or self._mark_for(symbol)
            side = str(row.get("side") or "").lower()
            qty = abs(contracts)
            # 2026-10-16：**同一个 symbol 可能返回两行** —— OKX 在双向持仓
            # （long/short）模式下会分别报 long 与 short 两条腿。这里原来是
            # `swap_positions[symbol] = ...`，**后一行直接盖掉前一行**，于是
            # 「场馆仓位」只反映其中一条腿，对账就会报一条永不消失的差：
            # 实测 CRV/USDT:USDT `local=-5,970 venue=-13,357`，差 **7,387**
            # 正是 10-15 那笔买回——因为 `buy` 在双向模式下开的是**多腿**，
            # 两腿相加才等于我们账本的净额。账本是净头寸，所以这里必须**累加**。
            signed = (swap_positions[symbol].qty if symbol in swap_positions else 0.0) + (
                -qty if side == "short" else qty
            )
            if abs(signed) < 1e-12:
                swap_positions.pop(symbol, None)   # 两腿抵消 = 这个标的没有净头寸
            else:
                swap_positions[symbol] = Position(
                    symbol=symbol,
                    qty=signed,
                    avg_price=mark,
                    ct_val=self.contract_size(symbol),
                )
            unrealized += float(row.get("unrealizedPnl") or 0.0)
        if self.market_type == "swap":
            positions = {}
            equity = total_quote + unrealized
        else:
            # Spot mode keeps balance-derived positions for equity, but the swap
            # legs must still appear (at a zero cash impact - they are not coins).
            positions.update(swap_positions)

        open_orders = self.fetch_open_orders(list(positions))
        return AccountState(
            venue=self.name,
            base_ccy=self.base_ccy,
            cash=cash,
            cash_bal=cash_bal,
            equity=equity,
            positions=positions,
            open_order_count=len(open_orders),
        )

    def top_of_book(self, symbol: str) -> tuple[float | None, float | None]:
        """(best bid, best ask), or (None, None) when the book cannot be read.

        Used by the passive carry entry to choose a maker price. A one-sided book
        legitimately returns (bid, None) - the caller must treat a missing side
        as "no maker price here", not as zero.
        """
        try:
            book = self.ex.fetch_order_book(self._venue_symbol(symbol), limit=5)
        except Exception:  # noqa: BLE001 - unreadable is not a price
            return None, None
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        bid = float(bids[0][0]) if bids else None
        ask = float(asks[0][0]) if asks else None
        return bid, ask

    def entry_impact_bps(self, symbol: str, qty: float, side: str) -> float | None:
        """What taking `qty` out of the book would cost, in bps of mid.

        None means "cannot tell" (no book, or too thin to fill) - callers must
        treat that as a refusal, not as zero impact. Measured 2026-10-09: a carry
        entry bought $2.2k of ACT spot 1.33% above the market, 3x the 44.9 bps
        round-trip assumption everything else was built on.
        """
        from ..execution.sizing import book_impact_bps

        try:
            book = self.ex.fetch_order_book(self._venue_symbol(symbol), limit=50)
        except Exception:  # noqa: BLE001 - a venue hiccup must not block a check
            return None
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return None
        best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
        if best_bid <= 0 or best_ask <= 0:
            return None
        mid = (best_bid + best_ask) / 2.0
        levels = asks if str(side).lower() == "buy" else bids
        # OKX returns [price, size, order_count]; take the first two and stay
        # tolerant of a venue that returns pairs.
        return book_impact_bps(
            [(float(row[0]), float(row[1])) for row in levels if len(row) >= 2], qty, mid, side
        )

    def _mark_for(self, symbol: str) -> float:
        try:
            ticker = self.ex.fetch_ticker(self._venue_symbol(symbol))
            return float(ticker.get("last") or 0.0)
        except Exception:  # pragma: no cover - network dependent
            return 0.0

    def _free_base(self, symbol: str) -> float | None:
        """Free balance of the asset being sold, straight from the venue."""
        market = (self.ex.markets or {}).get(symbol) or {}
        base = market.get("base") or self._split_symbol(symbol)[0]
        if not base:
            return None
        try:
            balance = self.ex.fetch_balance({"type": self.market_type})
        except Exception:  # pragma: no cover - network dependent
            return None
        entry = balance.get(base) or {}
        free = entry.get("free") if isinstance(entry, dict) else None
        return float(free) if free is not None else None

    # ---- trading -----------------------------------------------------------
    def _create_order(self, request: OrderRequest, qty: float, params: dict) -> dict:
        """Submit, and repair `tdMode` once if the venue rejects it.

        Why this exists (2026-10-10). `spot_td_mode` is resolved in `connect()` from
        the account's `acctLv`, but that probe is allowed to fail - it falls back to
        the field's default, `"cash"`, silently. On an acctLv>=2 account OKX rejects
        `cash` with `51000 Parameter tdMode error`, so a process that missed the
        probe rejects EVERY spot order it sends, while a process that caught it
        trades normally. The evidence is three rejections in one day that looked
        unrelated: two OKB/USDT BUYS (04:43, 04:45, `tag=mean_reversion`) and one
        BTC/USDT sell (11:16, `tag=mean_reversion:exit`) - same sCode as the
        2026-10-07 incident whose root cause was exactly `cash` on this account.

        So: on that specific rejection, re-read the account level and try once more.
        One retry, spot only, and only for the tdMode error - anything else keeps
        its original failure path.
        """
        try:
            return self.ex.create_order(
                self._venue_symbol(request.symbol),
                request.type,
                request.side,
                qty,
                request.price,
                params,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is repairable
            text = str(exc)
            if self.market_type != "spot" or "tdMode" not in text:
                raise
            before = str(getattr(self, "spot_td_mode", "") or "")
            after = self.resolve_spot_td_mode()
            if not after or after == before:
                raise
            params = {**params, "tdMode": after}
            return self.ex.create_order(
                self._venue_symbol(request.symbol),
                request.type,
                request.side,
                qty,
                request.price,
                params,
            )

    def place_order(self, request: OrderRequest) -> Order:
        params: dict[str, Any] = {"clientOrderId": request.client_order_id}
        # Make-or-kill. Measured 2026-10-09 (scripts/cancel_path_check.py): OKX does
        # NOT reject a post-only order that would take liquidity - it ACCEPTS it
        # (`status=open`) and kills it a moment later with `cancelSource=31`,
        # `accFillSz=0`. The economics are what matter and they hold (no fill, no
        # fee, no spread paid), but the submit result is NOT the answer: any caller
        # deciding "did my passive order rest?" must read the order's final state,
        # never `status == "open"`.
        if request.post_only:
            params["ordType"] = "post_only"
        # `reduceOnly` exists on contracts, not on spot. Sending it to OKX spot
        # returns "Reduce Only is not available" (sCode 51205) and rejects the
        # order - which means NO position could ever be closed. On spot a sell is
        # inherently reducing, so the flag is simply omitted.
        market = (self.ex.markets or {}).get(request.symbol) or {}
        # Options are one-way: a sell closes the long, so `reduceOnly` and
        # `posSide` are not accepted on them (the same class of rejection as
        # reduceOnly-on-spot, sCode 51205). Measured 2026-10-10 while wiring the
        # 1.1 adapter: options would otherwise inherit the swap branch below.
        is_option = self.market_type == "option"
        supports_reduce_only = (
            bool(market.get("contract")) or self.market_type != "spot"
        ) and not is_option
        if request.reduce_only and supports_reduce_only:
            params["reduceOnly"] = True

        # Spot tdMode must match the ACCOUNT level, not the market (see __init__):
        # "cash" on a margin-level account is rejected with sCode 51000, and that
        # rejection is what silently stopped every spot entry after the 2026-10-06
        # switch. The value is resolved in connect().
        if self.market_type == "spot" and not market.get("contract"):
            params["tdMode"] = str(getattr(self, "spot_td_mode", "cash") or "cash")
            # …and because tdMode is no longer "cash", ccxt classifies the order as a
            # MARGIN order and deliberately stops sending `tgtCcy`. OKX then falls
            # back to its own default for a spot market BUY, which is to read `sz`
            # as QUOTE currency: a request for "3 LTC" quietly buys 3 USDT worth,
            # i.e. ~1/67th of the intended position. Measured on this account on
            # 2026-10-07 (buy 1.0 LTC filled 0.014814 LTC = 1.00 USDT). So the
            # unit has to be stated explicitly on the buy leg; sells are always in
            # base units and must not carry it.
            if request.side == "buy":
                params["tgtCcy"] = "base_ccy"

        # Swap leg (carry): OKX needs tdMode=cross AND an explicit posSide, and every
        # exit must carry reduceOnly - otherwise closing a short can open a long
        # instead (the bug swap_orders.assert_exit_cannot_flip exists to catch).
        # The rules live in swap_orders (unit-tested); imported locally so this
        # stays a single-hunk change to a 495-line running file.
        if market.get("contract") or self.market_type == "swap":
            from .swap_orders import SwapOrderError, pos_side_for

            params["tdMode"] = str(getattr(self, "swap_td_mode", "cross"))
            # tdMode=cross applies to coin-margined options too, but the POSITION
            # SIDE does not: an option position is long the contract, full stop, and
            # OKX refuses `posSide` on one-way option books.
            if not is_option:
                try:
                    params["posSide"] = pos_side_for(
                        request.side,
                        reduce_only=bool(request.reduce_only),
                        current=str(getattr(self, "default_pos_side", "long")),
                    )
                except SwapOrderError as exc:
                    order = Order(
                        client_order_id=request.client_order_id,
                        symbol=request.symbol,
                        side=request.side,
                        qty=request.qty,
                        type=request.type,
                        price=request.price,
                        status="rejected",
                        reason=f"swap order rejected: {exc}",
                        ts=now_ms(),
                    )
                    self._orders[order.client_order_id] = order
                    return order

        qty = float(request.qty)
        # Swap: OKX takes an integer number of CONTRACTS, not base units. The
        # conversion (floor to lotSz, and refuse rather than round down into dust)
        # lives in the unit-tested swap_math module, so callers keep speaking base
        # units and this is the only place that knows about ctVal.
        is_contract = bool(market.get("contract")) or self.market_type == "swap"
        if is_contract:
            from .swap_math import ContractSpec, SwapMathError, base_to_contracts

            spec = None
            info = market.get("info") or {}
            if info.get("ctVal"):
                try:
                    spec = ContractSpec.from_okx(info)
                except SwapMathError:
                    spec = None
            try:
                if spec is not None:
                    qty = base_to_contracts(spec, float(request.qty))
                else:
                    size = float(market.get("contractSize") or 1.0) or 1.0
                    qty = float(request.qty) / size
            except SwapMathError as exc:
                order = Order(
                    client_order_id=request.client_order_id,
                    symbol=request.symbol,
                    side=request.side,
                    qty=request.qty,
                    type=request.type,
                    price=request.price,
                    status="rejected",
                    reason=f"swap size rejected: {exc}",
                    ts=now_ms(),
                )
                self._orders[order.client_order_id] = order
                return order

        # A sell may never ask the venue for more than it will hand over. A book
        # that overstates a position is the difference between "exit" and
        # "rejected: 51008 insufficient balance" - which happened for real on
        # 2026-09-30 and left a position that could not be closed. Clamp to the
        # venue's free balance; truncate via amount_to_precision so rounding can
        # never re-introduce the overshoot.
        if request.side == "sell" and not is_contract:
            free = self._free_base(request.symbol)
            if free is not None:
                try:
                    capped = float(self.ex.amount_to_precision(request.symbol, free))
                except Exception:  # pragma: no cover - venue dependent
                    capped = free
                if capped <= 0:
                    order = Order(
                        client_order_id=request.client_order_id,
                        symbol=request.symbol,
                        side=request.side,
                        qty=request.qty,
                        type=request.type,
                        price=request.price,
                        status="rejected",
                        reason=f"no free {request.symbol} balance to sell",
                        ts=now_ms(),
                    )
                    self._orders[order.client_order_id] = order
                    return order
                if capped < qty:
                    qty = capped
                    request.qty = capped
            # A remainder below the venue's own minimum is DUST, not an exit -
            # checked on every sell, not only when the clamp bites. Measured
            # 2026-10-08 (SUI/USDT, 1.4e-06): sending it earns "must be greater
            # than minimum amount precision" from OKX every tick. The 2026-10-01
            # fix taught `min_qty` to fall back to the amount precision, but
            # nothing consulted it on this path. Refusing locally turns an endless
            # rejected-order alert into one dust record the book can absorb.
            floor = self.min_qty(request.symbol)
            if floor > 0 and qty < floor:
                order = Order(
                    client_order_id=request.client_order_id,
                    symbol=request.symbol,
                    side=request.side,
                    qty=qty,
                    type=request.type,
                    price=request.price,
                    status="rejected",
                    reason=(
                        f"dust: {qty} {request.symbol} is below the venue minimum "
                        f"{floor} - not sent"
                    ),
                    ts=now_ms(),
                )
                self._orders[order.client_order_id] = order
                return order
        try:
            raw = self._create_order(request, qty, params)
        except Exception as exc:
            order = Order(
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                type=request.type,
                price=request.price,
                status="rejected",
                reason=f"{type(exc).__name__}: {exc}",
                ts=now_ms(),
            )
            self._orders[order.client_order_id] = order
            return order

        order = self._to_order(raw, request)
        self._orders[order.client_order_id] = order
        self._track(order)
        if order.status != "filled":
            self._pending_orders.setdefault(
                order.client_order_id, (order.symbol, time.time())
            )
        return order

    def _to_order(self, raw: dict[str, Any], request: OrderRequest) -> Order:
        filled = float(raw.get("filled") or 0.0)
        amount = float(raw.get("amount") or request.qty or 0.0)
        status = _STATUS_MAP.get(str(raw.get("status")), "open")
        if status == "filled" and 0 < filled < amount:
            status = "partially_filled"
        raw_fee = raw.get("fee") if isinstance(raw.get("fee"), dict) else {}
        return Order(
            client_order_id=str(raw.get("clientOrderId") or request.client_order_id),
            symbol=str(raw.get("symbol") or request.symbol),
            side=str(raw.get("side") or request.side),
            qty=amount,
            type=str(raw.get("type") or request.type),
            price=float(raw.get("price") or request.price or 0.0),
            status=status,
            filled_qty=filled,
            avg_price=float(raw.get("average") or raw.get("price") or 0.0),
            venue_order_id=str(raw.get("id") or ""),
            ts=now_ms(),
            fee=float((raw_fee or {}).get("cost") or 0.0),
            fee_ccy=str((raw_fee or {}).get("currency") or ""),
        )

    @staticmethod
    def _split_symbol(symbol: str) -> tuple[str, str]:
        parts = str(symbol).split("/")
        base = parts[0] if parts else ""
        quote = parts[1].split(":")[0] if len(parts) > 1 else ""
        return base, quote

    def _track(self, order: Order) -> None:
        """Turn cumulative filled quantity into incremental Fill events.

        The venue is the source of truth for BOTH quantity and fee. OKX spot can
        deduct the fee in the BASE asset ("pay fees in OKB" / demo default):
        measured 2026-09-30, a buy of 4.979831 OKB was charged
        `fee = 0.004979831 OKB`, and only 4.97485209 OKB landed in the account.
        The old code ignored that and synthesised `notional * fee_bps/10_000` in
        the QUOTE currency, which overstated the book position by exactly one
        fee on every fill and mislabelled `fees_paid`.
        """
        seen = self._filled_tracker.get(order.client_order_id, 0.0)
        delta = order.filled_qty - seen
        if delta <= 0:
            return
        price = order.avg_price or order.price or self._mark_for(order.symbol)

        base, quote = self._split_symbol(order.symbol)
        quote = quote or self.base_ccy

        reported_fee = float(getattr(order, "fee", 0.0) or 0.0)
        seen_fee = self._fee_tracker.get(order.client_order_id, 0.0)
        delta_fee = max(0.0, reported_fee - seen_fee)
        fee_ccy_raw = str(getattr(order, "fee_ccy", "") or "")

        if delta_fee > 0 and fee_ccy_raw:
            if base and fee_ccy_raw.upper() == base.upper():
                # Charged in the asset being traded: it is deducted from what we
                # actually receive, so ask the venue-truth quantity and convert
                # the fee to quote value for the cash leg.
                qty = max(0.0, delta - delta_fee) if order.side == "buy" else delta
                fee = delta_fee * price
            else:
                qty = delta
                fee = delta_fee
            fee_ccy = quote
        else:
            # Venue reported nothing (or a venue that hides fees): fall back to
            # the configured estimate, quoted in the quote currency.
            qty = delta
            fee = delta * price * self.fee_bps / 10_000.0
            fee_ccy = quote

        self._filled_tracker[order.client_order_id] = order.filled_qty
        self._fee_tracker[order.client_order_id] = reported_fee
        if qty <= 0 and fee <= 0:
            return
        self._fills.append(
            Fill(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,  # type: ignore[arg-type]
                qty=qty,
                price=price,
                fee=fee,
                fee_ccy=fee_ccy,
                ts=now_ms(),
                tag="ccxt",
            )
        )

    def sync(self, symbols: list[str]) -> None:
        """Poll resting AND recently-submitted orders so every fill is recorded."""
        for order in self.fetch_open_orders(symbols):
            self._track(order)
        now = time.time()
        for client_id, (symbol, submitted) in list(self._pending_orders.items()):
            known = self._orders.get(client_id)
            if known is not None and known.status in ("filled", "rejected", "canceled"):
                self._pending_orders.pop(client_id, None)
                continue
            if now - submitted > self.order_poll_ttl_seconds:
                self._pending_orders.pop(client_id, None)
                continue
            try:
                raw = self._fetch_order(client_id, symbol, known)
            except Exception:
                continue
            stub = OrderRequest(
                client_order_id=client_id,
                symbol=symbol,
                side=(known.side if known else "buy"),
                qty=(known.qty if known else 0.0),
                type=(known.type if known else "market"),
                price=(known.price if known else None),
            )
            refreshed = self._to_order(raw, stub)
            self._orders[client_id] = refreshed
            self._track(refreshed)
            if refreshed.status in ("filled", "rejected", "canceled"):
                self._pending_orders.pop(client_id, None)

    def _fetch_order(self, client_order_id: str, symbol: str, known: Order | None):
        """Look up one order by venue id (preferred) or client order id."""
        venue_id = known.venue_order_id if known else None
        if venue_id:
            return self.ex.fetch_order(venue_id, symbol)
        # OKX accepts clOrdId through params; other venues ignore unknown params.
        return self.ex.fetch_order(client_order_id, symbol, params={"clOrdId": client_order_id})

    def cancel_order(self, client_order_id: str, symbol: str) -> bool:
        order = self._orders.get(client_order_id)
        try:
            if order and order.venue_order_id:
                self.ex.cancel_order(order.venue_order_id, self._venue_symbol(symbol))
            else:
                # A client order id is NOT an order id. Measured 2026-10-11: from a
                # FRESH process (the runner was not the one that rested the order, so
                # `self._orders` is empty) OKX refuses `ordId=<clOrdId>`, the order
                # stays resting, and `cancel_order` returned False - an orphan with
                # real notional. The venue takes `clOrdId` as a parameter, so say so.
                self.ex.cancel_order(
                    None, self._venue_symbol(symbol), {"clOrdId": client_order_id}
                )
            return True
        except Exception:
            # Fall back to the plain form: some ccxt paths resolve the id themselves.
            try:
                self.ex.cancel_order(client_order_id, self._venue_symbol(symbol))
                return True
            except Exception:
                return False

    def cancel_all(self, symbols: list[str]) -> int:
        count = 0
        for order in self.fetch_open_orders(symbols):
            if order.venue_order_id and self.cancel_order(order.client_order_id, order.symbol):
                count += 1
        return count

    def open_order_ids(self, symbols: list[str]) -> set[str] | None:
        """Client order ids the venue still has resting, or None if unreadable.

        None is NOT "nothing is resting". `fetch_open_orders` above swallows a
        failed call and returns an empty list, which would let the passive entry
        buy at market on top of an order it cannot prove is gone - the one
        outcome that doubles the spot leg. So this variant reports the failure.
        """
        ids: set[str] = set()
        for symbol in symbols or [None]:  # type: ignore[list-item]
            try:
                raw_list = (
                    self.ex.fetch_open_orders(symbol) if symbol else self.ex.fetch_open_orders()
                )
            except Exception:  # noqa: BLE001 - "cannot tell" must be distinguishable
                return None
            for raw in raw_list or []:
                info = raw.get("info") or {}
                cid = raw.get("clientOrderId") or raw.get("client_order_id") or info.get("clOrdId")
                if cid:
                    ids.add(str(cid))
        return ids

    def order_state(self, client_order_id: str, symbol: str) -> tuple[str, float] | None:
        """The venue's OWN record for one order: (status, cumulative filled), or None.

        This exists because absence from the open-order list cannot tell "cancelled"
        apart from "filled" - a filled order leaves the list too. Measured
        2026-10-10 on LTC/USDT: a post-only buy filled 3.6s after submission, the
        open-order list already showed nothing, the passive entry read that as
        "cancelled" and sent the market fallback, and the spot leg ended up at 2x
        the hedge. `None` means "cannot tell", which callers must treat as a
        refusal to fall back, never as a pass.
        """
        known = self._orders.get(client_order_id)
        try:
            raw = self._fetch_order(client_order_id, symbol, known)
        except Exception:  # noqa: BLE001 - unreadable is not a verdict
            return None
        if not raw:
            return None
        info = raw.get("info") or {}
        state = str(info.get("state") or raw.get("status") or "").lower()
        status = _STATUS_MAP.get(state)
        if status is None:
            return None
        filled = float(info.get("accFillSz") or raw.get("filled") or 0.0)
        return status, filled

    def fetch_open_orders(self, symbols: list[str]) -> list[Order]:
        orders: list[Order] = []
        for symbol in symbols or [None]:  # type: ignore[list-item]
            try:
                raw_list = self.ex.fetch_open_orders(self._venue_symbol(symbol)) if symbol else self.ex.fetch_open_orders()
            except Exception:
                continue
            for raw in raw_list:
                request = OrderRequest(
                    client_order_id=str(raw.get("clientOrderId") or raw.get("id")),
                    symbol=str(raw.get("symbol")),
                    side=str(raw.get("side")),
                    qty=float(raw.get("amount") or 0.0),
                    type=str(raw.get("type") or "limit"),
                    price=float(raw.get("price") or 0.0),
                )
                orders.append(self._to_order(raw, request))
        return orders

    def drain_fills(self) -> list[Fill]:
        fills, self._fills = self._fills, []
        return fills

    def account_snapshot(self) -> dict[str, Any]:
        snapshot = super().account_snapshot()
        snapshot["open_orders"] = sorted(
            {o.client_order_id for o in self.fetch_open_orders([])}
        )
        return snapshot

    def clock_skew_ms(self) -> int:
        """Local vs venue clock. >1s of skew breaks signing and bar alignment."""
        try:
            venue_ms = int(self.ex.fetch_time())
        except Exception:
            return 0
        return int(time.time() * 1000) - venue_ms
