"""Unit + integration tests. Stdlib unittest, so `python3 -m unittest` works
without installing anything; pytest also collects them.
"""

from __future__ import annotations

import shutil
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.backtest import run_backtest  # noqa: E402
from ta.config import Config  # noqa: E402
from ta.execution.oms import OMS  # noqa: E402
from ta.execution.sizing import (  # noqa: E402
    risk_based_qty,
    take_profit_price_for,
    trailing_stop_price,
    vol_target_scale,
)
from ta.log import setup  # noqa: E402
from ta.marketdata import LiveFeed, SyntheticFeed  # noqa: E402
from ta.monitoring import Metrics  # noqa: E402
from ta.models import (  # noqa: E402
    AccountState,
    Bar,
    Fill,
    Order,
    OrderRequest,
    Position,
)
from ta.onchain import TokenScreener, TokenSnapshot, load_snapshots  # noqa: E402
from ta.portfolio import Ledger, PositionBook  # noqa: E402
from ta.risk.engine import RiskEngine  # noqa: E402
from ta.runner import TradingRunner  # noqa: E402
from ta.resilience import ResilientVenue, RetryPolicy, VenueDegraded, retry_call  # noqa: E402
from ta.strategies import build_strategy  # noqa: E402
from ta.venues import build_venue  # noqa: E402

CONFIG_DIR = PROJECT / "config"


class CcxtFeeCurrencyTests(unittest.TestCase):
    """The ledger must take the fee from the venue, not estimate it.

    OKX spot can deduct the fee in the BASE asset ("pay fees in OKB"). Measured
    on the demo account 2026-09-30: buy OKB/USDT filled 4.979831, fee
    0.004979831 OKB, and only 4.97485209 OKB was credited. Estimating the fee as
    `notional * fee_bps` and labelling it USDT overstated the book position by
    exactly one fee on every fill, and mislabelled `fees_paid`.
    """

    def _venue(self):
        try:
            from ta.venues.ccxt_venue import CcxtVenue
        except ImportError:  # pragma: no cover - environment dependent
            self.skipTest("ccxt not installed")
        return CcxtVenue("okx", api_key="k", secret="s", password="p", sandbox=True)

    def _fill(self, raw: dict, side: str = "buy", qty: float = 1.0) -> Fill:
        venue = self._venue()
        request = OrderRequest(
            client_order_id=raw["clientOrderId"],
            symbol=raw["symbol"],
            side=side,
            qty=qty,
            type="market",
        )
        venue._track(venue._to_order(raw, request))
        fills = venue.drain_fills()
        self.assertEqual(len(fills), 1)
        return fills[0]

    def test_fee_charged_in_base_asset_nets_the_quantity(self):
        fill = self._fill(
            {
                "id": "1",
                "clientOrderId": "t1",
                "symbol": "OKB/USDT",
                "side": "buy",
                "type": "market",
                "amount": 4.980079,
                "filled": 4.979831,
                "average": 120.46598379945023,
                "status": "closed",
                "fee": {"cost": 0.004979831, "currency": "OKB"},
            },
            qty=4.980079,
        )
        # 4.979831 filled - 0.004979831 charged in OKB. The venue's *account
        # balance* reads 4.97485209 because it also carries 9.2e-07 of old dust.
        self.assertAlmostEqual(fill.qty, 4.974851169, places=8)
        self.assertAlmostEqual(fill.fee, 0.5999, places=3)
        self.assertEqual(fill.fee_ccy, "USDT")

    def test_fee_charged_in_quote_asset_passes_through_unchanged(self):
        fill = self._fill(
            {
                "id": "2",
                "clientOrderId": "t2",
                "symbol": "OKB/USDT",
                "side": "sell",
                "type": "market",
                "amount": 4.9711,
                "filled": 4.9711,
                "average": 120.38,
                "status": "closed",
                "fee": {"cost": 0.598421018, "currency": "USDT"},
            },
            side="sell",
            qty=4.9711,
        )
        self.assertAlmostEqual(fill.qty, 4.9711, places=8)
        self.assertAlmostEqual(fill.fee, 0.598421018, places=6)
        self.assertEqual(fill.fee_ccy, "USDT")

    def test_fill_preserves_strategy_tag_from_the_order_request(self) -> None:
        """Loss attribution must survive the venue normalization.

        Fills used to be stamped with the transport label ``ccxt`` regardless of
        the order's intent. That made 43/55 closed trades unattributable to a
        strategy and forced the loss review to infer causes from symbols alone.
        """
        venue = self._venue()
        request = OrderRequest(
            client_order_id="tagged-fill",
            symbol="BTC/USDT",
            side="buy",
            qty=0.01,
            tag="trend_pullback:entry",
        )
        order = venue._to_order(
            {
                "id": "5",
                "clientOrderId": request.client_order_id,
                "symbol": request.symbol,
                "side": request.side,
                "type": "market",
                "amount": request.qty,
                "filled": request.qty,
                "average": 83_000.0,
                "status": "closed",
                "fee": {"cost": 0.1, "currency": "USDT"},
            },
            request,
        )
        venue._track(order)
        fill = venue.drain_fills()[0]
        self.assertEqual(fill.tag, "trend_pullback:entry")

    def test_delayed_fill_keeps_strategy_tag_through_pending_order_sync(self) -> None:
        """OKX usually reports `open` first and the fill arrives through sync().

        That refresh path builds a new OrderRequest; omitting the known order's
        tag made every delayed carry fill fall back to the transport label
        `ccxt`, even though the original request was tagged `carry`.
        """
        venue = self._venue()
        request = OrderRequest(
            client_order_id="delayed-carry",
            symbol="HBAR/USDT",
            side="buy",
            qty=100.0,
            tag="carry",
        )
        venue._orders[request.client_order_id] = venue._to_order(
            {
                "id": "6",
                "clientOrderId": request.client_order_id,
                "symbol": request.symbol,
                "side": request.side,
                "type": "market",
                "amount": request.qty,
                "filled": 0.0,
                "status": "open",
            },
            request,
        )
        venue._pending_orders[request.client_order_id] = (request.symbol, time.time())
        venue.fetch_open_orders = lambda symbols: []
        venue._fetch_order = lambda client_order_id, symbol, known: {
            "id": "6",
            "clientOrderId": request.client_order_id,
            "symbol": request.symbol,
            "side": request.side,
            "type": "market",
            "amount": request.qty,
            "filled": request.qty,
            "average": 0.08,
            "status": "closed",
            "fee": {"cost": 0.01, "currency": "USDT"},
        }

        venue.sync([request.symbol])
        fill = venue.drain_fills()[0]
        self.assertEqual(fill.tag, "carry")

    def test_missing_fee_falls_back_to_the_configured_estimate(self):
        fill = self._fill(
            {
                "id": "3",
                "clientOrderId": "t3",
                "symbol": "BTC/USDT",
                "side": "buy",
                "type": "market",
                "amount": 0.01,
                "filled": 0.01,
                "average": 83_000.0,
                "status": "closed",
            },
            qty=0.01,
        )
        self.assertAlmostEqual(fill.qty, 0.01, places=10)
        self.assertAlmostEqual(fill.fee, 0.01 * 83_000.0 * 8.0 / 10_000.0, places=6)

    def test_partial_fills_do_not_double_count_the_fee(self):
        venue = self._venue()
        request = OrderRequest(
            client_order_id="t4", symbol="OKB/USDT", side="buy", qty=2.0, type="market"
        )
        first = venue._to_order(
            {
                "id": "4",
                "clientOrderId": "t4",
                "symbol": "OKB/USDT",
                "side": "buy",
                "type": "market",
                "amount": 2.0,
                "filled": 1.0,
                "average": 100.0,
                "status": "open",
                "fee": {"cost": 0.001, "currency": "OKB"},
            },
            request,
        )
        venue._track(first)
        second = venue._to_order(
            {
                "id": "4",
                "clientOrderId": "t4",
                "symbol": "OKB/USDT",
                "side": "buy",
                "type": "market",
                "amount": 2.0,
                "filled": 2.0,
                "average": 100.0,
                "status": "closed",
                "fee": {"cost": 0.002, "currency": "OKB"},
            },
            request,
        )
        venue._track(second)
        fills = venue.drain_fills()
        self.assertEqual(len(fills), 2)
        self.assertAlmostEqual(sum(f.qty for f in fills), 2.0 - 0.002, places=10)
        self.assertAlmostEqual(sum(f.fee for f in fills), 0.2, places=10)

    def test_book_matches_the_venue_after_a_base_fee_buy(self):
        """End to end: after applying the fill, cash and position equal reality."""
        venue = self._venue()
        book = PositionBook(cash=103_361.12875012167)
        request = OrderRequest(
            client_order_id="t1", symbol="OKB/USDT", side="buy", qty=4.980079, type="market"
        )
        venue._track(
            venue._to_order(
                {
                    "id": "1",
                    "clientOrderId": "t1",
                    "symbol": "OKB/USDT",
                    "side": "buy",
                    "type": "market",
                    "amount": 4.980079,
                    "filled": 4.979831,
                    "average": 120.46598379945023,
                    "status": "closed",
                    "fee": {"cost": 0.004979831, "currency": "OKB"},
                },
                request,
            )
        )
        for fill in venue.drain_fills():
            book.apply_fill(fill)
        # venue truth after the order: USDT 102,761.22851, and the OKB credited
        # by this order alone is 4.974851169 (balance 4.97485209 incl. dust).
        self.assertAlmostEqual(book.cash, 102_761.22850955, places=4)
        self.assertAlmostEqual(book.positions["OKB/USDT"].qty, 4.974851169, places=8)

    def test_a_sell_never_asks_for_more_than_the_venue_holds(self):
        """A book that overstates a position must not break the exit.

        On 2026-09-30 the book held 4.979831 OKB while the venue held 4.97485209,
        so the exit order was rejected with `51008 insufficient balance` and the
        position could not be closed. The venue now clamps a sell to the free
        balance, so the exit still happens.
        """
        venue = self._venue()
        sent: list[float] = []

        class _Ex:
            markets = {"OKB/USDT": {"base": "OKB", "quote": "USDT"}}

            def fetch_balance(self, params=None):  # noqa: ANN001
                return {"OKB": {"free": 4.97485209}}

            def amount_to_precision(self, symbol, amount):  # noqa: ANN001
                return f"{amount:.8f}"

            def create_order(self, symbol, otype, side, qty, price, params):  # noqa: ANN001
                sent.append(qty)
                return {
                    "id": "1",
                    "clientOrderId": params["clientOrderId"],
                    "symbol": symbol,
                    "side": side,
                    "type": otype,
                    "amount": qty,
                    "filled": qty,
                    "average": 120.0,
                    "status": "closed",
                    "fee": {"cost": 0.6, "currency": "USDT"},
                }

        venue.ex = _Ex()
        order = venue.place_order(
            OrderRequest(
                client_order_id="x1", symbol="OKB/USDT", side="sell", qty=4.979831, type="market"
            )
        )
        self.assertEqual(order.status, "filled", order.reason)
        self.assertAlmostEqual(sent[0], 4.97485209, places=8)

    def test_a_sell_with_no_free_balance_is_rejected_locally(self):
        venue = self._venue()

        class _Ex:
            markets = {"OKB/USDT": {"base": "OKB", "quote": "USDT"}}

            def fetch_balance(self, params=None):  # noqa: ANN001
                return {"OKB": {"free": 0.0}}

            def amount_to_precision(self, symbol, amount):  # noqa: ANN001
                return f"{amount:.8f}"

            def create_order(self, *a, **k):  # noqa: ANN001, ANN002, ANN003
                raise AssertionError("must not reach the venue")

        venue.ex = _Ex()
        order = venue.place_order(
            OrderRequest(
                client_order_id="x2", symbol="OKB/USDT", side="sell", qty=1.0, type="market"
            )
        )
        self.assertEqual(order.status, "rejected")
        self.assertIn("no free", order.reason)


def _load_script(name: str):
    """Load `scripts/<name>.py` by path: they are not an installed package, and
    several of them are CLI entry points rather than importable modules."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, PROJECT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iso(ts_ms: float) -> str:
    """Epoch ms -> the same ISO shape `logs/alerts.jsonl` writes (UTC, +00:00)."""
    import datetime

    return datetime.datetime.fromtimestamp(ts_ms / 1000.0, datetime.timezone.utc).isoformat()


def account(
    cash: float = 10_000.0,
    equity: float | None = None,
    day_start_equity: float = 10_000.0,
    peak_equity: float = 10_000.0,
    **kwargs,
) -> AccountState:
    equity = cash if equity is None else equity
    return AccountState(
        venue="test",
        base_ccy="USDT",
        cash=cash,
        equity=equity,
        # risk percentages are measured against the capital the bot controls
        risk_equity=equity,
        day_start_equity=day_start_equity,
        peak_equity=peak_equity,
        **kwargs,
    )


class ConfigTests(unittest.TestCase):
    def test_loads_and_validates(self) -> None:
        config = Config.load(CONFIG_DIR)
        self.assertEqual(config.validate(), [])
        self.assertIn("paper", config.venues)
        self.assertIn("trend_breakout", config.strategies)
        self.assertGreater(config.risk.per_trade.risk_per_trade_pct, 0)

    def test_cluster_lookup(self) -> None:
        config = Config.load(CONFIG_DIR)
        self.assertEqual(config.risk.clusters.bucket_of("BTC/USDT"), "majors")
        self.assertEqual(config.risk.clusters.bucket_of("DOGE/USDT"), "meme")
        self.assertIsNone(config.risk.clusters.bucket_of("DOGE/USD"))

    def test_tiers_and_symbol_resolution(self) -> None:
        config = Config.load(CONFIG_DIR)
        self.assertIn("new", config.risk.tier_names)
        self.assertEqual(config.symbol_tiers["BTC/USDT"], "majors")
        self.assertEqual(
            config.resolve_symbols(config.strategy("new_listing_breakout")),
            config.universe.symbols_by_tier["new"],
        )
        self.assertLess(
            config.risk.tiers["new"].risk_per_trade_pct,
            config.risk.tiers["alts"].risk_per_trade_pct,
        )

    def test_unknown_strategy_raises(self) -> None:
        config = Config.load(CONFIG_DIR)
        with self.assertRaises(KeyError):
            config.strategy("does_not_exist")


class SizingTests(unittest.TestCase):
    def test_risk_based_qty(self) -> None:
        self.assertAlmostEqual(risk_based_qty(10_000, 1.0, 100, 95), 20.0)
        self.assertAlmostEqual(risk_based_qty(10_000, 0.5, 100, 95), 10.0)
        self.assertEqual(risk_based_qty(10_000, 1.0, 100, 100), 0.0)

    def test_vol_target_scaling_is_clamped(self) -> None:
        self.assertAlmostEqual(vol_target_scale(1.5, 1.5), 1.0)
        self.assertEqual(vol_target_scale(100.0, 1.5, 0.3, 1.5), 0.3)
        self.assertEqual(vol_target_scale(0.01, 1.5, 0.3, 1.5), 1.5)
        self.assertEqual(vol_target_scale(None, 1.5), 1.0)

    def test_stop_and_target_math(self) -> None:
        stop = 95.0
        self.assertEqual(take_profit_price_for(100.0, stop, 2.0), 110.0)

    def test_trailing_stop_only_ratchets_up(self) -> None:
        self.assertAlmostEqual(trailing_stop_price(90.0, 100.0, 2.0, 3.0), 94.0)
        self.assertEqual(trailing_stop_price(98.0, 100.0, 2.0, 3.0), 98.0)
        self.assertEqual(trailing_stop_price(None, 100.0, 2.0, 3.0), 94.0)


class LedgerTests(unittest.TestCase):
    def test_book_accounting(self) -> None:
        book = PositionBook(cash=1_000.0)
        book.mark("BTC/USDT", 100.0)
        book.apply_fill(Fill("a", "BTC/USDT", "buy", 1.0, 100.0, fee=0.1, ts=1))
        self.assertAlmostEqual(book.cash, 1_000.0 - 100.0 - 0.1)
        self.assertAlmostEqual(book.equity(), 1_000.0 - 0.1)
        realized = book.apply_fill(Fill("b", "BTC/USDT", "sell", 1.0, 110.0, fee=0.11, ts=2))
        self.assertAlmostEqual(realized, 10.0 - 0.11)
        self.assertAlmostEqual(book.cash, 1_000.0 - 100.0 - 0.1 + 110.0 - 0.11)
        self.assertEqual(book.open_positions(), {})
        self.assertEqual(book.wins, 1)

    def test_sell_cannot_go_short(self) -> None:
        book = PositionBook(cash=1_000.0)
        book.mark("BTC/USDT", 100.0)
        book.apply_fill(Fill("a", "BTC/USDT", "sell", 5.0, 100.0, fee=0.0, ts=1))
        self.assertEqual(book.positions["BTC/USDT"].qty, 0.0)
        self.assertEqual(book.cash, 1_000.0)

    def test_history_is_reloaded_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            ledger = Ledger(path)
            ledger.record_order(Order("cid1", "BTC/USDT", "buy", 1.0, status="filled"))
            ledger.record_fill(Fill("cid1", "BTC/USDT", "buy", 1.0, 100.0, fee=0.1, ts=1))
            reopened = Ledger(path)
            self.assertTrue(reopened.has_order("cid1"))
            self.assertEqual(len(reopened.fills), 1)
            self.assertAlmostEqual(reopened.stats()["fees_paid"], 0.1)
            self.assertEqual(len(Ledger.read_fills(path)), 1)
            # a restarted process must still refuse the same client order id
            replay_oms = OMS(
                build_venue(Config.load(CONFIG_DIR), "paper"), reopened, dry_run=False
            )
            self.assertEqual(
                replay_oms.submit(OrderRequest("cid1", "BTC/USDT", "buy", 1.0)).status,
                "rejected",
            )


class RiskEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config.load(CONFIG_DIR)
        self.tmp = tempfile.TemporaryDirectory()
        self.kill = Path(self.tmp.name) / "KILL_SWITCH"
        self.risk = RiskEngine(
            self.config.risk,
            self.kill,
            self.config.onchain,
            symbol_tiers=self.config.symbol_tiers,
        )
        self.account = account()
        self.marks = {"BTC/USDT": 60_000.0}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_tier_cap_counts_only_its_own_positions(self) -> None:
        """Measured 2026-10-10: the cap was compared against the WHOLE book.

        With three carry pairs open (6 positions) the carry tier refused every new
        pair with "6 >= 5 (tier carry)", and the TradFi tier's own cap of 2 would
        have refused its first entry - a funded sleeve that could never start.
        """
        carry = self.config.universe.symbols_by_tier["carry"][:6]
        self.account.positions = {
            s: Position(symbol=s, qty=1.0, avg_price=1.0) for s in carry
        }
        self.marks = {"JUP/USDT": 1.0}
        decision = self.risk.pre_trade(
            OrderRequest("t", "JUP/USDT", "buy", 30.0, stop_price=0.97),
            self.account,
            1.0,
            self.marks,
        )
        self.assertNotIn("max_open_positions", decision.reason, decision.reason)

    def test_a_tier_cap_still_blocks_its_own_next_position(self) -> None:
        tier = self.config.universe.symbols_by_tier["new"]  # JUP, PYTH, ONDO, W, PENGU
        self.account.positions = {
            s: Position(symbol=s, qty=1.0, avg_price=1.0) for s in tier[:2]
        }
        self.marks = {tier[2]: 1.0}
        decision = self.risk.pre_trade(
            OrderRequest("t", tier[2], "buy", 30.0, stop_price=0.97),
            self.account,
            1.0,
            self.marks,
        )
        self.assertIn("max_open_positions", decision.reason)

    def test_small_order_approved(self) -> None:
        decision = self.risk.pre_trade(
            OrderRequest("a", "BTC/USDT", "buy", 0.002), self.account, 60_000.0, self.marks
        )
        self.assertTrue(decision.approved)
        self.assertFalse(decision.adjusted)

    def test_oversized_order_is_shrunk_not_rejected(self) -> None:
        decision = self.risk.pre_trade(
            OrderRequest("a", "BTC/USDT", "buy", 5.0), self.account, 60_000.0, self.marks
        )
        self.assertTrue(decision.approved)
        self.assertTrue(decision.adjusted)
        self.assertLess(decision.qty, 5.0)
        per_trade = self.config.risk.per_trade
        tier = self.config.risk.tiers[self.risk.tier_of("BTC/USDT")]
        caps = [
            self.account.equity * per_trade.max_order_notional_pct / 100.0,
            self.account.equity * tier.max_order_notional_pct / 100.0,
        ]
        if per_trade.max_order_notional_usd > 0:
            caps.append(per_trade.max_order_notional_usd)
        self.assertLessEqual(decision.qty * 60_000.0, min(caps) + 1e-6)

    def test_zero_absolute_cap_means_uncapped_and_percentages_govern(self) -> None:
        """`max_order_notional_usd = 0` must mean "no absolute cap".

        Read as a literal value it would make `min(0, ...)` zero every order and
        silently stop all trading, which is the opposite of removing the cap.
        """
        self.config.risk.per_trade.max_order_notional_usd = 0.0
        risk = RiskEngine(
            self.config.risk,
            self.kill,
            self.config.onchain,
            symbol_tiers=self.config.symbol_tiers,
        )
        decision = risk.pre_trade(
            OrderRequest("a", "BTC/USDT", "buy", 5.0), self.account, 60_000.0, self.marks
        )
        self.assertTrue(decision.approved, decision.reason)
        self.assertTrue(decision.adjusted)
        # bounded by the relative caps only, not by a $600-style absolute number
        per_trade = self.config.risk.per_trade
        tier = self.config.risk.tiers[risk.tier_of("BTC/USDT")]
        expected = min(
            self.account.equity * per_trade.max_order_notional_pct / 100.0,
            self.account.equity * tier.max_order_notional_pct / 100.0,
        )
        self.assertAlmostEqual(decision.qty * 60_000.0, expected, delta=1.0)

    def test_whitelist_enforced(self) -> None:
        decision = self.risk.pre_trade(
            OrderRequest("a", "NOTLISTED/USDT", "buy", 100.0),
            self.account,
            0.2,
            {"NOTLISTED/USDT": 0.2},
        )
        self.assertTrue(decision.rejected)
        self.assertIn("symbol_not_in_any_tier", decision.reason)

    def test_missing_allowlist_fails_closed(self) -> None:
        # no tiers and no whitelist must mean "nothing allowed", not "anything goes"
        bare = RiskEngine(self.config.risk, self.kill, self.config.onchain)
        decision = bare.pre_trade(
            OrderRequest("a", "ANY/USDT", "buy", 100.0), self.account, 1.0, {"ANY/USDT": 1.0}
        )
        self.assertTrue(decision.rejected)
        self.assertIn("no_allowlist_configured", decision.reason)

    def test_min_notional_enforced(self) -> None:
        decision = self.risk.pre_trade(
            OrderRequest("a", "BTC/USDT", "buy", 0.00005), self.account, 60_000.0, self.marks
        )
        self.assertTrue(decision.rejected)

    def test_position_and_exposure_caps(self) -> None:
        heavy = account(cash=500.0)
        heavy.positions["BTC/USDT"] = Position(symbol="BTC/USDT", qty=0.3, avg_price=60_000.0)
        decision = self.risk.pre_trade(
            OrderRequest("a", "BTC/USDT", "buy", 0.01), heavy, 60_000.0, self.marks
        )
        self.assertTrue(decision.rejected)
        # Since the 2026-10-07 split the SPOT leg's own ceiling usually binds
        # before the portfolio one, so either refusal is correct - what matters is
        # that the order does not go through.
        self.assertTrue(
            "no_room" in decision.reason or "leg_room" in decision.reason, decision.reason
        )

    def test_order_rate_limit(self) -> None:
        # isolate the per-minute limiter from the daily budgets
        import dataclasses

        relaxed = dataclasses.replace(
            self.config.risk,
            account=dataclasses.replace(self.config.risk.account, max_trades_per_day=500),
            symbols=dataclasses.replace(
                self.config.risk.symbols, max_trades_per_symbol_per_day=500
            ),
        )
        engine = RiskEngine(
            relaxed, self.kill, self.config.onchain, symbol_tiers=self.config.symbol_tiers
        )
        per_minute = engine.limits_for("BTC/USDT").max_orders_per_minute
        for i in range(per_minute):
            decision = engine.pre_trade(
                OrderRequest(f"r{i}", "BTC/USDT", "buy", 0.002), self.account, 60_000.0, self.marks
            )
            self.assertTrue(decision.approved)
        blocked = engine.pre_trade(
            OrderRequest("overflow", "BTC/USDT", "buy", 0.002), self.account, 60_000.0, self.marks
        )
        self.assertTrue(blocked.rejected)
        self.assertIn("rate_limit", blocked.reason)

    def test_daily_trade_caps(self) -> None:
        tier_cap = self.risk.limits_for("BTC/USDT").max_trades_per_day
        symbol_cap = self.config.risk.symbols.max_trades_per_symbol_per_day
        cap = min(tier_cap, symbol_cap)
        for i in range(cap):
            decision = self.risk.pre_trade(
                OrderRequest(f"d{i}", "BTC/USDT", "buy", 0.002),
                self.account,
                60_000.0,
                self.marks,
            )
            self.assertTrue(decision.approved, decision.reason)
        over = self.risk.pre_trade(
            OrderRequest("d-over", "BTC/USDT", "buy", 0.002), self.account, 60_000.0, self.marks
        )
        self.assertTrue(over.rejected)
        self.assertTrue(
            "daily_trade_cap" in over.reason or "symbol_daily_cap" in over.reason, over.reason
        )

    def test_symbol_cooldown_after_stop_out(self) -> None:
        self.risk.note_stop_out("BTC/USDT")
        self.assertGreater(self.risk.cooldown_remaining_minutes("BTC/USDT"), 0)
        decision = self.risk.pre_trade(
            OrderRequest("c1", "BTC/USDT", "buy", 0.002), self.account, 60_000.0, self.marks
        )
        self.assertTrue(decision.rejected)
        self.assertIn("symbol_cooldown", decision.reason)

    def test_portfolio_heat_is_capped(self) -> None:
        hot = account()
        # Keep the notional INSIDE the spot leg's budget so the heat gate is what
        # this test actually exercises - the leg ceiling would otherwise refuse the
        # order first and the assertion below would pass for the wrong reason.
        # The spot leg is 30% ($3k of $10k) since the four-leg split; stops 25%
        # away put heat at ~6%, above the 4% cap.
        hot.positions["BTC/USDT"] = Position(symbol="BTC/USDT", qty=0.02, avg_price=60_000.0)
        hot.positions["ETH/USDT"] = Position(symbol="ETH/USDT", qty=0.4, avg_price=3_000.0)
        marks = {"BTC/USDT": 60_000.0, "ETH/USDT": 3_000.0}
        stops = {"BTC/USDT": 60_000.0 * 0.75, "ETH/USDT": 3_000.0 * 0.75}
        self.assertGreater(
            self.risk.portfolio_heat_pct(hot, marks, stops),
            self.config.risk.account.max_heat_pct,
        )
        decision = self.risk.pre_trade(
            OrderRequest("h1", "ETH/USDT", "buy", 0.5, stop_price=3_000.0 * 0.75),
            hot,
            3_000.0,
            marks,
            stops=stops,
        )
        self.assertTrue(decision.rejected)
        self.assertIn("heat", decision.reason)

    def test_tier_limits_differ_by_symbol(self) -> None:
        self.assertGreater(
            self.risk.risk_per_trade_pct("BTC/USDT"),
            self.risk.risk_per_trade_pct("JUP/USDT"),
        )
        self.assertGreater(
            self.risk.limits_for("BTC/USDT").max_position_pct,
            self.risk.limits_for("JUP/USDT").max_position_pct,
        )

    def test_kill_switch_blocks_and_persists(self) -> None:
        self.risk.trigger_kill("unit test")
        self.assertTrue(self.kill.exists())
        blocked = self.risk.pre_trade(
            OrderRequest("a", "BTC/USDT", "buy", 0.002), self.account, 60_000.0, self.marks
        )
        self.assertTrue(blocked.rejected)
        fresh = RiskEngine(self.config.risk, self.kill, self.config.onchain)
        self.assertTrue(
            fresh.pre_trade(
                OrderRequest("a", "BTC/USDT", "buy", 0.002), self.account, 60_000.0, self.marks
            ).rejected
        )

    def test_drawdown_triggers_kill(self) -> None:
        crashed = account(cash=8_000.0, equity=8_000.0, peak_equity=10_000.0)
        actions = self.risk.on_tick(crashed)
        self.assertIn("kill", actions)
        self.assertTrue(self.risk.killed)

    def test_daily_loss_pauses_then_flattens(self) -> None:
        circuit = self.config.risk.circuit
        soft = account(equity=10_000.0 * (1 - (circuit.daily_loss_stop_open_pct + 0.3) / 100.0))
        self.assertIn("pause", self.risk.on_tick(soft))
        hard = account(equity=10_000.0 * (1 - (circuit.daily_loss_flatten_pct + 0.3) / 100.0))
        self.assertIn("flatten", self.risk.on_tick(hard))

    def test_pause_does_not_repeat_every_tick(self) -> None:
        circuit = self.config.risk.circuit
        soft = account(equity=10_000.0 * (1 - (circuit.daily_loss_stop_open_pct + 0.3) / 100.0))
        self.assertIn("pause", self.risk.on_tick(soft))
        self.assertEqual(self.risk.on_tick(soft), [])


class PaperVenueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.venue = build_venue(Config.load(CONFIG_DIR), "paper")
        self.venue.set_mark("BTC/USDT", 60_000.0)
        self.ledger = Ledger(Path(self.tmp.name) / "ledger.jsonl")
        self.oms = OMS(self.venue, self.ledger, dry_run=False)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_market_buy_then_sell(self) -> None:
        buy = self.oms.submit(OrderRequest("b1", "BTC/USDT", "buy", 0.01))
        self.assertEqual(buy.status, "filled")
        position = self.venue.get_account().positions["BTC/USDT"]
        self.assertAlmostEqual(position.qty, 0.01)
        sell = self.oms.submit(
            OrderRequest("s1", "BTC/USDT", "sell", 0.01, reduce_only=True)
        )
        self.assertEqual(sell.status, "filled")
        self.assertEqual(self.venue.get_account().positions, {})

    def test_insufficient_cash_rejected(self) -> None:
        order = self.oms.submit(OrderRequest("big", "BTC/USDT", "buy", 1.0))
        self.assertEqual(order.status, "rejected")
        self.assertIn("insufficient cash", order.reason)

    def test_sell_without_position_rejected(self) -> None:
        order = self.oms.submit(OrderRequest("s", "BTC/USDT", "sell", 0.01))
        self.assertEqual(order.status, "rejected")

    def test_limit_order_fills_only_when_crossed(self) -> None:
        self.oms.submit(OrderRequest("l1", "BTC/USDT", "buy", 0.001, type="limit", price=50_000.0))
        self.assertEqual(len(self.venue.fetch_open_orders(["BTC/USDT"])), 1)
        self.venue.set_mark("BTC/USDT", 51_000.0)
        self.assertEqual(len(self.venue.fetch_open_orders(["BTC/USDT"])), 1)
        self.venue.set_mark("BTC/USDT", 49_500.0)
        self.assertEqual(len(self.venue.fetch_open_orders(["BTC/USDT"])), 0)

    def test_duplicate_client_order_id_is_refused(self) -> None:
        self.oms.submit(OrderRequest("dup", "BTC/USDT", "buy", 0.001))
        again = self.oms.submit(OrderRequest("dup", "BTC/USDT", "buy", 0.001))
        self.assertEqual(again.status, "rejected")
        self.assertIn("duplicate", again.reason)

    def test_dry_run_sends_nothing(self) -> None:
        oms = OMS(self.venue, self.ledger, dry_run=True)
        order = oms.submit(OrderRequest("dry", "BTC/USDT", "buy", 0.001))
        self.assertEqual(order.status, "dry_run")
        self.assertEqual(self.venue.get_account().positions, {})

    def test_client_order_id_shape(self) -> None:
        cid = OMS.new_client_order_id("BTC/USDT", "trend_breakout")
        self.assertLessEqual(len(cid), 32)
        self.assertTrue(cid.isalnum())


class ScreenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config.load(CONFIG_DIR)
        self.screener = TokenScreener(self.config.onchain)

    def test_fixtures(self) -> None:
        results = {r.symbol: r for r in self.screener.screen_many(load_snapshots(PROJECT / "fixtures" / "tokens.json"))}
        self.assertTrue(results["CLEAN"].passed)
        self.assertFalse(results["SCAM"].passed)
        self.assertFalse(results["TAXED"].passed)

    def test_honeypot_always_rejected(self) -> None:
        token = TokenSnapshot(
            mint="x",
            symbol="HP",
            age_minutes=1_000,
            liquidity_usd=10_000_000,
            volume_24h_usd=10_000_000,
            holder_count=99_999,
            top10_holder_pct=1.0,
            lp_locked=True,
            is_honeypot=True,
            price_impact_pct=0.1,
        )
        self.assertFalse(self.screener.screen(token).passed)

    def test_blacklist(self) -> None:
        config = Config.load(CONFIG_DIR)
        config.onchain.blacklist.mints.append("bad")
        token = TokenSnapshot(
            mint="bad",
            symbol="BAD",
            age_minutes=10_000,
            liquidity_usd=10_000_000,
            volume_24h_usd=10_000_000,
            holder_count=99_999,
            lp_locked=True,
            price_impact_pct=0.1,
        )
        self.assertFalse(TokenScreener(config.onchain).screen(token).passed)


class MarketDataTests(unittest.TestCase):
    def test_synthetic_feed_is_deterministic(self) -> None:
        a = SyntheticFeed(["BTC/USDT"], warmup_bars=50, total_bars=200, seed=3)
        b = SyntheticFeed(["BTC/USDT"], warmup_bars=50, total_bars=200, seed=3)
        self.assertEqual(
            [bar.close for bar in a.bars("BTC/USDT")],
            [bar.close for bar in b.bars("BTC/USDT")],
        )

    def test_feed_never_exposes_future_bars(self) -> None:
        feed = SyntheticFeed(["BTC/USDT"], warmup_bars=50, total_bars=300, seed=5)
        before = len(feed.bars("BTC/USDT"))
        feed.advance(10)
        self.assertEqual(len(feed.bars("BTC/USDT")), before + 10)
        self.assertEqual(feed.mark("BTC/USDT"), feed.bars("BTC/USDT")[-1].close)


class _FlakyVenue:
    """Minimal venue stand-in whose calls fail a fixed number of times."""

    name = "flaky"
    simulated = False
    base_ccy = "USDT"

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.account_value = 1000.0

    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("network down")

    def fetch_bars(self, symbol, timeframe, limit):
        self._maybe_fail()
        return []

    def refresh_marks(self, symbols):
        self._maybe_fail()
        return {}

    def get_account(self):
        self._maybe_fail()
        return AccountState(venue=self.name, cash=self.account_value, equity=self.account_value)

    def place_order(self, request):
        raise AssertionError("should not be reached")

    def cancel_order(self, client_order_id, symbol):
        return True

    def cancel_all(self, symbols):
        return 0

    def fetch_open_orders(self, symbols):
        return []

    def drain_fills(self):
        return []

    def min_notional(self, symbol):
        return 10.0

    def account_snapshot(self):
        return {"venue": self.name, "cash": self.account_value, "positions": {}}


class ResilienceTests(unittest.TestCase):
    def test_retry_call_succeeds_after_transient_failures(self) -> None:
        attempts = {"n": 0}

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("boom")
            return "ok"

        self.assertEqual(
            retry_call(flaky, RetryPolicy(attempts=3), sleep=lambda _: None), "ok"
        )
        self.assertEqual(attempts["n"], 3)

    def test_retry_call_gives_up_as_degraded(self) -> None:
        def always_fail() -> str:
            raise ConnectionError("boom")

        with self.assertRaises(VenueDegraded):
            retry_call(always_fail, RetryPolicy(attempts=2), sleep=lambda _: None)

    def test_resilient_venue_recovers_and_counts_retries(self) -> None:
        venue = ResilientVenue(
            _FlakyVenue(fail_times=2),  # type: ignore[arg-type]
            read_policy=RetryPolicy(attempts=3),
            sleep=lambda _: None,
        )
        account = venue.get_account()
        self.assertEqual(account.cash, 1000.0)
        self.assertEqual(venue.stats()["retries"], 2)
        self.assertFalse(venue.degraded)

    def test_resilient_venue_raises_after_exhausting_retries(self) -> None:
        venue = ResilientVenue(
            _FlakyVenue(fail_times=99),  # type: ignore[arg-type]
            read_policy=RetryPolicy(attempts=2),
            sleep=lambda _: None,
        )
        with self.assertRaises(VenueDegraded):
            venue.get_account()
        self.assertTrue(venue.degraded)
        self.assertIn("failed after 2 attempt", venue.stats()["last_error"])

    def test_a_degraded_venue_pauses_entries_instead_of_killing_the_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            config = Config.load(work / "config")
            logger = setup(config.log_dir, level="CRITICAL")
            runner = TradingRunner.build(
                config,
                logger,
                venue_name="paper",
                strategy_names=["trend_pullback"],
                symbols=["BTC/USDT"],
                iterations=5,
                interval=0,
                dry_run=True,
            )
            runner._degrade("account", ConnectionError("network down"))
            self.assertTrue(runner.risk.paused)
            self.assertEqual(runner.degraded_ticks, 1)
            self.assertFalse(runner.risk.killed, "a degraded venue is not a reason to kill")
            runner.close()


class LedgerIsolationTests(unittest.TestCase):
    def test_runs_and_venues_do_not_mix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            Ledger(path, run_id="run-a", venue="okx_demo").record_fill(
                Fill("a1", "BTC/USDT", "buy", 1.0, 100.0, fee=0.1, ts=1)
            )
            Ledger(path, run_id="run-b", venue="okx_demo").record_fill(
                Fill("b1", "BTC/USDT", "buy", 2.0, 100.0, fee=0.2, ts=2)
            )
            Ledger(path, run_id="run-c", venue="paper").record_fill(
                Fill("c1", "ETH/USDT", "buy", 3.0, 10.0, fee=0.3, ts=3)
            )

            reopened = Ledger(path, run_id="run-d", venue="okx_demo")
            self.assertEqual(len(reopened.fills_for(run_id="run-a")), 1)
            self.assertEqual(len(reopened.fills_for(venue="okx_demo")), 2)
            self.assertEqual(len(reopened.fills_for(venue="paper")), 1)
            self.assertEqual(reopened.stats(run_id="run-b")["fills"], 1)
            book = reopened.rebuild_book(venue="paper", initial_cash=1000.0)
            self.assertIn("ETH/USDT", book.positions)
            self.assertNotIn("BTC/USDT", book.positions)


class ExitNeverBlockedTests(unittest.TestCase):
    """Reducing risk is always allowed, whatever the entry limits say."""

    def _engine(self, tmp: str) -> RiskEngine:
        config = Config.load(CONFIG_DIR)
        return RiskEngine(
            config.risk,
            Path(tmp) / "KILL_SWITCH",
            config.onchain,
            symbol_tiers=config.symbol_tiers,
        )

    def test_tiny_reduce_only_sell_passes_every_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            account = AccountState(venue="t", cash=100.0, equity=100.0, risk_equity=100.0)
            decision = self._engine(tmp).pre_trade(
                OrderRequest("close", "BTC/USDT", "sell", 0.00001, reduce_only=True),
                account,
                60_000.0,
                {"BTC/USDT": 60_000.0},
            )
            self.assertTrue(decision.approved)
            self.assertFalse(decision.adjusted, "an exit must never be resized")
            self.assertEqual(decision.qty, 0.00001)

    def test_exit_passes_even_when_paused_or_symbol_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            engine.pause(60, "test")
            account = AccountState(venue="t", cash=100.0, equity=100.0, risk_equity=100.0)
            decision = engine.pre_trade(
                OrderRequest("close", "DELISTED/USDT", "sell", 5.0, reduce_only=True),
                account,
                1.0,
                {"DELISTED/USDT": 1.0},
            )
            self.assertTrue(decision.approved)

    def test_kill_switch_blocks_new_risk_but_never_the_flatten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            engine.trigger_kill("test")
            account = AccountState(venue="t", cash=0.0, equity=10.0, risk_equity=10.0)
            flatten = engine.pre_trade(
                OrderRequest("flat", "BTC/USDT", "sell", 1.0, reduce_only=True),
                account,
                60_000.0,
                {"BTC/USDT": 60_000.0},
            )
            self.assertTrue(flatten.approved, "the kill switch must be able to flatten")
            entry = engine.pre_trade(
                OrderRequest("new", "BTC/USDT", "buy", 0.001), account, 60_000.0
            )
            self.assertTrue(entry.rejected)
            self.assertIn("kill_switch", entry.reason)


class BacktestTests(unittest.TestCase):
    def test_backtest_respects_the_same_risk_gate(self) -> None:
        config = Config.load(CONFIG_DIR)
        spec = config.strategy("trend_breakout")
        # this test checks the risk gate, not trend_breakout's selectivity; the
        # retest confirmation path is too selective for a 3000-bar random walk,
        # so use the plain breakout path here.
        spec.params["retest_window_bars"] = 0
        symbols = ["BTC/USDT", "ETH/USDT"]
        strategy = build_strategy(spec, config.risk, symbols)
        timeframes = ["15m", "1h", "4h"]
        feed = SyntheticFeed(
            symbols=symbols,
            timeframe="15m",
            timeframes=timeframes,
            warmup_bars=strategy.warmup + 5,
            total_bars=8_000,
            seed=17,
        )
        result = run_backtest(
            strategy,
            {s: {tf: feed.full(s, tf) for tf in timeframes} for s in symbols},
            config.risk,
            timeframe=config.universe.timeframe,
            symbol_tiers=config.symbol_tiers,
        )
        self.assertGreater(result.bars, 0)
        self.assertEqual(len(result.equity_curve), result.bars)
        self.assertGreater(result.trades, 0)
        self.assertTrue(result.exits_by_reason)
        # risk engine must have been applied: exposure can never exceed the gross cap
        self.assertLessEqual(
            max(result.equity_curve),
            result.initial_cash * (1 + config.risk.account.max_gross_exposure_pct / 100.0) * 1.5,
        )

    def test_costs_reduce_returns(self) -> None:
        config = Config.load(CONFIG_DIR)
        spec = config.strategy("trend_pullback")
        # two symbols on purpose: one symbol + this seed produced zero trades, and
        # a fee comparison of 0.0 vs 0.0 passes vacuously
        symbols = ["BTC/USDT", "ETH/USDT"]
        strategy = build_strategy(spec, config.risk, symbols)
        timeframes = ["15m", "1h"]
        feed = SyntheticFeed(
            symbols=symbols,
            timeframe="15m",
            timeframes=timeframes,
            warmup_bars=strategy.warmup + 5,
            total_bars=8_000,
            seed=23,
        )
        bars = {s: {tf: feed.full(s, tf) for tf in timeframes} for s in symbols}
        cheap = run_backtest(
            strategy, bars, config.risk, fee_multiplier=1.0, symbol_tiers=config.symbol_tiers
        )
        dear = run_backtest(
            strategy, bars, config.risk, fee_multiplier=3.0, symbol_tiers=config.symbol_tiers
        )
        self.assertGreater(cheap.trades, 0, "sample produced no trades to compare")
        self.assertGreater(dear.fees_paid, cheap.fees_paid)
        self.assertLess(dear.total_return_pct, cheap.total_return_pct)


class IntegrationTests(unittest.TestCase):
    """Runs the whole loop against a throwaway copy of the config."""

    def test_bars_evaluated_is_counted_so_quiet_loops_are_provable(self) -> None:
        """'0 signals in 9 hours' must be distinguishable from 'loop is wedged'.

        Measured 2026-10-02: the loop ran 425 ticks / 9.3 hours with zero signals
        where roughly six were expected, and there was no way to tell whether the
        strategies were being evaluated at all - `ta_signals_total` is simply
        absent in both cases. Counting evaluations makes the question answerable
        instead of a guess.
        """
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            config = Config.load(work / "config")
            logger = setup(config.log_dir, level="ERROR")
            runner = TradingRunner.build(
                config,
                logger,
                venue_name="paper",
                strategy_names=["trend_pullback"],
                symbols=["BTC/USDT"],
                iterations=200,
                interval=0,
                dry_run=True,
            )
            try:
                runner.run()
                evaluated = runner.metrics.counters.get("ta_bars_evaluated_total", 0)
                signals = runner.metrics.counters.get("ta_signals_total", 0)
                self.assertGreater(evaluated, 0, "evaluations must be observable")
                self.assertGreaterEqual(evaluated, signals)
            finally:
                runner.close()

    def test_periodic_reconciliation_catches_a_book_that_overstates_the_venue(self) -> None:
        """The bug that made the OKB exit fail must not be silent again.

        `reconcile_and_report()` only runs in the shutdown path, so a loop that
        keeps running (or is SIGKILLed) never compared the book to the exchange.
        Measured 2026-09-30: the book overstated OKB by 0.1% for over an hour and
        the exit order was rejected with `51008 insufficient balance`, with no
        reconciliation alert. This asserts the in-loop check fires.
        """
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            config = Config.load(work / "config")
            logger = setup(config.log_dir, level="ERROR")
            runner = TradingRunner.build(
                config,
                logger,
                venue_name="paper",
                strategy_names=["trend_pullback"],
                symbols=["OKB/USDT"],
                iterations=0,
                interval=0,
                dry_run=True,
            )
            try:
                self.assertTrue(
                    runner.check_reconciliation(),
                    "an untouched book must reconcile cleanly",
                )

                # The venue never saw this fill (that is exactly the failure mode
                # of a book that overstates a position).
                runner.ledger.record_fill(
                    Fill(
                        client_order_id="ghost-1",
                        symbol="OKB/USDT",
                        side="buy",
                        qty=4.979831,
                        price=120.466,
                        fee=0.6,
                        fee_ccy="USDT",
                        ts=1,
                        tag="ccxt",
                    )
                )
                self.assertFalse(runner.check_reconciliation())
                actions = runner.ledger.stats()["risk_actions"]
                self.assertIn("reconciliation mismatch", actions)
                # A repeating, unchanged mismatch must not re-alert every tick.
                before = actions.count("reconciliation mismatch")
                runner.check_reconciliation()
                after = runner.ledger.stats()["risk_actions"]
                self.assertEqual(after.count("reconciliation mismatch"), before)
            finally:
                runner.close()

    def test_dust_is_not_counted_as_an_open_position(self) -> None:
        """OKX charges spot fees in the base asset, so a "full" exit always leaves
        a sub-lot remainder. Measured 2026-09-30: 4.5e-07 OKB left after selling a
        position, against a 0.01 OKB venue minimum. That remainder can never be
        sold, but `len(account.positions)` is what enforces `max_open_positions`,
        so a handful of round trips would have silently filled the position budget
        and stopped every new entry.
        """
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            config = Config.load(work / "config")
            logger = setup(config.log_dir, level="ERROR")
            runner = TradingRunner.build(
                config,
                logger,
                venue_name="paper",
                strategy_names=["trend_pullback"],
                symbols=["OKB/USDT"],
                iterations=0,
                interval=0,
                dry_run=True,
            )
            try:
                runner.ledger.record_fill(
                    Fill(
                        client_order_id="dust-1",
                        symbol="OKB/USDT",
                        side="buy",
                        qty=4.5e-07,
                        price=120.0,
                        fee=0.0,
                        fee_ccy="USDT",
                        ts=1,
                        tag="ccxt",
                    )
                )
                raw = AccountState(
                    venue="paper",
                    base_ccy="USDT",
                    cash=10_000.0,
                    equity=10_000.0,
                    positions={
                        "OKB/USDT": Position(symbol="OKB/USDT", qty=4.5e-07, avg_price=120.0)
                    },
                )
                managed = runner._managed_account(raw)
                self.assertNotIn("OKB/USDT", managed.positions)
                self.assertIn("OKB/USDT", managed.dust_positions)
                self.assertLess(managed.dust_notional, 0.01)
            finally:
                runner.close()

    def test_paper_run_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            config = Config.load(work / "config")
            logger = setup(config.log_dir, level="ERROR")
            runner = TradingRunner.build(
                config,
                logger,
                venue_name="paper",
                strategy_names=["trend_pullback"],
                symbols=["BTC/USDT", "ETH/USDT"],
                iterations=400,
                interval=0,
                dry_run=True,
            )
            report = runner.run()
            runner.close()

            self.assertGreater(report.orders_submitted, 0, report.block_reasons)
            self.assertGreater(report.fills, 0)
            self.assertFalse(report.killed)
            self.assertEqual(report.run_id, runner.ledger.run_id)
            self.assertTrue(runner.ledger.path.exists())
            self.assertEqual(report.fills, len(runner.ledger.fills))
            book = runner.ledger.rebuild_book(initial_cash=10_000.0)
            self.assertGreaterEqual(book.cash, 0.0)

    def test_stop_loss_produces_a_protective_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            config = Config.load(work / "config")
            logger = setup(config.log_dir, level="ERROR")
            runner = TradingRunner.build(
                config,
                logger,
                venue_name="paper",
                strategy_names=["trend_pullback"],
                symbols=["BTC/USDT"],
                iterations=10,
                interval=0,
                dry_run=True,
            )
            # open a position at the simulated mark, then arm a stop above the next mark
            runner.venue.set_mark("BTC/USDT", 60_000.0)
            runner.oms.submit(OrderRequest("stop-test", "BTC/USDT", "buy", 0.001))
            from ta.runner import ExitPlan

            runner.exits["BTC/USDT"] = ExitPlan(
                symbol="BTC/USDT", entry=60_000.0, stop=59_000.0, risk_per_unit=1_000.0
            )

            before = runner.venue.get_account()
            self.assertIn("BTC/USDT", before.positions)
            runner._manage_exits(before, {"BTC/USDT": 57_500.0, "ETH/USDT": 3_000.0})

            after = runner.venue.get_account()
            self.assertNotIn("BTC/USDT", after.positions)
            self.assertIn("stop", [fill.tag for fill in runner.ledger.fills])
            exits = [e for e in runner.ledger.events if e.get("kind") == "trade_exit"]
            self.assertEqual(len(exits), 1)
            self.assertEqual(exits[0]["symbol"], "BTC/USDT")
            self.assertAlmostEqual(exits[0]["mae_r"], -2.5)
            self.assertEqual(exits[0]["mfe_r"], 0.0)
            self.assertGreater(runner.risk.cooldown_remaining_minutes("BTC/USDT"), 0)
            runner.close()


class RuntimeProvenanceTests(unittest.TestCase):
    """A release identity must bind version, source tree, and runtime config."""

    def test_config_fingerprint_changes_with_config_content(self) -> None:
        from ta.provenance import config_fingerprint

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            path = config_dir / "app.toml"
            path.write_text("mode = \"paper\"\n", encoding="utf-8")
            first, first_files = config_fingerprint(config_dir)
            second, second_files = config_fingerprint(config_dir)
            self.assertEqual(first, second)
            self.assertEqual(first_files, second_files)
            path.write_text("mode = \"live\"\n", encoding="utf-8")
            changed, _ = config_fingerprint(config_dir)
            self.assertNotEqual(first, changed)

    def test_runtime_docs_do_not_count_as_source_drift(self) -> None:
        from ta.provenance import is_source_path

        for path in ("src/ta/runner.py", "scripts/lifecycle.py", "config/risk.toml",
                     "deploy/com.tradingagent.runner.plist.template", ".github/workflows/ci.yml",
                     "VERSION", "pyproject.toml", "Makefile"):
            self.assertTrue(is_source_path(path), path)
        for path in ("README.md", "CHANGELOG.md", "docs/changelog.jsonl",
                     "docs/reviews/2026-10-26.md", "reports/live-readiness.md"):
            self.assertFalse(is_source_path(path), path)

    def test_source_fingerprint_ignores_runtime_docs_but_tracks_code(self) -> None:
        from ta.provenance import source_fingerprint

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "engine.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "README.md").write_text("release notes\n", encoding="utf-8")
            (root / "VERSION").write_text("1.1.26\n", encoding="utf-8")
            first, files = source_fingerprint(root)
            self.assertEqual({row["path"] for row in files}, {"src/engine.py", "VERSION"})
            (root / "README.md").write_text("updated runtime notes\n", encoding="utf-8")
            docs_changed, _ = source_fingerprint(root)
            self.assertEqual(first, docs_changed)
            (root / "src" / "engine.py").write_text("VALUE = 2\n", encoding="utf-8")
            code_changed, _ = source_fingerprint(root)
            self.assertNotEqual(first, code_changed)

    def test_runtime_provenance_is_atomic_and_secret_free(self) -> None:
        from types import SimpleNamespace

        from ta.provenance import collect_provenance, read_runtime_provenance
        from ta.provenance import write_runtime_provenance

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            data_dir = root / "data"
            config_dir.mkdir()
            (config_dir / "app.toml").write_text("mode = \"paper\"\n", encoding="utf-8")
            config = SimpleNamespace(
                project_dir=root,
                config_dir=config_dir,
                data_dir=data_dir,
            )
            path, payload = write_runtime_provenance(
                config, context={"command": "run", "dry_run": True}
            )
            self.assertTrue(path.exists())
            self.assertEqual(payload["schema"], 1)
            self.assertEqual(payload["context"]["command"], "run")
            self.assertTrue(payload["config_sha256"])
            self.assertNotIn("api_key", json.dumps(payload).lower())
            loaded = read_runtime_provenance(config)
            self.assertTrue(loaded["available"])
            self.assertEqual(loaded["build_id"], payload["build_id"])

            direct = collect_provenance(config, context={"command": "run"})
            self.assertEqual(len(direct["config_files"]), 1)
            self.assertEqual(direct["config_files"][0]["path"], "app.toml")


class MetricsHonestyTests(unittest.TestCase):
    """The nightly review found two metrics that could not be trusted.

    Both were verified on the live demo account on 2026-09-30, and both matter for
    the one question the daily review exists to answer: where did the money go?
    """

    def test_min_qty_falls_back_to_amount_precision(self) -> None:
        """A remainder below the venue's precision can never be sold.

        Measured 2026-10-01: the bot twice tried to sell 3.36e-07 SOL / 6.9e-08 OKB
        and OKX rejected both ("must be greater than minimum amount precision of
        0.000001"). The dust filter missed them because `min_qty` returned 0 when
        `minSz` was absent while the only other gate needs a mark, which can be 0.
        """
        from ta.venues.ccxt_venue import CcxtVenue

        venue = CcxtVenue("okx", api_key="k", secret="s", password="p", sandbox=True)
        venue.ex.markets = {"X/USDT": {}}   # the flag derives from ccxt now

        class _Ex:
            markets = {
                "OKB/USDT": {"limits": {"amount": {"min": None}}, "precision": {"amount": 1e-6}},
                "SOL/USDT": {"limits": {"amount": {"min": 0.01}}, "precision": {"amount": 1e-6}},
                "XX/USDT": {"limits": {"amount": {"min": None}}, "precision": {"amount": None}},
            }

        venue.ex = _Ex()
        self.assertAlmostEqual(venue.min_qty("OKB/USDT"), 1e-6, places=12)
        self.assertAlmostEqual(venue.min_qty("SOL/USDT"), 0.01, places=12)
        self.assertEqual(venue.min_qty("XX/USDT"), 0.0)
        self.assertEqual(venue.min_qty("MISSING/USDT"), 0.0)

    def test_an_unsellable_remainder_is_treated_as_dust_without_a_mark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            config = Config.load(work / "config")
            logger = setup(config.log_dir, level="ERROR")
            runner = TradingRunner.build(
                config,
                logger,
                venue_name="paper",
                strategy_names=["trend_pullback"],
                symbols=["BTC/USDT"],
                iterations=0,
                interval=0,
                dry_run=True,
            )
            try:
                # no mark, no venue minimum, absurdly small qty -> still dust
                self.assertTrue(
                    runner._is_unsellable(
                        "BTC/USDT", Position(symbol="BTC/USDT", qty=3.36e-07, avg_price=0.0)
                    )
                )
                self.assertFalse(
                    runner._is_unsellable(
                        "BTC/USDT", Position(symbol="BTC/USDT", qty=0.01, avg_price=0.0)
                    )
                )
                # Swap quantity is contracts. A $2,050 SHIB hedge must not be
                # mistaken for $0.002 because its coin price has many decimals.
                self.assertFalse(
                    runner._is_unsellable(
                        "SHIB/USDT:USDT",
                        Position(
                            symbol="SHIB/USDT:USDT",
                            qty=-410.1,
                            avg_price=0.000005,
                            ct_val=1_000_000.0,
                        ),
                    )
                )
                self.assertTrue(
                    runner._is_unsellable(
                        "TINY/USDT:USDT",
                        Position(
                            symbol="TINY/USDT:USDT",
                            qty=-1.0,
                            avg_price=0.01,
                            ct_val=1.0,
                        ),
                    )
                )
                self.assertTrue(
                    runner._is_unsellable(
                        "BTC/USDT:USDT",
                        Position(
                            symbol="BTC/USDT:USDT",
                            qty=-0.001,
                            avg_price=80_000.0,
                            ct_val=0.01,
                        ),
                    )
                )
            finally:
                runner.close()

    def test_ledger_reports_lifetime_fees(self) -> None:
        """`ta_fees_total` read `AccountState.fees_paid`, which the live venue never
        populates - so it showed 0.00 while fees were 45% of a $63.50 loss."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            ledger = Ledger(path, run_id="r1", venue="okx_demo")
            self.assertEqual(ledger.fees_paid, 0.0)
            ledger.record_fill(Fill("a", "OKB/USDT", "buy", 1.0, 100.0, fee=0.1, ts=1))
            ledger.record_fill(Fill("b", "OKB/USDT", "sell", 1.0, 101.0, fee=0.101, ts=2))
            self.assertAlmostEqual(ledger.fees_paid, 0.201, places=9)
            # and it survives a restart, because it is rebuilt from the fills
            reopened = Ledger(path, run_id="r2", venue="okx_demo")
            self.assertAlmostEqual(reopened.fees_paid, 0.201, places=9)

    def test_entries_paused_metric_reflects_the_risk_engine(self) -> None:
        """There was no metric at all for "is anything blocking new entries"."""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            config = Config.load(work / "config")
            logger = setup(config.log_dir, level="ERROR")
            runner = TradingRunner.build(
                config,
                logger,
                venue_name="paper",
                strategy_names=["trend_pullback"],
                symbols=["BTC/USDT"],
                iterations=0,
                interval=0,
                dry_run=True,
            )
            try:
                self.assertFalse(runner.risk.paused)
                runner.check_reconciliation()
                runner._record_metrics(runner.venue.get_account())
                self.assertEqual(runner.metrics.gauges.get("ta_entries_paused"), 0.0)

                runner._degrade("market data", ConnectionError("boom"))
                self.assertTrue(runner.risk.paused)
                runner._record_metrics(runner.venue.get_account())
                self.assertEqual(runner.metrics.gauges.get("ta_entries_paused"), 1.0)
                # the venue-degraded gauge must reflect THIS tick, not latch on
                self.assertEqual(runner.metrics.gauges.get("ta_venue_degraded"), 1.0)
            finally:
                runner.close()


class DashboardTests(unittest.TestCase):
    """The operations dashboard is read-only reporting, so it must be exact.

    Every number on the page is either taken from a file or derived here; a wrong
    derivation would make the page confidently lie, which is worse than no page.
    """

    def _data(self):
        from ta.dashboard import DashboardData

        return DashboardData(CONFIG_DIR)

    def test_alert_thresholds_come_from_the_config_file(self) -> None:
        """Thresholds are data, not code: editing alerts.json must be enough."""
        import json as _json

        from ta.dashboard import DEFAULT_THRESHOLDS, DashboardData

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            (work / "config" / "alerts.json").write_text(
                _json.dumps({"max_drawdown_pct": 1.25, "bogus_key": 99}), encoding="utf-8"
            )
            thresholds = DashboardData(work / "config").thresholds()
            self.assertAlmostEqual(thresholds["max_drawdown_pct"], 1.25, places=6)
            self.assertNotIn("bogus_key", thresholds, "unknown keys are ignored")
            self.assertEqual(
                thresholds["daily_loss_pct"], DEFAULT_THRESHOLDS["daily_loss_pct"]
            )

    def test_thresholds_fall_back_when_the_file_is_corrupt(self) -> None:
        from ta.dashboard import DEFAULT_THRESHOLDS, DashboardData

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            (work / "config" / "alerts.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(DashboardData(work / "config").thresholds(), DEFAULT_THRESHOLDS)

    def test_the_dashboard_says_the_bill_based_funding_is_a_demo_rate(self) -> None:
        """2026-10-12 定性：模拟盘的资金费与实盘差两个数量级（CRV 实测 248 倍），
        所以看板必须自己把两个口径并排说出来 —— 否则下一个人还会拿账单当收益率。

        造一个小项目：账本里 24 小时内有一笔 $40 的账单，而按公开费率只该值 $0.02。
        """
        import datetime
        import json as _json

        from ta.dashboard import DashboardData

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            (work / "data" / "history" / "funding").mkdir(parents=True)
            now = datetime.datetime.now(datetime.UTC)
            when = int(now.timestamp() * 1000)
            (work / "data" / "history" / "funding" / "AAA-USDT_USDT.jsonl").write_text(
                _json.dumps({"ts": when - 3600_000, "rate": 0.0001}) + "\n",
                encoding="utf-8")
            rows = [
                {"kind": "equity", "ts": when, "equity": 1000.0,
                 "positions": {"AAA/USDT": 100.0, "AAA/USDT:USDT": -100.0},
                 "marks": {"AAA/USDT": 2.0}},
                {"kind": "funding", "ts": when, "symbol": "AAA/USDT:USDT", "amount": 40.0},
            ]
            (work / "data" / f"ledger.okx_demo.jsonl").write_text(
                "\n".join(_json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            data = DashboardData(work / "config")
            basis = data.funding_basis()
            self.assertTrue(basis["available"], basis)
            self.assertAlmostEqual(basis["demo_usd"], 40.0, places=4)   # 账单口径
            self.assertAlmostEqual(basis["live_usd"], 0.02, places=4)   # 公开费率口径
            self.assertGreater(basis["ratio"], 1000)
            titles = [f["title"] for f in data.redflags()]
            self.assertIn("资金费口径：账单是模拟盘费率", titles)
            detail = [f["detail"] for f in data.redflags()
                      if f["title"] == "资金费口径：账单是模拟盘费率"][0]
            self.assertIn("公开费率口径", detail)
            self.assertIn("只能用公开费率", detail)

    def test_history_flags_get_quieter_instead_of_staying_critical_forever(self) -> None:
        """2026-10-12：三条扫**全量历史**的检查（幽灵卖单 / 接管成交 / 熔断与强平）
        原来是「一旦发生过就永远报同一级别」——那是把**审计发现**当**现状**报，
        会训练出「红旗看多了就不看」。改成**新鲜度决定音量**：24 小时内按原级别，
        更早降为 info 并带「最近一次」的日期（不隐藏，只降音量）。
        """
        import datetime
        import json as _json

        from ta.dashboard import DashboardData

        def ledger_rows(age_hours: float) -> list[dict]:
            when = int((datetime.datetime.now(datetime.UTC)
                        - datetime.timedelta(hours=age_hours)).timestamp() * 1000)
            return [
                {"kind": "equity", "ts": when, "equity": 1000.0},
                {"kind": "order", "ts": when, "symbol": "BTC/USDT", "side": "sell",
                 "qty": 1.0, "tag": "manual"},
                {"kind": "fill", "ts": when, "symbol": "BTC/USDT", "side": "buy",
                 "qty": 1.0, "tag": "adopted"},
                {"kind": "risk", "ts": when, "action": "killed",
                 "reason": "kill switch file present on disk"},
            ]

        def levels(age_hours: float) -> dict[str, str]:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "project"
                (work / "config").mkdir(parents=True)
                for toml in CONFIG_DIR.glob("*.toml"):
                    shutil.copy(toml, work / "config" / toml.name)
                (work / "data").mkdir(parents=True, exist_ok=True)
                (work / "data" / "ledger.okx_demo.jsonl").write_text(
                    "\n".join(_json.dumps(r) for r in ledger_rows(age_hours)) + "\n",
                    encoding="utf-8")
                return {f["title"]: f["level"]
                        for f in DashboardData(work / "config").redflags()}

        fresh = levels(1.0)
        self.assertEqual(fresh.get("幽灵卖单"), "critical")
        self.assertEqual(fresh.get("出现接管成交"), "warn")
        self.assertEqual(fresh.get("killed"), "critical")

        old = levels(72.0)                       # 三天前：只存档，不再占 critical/warn
        self.assertNotIn("幽灵卖单", old)
        self.assertEqual(old.get("幽灵卖单（历史）"), "info")
        self.assertEqual(old.get("接管成交（历史）"), "info")
        self.assertEqual(old.get("killed（历史）"), "info")

    def test_the_options_panel_reads_the_latest_gate_and_admits_the_environment(self) -> None:
        """2026-10-12：闸门**首次开启**，但执行能力是**环境问题**（README §69：这套模拟
        环境买不到期权）。面板必须同时说出这两件事 —— 我自己就是因为没在同一个地方看到
        它们，把一件已结案的事（换 ~$100 USDT→BTC）又做了一遍，净成本 $0.27。
        """
        import json as _json

        from ta.dashboard import DashboardData

        def build(lines: list[dict]) -> dict:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "project"
                (work / "config").mkdir(parents=True)
                for toml in CONFIG_DIR.glob("*.toml"):
                    shutil.copy(toml, work / "config" / toml.name)
                (work / "data" / "options").mkdir(parents=True, exist_ok=True)
                (work / "data" / "options" / "gate.jsonl").write_text(
                    "\n".join(_json.dumps(x) for x in lines) + "\n", encoding="utf-8")
                return DashboardData(work / "config").options()

        closed = {"generated_at": "2026-10-11T01:22:13+00:00", "days": 5, "min_edge_pp": 1.5,
                  "families": {"BTC-USD": {"run_days": 4, "latest_rv_minus_iv_pp": 9.79,
                                           "open": False, "series": []}},
                  "open": []}
        opened = {"generated_at": "2026-10-12T01:22:13+00:00", "days": 5, "min_edge_pp": 1.5,
                  "families": {"BTC-USD": {"run_days": 5, "latest_rv_minus_iv_pp": 6.87,
                                           "open": True,
                                           "series": [["2026-10-12", 6.87]]}},
                  "open": ["BTC-USD"]}
        got = build([closed, opened])              # 只认最后一条判决
        self.assertTrue(got["available"])
        self.assertEqual(got["open"], ["BTC-USD"])
        self.assertEqual(got["families"][0]["run_days"], 5)
        self.assertEqual(got["families"][0]["latest_pct"], 6.87)
        self.assertFalse(got["executable"])        # 环境事实，与闸门开不开无关
        self.assertIn("买不到期权", got["executable_reason"])
        self.assertEqual(build([])["available"], False)   # 没有记录 → 说没有，不猜

    def test_a_basis_change_is_not_mistaken_for_drift(self) -> None:
        """2026-10-12：我们**换了期望口径**（把资金费并进 `our flow`）之后，看板喊了
        "现金对账残余在漂移"，窗口波动正是那次跳跃的 $248.85 —— 但那是模型变了，不是钱在动。

        `cash_series` 把每个周期的 `(our flow, venue cash)` 拿出来，`basis_step_index` 找
        最后一个「flow 跳了而 venue 没动」的点；切掉它之前的那一段，残余序列就只在新口径内比较。
        """
        from ta.dashboard import basis_step_index, cash_residual_flag, cash_residuals
        from ta.dashboard import cash_series as series_of

        def event(flow: float, venue: float, ts: int) -> dict:
            return {
                "kind": "risk", "action": "reconciliation mismatch", "ts": ts,
                "diffs": [f"cash delta: expected {103358.46688886615 + flow} "
                          f"(baseline 103358.46688886615 + our flow {flow}) "
                          f"but venue shows {venue}"],
            }

        # 四小时：资金费每小时到账 $10（flow 与 venue 一起动）→ 残余稳定在 $830
        normal = [event(-11000 - 10 * i, 103358.46688886615 - 11000 - 10 * i - 830, i)
                  for i in range(4)]
        self.assertEqual(basis_step_index(series_of(normal)), 0)      # 没有换过口径
        self.assertEqual(cash_residual_flag(cash_residuals(normal))[0], "info")

        # 第五个小时：我们换了口径（flow 跳 +250）、场馆一动没动 → 残余跳到 $1,080
        stepped = normal + [event(-11000 - 40 + 250,
                                  103358.46688886615 - 11000 - 40 - 830, 4)]
        cut = basis_step_index(series_of(stepped))
        self.assertEqual(cut, 4)
        # 不切：窗口里混着两种口径 → 被喊"在漂移"（这正是实测发生的事）
        self.assertEqual(cash_residual_flag(cash_residuals(stepped))[0], "warn")
        # 切掉换口径之前那一段：只有新口径的数据 → 稳定
        level, detail = cash_residual_flag(cash_residuals(stepped[cut:]))
        self.assertEqual(level, "info")
        self.assertIn("波动 $0.00", detail)

    def test_a_position_diff_flag_expires_instead_of_sticking_forever(self) -> None:
        """2026-10-12 我自己造出的一次：外部换币 → 场馆有 BTC、回放账本没有 → 位置差 →
        卖回后两边已经一致，但看板那条 warn 还挂着。

        根因：这条标志读的是**最后一条** `reconciliation mismatch` 事件，而 runner 只在
        签名变化时记录（成功时不记）→ 历史差会永远显示。修法：加新鲜度约束 —— 签名里含
        场馆现金值、现金随资金费约每小时变一次，所以真还在不一致的会每小时刷新，已解决的
        不会。这里造一旧一新两条，只有新的该报。
        """
        import datetime
        import json as _json

        from ta.dashboard import DashboardData

        def ledger_with(age_hours: float) -> list[dict]:
            when = int((datetime.datetime.now(datetime.UTC)
                        - datetime.timedelta(hours=age_hours)).timestamp() * 1000)
            return [
                {"kind": "equity", "ts": when, "equity": 1000.0},
                {"kind": "risk", "ts": when, "action": "reconciliation mismatch",
                 "diffs": ["position BTC/USDT: local=2.5e-09 venue=0.0011948225",
                           "cash delta: expected 1 but venue shows 2"]},
            ]

        def flags_for(age_hours: float) -> list[str]:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "project"
                (work / "config").mkdir(parents=True)
                for toml in CONFIG_DIR.glob("*.toml"):
                    shutil.copy(toml, work / "config" / toml.name)
                (work / "data").mkdir(parents=True, exist_ok=True)
                (work / "data" / "ledger.okx_demo.jsonl").write_text(
                    "\n".join(_json.dumps(r) for r in ledger_with(age_hours)) + "\n",
                    encoding="utf-8")
                return [f["title"] for f in DashboardData(work / "config").redflags()]

        self.assertIn("仓位对账不一致", flags_for(0.5))      # 新鲜 → 报
        self.assertNotIn("仓位对账不一致", flags_for(3.0))   # 3 小时前 → 不再声称它还在

    def test_concurrent_snapshot_requests_share_one_build(self) -> None:
        """2026-10-12：页面每 15s 轮询、慢的时候堆到 5 条并发（lsof 上看得见 5 条
        ESTABLISHED），彼此拖慢到 60~190 秒。修法是 HTTP 层用 `snapshot_cached()`：
        TTL 内吃缓存，过期时只有**一个**线程构建，其余等它 —— 并发不再放大成本。
        """
        import threading as _threading
        import time as _time

        from ta.dashboard import DashboardData

        data = DashboardData(CONFIG_DIR)
        calls = {"n": 0}
        lock = _threading.Lock()

        def fake_snapshot():
            with lock:
                calls["n"] += 1
            _time.sleep(0.05)                  # 假装这是一次真实的构建
            return {"generated_at": calls["n"]}

        data.snapshot = fake_snapshot          # type: ignore[method-assign]
        results: list[dict] = []
        rlock = _threading.Lock()

        def one() -> None:
            payload = data.snapshot_cached(ttl=5.0)
            with rlock:
                results.append(payload)

        threads = [_threading.Thread(target=one) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(calls["n"], 1, "6 个并发只该构建一次")
        self.assertEqual(len(results), 6)
        self.assertEqual({r["generated_at"] for r in results}, {1})
        # TTL 内再请求：还是那一次构建
        data.snapshot_cached(ttl=5.0)
        self.assertEqual(calls["n"], 1)
        # TTL 过期后：重新构建
        data.snapshot_cached(ttl=0.0)
        self.assertEqual(calls["n"], 2)

    def _stub_summary(self, data, **over: object) -> None:
        base = {
            "drawdown_pct": 0.0,
            "daily_pnl_pct": 0.0,
            "fees_pct_of_loss": 0.0,
            "unmanaged_notional": 0.0,
            "equity": 100_000.0,
        }
        base.update(over)
        data.summary = lambda: dict(base)  # type: ignore[method-assign]
        data.onchain = lambda: {"zero_change": {"unchanged_fraction": 0.0}}  # type: ignore[method-assign]

    def test_a_stalled_loop_is_flagged(self) -> None:
        """2026-10-10: the proxy's upstream died and NOTHING alerted.

        Every market-data call failed for ~25 minutes, but `ta_venue_degraded_total`
        stayed at 1 (it counts venue-level transitions, and `fetch_bars` fails per
        symbol). The page looked calm while the loop had stopped making decisions
        entirely - no entries, no exits, no stop enforcement.
        """
        import time as _time

        data = self._data()
        self._stub_summary(data, as_of=int((_time.time() - 30 * 60) * 1000))
        breach = next((b for b in data.alert_breaches() if b["key"] == "runner_stale_minutes"), None)
        self.assertIsNotNone(breach, "a 30-minute-old tick must be reported")
        self.assertEqual(breach["level"], "critical")

    def test_a_recent_tick_is_not_a_breach(self) -> None:
        import time as _time

        data = self._data()
        self._stub_summary(data, as_of=int((_time.time() - 2 * 60) * 1000))
        keys = {b["key"] for b in data.alert_breaches()}
        self.assertNotIn("runner_stale_minutes", keys)

    def test_an_empty_ledger_does_not_look_stalled(self) -> None:
        # A fresh install (no equity event yet) must not raise a critical breach.
        data = self._data()
        self._stub_summary(data, as_of=0)
        keys = {b["key"] for b in data.alert_breaches()}
        self.assertNotIn("runner_stale_minutes", keys)

    def _stub_onchain_age(self, data, minutes: float) -> None:
        """把「快照文件多久没动」设成给定值（stub 掉 onchain 的实时读取）。"""
        import time as _time

        data.onchain = lambda: {  # type: ignore[method-assign]
            "zero_change": {"unchanged_fraction": 0.0},
            "store_mtime": int((_time.time() - minutes * 60) * 1000),
        }

    def test_a_stalled_scanner_is_flagged(self) -> None:
        """2026-10-16：扫描器静默停摆 ~2 小时，**没有任何红标** —— 那条「mtime 超过
        10 分钟就算异常」只写在巡检指令里，不是判据。现在看门狗自己判：
        ≥15 分钟（3× 轮间隔）warn，≥30 分钟 critical。"""
        data = self._data()
        self._stub_summary(data)
        self._stub_onchain_age(data, 20)
        breach = next((b for b in data.alert_breaches() if b["key"] == "onchain_stale_minutes"), None)
        self.assertIsNotNone(breach, "20 分钟没落盘必须报出来")
        self.assertEqual(breach["level"], "warn")

    def test_a_long_scanner_stall_escalates_to_critical(self) -> None:
        data = self._data()
        self._stub_summary(data)
        self._stub_onchain_age(data, 45)
        breach = next((b for b in data.alert_breaches() if b["key"] == "onchain_stale_minutes"), None)
        self.assertIsNotNone(breach)
        self.assertEqual(breach["level"], "critical")

    def test_a_fresh_or_unknown_scanner_is_not_a_breach(self) -> None:
        data = self._data()
        self._stub_summary(data)
        self._stub_onchain_age(data, 1)
        self.assertNotIn("onchain_stale_minutes", {b["key"] for b in data.alert_breaches()})
        # 没有 store_mtime（还没跑过 / 文件不存在）→ 不报；「不知道」不等于「坏了」
        data = self._data()
        self._stub_summary(data)
        self.assertNotIn("onchain_stale_minutes", {b["key"] for b in data.alert_breaches()})

    def test_a_breached_threshold_is_reported_and_sorted_first(self) -> None:
        """A breach must reach the page, and must outrank the informational flags."""
        data = self._data()
        data.summary = lambda: {  # type: ignore[method-assign]
            "drawdown_pct": 9.5,
            "daily_pnl_pct": -3.1,
            "fees_pct_of_loss": 10.0,
            "unmanaged_notional": 0.0,
            "equity": 100_000.0,
        }
        data.onchain = lambda: {"zero_change": {"unchanged_fraction": 0.0}}  # type: ignore[method-assign]
        breaches = data.alert_breaches()
        keys = {b["key"] for b in breaches}
        self.assertIn("max_drawdown_pct", keys)
        self.assertIn("daily_loss_pct", keys)
        self.assertNotIn("fees_pct_of_loss", keys, "10% is under the 60% default")
        self.assertTrue(all(b["level"] == "critical" for b in breaches))
        flags = data.redflags()
        self.assertEqual(flags[0]["level"], "critical", "breaches sort above info flags")

    def test_push_alerts_is_a_noop_without_a_webhook_url(self) -> None:
        import os as _os

        data = self._data()
        saved = _os.environ.pop("TA_ALERT_WEBHOOK_URL", None)
        try:
            self.assertFalse(data.push_alerts([{"key": "x", "level": "critical"}]))
            self.assertFalse(data.push_alerts([]))
        finally:
            if saved is not None:
                _os.environ["TA_ALERT_WEBHOOK_URL"] = saved

    def test_fee_ratio_and_onchain_drift_are_reported(self) -> None:
        data = self._data()
        data.summary = lambda: {  # type: ignore[method-assign]
            "drawdown_pct": 0.0,
            "daily_pnl_pct": 0.0,
            "fees_pct_of_loss": 95.0,
            "unmanaged_notional": 0.0,
            "equity": 100_000.0,
        }
        data.onchain = lambda: {"zero_change": {"unchanged_fraction": 0.99}}  # type: ignore[method-assign]
        kinds = {f["title"] for f in data.redflags()}
        self.assertIn("手续费占亏损比超阈值", kinds)
        self.assertIn("链上样本零变动占比过高", kinds)

    def test_partial_exits_are_one_trade_not_two(self) -> None:
        """Banking 60% at 1R and trailing the rest is ONE position.

        Splitting them would double-count the entry fee and halve the apparent risk
        per trade - and OKX charges the spot fee in the base asset, so a "full"
        exit always leaves ~0.01% behind, which must not keep the trade open.
        """
        from ta.dashboard import round_trips

        fills = [
            {"ts": 1, "symbol": "OKB/USDT", "side": "buy", "qty": 4.983562449, "price": 120.0, "fee": 0.6, "tag": "ccxt"},
            {"ts": 2, "symbol": "OKB/USDT", "side": "sell", "qty": 3.0, "price": 121.0, "fee": 0.36, "tag": "partial"},
            {"ts": 3, "symbol": "OKB/USDT", "side": "sell", "qty": 1.983562, "price": 119.0, "fee": 0.24, "tag": "stop"},
        ]
        trades = round_trips(fills)
        self.assertEqual(len(trades), 1, "a partial exit must not split the position")
        trade = trades[0]
        self.assertAlmostEqual(trade["cost"], 4.983562449 * 120.0, places=4)
        self.assertAlmostEqual(trade["proceeds"], 3.0 * 121.0 + 1.983562 * 119.0, places=4)
        self.assertAlmostEqual(trade["fees"], 0.6 + 0.36 + 0.24, places=6)
        self.assertAlmostEqual(
            trade["net"], trade["proceeds"] - trade["cost"] - trade["fees"], places=4
        )

    def test_trade_fees_reconcile_with_the_fill_log(self) -> None:
        from ta.dashboard import round_trips

        fills = [
            {"ts": 1, "symbol": "OKB/USDT", "side": "buy", "qty": 1.0, "price": 100.0, "fee": 0.1},
            {"ts": 2, "symbol": "OKB/USDT", "side": "sell", "qty": 1.0, "price": 110.0, "fee": 0.11},
            {"ts": 3, "symbol": "JUP/USDT", "side": "buy", "qty": 2.0, "price": 50.0, "fee": 0.1},
            {"ts": 4, "symbol": "JUP/USDT", "side": "sell", "qty": 2.0, "price": 45.0, "fee": 0.09},
        ]
        trades = round_trips(fills)
        self.assertEqual(len(trades), 2)
        self.assertAlmostEqual(sum(t["fees"] for t in trades), 0.4, places=9)
        self.assertAlmostEqual(sum(t["net"] for t in trades), (10.0 - 0.21) + (-10.0 - 0.19), places=6)

    def test_a_sell_without_a_local_entry_is_ignored(self) -> None:
        """A manual sale must not be scored as a -100% or +inf bot trade."""
        from ta.dashboard import round_trips

        trades = round_trips(
            [{"ts": 1, "symbol": "OKB/USDT", "side": "sell", "qty": 100.0, "price": 120.0, "fee": 1.2}]
        )
        self.assertEqual(trades, [])

    def test_read_jsonl_skips_malformed_lines_instead_of_raising(self) -> None:
        from ta.dashboard import read_jsonl

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.jsonl"
            path.write_text('{"a": 1}\n\nnot json\n{"a": 2}\n', encoding="utf-8")
            self.assertEqual(read_jsonl(path), [{"a": 1}, {"a": 2}])

    def test_parse_metrics_reads_the_prometheus_text_format(self) -> None:
        from ta.dashboard import parse_metrics

        text = (
            "# TYPE ta_equity gauge\n"
            "ta_equity 103280.44\n"
            "# TYPE ta_ticks_total counter\n"
            "ta_ticks_total 351.0\n"
            "garbage line\n"
        )
        parsed = parse_metrics(text)
        self.assertAlmostEqual(parsed["ta_equity"], 103280.44, places=2)
        self.assertEqual(parsed["ta_ticks_total"], 351.0)
        self.assertNotIn("garbage", parsed)

    def test_timeline_normalises_ledger_milliseconds_and_alert_iso(self) -> None:
        from ta.dashboard import merge_timeline

        ledger = [
            {"kind": "order", "ts": 1_700_000_000_000, "symbol": "OKB/USDT", "side": "buy", "qty": 1.0, "status": "filled"},
            {"kind": "equity", "ts": 1, "equity": 1.0},  # must be dropped as noise
        ]
        alerts = [{"ts": "2023-11-14T22:13:20+00:00", "level": "warning", "title": "protective exit"}]
        rows = merge_timeline(ledger, alerts)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(isinstance(r["ts"], int) and r["ts"] > 0 for r in rows))
        self.assertEqual(rows[0]["ts"], rows[1]["ts"])  # same instant, newest first
        self.assertIn("protective exit", [r["text"] for r in rows])

    def test_dashboard_snapshot_builds_from_the_real_project(self) -> None:
        from ta.dashboard import DashboardData

        data = DashboardData(CONFIG_DIR)
        snapshot = data.snapshot()
        for key in ("summary", "equity", "trades", "timeline", "strategies", "risk",
                    "audit", "artifacts", "refit", "onchain", "redflags"):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot["venue"] if "venue" in snapshot else "okx_demo", "okx_demo")
        self.assertTrue(snapshot["strategies"], "strategies.toml must render")
        # the page must never claim a limit it cannot express in dollars
        self.assertIn("max_order_majors", snapshot["risk"]["in_usd"])

    def test_audit_changelog_is_append_only_and_valid(self) -> None:
        from ta.dashboard import read_jsonl

        rows = read_jsonl(PROJECT / "docs" / "changelog.jsonl")
        self.assertGreater(len(rows), 10, "the audit trail must be seeded")
        for row in rows:
            self.assertIn(row.get("kind"), {"bug", "decision", "research", "ops", "strategy"})
            self.assertTrue(row.get("title"))
            self.assertRegex(str(row.get("ts")), r"^\d{4}-\d{2}-\d{2}T")


class HistoryPagingTests(unittest.TestCase):
    pass


class PairedExecutionTests(unittest.TestCase):
    pass


class FeedIsolationTests(unittest.TestCase):
    """One bad symbol must not suspend the whole book.

    Measured 2026-10-08: the runner's refresh walked its symbol list, A/USDT
    failed first every tick, the exception aborted the refresh, no other symbol
    was ever fetched, the venue's GLOBAL consecutive-failure counter climbed to
    42 and the loop suspended every new entry - one symbol holding the other 60
    hostage. A single unnamed listing should cost that listing, not the book.
    """

    class _Venue:
        def __init__(self, bad: set[str]):
            self.bad = bad
            self.calls: list[str] = []

        def fetch_bars(self, symbol, timeframe, limit):
            self.calls.append(symbol)
            if symbol in self.bad:
                raise RuntimeError(f"{symbol}: venue refuses")
            from ta.models import Bar
            # two bars: the feed drops the newest (it may still be forming)
            return [
                Bar(ts=1_700_000_000_000, open=1, high=1, low=1, close=1, volume=1),
                Bar(ts=1_700_000_900_000, open=1, high=1, low=1, close=1, volume=1),
            ]

        def refresh_marks(self, symbols):
            return {s: 1.0 for s in symbols}

    def _feed(self, bad: set[str]):
        from ta.marketdata import LiveFeed

        venue = self._Venue(bad)
        return LiveFeed(venue, "15m", 300, timeframes=["15m"]), venue

    def test_a_failing_symbol_is_skipped_and_the_rest_still_fetch(self) -> None:
        feed, venue = self._feed({"A/USDT"})
        feed.refresh(["A/USDT", "BTC/USDT", "ETH/USDT"], force=True)
        self.assertEqual(feed.last_failed_symbols, ["A/USDT"])
        self.assertIn("BTC/USDT", venue.calls)
        self.assertIn("ETH/USDT", venue.calls)
        self.assertTrue(feed.bars("BTC/USDT"), "the healthy symbol must have data")

    def test_a_clean_refresh_reports_no_failures(self) -> None:
        feed, _ = self._feed(set())
        feed.refresh(["BTC/USDT"], force=True)
        self.assertEqual(feed.last_failed_symbols, [])


class LiveFeedFundingTests(unittest.TestCase):
    """The feed must read funding from the venue that HAS it.

    Measured 2026-10-08: with 48 qualifying carry candidates live and the runner
    restarted, positions stayed at 0. The reason was here - the strategy asks for
    funding on the SPOT symbol, the feed asked the SPOT venue, spot has no funding,
    and the strategy's fail-closed rule then did exactly what it promises: nothing.
    No error, no alert, just zero trades.
    """

    class _Venue:
        def __init__(self, rates):
            self.rates = rates
            self.asked: list[str] = []

        def fetch_funding_rate(self, symbol):
            self.asked.append(symbol)
            return self.rates.get(symbol)

    def _feed(self, spot_rates, perp_rates=None):
        from ta.marketdata import LiveFeed

        spot = self._Venue(spot_rates)
        feed = LiveFeed(spot, "15m", 300)
        if perp_rates is not None:
            feed.funding_venue = self._Venue(perp_rates)
        return feed, spot

    def test_without_a_funding_venue_a_spot_symbol_has_no_rate(self) -> None:
        feed, _ = self._feed({})
        self.assertIsNone(feed.funding_rate("WLFI/USDT"))

    def test_a_funding_venue_is_asked_for_the_perp_symbol(self) -> None:
        feed, _ = self._feed({}, {"WLFI/USDT:USDT": 0.0002})
        self.assertAlmostEqual(feed.funding_rate("WLFI/USDT"), 0.0002)
        self.assertEqual(feed.funding_venue.asked, ["WLFI/USDT:USDT"])

    def test_the_answer_is_cached_per_symbol(self) -> None:
        feed, _ = self._feed({}, {"SOL/USDT:USDT": 0.0001})
        feed.funding_rate("SOL/USDT")
        feed.funding_rate("SOL/USDT")
        self.assertEqual(len(feed.funding_venue.asked), 1, "one venue call per tick")

    def test_a_venue_error_is_reported_as_no_funding_not_raised(self) -> None:
        from ta.marketdata import LiveFeed

        class Boom:
            def fetch_funding_rate(self, symbol):
                raise RuntimeError("venue down")

        feed = LiveFeed(Boom(), "15m", 300)
        self.assertIsNone(feed.funding_rate("SOL/USDT"))


class SettlementIntervalTests(unittest.TestCase):
    """结算频率这件事：**单位错了，比较就不成立**（2026-10-13）。

    `carry_rotation.daily_bps_series` 一直把 `settlements_per_day` 固定成 **3.0**（8 小时），
    而场馆对一部分合约是 **4 小时结算（6 次/天）**。实测持仓腿里的 **ACT 就是 6/天** ——
    修正前它报 **1.500 bps/天**（离 1.0 的 floor 只差 0.5，是"最像要死"的那条腿），
    修正后是 **3.000**。被低估的方向很要命：腿看起来更接近死掉（更容易被换掉，一次 44.9 bps），
    而同样 4h 的候选看起来更不值钱（该换的不换）。
    """

    @staticmethod
    def _prints(gap_hours: float, count: int = 30, start_hour: float = 0.0) -> list[dict]:
        base = 1_790_000_000_000
        step = int(gap_hours * 3_600_000)
        return [{"ts": base + int(start_hour * 3_600_000) + i * step, "rate": 0.0005}
                for i in range(count)]

    def test_the_interval_is_measured_not_assumed(self) -> None:
        from scripts.carry_rotation import detect_settlements_per_day

        self.assertEqual(detect_settlements_per_day(self._prints(8.0)), 3.0)
        self.assertEqual(detect_settlements_per_day(self._prints(4.0)), 6.0)
        self.assertEqual(detect_settlements_per_day(self._prints(1.0)), 24.0)
        # 漏抓留下大空洞，均值会被拖走 —— 中位数不会
        gappy = self._prints(8.0, count=10) + [
            {"ts": self._prints(8.0, count=1)[0]["ts"] + 30 * 86_400_000, "rate": 0.0005}]
        self.assertEqual(detect_settlements_per_day(gappy), 3.0)
        self.assertEqual(detect_settlements_per_day([{"ts": 1}, {"ts": 2}]), 3.0)  # 印太少

    def test_a_four_hour_leg_is_no_longer_halved(self) -> None:
        """回归：ACT 那种 4h 腿的中位必须翻倍 —— 这是"离 floor 多远"的直接判据。"""
        import statistics

        from scripts.carry_rotation import daily_bps_series, detect_settlements_per_day

        prints = self._prints(4.0, count=18)      # 4h × 18 = 3 天
        old = daily_bps_series(prints, 3, 3.0)             # 旧的统一假设
        new = daily_bps_series(prints, 3, detect_settlements_per_day(prints))
        # rate=0.0005（0.05%/次）× 10000 bps：3 次/天 → 15.0，6 次/天 → 30.0
        self.assertEqual(statistics.median([b for _d, b, _n in old]), 15.0)
        self.assertEqual(statistics.median([b for _d, b, _n in new]), 30.0)

    def test_it_agrees_with_the_screens_own_helper(self) -> None:
        """屏幕那边（`carry_live`）早就有同功能的函数 —— 两份实现必须当场对齐，
        否则迟早分叉（本项目已经吃过一次"两把尺子"的亏）。"""
        from scripts.carry_live import settlements_per_day
        from scripts.carry_rotation import detect_settlements_per_day

        for gap in (1.0, 2.0, 4.0, 8.0):
            stamps = [p["ts"] for p in self._prints(gap, count=20)]
            self.assertEqual(settlements_per_day(stamps)[0], detect_settlements_per_day(
                [{"ts": t} for t in stamps]), f"gap={gap}h 两份实现不一致")


class CarryRotationFreshnessTests(unittest.TestCase):
    """换腿规则两侧的「尺子」与「新鲜度」（2026-10-13 两轮改动）。

    第一轮查出：候选读的快照 `screen_live.json` **没有任何定时任务在产出**（只有手跑
    `carry_live.py --write`），而 `daily_bps_series` 取的是「最后 N 个**有印**的日子」，
    冻住的序列照样给出三个正常形状的桶 —— 于是加了时效护栏（过期就拒绝出提议）。

    第二轮（用户批准执行选项 ①+②）改成：**候选的 bps 从本地费率历史、按与现任相同的窗口
    重算**，快照只提供深度/资格过滤（且只在快照够新时用）；候选侧加**与现任对称的持续性检验**
    —— 现任要「连续 N 天每天都低于 floor」才算死，候选就要「连续 N 天每一天都赢够」，
    且回本天数用**最差的一天**算。理由：同窗中位数会被尖峰拉着走（实测 NG 2 天中位 135 bps/天）。
    """

    def _module(self):
        return _load_script("carry_rotation")

    @staticmethod
    def _write_history(root: Path, base: str, last_day, rate: float, days: int = 3) -> None:
        import datetime as dt

        rows = []
        for back in range(days):
            day = last_day - dt.timedelta(days=back)
            for slot in range(3):
                stamp = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
                rows.append({"ts": int(stamp.timestamp() * 1000) + slot * 8 * 3_600_000,
                             "rate": rate})
        (root / f"{base}-USDT_USDT.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    @staticmethod
    def _cand(symbol: str, day_bps: list[float], *, depth_ok: bool = True,
              eligible=True, screen_bps: float | None = None) -> dict:
        """一个已经过 `remeasure_candidates` 的候选：带 series、bps（中位）与快照字段。"""
        series = [(f"2026-10-{11 + i}", bps, 3) for i, bps in enumerate(day_bps)]
        median = sorted(day_bps)[len(day_bps) // 2]
        return {"symbol": symbol, "bps": median, "series": series,
                "screen_bps": screen_bps if screen_bps is not None else median,
                "eligible": eligible, "depth_ok": depth_ok}

    def _plan(self, module, root, today, *, screen_ms=None, held=("LTC/USDT",),
              candidates=(), rate=0.00001, history_last_day=None):
        import datetime as dt

        for symbol in held:
            # 「现在」与「历史最后一天」必须能分开 —— 冻住的历史正是靠这两个错开才测得出来
            self._write_history(root, symbol.split("/")[0],
                                history_last_day or today, rate)
        now_ms = int(dt.datetime(today.year, today.month, today.day, 12,
                                 tzinfo=dt.timezone.utc).timestamp() * 1000)
        return module.plan({s: {} for s in held}, root, list(candidates), [],
                           days=3, floor_bps=1.0, round_trip_bps=44.9,
                           min_hold_hours=0.0, intended_hold_days=30.0,
                           now_ms=now_ms, screen_generated_at_ms=screen_ms)

    def test_a_candidate_that_clears_the_bar_every_day_produces_a_proposal(self) -> None:
        """正常路（护栏没把它堵死）：现任连续 3 天低于 floor、候选 3 天每天都赢够。"""
        import datetime as dt
        import tempfile

        module = self._module()
        today = dt.date(2026, 10, 13)
        fresh = int(dt.datetime(2026, 10, 13, 8, tzinfo=dt.timezone.utc).timestamp() * 1000)
        cand = self._cand("TRIA/USDT:USDT", [5.0, 6.0, 7.0], screen_bps=11.933)
        with tempfile.TemporaryDirectory() as tmp:
            report = self._plan(module, Path(tmp), today, screen_ms=fresh, candidates=[cand])
        self.assertEqual(len(report["rotations"]), 1)
        rot = report["rotations"][0]
        self.assertEqual(rot["from"], "LTC/USDT")
        self.assertEqual(rot["to"], "TRIA/USDT:USDT")
        # 回本用最差的一天（5.0），不是中位/最好的一天；中位与快照值一起留痕
        self.assertEqual(rot["candidate_bps"], 5.0)
        self.assertEqual(rot["worst_day_bps"], 5.0)
        self.assertEqual(rot["candidate_bps_median"], 6.0)
        self.assertEqual(rot["candidate_bps_screen"], 11.933)

    def test_one_bad_day_disqualifies_a_candidate(self) -> None:
        """**对称性**：插针日不能把候选送上位。中位可以很好看，但有一天没赢够就不算。"""
        import datetime as dt
        import tempfile

        module = self._module()
        today = dt.date(2026, 10, 13)
        fresh = int(dt.datetime(2026, 10, 13, 8, tzinfo=dt.timezone.utc).timestamp() * 1000)
        spike = self._cand("TRIA/USDT:USDT", [0.2, 40.0, 45.0])   # 中位 40，但第一天只有 0.2
        with tempfile.TemporaryDirectory() as tmp:
            report = self._plan(module, Path(tmp), today, screen_ms=fresh,
                                candidates=[spike])
        self.assertEqual(report["rotations"], [])
        self.assertEqual(report["pairs"][0]["stale"], True)        # 现任确实"死"了
        # 换一个每天都赢够的候选 → 立刻就能出提议（上一条不是被别的条件挡住的）
        good = self._cand("TRIA/USDT:USDT", [5.0, 5.0, 5.0])
        with tempfile.TemporaryDirectory() as tmp:
            report = self._plan(module, Path(tmp), today, screen_ms=fresh, candidates=[good])
        self.assertEqual(len(report["rotations"]), 1)

    def test_a_candidate_with_incomplete_history_is_dropped(self) -> None:
        """候选历史不足 `days` 天 → 丢掉，而不是拿「最近 N 个有印的日子」冒充窗口。"""
        import datetime as dt
        import tempfile

        module = self._module()
        today = dt.date(2026, 10, 13)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root, "TRIA", today, 0.0005, days=3)
            self._write_history(root, "ONLYTWO", today, 0.0005, days=2)
            screen = {"rows": [{"symbol": "TRIA/USDT:USDT", "median_daily_bps": 9.0},
                               {"symbol": "ONLYTWO/USDT:USDT", "median_daily_bps": 99.0}]}
            kept = module.remeasure_candidates(module.screen_rows(screen), root, 3)
        names = [c["symbol"] for c in kept]
        self.assertEqual(names, ["TRIA/USDT:USDT"])
        self.assertIn("series", kept[0])          # 序列随候选一起带到报告里，便于复核

    def test_a_stale_screen_no_longer_blocks_the_ranking(self) -> None:
        """快照过旧 → 只停用深度/资格过滤（并写明原因），**不再**挡住提议：
        排名现在来自本地费率历史，与快照无关。"""
        import datetime as dt
        import tempfile

        module = self._module()
        today = dt.date(2026, 10, 13)
        stale = int(dt.datetime(2026, 10, 10, 3, tzinfo=dt.timezone.utc).timestamp() * 1000)
        # depth_ok=False 且 eligible=False：若过滤生效就会被挡掉
        cand = self._cand("TRIA/USDT:USDT", [5.0, 5.0, 5.0], depth_ok=False, eligible=False)
        with tempfile.TemporaryDirectory() as tmp:
            report = self._plan(module, Path(tmp), today, screen_ms=stale, candidates=[cand])
        self.assertEqual(len(report["rotations"]), 1)              # 排名照常
        refusals = report["refusals"]
        self.assertEqual(len(refusals), 1)
        self.assertIn("depth field is stale", refusals[0]["why"])
        self.assertEqual(len(report["pairs"]), 1)                  # 每对腿的读数表保留

    def test_a_fresh_unsuitable_candidate_is_filtered(self) -> None:
        """快照新鲜时，它自己的过滤仍然生效：depth_ok=False 或 eligible=False 的候选不用。"""
        import datetime as dt
        import tempfile

        module = self._module()
        today = dt.date(2026, 10, 13)
        fresh = int(dt.datetime(2026, 10, 13, 8, tzinfo=dt.timezone.utc).timestamp() * 1000)
        shallow = self._cand("TRIA/USDT:USDT", [5.0, 5.0, 5.0], depth_ok=False)
        ineligible = self._cand("ONLYSPIKE/USDT:USDT", [5.0, 5.0, 5.0], eligible=False)
        good = self._cand("GOOD/USDT:USDT", [4.0, 4.0, 4.0])
        with tempfile.TemporaryDirectory() as tmp:
            report = self._plan(module, Path(tmp), today, screen_ms=fresh,
                                candidates=[shallow, ineligible, good])
        self.assertEqual([r["to"] for r in report["rotations"]], ["GOOD/USDT:USDT"])

    def test_a_frozen_incumbent_history_is_refused_not_read_as_persistently_below(self) -> None:
        import datetime as dt
        import tempfile

        module = self._module()
        today = dt.date(2026, 10, 13)
        fresh = int(dt.datetime(2026, 10, 13, 8, tzinfo=dt.timezone.utc).timestamp() * 1000)
        cand = self._cand("TRIA/USDT:USDT", [5.0, 5.0, 5.0])
        with tempfile.TemporaryDirectory() as tmp:
            report = self._plan(module, Path(tmp), today, screen_ms=fresh, candidates=[cand],
                                history_last_day=today - dt.timedelta(days=13))
        self.assertEqual(report["rotations"], [])
        self.assertTrue(report["pairs"][0]["stale_history"])
        self.assertFalse(report["pairs"][0]["stale"])
        self.assertIn("days old", report["refusals"][0]["why"])


class CarryRotationRuleTests(unittest.TestCase):
    """1.2's decision half: what the RUNNER may close, given the detector's report.

    The detector lives in the daily job and writes a report; the close has to happen
    inside the runner, because only the runner holds pairs.json, the ledger and the
    executor - an external script closing a pair leaves the pair state describing a
    position that no longer exists (the shape behind the duplicate-hedge incidents).
    """

    def _module(self):
        from ta.execution.rotation import plan_rotation

        return plan_rotation

    def test_a_reported_pair_the_runner_holds_is_closed(self) -> None:
        plan = self._module()
        report = {"rotations": [{"from": "CRV/USDT", "to": "WLFI/USDT:USDT",
                                 "current_bps": 0.4, "breakeven_days": 12}]}
        out = plan(report, {"CRV/USDT"}, 100_000.0, {"CRV/USDT": 2_700.0})
        self.assertEqual(out["symbol"], "CRV/USDT")
        self.assertEqual(out["refused"], "")

    def test_a_pair_the_runner_does_not_hold_is_ignored(self) -> None:
        plan = self._module()
        report = {"rotations": [{"from": "WLFI/USDT", "to": "X"}]}
        self.assertIsNone(plan(report, {"CRV/USDT"}, 100_000.0, {"WLFI/USDT": 10.0}))

    def test_the_notional_cap_and_the_daily_cap_both_refuse(self) -> None:
        plan = self._module()
        report = {"rotations": [{"from": "CRV/USDT", "to": "WLFI/USDT:USDT"}]}
        big = plan(report, {"CRV/USDT"}, 100_000.0, {"CRV/USDT": 6_000.0})
        self.assertEqual(big["refused"], "notional")           # 6000 > 5% of 100k
        spent = plan(report, {"CRV/USDT"}, 100_000.0, {"CRV/USDT": 100.0}, actions_today=1)
        self.assertIsNone(spent)                               # one a day, no more

    def test_a_rotation_that_merely_breaks_even_is_refused(self) -> None:
        """A 30-day breakeven leaves no edge after paying the 44.9 bps round trip."""
        plan = self._module()
        report = {
            "round_trip_bps": 44.9,
            "rotations": [{
                "from": "CRV/USDT", "to": "WLFI/USDT:USDT",
                "gain_bps_per_day": 1.55, "breakeven_days": 29,
            }],
        }
        out = plan(report, {"CRV/USDT"}, 100_000.0, {"CRV/USDT": 2_000.0})
        self.assertEqual(out["refused"], "insufficient_net_edge")
        self.assertLess(out["expected_net_bps"], 20.0)

    def test_a_rotation_with_real_post_cost_edge_is_allowed(self) -> None:
        plan = self._module()
        report = {
            "round_trip_bps": 44.9,
            "rotations": [{
                "from": "CRV/USDT", "to": "WLFI/USDT:USDT",
                "gain_bps_per_day": 4.0, "breakeven_days": 12,
            }],
        }
        out = plan(report, {"CRV/USDT"}, 100_000.0, {"CRV/USDT": 2_000.0})
        self.assertEqual(out["refused"], "")
        self.assertAlmostEqual(out["expected_net_bps"], 75.1)

    def test_no_report_and_no_proposals_do_nothing(self) -> None:
        plan = self._module()
        self.assertIsNone(plan(None, {"CRV/USDT"}, 100_000.0, {}))
        self.assertIsNone(plan({"rotations": []}, {"CRV/USDT"}, 100_000.0, {}))


class RotationRefusalAlertCooldownTests(unittest.TestCase):
    """A standing low-edge refusal must not write one warning every tick."""

    def _runner(self) -> TradingRunner:
        runner = TradingRunner.__new__(TradingRunner)
        runner._rotation_refusal_alerted_at = {}
        runner.metrics = Metrics()
        return runner

    def test_insufficient_net_edge_is_deduplicated_per_symbol_for_one_hour(self) -> None:
        runner = self._runner()

        self.assertTrue(runner._rotation_refusal_alert_allowed(
            "NEAR/USDT", "insufficient_net_edge", now_s=1_000.0
        ))
        self.assertFalse(runner._rotation_refusal_alert_allowed(
            "NEAR/USDT", "insufficient_net_edge", now_s=1_001.0
        ))
        self.assertTrue(runner._rotation_refusal_alert_allowed(
            "ETH/USDT", "insufficient_net_edge", now_s=1_001.0
        ))
        self.assertTrue(runner._rotation_refusal_alert_allowed(
            "NEAR/USDT", "insufficient_net_edge", now_s=4_600.0
        ))
        self.assertEqual(
            runner.metrics.counters["ta_rotation_refusal_suppressed_total"], 1
        )

    def test_other_rotation_refusals_remain_immediate(self) -> None:
        runner = self._runner()

        self.assertTrue(runner._rotation_refusal_alert_allowed(
            "NEAR/USDT", "notional", now_s=1_000.0
        ))
        self.assertTrue(runner._rotation_refusal_alert_allowed(
            "NEAR/USDT", "notional", now_s=1_001.0
        ))
        self.assertEqual(
            runner.metrics.counters["ta_rotation_refusal_suppressed_total"], 0
        )


class DailyReviewGeneratorTests(unittest.TestCase):
    """The numbers half of a daily review has to be reproducible, not remembered.

    The first version of this script had the date and project path hardcoded
    (2026-10-06), so every morning the arithmetic was redone by hand - which is
    where a review goes wrong silently. Checked against README 68 (2026-10-10):
    equity 101,569.14 -> 101,411.33, 4 fills, $5.4984 of fees, funding +$47.6879
    across 25 settlements.
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "daily_review", PROJECT / "scripts" / "daily_review.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_cst_day_is_a_half_open_utc_window(self) -> None:
        module = self._module()
        start, end = module.day_window("2026-10-10")
        import datetime

        self.assertEqual(end - start, 24 * 3600 * 1000)
        start_utc = datetime.datetime.fromtimestamp(start / 1000, datetime.UTC)
        # CST midnight is 16:00 UTC the previous day
        self.assertEqual(start_utc.isoformat(timespec="seconds"), "2026-10-09T16:00:00+00:00")

    def test_the_summary_is_built_from_the_ledger_alone(self) -> None:
        module = self._module()
        import datetime

        base = int(datetime.datetime(2026, 10, 10, 1, 0, tzinfo=module.CST).timestamp() * 1000)
        rows = [
            {"kind": "equity", "ts": base, "equity": 100.0,
             "positions": {"AAA/USDT": 1.0, "AAA/USDT:USDT": -1.0}},
            {"kind": "fill", "ts": base + 1, "symbol": "AAA/USDT", "side": "buy",
             "qty": 1.0, "price": 10.0, "fee": 0.05, "fee_ccy": "USDT"},
            {"kind": "funding", "ts": base + 2, "symbol": "AAA/USDT:USDT", "amount": 3.0},
            {"kind": "blocked", "ts": base + 3, "reason": "leg_positions: spot 5 >= 5"},
            {"kind": "equity", "ts": base + 4, "equity": 104.0,
             "positions": {"AAA/USDT": 1.0, "AAA/USDT:USDT": -1.0}},
            # 23:00 CST the day before - outside the window, must not be counted
            {"kind": "equity", "ts": base - 2 * 3600_000, "equity": 999.0},
        ]
        stats = module.summarise(rows, *module.day_window("2026-10-10"))
        self.assertEqual((stats["equity_start"], stats["equity_end"]), (100.0, 104.0))
        self.assertEqual(len(stats["fills"]), 1)
        self.assertAlmostEqual(stats["fill_fees"], 0.05)
        self.assertAlmostEqual(stats["funding_total"], 3.0)
        self.assertEqual(stats["funding_top"][0], "AAA/USDT:USDT")
        self.assertEqual(stats["blocked_total"], 1)
        self.assertEqual(stats["legs"], (1, 1))

    def test_capacity_blocks_report_expected_net_edge_opportunity(self) -> None:
        module = self._module()
        import datetime

        base = int(datetime.datetime(2026, 10, 10, 1, 0, tzinfo=module.CST).timestamp() * 1000)
        rows = [
            {"kind": "blocked", "ts": base + i, "reason": "leg_positions: spot 5 >= 5",
             "symbol": symbol, "expected_net_bps": edge}
            for i, (symbol, edge) in enumerate([
                ("AAA/USDT", 25.0),
                ("BBB/USDT", 35.0),
                ("CCC/USDT", 45.0),
            ])
        ]
        stats = module.summarise(rows, *module.day_window("2026-10-10"))
        opportunity = stats["capacity_opportunity"]
        self.assertEqual(opportunity["events"], 3)
        self.assertEqual(opportunity["symbols"], 3)
        self.assertEqual(opportunity["median_bps"], 35.0)
        self.assertEqual(opportunity["p90_bps"], 45.0)

    def test_a_batch_sync_is_reported_as_backfilled_not_as_todays_income(self) -> None:
        """2026-10-12：10-11 那天 09:10 一分钟内入账 6 条 CRV 账单（$233.24），
        它们是之前几次结算的欠账，却整笔算进了当日 +0.700%。

        `bill_ts`（账单自己的时刻）回答"这笔钱哪天挣的"，`ts` 只回答"哪天写的"。
        旧行没有 `bill_ts`，必须报未知而不是当成当日收入。
        """
        module = self._module()
        import datetime

        start, end = module.day_window("2026-10-10")
        written_today = int(datetime.datetime(2026, 10, 10, 9, 10,
                                             tzinfo=module.CST).timestamp() * 1000)
        settled_yesterday = written_today - 26 * 3600 * 1000
        rows = [
            # written today, settled two days ago -> backfilled
            {"kind": "funding", "ts": written_today, "bill_ts": settled_yesterday,
             "amount": 43.0, "symbol": "CRV/USDT:USDT"},
            # written today, settled today -> today's income
            {"kind": "funding", "ts": written_today, "bill_ts": written_today,
             "amount": 5.0, "symbol": "CRV/USDT:USDT"},
            # old row: no bill_ts at all -> unknown, never guessed as today's
            {"kind": "funding", "ts": written_today, "amount": 1.0,
             "symbol": "ACT/USDT:USDT"},
        ]
        stats = module.summarise(rows, start, end)
        self.assertAlmostEqual(stats["funding_total"], 49.0)      # bill-based fact
        self.assertEqual(stats["funding_backfilled"]["count"], 1)
        self.assertAlmostEqual(stats["funding_backfilled"]["total"], 43.0)
        self.assertEqual(stats["funding_backfilled"]["unknown_bill_ts"], 1)
        text = module.markdown("2026-10-10", stats)
        self.assertIn("补记", text)
        self.assertIn("43.0000", text)

    def test_pair_conservation_flags_a_missing_hedge_leg(self) -> None:
        module = self._module()
        positions = {
            "ACT/USDT": 100.0,
            "ACT/USDT:USDT": -10.0,
            "BNB/USDT": 2.0,
            "BNB/USDT:USDT": -2.0,
            "CRV/USDT": 10.0,
            "CRV/USDT:USDT": -10.0,
            "SHIB/USDT": 1_000_000.0,
            "NEAR/USDT:USDT": -3.0,
        }
        check = module.pair_conservation(
            positions,
            ["ACT/USDT", "BNB/USDT", "CRV/USDT", "SHIB/USDT", "NEAR/USDT"],
        )
        self.assertFalse(check["ok"])
        self.assertEqual(check["missing_spot"], ["NEAR/USDT"])
        self.assertEqual(check["missing_perp"], ["SHIB/USDT"])
        self.assertEqual(check["active_pairs"], 5)

    def test_pair_conservation_passes_when_every_registered_pair_has_two_legs(self) -> None:
        module = self._module()
        positions = {
            "ACT/USDT": 100.0,
            "ACT/USDT:USDT": -10.0,
            "SHIB/USDT": 1_000_000.0,
            "SHIB/USDT:USDT": -1.0,
        }
        check = module.pair_conservation(positions, ["ACT/USDT", "SHIB/USDT"])
        self.assertTrue(check["ok"])
        self.assertEqual(check["active_pairs"], 2)

    def test_pair_conservation_requires_long_spot_and_short_perp(self) -> None:
        module = self._module()
        positions = {
            "ACT/USDT": -100.0,
            "ACT/USDT:USDT": 10.0,
        }
        check = module.pair_conservation(positions, ["ACT/USDT"])
        self.assertFalse(check["ok"])
        self.assertEqual(check["wrong_spot_sign"], ["ACT/USDT"])
        self.assertEqual(check["wrong_perp_sign"], ["ACT/USDT"])

    def test_live_rate_funding_uses_the_public_rate_not_the_venue_bills(self) -> None:
        """结论口径：空头永续收正费率、多头付；缺费率文件时报"拿不到"而不是猜。"""
        module = self._module()
        import datetime
        import json as _json

        start, end = module.day_window("2026-10-10")
        when = int(datetime.datetime(2026, 10, 10, 8, 0,
                                    tzinfo=module.CST).timestamp() * 1000)
        rows = [
            {"kind": "equity", "ts": when, "equity": 100.0,
             "positions": {"AAA/USDT": 1000.0, "AAA/USDT:USDT": -1000.0},
             "marks": {"AAA/USDT": 2.0}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            # 空头名义 $2,000、费率 0.01% → 收 $0.20
            (directory / "AAA-USDT_USDT.jsonl").write_text(
                _json.dumps({"ts": when, "rate": 0.0001}) + "\n", encoding="utf-8")
            got = module.live_rate_funding(rows, start, end, funding_dir=directory)
            self.assertTrue(got["available"])
            self.assertAlmostEqual(got["total"], 0.2, places=6)
            self.assertAlmostEqual(got["by_symbol"]["AAA/USDT:USDT"], 0.2, places=6)
            # 费率文件只到 08:00，窗口到 24:00 → 必须披露"数字是下界"，
            # 不能让人把一个"还没到结算时刻"的 0 读成"今天没有资金费"
            self.assertTrue(got["partial"])
            self.assertEqual(got["series_through"], when)

            # 多头同一个费率 → 付 $0.20
            long_rows = [dict(rows[0],
                              positions={"AAA/USDT": -1000.0, "AAA/USDT:USDT": 1000.0})]
            self.assertAlmostEqual(
                module.live_rate_funding(long_rows, start, end,
                                         funding_dir=directory)["total"], -0.2, places=6)

            # 没有费率文件 → 说清楚拿不到，不算 0
            empty = module.live_rate_funding(rows, start, end,
                                             funding_dir=directory / "nope")
            self.assertFalse(empty["available"])
            self.assertIsNone(empty["total"])
            self.assertIn("没有费率文件", empty["reason"])


class EnsureMarketsTests(unittest.TestCase):
    """2026-10-12：`connect()` 刻意容忍 `load_markets()` 失败（降级启动优于
    crash-restart 循环，10-10 那次 142 个启动横幅就是教训），所以 `ex.markets`
    可能是 None。需要全量目录的脚本必须**显式要一次** —— 实测
    `options_order_path_check.py` 直接 `AttributeError: 'NoneType' has no attribute
    'values'`，看起来像脚本坏了，其实只是目录没加载。
    """

    def _venue(self, error: Exception | None = None):
        from ta.venues.ccxt_venue import CcxtVenue

        class Ex:
            def __init__(self) -> None:
                self.markets = None
                self.calls = 0

            def load_markets(self):
                self.calls += 1
                if error is not None:
                    raise error
                self.markets = {"BTC-USD-261030-82000-C": {"id": "ok"}}
                return self.markets

        venue = CcxtVenue.__new__(CcxtVenue)      # 只测这一条路径，不建真客户端
        venue.ex = Ex()
        venue.last_connect_error = ""
        return venue

    def test_it_loads_once_and_returns_true(self) -> None:
        venue = self._venue()
        self.assertTrue(venue.ensure_markets())
        self.assertEqual(venue.ex.calls, 1)
        self.assertTrue(venue.ensure_markets())    # 已有目录：不再打网络
        self.assertEqual(venue.ex.calls, 1)

    def test_a_dead_catalogue_returns_false_instead_of_raising(self) -> None:
        venue = self._venue(error=RuntimeError("proxy boom"))
        self.assertFalse(venue.ensure_markets())
        self.assertIn("proxy boom", venue.last_connect_error)


class CancelByClientOrderIdTests(unittest.TestCase):
    """A client order id is not an order id, and a fresh process has neither.

    Measured 2026-10-11: after `options_round_trip.py` rested a limit buy, the
    cancel from a NEW process failed (`cancel_order` returned False) and the order
    stayed on the book - an orphan with real notional. The runner never saw it
    because the runner is the process that placed it; anything that rests an order
    and exits has to be able to name it the way the venue does.
    """

    class _Exchange:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def cancel_order(self, order_id, symbol, params=None):
            self.calls.append((order_id, symbol, params))
            if order_id is None and not params:
                raise RuntimeError("no id and no params")
            return {"id": "ok"}

    def _venue(self):
        from ta.venues.ccxt_venue import CcxtVenue

        exchange = self._Exchange()

        class Fake:
            ex = exchange
            _orders: dict = {}

            def _venue_symbol(self, symbol: str) -> str:
                return symbol

            cancel_order = CcxtVenue.cancel_order

        return Fake(), exchange

    def test_a_client_id_is_passed_as_clordid(self) -> None:
        venue, exchange = self._venue()
        self.assertTrue(venue.cancel_order("TAoptpc1", "BTC/USD:BTC-261030-82000-C"))
        order_id, symbol, params = exchange.calls[0]
        self.assertIsNone(order_id)
        self.assertEqual(params, {"clOrdId": "TAoptpc1"})
        self.assertEqual(symbol, "BTC/USD:BTC-261030-82000-C")

    def test_a_known_venue_id_still_wins(self) -> None:
        from ta.models import Order

        venue, exchange = self._venue()
        venue._orders["c1"] = Order(
            client_order_id="c1", symbol="X", side="buy", qty=1.0, type="limit",
            price=1.0, status="open", venue_order_id="V9",
        )
        venue.cancel_order("c1", "BTC/USD:BTC-261030-82000-C")
        self.assertEqual(exchange.calls[0][0], "V9")
        self.assertIsNone(exchange.calls[0][2])


class AvailabilityWatchScopeTests(unittest.TestCase):
    """Narrowing the tier must not narrow what we WATCH.

    Measured 2026-10-10: the first sampler run after `tradfi` went from 13 symbols
    to 1 sampled exactly one symbol, deleting the only evidence that could bring a
    symbol back. The excluded list already exists in the tier report.
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "venue_availability", PROJECT / "scripts" / "venue_availability.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_tier_is_the_baseline_and_the_excluded_are_added(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tradfi_tier.json"
            path.write_text(json.dumps({
                "symbols": {"XMU/USDT": {}, "XTSM/USDT": {}, "XIWM/USDT": {}},
            }), encoding="utf-8")
            symbols = module.watch_symbols(["XMU/USDT"], path)
        self.assertEqual(symbols, ["XMU/USDT", "XTSM/USDT", "XIWM/USDT"])

    def test_a_missing_or_broken_report_falls_back_to_the_tier(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(module.watch_symbols(["XMU/USDT"], Path(tmp) / "no.json"),
                             ["XMU/USDT"])
            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertEqual(module.watch_symbols(["XMU/USDT"], broken), ["XMU/USDT"])


class CashAttributionTests(unittest.TestCase):
    """The residual question needs measurement, and measurement needs conventions.

    2026-10-11: bills carry `pnl + fee`, which for a spot trade is the FEE, not the
    notional - so summing every bill does not reproduce a cash flow. Only funding
    (type 8) is pure cash. Getting this wrong is how a diagnostic invents a leak.
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "cash_residual_attribution", PROJECT / "scripts" / "cash_residual_attribution.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_our_flow_counts_fills_as_cash_and_funding_separately(self) -> None:
        module = self._module()
        rows = [
            {"kind": "fill", "ts": 20, "side": "buy", "qty": 2.0, "price": 10.0,
             "fee": 0.1, "fee_ccy": "USDT"},
            {"kind": "fill", "ts": 21, "side": "sell", "qty": 1.0, "price": 12.0,
             "fee": 0.05, "fee_ccy": "USDT"},
            {"kind": "funding", "ts": 22, "amount": 3.0},
            {"kind": "fill", "ts": 5, "side": "buy", "qty": 9.0, "price": 9.0},  # outside window
        ]
        flow = module.our_cash_flow(rows, start_ms=10)
        self.assertEqual(flow["fills"], 2)
        self.assertAlmostEqual(flow["trade"], -20.0 + 12.0)
        self.assertAlmostEqual(flow["fees"], -0.15)
        self.assertAlmostEqual(flow["funding"], 3.0)
        self.assertAlmostEqual(flow["total"], -8.0 - 0.15 + 3.0)

    def test_bills_are_grouped_by_type_and_subtype(self) -> None:
        module = self._module()
        bills = [
            {"type": "8", "subType": "174", "ccy": "USDT", "pnl": 10.0, "fee": 0.0},
            {"type": "8", "subType": "174", "ccy": "USDT", "pnl": 5.0, "fee": 0.0},
            {"type": "2", "subType": "1", "ccy": "USDT", "pnl": 0.0, "fee": -1.0},
        ]
        grouped = module.summarise_bills(bills)
        self.assertEqual(grouped[("8", "174")]["count"], 2)
        self.assertAlmostEqual(grouped[("8", "174")]["amount"], 15.0)
        self.assertAlmostEqual(grouped[("2", "1")]["amount"], -1.0)

    def test_a_spot_trade_bill_is_a_fee_not_a_notional(self) -> None:
        module = self._module()
        # 3 SUI at ~1.08 is ~3.25 USDT of notional, but the bill only carries the fee.
        grouped = module.summarise_bills([
            {"type": "2", "subType": "1", "ccy": "USDT", "pnl": 0.0, "fee": -0.002},
        ])
        self.assertAlmostEqual(grouped[("2", "1")]["amount"], -0.002)

    def test_the_equity_row_records_the_venues_own_cash_bal(self) -> None:
        """`cash` includes swap upl; `cash_bal` is what the reconciler compares.

        Until 2026-10-11 the equity rows carried only `cash`, so a cash-residual
        series could not be built from them at all - the drift question had no data.
        """
        from ta.portfolio import Ledger

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.jsonl", run_id="t", venue="paper")
            account = AccountState(venue="paper", cash=100.0, equity=100.0, cash_bal=97.5)
            ledger.record_equity(account, {})
            row = [e for e in ledger.events if e.get("kind") == "equity"][0]
        self.assertEqual(row["cash"], 100.0)
        self.assertEqual(row["cash_bal"], 97.5)


class OptionsRoundTripSelectionTests(unittest.TestCase):
    """The contract choice is a rule, so it is testable without a venue.

    1.1's spec picks by (expiry nearest the target, then strike nearest the money)
    because the measured spread falls with both. The chain changes daily; the rule
    must not.
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "options_round_trip", PROJECT / "scripts" / "options_round_trip.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _market(inst_id: str, strike: float, family: str = "BTC-USD") -> dict:
        return {
            "symbol": inst_id.replace("BTC-USD-", "BTC/USD:BTC-"),
            "info": {"instId": inst_id, "uly": family, "stk": str(strike)},
        }

    def test_it_prefers_the_target_expiry_then_the_money(self) -> None:
        import datetime

        module = self._module()
        today = datetime.date(2026, 10, 10)
        markets = [
            self._market("BTC-USD-261023-80000-C", 80_000),   # 13d, out of window
            self._market("BTC-USD-261110-80000-C", 80_000),   # 31d, ATM
            self._market("BTC-USD-261110-90000-C", 90_000),   # 31d, away
            self._market("BTC-USD-261230-80000-C", 80_000),   # 81d, out of window
        ]
        picked = module.pick_contract(markets, "BTC-USD", today, 30, 25, 45, "C", 80_000)
        self.assertEqual(picked["inst_id"], "BTC-USD-261110-80000-C")
        self.assertEqual(picked["dte"], 31)
        self.assertAlmostEqual(picked["offset_pct"], 0.0)

    def test_puts_are_not_mistaken_for_calls(self) -> None:
        import datetime

        module = self._module()
        today = datetime.date(2026, 10, 10)
        markets = [
            self._market("BTC-USD-261110-80000-C", 80_000),
            self._market("BTC-USD-261110-80000-P", 80_000),
        ]
        self.assertTrue(module.pick_contract(markets, "BTC-USD", today, 30, 25, 45,
                                             "P", 80_000)["inst_id"].endswith("-P"))
        self.assertTrue(module.pick_contract(markets, "BTC-USD", today, 30, 25, 45,
                                             "C", 80_000)["inst_id"].endswith("-C"))

    def test_an_empty_window_returns_nothing_rather_than_a_bad_contract(self) -> None:
        import datetime

        module = self._module()
        today = datetime.date(2026, 10, 10)
        markets = [self._market("SOL-USD_UM-261011-100-C", 100, family="SOL-USD")]
        self.assertIsNone(module.pick_contract(markets, "BTC-USD", today, 30, 25, 45, "C", 80_000))
        self.assertIsNone(module.pick_contract([], "BTC-USD", today, 30, 25, 45, "C", 80_000))


class DashboardBatchJobsTests(unittest.TestCase):
    """Batch jobs are invisible to a port/pid check, so they need their own.

    The daily research job and the TradFi session sampler run twice a day and are
    idle the rest of the time - "not running" is their normal state. Judging them by
    liveness would make `all_alive` permanently false (which is why they were left
    off the board until 2026-10-10); judging them by "did the last run happen and
    what did it say" is a fact.
    """

    def _data(self, root: Path):
        from ta.dashboard import DashboardData

        data = DashboardData(CONFIG_DIR)
        data.root = root
        data.ledger_path = root / "ledger.jsonl"
        data.alerts_path = root / "alerts.jsonl"
        data.live_metrics = lambda: {}
        return data

    def test_a_fresh_log_is_not_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs").mkdir()
            (root / "logs" / "ta-newlisting-service.log").write_text("ran\n", encoding="utf-8")
            (root / "logs" / "ta-tradfi-session.log").write_text("ran\n", encoding="utf-8")
            jobs = {b["label"]: b for b in self._data(root).batch_jobs()}
        self.assertFalse(jobs["com.tradingagent.newlisting"]["stale"])
        self.assertFalse(jobs["com.tradingagent.tradfi-session"]["stale"])
        # Every configured batch job must appear - the list grows as jobs are added,
        # so assert against the configuration rather than a frozen count.
        from ta.dashboard import BATCH_JOBS

        self.assertEqual(len(jobs), len(BATCH_JOBS))

    def test_a_missing_log_counts_as_stale_and_raises_a_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = self._data(root)
            jobs = {b["label"]: b for b in data.batch_jobs()}
            titles = [f["title"] for f in data.redflags()
                      if f["title"].startswith("批量任务超时未运行")]
        self.assertTrue(jobs["com.tradingagent.tradfi-session"]["stale"])
        self.assertIsNone(jobs["com.tradingagent.tradfi-session"]["age_hours"])
        self.assertGreaterEqual(len(titles), 2)   # every missing log raises one
        self.assertEqual(len(titles), len(jobs))

    def test_the_research_job_repeats_what_its_own_status_file_said(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs").mkdir()
            (root / "logs" / "ta-newlisting-service.log").write_text("ran\n", encoding="utf-8")
            (root / "data").mkdir()
            (root / "data" / "daily_job_status.json").write_text(json.dumps({
                "generated_at": "2026-10-10T13:46:20+00:00",
                "needs_rerun": ["new_listing_track"],
                "steps": {},
            }), encoding="utf-8")
            jobs = {b["label"]: b for b in self._data(root).batch_jobs()}
        self.assertEqual(jobs["com.tradingagent.newlisting"]["last_run"],
                         "needs rerun: new_listing_track")


class ReservedInventoryTests(unittest.TestCase):
    """A declared manual holding must stop being an alarm, without going invisible.

    Measured 2026-10-10: 0.00120705 BTC bought for a coin-margined option premium
    tripped `unmanaged_notional` and wrote a `position BTC/USDT` reconciliation diff
    every cycle. Both were TRUE statements about an UNKNOWN position - but this one
    is known, and an alarm that fires forever is one nobody reads.
    """

    def _loader(self):
        from ta.config import load_reserved_inventory

        return load_reserved_inventory

    def test_a_missing_or_broken_file_means_nothing_is_reserved(self) -> None:
        load = self._loader()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load(Path(tmp) / "nope.json"), {})
            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertEqual(load(broken), {})
            wrong = Path(tmp) / "wrong.json"
            wrong.write_text('{"symbols": ["BTC/USDT"]}', encoding="utf-8")
            self.assertEqual(load(wrong), {})

    def test_the_shipped_file_loads_and_the_mechanism_is_intact(self) -> None:
        """The BTC reserve was unwound on 2026-10-11 (sold back), so `symbols` is
        empty - but the MECHANISM must still load, because the next deliberate
        manual holding will need it. The populated case is covered above."""
        load = self._loader()
        reserved = load(PROJECT / "config" / "reserved_inventory.json")
        self.assertIsInstance(reserved, dict)
        self.assertNotIn("BTC/USDT", reserved)

    def test_reconciliation_skips_a_reserved_position_but_keeps_the_others(self) -> None:
        from ta.execution.oms import OMS

        class Venue:
            name = "fake"

            def account_snapshot(self):
                return {"venue": "fake", "cash": 100.0, "positions": {"BTC/USDT": 1.0, "X/USDT": 5.0}}

        class Book:
            cash = 0.0
            marks = {"BTC/USDT": 80_000.0, "X/USDT": 10.0}

            def equity(self):
                return 0.0

            def open_positions(self):
                class P:  # noqa: D401 - tiny stand-in
                    def __init__(self, qty):
                        self.qty = qty

                return {"BTC/USDT": P(0.0), "X/USDT": P(1.0)}

        oms = OMS.__new__(OMS)
        oms.venue = Venue()
        account = AccountState(venue="fake", cash=0.0, unmanaged_notional=0.0)
        without = oms.reconcile(account, Book())
        with_ignore = oms.reconcile(account, Book(), ignore_positions=frozenset({"BTC/USDT"}))
        self.assertTrue(any("BTC/USDT" in d for d in without.diffs))
        self.assertFalse(any("BTC/USDT" in d for d in with_ignore.diffs))
        self.assertTrue(any("X/USDT" in d for d in with_ignore.diffs), "others still report")


class CarryRotateExecutionTests(unittest.TestCase):
    """Acting on a rotation is CLOSE-ONLY, and only once a day.

    2026-10-10 decision: opening the replacement belongs to the runner (it already
    has the passive entry, the depth gate and the pairing invariants), while the
    half that carries the risk is the sequence - and the first day of live carry
    showed what moving twice in an afternoon costs (31 fills, $284 of fees).
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "carry_rotate", PROJECT / "scripts" / "carry_rotate.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_newest_row_that_actually_proposed_something_wins(self) -> None:
        module = self._module()
        rows = [
            {"generated_at": "2026-10-10T01:00:00+00:00", "rotations": [{"from": "A/USDT"}]},
            {"generated_at": "2026-10-10T02:00:00+00:00", "rotations": []},
        ]
        self.assertEqual(module.latest_proposal(rows)["generated_at"], "2026-10-10T01:00:00+00:00")
        self.assertIsNone(module.latest_proposal([]))
        self.assertIsNone(module.latest_proposal([{"generated_at": "x", "rotations": []}]))

    def test_one_rotation_a_day_is_counted_from_its_own_log(self) -> None:
        module = self._module()
        rows = [
            {"generated_at": "2026-10-09T23:00:00+00:00"},
            {"generated_at": "2026-10-10T01:00:00+00:00"},
        ]
        self.assertEqual(module.rotations_today(rows, "2026-10-10"), 1)
        self.assertEqual(module.rotations_today(rows, "2026-10-11"), 0)

    def test_the_notional_cap_reads_the_latest_equity(self) -> None:
        module = self._module()
        rows = [
            {"kind": "equity", "equity": 101_000.0},
            {"kind": "fill", "symbol": "BTC/USDT"},
            {"kind": "equity", "equity": 102_000.0},
        ]
        self.assertEqual(module.last_equity(rows), 102_000.0)
        self.assertEqual(module.last_equity([]), 0.0)


class RefreshBudgetTests(unittest.TestCase):
    """A SLOW venue has to stop the pass too, not only a dead one.

    Measured 2026-10-10 21:30 CST: the proxy answered every symbol on a retry after
    a ~40s timeout, so nothing was "consecutive" and nothing ever failed - the pass
    simply walked 63 symbols and took **86 minutes**, in which the loop completed no
    tick, checked no exit and checked no stop. The consecutive-failure limit cannot
    see that shape; a wall-clock budget can.
    """

    class _Slow:
        def __init__(self, delay: float) -> None:
            self.delay = delay
            self.calls = 0

        def fetch_bars(self, symbol, timeframe, limit):
            self.calls += 1
            time.sleep(self.delay)
            return []

    def test_a_pass_that_cannot_finish_inside_its_budget_is_degraded(self) -> None:
        from ta.marketdata import LiveFeed
        from ta.resilience import VenueDegraded

        venue = self._Slow(0.02)
        feed = LiveFeed(venue, "15m", 10, timeframes=["15m"])
        feed.pass_budget_seconds = 0.05
        symbols = [f"S{i}/USDT" for i in range(20)]
        with self.assertRaises(VenueDegraded) as caught:
            feed.refresh(symbols, force=True)
        self.assertIn("budget", str(caught.exception))
        # it stopped early instead of walking all 20 symbols
        self.assertLess(venue.calls, len(symbols))

    def test_a_pass_inside_its_budget_completes(self) -> None:
        from ta.marketdata import LiveFeed

        venue = self._Slow(0.0)
        feed = LiveFeed(venue, "15m", 10, timeframes=["15m"])
        feed.pass_budget_seconds = 30.0
        feed.refresh([f"S{i}/USDT" for i in range(5)], force=True)
        self.assertEqual(venue.calls, 5)

    def test_zero_means_no_budget(self) -> None:
        from ta.marketdata import LiveFeed

        venue = self._Slow(0.01)
        feed = LiveFeed(venue, "15m", 10, timeframes=["15m"])
        feed.pass_budget_seconds = 0.0
        feed.refresh([f"S{i}/USDT" for i in range(5)], force=True)
        self.assertEqual(venue.calls, 5)


class CarryFundingStrategyTests(unittest.TestCase):
    """The carry DECISION, separated from the two legs that execute it.

    Funding is the edge itself here, so a feed without funding data must produce
    no proposal at all - the opposite of a gate, which fails open.
    """

    class _View:
        def __init__(self, rate):
            self.symbol = "SOL/USDT"
            self.mark = 100.0
            self._rate = rate
            self.source = self

        def bars(self, timeframe=None):
            return []

        def funding_rate(self, symbol):
            return self._rate

        def funding_mean(self, symbol, samples=90):
            """The venue's multi-day mean - what carry must decide on."""
            return self._rate

    def _strategy(self, **params):
        from ta.strategies.carry_funding import CarryFunding

        base = {"intended_hold_days": 30.0, "round_trip_bps": 44.9}
        base.update(params)
        return CarryFunding(params=base, symbols=["SOL/USDT"], timeframe="1h")

    def test_a_fat_funding_rate_proposes_the_spot_leg(self) -> None:
        # 0.0002 per 8h = 6 bps/day = 180 bps over 30 days vs 44.9 cost
        signal = self._strategy().on_bar(self._View(0.0002), None)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "buy")
        self.assertEqual(signal.tag, "carry")
        self.assertAlmostEqual(signal.diagnostics["daily_funding_bps"], 6.0)
        self.assertAlmostEqual(signal.diagnostics["expected_net_bps"], 135.1)
        self.assertAlmostEqual(signal.diagnostics["min_edge_bps"], 20.0)

    def test_a_thin_funding_rate_proposes_nothing(self) -> None:
        # 0.00001 per 8h = 0.3 bps/day = 9 bps over 30 days, under the 44.9 cost
        self.assertIsNone(self._strategy().on_bar(self._View(0.00001), None))

    def test_no_funding_data_proposes_nothing(self) -> None:
        self.assertIsNone(self._strategy().on_bar(self._View(None), None))

    def test_funding_turning_negative_sends_an_exit(self) -> None:
        from ta.models import Position

        # Past the minimum hold: carry is judged on a month, not on the first
        # hours, so an exit before min_hold_hours is deliberately suppressed.
        opened = 1_700_000_000_000

        class Aged(self._View):
            def bars(self, timeframe=None):
                from ta.models import Bar
                return [Bar(ts=opened + 48 * 3_600_000, open=1, high=1, low=1,
                            close=1, volume=1)]

        position = Position(symbol="SOL/USDT", qty=1.0, avg_price=100.0,
                            opened_ts=opened)
        signal = self._strategy().on_bar(Aged(-0.0002), position)
        self.assertIsNotNone(signal)
        self.assertTrue(signal.exit)
        self.assertEqual(signal.side, "sell")

        pass

    def test_a_single_negative_spike_does_not_trigger_an_exit(self) -> None:
        """The churn that cost $284 of fees in one afternoon.

        Measured 2026-10-08: carry opened six pairs, sold three of them eight
        minutes later, re-opened, and sold again - because the exit rule watched
        the INSTANTANEOUS funding print. The decision must use the multi-day mean
        (what the screener ranks by), which a single spike cannot move.
        """
        from ta.models import Position

        strategy = self._strategy()

        class Spiky(self._View):
            def funding_rate(self, symbol):
                return -0.01          # a violent negative print

            def funding_mean(self, symbol, samples=90):
                return 0.0003         # ...inside a solidly positive month

        position = Position(symbol="SOL/USDT", qty=1.0, avg_price=100.0)
        self.assertIsNone(
            strategy.on_bar(Spiky(None), position),
            "a spike must not close a month-long carry",
        )

    def test_no_rate_history_and_no_venue_mean_proposes_nothing(self) -> None:
        from ta.strategies.carry_funding import CarryFunding

        class NoMean(self._View):
            def funding_mean(self, symbol, samples=90):
                return None

        strategy = CarryFunding(
            params={"intended_hold_days": 30.0, "round_trip_bps": 44.9},
            symbols=["SOL/USDT"], timeframe="1h",
        )
        # one print in the buffer, below min_samples: not enough evidence
        self.assertIsNone(strategy.on_bar(NoMean(0.0004), None))

    def test_hourly_settlement_contracts_are_priced_differently_impl(self) -> None:
        # Same rate, 24 settlements instead of 3: the edge is 8x larger.
        view = self._View(0.00002)
        self.assertIsNone(self._strategy().on_bar(view, None))
        self.assertIsNotNone(
            self._strategy(settlements_per_day=24.0).on_bar(view, None)
        )


class _PairedExecutionTestsBase(unittest.TestCase):
    """Spot-long + perp-short pairing, and the three failure invariants.

    This is the last structural piece carry needs (README 36.3): the runner is
    single-venue and the strategy interface is spot-shaped, so "also short the
    perp" has to be expressed below the strategies. What matters is not the happy
    path - it is that a half-open pair can never sit on the book.
    """

    class _Sink:
        def __init__(self, *, reject: bool = False, fill_ratio: float = 1.0):
            self.requests: list[OrderRequest] = []
            self.reject = reject
            self.fill_ratio = fill_ratio

        def submit(self, request: OrderRequest) -> Order:
            self.requests.append(request)
            qty = 0.0 if self.reject else request.qty * self.fill_ratio
            return Order(
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                status="rejected" if self.reject else "filled",
                filled_qty=qty,
                avg_price=100.0,
                reason="no" if self.reject else "",
            )

    def test_happy_path_opens_both_legs_with_the_same_size(self) -> None:
        from ta.execution.paired import PairedExecutor, perp_symbol

        spot, perp = self._Sink(), self._Sink()
        pair = PairedExecutor(spot, perp).open_pair("SOL/USDT", 1.68, tag="carry")
        self.assertEqual(spot.requests[0].symbol, "SOL/USDT")
        self.assertEqual(spot.requests[0].side, "buy")
        self.assertEqual(perp.requests[0].symbol, perp_symbol("SOL/USDT"))
        self.assertEqual(perp.requests[0].side, "sell")
        self.assertEqual(perp.requests[0].qty, spot.requests[0].qty)
        self.assertEqual(pair.residual(), 0.0)

    def test_a_failed_spot_leg_never_sends_the_short(self) -> None:
        from ta.execution.paired import PairedExecutor, PairExecutionError

        spot, perp = self._Sink(reject=True), self._Sink()
        with self.assertRaises(PairExecutionError):
            PairedExecutor(spot, perp).open_pair("SOL/USDT", 1.0)
        self.assertEqual(len(perp.requests), 0, "no long means no short")

    def test_a_failed_perp_leg_unwinds_the_spot_immediately(self) -> None:
        from ta.execution.paired import PairedExecutor, PairExecutionError

        spot, perp = self._Sink(), self._Sink(reject=True)
        with self.assertRaises(PairExecutionError) as ctx:
            PairedExecutor(spot, perp).open_pair("SOL/USDT", 2.0)
        self.assertIn("unwound", str(ctx.exception))
        # buy, then the protective sell - the naked long must not survive
        self.assertEqual([r.side for r in spot.requests], ["buy", "sell"])
        self.assertEqual(spot.requests[1].qty, spot.requests[0].qty)

    def test_the_hedge_matches_what_actually_filled(self) -> None:
        from ta.execution.paired import PairedExecutor

        spot, perp = self._Sink(fill_ratio=0.5), self._Sink()
        pair = PairedExecutor(spot, perp).open_pair("SOL/USDT", 2.0)
        self.assertEqual(perp.requests[0].qty, 1.0, "hedge the fill, not the request")
        self.assertEqual(pair.residual(), 0.0)

    def test_closing_is_reduce_only_and_perp_first(self) -> None:
        from ta.execution.paired import PairedExecutor

        spot, perp = self._Sink(), self._Sink()
        executor = PairedExecutor(spot, perp)
        pair = executor.open_pair("SOL/USDT", 1.0)
        executor.close_pair(pair)
        close_perp = perp.requests[-1]
        self.assertEqual(close_perp.side, "buy")
        self.assertTrue(close_perp.reduce_only, "must not flip into a new long")
        self.assertEqual(spot.requests[-1].side, "sell")

    def test_an_async_open_spot_order_is_waited_for_not_treated_as_a_failure(self) -> None:
        """The bug that left a $2,969 naked long on 2026-10-08.

        OKX answers a market order with status=open, filled=0 and fills a beat
        later. Reading that as "not filled" raised before the hedge was sent, and
        because the spot-leg failure path assumed "no long exists", nothing was
        unwound - the order simply filled afterwards.
        """
        from ta.execution.paired import PairedExecutor
        from ta.models import Order

        class AsyncSink:
            def __init__(inner):
                inner.requests: list[OrderRequest] = []
                inner._orders: dict = {}
                inner.polls = 0

            def submit(inner, request: OrderRequest) -> Order:
                inner.requests.append(request)
                # the venue's immediate answer: accepted, nothing filled YET
                order = Order(
                    client_order_id=request.client_order_id,
                    symbol=request.symbol,
                    side=request.side,
                    qty=request.qty,
                    status="open",
                    filled_qty=0.0,
                )
                inner._orders[request.client_order_id] = order
                return order

            def sync(inner, symbols) -> list:
                inner.polls += 1
                out = []
                for order in inner._orders.values():
                    if order.filled_qty == 0:
                        order.filled_qty = order.qty      # fills on the first poll
                        order.status = "filled"
                        out.append(
                            Fill(
                                client_order_id=order.client_order_id,
                                symbol=order.symbol,
                                side=order.side,
                                qty=order.qty,
                                price=1.0,
                            )
                        )
                return out

        spot, perp = AsyncSink(), AsyncSink()
        pair = PairedExecutor(spot, perp).open_pair("ACT/USDT", 1000.0)
        self.assertEqual(pair.spot_filled, 1000.0, "the async fill must be picked up")
        self.assertEqual(perp.requests[0].side, "sell", "and the hedge must still go out")

    def test_an_async_open_spot_order_that_never_fills_is_unwound(self) -> None:
        from ta.execution.paired import PairedExecutor, PairExecutionError
        from ta.models import Order

        class NeverFills:
            def __init__(inner):
                inner.requests: list[OrderRequest] = []
                inner._orders: dict = {}

            def submit(inner, request: OrderRequest) -> Order:
                inner.requests.append(request)
                return Order(
                    client_order_id=request.client_order_id,
                    symbol=request.symbol,
                    side=request.side,
                    qty=request.qty,
                    status="open",
                    filled_qty=0.0,
                )

            def sync(inner, symbols) -> list:
                return []

        spot, perp = NeverFills(), NeverFills()
        executor = PairedExecutor(spot, perp)
        executor.fill_timeout_seconds = 0.1
        with self.assertRaises(PairExecutionError):
            executor.open_pair("ACT/USDT", 1000.0)
        self.assertEqual(len(perp.requests), 0, "no fill means no hedge")

    def test_a_contract_denominated_perp_fill_is_converted_to_base(self) -> None:
        """DOGE-USDT-SWAP is 1000 DOGE per contract.

        The venue reports "1.69" and means 1690 DOGE. Reading that as base units
        produced a fake residual of +1688 and a close that OKX rejected as below
        minSz (measured 2026-10-08).
        """
        from ta.execution.paired import PairLegs
        from ta.models import Order

        pair = PairLegs(
            pair_id="p",
            symbol="DOGE/USDT",
            base_qty=1690.0,
            spot_order=Order("s", "DOGE/USDT", "buy", 1690.0, status="filled", filled_qty=1690.0),
            perp_order=Order("q", "DOGE/USDT:USDT", "sell", 1.69, status="filled", filled_qty=1.69),
            contract_ct_val=1000.0,
        )
        self.assertEqual(pair.perp_contracts, 1.69)
        self.assertAlmostEqual(pair.perp_filled, 1690.0)
        self.assertAlmostEqual(pair.residual(), 0.0, places=6)

    def test_closing_marks_the_hedge_venue_as_short(self) -> None:
        """Otherwise `buy + reduceOnly` opens a LONG instead of closing the short.

        Measured 2026-10-08: the controlled run left a stray long 1.68 contracts
        sitting on top of the short it was meant to close, because the venue's
        posSide defaults to "long" and OKX is in long/short mode.
        """
        from ta.execution.paired import PairedExecutor
        from ta.models import Order

        class Sink:
            def __init__(inner):
                inner.venue = type("V", (), {"default_pos_side": "long"})()
                inner.requests = []

            def submit(inner, request):
                inner.requests.append(request)
                return Order(request.client_order_id, request.symbol, request.side,
                             request.qty, status="filled", filled_qty=request.qty)

        spot, perp = Sink(), Sink()
        executor = PairedExecutor(spot, perp)
        pair = executor.open_pair("DOGE/USDT", 1000.0)
        executor.close_pair(pair)
        self.assertEqual(perp.venue.default_pos_side, "short")
        self.assertTrue(perp.requests[-1].reduce_only)

    def test_an_unreadable_contract_spec_does_not_rescale_the_hedge(self) -> None:
        from ta.execution.paired import PairedExecutor

        class NoVenue:
            def submit(self, request):
                from ta.models import Order
                return Order(request.client_order_id, request.symbol, request.side,
                             request.qty, status="filled", filled_qty=request.qty)

        executor = PairedExecutor(NoVenue(), NoVenue())
        self.assertEqual(executor._ct_val("ANY/USDT:USDT"), 1.0)

    def test_funding_edge_is_the_whole_open_rule(self) -> None:
        from ta.execution.paired import PairedExecutor

        # 20 bps/day for 5 days = 100 bps of funding against a 44.9 bps round trip
        self.assertGreater(PairedExecutor.funding_edge_bps(20.0, 44.9, 5.0), 0)
        # 2 bps/day for 5 days does not pay for the trip
        self.assertLess(PairedExecutor.funding_edge_bps(2.0, 44.9, 5.0), 0)


class _HistoryPagingTestsBase(unittest.TestCase):
    """A young listing must come back as its RECENT bars, not its oldest ones.

    Measured 2026-10-07 while building the daily universe: asking for 1500 daily
    bars when a symbol only has ~590 makes OKX answer with the oldest window it
    holds, and the "a short chunk means no more history" guard then stops the walk
    immediately. The file that lands on disk looks perfectly plausible - SUI/PEPE
    came back as 51 bars ending 2023-06-23, TIA as 12 bars ending 2025-07-13 - but
    covers the wrong years, and the daily backtest then died with "symbols share
    only 11 1d timestamps". Weekly history was silently truncated the same way
    (882 days instead of 1596).
    """

    DAY_MS = 86_400_000

    def _exchange(self, listed_days_ago: int):
        """Stands in for OKX: `since` before the listing returns the OLDEST page."""
        now = int(time.time() * 1000)
        first = now - listed_days_ago * self.DAY_MS

        class _Ex:
            def fetch_ohlcv(inner, symbol, timeframe, since=None, limit=300):
                start = max(first, since or first)
                if since is not None and since < first:
                    start = first          # the venue's actual behaviour
                rows = []
                for i in range(limit):
                    ts = start + i * self.DAY_MS
                    if ts > now:
                        break
                    rows.append([ts, 100.0, 101.0, 99.0, 100.5, 10.0])
                return rows

        return _Ex()

    def test_a_depth_limited_symbol_returns_recent_bars(self) -> None:
        from ta.history import fetch_symbol

        bars = fetch_symbol(self._exchange(590), "SUI/USDT", "1d", 1500, pause=0.0)
        self.assertGreater(len(bars), 500, "should keep everything the venue has")
        newest = max(b.ts for b in bars)
        self.assertLess(
            (int(time.time() * 1000) - newest) // self.DAY_MS,
            3,
            "the newest bar must be near now, not years in the past",
        )

    def test_a_long_history_symbol_is_unaffected(self) -> None:
        from ta.history import fetch_symbol

        bars = fetch_symbol(self._exchange(1500), "BTC/USDT", "1d", 400, pause=0.0)
        self.assertAlmostEqual(len(bars), 400, delta=2)
        self.assertLess(
            (int(time.time() * 1000) - max(b.ts for b in bars)) // self.DAY_MS, 3
        )


class KellySizingTests(unittest.TestCase):
    """Layer 3 of the borrowed framework: how much to put on.

    Kelly is the one part of that layer that is arithmetic rather than judgement,
    and applied to our own measured numbers it returns 0 - which is a verdict,
    not a sizing method (README section 29).
    """

    def test_payoff_ratio_is_recovered_from_a_backtest_summary(self) -> None:
        from ta.execution.sizing import payoff_ratio

        # PF = (W x avg_win) / ((1-W) x avg_loss). With W=0.405 and PF=0.31:
        self.assertAlmostEqual(payoff_ratio(40.5, 0.31), 0.31 * 0.595 / 0.405, places=6)

    def test_a_coin_flip_at_even_odds_gets_no_stake(self) -> None:
        from ta.execution.sizing import kelly_fraction

        self.assertAlmostEqual(kelly_fraction(0.5, 1.0), 0.0, places=9)

    def test_a_negative_edge_returns_zero_not_a_short(self) -> None:
        from ta.execution.sizing import kelly_fraction, payoff_ratio

        # Every one of our four spot strategies, out of sample.
        for win_pct, pf in ((40.5, 0.31), (21.7, 0.27), (31.6, 0.25), (37.0, 0.51)):
            self.assertEqual(kelly_fraction(win_pct / 100.0, payoff_ratio(win_pct, pf)), 0.0)

    def test_a_real_edge_is_capped(self) -> None:
        from ta.execution.sizing import kelly_fraction

        self.assertGreater(kelly_fraction(0.55, 1.8), 0.2)
        self.assertLessEqual(kelly_fraction(0.90, 5.0), 0.25)


class MonitorTests(unittest.TestCase):
    """The six-dimension environment score (README section 29).

    The study in scripts/monitor_study.py found the score does NOT separate
    forward returns the way the framework claims - risk_off buckets had the
    BETTER forward 24h returns in both halves of the sample. These tests pin the
    mechanics, not the claim.
    """

    def _bars(self, step: float, n: int = 400) -> list[Bar]:
        out = []
        price = 100.0
        for i in range(n):
            # Two steps forward, one back: a real tape has counter-moves, and a
            # strictly monotone series pins RSI at 100, which the crowding
            # dimension would (correctly) score as parabolic rather than healthy.
            price *= 1.0 + (-step if i % 3 == 2 else 2.0 * step)
            out.append(
                Bar(ts=i * 3_600_000, open=price, high=price * 1.002,
                    low=price * 0.998, close=price, volume=100.0)
            )
        return out

    def test_a_rising_universe_scores_high_and_keeps_full_size(self) -> None:
        from ta.monitor import detect

        state = detect({f"S{i}/USDT": self._bars(0.004) for i in range(10)})
        self.assertEqual(state.symbols, 10)
        self.assertGreaterEqual(state.score, 20)
        self.assertGreaterEqual(state.multiplier, 0.75)
        self.assertIn(state.label, ("risk_on", "neutral"))

    def test_a_falling_universe_triggers_risk_off(self) -> None:
        from ta.monitor import detect

        state = detect({f"S{i}/USDT": self._bars(-0.004) for i in range(10)})
        self.assertGreaterEqual(state.reds, 3)
        self.assertEqual(state.label, "risk_off")
        self.assertLessEqual(state.multiplier, 0.25)

    def test_missing_funding_scores_neutral_rather_than_passing(self) -> None:
        from ta.monitor import detect

        bars = {f"S{i}/USDT": self._bars(0.001) for i in range(6)}
        without = detect(bars)
        with_rate = detect(bars, funding_rate=0.0009)
        funding = next(d for d in with_rate.dimensions if d.name == "funding")
        neutral = next(d for d in without.dimensions if d.name == "funding")
        self.assertEqual(neutral.score, 3)
        self.assertIsNone(neutral.value)
        # a heavily positive funding rate means crowded longs, so it must DEDUCT
        self.assertLess(funding.score, neutral.score)

    def test_too_little_history_is_skipped_not_scored(self) -> None:
        from ta.monitor import detect

        state = detect({"NEW/USDT": self._bars(0.01, n=20)})
        self.assertEqual(state.symbols, 0)
        # no symbols -> every dimension neutral -> the middle of the ladder
        self.assertEqual(state.score, 18)


class LegSplitTests(unittest.TestCase):
    """The 50/50 book must be two budgets, not one shared pot (README 20/28).

    Without per-leg ceilings the split degrades exactly as you would fear:
    whichever leg fills first consumes the budget the other was promised, and
    "half in spot, half in derivatives" becomes "all in whichever moved first".
    """

    def setUp(self) -> None:
        from ta.risk.engine import RiskEngine

        self.config = Config.load(CONFIG_DIR)
        self.tmp = tempfile.TemporaryDirectory()
        self.kill = Path(self.tmp.name) / "KILL_SWITCH"
        self.tiers = dict(self.config.symbol_tiers)
        self.tiers["ETH/USDT:USDT"] = "alts"
        self.risk = RiskEngine(
            self.config.risk, self.kill, self.config.onchain, symbol_tiers=self.tiers
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_symbol_with_a_settlement_suffix_is_the_derivative_leg(self) -> None:
        from ta.risk.engine import leg_of

        self.assertEqual(leg_of("SOL/USDT"), "spot")
        self.assertEqual(leg_of("SOL/USDT:USDT"), "derivatives")

    def test_spot_and_derivatives_do_not_share_a_budget(self) -> None:
        # 60% of a $10k book already sits in spot, so the spot leg's 50% is spent
        # while the derivatives leg's 50% is untouched.
        account_state = account(
            cash=4_000.0,
            equity=10_000.0,
            positions={"BTC/USDT": Position("BTC/USDT", 0.1, 60_000.0)},
        )
        marks = {"BTC/USDT": 60_000.0, "ETH/USDT": 3_000.0, "ETH/USDT:USDT": 3_000.0}

        spot = self.risk.pre_trade(
            OrderRequest("s1", "ETH/USDT", "buy", 0.5), account_state, 3_000.0, marks
        )
        self.assertTrue(spot.rejected)
        self.assertIn("leg_room: spot", spot.reason)

        perp = self.risk.pre_trade(
            OrderRequest("d1", "ETH/USDT:USDT", "buy", 0.5),
            account_state,
            3_000.0,
            marks,
        )
        self.assertTrue(perp.approved, perp.reason)

    def test_each_leg_has_its_own_daily_entry_budget(self) -> None:
        # Relax the per-symbol budget so this test isolates the LEG budget; the
        # symbols.toml max_trades_per_symbol_per_day of 2 would otherwise stop the
        # loop first and the leg cap would never be reached.
        import dataclasses

        relaxed = dataclasses.replace(
            self.config.risk,
            account=dataclasses.replace(
                self.config.risk.account, max_trades_per_day=500
            ),
            symbols=dataclasses.replace(
                self.config.risk.symbols, max_trades_per_symbol_per_day=500
            ),
            tiers={
                name: dataclasses.replace(tier, max_trades_per_day=500)
                for name, tier in self.config.risk.tiers.items()
            },
            # Lower the SPOT leg's own budget so the leg cap is unambiguously the
            # one that binds - otherwise the tier budget (ETH is a major, 6/day)
            # stops the loop and this test would pass for the wrong reason.
            legs={
                **self.config.risk.legs,
                "spot": dataclasses.replace(
                    self.config.risk.legs["spot"], max_trades_per_day=3
                ),
            },
        )
        self.risk = RiskEngine(relaxed, self.kill, self.config.onchain, symbol_tiers=self.tiers)
        account_state = account(cash=9_000.0, equity=10_000.0)
        marks = {"ETH/USDT": 3_000.0, "ETH/USDT:USDT": 3_000.0}
        spot_cap = relaxed.legs["spot"].max_trades_per_day
        # Step the clock past the per-minute limiter instead of sleeping; a fixed
        # start keeps it inside one UTC day so the daily counters accumulate.
        clock = [1_700_000_000.0]
        self.risk._time = lambda: clock[0]
        for i in range(spot_cap):
            clock[0] += 61.0
            decision = self.risk.pre_trade(
                OrderRequest(f"s{i}", "ETH/USDT", "buy", 0.02), account_state, 3_000.0, marks
            )
            self.assertTrue(decision.approved, decision.reason)
        clock[0] += 61.0
        blocked = self.risk.pre_trade(
            OrderRequest("sX", "ETH/USDT", "buy", 0.02), account_state, 3_000.0, marks
        )
        self.assertTrue(blocked.rejected)
        self.assertIn("leg_daily_cap: spot", blocked.reason)
        # the derivative leg's budget is a separate counter and is still open
        clock[0] += 61.0
        perp = self.risk.pre_trade(
            OrderRequest("d1", "ETH/USDT:USDT", "buy", 0.02), account_state, 3_000.0, marks
        )
        self.assertTrue(perp.approved, perp.reason)
        self.assertEqual(self.risk.stats()["entries_by_leg"]["spot"], spot_cap)

    def test_config_refuses_legs_that_oversubscribe_the_account(self) -> None:
        raw = {
            "account": {"max_gross_exposure_pct": 60.0, "max_heat_pct": 3.0},
            "legs": {
                "spot": {"max_gross_exposure_pct": 50.0, "max_heat_pct": 2.0},
                "derivatives": {"max_gross_exposure_pct": 50.0, "max_heat_pct": 2.0},
            },
        }
        from ta.config import RiskConfig

        limits = RiskConfig.from_dict(raw)
        self.assertEqual(limits.legs["spot"].max_gross_exposure_pct, 50.0)
        self.assertEqual(limits.legs["derivatives"].max_heat_pct, 2.0)
        # 50+50 > 60 and 2+2 > 3: both are caught by Config.validate, exercised
        # here through the same arithmetic the real config uses.
        self.assertGreater(
            sum(leg.max_gross_exposure_pct for leg in limits.legs.values()),
            limits.account.max_gross_exposure_pct,
        )
        self.assertGreater(
            sum(leg.max_heat_pct for leg in limits.legs.values()),
            limits.account.max_heat_pct,
        )


class CcxtSpotTdModeTests(unittest.TestCase):
    """Spot tdMode must follow the ACCOUNT level, and a buy must state its unit.

    Root cause of the 2026-10-06/07 entry outage. The demo account was moved to
    跨币种保证金模式 (acctLv=3); from then on every spot order pinned to
    tdMode="cash" was refused with sCode 51000 "Parameter tdMode error", so the
    loop could not open anything. Probe against the live demo venue:

        no tdMode            -> 51000
        tdMode=cash          -> 51000
        tdMode=cross         -> accepted (size/other checks proceed)
        tgtCcy=base / quote  -> 51000 "Parameter tgtCcy error" (only *_ccy is legal)

    And the trap inside the obvious fix: ccxt only attaches `tgtCcy` while tdMode
    is "cash", so switching to "cross" makes OKX read a market BUY's `sz` as QUOTE
    currency. Measured: "buy 1.0 LTC" filled 0.014814 LTC = 1.00 USDT, i.e. ~1/67
    of the intended position. The buy leg therefore has to name its unit.
    """

    def _venue(self, td_mode: str):
        from ta.venues.ccxt_venue import CcxtVenue

        venue = CcxtVenue("okx", api_key="k", secret="s", password="p", sandbox=True)
        venue.spot_td_mode = td_mode
        sent: dict = {}

        class _Ex:
            markets = {
                "LTC/USDT": {
                    "spot": True,
                    "contract": False,
                    "base": "LTC",
                    "quote": "USDT",
                    "limits": {"amount": {"min": 0.001}},
                }
            }

            def create_order(self, symbol, type_, side, amount, price, params):
                sent.update(params)
                sent["_amount"] = amount
                return {
                    "id": "1",
                    "clientOrderId": params["clientOrderId"],
                    "symbol": symbol,
                    "side": side,
                    "type": type_,
                    "amount": amount,
                    "filled": amount,
                    "average": 67.5,
                    "status": "closed",
                    "fee": {"cost": 0.1, "currency": "USDT"},
                }

            def fetch_balance(self, params=None):
                return {"LTC": {"free": 3.0}, "USDT": {"free": 1000.0}}

            def amount_to_precision(self, symbol, amount):
                return amount

        venue.ex = _Ex()
        return venue, sent

    def _order(self, venue, side: str, qty: float = 3.0):
        request = OrderRequest(
            client_order_id=f"t{side}", symbol="LTC/USDT", side=side,
            qty=qty, type="market",
        )
        return venue.place_order(request)

    def test_a_sub_minimum_sell_is_refused_locally_as_dust(self) -> None:
        """Reported again on 2026-10-08 (SUI/USDT 1.4e-06), after the 2026-10-01
        fix - so the fix did not cover this path. Clamping a sell to the venue's
        free balance can leave a sub-precision amount, and sending it earns an
        OKX rejection every tick. It must be refused here instead.
        """
        venue, sent = self._venue("cross")
        venue.ex.markets.setdefault("SUI/USDT", {})  # min_qty needs a catalogue
        venue.ex.markets["SUI/USDT"] = {
            "spot": True,
            "contract": False,
            "base": "SUI",
            "quote": "USDT",
            "limits": {"amount": {"min": 1e-5}},
        }
        venue.ex.fetch_balance = lambda params=None: {"SUI": {"free": 1.4e-06},
                                                      "USDT": {"free": 10.0}}
        order = venue.place_order(
            OrderRequest(client_order_id="d1", symbol="SUI/USDT", side="sell",
                         qty=1e-06, type="market")
        )
        self.assertEqual(order.status, "rejected")
        self.assertIn("dust", order.reason)
        self.assertEqual(sent, {}, "a dust order must not reach the venue")

    def test_margin_level_account_buys_with_cross_and_states_the_unit(self) -> None:
        venue, sent = self._venue("cross")
        order = self._order(venue, "buy")
        self.assertEqual(order.status, "filled")
        self.assertEqual(sent["tdMode"], "cross")
        self.assertEqual(sent["tgtCcy"], "base_ccy", "or the size means USDT")
        self.assertEqual(sent["_amount"], 3.0, "3 LTC must stay 3 LTC, not 3 USDT")

    def test_simple_account_keeps_cash(self) -> None:
        venue, sent = self._venue("cash")
        self._order(venue, "buy")
        self.assertEqual(sent["tdMode"], "cash")

    def test_sell_never_carries_tgtccy(self) -> None:
        venue, sent = self._venue("cross")
        self._order(venue, "sell")
        self.assertEqual(sent["tdMode"], "cross")
        self.assertNotIn("tgtCcy", sent, "sells are always quoted in base units")

    def test_account_level_two_or_more_resolves_to_cross(self) -> None:
        from ta.venues.ccxt_venue import CcxtVenue

        for level, expected in ((1, "cash"), (2, "cross"), (3, "cross"), (4, "cross")):
            venue = CcxtVenue("okx", api_key="k", secret="s", password="p", sandbox=True)

            class _Ex:
                def load_markets(self):
                    return {}

                def privateGetAccountConfig(self):
                    return {"data": [{"acctLv": str(level)}]}

            venue.ex = _Ex()
            venue.connect()
            self.assertEqual(venue.spot_td_mode, expected, f"acctLv={level}")

    def test_a_failed_level_probe_falls_back_to_cash(self) -> None:
        from ta.venues.ccxt_venue import CcxtVenue

        venue = CcxtVenue("okx", api_key="k", secret="s", password="p", sandbox=True)

        class _Ex:
            def load_markets(self):
                return {}

            def privateGetAccountConfig(self):
                raise RuntimeError("network down")

        venue.ex = _Ex()
        venue.connect()
        self.assertEqual(venue.spot_td_mode, "cash")


class ReconcileCadenceTests(unittest.TestCase):
    """A restart must not push the book-vs-venue check out of reach.

    Measured 2026-10-08: the runner restarted 4 times inside 85 minutes while one
    tick takes ~103s, so the 30-tick (≈52 min) reconciliation period was longer
    than the process uptime and the check never ran - the one guard that exists to
    catch a stale book was silently skipped. A fresh runner is therefore seeded
    one tick short of the period, so it reconciles on its first good tick.
    """

    def test_first_tick_after_restart_reconciles(self) -> None:
        from ta.runner import TradingRunner

        with tempfile.TemporaryDirectory() as tmp:
            runner = TradingRunner.build(
                Config.load(str(PROJECT / "config")),
                setup(Path(tmp) / "run.log", level="CRITICAL", console=False),
                venue_name="paper",
            )
            self.assertGreaterEqual(runner.reconcile_every_ticks, 2)
            # The loop increments the counter and then compares it against the
            # period, so a fresh runner must already sit one short of it.
            self.assertGreaterEqual(
                runner._ticks_since_reconcile + 1, runner.reconcile_every_ticks
            )


class DataFetchSourceTests(unittest.TestCase):
    """`data fetch --source live` must not write into the demo directory.

    Measured 2026-10-08: 21 of 427 spot+perp symbols quote real candles only on
    the live catalogue, while the demo catalogue answers `code:0, data:[]` - a
    silent empty rather than an error, so it reads as "no data for this symbol"
    instead of "wrong catalogue". Merging the two series into one directory would
    let a backtest mix catalogues without saying so.
    """

    def test_live_series_get_their_own_directory(self) -> None:
        from ta.cli import _history_store_venue

        self.assertEqual(_history_store_venue("okx_demo", "demo"), "okx_demo")
        self.assertEqual(_history_store_venue("okx_demo", "live"), "okx_demo_live")


class PerpShortBookTests(unittest.TestCase):
    """A perp sell must be able to OPEN a short.

    The book clamped every sell with `min(qty, position.qty)`, which is right for
    spot and silently wrong for a swap leg. Measured 2026-10-08: the BAND carry
    hedge (sell 4431 BAND-USDT-SWAP) became `min(4431, 0) = 0`, so the pair never
    registered a hedge; the runner re-sent it on every restart (six orders, ~$1,600
    notional) and reconciliation mismatched every 52 minutes. The book's BAND value
    was exactly the running sum of the BUY legs - 4159 = 1249+1253+1253+404.
    """

    def _fill(self, symbol: str, side: str, qty: float, price: float, ts: int = 1) -> Fill:
        return Fill(
            client_order_id=f"t{ts}", symbol=symbol, side=side, qty=qty,
            price=price, fee=0.0, fee_ccy="USDT", ts=ts, tag="t",
        )

    def test_perp_sell_opens_a_short(self) -> None:
        book = PositionBook(base_ccy="USDT")
        book.mark("BAND/USDT:USDT", 0.23)
        book.apply_fill(self._fill("BAND/USDT:USDT", "sell", 4431.0, 0.230964))
        position = book.positions["BAND/USDT:USDT"]
        self.assertAlmostEqual(position.qty, -4431.0)
        self.assertAlmostEqual(position.avg_price, 0.230964)
        # Opening a swap moves NO notional: cash is untouched and nothing is
        # realised. Crediting the proceeds is the spot model, and on this ledger's
        # $3.42M of historical swap sells it would have "created" $3.42M of cash
        # on a $101k account.
        self.assertAlmostEqual(book.cash, 0.0)
        self.assertAlmostEqual(book.realized_total, 0.0)
        # And the short shows up as unrealised PnL, never as notional.
        book.mark("BAND/USDT:USDT", 0.20)
        self.assertAlmostEqual(book.equity(), (0.230964 - 0.20) * 4431.0)
        # And the short is a visible position, not dust.
        self.assertIn("BAND/USDT:USDT", book.open_positions())

    def test_the_band_regression_replays_as_a_short_leg(self) -> None:
        """The exact fill sequence from the 2026-10-08 incident."""
        fills = [
            self._fill("BAND/USDT", "buy", 4431.298806, 0.225678, ts=1),
            self._fill("BAND/USDT:USDT", "sell", 4431.0, 0.230964, ts=2),
            self._fill("BAND/USDT:USDT", "buy", 1249.0, 0.232937, ts=3),
            self._fill("BAND/USDT:USDT", "buy", 1253.0, 0.232941, ts=4),
            self._fill("BAND/USDT:USDT", "buy", 1253.0, 0.232941, ts=5),
            self._fill("BAND/USDT:USDT", "buy", 404.0, 0.233248, ts=6),
        ]
        book = PositionBook.replay(fills, base_ccy="USDT")
        # Before the fix the hedge vanished and the perp leg read +4159 (the sum
        # of the buys). The netted truth is short 272.
        self.assertAlmostEqual(book.positions["BAND/USDT:USDT"].qty, -272.0)

    def test_covering_a_short_realises_the_difference(self) -> None:
        book = PositionBook(base_ccy="USDT")
        book.mark("X/USDT:USDT", 0.20)
        book.apply_fill(self._fill("X/USDT:USDT", "sell", 100.0, 0.25, ts=1))
        book.apply_fill(self._fill("X/USDT:USDT", "buy", 100.0, 0.20, ts=2))
        self.assertAlmostEqual(book.positions["X/USDT:USDT"].qty, 0.0)
        self.assertAlmostEqual(book.realized_total, (0.25 - 0.20) * 100.0)
        self.assertNotIn("X/USDT:USDT", book.open_positions())

    def test_a_spot_oversell_is_still_clamped(self) -> None:
        """The spot invariant this fix must not break: you cannot sell what you
        do not have, so an oversell stays a data error rather than a fake short."""
        book = PositionBook(base_ccy="USDT")
        book.mark("BAND/USDT", 0.23)
        book.apply_fill(self._fill("BAND/USDT", "buy", 10.0, 0.20, ts=1))
        book.apply_fill(self._fill("BAND/USDT", "sell", 25.0, 0.25, ts=2))
        self.assertAlmostEqual(book.positions["BAND/USDT"].qty, 0.0)
class CashBasisTests(unittest.TestCase):
    """The cash comparison must use cash WITHOUT open-position unrealised PnL.

    ccxt maps OKX's `free` and `total` to `eq` = cashBal + upl (measured
    2026-10-09: cashBal 97,708.60 vs eq 97,934.29, the +$225.69 being the CRV
    hedge's unrealised PnL). Comparing against that made the residual drift with
    the mark price of every open swap.
    """

    def test_snapshot_prefers_cash_bal(self) -> None:
        from ta.execution.oms import OMS

        class _Venue:
            name = "v"

            def account_snapshot(self):
                return {"cash": 97934.29, "cash_bal": 97708.60, "positions": {}}

        class _Book:
            cash = 0.0

            def open_positions(self):
                return {}

            def equity(self):
                return 0.0

            marks: dict = {}

        report = OMS(_Venue(), None).reconcile(  # type: ignore[arg-type]
            AccountState(venue="v"), _Book(), cash_baseline=97708.60, cash_tolerance=1.0
        )
        self.assertTrue(report.ok, report.diffs)
        self.assertAlmostEqual(report.local["cash_actual"], 97708.60)

    def test_snapshot_falls_back_to_cash_when_cash_bal_is_absent(self) -> None:
        from ta.execution.oms import OMS

        class _Venue:
            name = "v"

            def account_snapshot(self):
                return {"cash": 100.0, "positions": {}}

        class _Book:
            cash = 5.0
            marks: dict = {}

            def open_positions(self):
                return {}

            def equity(self):
                return 0.0

        report = OMS(_Venue(), None).reconcile(  # type: ignore[arg-type]
            AccountState(venue="v"), _Book(), cash_baseline=95.0, cash_tolerance=1.0
        )
        self.assertTrue(report.ok, report.diffs)


class ReconcileSwapFixTests(unittest.TestCase):
    """The swap-side write-off must be PnL-neutral.

    Every rule here is a measured incident, not a guess:

      * the first version priced the repair at the MARKET, which booked $35,956 of
        phantom realised PnL on eleven legs that never existed at the venue and
        moved the cash flow by +$31,765 - a six-figure cash mismatch;
      * the same version flattened CRV to zero even though the venue really held
        7387 short, i.e. it wrote off a position that was real.
    """

    def _plan(self, symbol="BAND/USDT:USDT", local=-19020.0, actual=0.0, avg=0.0101678):
        from scripts.reconcile_ledger_to_venue import plan_swap_fix

        return plan_swap_fix(symbol, local, actual, avg)

    def test_a_phantom_short_is_bought_back_at_the_ledger_entry_price(self) -> None:
        side, qty, price = self._plan()
        self.assertEqual(side, "buy")
        self.assertAlmostEqual(qty, 19020.0)
        # The entry price, NOT the ticker: closing at market is what created the
        # phantom PnL.
        self.assertAlmostEqual(price, 0.0101678)

    def test_a_phantom_long_is_sold_at_the_ledger_entry_price(self) -> None:
        side, qty, price = self._plan(local=2361.0, avg=0.233809)
        self.assertEqual(side, "sell")
        self.assertAlmostEqual(qty, 2361.0)
        self.assertAlmostEqual(price, 0.233809)

    def test_only_the_gap_is_corrected_when_the_venue_holds_the_leg(self) -> None:
        """CRV: ledger -15501, venue -7387 -> buy back 8114, leaving -7387."""
        side, qty, price = self._plan(local=-15501.0, actual=-7387.0, avg=0.3884782)
        self.assertEqual(side, "buy")
        self.assertAlmostEqual(qty, 8114.0)
        self.assertAlmostEqual(price, 0.3884782)

    def test_nothing_is_owed_when_the_venue_holds_more(self) -> None:
        self.assertIsNone(self._plan(local=-100.0, actual=-5000.0))

    def test_nothing_is_owed_when_they_agree(self) -> None:
        self.assertIsNone(self._plan(local=-7387.0, actual=-7387.0))

    def test_a_missing_entry_price_is_not_guessed(self) -> None:
        self.assertIsNone(self._plan(avg=0.0))


class FundingAccountingTests(unittest.TestCase):
    """Funding is cash with no position attached, and the book must carry it.

    OKX settles it three times a day on every open swap; verified 2026-10-08 that
    consecutive swap bills' `bal` delta equals exactly (pnl + fee), funding
    included. The book recorded none of it (11 settlements, +$12.63), so the flow
    the reconciler adds to its baseline was permanently short.
    """

    def _ledger(self, tmp: str):
        return Ledger(Path(tmp) / "ledger.jsonl", run_id="t", venue="okx_demo")

    def test_funding_moves_cash_and_realised(self) -> None:
        book = PositionBook(base_ccy="USDT")
        book.apply_funding(2.5)
        self.assertAlmostEqual(book.cash, 2.5)
        self.assertAlmostEqual(book.realized_total, 2.5)
        book.apply_funding(-1.0)
        self.assertAlmostEqual(book.cash, 1.5)

    def test_zero_funding_is_a_no_op(self) -> None:
        book = PositionBook(base_ccy="USDT")
        self.assertEqual(book.apply_funding(0.0), 0.0)
        self.assertAlmostEqual(book.cash, 0.0)

    def test_rebuild_book_includes_funding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            ledger.record_fill(
                Fill(client_order_id="a", symbol="BAND/USDT", side="buy", qty=10.0,
                     price=0.20, fee=0.0, fee_ccy="USDT", ts=1, tag="t")
            )
            ledger.record_funding("BAND/USDT:USDT", 3.25, "bill-1")
            book = ledger.rebuild_book(base_ccy="USDT", venue="okx_demo")
            # -2.00 for the spot buy, +3.25 funding.
            self.assertAlmostEqual(book.cash, 1.25)
            # A fills-only replay is what the bug was: it misses the funding row.
            fills_only = PositionBook.replay(ledger.fills_for(venue="okx_demo"), base_ccy="USDT")
            self.assertAlmostEqual(fills_only.cash, -2.0)

    def test_fills_for_still_returns_only_fills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            ledger.record_funding("BAND/USDT:USDT", 1.0, "bill-1")
            self.assertEqual(ledger.fills_for(venue="okx_demo"), [])
            self.assertEqual(len(ledger.events_for(venue="okx_demo")), 1)

    def test_a_funding_row_keeps_the_venues_own_settlement_time(self) -> None:
        """`ts` 是写入时刻、`bill_ts` 是场馆结算时刻 —— 只有后者能回答
        「这笔钱是哪天挣的」（2026-10-12 复盘补记那一栏就靠它）。
        老调用不带 `bill_ts` 时不写这个字段，读者按未知处理。"""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self._ledger(tmp)
            ledger.record_funding("CRV/USDT:USDT", 43.0, "bill-1", bill_ts=1786000000000)
            ledger.record_funding("ACT/USDT:USDT", 1.0, "bill-2")
            rows = ledger.events_for(venue="okx_demo")
            self.assertEqual(rows[0]["bill_ts"], 1786000000000)
            self.assertNotIn("bill_ts", rows[1])

    def test_reload_picks_up_rows_written_by_another_process(self) -> None:
        """2026-10-12 对账残余的最终根因：账本是**多进程**写的（runner / funding-sync /
        每日任务），而 `Ledger.__init__` 只读一次文件 —— 于是长跑的 runner 看不见别的
        进程写进来的资金费，它的 `our flow` 冻结在启动那一刻，场馆每收一笔资金费就变成
        一条「现金残余」，看门狗只好在 info/warn 之间来回翻。

        `reload()` 回读文件后两边看同一份账；重复回读不会重复计数。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            runner_view = Ledger(path, run_id="runner", venue="okx_demo")
            runner_view.record_funding("AAA/USDT:USDT", 1.0, "bill-1")
            before = runner_view.rebuild_book(base_ccy="USDT", venue="okx_demo").cash

            other = Ledger(path, run_id="funding-sync", venue="okx_demo")
            other.record_funding("AAA/USDT:USDT", 2.5, "bill-2")

            # 不回读 = 旧行为：别人的行看不见，账目少 2.5
            stale = runner_view.rebuild_book(base_ccy="USDT", venue="okx_demo").cash
            self.assertAlmostEqual(stale, before, places=9)

            count = runner_view.reload()
            fresh = runner_view.rebuild_book(base_ccy="USDT", venue="okx_demo").cash
            self.assertAlmostEqual(fresh, before + 2.5, places=9)
            self.assertEqual(count, len(runner_view.events))

            # 回读两次不会翻倍
            runner_view.reload()
            self.assertAlmostEqual(
                runner_view.rebuild_book(base_ccy="USDT", venue="okx_demo").cash,
                before + 2.5, places=9,
            )

    def test_a_bill_id_makes_the_sync_idempotent(self) -> None:
        from scripts.sync_funding import new_funding_bills

        bills = [
            {"type": "8", "billId": "b2", "ts": "200", "pnl": "1.0", "instId": "BAND-USDT-SWAP"},
            {"type": "8", "billId": "b1", "ts": "100", "pnl": "2.0", "instId": "CRV-USDT-SWAP"},
            {"type": "2", "billId": "t1", "ts": "300", "pnl": "9.9", "instId": "CRV-USDT-SWAP"},
        ]
        fresh = new_funding_bills(bills, already=set())
        self.assertEqual([b["billId"] for b in fresh], ["b1", "b2"])   # oldest first
        self.assertEqual(new_funding_bills(bills, already={"b1", "b2"}), [])

    def test_venue_inst_id_maps_to_the_project_symbol(self) -> None:
        from scripts.sync_funding import symbol_of

        self.assertEqual(symbol_of("BAND-USDT-SWAP"), "BAND/USDT:USDT")
        self.assertEqual(symbol_of("BTC-USDT-SWAP"), "BTC/USDT:USDT")

    def test_env_file_is_read_without_clobbering_the_environment(self) -> None:
        """The launchd job exports the proxy but NOT the API keys, so the sync
        step would have failed every morning at 09:10 with "API key is not set"."""
        import os

        from scripts.sync_funding import load_env_file

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                '# comment\nOKX_DEMO_API_KEY="from-file"\nEMPTY=\nTA_TEST_PRESET=file\n',
                encoding="utf-8",
            )
            os.environ["TA_TEST_PRESET"] = "env-wins"
            for key in ("OKX_DEMO_API_KEY", "EMPTY"):
                os.environ.pop(key, None)
            try:
                loaded = load_env_file(path)
                self.assertEqual(loaded, 1)                      # only the real key
                self.assertEqual(os.environ["OKX_DEMO_API_KEY"], "from-file")
                self.assertEqual(os.environ["TA_TEST_PRESET"], "env-wins")
                self.assertNotIn("EMPTY", os.environ)
            finally:
                os.environ.pop("OKX_DEMO_API_KEY", None)
                os.environ.pop("TA_TEST_PRESET", None)


class CashResidualStabilityTests(unittest.TestCase):
    """The cash residual cannot reach zero on this account, so judge stability.

    Measured 2026-10-08: the demo account carries 2,404 venue bills against our 140
    fills, so `baseline + our flow == venue cash` misses by a settled ~$1,062. The
    old dashboard flagged "reconciliation mismatch" by COUNT, which meant a stable
    account warned forever; a moving residual is the real signal.
    """

    DIFF = (
        "cash delta: expected 98486.20042395 (baseline 103358.46688886615 + our "
        "flow -4872.26646492) but venue shows 97423.84241606"
    )

    def test_residual_is_expected_minus_actual(self) -> None:
        from ta.dashboard import cash_residuals

        ledger = [
            {"kind": "risk", "action": "reconciliation mismatch",
             "diffs": [self.DIFF, "position CRV/USDT:USDT: local=-7387.0 venue=0.0"]},
            {"kind": "risk", "action": "other", "diffs": [self.DIFF]},   # ignored
        ]
        out = cash_residuals(ledger)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0], 98486.20042395 - 97423.84241606, places=6)

    def test_position_diffs_are_not_cash(self) -> None:
        from ta.dashboard import cash_residuals, position_diff_count

        diffs = [
            "position ACT/USDT:USDT: local=-19020.0 venue=0.0",
            "position SUI/USDT:USDT: local=-28842.0 venue=0.0",
            self.DIFF,
        ]
        self.assertEqual(position_diff_count(diffs), 2)
        self.assertEqual(len(cash_residuals([{"kind": "risk",
                                               "action": "reconciliation mismatch",
                                               "diffs": diffs}])), 1)

    def test_a_settled_residual_is_information_not_a_warning(self) -> None:
        from ta.dashboard import cash_residual_flag

        level, detail = cash_residual_flag([1060.0, 1061.0, 1062.36])
        self.assertEqual(level, "info")
        self.assertIn("1,062.36", detail)

    def test_a_moving_residual_is_a_warning(self) -> None:
        from ta.dashboard import cash_residual_flag

        level, detail = cash_residual_flag([1062.0, 2000.0])
        self.assertEqual(level, "warn")
        self.assertIn("漂移", detail)

    def test_a_settled_excursion_stops_warning(self) -> None:
        """The cry-wolf case, measured 2026-10-09: the residual spiked to $1,062
        and came back, and a range-based rule kept the warning on for ~8 hours
        while the last four checks sat within $40 of each other."""
        from ta.dashboard import cash_residual_flag

        series = [1062.36, 1102.99, 978.15, 610.97, 767.58, 732.12, 718.46, 711.08, 692.04]
        level, detail = cash_residual_flag(series)
        self.assertEqual(level, "info")
        self.assertIn("692.04", detail)

class CarryEligibilityTests(unittest.TestCase):
    """The three gates that keep a spike-only contract out of the carry list.

    Measured 2026-10-09: the mean-ranked leaderboard was led by NG - "114%
    annualised, breakeven 1.4 days" - whose 306 prints contain 174 exact zeros
    and only 39% positives. The mean IS the income; the median and the positive
    share are the robustness check that catches exactly that shape.
    """

    def _gate(self, *a, **kw):
        from scripts.carry_live import carry_eligible

        return carry_eligible(*a, **kw)

    def test_a_spike_only_contract_is_rejected(self) -> None:
        ok, why = self._gate(12.38, 0.0, 0.39, 3.6)
        self.assertFalse(ok)
        self.assertIn("spike", why)

    def test_a_low_positive_share_is_rejected(self) -> None:
        ok, why = self._gate(4.14, 1.71, 0.54, 10.8)
        self.assertFalse(ok)
        self.assertIn("positive share", why)

    def test_a_too_slow_payback_is_rejected(self) -> None:
        ok, why = self._gate(1.10, 1.00, 0.90, 40.8)
        self.assertFalse(ok)
        self.assertIn("breakeven", why)

    def test_a_negative_mean_is_rejected_even_with_a_positive_median(self) -> None:
        ok, why = self._gate(-0.5, 1.0, 0.95, None)
        self.assertFalse(ok)
        self.assertEqual(why, "mean<=0")

    def test_a_sound_contract_passes(self) -> None:
        ok, why = self._gate(8.53, 4.41, 1.00, 5.3)
        self.assertTrue(ok, why)


class CarryPairSizingTests(unittest.TestCase):
    """A carry pair is sized by NOTIONAL, not by distance to a price stop.

    Measured 2026-10-09: ACT opened at 402,735 (~$3.93k) purely because its stop
    sat 5.1% below entry - the notional was a function of the symbol's volatility,
    and the only real bound was `max_order_notional_pct`.
    """

    def test_qty_is_notional_over_price(self) -> None:
        from ta.execution.sizing import carry_pair_qty

        # 2% of $101,500 = $2,030 at 0.0098 -> ~207,143 ACT
        self.assertAlmostEqual(carry_pair_qty(101500.0, 2.0, 0.0098), 207142.857, places=2)

    def test_it_does_not_depend_on_volatility(self) -> None:
        """Two symbols with the same price but different ATR get the same notional;
        under risk_based_qty the calmer one would have been sized far larger."""
        from ta.execution.sizing import carry_pair_qty, risk_based_qty

        calm = risk_based_qty(101500.0, 0.20, 1.00, 0.99)      # 1% stop
        wild = risk_based_qty(101500.0, 0.20, 1.00, 0.90)      # 10% stop
        self.assertAlmostEqual(carry_pair_qty(101500.0, 2.0, 1.0), 2030.0)
        self.assertGreater(calm, wild)                          # the volatility trap

    def test_zero_or_missing_inputs_size_nothing(self) -> None:
        from ta.execution.sizing import carry_pair_qty

        self.assertEqual(carry_pair_qty(0.0, 2.0, 1.0), 0.0)
        self.assertEqual(carry_pair_qty(101500.0, 0.0, 1.0), 0.0)
        self.assertEqual(carry_pair_qty(101500.0, 2.0, 0.0), 0.0)

    def test_the_carry_tier_declares_the_notional_basis(self) -> None:
        config = Config.load(str(PROJECT / "config"))
        tier = config.risk.tiers["carry"]
        self.assertGreater(tier.carry_notional_pct, 0.0)
        self.assertLessEqual(tier.carry_notional_pct, tier.max_order_notional_pct)


class FundingIncomeTests(unittest.TestCase):
    """Realised carry income, from the venue's own bills.

    The rate history says ~3 bps/day (median) or much more (mean). Neither is what
    the account was paid. Measured 2026-10-09, holding two pairs at once gave a
    clean natural control: the older CRV leg read ~1,149 bps/day (a funding spike)
    while the pair opened that morning read 3.2 bps/day - i.e. the steady-state
    the median predicted.
    """

    def test_income_and_implied_bps_per_day(self) -> None:
        from scripts.funding_income import summarise

        bills = [
            {"instId": "ACT-USDT-SWAP", "pnl": "0.7"},
            {"instId": "ACT-USDT-SWAP", "pnl": "0.3"},
            {"instId": "CRV-USDT-SWAP", "pnl": "1.5"},
        ]
        out = summarise(bills, {"ACT/USDT:USDT": 1000.0, "CRV/USDT:USDT": 3000.0})
        self.assertAlmostEqual(out["ACT-USDT-SWAP"]["usdt"], 1.0)
        self.assertEqual(out["ACT-USDT-SWAP"]["settlements"], 2)
        self.assertAlmostEqual(out["ACT-USDT-SWAP"]["bps_per_day"], 10.0)
        self.assertAlmostEqual(out["CRV-USDT-SWAP"]["bps_per_day"], 5.0)

    def test_a_contract_with_no_open_position_reports_no_bps(self) -> None:
        """Dividing by a notional we do not have is how a closed leg turns into a
        headline number; say 'n/a' instead."""
        from scripts.funding_income import summarise

        out = summarise([{"instId": "BAND-USDT-SWAP", "pnl": "5.0"}], {})
        self.assertEqual(out["BAND-USDT-SWAP"]["usdt"], 5.0)
        self.assertIsNone(out["BAND-USDT-SWAP"]["bps_per_day"])

    def test_bps_per_day_divides_by_the_real_window_not_one_day(self) -> None:
        """2026-10-11: the window is rolling, so `--days 2` rows cover two days.

        Dividing the window's whole sum by one day inflates every rate by the
        window length; the recorded `bps_per_day` was ~2x the truth for that
        reason. The same income over two days must read half the bps.
        """
        from scripts.funding_income import summarise, window_days

        day = 86_400_000
        bills = [
            {"instId": "ACT-USDT-SWAP", "pnl": "1.0", "ts": 1_790_000_000_000},
            {"instId": "ACT-USDT-SWAP", "pnl": "1.0", "ts": 1_790_000_000_000 + day},
        ]
        self.assertAlmostEqual(window_days(bills), 1.0, places=6)
        one_day = summarise(bills, {"ACT/USDT:USDT": 1000.0}, window=1.0)
        self.assertAlmostEqual(one_day["ACT-USDT-SWAP"]["bps_per_day"], 20.0)
        two_days = summarise(bills, {"ACT/USDT:USDT": 1000.0}, window=2.0)
        self.assertAlmostEqual(two_days["ACT-USDT-SWAP"]["bps_per_day"], 10.0)

    def test_window_days_measures_the_span_actually_returned(self) -> None:
        """An account with fewer than 100 funding bills gets one whole page back,
        so a `--days 2` request can hand over three days of history."""
        from scripts.funding_income import window_days

        day = 86_400_000
        base = 1_790_000_000_000
        bills = [{"instId": "CRV-USDT-SWAP", "pnl": "1.0", "ts": base + k * day}
                 for k in range(3)]
        self.assertAlmostEqual(window_days(bills), 2.0, places=6)
        self.assertEqual(window_days([]), 1.0)
        self.assertEqual(window_days([{"instId": "X", "ts": base}]), 1.0)


class CarryIncomeReportTests(unittest.TestCase):
    """Realised carry income vs the market's own funding prints (README 71).

    2026-10-11: `data/funding/income.jsonl` stores a ROLLING window per day, so
    adding its rows double-counts. On the real file BAND's 10-08 credit appears in
    all three rows, and CRV's rows sum to 1,301.6 USDT when the venue only ever
    paid 796.1. These lock the re-derivation from raw bills.
    """

    DAY = 86_400_000
    BASE = 1_790_000_000_000 - (1_790_000_000_000 % 86_400_000)  # a UTC midnight

    def _rate_dir(self, tmp: str, name: str, rows: list[tuple[int, float, int]]) -> Path:
        path = Path(tmp) / f"{name}-USDT_USDT.jsonl"
        path.write_text(
            "\n".join(json.dumps({"ts": ts, "rate": rate}) for ts, rate, _ in rows) + "\n",
            encoding="utf-8",
        )
        return Path(tmp)

    def _rates(self, days: int, per_day_prints: int, rate: float) -> list[tuple[int, float, int]]:
        step = self.DAY // per_day_prints
        return [(self.BASE + d * self.DAY + k * step, rate, k)
                for d in range(days) for k in range(per_day_prints)]

    def test_realised_income_is_bucketed_by_utc_day(self) -> None:
        from scripts.carry_income_report import realised_by_day

        bills = [
            {"instId": "ACT-USDT-SWAP", "pnl": "1.0", "ts": self.BASE + 3_600_000},
            {"instId": "ACT-USDT-SWAP", "pnl": "2.0", "ts": self.BASE + 7_200_000},
            {"instId": "ACT-USDT-SWAP", "pnl": "4.0", "ts": self.BASE + self.DAY + 3_600_000},
        ]
        out = realised_by_day(bills)
        days = sorted(out)
        self.assertEqual(len(days), 2)
        self.assertAlmostEqual(out[days[0]]["ACT-USDT-SWAP"]["usdt"], 3.0)
        self.assertEqual(out[days[0]]["ACT-USDT-SWAP"]["settles"], 2)
        self.assertAlmostEqual(out[days[1]]["ACT-USDT-SWAP"]["usdt"], 4.0)
        self.assertEqual(out[days[1]]["ACT-USDT-SWAP"]["settles"], 1)

    def test_rate_history_sums_each_day_and_reads_the_interval(self) -> None:
        from scripts.carry_income_report import rate_summary

        with tempfile.TemporaryDirectory() as tmp:
            # 3 prints a day at 1 bp each -> 3 bps/day, 8h interval
            rate_dir = self._rate_dir(tmp, "ACT", self._rates(days=5, per_day_prints=3,
                                                              rate=0.0001))
            out = rate_summary(rate_dir, "ACT", 30)
            self.assertEqual(out["days"], 5)
            self.assertAlmostEqual(out["median_bps"], 3.0, places=6)
            self.assertAlmostEqual(out["settle_hours"], 8.0, places=6)
            self.assertEqual(out["negative_days"], 0)
            self.assertIsNone(rate_summary(rate_dir, "NOPE", 30))

    def test_a_demo_artefact_is_dropped_from_the_basket_not_averaged_in(self) -> None:
        """CRV-like: the market pays 2 bps/day while the demo credited 700.

        Including that row would set the basket rate 350x too high and turn a
        22-day payback into a 0.06-day one.
        """
        from scripts.carry_income_report import build_report

        with tempfile.TemporaryDirectory() as tmp:
            rate_dir = self._rate_dir(tmp, "SUI", self._rates(days=5, per_day_prints=3,
                                                              rate=0.0000666))
            self._rate_dir(tmp, "CRV", self._rates(days=5, per_day_prints=3, rate=0.0000666))
            realised = {
                "2026-10-09": {"SUI-USDT-SWAP": {"usdt": 0.31, "settles": 2},
                               "CRV-USDT-SWAP": {"usdt": 200.0, "settles": 12}},
                "2026-10-10": {"SUI-USDT-SWAP": {"usdt": 0.31, "settles": 2},
                               "CRV-USDT-SWAP": {"usdt": 200.0, "settles": 12}},
            }
            notionals = {"SUI/USDT:USDT": 2000.0, "CRV/USDT:USDT": 2800.0}
            report = build_report(
                realised=realised, notionals=notionals, rate_dir=rate_dir,
                names=[], days=30, artefact_ratio=3.0, round_trip_bps=44.9, min_days=5,
            )
        self.assertEqual(report["basket_names"], ["SUI"])
        self.assertIn("CRV", report["excluded"])
        self.assertGreater(report["per_contract"]["CRV"]["realised_over_market"], 3.0)
        # SUI is credible here (0.31 USDT/day over $2k ~= 1.55 bps/day)
        self.assertLess(report["per_contract"]["SUI"]["realised_over_market"], 3.0)

    def test_breakeven_uses_the_basket_rate_and_flags_a_short_sample(self) -> None:
        from scripts.carry_income_report import build_report

        with tempfile.TemporaryDirectory() as tmp:
            rate_dir = self._rate_dir(tmp, "SUI", self._rates(days=30, per_day_prints=3,
                                                              rate=0.0000666))
            realised = {"2026-10-09": {"SUI-USDT-SWAP": {"usdt": 0.3, "settles": 2}}}
            report = build_report(
                realised=realised, notionals={"SUI/USDT:USDT": 2000.0}, rate_dir=rate_dir,
                names=[], days=30, artefact_ratio=3.0, round_trip_bps=44.9, min_days=5,
            )
        basket = report["basket_market_bps_per_day"]
        self.assertAlmostEqual(basket, round(0.0000666 * 3 * 1e4, 3), places=3)
        self.assertAlmostEqual(report["breakeven_days"], round(44.9 / basket, 1), places=1)
        self.assertFalse(report["sample_ok"])          # 1 realised day < min_days
        self.assertEqual(report["realised_days"], 1)


class DepthGateTests(unittest.TestCase):
    """Entry impact from the order book, in bps of mid.

    Measured 2026-10-09: a carry entry bought $2.2k of ACT spot 1.33% above the
    market's high for that minute while the hedge leg filled at market. That one
    leg cost 3x the 44.9 bps round trip every carry conclusion rested on, and no
    check in the pipeline looked at depth - a spread check cannot catch it,
    because the spread was fine.
    """

    def test_taking_the_touch_costs_the_half_spread(self) -> None:
        from ta.execution.sizing import book_impact_bps

        # mid 100.0, ask 100.05 -> 5 bps
        self.assertAlmostEqual(
            book_impact_bps([(100.05, 1_000_000)], 100.0, 100.0, "buy"), 5.0, places=6
        )

    def test_walking_the_book_costs_more_and_is_volume_weighted(self) -> None:
        from ta.execution.sizing import book_impact_bps

        # 100 at 100.1 then 100 at 100.3 -> vwap 100.2 vs mid 100.0 = 20 bps
        self.assertAlmostEqual(
            book_impact_bps([(100.1, 100), (100.3, 100)], 200.0, 100.0, "buy"), 20.0, places=6
        )

    def test_a_sell_is_measured_the_other_way(self) -> None:
        from ta.execution.sizing import book_impact_bps

        self.assertAlmostEqual(
            book_impact_bps([(99.95, 1_000_000)], 100.0, 100.0, "sell"), 5.0, places=6
        )

    def test_too_thin_to_fill_is_none_not_zero(self) -> None:
        """Treating an unknown as zero impact is exactly how 1.33% got through."""
        from ta.execution.sizing import book_impact_bps

        self.assertIsNone(book_impact_bps([(100.1, 10)], 1000.0, 100.0, "buy"))
        self.assertIsNone(book_impact_bps([], 1000.0, 100.0, "buy"))

    def test_the_real_act_fill_would_have_been_refused(self) -> None:
        """133 bps of measured impact against a 50 bps cap."""
        from ta.execution.sizing import book_impact_bps

        # Buy 229,123 at ~1.33% above the mid that prevailed (0.00977).
        impact = book_impact_bps([(0.0099004, 1_000_000)], 229_123.0, 0.00977, "buy")
        self.assertIsNotNone(impact)
        self.assertGreater(impact, 50.0)

    def test_the_cap_is_configured(self) -> None:
        config = Config.load(str(PROJECT / "config"))
        self.assertGreater(config.risk.per_trade.max_entry_impact_bps, 0.0)


class OptionContractUnitsTests(unittest.TestCase):
    """The 1.1 adapter's unit facts, measured 2026-10-10 with options_probe.py.

    A BTC-USD option returns ctVal=1 AND ctMult=0.01: one contract is 0.01 BTC of
    underlying, the premium is quoted in BTC, and minSz is 1 contract. The swap
    path read ctVal only - invisible for swaps (ctMult=1) and 100x wrong for
    options, either oversizing every order or flooring a correct 0.01 BTC target to
    zero contracts and refusing to trade at all.
    """

    OPTION = {
        "instId": "BTC-USD-261127-82000-C", "ctVal": "1", "ctValCcy": "BTC",
        "ctMult": "0.01", "lotSz": "1", "minSz": "1", "tickSz": "0.0001",
        "state": "live",
    }

    def test_one_option_contract_is_ctval_times_ctmult(self) -> None:
        from ta.venues.swap_math import ContractSpec, base_to_contracts, contracts_to_base

        spec = ContractSpec.from_okx(self.OPTION)
        self.assertAlmostEqual(spec.base_per_contract, 0.01, places=12)
        # 0.01 BTC of underlying is exactly one contract - not 0.01 of a contract.
        self.assertEqual(base_to_contracts(spec, 0.01), 1.0)
        self.assertAlmostEqual(contracts_to_base(spec, 1.0), 0.01, places=12)
        # ...and a target below one contract still fails loudly.
        with self.assertRaises(Exception):
            base_to_contracts(spec, 0.005)

    def test_swaps_are_unaffected(self) -> None:
        from ta.venues.swap_math import ContractSpec, base_to_contracts

        swap = ContractSpec.from_okx({
            "instId": "ACT-USDT-SWAP", "ctVal": "10", "ctValCcy": "ACT",
            "ctMult": "1", "lotSz": "1", "minSz": "1", "state": "live",
        })
        self.assertEqual(swap.base_per_contract, 10.0)
        self.assertEqual(base_to_contracts(swap, 1000.0), 100.0)

    def test_the_venue_translates_instids_to_ccxt_option_symbols(self) -> None:
        """Measured 2026-10-10: ccxt answers BadSymbol 51001 for the raw instId.

        Our internal symbol stays the OKX instId (`leg_of` classifies options by
        that shape, and every public-API probe speaks instIds); ccxt names the same
        contract `BTC/USD:BTC-261127-82000-C`. Spot/swap must stay identity.
        """
        from ta.venues.ccxt_venue import CcxtVenue

        venue = object.__new__(CcxtVenue)
        venue.market_type = "option"
        venue.ex = type("Ex", (), {"markets": {
            "BTC/USD:BTC-261127-82000-C": {
                "id": "BTC-USD-261127-82000-C",
                "symbol": "BTC/USD:BTC-261127-82000-C",
            },
        }})()
        self.assertEqual(
            venue._venue_symbol("BTC-USD-261127-82000-C"), "BTC/USD:BTC-261127-82000-C"
        )
        # An unknown instId passes through unchanged rather than inventing a symbol.
        self.assertEqual(venue._venue_symbol("BTC-USD-999999-1-C"), "BTC-USD-999999-1-C")

        venue.market_type = "spot"
        venue.ex = type("Ex", (), {"markets": {"SOL/USDT": {"id": "SOL-USDT"}}})()
        self.assertEqual(venue._venue_symbol("SOL/USDT"), "SOL/USDT")


class ReconcileSwapUnitsTests(unittest.TestCase):
    """The reconciler must speak the SAME unit the ledger does: contracts (张).

    Measured 2026-10-10: it converted the venue's positions into base units while
    the ledger stores contracts, so ACT (ctVal 10) read `ledger=-22912
    venue=-229120` and showed up as "venue-only". No correction was made - but the
    quantity this feeds is written into the ledger as a contract count, so a real
    correction on any ctVal != 1 symbol would have been off tenfold.
    """

    def _positions(self, contracts: float, side: str, contract_size: float):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "reconcile_ledger_to_venue", PROJECT / "scripts" / "reconcile_ledger_to_venue.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class Ex:
            def fetch_positions(self):
                return [{"symbol": "ACT/USDT:USDT", "contracts": contracts, "side": side}]

        venue = type("V", (), {"ex": Ex(), "markets": {"ACT/USDT:USDT": {"contractSize": contract_size}}})()
        return module._venue_swap_positions(venue)

    def test_contracts_are_not_multiplied_by_the_contract_size(self) -> None:
        # 10 contracts of something worth 10 base units each is still 10 contracts.
        self.assertEqual(self._positions(10.0, "short", 10.0), {"ACT/USDT:USDT": -10.0})
        self.assertEqual(self._positions(10.0, "long", 10.0), {"ACT/USDT:USDT": 10.0})

    def test_a_contract_size_of_one_is_unaffected(self) -> None:
        self.assertEqual(self._positions(7.0, "short", 1.0), {"ACT/USDT:USDT": -7.0})


class WatchRoundBudgetTests(unittest.TestCase):
    """2026-10-18：watcher 的「一轮超预算 ⚠」阈值从 900 调到 1800 秒。

    900 是照「40 个代币 × 4.5 秒 ≈ 3 分钟」的设计基准定的，但实测代理慢时单轮要
    **985~1362 秒** —— 于是每一轮都带 ⚠，把「异常」稀释成了常态。1800 秒（6× 间隔）
    高于最慢观测约 30%，真卡住时照样会喊。这里钉住「预算必须明显大于实测轮长」。
    """

    def test_the_round_budget_has_headroom_over_measured_rounds(self) -> None:
        from ta.cli import build_parser

        args = build_parser().parse_args(["scan", "watch", "--mints", "X"])
        self.assertEqual(args.round_budget_seconds, 1800.0)
        self.assertGreaterEqual(args.round_budget_seconds, args.interval * 4)
        self.assertGreater(args.round_budget_seconds, 1362 * 1.2)   # 实测最慢一轮 1362 秒


class TwoSidedPositionTests(unittest.TestCase):
    """2026-10-16：OKX 在**双向持仓**（long/short）下，同一个 symbol 会返回两行。

    两处解析（`CcxtVenue.get_account` 与 `reconcile_ledger_to_venue._venue_swap_positions`）
    原来都是 `out[symbol] = ...` —— **后一行覆盖前一行**，等于把一条腿当成了净头寸。
    症状就是那条持续 24 小时、每小时响一次的告警：`local=-5,970 venue=-13,357`
    （差 7,387 = 10-15 那笔买回；双向模式下 `buy` 开的是**多腿**，两腿相加才等于账本净额）。
    账本是净头寸，所以两行必须**相加**。
    """

    ROWS = [
        {"symbol": "CRV/USDT:USDT", "contracts": 7387.0, "side": "long"},
        {"symbol": "CRV/USDT:USDT", "contracts": 13357.0, "side": "short"},
    ]
    CANCELLING = [
        {"symbol": "X/USDT:USDT", "contracts": 5.0, "side": "long"},
        {"symbol": "X/USDT:USDT", "contracts": 5.0, "side": "short"},
    ]

    def _script_positions(self, rows: list[dict]) -> dict:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "reconcile_ledger_to_venue", PROJECT / "scripts" / "reconcile_ledger_to_venue.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class Ex:
            markets = {"CRV/USDT:USDT": {"contractSize": 1.0}}

            def fetch_positions(self):
                return rows

        return module._venue_swap_positions(type("V", (), {"ex": Ex()})())

    def _venue_positions(self, rows: list[dict]) -> dict:
        from ta.venues.ccxt_venue import CcxtVenue
        venue = CcxtVenue("okx", api_key="k", secret="s", password="p", sandbox=True)

        class Ex:
            markets = {"CRV/USDT:USDT": {"contractSize": 1.0}}

            def fetch_balance(self):
                return {"USDT": {"free": 0.0, "total": 0.0}, "info": []}

            def fetch_positions(self):
                return rows

            def fetch_open_orders(self, symbol=None):
                return []

        venue.ex = Ex()
        venue.markets = dict(Ex.markets)
        venue._mark_for = lambda symbol: 0.34          # 不联网
        state = venue.get_account()
        return {s: p.qty for s, p in state.positions.items()}

    def test_two_rows_for_one_symbol_are_summed_not_overwritten(self) -> None:
        # 场馆两腿：多 7,387 / 空 13,357 → 净 -5,970（正是账本的数字）
        self.assertEqual(self._script_positions(self.ROWS), {"CRV/USDT:USDT": -5970.0})
        self.assertEqual(self._venue_positions(self.ROWS), {"CRV/USDT:USDT": -5970.0})

    def test_two_legs_that_cancel_leave_no_position(self) -> None:
        self.assertEqual(self._script_positions(self.CANCELLING), {})
        self.assertEqual(self._venue_positions(self.CANCELLING), {})

    def test_one_sided_rows_still_work(self) -> None:
        short_only = [{"symbol": "A/USDT:USDT", "contracts": 10.0, "side": "short"}]
        self.assertEqual(self._script_positions(short_only), {"A/USDT:USDT": -10.0})
        self.assertEqual(self._venue_positions(short_only), {"A/USDT:USDT": -10.0})


class VenueAvailabilityTests(unittest.TestCase):
    """One word for what the venue offers a symbol right now.

    2026-10-10: 12 of the TradFi sleeve's 13 symbols answered the venue with zero
    bars on 1d/1h/15m while keeping 84+ bars on DISK, and 11 of them had a bid with
    no ask. "Tradable" has to mean both halves, because a signal the venue cannot
    price is one the depth gate refuses anyway.
    """

    def _classify(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "venue_availability", PROJECT / "scripts" / "venue_availability.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.classify

    def test_states(self) -> None:
        classify = self._classify()
        self.assertEqual(classify(695, 1034.0, 1034.1), "tradable")
        self.assertEqual(classify(695, 1034.0, None), "one-sided")
        self.assertEqual(classify(0, 453.9, None), "quote-only")
        self.assertEqual(classify(0, None, None), "absent")
        self.assertEqual(classify(695, None, None), "bars-only")

    def test_bars_from_disk_do_not_make_a_symbol_tradable(self) -> None:
        # The whole point: bars=0 with a stale quote is NOT tradable.
        classify = self._classify()
        self.assertNotEqual(classify(0, 5000.0, None), "tradable")


class ContractSizeTests(unittest.TestCase):
    """Contracts are not coins: a swap's P&L needs the contract size.

    Measured 2026-10-10: the book stored ACT's hedge leg in 张 (ctVal 10) but
    computed `(mark - avg) * qty`, so that leg's P&L was understated tenfold -
    CRV and LTC only looked right because their ctVal happens to be 1.
    """

    def test_a_swap_pnl_scales_with_the_contract_size(self) -> None:
        short = Position(symbol="ACT/USDT:USDT", qty=-10.0, avg_price=1.00, ct_val=10.0)
        # short 10 contracts (=100 units) after a 0.10 drop -> +10.0, not +1.0
        self.assertAlmostEqual(short.unrealized(0.90), 10.0, places=6)
        self.assertAlmostEqual(
            Position(symbol="ACT/USDT:USDT", qty=-10.0, avg_price=1.00).unrealized(0.90),
            1.0,
            places=6,
        )

    def test_spot_is_unaffected(self) -> None:
        spot = Position(symbol="ACT/USDT", qty=10.0, avg_price=1.00)
        self.assertAlmostEqual(spot.unrealized(1.10), 1.0, places=6)

    def test_the_venue_fills_ct_val_from_its_catalogue(self) -> None:
        """`get_account` must mark a swap position with the venue's contract size."""
        from ta.venues.ccxt_venue import CcxtVenue

        venue = object.__new__(CcxtVenue)
        venue.ex = type("Ex", (), {"markets": {"ACT/USDT:USDT": {"contractSize": 10.0}}})()
        self.assertEqual(venue.contract_size("ACT/USDT:USDT"), 10.0)
        self.assertEqual(venue.contract_size("UNKNOWN/USDT:USDT"), 1.0)


class VenueConnectResilienceTests(unittest.TestCase):
    """A dead network must DEGRADE the venue, not kill the process.

    Measured 2026-10-10: the proxy's upstream died and `load_markets()` raised a
    ccxt NetworkError straight out of `connect()`, so the runner exited before its
    first tick and launchd relaunched it every ~10 seconds (142 startup banners, a
    growing .err.log). The loop was not degraded - it was gone. Everything
    downstream already tolerates a missing catalogue, so a failed connect is a
    degraded start.
    """

    def test_connect_survives_a_dead_network_and_heals_itself(self) -> None:
        from ta.venues.ccxt_venue import CcxtVenue

        venue = object.__new__(CcxtVenue)
        venue.market_type = "spot"
        venue.spot_td_mode = "cash"
        venue.last_connect_error = ""

        class Ex:
            markets: dict = {}

            def load_markets(self):
                raise RuntimeError("NetworkError: okx GET .../instruments?instType=SPOT")

        venue.ex = Ex()
        venue.connect()  # must NOT raise
        self.assertFalse(venue._markets_loaded)
        self.assertIn("NetworkError", venue.last_connect_error)

        Ex.markets = {"SOL/USDT": {}}
        self.assertTrue(
            venue._markets_loaded, "the catalogue flag derives from ccxt, so it heals"
        )


class FeedFailurePassTests(unittest.TestCase):
    """One bad symbol must not stop the tick - but every symbol failing must.

    Measured 2026-10-08: A/USDT failed at the head of the list and aborted the
    whole refresh, so no other symbol got fetched and the venue's global
    consecutive-failure counter suspended every entry - one symbol holding 61
    hostage. The fix then was "skip the bad symbol and keep going", which is right
    until ALL of them fail: measured 2026-10-10, the proxy's upstream died and a
    single refresh ground through 64 symbols x 4 timeframes x 3 retries, so the
    runner completed no tick for 42 minutes. Hence: scattered failures are
    skipped, consecutive ones fail the PASS.
    """

    class _Feed(LiveFeed):
        def __init__(self, failing: set[str], symbols: list[str]) -> None:
            self.failing = failing
            super().__init__(venue=self, timeframe="1h", bar_limit=10, timeframes=["4h"])
            self.symbols = symbols

        def fetch_bars(self, symbol: str, timeframe: str, limit: int):  # type: ignore[override]
            if symbol in self.failing:
                raise RuntimeError("NetworkError: okx GET ...")
            # Two bars, because `drop_unclosed` discards the last (still forming) one.
            return [
                Bar(ts=1, open=1, high=1, low=1, close=1.0),
                Bar(ts=2, open=1, high=1, low=1, close=1.0),
            ]

        def refresh_marks(self, symbols: list[str]) -> dict[str, float]:  # type: ignore[override]
            return {}

    def test_every_symbol_failing_raises_instead_of_grinding(self) -> None:
        from ta.resilience import VenueDegraded

        symbols = [f"S{i}/USDT" for i in range(20)]
        feed = self._Feed(set(symbols), symbols)
        with self.assertRaises(VenueDegraded):
            feed.refresh(symbols)

    def test_one_bad_symbol_is_still_skipped_not_fatal(self) -> None:
        symbols = [f"S{i}/USDT" for i in range(20)]
        feed = self._Feed({"S0/USDT", "S7/USDT"}, symbols)
        feed.refresh(symbols)  # must not raise
        self.assertEqual(feed.last_failed_symbols, ["S0/USDT", "S7/USDT"])
        self.assertTrue(feed.bars("S1/USDT", "1h"), "the other symbols were fetched")

    def test_a_short_run_of_failures_does_not_trip_the_limit(self) -> None:
        symbols = [f"S{i}/USDT" for i in range(20)]
        feed = self._Feed({"S0/USDT", "S1/USDT", "S2/USDT"}, symbols)
        feed.refresh(symbols)  # 3 < the default limit of 5
        self.assertEqual(feed.last_failed_symbols, ["S0/USDT", "S1/USDT", "S2/USDT"])

    def test_prime_gives_up_early_without_raising(self) -> None:
        """Startup must stop early - but NOT by raising.

        `prime()` runs inside `TradingRunner.build()`: an exception there exits the
        process and launchd relaunches it every ~10s, which is how the 2026-10-10
        proxy outage turned into 142 startup banners instead of a degraded loop.
        """
        symbols = [f"S{i}/USDT" for i in range(20)]
        feed = self._Feed(set(symbols), symbols)
        feed.prime(symbols)  # must not raise
        self.assertTrue(feed.last_failed_symbols, "it records what it could not fetch")
        self.assertLess(
            len(feed.last_failed_symbols), len(symbols), "it stopped before trying all 20"
        )


class RiskEquityUnitsTests(unittest.TestCase):
    """The risk basis and the book must value a perp the SAME way.

    Measured 2026-10-09: the runner's risk_equity was `cash + Σ qty*price` for
    every position, so each perp SHORT was subtracted at full notional, while the
    book counted a swap as its unrealised PnL. The two differed by $4,823.79 -
    exactly the three hedge legs' notionals - and because the daily-loss breaker
    compares that figure against its own day-start baseline, OPENING a new carry
    pair read as a ~2% daily loss: entries paused at 15:05:56Z on a day the book
    was UP 0.5%. A slightly bigger pair crosses -3% and flattens everything.
    """

    def test_a_swap_counts_as_pnl_and_spot_as_notional(self) -> None:
        from ta.portfolio import marked_value

        spot = Position(symbol="LTC/USDT", qty=2.0, avg_price=60.0)
        perp = Position(symbol="LTC/USDT:USDT", qty=-2.0, avg_price=60.0)
        self.assertAlmostEqual(marked_value("LTC/USDT", spot, 63.0), 126.0, places=6)
        # short 2 @60 marked at 63 -> -6.0 of PnL, NOT -126 of notional
        self.assertAlmostEqual(marked_value("LTC/USDT:USDT", perp, 63.0), -6.0, places=6)

    def _runner(self, work: Path) -> TradingRunner:
        (work / "config").mkdir(parents=True)
        for toml in CONFIG_DIR.glob("*.toml"):
            shutil.copy(toml, work / "config" / toml.name)
        config = Config.load(work / "config")
        return TradingRunner.build(
            config,
            setup(config.log_dir, level="CRITICAL", console=False),
            venue_name="paper",
            iterations=1,
            dry_run=True,
        )

    def test_opening_a_hedge_does_not_move_the_risk_basis(self) -> None:
        """The regression: a delta-neutral pair must not change risk_equity."""
        with tempfile.TemporaryDirectory() as tmp:
            runner = self._runner(Path(tmp) / "project")
            try:
                marks = {"LTC/USDT": 63.0, "LTC/USDT:USDT": 63.0}
                before = AccountState(
                    venue="paper", base_ccy="USDT", cash=10_000.0, equity=10_000.0
                )
                runner._prepare_risk_account(before, marks)
                after = AccountState(
                    venue="paper",
                    base_ccy="USDT",
                    # Buying the spot leg MOVES cash into the position: 2 x 63.
                    cash=10_000.0 - 126.0,
                    equity=10_000.0,
                    positions={
                        "LTC/USDT": Position(symbol="LTC/USDT", qty=2.0, avg_price=63.0),
                        "LTC/USDT:USDT": Position(
                            symbol="LTC/USDT:USDT", qty=-2.0, avg_price=63.0
                        ),
                    },
                )
                runner._prepare_risk_account(after, marks)
                # A hedge opened AT the mark is worth nothing yet, so the basis
                # must be unchanged - under the old formula it dropped by the
                # $126 notional of the short leg.
                self.assertAlmostEqual(after.risk_equity, before.risk_equity, places=6)
            finally:
                runner.close()


class MergeBarsTests(unittest.TestCase):
    """A daily top-up must never shrink history.

    Measured 2026-10-09: every 1d file under data/history/okx_demo ended at
    2026-10-05, because `write_bars` REPLACES the file and the only way to refresh
    was to re-download the whole window. The research side consumed those files -
    the options sleeve's 30-day realized vol was a frozen constant, and every
    `--source history` backtest silently ended four days ago.
    """

    def test_merging_keeps_existing_bars_and_adds_the_new_ones(self) -> None:
        from ta.history import merge_bars, read_bars, write_bars

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SYM__1d.jsonl"
            write_bars(path, [Bar(ts=1, open=1, high=1, low=1, close=1.0),
                              Bar(ts=2, open=1, high=1, low=1, close=2.0)])
            total = merge_bars(path, [Bar(ts=3, open=1, high=1, low=1, close=3.0)])
            bars = read_bars(path)
            self.assertEqual(total, 3)
            self.assertEqual([b.ts for b in bars], [1, 2, 3])

    def test_a_restated_bar_replaces_the_old_one(self) -> None:
        # Same timestamp, corrected price: newest wins, and the count never grows.
        from ta.history import merge_bars, read_bars, write_bars

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SYM__1d.jsonl"
            write_bars(path, [Bar(ts=1, open=1, high=1, low=1, close=1.0)])
            total = merge_bars(path, [Bar(ts=1, open=1, high=1, low=1, close=9.0)])
            bars = read_bars(path)
            self.assertEqual(total, 1)
            self.assertEqual(bars[0].close, 9.0)

    def test_merging_into_a_missing_file_just_writes_it(self) -> None:
        from ta.history import merge_bars, read_bars

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "SYM__1d.jsonl"
            total = merge_bars(path, [Bar(ts=5, open=1, high=1, low=1, close=1.0)])
            self.assertEqual(total, 1)
            self.assertEqual(len(read_bars(path)), 1)


class PassiveCarryEntryTests(unittest.TestCase):
    """The spot leg may try to be a maker - but never at the cost of a clean state.

    Real-venue semantics (verified 2026-10-09 with scripts/cancel_path_check.py):
    a post-only order that would take liquidity is ACCEPTED and then killed by the
    exchange (`cancelSource=31`, zero fill). So the submit ack says nothing, and
    the only two questions that matter are answered by the order's final state:
    did it fill, and is it definitely gone?
    """

    class _Sink:
        """Accepts a resting post-only buy and then does whatever it is told."""

        def __init__(
            self,
            *,
            fills_passively: bool = False,
            venue_kills_it: bool = False,
            cancel_works: bool = True,
            reject_limit: bool = False,
        ) -> None:
            self.requests: list[OrderRequest] = []
            self._orders: dict[str, Order] = {}
            self.fills_passively = fills_passively
            self.venue_kills_it = venue_kills_it
            self.cancel_works = cancel_works
            self.reject_limit = reject_limit
            self.cancelled: list[str] = []

        def submit(self, request: OrderRequest) -> Order:
            self.requests.append(request)
            order = Order(
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                type=request.type,
                price=request.price,
                status="filled",
                filled_qty=request.qty,
                avg_price=request.price or 100.0,
            )
            if request.type == "limit":
                if self.reject_limit:
                    order.status, order.filled_qty = "rejected", 0.0
                elif self.fills_passively:
                    pass  # stays "filled" with the full size
                elif self.venue_kills_it:
                    # The real venue's answer to a marketable post-only order.
                    order.status, order.filled_qty = "canceled", 0.0
                else:
                    order.status, order.filled_qty = "open", 0.0
            self._orders[request.client_order_id] = order
            return order

        def cancel(self, client_order_id: str, symbol: str) -> bool:
            self.cancelled.append(client_order_id)
            if not self.cancel_works:
                return False
            order = self._orders.get(client_order_id)
            if order is not None:
                order.status = "canceled"
            return True

    def _open(self, spot: "PassiveCarryEntryTests._Sink", perp=None):
        from ta.execution.paired import PairedExecutor

        executor = PairedExecutor(spot, perp or self._Sink(), stamp=lambda: 0)
        return executor.open_pair(
            "SOL/USDT", 1.5, tag="carry", passive_price=100.0, passive_wait_seconds=0.1
        )

    def test_a_passive_fill_needs_no_market_buy(self) -> None:
        spot = self._Sink(fills_passively=True)
        self._open(spot)
        self.assertEqual(len(spot.requests), 1)
        self.assertEqual(spot.requests[0].type, "limit")
        self.assertTrue(spot.requests[0].post_only)
        self.assertEqual(spot.requests[0].price, 100.0)

    def test_an_unfilled_passive_order_is_cancelled_before_the_market_buy(self) -> None:
        spot = self._Sink()
        self._open(spot)
        self.assertEqual([r.type for r in spot.requests], ["limit", "market"])
        self.assertEqual(spot.cancelled, [spot.requests[0].client_order_id])
        self.assertEqual(spot._orders[spot.requests[0].client_order_id].status, "canceled")
        # The fallback must carry its OWN id: the OMS refuses a client order id it
        # has already seen, so reusing the passive one silently cancels the entry.
        self.assertNotEqual(
            spot.requests[0].client_order_id, spot.requests[1].client_order_id
        )

    def test_an_order_the_venue_kills_falls_back_to_market(self) -> None:
        # The post-only order never rested, so there is nothing to cancel and the
        # market leg is safe - this is the common case on a moving book.
        spot = self._Sink(venue_kills_it=True)
        self._open(spot)
        self.assertEqual([r.type for r in spot.requests], ["limit", "market"])
        self.assertEqual(spot.cancelled, [])

    def test_an_unconfirmed_cancel_refuses_the_market_fallback(self) -> None:
        # The dangerous case: the order may still be live. Buying at market on top
        # of it would double the spot leg while the hedge stays single-size, so the
        # pair aborts instead - and the perp is never sent.
        from ta.execution.paired import PairExecutionError

        spot, perp = self._Sink(cancel_works=False), self._Sink()
        with self.assertRaises(PairExecutionError) as ctx:
            self._open(spot, perp)
        self.assertIn("could not be confirmed cancelled", str(ctx.exception))
        self.assertEqual([r.type for r in spot.requests], ["limit"])
        self.assertEqual(perp.requests, [])

    def test_a_rejected_post_only_order_falls_back_to_market(self) -> None:
        spot = self._Sink(reject_limit=True)
        self._open(spot)
        self.assertEqual([r.type for r in spot.requests], ["limit", "market"])

    class _RecordSink:
        """A venue with NO open-order list - only the order's own record.

        This is the shape that broke production on 2026-10-10 (LTC): a filled
        order is absent from the open-order list, so that list cannot tell
        "cancelled" from "filled".
        """

        def __init__(self, *, state: str | None = "open", filled: float = 0.0) -> None:
            self.requests: list[OrderRequest] = []
            self._state = state
            # An unreadable venue stays unreadable even after a cancel call -
            # otherwise the test would "pass" by inventing the very confirmation
            # the production code is not allowed to assume.
            self._readable = state is not None
            self._filled = filled
            self.cancelled: list[str] = []

        def submit(self, request: OrderRequest) -> Order:
            self.requests.append(request)
            limit = request.type == "limit"
            return Order(
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                type=request.type,
                price=request.price,
                status="open" if limit else "filled",
                filled_qty=0.0 if limit else request.qty,
                avg_price=request.price or 100.0,
            )

        def order_state(self, client_order_id: str, symbol: str):
            return (self._state, self._filled) if self._readable else None

        def cancel(self, client_order_id: str, symbol: str) -> bool:
            self.cancelled.append(client_order_id)
            self._state = "canceled"
            return True

        def sync(self, symbols: list[str]) -> list:
            return []

    def test_a_fill_is_never_mistaken_for_a_cancel(self) -> None:
        """The regression that cost ~$2k of unintended exposure on LTC."""
        from ta.execution.paired import PairedExecutor

        spot = self._RecordSink(state="filled", filled=1.5)
        perp = self._RecordSink(state="filled", filled=1.5)
        executor = PairedExecutor(spot, perp, stamp=lambda: 0)
        pair = executor.open_pair(
            "SOL/USDT", 1.5, tag="carry", passive_price=100.0, passive_wait_seconds=0.1
        )
        # Exactly ONE spot order: the passive fill is used, no market fallback.
        self.assertEqual([r.type for r in spot.requests], ["limit"])
        self.assertEqual(pair.spot_order.filled_qty, 1.5)
        self.assertEqual(perp.requests[0].qty, 1.5)

    def test_an_unreadable_order_state_refuses_the_market_fallback(self) -> None:
        """Cannot tell whether it filled? Then do not buy again."""
        from ta.execution.paired import PairExecutionError, PairedExecutor

        spot, perp = self._RecordSink(state=None), self._RecordSink()
        executor = PairedExecutor(spot, perp, stamp=lambda: 0)
        with self.assertRaises(PairExecutionError) as ctx:
            executor.open_pair(
                "SOL/USDT", 1.5, tag="carry", passive_price=100.0, passive_wait_seconds=0.1
            )
        self.assertIn("could not be confirmed cancelled", str(ctx.exception))
        self.assertEqual([r.type for r in spot.requests], ["limit"])
        self.assertEqual(perp.requests, [])


class DepthRefusalTests(unittest.TestCase):
    """The refusal rule itself: unknown depth is never "no impact"."""

    def test_unknown_depth_is_a_refusal(self) -> None:
        from ta.execution.sizing import depth_refusal

        self.assertIsNotNone(depth_refusal(None, 50.0))

    def test_impact_over_the_cap_is_a_refusal(self) -> None:
        from ta.execution.sizing import depth_refusal

        self.assertIsNotNone(depth_refusal(60.0, 50.0))
        self.assertIsNotNone(depth_refusal(4591.5, 50.0))

    def test_a_deep_book_passes(self) -> None:
        from ta.execution.sizing import depth_refusal

        self.assertIsNone(depth_refusal(0.9, 50.0))
        self.assertIsNone(depth_refusal(50.0, 50.0))  # at the cap is still allowed

    def test_a_zero_cap_disables_the_gate(self) -> None:
        # What the paper venue and the backtester depend on: no order book to probe.
        from ta.execution.sizing import depth_refusal

        self.assertIsNone(depth_refusal(None, 0.0))


class EntryDepthGateCoverageTests(unittest.TestCase):
    """The gate must run on EVERY entry, not only carry's two legs (README 65).

    Measured 2026-10-09: the check lived inside `if signal.tag == "carry"`, so
    the directional legs were unprotected. That mattered the moment the TradFi
    sleeve went live: 17 of its 20 tokenised-equity lines had bids but no asks,
    and the risk engine's spread check passes when the spread cannot be measured
    (`spread_bps is not None`). A market buy into a one-sided book either fails
    at the venue or fills far from the mid.
    """

    def _runner(self, work: Path) -> TradingRunner:
        (work / "config").mkdir(parents=True)
        for toml in CONFIG_DIR.glob("*.toml"):
            shutil.copy(toml, work / "config" / toml.name)
        config = Config.load(work / "config")
        return TradingRunner.build(
            config,
            setup(config.log_dir, level="CRITICAL", console=False),
            venue_name="paper",
            strategy_names=["trend_breakout_daily"],
            symbols=["XTSM/USDT"],
            iterations=1,
            dry_run=False,
        )

    def test_a_depth_refusal_says_which_size_it_judged(self) -> None:
        """2026-10-13：闸门被喂的是**缩量前**的期望尺寸（`权益×tier风险%÷止损距离`），
        而风控引擎随后会把它缩到 tier 上限（日志里 `shrunk 54922 -> 12240`）。
        实测今天的盘口：ETH 想要的 21.7 张（$54k）→ `None` ⇒ 拒绝，而缩量后的 4.88 张
        （$12.2k）只要 9.8 bps，本来会通过。闸门保留（那两条腿样本外 −0.82%~−2.02%，
        不该靠它去攒纸面成交），但日志必须说清判的是哪个数量。
        """
        from ta.runner import depth_size_fields

        fields = depth_size_fields("ETH/USDT", 21.735, 2507.01, "mean_reversion")
        self.assertEqual(fields["symbol"], "ETH/USDT")
        self.assertEqual(fields["kind"], "mean_reversion")
        self.assertEqual(fields["judged_notional_usd"], round(21.735 * 2507.01, 2))
        self.assertIn("pre-cap", fields["size_basis"])
        self.assertIn("shrink", fields["size_basis"])
        # 没有 mark 时不编造名义，但「判的是哪个尺寸」这句必须还在
        bare = depth_size_fields("ETH/USDT", 21.735, None, "mean_reversion")
        self.assertNotIn("judged_notional_usd", bare)
        self.assertIn("size_basis", bare)

    def _entry(self, runner: TradingRunner, impact: float | None) -> bool:
        from ta.models import Signal
        from ta.strategies.base import MarketView

        runner.venue.entry_impact_bps = lambda symbol, qty, side: impact  # type: ignore[method-assign]
        strategy = next(s for s in runner.strategies if s.name == "trend_breakout_daily")
        bars = [
            Bar(ts=i * 86_400_000, open=100.0 + i * 0.1, high=101.0 + i * 0.1,
                low=99.0 + i * 0.1, close=100.0 + i * 0.1, volume=10.0)
            for i in range(60)
        ]
        mark = bars[-1].close

        class _Source:
            def bars(self, symbol: str, timeframe: str | None = None) -> list[Bar]:
                return bars

        # XMU/USDT: the only TradFi symbol the venue still serves (2026-10-10). The
        # subject here is the depth gate, so the symbol just has to be one the tier
        # actually carries.
        signal = Signal(symbol="XMU/USDT", side="buy", reason="test", tag=strategy.name)
        account = AccountState(venue="paper", cash=10_000.0, equity=10_000.0)
        view = MarketView(symbol="XMU/USDT", mark=mark, source=_Source())
        return runner._handle_signal(
            strategy, signal, account, {"XMU/USDT": mark}, view
        )

    def test_a_one_sided_book_refuses_a_directional_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = self._runner(Path(tmp) / "project")
            try:
                handled = self._entry(runner, None)
                self.assertFalse(handled)
                self.assertEqual(runner.session_orders, 0)
            finally:
                runner.close()

    def test_a_deep_book_still_sends_the_same_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = self._runner(Path(tmp) / "project")
            try:
                self._entry(runner, 5.0)
                # What this asserts is COVERAGE, not a fill: with a deep book the
                # same signal must reach the OMS. (The paper venue has no real
                # market for a tokenised equity, so the order comes back as
                # something other than filled - that is not what is under test.)
                self.assertEqual(runner.session_orders, 1)
            finally:
                runner.close()


class CarryBasisTests(unittest.TestCase):
    """Perp premium over spot: positive pays the pair, negative is paid up front.

    A carry pair buys spot at S and shorts the perp at P, so convergence realises
    exactly P - S. Measured 2026-10-09: ACT opened with the perp 130.6 bps BELOW
    spot (~$29 hole on top of fees), and CRV's premium drifted from -0.16% to
    +1.96% against us for -$54; those two (-$82) exceeded either pair's monthly
    steady-state funding.
    """

    def test_a_perp_premium_is_income(self) -> None:
        from scripts.carry_live import basis_bps

        self.assertAlmostEqual(basis_bps(100.0, 100.05), 5.0, places=6)

    def test_a_perp_discount_is_a_cost(self) -> None:
        from scripts.carry_live import basis_bps

        self.assertAlmostEqual(basis_bps(100.0, 99.5), -50.0, places=6)

    def test_the_real_act_entry_is_reproduced(self) -> None:
        from scripts.carry_live import basis_bps

        got = basis_bps(0.0099003814682765, 0.0097710911312861)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, -130.6, delta=0.2)

    def test_missing_prices_are_not_guessed(self) -> None:
        from scripts.carry_live import basis_bps

        self.assertIsNone(basis_bps(0.0, 100.0))
        self.assertIsNone(basis_bps(100.0, 0.0))

    def test_the_runner_and_the_screener_share_one_rule(self) -> None:
        """The gate has to exist at ORDER time too - a screen is a snapshot, and
        the basis can move before the order goes out."""
        from ta.config import Config
        from ta.execution.sizing import basis_bps as runner_rule

        config = Config.load(str(PROJECT / "config"))
        self.assertGreater(config.risk.per_trade.max_basis_discount_bps, 0.0)
        self.assertAlmostEqual(runner_rule(100.0, 99.5), -50.0, places=6)


class PostOnlyFlagTests(unittest.TestCase):
    """`post_only` must reach the venue as `ordType`, and must be opt-in.

    The carry entry wants to try buying its spot leg passively: measured
    2026-10-09, market entries paid 12-73 bps round trip (worst single leg 133 bps)
    against ~2 bps/day of funding, so the spread is worth days of income.
    """

    def test_the_flag_is_opt_in(self) -> None:
        request = OrderRequest(client_order_id="t", symbol="DOGE/USDT", side="buy", qty=1.0)
        self.assertFalse(request.post_only)

    def test_the_venue_passes_ordtype(self) -> None:
        from ta.venues.ccxt_venue import CcxtVenue

        venue = CcxtVenue("okx", api_key="k", secret="s", password="p", sandbox=True)
        sent: dict = {}

        class _Ex:
            markets = {
                "DOGE/USDT": {"spot": True, "contract": False, "base": "DOGE",
                              "quote": "USDT", "limits": {"amount": {"min": 1.0}}},
            }

            def fetch_balance(self, params=None):
                return {"DOGE": {"free": 1e9}, "USDT": {"free": 1e9}}

            def create_order(self, symbol, type_, side, amount, price, params):
                sent.update(params)
                return {"id": "1", "clientOrderId": params["clientOrderId"], "symbol": symbol,
                        "side": side, "type": type_, "amount": amount, "filled": 0.0,
                        "status": "open", "price": price}

            def amount_to_precision(self, symbol, amount):
                return amount

        venue.ex = _Ex()
        venue.ex.markets = {"X/USDT": {}}    # the flag derives from ccxt now
        venue.spot_td_mode = "cross"
        venue.place_order(
            OrderRequest(client_order_id="t", symbol="DOGE/USDT", side="buy", qty=10.0,
                         type="limit", price=0.1, post_only=True)
        )
        self.assertEqual(sent.get("ordType"), "post_only")

    def test_a_plain_order_does_not_ask_for_post_only(self) -> None:
        from ta.venues.ccxt_venue import CcxtVenue

        venue = CcxtVenue("okx", api_key="k", secret="s", password="p", sandbox=True)
        sent: dict = {}

        class _Ex:
            markets = {
                "DOGE/USDT": {"spot": True, "contract": False, "base": "DOGE",
                              "quote": "USDT", "limits": {"amount": {"min": 1.0}}},
            }

            def fetch_balance(self, params=None):
                return {"DOGE": {"free": 1e9}, "USDT": {"free": 1e9}}

            def create_order(self, symbol, type_, side, amount, price, params):
                sent.update(params)
                return {"id": "1", "clientOrderId": params["clientOrderId"], "symbol": symbol,
                        "side": side, "type": type_, "amount": amount, "filled": amount,
                        "status": "closed", "average": price or 0.1}

            def amount_to_precision(self, symbol, amount):
                return amount

        venue.ex = _Ex()
        venue.ex.markets = {"X/USDT": {}}    # the flag derives from ccxt now
        venue.spot_td_mode = "cross"
        venue.place_order(
            OrderRequest(client_order_id="t", symbol="DOGE/USDT", side="buy", qty=10.0,
                         type="market")
        )
        self.assertNotIn("ordType", sent)


class TradFiLegIdentityTests(unittest.TestCase):
    """Cryptos that merely START with X must not draw on the TradFi quota.

    The TradFi leg is matched by `symbol_prefix = "X"`, which over-captures: the
    demo catalogue's X-starting spot lines include XLM/XMR/XRP/XTZ/XAUT/XEM/XPL.
    Measured 2026-10-09 - three candidate discriminators were tried; two were
    falsified outright and one is readable only off the live catalogue:

      * `contTdSwTime` is non-empty for 122 instruments, including obvious crypto
        (2Z/ACT/ANIME/ARKM...), so it is not an asset-class marker;
      * "tokenised equities are demo-only" is FALSE - the live catalogue lists the
        same XNVDA/XQQQ names.

      * `instCategory` IS an asset class (live: 1 crypto / 3 equity / 4 commodity
        token) but the demo catalogue's copy disagrees with live on 8 ids and calls
        XRP-USDT an equity, so it cannot be read where we trade.

    The unknowns were dated instead: OKX's equity programme began 2026-07-15, so
    XPO (2024-07-24) and XUC (2024-11-04) - both demo-only, both flat - are crypto,
    while XCU (2026-07-02, live perp filed as a commodity) is not. Identity stays a
    curated list, and this test is what stops it regressing.
    """

    def _leg(self, symbol: str) -> str:
        from ta.config import Config
        from ta.risk.engine import leg_of

        config = Config.load(str(PROJECT / "config"))
        members = {n: set(getattr(l, "symbols", []) or []) for n, l in config.risk.legs.items()}
        prefixes = {
            n: getattr(l, "symbol_prefix", None)
            for n, l in config.risk.legs.items()
            if getattr(l, "symbol_prefix", None)
        }
        return leg_of(symbol, members, prefixes)

    def test_crypto_x_names_are_spot(self) -> None:
        for symbol in ("XRP/USDT", "XLM/USDT", "XMR/USDT", "XTZ/USDT",
                       "XAUT/USDT", "XEM/USDT", "XPL/USDT",
                       "XPO/USDT", "XUC/USDT"):
            self.assertEqual(self._leg(symbol), "spot", symbol)

    def test_real_tokenised_equities_still_reach_tradfi(self) -> None:
        for symbol in ("XNVDA/USDT", "AAPLX/USDT", "TSLAX/USDT", "XQQQ/USDT"):
            self.assertEqual(self._leg(symbol), "tradfi", symbol)

    def test_tokenised_copper_stays_in_tradfi(self) -> None:
        # XCU is a commodity token, not a crypto: it has a live perp filed under
        # instCategory 4 next to XAU/XAG/XPT, so it draws on the TradFi quota.
        self.assertEqual(self._leg("XCU/USDT"), "tradfi")

    def test_plain_crypto_is_spot(self) -> None:
        for symbol in ("BTC/USDT", "DOGE/USDT", "SOL/USDT"):
            self.assertEqual(self._leg(symbol), "spot", symbol)


class NewCoinPanelTests(unittest.TestCase):
    """看板的第二个面板：只读兄弟项目自己发布的报告，不 import 它的代码。

    2026-10-11 用户要求「把多资产组合策略与新币主观量化策略分开展示」。两个项目是
    独立仓库、独立证据标准（README §71），所以耦合必须停在"读一个 JSON 文件"这一层：
    对方重命名一个函数不该能把这里的看板弄坏。
    """

    def _write(self, tmp: str, payload: dict | str) -> Path:
        target = Path(tmp) / "data" / "reports" / "latest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                          encoding="utf-8")
        return target

    def test_a_missing_report_says_why_instead_of_showing_zeros(self) -> None:
        from ta.newcoin_panel import view

        with tempfile.TemporaryDirectory() as tmp:
            out = view(Path(tmp))
        self.assertFalse(out["available"])
        self.assertIn("latest.json", out["reason"])
        # 关键：不是 available=True + 一堆 0 —— 0 和"没采集"是两件事
        self.assertNotIn("candidates", out)

    def test_a_fresh_report_is_passed_through(self) -> None:
        import datetime

        from ta.newcoin_panel import view

        now = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, {"generated_at": now, "version": "0.2.0",
                              "candidates": {"rows": [], "passed": 0}})
            out = view(Path(tmp))
        self.assertTrue(out["available"])
        self.assertFalse(out["stale"])
        self.assertEqual(out["version"], "0.2.0")
        self.assertLess(out["age_seconds"], 60)

    def test_a_report_older_than_a_day_is_flagged_stale(self) -> None:
        """采集是每天一次，超过 26 小时基本就是某天没跑成。"""
        import datetime

        from ta.newcoin_panel import view

        old = (datetime.datetime.now(datetime.UTC)
               - datetime.timedelta(hours=30)).replace(microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, {"generated_at": old, "version": "0.2.0"})
            out = view(Path(tmp))
        self.assertTrue(out["stale"])

    def test_a_broken_report_is_a_reason_not_an_exception(self) -> None:
        from ta.newcoin_panel import view

        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "{ this is not json")
            out = view(Path(tmp))
        self.assertFalse(out["available"])
        self.assertIn("读不了", out["reason"])

    def test_the_default_points_at_the_sibling_project(self) -> None:
        from ta.newcoin_panel import DEFAULT_DIR

        self.assertEqual(DEFAULT_DIR.name, "newcoin-subjective-quant")
        self.assertEqual(DEFAULT_DIR.parent.name, "outputs")

    def test_freshness_points_at_the_oldest_block_not_the_file(self) -> None:
        """2026-10-13：视图文件可以是 6 秒前写的，而里面的 `scores` 还是两小时前那轮的产物。
        「视图新鲜」不等于「里面的数字都新鲜」—— 而读者只会看到"采集正常"，所以要**点名**。"""
        import datetime

        from ta.newcoin_panel import freshness

        now = datetime.datetime.now(datetime.UTC)
        fresh = (now - datetime.timedelta(seconds=30)).replace(microsecond=0).isoformat()
        old = (now - datetime.timedelta(hours=2)).replace(microsecond=0).isoformat()
        out = freshness({
            "generated_at": fresh,
            "scores": {"generated_at": old},
            "thesis_scores": {"generated_at": fresh},
            "invalidations": {"generated_at": fresh},
            "cohort": {"generated_at": fresh},
        })
        self.assertEqual(out["oldest_block"], "scores")
        self.assertGreater(out["oldest_age_seconds"], 7000)      # ≈2 小时
        self.assertLess(out["ages"]["cohort"], 120)              # 但它自己是新的
        # 一块时刻都没有（旧版脚本写的视图）→ 返回空，不编一个年龄出来
        self.assertEqual(freshness({"generated_at": fresh}), {})
        self.assertEqual(freshness({"scores": {"generated_at": "不是时间"}}), {})

    def test_the_view_carries_the_freshness_block(self) -> None:
        import datetime

        from ta.newcoin_panel import view

        now = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, {"generated_at": now, "version": "0.2.0",
                              "cohort": {"generated_at": now}})
            out = view(Path(tmp))
        self.assertEqual(out["freshness"]["oldest_block"], "cohort")


class DailyResearchRetryTests(unittest.TestCase):
    """A lost day of daily research is a lost day of IV-RV sample.

    Measured 2026-10-10: five of the ten daily steps came back rc != 0 with SSL
    UNEXPECTED_EOF (the same proxy drop as 10-09) and nothing retried them, so the
    run had to be re-done by hand. The steps are idempotent (keyed by bill id /
    listing / merged writes), so a bounded retry costs nothing when the venue is
    flaky and returns the same rc when it is genuinely dead.
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "daily_research", PROJECT / "scripts" / "daily_research.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_first_try_success_is_not_retried(self) -> None:
        module = self._module()
        calls: list[int] = []
        slept: list[float] = []

        def action() -> int:
            calls.append(1)
            return 0

        code, used = module.run_with_retry(
            "step", action, attempts=3, sleep_seconds=0, sleep=slept.append
        )
        self.assertEqual((code, used, len(calls), slept), (0, 1, 1, []))

    def test_a_flaky_step_recovers_within_the_budget(self) -> None:
        module = self._module()
        codes = [1, 1, 0]
        slept: list[float] = []

        def action() -> int:
            return codes.pop(0)

        code, used = module.run_with_retry(
            "step", action, attempts=3, sleep_seconds=60.0, sleep=slept.append
        )
        self.assertEqual((code, used), (0, 3))
        self.assertEqual(slept, [60.0, 60.0])

    def test_a_dead_step_stops_after_the_budget(self) -> None:
        module = self._module()
        calls: list[int] = []

        def action() -> int:
            calls.append(1)
            return 2

        code, used = module.run_with_retry(
            "step", action, attempts=3, sleep_seconds=0, sleep=lambda _s: None
        )
        self.assertEqual((code, used, len(calls)), (2, 3, 3))

    def test_an_exception_counts_as_a_failed_attempt(self) -> None:
        module = self._module()
        calls: list[int] = []

        def action() -> int:
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("URLError: SSL UNEXPECTED_EOF_WHILE_READING")
            return 0

        code, used = module.run_with_retry(
            "step", action, attempts=3, sleep_seconds=0, sleep=lambda _s: None
        )
        self.assertEqual((code, used, len(calls)), (0, 2, 2))

    def test_the_status_file_names_what_still_needs_a_rerun(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily_job_status.json"
            needs = module.write_status(path, {"a": 0, "b": 1}, {"a": 1, "b": 3})
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(needs, ["b"])
        self.assertEqual(payload["needs_rerun"], ["b"])
        self.assertEqual(payload["steps"]["b"], {"rc": 1, "attempts": 3})
        self.assertEqual(payload["steps"]["a"], {"rc": 0, "attempts": 1})


class EnvLoaderIsSharedTests(unittest.TestCase):
    """Three daily-job steps read credentials through ONE loader, not three copies.

    Measured 2026-10-10: `scripts/venue_availability.py` went into the daily job
    without the `.env` loader `sync_funding.py` and `funding_income.py` already had,
    so under launchd - proxy yes, API keys no (README 50.4) - it died with "API key
    is not set" on all three attempts, and its 21:10 row is the entire input to the
    TradFi tier decision. One function is what stops the next script repeating it.
    """

    def _daily_research(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "daily_research", PROJECT / "scripts" / "daily_research.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_missing_file_is_not_an_error(self) -> None:
        from ta.config import load_env_file

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_env_file(Path(tmp) / "nope.env"), 0)

    def test_every_daily_step_that_builds_a_venue_loads_env(self) -> None:
        """Walked from the job's own step list, so adding a step is what breaks it."""
        import importlib

        from ta.config import load_env_file as shared

        checked = []
        for script, _argv in self._daily_research().SCRIPT_STEPS:
            source = (PROJECT / "scripts" / script).read_text(encoding="utf-8")
            if "build_venue(" not in source:
                continue
            checked.append(script)
            self.assertIn("load_env_file", source, f"{script} builds a venue without loading .env")
            module = importlib.import_module(f"scripts.{script[:-3]}")
            self.assertIs(module.load_env_file, shared, script)
        self.assertEqual(
            sorted(checked),
            [
                "carry_income_report.py",
                # 2026-10-14 加进每日任务：换腿规则读的候选快照必须每天刷新，
                # 否则它的深度过滤会一直被关掉（见 docs/LIVE-PREFLIGHT.md 的 P1）
                "carry_live.py",
                "funding_income.py",
                "refresh_daily_bars.py",
                "sync_funding.py",
                "venue_availability.py",
            ],
        )


class OptionsBookSurveyTests(unittest.TestCase):
    """The 1.1 entry-cost measurement has to sample the curve, not the front expiry.

    Measured 2026-10-10: three consecutive weeklies are three draws from one
    liquidity regime, and a single contract (BTC-USD-261030-68000-C, bid 0.114 /
    ask 0.182) had earlier suggested a spread near half the price. Walking 60
    quotes across 13-48d put the median at 2.2% of mid instead.
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "options_book_survey", PROJECT / "scripts" / "options_book_survey.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_expiries_are_spread_across_the_curve(self) -> None:
        import datetime

        module = self._module()
        today = datetime.date(2026, 10, 10)
        inst_ids = [
            f"BTC-USD-2610{day:02d}-80000-C" for day in (11, 12, 13, 16, 23, 30)
        ] + ["BTC-USD-261127-80000-C"]
        picked = module.pick_expiries(inst_ids, today, 3, 10, 60)
        self.assertEqual(len(picked), 3)
        self.assertEqual(picked[0][1], 13)   # the shortest in-window
        self.assertEqual(picked[-1][1], 48)  # and the longest, not the front three

    def test_the_dte_window_is_respected(self) -> None:
        import datetime

        module = self._module()
        today = datetime.date(2026, 10, 10)
        picked = module.pick_expiries(["SOL-USD_UM-261010-100-C", "SOL-USD_UM-261016-100-C"],
                                      today, 3, 10, 60)
        self.assertEqual(picked, [])  # OKX's SOL chain ends at 6d (2026-10-10)

    def test_summarise_counts_one_sided_quotes_but_keeps_them_out_of_the_median(self) -> None:
        module = self._module()
        rows = [
            {"uly": "BTC-USD", "mid": 1.0, "spread_pct": 2.0, "half_spread_pct": 1.0},
            {"uly": "BTC-USD", "mid": 2.0, "spread_pct": 4.0, "half_spread_pct": 2.0},
            {"uly": "BTC-USD", "bid": 0.5, "ask": None},  # cannot be bought
        ]
        stats = module.summarise(rows)
        self.assertEqual((stats["quotes"], stats["two_sided"], stats["one_sided"]), (3, 2, 1))
        self.assertEqual(stats["median_spread_pct"], 3.0)
        self.assertEqual(stats["median_half_spread_pct"], 1.5)
        self.assertEqual(stats["by_uly"]["BTC-USD"]["two_sided"], 2)

    def test_summarise_of_nothing_is_not_an_error(self) -> None:
        module = self._module()
        stats = module.summarise([])
        self.assertEqual(stats["quotes"], 0)
        self.assertIsNone(stats["median_spread_pct"])


class CarryRotationTests(unittest.TestCase):
    """Rotation is the only way into a full carry book, and it costs 44.9 bps.

    The rule has to be "persistently unpaid", not "worst right now": the first day
    of live carry churned six pairs in an afternoon (31 fills, $284 of fees) by
    deciding on the instantaneous print.
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "carry_rotation", PROJECT / "scripts" / "carry_rotation.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _day(day: int, hour: int = 0) -> int:
        import datetime

        return int(datetime.datetime(2026, 10, day, hour, tzinfo=datetime.UTC).timestamp() * 1000)

    def test_daily_bps_is_a_per_day_median_not_a_mean(self) -> None:
        module = self._module()
        # One spike and two quiet prints in the same UTC day: the median is the
        # quiet print, so a 20x spike cannot carry the day.
        prints = [
            {"ts": self._day(9, 0), "rate": 0.0001},      # 3 bps/day
            {"ts": self._day(9, 8), "rate": 0.0001},
            {"ts": self._day(9, 16), "rate": 0.002},
            {"ts": self._day(10, 0), "rate": 0.00005},    # 1.5 bps/day
        ]
        series = module.daily_bps_series(prints, 2)
        self.assertEqual([(d, bps, n) for d, bps, n in series],
                         [("2026-10-09", 3.0, 3), ("2026-10-10", 1.5, 1)])

    def test_persistence_needs_every_day_of_the_window(self) -> None:
        module = self._module()
        low = [("2026-10-08", 0.5, 3), ("2026-10-09", 0.9, 3), ("2026-10-10", 0.2, 3)]
        self.assertTrue(module.is_persistently_below(low, 1.0, 3))
        # one good day in the window is enough to keep the pair
        mixed = [("2026-10-08", 0.5, 3), ("2026-10-09", 1.4, 3), ("2026-10-10", 0.2, 3)]
        self.assertFalse(module.is_persistently_below(mixed, 1.0, 3))
        # and two days of history is not three
        self.assertFalse(module.is_persistently_below(low[:2], 1.0, 3))

    def test_age_comes_from_the_current_holding_streak(self) -> None:
        module = self._module()
        fills = [
            {"symbol": "CRV/USDT", "side": "buy", "qty": 10.0, "ts": self._day(5)},
            {"symbol": "CRV/USDT", "side": "sell", "qty": 10.0, "ts": self._day(6)},
            {"symbol": "CRV/USDT", "side": "buy", "qty": 10.0, "ts": self._day(7)},
            {"symbol": "LTC/USDT", "side": "buy", "qty": 1.0, "ts": self._day(8)},
        ]
        self.assertEqual(module.open_since(fills, "CRV/USDT"), self._day(7))
        self.assertEqual(module.open_since(fills, "LTC/USDT"), self._day(8))
        self.assertIsNone(module.open_since(fills, "NEAR/USDT"))

    def test_breakeven_days_needs_a_real_gain(self) -> None:
        module = self._module()
        self.assertEqual(module.breakeven_days(44.9, 1.5), 30)
        self.assertEqual(module.breakeven_days(44.9, 4.49), 10)
        self.assertIsNone(module.breakeven_days(44.9, 0.0))
        self.assertIsNone(module.breakeven_days(44.9, -0.5))

    def test_a_stale_pair_rotates_into_the_best_free_name(self) -> None:
        module = self._module()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp)
            low = "\n".join(
                json.dumps({"ts": self._day(day, hour), "rate": 0.00002})
                for day in (8, 9, 10) for hour in (0, 8, 16)
            )
            (history / "CRV-USDT_USDT.jsonl").write_text(low + "\n", encoding="utf-8")
            fills = [{"symbol": "CRV/USDT", "side": "buy", "qty": 10.0, "ts": self._day(1)}]
            # 2026-10-13：候选现在必须带**同窗口序列**（`remeasure_candidates` 的产物），
            # 而且回本用**最差的一天**算 —— 这里三天都是 4.49，所以与旧断言同解。
            series = [("2026-10-08", 4.49, 3), ("2026-10-09", 4.49, 3), ("2026-10-10", 4.49, 3)]
            report = module.plan(
                {"CRV/USDT": {"pair_id": "x", "base_qty": 10.0}},
                history,
                [{"symbol": "WLFI/USDT:USDT", "bps": 4.49, "series": series,
                  "depth_ok": True, "eligible": True},
                 {"symbol": "CRV/USDT:USDT", "bps": 9.0, "series": series,
                  "depth_ok": True, "eligible": True}],
                fills,
                days=3, floor_bps=1.0, round_trip_bps=44.9, min_hold_hours=24.0,
                intended_hold_days=30.0,
                now_ms=self._day(10, 20),
            )
        pair = report["pairs"][0]
        self.assertTrue(pair["stale"])
        self.assertEqual(pair["median_bps"], 0.6)
        # the held name is not a candidate for itself even though it ranks highest
        self.assertEqual([r["to"] for r in report["rotations"]], ["WLFI/USDT:USDT"])
        # 4.49 bps/day against a 0.6 bps/day pair = 3.89 bps/day of gain, so the
        # 44.9 bps round trip is paid back in 12 days - inside the 30-day hold.
        self.assertEqual(report["rotations"][0]["breakeven_days"], 12)

    def test_a_young_pair_is_not_rotated_however_bad_it_looks(self) -> None:
        module = self._module()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp)
            low = "\n".join(
                json.dumps({"ts": self._day(day, hour), "rate": 0.00002})
                for day in (8, 9, 10) for hour in (0, 8, 16)
            )
            (history / "CRV-USDT_USDT.jsonl").write_text(low + "\n", encoding="utf-8")
            fills = [{"symbol": "CRV/USDT", "side": "buy", "qty": 10.0, "ts": self._day(10, 19)}]
            report = module.plan(
                {"CRV/USDT": {"pair_id": "x", "base_qty": 10.0}},
                history,
                [{"symbol": "WLFI/USDT:USDT", "bps": 9.0}],
                fills,
                days=3, floor_bps=1.0, round_trip_bps=44.9, min_hold_hours=24.0,
                intended_hold_days=30.0,
                now_ms=self._day(10, 20),
            )
        self.assertTrue(report["pairs"][0]["stale"])
        self.assertEqual(report["rotations"], [])


class DashboardDailyJobTests(unittest.TestCase):
    """A lost daily run has to be visible on the board, not only in the launchd log.

    The v1.0.2 changelog promised this half in writing ("把需补跑的步骤写进留痕/看板").
    Retries that self-heal are one thing; a day that still needs a re-run is a data
    gap in the making, and 1.1's IV-RV gate reads exactly the series this job writes.
    """

    def _data(self, root: Path):
        from ta.dashboard import DashboardData

        data = DashboardData(CONFIG_DIR)
        data.root = root
        data.ledger_path = root / "ledger.jsonl"
        data.alerts_path = root / "alerts.jsonl"
        data.live_metrics = lambda: {}  # no metrics endpoint in a unit test
        return data

    def _status(self, root: Path, payload: dict) -> None:
        (root / "data").mkdir(exist_ok=True)
        (root / "data" / "daily_job_status.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_missing_status_file_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._data(Path(tmp)).daily_job())

    def test_a_clean_run_reports_ok_and_the_retries_it_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._status(root, {
                "generated_at": "2026-10-10T07:13:57+00:00",
                "needs_rerun": [],
                "steps": {"a": {"rc": 0, "attempts": 1}, "b": {"rc": 0, "attempts": 2}},
            })
            job = self._data(root).daily_job()
        self.assertTrue(job["ok"])
        self.assertEqual(job["needs_rerun"], [])
        self.assertEqual(job["steps"], 2)
        self.assertEqual(job["steps_that_needed_a_retry"], {"b": 2})

    def test_a_failed_step_is_named_and_raises_a_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._status(root, {
                "generated_at": "2026-10-10T07:13:57+00:00",
                "needs_rerun": ["refresh_daily_bars.py"],
                "steps": {"refresh_daily_bars.py": {"rc": 1, "attempts": 3}},
            })
            data = self._data(root)
            job = data.daily_job()
            flags = [f for f in data.redflags() if f["title"] == "每日任务需补跑"]
        self.assertFalse(job["ok"])
        self.assertEqual(job["needs_rerun"], ["refresh_daily_bars.py"])
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["level"], "warn")
        self.assertIn("refresh_daily_bars.py", flags[0]["detail"])

    def test_a_corrupt_status_file_degrades_to_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "daily_job_status.json").write_text("{not json", encoding="utf-8")
            self.assertIsNone(self._data(root).daily_job())


class OptionsGateTests(unittest.TestCase):
    """The 1.1 gate is one inequality, so it should be evaluated, not eyeballed.

    `data/options/cost_history.jsonl` stores `variance_premium_pp = IV - RV`; a buyer
    is paid when the sign is the other way and the gap clears the ~1.3 vol points of
    round-trip cost. Measured 2026-10-10: BTC cleared it on all three days it has
    (5.54 / 12.41 / 7.65), ETH flipped sign twice - reading that off a series by hand
    is how a 3-day run gets mistaken for a 5-day one.
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "options_gate", PROJECT / "scripts" / "options_gate.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _rows(self, values: dict[str, list[float]], family: str = "BTC-USD") -> list[dict]:
        rows = []
        for index, value in enumerate(values.get(family, [])):
            rows.append({
                "date": f"2026-10-{8 + index:02d}",
                "family": family,
                "variance_premium_pp": value,   # IV - RV, as stored
            })
        return rows

    def test_the_stored_sign_is_flipped_once(self) -> None:
        module = self._module()
        # IV 36.2 against RV 41.7 => the file stores -5.5, and the buyer sees +5.5.
        self.assertAlmostEqual(module.rv_minus_iv({"variance_premium_pp": -5.54}), 5.54)
        self.assertIsNone(module.rv_minus_iv({}))

    def test_only_the_current_streak_counts(self) -> None:
        module = self._module()
        rows = self._rows({"BTC-USD": [-5.0, -12.0, 3.0]})
        info = module.gate_runs(rows, ["BTC-USD"], days=5, min_edge_pp=1.5)["BTC-USD"]
        self.assertEqual(info["run_days"], 0)
        self.assertFalse(info["open"])

    def test_a_short_streak_is_not_an_open_gate(self) -> None:
        module = self._module()
        rows = self._rows({"BTC-USD": [-5.0, -12.0, -7.65]})
        info = module.gate_runs(rows, ["BTC-USD"], days=5, min_edge_pp=1.5)["BTC-USD"]
        self.assertEqual(info["run_days"], 3)
        self.assertFalse(info["open"])
        self.assertTrue(module.gate_runs(rows, ["BTC-USD"], days=3, min_edge_pp=1.5)["BTC-USD"]["open"])

    def test_the_threshold_is_applied_not_rounded_towards_opening(self) -> None:
        module = self._module()
        rows = self._rows({"BTC-USD": [1.49, 1.49, 1.49]})
        info = module.gate_runs(rows, ["BTC-USD"], days=3, min_edge_pp=1.5)["BTC-USD"]
        self.assertEqual(info["run_days"], 0)

    def test_families_do_not_leak_into_each_other(self) -> None:
        module = self._module()
        rows = self._rows({"BTC-USD": [-5.0, -12.0]}) + self._rows({"ETH-USD": [6.5]}, "ETH-USD")
        runs = module.gate_runs(rows, ["BTC-USD", "ETH-USD"], days=2, min_edge_pp=1.5)
        self.assertTrue(runs["BTC-USD"]["open"])
        self.assertFalse(runs["ETH-USD"]["open"])
        self.assertEqual(runs["ETH-USD"]["run_days"], 0)

    def test_settings_degrade_to_the_defaults(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            missing = module.load_settings(Path(tmp) / "nope.json")
            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertEqual(module.load_settings(broken), missing)
        self.assertEqual(missing["gate"], {"days": 5, "min_edge_pp": 1.5})
        self.assertFalse(missing["enabled"])

    def test_a_partial_settings_file_merges_over_the_defaults(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "options_premium.json"
            path.write_text(
                json.dumps({"_note": "comment", "gate": {"days": 7}, "enabled": True}),
                encoding="utf-8",
            )
            settings = module.load_settings(path)
        self.assertEqual(settings["gate"]["days"], 7)
        self.assertEqual(settings["gate"]["min_edge_pp"], 1.5)  # untouched default
        self.assertTrue(settings["enabled"])
        self.assertNotIn("_note", settings)
        self.assertEqual(settings["entry"]["max_spread_pct"], 3.0)

    def test_the_shipped_config_file_ships_disabled(self) -> None:
        """The sleeve is off until the gate opens AND the premium coin exists."""
        module = self._module()
        settings = module.load_settings(PROJECT / "config" / "options_premium.json")
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["families"], ["BTC-USD", "ETH-USD"])
        self.assertEqual(settings["gate"]["min_edge_pp"], 1.5)
        self.assertEqual(settings["size"]["first_order_contracts"], 1)


class TradFiTierTests(unittest.TestCase):
    """Session-limited and dead look identical until you sample both sessions.

    2026-10-10: 12 of the sleeve's 13 symbols answered with zero bars and 11 had a
    bid with no ask - but every measurement so far was taken with the US market
    closed, so the honest verdict is "not decidable", not "narrow the tier".
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "tradfi_tier", PROJECT / "scripts" / "tradfi_tier.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _obs(session: bool, bars: int = 0, bid=None, ask=None) -> dict:
        return {"us_session": session, "bars_1h": bars, "bars_1d": bars,
                "bars_15m": bars, "bid": bid, "ask": ask}

    def test_tradable_needs_bars_and_both_sides(self) -> None:
        module = self._module()
        self.assertEqual(module.classify([self._obs(False, 300, 10.0, 10.1)])["verdict"],
                         "tradable_outside_session")
        self.assertEqual(module.classify([self._obs(True, 300, 10.0, 10.1)])["verdict"],
                         "tradable")
        # a bid with no ask is not a market, however many bars came with it
        self.assertEqual(module.classify([self._obs(True, 300, 10.0, None)])["verdict"],
                         "bars_but_one_sided")
        self.assertEqual(module.classify([self._obs(True, 0, None, None)])["verdict"],
                         "no_data")

    def test_a_symbol_tradable_in_both_sessions_is_kept(self) -> None:
        module = self._module()
        info = module.classify([self._obs(False, 300, 1.0, 1.1), self._obs(True, 300, 1.0, 1.1)])
        self.assertTrue(info["tradable_in_session"])
        self.assertEqual(info["verdict"], "tradable")
        self.assertEqual(info["max_bars_1h"], 300)

    def test_no_in_session_run_means_not_decided(self) -> None:
        module = self._module()
        history = [
            {"us_session": False, "rows": [{"symbol": "XMU/USDT", "bars_1h": 300,
                                            "bid": 1.0, "ask": 1.1, "state": "tradable"}]},
            {"us_session": False, "rows": [{"symbol": "XTSM/USDT", "bars_1h": 0,
                                            "bid": 4.0, "ask": None, "state": "quote-only"}]},
        ]
        report = module.summarise(history)
        self.assertFalse(report["decided"])
        self.assertEqual(report["keep"], [])
        self.assertEqual(report["symbols"]["XTSM/USDT"]["verdict"], "no_data")

    def test_an_in_session_run_produces_a_keep_list(self) -> None:
        module = self._module()
        history = [
            {"us_session": False, "rows": [{"symbol": "XMU/USDT", "bars_1h": 300,
                                            "bid": 1.0, "ask": 1.1, "state": "tradable"}]},
            {"us_session": True, "rows": [
                {"symbol": "XMU/USDT", "bars_1h": 300, "bid": 1.0, "ask": 1.1, "state": "tradable"},
                {"symbol": "XTSM/USDT", "bars_1h": 120, "bid": 4.0, "ask": 4.1, "state": "tradable"},
            ]},
        ]
        report = module.summarise(history)
        self.assertTrue(report["decided"])
        self.assertEqual(sorted(report["keep"]), ["XMU/USDT", "XTSM/USDT"])
        self.assertEqual(report["in_session_runs"], 1)


class SpotTdModeTests(unittest.TestCase):
    """A spot venue that cannot read `acctLv` rejects every order it sends.

    Measured 2026-10-10: three `51000 Parameter tdMode error` rejections in one day
    (two OKB/USDT buys at 04:43 and 04:45, one BTC/USDT sell at 11:16) against a
    probe that traded the same shape fine - because the probe HAD read the account
    level and the loop had not. `cash` is the fallback, and this account refuses it.
    """

    class _Exchange:
        def __init__(self, failures: int, level: str = "3") -> None:
            self.failures = failures
            self.level = level
            self.calls: list[dict] = []
            self.config_calls = 0

        def create_order(self, symbol, type, side, amount, price, params):
            self.calls.append(dict(params))
            if self.failures > 0:
                self.failures -= 1
                raise RuntimeError('okx {"sCode":"51000","sMsg":"Parameter tdMode error "}')
            return {"id": "ok", "status": "open"}

        def privateGetAccountConfig(self):
            self.config_calls += 1
            return {"data": [{"acctLv": self.level}]}

    def _venue(self, failures: int = 0, level: str = "3"):
        from ta.venues.ccxt_venue import CcxtVenue

        template = self._Exchange(failures, level)

        class Fake:
            market_type = "spot"
            spot_td_mode = "cash"
            td_mode_resolved = False
            account_level = 0
            last_td_mode_error = ""
            ex = template

            def _venue_symbol(self, symbol: str) -> str:
                return symbol

            resolve_spot_td_mode = CcxtVenue.resolve_spot_td_mode
            _create_order = CcxtVenue._create_order

        return Fake()

    @staticmethod
    def _request():
        from ta.models import OrderRequest

        return OrderRequest("c1", "BTC/USDT", "sell", 0.001, reduce_only=True)

    def test_the_account_level_decides_the_mode(self) -> None:
        from ta.venues.ccxt_venue import CcxtVenue

        margin = self._venue(level="3")
        self.assertEqual(CcxtVenue.resolve_spot_td_mode(margin), "cross")
        self.assertTrue(margin.td_mode_resolved)
        self.assertEqual(margin.account_level, 3)

        simple = self._venue(level="1")
        self.assertEqual(CcxtVenue.resolve_spot_td_mode(simple), "cash")
        self.assertTrue(simple.td_mode_resolved)

    def test_a_failed_probe_is_recorded_not_hidden(self) -> None:
        from ta.venues.ccxt_venue import CcxtVenue

        venue = self._venue()

        def boom():
            raise RuntimeError("proxy down")

        venue.ex.privateGetAccountConfig = boom
        self.assertEqual(CcxtVenue.resolve_spot_td_mode(venue), "cash")
        self.assertFalse(venue.td_mode_resolved)
        self.assertIn("proxy down", venue.last_td_mode_error)

    def test_a_tdmode_rejection_is_repaired_once_with_the_real_level(self) -> None:
        from ta.venues.ccxt_venue import CcxtVenue

        venue = self._venue(failures=1)
        raw = CcxtVenue._create_order(venue, self._request(), 0.001, {"tdMode": "cash"})
        self.assertEqual(raw["id"], "ok")
        self.assertEqual(len(venue.ex.calls), 2)
        self.assertEqual(venue.ex.calls[0]["tdMode"], "cash")
        self.assertEqual(venue.ex.calls[1]["tdMode"], "cross")   # repaired, not repeated
        self.assertEqual(venue.ex.config_calls, 1)

    def test_any_other_rejection_is_not_retried(self) -> None:
        from ta.venues.ccxt_venue import CcxtVenue

        venue = self._venue(failures=1)
        venue.ex.create_order = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("insufficient balance")
        )
        with self.assertRaises(RuntimeError):
            CcxtVenue._create_order(venue, self._request(), 0.001, {"tdMode": "cash"})
        self.assertEqual(venue.ex.config_calls, 0)

    def test_a_swap_venue_never_takes_the_spot_repair(self) -> None:
        from ta.venues.ccxt_venue import CcxtVenue

        venue = self._venue(failures=1)
        venue.market_type = "swap"
        with self.assertRaises(RuntimeError):
            CcxtVenue._create_order(venue, self._request(), 0.001, {"tdMode": "cross"})


class PreconditionGuardTests(unittest.TestCase):
    """2026-10-12 的 $0.27：`docs/options-sleeve-spec.md` §6.1 只在**文档里**被划掉，
    代码侧没有拦截，于是那件事被重跑了一次。这里的判据必须**能被脚本读**。"""

    def test_a_closed_precondition_refuses_and_says_why(self) -> None:
        from scripts.preconditions import is_executable

        ok, reason = is_executable({"id": "x", "status": "closed",
                                    "why": "§69 已证明这里买不到期权",
                                    "repeat_policy": "禁止重跑"})
        self.assertFalse(ok)
        self.assertIn("closed", reason)
        self.assertIn("禁止重跑", reason)

    def test_a_blocked_precondition_is_not_an_unfinished_one(self) -> None:
        from scripts.preconditions import is_executable

        ok, reason = is_executable({"id": "x", "status": "blocked", "why": "没有对手方"})
        self.assertFalse(ok)
        self.assertIn("外部条件不具备", reason)

    def test_an_open_precondition_expires(self) -> None:
        import datetime

        from scripts.preconditions import is_executable

        now = datetime.datetime(2026, 10, 13, 12, 0, tzinfo=datetime.UTC)
        before = {"id": "x", "status": "open", "expires_at": "2026-10-13T23:59:59+08:00"}
        after = {"id": "x", "status": "open", "expires_at": "2026-10-12T23:59:59+08:00"}
        self.assertTrue(is_executable(before, now)[0])
        ok, reason = is_executable(after, now)
        self.assertFalse(ok)
        self.assertIn("过期", reason)

    def test_an_open_precondition_without_expiry_is_allowed(self) -> None:
        from scripts.preconditions import is_executable

        self.assertTrue(is_executable({"id": "x", "status": "open"})[0])

    def test_the_registry_marks_the_btc_inventory_as_closed(self) -> None:
        from scripts.preconditions import load

        items = {i["id"]: i for i in load()}
        btc = items["options-funding-btc"]
        self.assertEqual(btc["status"], "closed")
        self.assertIn("禁止重跑", btc["repeat_policy"])
        self.assertEqual(btc["spec"], "docs/options-sleeve-spec.md §6.1")
        self.assertIn("$0.27", btc["cost_of_repeat"])

    def test_an_unknown_status_is_an_error_not_a_silent_no(self) -> None:
        import json
        import tempfile

        from scripts.preconditions import load

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps({"items": [{"id": "x", "status": "maybe"}]}),
                            encoding="utf-8")
            with self.assertRaises(SystemExit):
                load(path)


class VersionSnapshotGateTests(unittest.TestCase):
    """2026-10-13：冻结工具曾经把**红着的树**冻成版本，并把 `1 failed, 399 passed`
    读成 `tests=1` —— 而它的 docstring 写的是「测试不过就不是版本」。"""

    def _count(self, stdout: str, returncode: int) -> int | None:
        import subprocess
        from unittest import mock

        from scripts import version_snapshot as v

        fake = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout,
                                           stderr="")
        with mock.patch.object(v.subprocess, "run", return_value=fake):
            return v._test_count()

    def test_a_green_suite_reports_the_passed_count(self) -> None:
        self.assertEqual(self._count("400 passed in 49.10s\n", 0), 400)

    def test_a_red_suite_refuses_to_freeze(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self._count("1 failed, 399 passed in 49.21s\n", 1)
        self.assertIn("拒绝冻结", str(ctx.exception))

    def test_a_snapshot_manifest_records_source_and_config_without_shadowing_paths(self) -> None:
        """The first source-aware snapshot crashed by reusing `source` as a Path."""
        import sys
        from unittest import mock

        from scripts import version_snapshot as v

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "docs").mkdir()
            (root / "config" / "app.toml").write_text("mode = \"paper\"\n", encoding="utf-8")
            (root / "docs" / "progress.json").write_text('{"rows": []}', encoding="utf-8")
            (root / "docs" / "changelog.jsonl").write_text("", encoding="utf-8")
            source = {
                "git_sha": "abc123",
                "git_branch": "main",
                "git_tag": "v1.1.23",
                "git_dirty": False,
                "git_status_entries": 0,
            }
            with (
                mock.patch.object(v, "PROJECT", root),
                mock.patch.object(v, "package_version", "1.1.23"),
                mock.patch.object(v, "source_identity", return_value=source),
                mock.patch.object(v, "_test_count", return_value=500),
                mock.patch.object(v, "_equity_and_positions", return_value={}),
                mock.patch.object(sys, "argv", ["version_snapshot.py", "--version", "v1.1.23"]),
            ):
                self.assertEqual(v.main(), 0)
            manifest = json.loads(
                (root / "archive" / "versions" / "v1.1.23" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["source"], source)
            self.assertTrue(manifest["config_sha256"])
            self.assertEqual(manifest["config_files"][0]["path"], "app.toml")


class CiAnnotationTests(unittest.TestCase):
    def test_pytest_failures_are_combined_into_one_escaped_annotation(self) -> None:
        from scripts.ci_annotate_pytest import annotation_for

        text = (
            "....\n"
            "FAILED tests/test_core.py::test_a - ValueError: 100%\n"
            "FAILED tests/test_funding.py::test_b - AssertionError\n"
        )
        annotation = annotation_for(text)
        self.assertTrue(annotation.startswith("::error title=Pytest failures::"))
        self.assertIn("test_core.py::test_a", annotation)
        self.assertIn("test_funding.py::test_b", annotation)
        self.assertIn("100%25", annotation)
        self.assertNotIn("\n", annotation)


class VersionSyncReadmeTests(unittest.TestCase):
    """2026-10-14：版本号有**第四处** —— README 头部那句「当前版本 vX」。

    实测它停在 **v1.1.7**，而 `VERSION` 已经是 **1.1.22**：十五个版本的漂移，就在入口文档的第一段。
    一直没人发现，是因为 `version_sync.py` 的「三处」从来只数代码（`VERSION` / `pyproject.toml` /
    `__version__`），`docs/RELEASING.md` 第 4 步要求人顺手改 README，而**靠记性的那一步没有失过一次手
    才怪**。现在它进对账，且这个脚本的 `--check` 已经挂在每日任务里。
    """

    def _mod(self):
        return _load_script("version_sync")

    def test_the_live_project_has_all_four_in_agreement(self) -> None:
        found = self._mod().read_versions(PROJECT)
        self.assertIn("README.md", found, "README 那句没被读进来，等于没对账")
        self.assertEqual(set(found.values()), {found["VERSION"]})
        self.assertEqual(found["README.md"], (PROJECT / "VERSION").read_text(encoding="utf-8").strip())

    def test_a_stale_readme_line_is_a_failure(self) -> None:
        """把 README 写旧一版，四处就不再一致 —— 这正是 v1.1.7 那个状态。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "ta").mkdir(parents=True)
            (root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
            (root / "pyproject.toml").write_text('version = "9.9.9"\n', encoding="utf-8")
            (root / "src" / "ta" / "__init__.py").write_text('__version__ = "9.9.9"\n',
                                                             encoding="utf-8")
            (root / "README.md").write_text("**当前版本 v9.9.8**（昨天）。版本线：…\n",
                                            encoding="utf-8")
            found = self._mod().read_versions(root)
            self.assertEqual(found["README.md"], "9.9.8")
            self.assertEqual(set(found.values()), {"9.9.9", "9.9.8"})   # 不一致 → --check 会红

    def test_a_removed_readme_line_does_not_break_the_check(self) -> None:
        """README 头部被重写、那句话没了：**不加进对账**，也不许因此判红。

        「没写」与「写错」是两件事 —— 前者该由人决定要不要恢复，后者才是漂移。
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "ta").mkdir(parents=True)
            (root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
            (root / "pyproject.toml").write_text('version = "9.9.9"\n', encoding="utf-8")
            (root / "src" / "ta" / "__init__.py").write_text('__version__ = "9.9.9"\n',
                                                             encoding="utf-8")
            (root / "README.md").write_text("# 这个 README 不提版本\n", encoding="utf-8")
            found = self._mod().read_versions(root)
            self.assertNotIn("README.md", found)
            self.assertEqual(set(found.values()), {"9.9.9"})

    def test_a_version_mentioned_elsewhere_in_the_readme_is_not_mistaken(self) -> None:
        """正文里到处是别的版本号（「1.0 的范围」「1.1 的门槛」）—— 只认『当前版本 vX』。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "1.0 的范围 = 四腿系统；1.1 的门槛见 docs/RELEASING.md。\n"
                "**当前版本 v3.2.1**（今天）。\n"
                "git tag v0.4.6 与 archive/versions/v9.9.9 是历史快照。\n",
                encoding="utf-8")
            self.assertEqual(self._mod().read_readme_version(root), "3.2.1")


class VenueDegradedFreshnessTests(unittest.TestCase):
    """2026-10-13（10-12 复盘建议 ③）：只报「累计 N 次」等于永远等不到阈值 ——
    一次网络抖动会永久挂在面板上。改成按**最近 24h 次数**判，>5 次才 warn。"""

    def _flags(self, *, fresh: int, old: int) -> list[dict]:
        """**把现役 metrics 屏蔽掉**：`ta_venue_degraded_total` 是进程级累计值，
        本机跑测试时它非零（服务在跑），会让「只数账本里的新鲜事件」这件事测不出来。"""
        import datetime
        import json
        import shutil
        import tempfile
        from unittest import mock

        from ta.dashboard import DashboardData

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            (work / "data").mkdir(parents=True, exist_ok=True)
            now = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
            rows = []
            for i in range(fresh):
                rows.append({"kind": "risk", "ts": now - (i + 1) * 60_000,
                             "action": "venue degraded - new entries suspended"})
            for i in range(old):
                rows.append({"kind": "risk", "ts": now - (48 + i) * 3_600_000,
                             "action": "venue degraded - new entries suspended"})
            (work / "data" / "ledger.okx_demo.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            with mock.patch.object(DashboardData, "live_metrics", return_value={}):
                flags = DashboardData(work / "config").redflags()
            return [f for f in flags if f["title"] == "网络降级计数"]

    def test_a_single_recent_degradation_is_info_not_a_warn(self) -> None:
        flags = self._flags(fresh=1, old=0)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["level"], "info")
        self.assertIn("最近 24h **1 次**", flags[0]["detail"])

    def test_a_stream_of_degradations_warns(self) -> None:
        flags = self._flags(fresh=6, old=0)
        self.assertEqual(flags[0]["level"], "warn")
        self.assertIn("超过阈值 5 次/24h", flags[0]["detail"])

    def test_old_events_do_not_keep_the_flag_alive(self) -> None:
        """只有三天前那次 → 不报（这正是「累计」口径的毛病：它会一直挂着）。"""
        self.assertEqual(self._flags(fresh=0, old=1), [])


class DailyReviewDecompositionTests(unittest.TestCase):
    """2026-10-13（10-12 复盘建议 ②）：复盘数字块必须给「可复制部分」一行 ——
    否则「账面 +0.129%」会被单独引用，而其中大头是模拟盘费率买不到的钱。"""

    def _stats(self, *, delta: float, funding_bill: float, fees: float,
               live_total: float) -> dict:
        return {
            "equity_start": 100_000.0, "equity_end": 100_000.0 + delta,
            "equity_peak": 100_000.0 + delta, "equity_trough": 100_000.0,
            "fills": [], "fill_fees": fees, "funding_total": funding_bill,
            "funding_rows": 1, "funding_by_symbol": {}, "funding_top": ("X", funding_bill),
            "funding_backfilled": {}, "funding_live_rate": {"available": True,
                                                            "total": live_total},
            "blocked": [], "blocked_total": 0, "risk": [], "positions": {}, "legs": (0, 0),
        }

    def test_the_reproducible_part_is_spelled_out(self) -> None:
        from scripts.daily_review import markdown

        text = markdown("2026-10-12", self._stats(delta=131.58, funding_bill=129.8556,
                                                  fees=0.1999, live_total=0.5617))
        self.assertIn("可复制部分（下界）", text)
        # 其余 = 131.58 − 129.8556 + 0.1999 = 1.9243；可复制 = 0.5617 + 1.9243 = 2.4860
        self.assertIn("+2.4860", text)
        self.assertIn("账面百分比不要单独引用", text)

    def test_without_the_live_rate_row_nothing_is_claimed(self) -> None:
        from scripts.daily_review import markdown

        stats = self._stats(delta=131.58, funding_bill=129.8556, fees=0.1999, live_total=0.0)
        stats["funding_live_rate"] = {"reason": "没有费率文件"}
        text = markdown("2026-10-12", stats)
        self.assertNotIn("可复制部分", text)
        self.assertIn("拿不到：没有费率文件", text)


class TradfiWatchListTests(unittest.TestCase):
    """2026-10-13：TradFi 池收窄到 XMU 之后，「场馆哪天开始供数据」这个问题只对 10-10 那 13 个成立。
    OKX 每天在上新股票代币（近 8 天 8 个成熟、全 cat=3），所以最近 N 个也要进采样列表。"""

    def test_new_listings_join_the_watch_list_newest_first(self) -> None:
        import json
        import tempfile

        from scripts.venue_availability import watch_symbols

        with tempfile.TemporaryDirectory() as tmp:
            listings = Path(tmp) / "tradfi_records.jsonl"
            listings.write_text("\n".join(json.dumps(r) for r in [
                {"inst": "XOLD-USDT", "entry_ts": 1_000},
                {"inst": "XNEW-USDT", "entry_ts": 9_000},
                {"inst": "XNOTUSDT", "entry_ts": 9_500},          # 不是 USDT → 丢
            ]) + "\n", encoding="utf-8")
            syms = watch_symbols(["XMU/USDT"], Path(tmp) / "none.json",
                                 listings_path=listings, listings_cap=1)
        self.assertEqual(syms, ["XMU/USDT", "XNEW/USDT"])   # 取最新的那个，且转成场馆格式

    def test_a_missing_listings_file_changes_nothing(self) -> None:
        import tempfile

        from scripts.venue_availability import watch_symbols

        with tempfile.TemporaryDirectory() as tmp:
            syms = watch_symbols(["XMU/USDT"], Path(tmp) / "none.json",
                                 listings_path=Path(tmp) / "missing.jsonl")
        self.assertEqual(syms, ["XMU/USDT"])


class UniverseRefreshTests(unittest.TestCase):
    """2026-10-13：`refresh_daily_bars` 默认只刷期权标的，而 refit / daily_study 读的是**全库**日线 ——
    实测 52 个配置标的里 49 个停在 5.5 天前，研究侧一直在看一周前的世界。"""

    def test_universe_mode_covers_every_configured_symbol(self) -> None:
        from ta.config import Config
        from scripts.refresh_daily_bars import DEFAULT_SYMBOLS, universe_symbols

        config = Config.load(PROJECT / "config")
        symbols = universe_symbols(config)
        self.assertGreater(len(symbols), len(DEFAULT_SYMBOLS))
        for symbol in DEFAULT_SYMBOLS:                      # 期权标的一个都不能少
            self.assertIn(symbol, symbols)
        for items in (config.universe.symbols_by_tier or {}).values():
            for symbol in items:
                self.assertIn(str(symbol), symbols)
        self.assertEqual(len(symbols), len(set(symbols)))    # 去重


class DecisionsPanelTests(unittest.TestCase):
    """2026-10-13：两个项目的「待决事项」集中到看板总览 —— 它们原本只活在巡检回复与进度表里，
    而进度表看板根本不读，于是「下一步做什么」的唯一事实来源在看板上是隐形的。"""

    def test_waiting_decision_rows_are_surfaced(self) -> None:
        import json
        import shutil
        import tempfile

        from ta.dashboard import DashboardData

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):          # DashboardData 要能加载配置
                shutil.copy(toml, work / "config" / toml.name)
            (work / "docs").mkdir(parents=True)
            (work / "docs" / "progress.json").write_text(json.dumps({
                "updated": "2026-10-13",
                "rows": [
                    {"id": "a", "state": "waiting_decision", "item": "要决定 A",
                     "next": "选项 1 或 2"},
                    {"id": "b", "state": "done", "item": "做完的"},
                    {"id": "c", "state": "waiting_data", "item": "等数据的"},
                ]}, ensure_ascii=False), encoding="utf-8")
            out = DashboardData(work / "config").decisions()
        self.assertEqual([r["id"] for r in out["rows"]], ["a"])
        self.assertEqual(out["rows"][0]["next"], "选项 1 或 2")

    def test_a_missing_progress_file_is_a_reason_not_a_crash(self) -> None:
        import shutil
        import tempfile

        from ta.dashboard import DashboardData

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            out = DashboardData(work / "config").decisions()
        self.assertEqual(out["rows"], [])
        self.assertIn("Error", out["reason"])


class NewListingCategoryTests(unittest.TestCase):
    """2026-10-13：`new_listing_track` 原来写死 `instCategory == "1"`，日志只写「added 0」——
    近 8 天 OKX 上线的 32 个交易对里，8 个已成熟的 X***（股票代币，cat=3）一个都没记，
    而读的人分不清「真的没有」和「被口径滤掉了」。"""

    ROWS = [
        {"instId": "AAA-USDT", "listTime": 1_000, "state": "live", "instCategory": "1"},
        {"instId": "XBBB-USDT", "listTime": 1_000, "state": "live", "instCategory": "3"},
        {"instId": "CCC-USDT", "listTime": 1_000, "state": "suspend",
         "instCategory": "1"},                                    # 非 live → 丢
        {"instId": "DDD-USDC", "listTime": 1_000, "state": "live",
         "instCategory": "1"},                                    # 非 USDT → 丢
        {"instId": "EEE-USDT", "listTime": 50, "state": "live",
         "instCategory": "1"},                                    # 太老（不在窗口）→ 丢
    ]

    def test_pending_listings_filters_by_category_window_and_store(self) -> None:
        from scripts.new_listing_track import pending_listings

        got = pending_listings(self.ROWS, {}, categories={"1"}, cutoff_ms=500, mature_ms=2_000)
        self.assertEqual(got, [("AAA-USDT", 1_000)])
        # 已经记过的不再记（幂等）
        again = pending_listings(self.ROWS, {"AAA-USDT": {}}, categories={"1"},
                                 cutoff_ms=500, mature_ms=2_000)
        self.assertEqual(again, [])
        # 换成股票代币口径 → 拿到的就是那一类
        x = pending_listings(self.ROWS, {}, categories={"3"}, cutoff_ms=500, mature_ms=2_000)
        self.assertEqual(x, [("XBBB-USDT", 1_000)])

    def test_filtered_by_category_makes_zero_visible(self) -> None:
        from scripts.new_listing_track import filtered_by_category

        counted = filtered_by_category(self.ROWS, categories={"1"},
                                       cutoff_ms=500, mature_ms=2_000)
        self.assertEqual(counted, {"3": 1})     # 「added 0」时也要说清挡掉了什么


class DoctorStrategyListTests(unittest.TestCase):
    """2026-10-13：`ta doctor` 原来把**实现**和**在跑的策略**印成同一行（8 个 vs 6 个），
    看起来像少了两条。实际两处差异都是有意的：`perp_trend_funding` 在 config 里 disabled
    并写明原因，`random_control` 只存在于 `config-exp/`（README §25 的随机对照）。
    混淆是真实成本 —— 我自己就查了几分钟才确认不是配置漂移。"""

    def test_doctor_separates_enabled_from_defined_but_off(self) -> None:
        import contextlib
        import io

        from ta.cli import main as cli_main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["doctor", "--config-dir", str(CONFIG_DIR)])
        out = buf.getvalue()
        self.assertIn("(enabled in", out)
        enabled_line = next(l for l in out.splitlines() if l.startswith("strategies"))
        off_line = next(l for l in out.splitlines() if l.strip().startswith("defined, off"))
        # 跑在循环里的那些在 enabled 行，两个有意的例外在 off 行
        for name in ("trend_breakout", "carry_funding", "new_listing_breakout"):
            self.assertIn(name, enabled_line)
        self.assertIn("perp_trend_funding", off_line)
        self.assertIn("random_control", off_line)
        self.assertNotIn("perp_trend_funding", enabled_line)

    def test_the_config_still_says_why_the_perp_leg_is_off(self) -> None:
        """关掉它是有意的，理由必须留在配置里（否则下一个人会当成漂移）。"""
        text = (CONFIG_DIR / "strategies.toml").read_text(encoding="utf-8")
        block = text.split("[strategies.perp_trend_funding]")[1].split("[strategies.")[0]
        self.assertIn("enabled                 = false", block)
        self.assertIn("DISABLED ON PURPOSE", block)
        self.assertIn("REAL FUNDING HISTORY", block)


class FundingHistorySummaryTests(unittest.TestCase):
    """2026-10-13：日更摘要把两件事混成一个词 —— "取不到费率"与"已是最新"。

    实测那句 `updated 140, unchanged 14, failed 1` 里的 14 个**根本没有文件**：
    本脚本刻意取公共（主网）费率（§70.8 定的口径），而模拟盘目录里有一批只存在于
    demo 的合约（PIGGY / AI16Z / TEST* …）——主网对它们返回空。分开报数之后，
    "unchanged"才重新只表示"已经跟上了"。
    """

    def test_the_four_outcomes_are_counted_separately(self) -> None:
        from scripts.fetch_funding_history import summary_line

        line = summary_line(140, 5, 9, 1)
        self.assertIn("updated 140", line)
        self.assertIn("unchanged 5", line)
        self.assertIn("no-data 9", line)      # 不能并进 unchanged
        self.assertIn("failed 1", line)
        self.assertIn("主网", line)            # 说清 no-data 是什么意思

    def test_a_clean_sweep_says_zero_rather_than_hiding_the_bucket(self) -> None:
        from scripts.fetch_funding_history import summary_line

        line = summary_line(155, 0, 0, 0)
        self.assertIn("no-data 0", line)      # 有桶就要显式出现，别等出事才发现它存在
        self.assertIn("failed 0", line)


class LiveReadinessTests(unittest.TestCase):
    """README 70.6 的六条「切实盘门槛」以前只活在散文里，每次都要重新论证一遍。
    2026-10-13 把它们做成 `scripts/live_readiness.py` 的**可复核数字**。这批测试
    盯的是三个最容易出错的地方：能不能从真实输出里解析出样本外数字、已知项与
    非结构性差异的分类、以及「没数据」必须说没数据而不是给个 0。
    """

    def _mod(self):
        return _load_script("live_readiness")

    # 这是 `ta backtest --split` 的真实输出形状（2026-10-13 实测，见 README 70.7）
    OOS = """
walk-forward split at ts=1790000000000
  --- in-sample (shape only; this is where tuning would happen)

backtest mean_reversion  symbols=BTC/USDT,ETH/USDT  bars=3000  timeframe=15m
  total_return_pct     12.34
  trades               99

  --- out-of-sample (the only number that means anything)

backtest mean_reversion  symbols=BTC/USDT,ETH/USDT  bars=1745  timeframe=15m
  final_equity         9991.69
  total_return_pct     -0.08
  trades               5
  win_rate_pct         33.3
  profit_factor        0.38
  exits                {'stop_loss': 2}

  out-of-sample at 3x costs: -0.11% (decay 0.03 pp)
  !! only 5 out-of-sample trades: not enough to conclude anything
"""

    def test_parse_oos_reads_the_out_of_sample_block_not_the_in_sample_one(self) -> None:
        got = self._mod().parse_oos(self.OOS)
        self.assertEqual(got["total_return_pct"], -0.08)   # 不是 in-sample 的 12.34
        self.assertEqual(got["trades"], 5)
        self.assertEqual(got["win_rate_pct"], 33.3)
        self.assertIsNone(got.get("nope"))

    def test_an_unreadable_backtest_block_is_empty_not_zero(self) -> None:
        """解析不出样本外段时不能返回 {'total_return_pct': 0}：那会读成「不赚不亏」。"""
        self.assertEqual(self._mod().parse_oos("no walk-forward here"), {})

    def test_parse_oos_remembers_where_the_out_of_sample_window_starts(self) -> None:
        """2026-10-13：把行情从 10-08 补到 10-13 之后，同一批策略的样本外数字**全变了**
        （−1.70%→−1.12%、0 笔→14 笔、−1.61%→−2.02%），而结论没变。
        因为切点是「序列长度的 70%」—— 序列变长，切点前移，**样本外窗口整段平移**：
        两次数的不是同一段时间。所以窗口起点必须和数字一起存下来，否则下一次对比
        就是拿两个不同区间互相解释。"""
        module = self._mod()
        text = ("\nwalk-forward split at ts=1786000000000\n"
                "  --- in-sample (shape only)\n\n"
                "  --- out-of-sample (the only number that means anything)\n\n"
                "backtest mean_reversion  symbols=BTC/USDT  bars=100  timeframe=15m\n"
                "  total_return_pct     -0.17\n"
                "  trades               14\n")
        got = module.parse_oos(text)
        self.assertEqual(got["total_return_pct"], -0.17)
        self.assertEqual(got["oos_from_ms"], 1786000000000)
        self.assertEqual(got["oos_from"], module.day_of(1786000000000))
        # 没有 split 行的输出（旧格式/未带 --split）不能凭空造一个窗口起点
        bare = ("  --- out-of-sample (the only number that means anything)\n\n"
                "backtest mean_reversion  symbols=BTC/USDT  bars=100  timeframe=15m\n"
                "  total_return_pct     -0.17\n  trades               14\n")
        self.assertNotIn("oos_from", module.parse_oos(bare))

    def _root(self, tmp: str) -> Path:
        root = Path(tmp)
        (root / "data" / "readiness").mkdir(parents=True, exist_ok=True)
        (root / "logs").mkdir(parents=True, exist_ok=True)
        return root

    def test_gate1_says_unmeasured_when_the_backtest_file_is_absent(self) -> None:
        import tempfile

        module = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            gate = module.gate_edge(self._root(tmp))
        self.assertEqual(gate["status"], "unmeasured")
        self.assertIn("--backtest", gate["detail"])

    def test_gate1_needs_a_positive_strategy_with_enough_trades(self) -> None:
        import tempfile

        module = self._mod()
        rows = [
            # 有 180 笔但为负：样本够、边缘不够 —— 这正是 2026-10-13 的现实
            {"strategy": "perp_trend_funding", "total_return_pct": -1.61, "trades": 180,
             "tb_selected_for_gate1": False},
            # 正收益但只有 3 笔：不够下结论
            {"strategy": "trend_breakout_daily", "total_return_pct": 0.02, "trades": 3,
             "tb_selected_for_gate1": False},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "data" / "readiness" / "edge.json").write_text(
                json.dumps({"generated_at": "2026-10-13T07:56:50+00:00", "rows": rows},
                           ensure_ascii=False), encoding="utf-8")
            gate = module.gate_edge(root)
        self.assertEqual(gate["status"], "unmet")
        self.assertIn("0/1", gate["value"])

        rows[1]["tb_selected_for_gate1"] = True
        rows[1]["trades"] = 40
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "data" / "readiness" / "edge.json").write_text(
                json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
            gate = module.gate_edge(root)
        self.assertEqual(gate["status"], "met")
        self.assertIn("1/2", gate["value"])

    def test_gate2_counts_fills_and_calendar_days_from_the_ledger(self) -> None:
        import tempfile

        module = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            day = 1_790_000_000_000  # 毫秒；同一天里放几行
            rows = [{"kind": "fill", "ts": day + i * 1000} for i in range(3)]
            rows += [{"kind": "equity", "ts": day + 5_000}]
            (root / "data" / "ledger.okx_demo.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            gate = module.gate_sample(root)
        self.assertEqual(gate["status"], "unmet")     # 3 笔、1 天，两个门槛都没到
        self.assertIn("3 笔", gate["value"])
        self.assertIn("1 个交易日", gate["value"])

    def test_gate3_known_cash_residual_does_not_break_the_streak(self) -> None:
        """只含 `cash delta` 的是**已知项**（期望线跟着资金费走）；含 `position` 的才是新闻。"""
        import tempfile
        from datetime import datetime, timedelta

        module = self._mod()
        cst = module.CST
        today = datetime.now(cst).date()

        def alert(day, diffs):
            when = datetime(day.year, day.month, day.day, 12, 0, tzinfo=cst)
            return {"ts": when.isoformat(), "level": "warning",
                    "title": "reconciliation mismatch", "diffs": diffs}

        cash = ["cash delta: expected 92464.40 but venue shows 91405.50"]
        rows = [alert(today - timedelta(days=n), cash) for n in range(4)]
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "logs" / "alerts.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            gate = module.gate_execution(root)
            self.assertEqual(gate["value"].split(" ·")[0], "4/30 天")
            self.assertIn("已知项", gate["detail"])

            # 昨天出现一条带持仓差异的 → 连击从今天重新开始
            rows.append(alert(today - timedelta(days=1),
                              ["position BTC/USDT: local=2.5e-09 venue=0.0012"] + cash))
            (root / "logs" / "alerts.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            gate = module.gate_execution(root)
        self.assertEqual(gate["value"].split(" ·")[0], "1/30 天")
        self.assertIn("position BTC/USDT", gate["detail"])

    def test_gate4_counts_two_green_runs_per_day(self) -> None:
        import tempfile
        from datetime import datetime, timedelta

        module = self._mod()
        today = datetime.now(module.CST).date()
        rows = []
        for back in range(3):
            date = (today - timedelta(days=back)).isoformat()
            for _ in range(2):
                rows.append({"date": date, "needs_rerun": [], "generated_at": "x"})
        # 第 4 天只跑了一轮 → 不算一天
        rows.append({"date": (today - timedelta(days=3)).isoformat(), "needs_rerun": []})
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "data" / "daily_job_history.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            gate = module.gate_pipeline(root)
        self.assertEqual(gate["value"], "3/14 天")

        rows.append({"date": (today - timedelta(days=1)).isoformat(),
                     "needs_rerun": ["options_gate.py"]})
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "data" / "daily_job_history.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            gate = module.gate_pipeline(root)
        self.assertEqual(gate["value"], "1/14 天")     # 昨天有一轮红了 → 只有今天

    def test_the_daily_job_appends_a_dated_run_row(self) -> None:
        """`data/daily_job_status.json` 每轮被覆盖，只答得了「最近一轮」；
        门槛 4 问的是 14 天，所以必须另有一份 append-only 的逐轮记录。"""
        import tempfile
        from datetime import datetime

        module = self._mod()  # 复用 PROJECT 常量，别让测试写到真项目里
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "history.jsonl"
            daily = _load_script("daily_research")
            daily.write_status(Path(tmp) / "status.json", {"a": 0, "b": 1}, {"a": 1, "b": 3}, history)
            rows = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["needs_rerun"], ["b"])
        self.assertEqual(rows[0]["steps"], {"a": 0, "b": 1})
        today = datetime.now(module.CST).date().isoformat()
        self.assertEqual(rows[0]["date"], today)

    def test_report_marks_the_venue_gate_as_never_auto_judgeable(self) -> None:
        module = self._mod()
        gate = module.gate_venue_only()
        self.assertEqual(gate["status"], "not_auto")
        self.assertIn("最小仓位", gate["detail"])

    def test_reject_classes_are_keyed_on_the_venue_scode(self) -> None:
        """分类要机械、可复核 —— 报告只负责说「这一类最近一次是什么时候」，
        不负责替读者宣布「已经修好了」。"""
        module = self._mod()
        sdk = ('BadRequest: okx {"code":"1","data":[{"sCode":"51000",'
               '"sMsg":"Parameter tdMode error "}]}')
        self.assertEqual(module.classify_reject({"reason": sdk})[0], "51000")
        self.assertEqual(
            module.classify_reject({"reason": "InvalidOrder: okx amount of SUI/USDT must be "
                                             "greater than minimum amount precision of 0.0001"})[0],
            "dust")
        self.assertEqual(
            module.classify_reject({"reason": "dust: 1.4e-06 SUI/USDT is below the venue "
                                             "minimum 1.0 - not sent"})[0],
            "dust")
        self.assertEqual(
            module.classify_reject({"reason": "InsufficientFunds: okx {\"code\":\"1\"}"})[0],
            "51008")
        self.assertEqual(module.classify_reject({"reason": "something new"})[0], "other")

    def test_venue_degraded_is_not_counted_as_an_incident(self) -> None:
        """`venue degraded` 是**已知外部条件**（代理上游抖动），却记在 `critical` 级 ——
        按级别当事故数的话，这条门槛在这个环境里永远不可能满足（10-13 实测 8 天里抖了 8 天）。
        它必须单独计数，而 kill switch 这类才是事故。"""
        import tempfile
        from datetime import datetime, timedelta

        module = self._mod()
        today = datetime.now(module.CST).date()

        def alert(back, level, title):
            when = datetime.combine(today - timedelta(days=back),
                                    datetime.min.time(), tzinfo=module.CST).replace(hour=12)
            return {"ts": when.isoformat(), "level": level, "title": title}

        # 已知项的形状是「只含 cash delta」；没有 diffs 的对账 mismatch 属于**没解释**，
        # 按保守口径应当打断连击（这条测试只放已知项）。
        known = ["cash delta: expected 92464.40 but venue shows 91405.50"]
        rows = [dict(alert(n, "warning", "reconciliation mismatch"), diffs=list(known))
                for n in range(3)]
        rows += [alert(n, "critical", module.VENUE_DEGRADED_TITLE) for n in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "logs" / "alerts.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            gate = module.gate_execution(root)
            self.assertEqual(gate["value"].split(" ·")[0], "3/30 天")   # 降级不打断连击
            self.assertIn("网络降级", gate["detail"])

            rows.append(alert(1, "critical", "kill switch"))            # 事故才打断
            (root / "logs" / "alerts.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            gate = module.gate_execution(root)
        self.assertEqual(gate["value"].split(" ·")[0], "1/30 天")
        self.assertIn("事故级事件", gate["detail"])

    def test_the_service_log_bounds_gate4_but_cannot_answer_it(self) -> None:
        """服务日志只有轮数、没有日期，而且里面有红轮 —— 它能给出上界，答不了「14 天」。
        把这件事写进报告，比让读者以为「日志里看着挺绿」可靠。"""
        import tempfile

        module = self._mod()
        log = "\n".join([
            "=== new_listing_track ===",
            "=== summary ===",
            "  new_listing_track    rc=0 attempts=1",
            "  vol_monitor          rc=0 attempts=1",
            "=== new_listing_track ===",
            "=== summary ===",
            "  new_listing_track    rc=1 attempts=3",
            "  changelog_md.py      rc=1 attempts=3",
            "=== new_listing_track ===",
            "=== summary ===",
            "  new_listing_track    rc=0 attempts=1",
            "  changelog_md.py      rc=0 attempts=1",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "logs").mkdir(parents=True, exist_ok=True)
            (root / "logs" / "ta-newlisting-service.log").write_text(log, encoding="utf-8")
            got = module.service_log_runs(root)
            gate = module.gate_pipeline(root)
        self.assertEqual(got["runs"], 3)
        self.assertEqual(got["red"], 1)
        self.assertEqual(got["trailing_green"], 1)
        self.assertIn("changelog_md.py", " ".join(got["last_red"]))
        self.assertIn("补不出历史", gate["detail"])

    def test_a_known_inventory_diff_matches_our_own_tagged_balance(self) -> None:
        """`position BTC/USDT local=2.5e-09 venue=0.0012070525` 之所以是「已知」，
        不是因为它眼熟，而是因为**我们自己带标签买过这么多、还没卖回**。
        实测那一串：19:10 买 0.00120705、20:00 试卖 0.0001 → 21:36 场馆仍显示
        0.00110705 = 滚动余额。用「多久之内有成交」的窗口会漏掉 21:36 那条。"""
        module = self._mod()
        base = 1_790_000_000_000
        ledger = [
            {"kind": "fill", "symbol": "BTC/USDT", "tag": "options-funding",
             "side": "buy", "qty": 0.00120705, "price": 120000.0, "ts": base},
            {"kind": "fill", "symbol": "BTC/USDT", "tag": "btc-premium-inventory",
             "side": "sell", "qty": 0.0001, "price": 120100.0, "ts": base + 50 * 60_000},
        ]
        windows = module.inventory_fills(ledger)
        prices = module.reference_prices(ledger)

        # 买完立刻：场馆等于买入量
        first = {"ts": _iso(base), "diffs": [
            "position BTC/USDT: local=2.5e-09 venue=0.0012070525"]}
        self.assertEqual(module.classify_position_diff(first, windows, prices), "inventory")
        # 两小时后（远超任何 ±15 分钟窗口）：场馆等于 买-卖 的滚动余额
        later = {"ts": _iso(base + 146 * 60_000), "diffs": [
            "position BTC/USDT: local=2.5e-09 venue=0.0011070525"]}
        self.assertEqual(module.classify_position_diff(later, windows, prices), "inventory")
        # 全部卖回之后场馆还有货 => 余额对不上 => 是新闻
        after = {"ts": _iso(base + 10 * 3_600_000), "diffs": [
            "position BTC/USDT: local=2.5e-09 venue=0.0011948225"]}
        self.assertEqual(module.classify_position_diff(after, windows, prices), "unexplained")

    def test_a_diff_on_a_symbol_we_never_tagged_is_always_news(self) -> None:
        module = self._mod()
        base = 1_790_000_000_000
        ledger = [{"kind": "fill", "symbol": "BTC/USDT", "tag": "options-funding",
                   "side": "buy", "qty": 0.001, "price": 120000.0, "ts": base}]
        alert = {"ts": _iso(base), "diffs": ["position ETH/USDT: local=0.0 venue=12.5"]}
        self.assertEqual(
            module.classify_position_diff(alert, module.inventory_fills(ledger),
                                          module.reference_prices(ledger)),
            "unexplained")

    def test_dust_against_dust_is_measured_not_guessed(self) -> None:
        """`INJ local=8.29e-06 venue=9.37e-06` 两侧都是 0；但如果**价差换得出钱**，
        同一个形状就必须算新闻（`BAND local=6792 venue=0` 那种）。"""
        module = self._mod()
        base = 1_790_000_000_000
        ledger = [{"kind": "fill", "symbol": "INJ/USDT", "side": "buy",
                   "qty": 10.0, "price": 20.0, "ts": base}]
        windows, prices = module.inventory_fills(ledger), module.reference_prices(ledger)
        dust = {"ts": _iso(base), "diffs": [
            "position INJ/USDT: local=8.29e-06 venue=9.37e-06"]}
        self.assertEqual(module.classify_position_diff(dust, windows, prices), "dust")
        real = {"ts": _iso(base), "diffs": [
            "position BAND/USDT: local=6792.0 venue=0.0"]}
        self.assertEqual(module.classify_position_diff(real, windows, prices), "unexplained")
        # 没有价格就判不了「灰尘」—— 宁可当新闻，也不猜
        self.assertEqual(
            module.classify_position_diff(
                {"ts": _iso(base), "diffs": ["position ZZZ/USDT: local=1e-06 venue=2e-06"]},
                windows, prices),
            "unexplained")

    def test_carry_rotation_reports_how_close_it_came(self) -> None:
        """门槛 5 的「换腿至少触发过一次」= 0，但 0 本身没有信息量。
        规则要的是「某条腿连续 3 天每天都低于 floor」；报告要能说出**差在哪一天**，
        否则「0 次」既可能是规则坏了、也可能是市场没给 —— 这两件事要分开。"""
        module = self._mod()
        rows = [
            {"generated_at": "2026-10-11T01:19:14+00:00", "days": 3, "floor_bps": 1.0,
             "pairs": [{"symbol": "LTC/USDT", "stale": False, "median_bps": 0.114,
                        "daily_bps": [["2026-10-09", 0.114, 3], ["2026-10-10", 0.081, 3],
                                      ["2026-10-11", 1.508, 1]]}],
             "rotations": []},
            {"generated_at": "2026-10-13T01:19:22+00:00", "days": 3, "floor_bps": 1.0,
             "pairs": [{"symbol": "LTC/USDT", "stale": True, "median_bps": 0.33,
                        "daily_bps": [["2026-10-11", 0.5, 3], ["2026-10-12", 0.2, 3],
                                      ["2026-10-13", 0.3, 1]]}],
             "rotations": []},
        ]
        # 平手取最近一轮（「现在离得多近」才是要问的）
        near = module.closest_call(rows)
        self.assertEqual(near["symbol"], "LTC/USDT")
        self.assertEqual(near["when"], "2026-10-13T01:19")   # 报告只显示到分钟
        self.assertEqual((near["below"], near["window"]), (3, 3))
        self.assertEqual(near["blocked"], [])

        earlier = module.closest_call(rows[:1])
        self.assertEqual(earlier["below"], 2)
        self.assertEqual(earlier["blocked"], [("2026-10-11", 1.508)])
        self.assertEqual(module.closest_call([]), None)

    def test_candidate_is_remeasured_on_the_incumbent_window(self) -> None:
        """换腿规则的现任是「3 天中位」，候选却是扫描快照的 100 个样本中位
        （4h 结算 ⇒ ≈17 天）。同一个名字因此可以看着比它近期实际支付的好一倍 ——
        2026-10-13 的 TRIA：快照 11.933、最近 5 天 5.2。"""
        import tempfile

        module = self._mod()
        import datetime

        day_ms = 86_400_000
        base_ms = int(datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)
                      .timestamp() * 1000)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "data" / "carry").mkdir(parents=True, exist_ok=True)
            (root / "data" / "carry" / "screen_live.json").write_text(json.dumps({
                "rows": [
                    {"symbol": "TRIA/USDT:USDT", "median_daily_bps": 11.933,
                     "samples": 100, "settle_hours": 4.0, "depth_ok": False,
                     "depth_bps": None},
                    {"symbol": "LTC/USDT:USDT", "median_daily_bps": 99.0,
                     "samples": 100, "settle_hours": 8.0, "depth_ok": True},
                ]}, ensure_ascii=False), encoding="utf-8")
            hist = root / "data" / "history" / "funding"
            hist.mkdir(parents=True, exist_ok=True)
            # 5 天 × 6 个 4h 结算，bps/天 = 12,12,3,3,3 → 取中位 3.0
            rates = [0.0002, 0.0002, 0.00005, 0.00005, 0.00005]
            prints = []
            for day, rate in enumerate(rates):
                for slot in range(6):
                    prints.append({"ts": base_ms + day * day_ms + slot * 14_400_000,
                                   "rate": rate})
            (hist / "TRIA-USDT_USDT.jsonl").write_text(
                "\n".join(json.dumps(p) for p in prints) + "\n", encoding="utf-8")
            got = module.candidate_window(root, ["BAND", "LTC", "NEAR", "SUI"])
        self.assertEqual(got["symbol"], "TRIA/USDT:USDT")   # 已持有的 LTC 不当候选
        self.assertEqual(got["screen_bps"], 11.933)
        self.assertAlmostEqual(got["window_days"], 100 / 6, places=1)
        self.assertAlmostEqual(got["recent_bps"], 3.0, places=1)
        self.assertFalse(got["depth_ok"])
        self.assertGreater(got["recent_breakeven"], 4.0)    # 折算后回本比快照说得久

    def test_gate1_lists_every_leg_not_just_the_strategies(self) -> None:
        """门槛 1 写的是「至少一条**腿**/策略」，第一版只量了策略那一半 ——
        carry、TradFi、期权都没有出现在答案里。而 carry 恰恰是唯一扣掉成本后
        为正的那条腿，它缺的是「30 天样本」（和门槛 5 同一口钟）。"""
        import tempfile

        module = self._mod()
        edge_rows = [
            {"strategy": "trend_breakout", "total_return_pct": -1.70, "trades": 94},
            {"strategy": "trend_breakout_daily", "total_return_pct": 0.02, "trades": 3},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "data" / "carry").mkdir(parents=True, exist_ok=True)
            (root / "data" / "carry" / "income_report.json").write_text(json.dumps(
                {"report": {"realised_days": 6, "net_bps_per_30d_hold": 22.4}}), encoding="utf-8")
            (root / "data" / "options").mkdir(parents=True, exist_ok=True)
            (root / "data" / "options" / "gate.jsonl").write_text(json.dumps(
                {"families": {"BTC-USD": {"open": True}}}) + "\n", encoding="utf-8")
            (root / "data" / "options" / "round_trip.jsonl").write_text(
                "\n".join(json.dumps({"error": "buy rejected"}) for _ in range(4)) + "\n",
                encoding="utf-8")
            legs = module.leg_inventory(root, edge_rows)
        by_leg = {g["leg"]: g for g in legs}
        # 有样本但为负 → 明确说"有样本"而不是含混的"未满足"
        self.assertIn("❌", by_leg["现货方向性"]["verdict"])
        # 只有 3 笔 → 判不了，不能算它不合格
        self.assertIn("样本不足", by_leg["TradFi"]["verdict"])
        # carry：唯一为正的腿，但样本不够
        self.assertIn("6/30", by_leg["carry（资金费）"]["verdict"])
        self.assertIn("22.4", by_leg["carry（资金费）"]["number"])
        # 期权：把"这个环境成交不了"写成数字（4 次尝试 4 次失败）
        self.assertIn("4 次尝试", by_leg["期权"]["number"])
        self.assertIn("🚫", by_leg["期权"]["verdict"])

    def test_a_degraded_backtest_never_replaces_a_real_measurement(self) -> None:
        """`--backtest` 原来无条件覆盖 `edge.json`：五条回放全挂时，盘上五条好数据会被
        五个 `None` 顶掉，而门槛 1 照样输出一个「0/2」装作量过了。本项目在 10-13 已经
        吃过一次同形状的亏（网络抖动把证据表写成空表），所以规则是：**降级的运行不许
        变成新的事实**。"""
        module = self._mod()
        good = {"rows": [{"strategy": "trend_breakout", "total_return_pct": -1.7, "trades": 94}]}
        self.assertTrue(module.should_replace_edge(good))
        self.assertFalse(module.should_replace_edge({}))
        self.assertFalse(module.should_replace_edge({"rows": []}))
        # 五条全 rc!=0 / 解析不出数字 —— 这是"跑挂了"，不是"结论变了"
        self.assertFalse(module.should_replace_edge({"rows": [
            {"strategy": "trend_breakout", "total_return_pct": None, "rc": 1},
            {"strategy": "mean_reversion", "total_return_pct": None, "rc": 1},
        ]}))
        # 只要有一条真跑出数字，就允许替换（其余可以缺）
        self.assertTrue(module.should_replace_edge({"rows": [
            {"strategy": "trend_breakout", "total_return_pct": None, "rc": 1},
            {"strategy": "mean_reversion", "total_return_pct": -0.08, "trades": 5},
        ]}))

    def test_a_stale_gate1_measurement_says_so(self) -> None:
        """`generated_at` 一直打印着，但**没人比较的日期不是护栏**：刷新一旦停掉，
        那张表看起来还是一次新测量。"""
        import datetime as dt

        module = self._mod()
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).isoformat()
        self.assertIn("没更新", module.edge_age_note(old))
        self.assertEqual(module.edge_age_note(dt.datetime.now(dt.timezone.utc).isoformat()), "")
        self.assertEqual(module.edge_age_note("not-a-date"), "")

    def test_gate1_reports_the_horizon_of_the_data_it_read(self) -> None:
        """2026-10-13：门槛 1 的回测读 15m/1h/4h/1d，而每日刷新只刷了 **1d**
        （`refresh_daily_bars.py` 的 `--timeframe` 默认值），于是 52 个配置标的的 15m
        全部停在 10-08、1h 有 20 个停在 09-29 —— 那张「样本外」表其实在看 5 天前的世界。
        `generated_at` 挡不住这件事：测量可以是五分钟前跑的。所以输入的日期要一起报。"""
        import datetime as dt
        import tempfile

        module = self._mod()
        # **日期不能写死**：这条断言原来用的是 `through: "2026-10-13"`，写下的头两天它确实
        # 「新鲜」，到 2026-10-16 就变成「3 天前」——断言自己过期了（`max_age_days=1`）。
        # 「新鲜」的定义是**相对今天**的，所以用例也相对今天造。
        yesterday = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
        fresh = module.coverage_note({"15m": {"through": yesterday, "at_through": 52,
                                              "files": 52}})
        self.assertIn("行情地平线", fresh)
        self.assertNotIn("⚠️", fresh)

        stale = module.coverage_note({
            "15m": {"through": "2026-10-08", "at_through": 52, "files": 52},
            "1h": {"through": "2026-10-08", "at_through": 32, "files": 52},
            "1d": {"through": "2026-10-12", "at_through": 52, "files": 52},
        })
        self.assertIn("⚠️", stale)
        self.assertIn("2026-10-08 收盘的世界", stale)
        self.assertIn("32/52", stale)          # 不齐也要说出来，不能只报最新那天
        self.assertEqual(module.coverage_note({}), "")

    def test_coverage_counts_only_the_files_the_backtest_reads(self) -> None:
        """地平线要按**配置里的标的**算，而不是"目录里最新的那个文件" ——
        否则一个没人用的标的就能把整条线装成今天。"""
        import tempfile
        import datetime as dt

        module = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            shutil.copy(CONFIG_DIR / "app.toml", root / "config" / "app.toml")
            hist = root / "data" / "history" / "okx_demo"
            hist.mkdir(parents=True, exist_ok=True)

            def write(symbol: str, day: str, tf: str = "15m") -> None:
                stamp = dt.datetime.fromisoformat(day + "T00:00:00+00:00")
                (hist / f"{symbol}__{tf}.jsonl").write_text(
                    json.dumps({"ts": int(stamp.timestamp() * 1000), "o": 1}) + "\n",
                    encoding="utf-8")

            symbols = module.configured_symbols(root)
            self.assertIn("BTC/USDT", symbols)
            write("BTC-USDT", "2026-10-08")
            write("ETH-USDT", "2026-10-08")
            # 目录里更新、但**不在配置的标的里** → 不该把地平线装成今天
            write("ZZZNOTCONFIGURED-USDT", "2026-10-13")
            cov = module.data_coverage(root)
        self.assertEqual(cov["15m"]["through"], "2026-10-08")
        self.assertEqual(cov["15m"]["files"], 2)   # 只数配置里的标的

    def test_dashboard_shows_the_gates_or_says_why_it_cannot(self) -> None:
        import tempfile

        from ta.dashboard import DashboardData

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            (work / "config").mkdir(parents=True)
            for toml in CONFIG_DIR.glob("*.toml"):
                shutil.copy(toml, work / "config" / toml.name)
            # 还没有跑过 live_readiness.py：要说原因，不能显示成「一条都不满足」
            self.assertFalse(DashboardData(work / "config").readiness()["available"])

            (work / "data" / "readiness").mkdir(parents=True)
            (work / "data" / "readiness" / "readiness.json").write_text(
                json.dumps({"generated_cst": "2026-10-13 15:56", "met": 0, "judgeable": 5,
                            "gates": [{"id": 6, "name": "模拟盘测不到的", "status": "not_auto",
                                       "requirement": "r", "value": "不可自动判", "window": "—"}]},
                           ensure_ascii=False), encoding="utf-8")
            out = DashboardData(work / "config").readiness()
        self.assertTrue(out["available"])
        self.assertEqual(out["judgeable"], 5)
        self.assertEqual(out["gates"][0]["status"], "not_auto")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class JobFailureClassTests(unittest.TestCase):
    """2026-10-14：门槛 4 与门槛 3 得过同一种病 ——
    把**网络抖动**当成"红轮"会让 14 天连击永远不可能满足（门槛 3 实测 8 天里 8 天在抖）。
    分类判据只有一条：**这条失败还会不会再发生**；读不出原因的一律按"自己的缺陷"处理。"""

    def test_known_external_conditions_are_recognised(self) -> None:
        from ta.jobfail import classify_failure
        for text in ("NetworkError: okx GET ...", "HTTP Error 503: Service Temporarily Unavailable",
                     "URLError: <urlopen error timed out>", "Too many requests"):
            rule, why = classify_failure(text)
            self.assertEqual(rule, "external", f"{text} -> {why}")

    def test_our_own_defects_win_over_the_broad_external_markers(self) -> None:
        """「API key is not set」看着像外部，其实是我们的配置问题 —— 先判 own。"""
        from ta.jobfail import classify_failure
        self.assertEqual(classify_failure("API key is not set. Export OKX_DEMO_API_KEY")[0], "own")
        self.assertEqual(classify_failure("changelog_md.py rc=1 attempts=3")[0], "own")
        self.assertEqual(classify_failure("KeyError: 'depth_notional_usd'")[0], "own")

    def test_unknown_is_not_an_exemption(self) -> None:
        from ta.jobfail import classify_failure, classify_run
        self.assertEqual(classify_failure("完全看不懂的一段话")[0], "unknown")
        self.assertEqual(classify_failure("")[0], "unknown")
        # 只要有一条读不出原因，这一轮就不能被豁免
        out = classify_run(["a", "b"], {"a": "NetworkError", "b": "???"})
        self.assertFalse(out["external_only"])
        self.assertEqual((out["external"], out["unknown"]), (1, 1))

    def test_external_only_needs_every_failed_step_to_be_external(self) -> None:
        from ta.jobfail import classify_run
        ok = classify_run(["a", "b"], {"a": "NetworkError", "b": "HTTP Error 503"})
        self.assertTrue(ok["external_only"])
        mixed = classify_run(["a", "b"], {"a": "NetworkError", "b": "rc=1"})
        self.assertFalse(mixed["external_only"])
        self.assertEqual((mixed["external"], mixed["own"]), (1, 1))
        self.assertFalse(classify_run([], {})["external_only"])      # 没红轮就不叫豁免


class Gate4StreakTests(unittest.TestCase):
    """门槛 4 的连击口径：绿轮算、外部条件轮不打断但如实计数、自己的红轮打断。"""

    @staticmethod
    def _today():
        import datetime as _dt
        return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).date()

    def _gate(self, rows: list[dict], tmp: str):
        import scripts.live_readiness as lr
        from pathlib import Path as _P
        root = _P(tmp)
        (root / "data").mkdir(parents=True, exist_ok=True)
        (root / lr.JOB_HISTORY).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        (root / lr.JOB_STATUS).write_text(json.dumps({"generated_at": "t", "needs_rerun": []}),
                                          encoding="utf-8")
        return lr.gate_pipeline(root)

    def test_an_external_only_round_does_not_break_the_streak(self) -> None:
        import datetime as _dt
        import tempfile
        today = self._today()
        days = [today.isoformat(), (today - _dt.timedelta(days=1)).isoformat()]
        rows = [{"date": days[1], "needs_rerun": []},
                {"date": days[1], "needs_rerun": []},
                {"date": days[0], "needs_rerun": []},
                {"date": days[0], "needs_rerun": ["sync_funding.py"], "external_only": True}]
        with tempfile.TemporaryDirectory() as tmp:
            gate = self._gate(rows, tmp)
        self.assertEqual(gate["status"], "unmet")
        # 两天都算满（今天那轮的"外部条件红"不打断）—— 只是离 14 天还远
        self.assertIn("2/", gate["value"])
        self.assertIn("外部条件轮 **1**", gate["detail"])

    def test_our_own_red_round_breaks_it(self) -> None:
        import datetime as _dt
        import tempfile
        today = self._today()
        yesterday = (today - _dt.timedelta(days=1)).isoformat()
        rows = [{"date": yesterday, "needs_rerun": []}, {"date": yesterday, "needs_rerun": []},
                {"date": today.isoformat(), "needs_rerun": ["progress.py"]}]
        with tempfile.TemporaryDirectory() as tmp:
            gate = self._gate(rows, tmp)
        # 今天那轮是**自己的红轮** → 今天不算数，连击只数到昨天（1 天）——
        # 这就是"红轮打断连击"在读数上的样子（它不会把已经过去的天数抹掉）
        self.assertIn("1/", gate["value"])
        self.assertIn("红轮 **1**", gate["detail"])

    def test_an_old_row_without_the_flag_is_not_retroactively_excused(self) -> None:
        """历史行没有 `external_only` 字段 → 按红轮处理（不许追认豁免）。"""
        import tempfile
        rows = [{"date": self._today().isoformat(), "needs_rerun": ["sync_funding.py"]}]
        with tempfile.TemporaryDirectory() as tmp:
            gate = self._gate(rows, tmp)
        self.assertIn("0/", gate["value"])          # 只有今天一轮、而且是红的 → 0 天


class Gate4StepListTests(unittest.TestCase):
    """同一天两轮跑的是**同一个脚本、同样的参数**，所以步骤集合应当一致；
    若有人白天改了步骤表，那天的「两轮全绿」就不是"跑了两遍同样的东西"——
    门槛要**说出来**（只报，不算打断）。2026-10-14 当天正好发生了这件事（加 carry_live.py）。"""

    @staticmethod
    def _today():
        import datetime as _dt
        return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).date()

    def _gate(self, rows: list[dict], tmp: str):
        import scripts.live_readiness as lr
        from pathlib import Path as _P
        root = _P(tmp)
        (root / "data").mkdir(parents=True, exist_ok=True)
        (root / lr.JOB_HISTORY).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        (root / lr.JOB_STATUS).write_text(json.dumps({"generated_at": "t", "needs_rerun": []}),
                                          encoding="utf-8")
        return lr.gate_pipeline(root)

    def test_a_step_added_mid_day_is_reported(self) -> None:
        import tempfile
        today = self._today().isoformat()
        rows = [
            {"date": today, "needs_rerun": [], "steps": {"a": 0, "b": 0}},
            {"date": today, "needs_rerun": [], "steps": {"a": 0, "b": 0, "carry_live.py": 0}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            gate = self._gate(rows, tmp)
        self.assertIn("两轮步骤表不一致", gate["detail"])
        self.assertIn("carry_live.py", gate["detail"])
        self.assertIn("1/", gate["value"])          # 仍然算满一天：它是**报**，不是打断

    def test_matching_step_lists_stay_quiet(self) -> None:
        import tempfile
        today = self._today().isoformat()
        rows = [{"date": today, "needs_rerun": [], "steps": {"a": 0, "b": 0}},
                {"date": today, "needs_rerun": [], "steps": {"a": 0, "b": 0}}]
        with tempfile.TemporaryDirectory() as tmp:
            gate = self._gate(rows, tmp)
        self.assertNotIn("步骤表不一致", gate["detail"])


class UnitMultipleDiffTests(unittest.TestCase):
    """2026-10-14：把「未解释」里那条 **ctVal/张 10 倍假象** 分出来（README 已记录过它）。

    判据要**窄**：整条差异都必须是「两边非零、同号、比值是小整数」——
    混合形状（比如同一行里还有一条 venue=0）仍算未解释，因为那种情况里
    「场馆说没有、我们说有」是**真实的**分歧（2026-10-08 的 carry 乱局就是它）。"""

    def _alert(self, diffs: list[str]) -> dict:
        return {"ts": "2026-10-09T03:47:27+00:00", "title": "reconciliation mismatch",
                "diffs": diffs}

    def test_a_clean_integer_ratio_is_a_units_artifact(self) -> None:
        import scripts.live_readiness as lr
        # ACT/USDT:USDT: 22,912 张 × ctVal 10 = 229,120 币（README 记录过的那条）
        # 要先能定价（"这条差异值不值钱"），才谈得上"这是单位差"：
        # 一笔恰好 2 倍的**灰尘**不算，这是下面那个测试守着的边界。
        kind = lr.classify_position_diff(
            self._alert(["position ACT/USDT:USDT: local=-22912 venue=-229120"]),
            {}, {"ACT/USDT": 0.013})
        self.assertEqual(kind, "unit_multiple")

    def test_venue_zero_stays_unexplained(self) -> None:
        """场馆说 0、我们说有 —— 那不是单位差，是真实的仓位分歧（10-08 的 carry 乱局）。"""
        import scripts.live_readiness as lr
        kind = lr.classify_position_diff(
            self._alert(["position ACT/USDT: local=289693.836 venue=0.0"]), {},
            {"ACT/USDT": 0.013})
        self.assertEqual(kind, "unexplained")

    def test_a_mixed_row_is_not_excused(self) -> None:
        """同一行里既有整数倍、又有 venue=0 → 整条按未解释（不许只挑好看的那半边）。"""
        import scripts.live_readiness as lr
        kind = lr.classify_position_diff(self._alert([
            "position ACT/USDT:USDT: local=-22912 venue=-229120",
            "position BAND/USDT:USDT: local=3755.0 venue=0.0",
        ]), {}, {"ACT/USDT": 0.013, "BAND/USDT": 1.0})
        self.assertEqual(kind, "unexplained")

    def test_opposite_signs_are_not_a_units_artifact(self) -> None:
        import scripts.live_readiness as lr
        kind = lr.classify_position_diff(
            self._alert(["position ACT/USDT:USDT: local=-22912 venue=229120"]), {},
            {"ACT/USDT": 0.013})
        self.assertEqual(kind, "unexplained")

    def test_the_real_alerts_now_split_one_case_out(self) -> None:
        """真账本上跑一遍：那条 10-09 的 ACT 从「未解释」里分出去。"""
        import scripts.live_readiness as lr
        rows = lr.read_jsonl(PROJECT / lr.ALERTS)
        ledger = lr.read_jsonl(PROJECT / lr.LEDGER)
        windows, prices = lr.inventory_fills(ledger), lr.reference_prices(ledger)
        kinds = [lr.classify_position_diff(a, windows, prices) for a in rows
                 if a.get("title") == "reconciliation mismatch" and not lr.is_cash_only(a)]
        self.assertEqual(kinds.count("unit_multiple"), 1)
        self.assertGreaterEqual(kinds.count("unexplained"), 10)


class Gate5WordingTests(unittest.TestCase):
    """2026-10-14：门槛 5 的说明**一度落后于代码**。

    它原来写着「两边的尺子不是同一把：现任按 3 天中位、候选按快照的 ≈17 天中位」——
    那句在 10-13 当晚是真的，但同一天稍后规则改成**候选也用本地费率历史同窗口重算**，
    排名不再读快照。留旧措辞会让人以为规则还在拿两把尺子比 —— 这正是这个项目反复修的
    「说明比行为慢一版」。"""

    def test_the_gate5_text_describes_the_current_rule(self) -> None:
        import scripts.live_readiness as lr
        from pathlib import Path as _P
        root = _P(PROJECT)
        candidate = lr.candidate_window(root, held_names=[])
        self.assertIsNotNone(candidate, "读不到候选就没法核对这段话")
        # 判据：新的措辞必须在、旧的断言必须不在
        source = (PROJECT / "scripts" / "live_readiness.py").read_text(encoding="utf-8")
        self.assertIn("排名**不再**读快照", source)
        self.assertIn("remeasure_candidates", source)
        self.assertNotIn("**两边的尺子不是同一把**", source)
        self.assertEqual(candidate["rule_days"], 3.0)          # 引用的规则窗口要对得上

    def test_candidate_window_still_reports_both_windows(self) -> None:
        """它现在回答的是"快照那个数代表什么"：必须同时给快照窗口与最近实测，不能只给一个。"""
        import scripts.live_readiness as lr
        from pathlib import Path as _P
        candidate = lr.candidate_window(_P(PROJECT), held_names=[])
        for key in ("screen_bps", "window_days", "recent_bps", "recent_days", "depth_ok"):
            self.assertIn(key, candidate)
        self.assertGreater(candidate["window_days"], candidate["recent_days"])


class Gate34WordingTests(unittest.TestCase):
    """2026-10-14：第 3、4 条的「要求」文案比行为狠一版。

    第 3 条写「无**非结构性**对账差异」，但 `classify_position_diff()` 早把仓位差异分成四桶、
    只有「未解释」打断连击 —— 字面读比实际更硬。第 4 条写「连续 14 天**两轮全绿**」，
    而 `day_ok()` 判的是「绿轮 + 已知外部条件轮 ≥ 2 且自己的红轮 = 0」；
    按字面读，14 天里连一次代理抖动都不能有，在这个环境里等于「永远不成立」。

    这批测试钉的是**三处会各自漂移的副本**：脚本给的 `requirement`、报告末尾的口径脚注、
    README §70.6 的原文。看板渲染的是脚本那一份，所以它跟着脚本走。
    """

    def _gate(self, fn: str):
        import scripts.live_readiness as lr
        from pathlib import Path as _P
        return getattr(lr, fn)(_P(PROJECT))

    def test_gate3_requirement_says_unexplained_not_non_structural(self) -> None:
        req = self._gate("gate_execution")["requirement"]
        self.assertIn("未解释", req)
        self.assertNotIn("非结构性", req)          # 那句把已知的三桶也算进了判据
        self.assertIn("order rejected", req)

    def test_gate4_requirement_counts_our_own_red_rounds(self) -> None:
        req = self._gate("gate_pipeline")["requirement"]
        self.assertIn("红轮", req)
        self.assertNotIn("两轮全绿", req)           # 字面读会让门槛永远不成立
        self.assertIn("外部条件", req)

    def test_the_report_footnotes_repeat_the_same_rules(self) -> None:
        import scripts.live_readiness as lr
        from pathlib import Path as _P
        gates = [lr.gate_edge(_P(PROJECT)), lr.gate_sample(_P(PROJECT)),
                 lr.gate_execution(_P(PROJECT)), lr.gate_pipeline(_P(PROJECT)),
                 lr.gate_carry(_P(PROJECT)), lr.gate_venue_only()]
        text = lr.render(gates, {"generated_at": "t", "generated_cst": "t"})
        self.assertIn("只有「未解释」那一桶打断连击", text)
        self.assertIn("绿轮 + 已知外部条件轮", text)
        self.assertNotIn("带 `position` 的一律算非结构性", text)

    def test_readme_section_70_6_carries_the_current_wording(self) -> None:
        readme = (PROJECT / "README.md").read_text(encoding="utf-8")
        # §70.6 的**规范条文**必须改；§70.10 里那张「第一次跑出来的六行」是留档快照，
        # 允许保留原措辞（它上面已经挂了一条指向 §70.10.17 的说明）。
        self.assertNotIn('3. **执行**：连续 30 天无"非结构性"对账差异', readme)
        self.assertNotIn("4. **数据管道**：每日任务连续 14 天两轮全绿", readme)
        self.assertIn("连续 30 天无**未解释**的对账差异", readme)
        self.assertIn("每天 ≥2 轮、且**没有我们自己的红轮**", readme)

    def test_gate1_requirement_quotes_the_bps_it_actually_uses(self) -> None:
        """第 1 条的「要求」不许把成本写死：真值是 config 的费率 × 乘数，读 edge.json 自己的数。

        2026-10-14 之前它硬编码「10 bps/边」。今天配置算出来恰好也是 10.0，所以看不出问题 ——
        但费率一改，**印出来的判据**就会和脚本实际用的成本分道扬镳，这正是门槛 3/4 修的那个病。
        """
        import scripts.live_readiness as lr
        from pathlib import Path as _P
        payload = lr.read_json(_P(PROJECT) / lr.EDGE)
        self.assertIsNotNone(payload, "读不到 edge.json 就没法核对这句话")
        bps = payload["implied_fee_bps_per_side"]
        req = lr.gate_edge(_P(PROJECT))["requirement"]
        self.assertIn(f"{bps:g} bps/边", req)
        # 反向：它必须能跟着 payload 变，而不是恰好等于硬编码的 10
        tweaked = dict(payload, implied_fee_bps_per_side=12.5)
        self.assertIn("12.5 bps/边", lr.edge_requirement(tweaked))
        self.assertNotIn("10 bps/边", lr.edge_requirement(tweaked))

    def test_gate1_requirement_still_reads_well_without_a_measurement(self) -> None:
        """没有 edge.json 时不能说「10 bps/边」（那是没经过测量的断言），要说清它是怎么折算的。"""
        import scripts.live_readiness as lr
        req = lr.edge_requirement(None)
        self.assertIn("fee-multiplier", req)
        self.assertNotIn("10 bps", req)
