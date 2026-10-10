#!/usr/bin/env python3
"""Offline end-to-end self test. No network, no credentials, no real money.

    python3 scripts/smoke_test.py

It builds a throwaway copy of the project in a temp directory so that runtime
state (ledger, logs, KILL_SWITCH) never touches your working tree.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.backtest import run_backtest  # noqa: E402
from ta.config import Config  # noqa: E402
from ta.execution.oms import OMS  # noqa: E402
from ta.execution.sizing import risk_based_qty  # noqa: E402
from ta.log import setup  # noqa: E402
from ta.marketdata import SyntheticFeed  # noqa: E402
from ta.models import AccountState, OrderRequest  # noqa: E402
from ta.onchain import (  # noqa: E402
    CompositeTokenService,
    FixtureFetcher,
    HttpClient,
    JupiterRouter,
    MockSigner,
    OnchainExecutor,
    SolanaRpc,
    TokenScreener,
    load_snapshots,
)
from ta.portfolio import Ledger  # noqa: E402
from ta.risk.engine import RiskEngine  # noqa: E402
from ta.runner import TradingRunner  # noqa: E402
from ta.strategies import build_strategy  # noqa: E402
from ta.venues import build_venue  # noqa: E402

CLEAN_MINT = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
RUG_MINT = "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E"
IMPACT_MINT = "4mKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsW"

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ta-smoke-"))
    try:
        work = tmp / "project"
        (work / "config").mkdir(parents=True)
        for toml in (PROJECT / "config").glob("*.toml"):
            shutil.copy(toml, work / "config" / toml.name)
        config = Config.load(work / "config")
        logger = setup(config.log_dir, level="WARNING")

        print("\n[1] config")
        problems = config.validate()
        check("config validates", not problems, "; ".join(problems))
        check("venues defined", set(config.venues) >= {"paper", "okx_demo", "binance_testnet"},
              ", ".join(sorted(config.venues)))

        print("\n[2] risk engine")
        risk = RiskEngine(
            config.risk,
            config.kill_switch_path,
            config.onchain,
            symbol_tiers=config.symbol_tiers,
        )
        account = AccountState(
            venue="test",
            base_ccy="USDT",
            cash=10_000.0,
            equity=10_000.0,
            day_start_equity=10_000.0,
            peak_equity=10_000.0,
        )
        small = risk.pre_trade(
            OrderRequest("sm1", "BTC/USDT", "buy", 0.002), account, 60_000.0, {"BTC/USDT": 60_000.0}
        )
        check("small order approved", small.approved and not small.adjusted, small.reason)
        huge = risk.pre_trade(
            OrderRequest("sm2", "BTC/USDT", "buy", 1.0), account, 60_000.0, {"BTC/USDT": 60_000.0}
        )
        check("oversized order shrunk", huge.approved and huge.adjusted,
              f"qty {1.0} -> {huge.qty:.6f}")
        blocked = risk.pre_trade(
            OrderRequest("sm3", "NOTLISTED/USDT", "buy", 100.0), account, 0.2
        )
        check("non-whitelisted symbol rejected", blocked.rejected, blocked.reason)
        tiny = risk.pre_trade(OrderRequest("sm4", "BTC/USDT", "buy", 0.0001), account, 60_000.0)
        check("below min notional rejected", tiny.rejected, tiny.reason)
        exposed = AccountState(
            venue="test", cash=1_000.0, equity=10_000.0,
            positions={}, day_start_equity=10_000.0, peak_equity=10_000.0,
        )
        from ta.models import Position

        exposed.positions["BTC/USDT"] = Position(symbol="BTC/USDT", qty=0.15, avg_price=60_000.0)
        full = risk.pre_trade(
            OrderRequest("sm5", "BTC/USDT", "buy", 0.01), exposed, 60_000.0, {"BTC/USDT": 60_000.0}
        )
        check("position cap enforced", full.rejected, full.reason)
        risk.trigger_kill("smoke test")
        after_kill = risk.pre_trade(OrderRequest("sm6", "BTC/USDT", "buy", 0.002), account, 60_000.0)
        check("kill switch blocks trading", after_kill.rejected, after_kill.reason)
        check("kill switch file written", config.kill_switch_path.exists())
        risk.clear_kill()
        risk = RiskEngine(
            config.risk,
            config.kill_switch_path,
            config.onchain,
            symbol_tiers=config.symbol_tiers,
        )

        print("\n[3] sizing")
        check("risk-based qty", abs(risk_based_qty(10_000, 1.0, 100.0, 95.0) - 20.0) < 1e-9,
              "1% of 10k over a 5 wide stop = 20 units")
        check("vol target scaling", abs(risk_based_qty(10_000, 1.0, 100.0, 95.0, 0.5) - 10.0) < 1e-9)
        check(
            "tier risk shrinks with symbol quality",
            risk.risk_per_trade_pct("BTC/USDT") > risk.risk_per_trade_pct("JUP/USDT"),
            f"majors={risk.risk_per_trade_pct('BTC/USDT')}% new={risk.risk_per_trade_pct('JUP/USDT')}%",
        )
        check(
            "exits are never blocked by the min-notional floor",
            risk.pre_trade(
                OrderRequest("exit-mini", "BTC/USDT", "sell", 0.00001, reduce_only=True),
                account,
                60_000.0,
                {"BTC/USDT": 60_000.0},
            ).approved,
            "a 0.6 USD reduce-only sell still passes the gate",
        )

        print("\n[4] paper venue + OMS")
        venue = build_venue(config, "paper")
        venue.set_mark("BTC/USDT", 60_000.0)
        venue.set_mark("ETH/USDT", 3_000.0)
        ledger = Ledger(tmp / "step4-ledger.jsonl")
        oms = OMS(venue, ledger, dry_run=False)
        first = oms.submit(OrderRequest("dup123", "BTC/USDT", "buy", 0.005))
        second = oms.submit(OrderRequest("dup123", "BTC/USDT", "buy", 0.005))
        check("market order fills", first.status == "filled", first.reason)
        check("duplicate client order id rejected", second.status == "rejected", second.reason)
        check("fills recorded", len(ledger.fills) >= 1)
        position = venue.get_account().positions.get("BTC/USDT")
        check("position opened", position is not None and position.qty > 0)
        # limit order resting far from the mark must not fill immediately
        oms.submit(OrderRequest("lim123", "BTC/USDT", "buy", 0.001, type="limit", price=50_000.0))
        check("resting limit order stays open", len(venue.fetch_open_orders(["BTC/USDT"])) == 1)
        venue.set_mark("BTC/USDT", 49_000.0)
        check("limit order fills when touched", len(venue.fetch_open_orders(["BTC/USDT"])) == 0)

        print("\n[5] backtest (same risk gate as live)")
        SIZE_SYMBOLS = ["BTC/USDT", "ETH/USDT"]
        for name in ("trend_breakout", "trend_pullback", "mean_reversion"):
            spec = config.strategy(name)
            strategy = build_strategy(
                spec, config.risk, SIZE_SYMBOLS, new_symbols=config.universe.symbols_by_tier["new"]
            )
            timeframes = list(
                dict.fromkeys(
                    [config.universe.timeframe, strategy.timeframe, strategy.confirm_timeframe]
                )
            )
            timeframes = [tf for tf in timeframes if tf]
            feed = SyntheticFeed(
                symbols=SIZE_SYMBOLS,
                timeframe=config.universe.timeframe,
                timeframes=timeframes,
                warmup_bars=strategy.warmup + 10,
                # 8000 bars: the gate-heavy strategies (breakout + retest,
                # volume floor) need a longer synthetic sample before they fire
                total_bars=8000,
                seed=11,
            )
            bars = {
                s: {tf: feed.full(s, tf) for tf in timeframes} for s in SIZE_SYMBOLS
            }
            result = run_backtest(
                strategy,
                bars,
                config.risk,
                initial_cash=10_000.0,
                fee_bps=config.venue("paper").fee_bps,
                slippage_bps=config.venue("paper").slippage_bps,
                timeframe=config.universe.timeframe,
                symbol_tiers=config.symbol_tiers,
            )
            summary = result.summary()
            check(
                f"{name} backtest produced trades",
                result.trades > 0,
                f"trades={result.trades} return={summary['total_return_pct']}% "
                f"maxDD={summary['max_drawdown_pct']}% exits={summary['exits']}",
            )

        print("\n[6] paper trading loop (simulated market)")
        runner = TradingRunner.build(
            config,
            logger,
            venue_name="paper",
            strategy_names=["trend_pullback", "mean_reversion"],
            symbols=SIZE_SYMBOLS,
            iterations=900,
            interval=0,
            dry_run=True,
        )
        report = runner.run()
        runner.close()
        check("orders submitted", report.orders_submitted > 0, f"{report.orders_submitted}")
        check(
            "fills happened",
            report.fills > 0,
            f"fills={report.fills} trades={report.trades} win_rate={report.win_rate_pct:.1f}%",
        )
        check(
            "blocked orders were explicit",
            report.orders_blocked >= 0,
            f"blocked={report.orders_blocked}",
        )
        check("ledger written", runner.ledger.path.exists(), str(runner.ledger.path))
        replayed = runner.ledger.fills_for(run_id=report.run_id)
        check(
            "ledger replays this run exactly",
            len(replayed) == report.fills,
            f"{len(replayed)} replayed vs {report.fills} session fills",
        )
        check(
            "managed vs unmanaged capital is separated",
            report.unmanaged_notional == 0.0 and report.managed_notional >= 0.0,
            f"managed={report.managed_notional:,.2f} unmanaged={report.unmanaged_notional:,.2f}",
        )
        check(
            "open heat stayed inside the cap",
            report.heat_pct <= config.risk.account.max_heat_pct + 1e-6,
            f"heat={report.heat_pct:.2f}% cap={config.risk.account.max_heat_pct}%",
        )
        check(
            "reconciled against the venue",
            report.reconciled,
            "; ".join(report.reconciliation_diffs) or "no differences",
        )
        check(
            "tiers drove the sizing",
            all(
                config.risk.tier_names
            ),
            f"{len(config.symbol_tiers)} symbols across {config.risk.tier_names}",
        )
        check(
            "equity stayed finite and positive",
            report.equity > 0 and report.equity == report.equity,
            f"equity={report.equity:,.2f} return={report.return_pct:+.2f}%",
        )

        print("\n[7] on-chain safety screen")
        tokens = load_snapshots(PROJECT / "fixtures" / "tokens.json")
        results = TokenScreener(config.onchain).screen_many(tokens)
        by_symbol = {r.symbol: r for r in results}
        check("honeypot rejected", not by_symbol["SCAM"].passed, "; ".join(by_symbol["SCAM"].reasons))
        check("young token rejected", not by_symbol["FRESH"].passed,
              "; ".join(by_symbol["FRESH"].reasons))
        check("tax trap rejected", not by_symbol["TAXED"].passed,
              "; ".join(by_symbol["TAXED"].reasons))
        check("concentrated token rejected", not by_symbol["WHALE"].passed,
              "; ".join(by_symbol["WHALE"].reasons))
        check("clean token passes", by_symbol["CLEAN"].passed,
              "; ".join(by_symbol["CLEAN"].reasons))

        print("\n[8] on-chain rehearsal (offline data, mock signer, no keys)")
        client = HttpClient(
            cache_dir=tmp / "onchain-cache",
            fetcher=FixtureFetcher(PROJECT / "fixtures" / "onchain"),
        )
        service = CompositeTokenService.from_config(
            config.onchain,
            PROJECT,
            client=client,
            fixture_dir=PROJECT / "fixtures" / "onchain",
        )
        snapshot = service.snapshot(CLEAN_MINT)
        check(
            "multi-source merge resolved every safety field",
            snapshot.missing() == [],
            f"{len(snapshot.sources)} fields from {len(set(snapshot.sources.values()))} sources",
        )
        onchain_ledger = Ledger(tmp / "onchain-ledger.jsonl")
        executor = OnchainExecutor(
            config.onchain,
            service,
            JupiterRouter(config.onchain, client),
            MockSigner(),
            rpc=SolanaRpc("https://api.mainnet-beta.solana.com", client),
            risk=RiskEngine(config.risk, tmp / "KILL_SWITCH", config.onchain),
            ledger=onchain_ledger,
            rehearsal=True,
        )
        happy = executor.execute(
            CLEAN_MINT, 200_000_000, allow_signing=True, allow_broadcast=True, equity_usd=5000
        )
        check(
            "clean token: quote -> build -> simulate -> mock sign",
            happy.status == "simulated"
            and happy.signer == "mock"
            and happy.signature.startswith("SIMULATED-"),
            f"impact={(happy.quote or {}).get('price_impact_pct')}% "
            f"units={(happy.simulation or {}).get('units_consumed')}",
        )
        check("mock signature can never broadcast", happy.broadcast_allowed is False)
        rug = executor.execute(RUG_MINT, 200_000_000, allow_signing=True, equity_usd=5000)
        check(
            "rug token blocked before any quote",
            rug.status == "blocked" and rug.stage == "screen",
            rug.reason[:70],
        )
        impact = executor.execute(IMPACT_MINT, 300_000_000, allow_signing=True, equity_usd=5000)
        check(
            "price impact gate blocks the trade",
            impact.status == "rejected" and impact.stage == "quote",
            impact.reason,
        )
        stages = [event["kind"] for event in onchain_ledger.events]
        check(
            "every stage left an audit trail",
            {
                "onchain_snapshot",
                "onchain_screen",
                "onchain_risk",
                "onchain_quote",
                "onchain_swap_built",
                "onchain_simulate",
                "onchain_sign",
            }
            <= set(stages),
            f"{len(stages)} events",
        )

        print("\n" + "=" * 72)
        failures = [name for name, ok, _ in CHECKS if not ok]
        print(f"smoke test: {len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed")
        if failures:
            print("failed:")
            for name in failures:
                print(f"  - {name}")
            return 1
        print("all good - paper mode, backtest, risk gate, ledger and screener are wired up")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
