"""Command line interface.

    python -m ta doctor
    python -m ta run --venue paper --strategy trend_breakout --iterations 500 --interval 0
    python -m ta run --venue okx_demo --strategy trend_breakout --enable-trading
    python -m ta backtest --strategy mean_reversion --bars 2000 --stress
    python -m ta screen --file fixtures/tokens.json
    python -m ta reconcile --venue okx_demo
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import time
from typing import Any
from pathlib import Path

from . import __version__
from .backtest import run_backtest
from .config import Config
from .log import setup
from .marketdata import SyntheticFeed
from .monitoring import Alerts, MetricsServer, format_header
from .onchain import (
    CompositeTokenService,
    JupiterRouter,
    OnchainExecutor,
    SolanaRpc,
    TokenScreener,
    build_signer,
    load_snapshots,
)
from .onchain.rpc import PUBLIC_RPC
from .portfolio import Ledger
from .risk.engine import RiskEngine
from .runner import TradingRunner
from .strategies import available, build_strategy
from .venues import credential_status
from .venues.base import VenueError


def _common() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config-dir", default="config", help="directory holding the *.toml files")
    parser.add_argument("--log-level", default="INFO", help="DEBUG|INFO|WARNING|ERROR")
    return parser


def _add_mint_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--mint", required=True, help="token mint address")
    parser.add_argument(
        "--offline", action="store_true", help="use recorded fixtures, never touch the network"
    )
    parser.add_argument("--fixture-dir", default="", help="override onchain.data.fixture_dir")
    return parser


def _add_sizing_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--amount",
        type=float,
        default=0.2,
        help="input amount in SOL (must clear risk.per_trade.min_order_notional_usd)",
    )
    parser.add_argument("--sol-price", type=float, default=0.0, help="override sol_price_usd")
    parser.add_argument("--equity-usd", type=float, default=0.0, help="reference equity for the risk gate")
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="ta",
        description="Spot + on-chain, low/mid frequency trading agent skeleton",
    )
    parser.add_argument("--version", action="version", version=f"trading-agent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", parents=[common], help="run the trading loop")
    run.add_argument("--venue", default="paper", help="paper | okx_demo | binance_testnet")
    run.add_argument("--strategy", action="append", default=None, help="repeatable")
    run.add_argument("--symbols", default="", help="comma separated, e.g. BTC/USDT,ETH/USDT")
    run.add_argument("--iterations", type=int, default=0, help="0 = run forever")
    run.add_argument("--interval", type=float, default=None, help="seconds between cycles")
    run.add_argument("--metrics-port", type=int, default=0, help="0 = do not expose /metrics")
    run.add_argument(
        "--enable-trading",
        action="store_true",
        help="required for a non-paper venue to actually send orders",
    )
    run.add_argument(
        "--env-file",
        default="",
        help=(
            "load KEY=VALUE lines before building the venue (e.g. .env). Lets a "
            "launchd unit start the loop without putting API keys in the plist."
        ),
    )
    run.add_argument(
        "--pid-file",
        default="",
        help="write this process's pid here on start and remove it on exit",
    )

    backtest = sub.add_parser("backtest", parents=[common], help="bar-replay backtest")
    backtest.add_argument("--strategy", default="trend_breakout")
    backtest.add_argument("--symbols", default="")
    backtest.add_argument("--bars", type=int, default=1500)
    backtest.add_argument(
        "--timeframe",
        default="",
        help=(
            "override the strategy's declared cadence (research only). The runner "
            "never does this; a backtest does, so a signal can be probed where the "
            "history actually reaches. The strategy's confirm frame stays as "
            "configured."
        ),
    )
    backtest.add_argument("--seed", type=int, default=7)
    backtest.add_argument("--fee-multiplier", type=float, default=1.0)
    backtest.add_argument("--stress", action="store_true", help="also run at 3x trading costs")
    backtest.add_argument(
        "--source",
        default="synthetic",
        choices=("synthetic", "history"),
        help="synthetic = random walk (mechanics only); history = real candles from ta data fetch",
    )
    backtest.add_argument("--venue", default="okx_demo", help="which history directory to read")
    backtest.add_argument(
        "--split",
        type=float,
        default=0.0,
        help="walk-forward split, e.g. 0.7 = first 70%% in-sample, last 30%% out-of-sample",
    )

    screen = sub.add_parser("screen", parents=[common], help="screen on-chain token snapshots")
    screen.add_argument("--file", default="fixtures/tokens.json")

    reconcile = sub.add_parser("reconcile", parents=[common], help="rebuild the local book from the ledger")
    reconcile.add_argument("--venue", default="paper")
    reconcile.add_argument(
        "--reset-baseline",
        action="store_true",
        help="freeze the current venue cash/positions as the reconciliation baseline",
    )

    sub.add_parser("doctor", parents=[common], help="environment / config / credential check")
    dash = sub.add_parser(
        "dashboard", parents=[common], help="local read-only operations dashboard (web UI)"
    )
    dash.add_argument("--host", default="127.0.0.1", help="bind address (loopback only by default)")
    dash.add_argument("--port", type=int, default=9200)
    dash.add_argument("--venue", default="okx_demo", help="which ledger.<venue>.jsonl to read")
    dash.add_argument("--metrics-port", type=int, default=9101)
    dash.add_argument(
        "--env-file",
        default="",
        help="load KEY=VALUE lines first (e.g. .env, for TA_ALERT_WEBHOOK_URL)",
    )

    # ---- on-chain ---------------------------------------------------------
    onchain = sub.add_parser("onchain", help="on-chain screening / quoting / gated execution")
    oc = onchain.add_subparsers(dest="onchain_command", required=True)
    _add_mint_args(
        oc.add_parser("screen", parents=[common], help="merge sources + run the safety filter")
    )
    _add_sizing_args(
        _add_mint_args(
            oc.add_parser("quote", parents=[common], help="screen + risk + quote + simulate")
        )
    )
    execute = _add_sizing_args(
        _add_mint_args(oc.add_parser("execute", parents=[common], help="full flow including signing"))
    )
    execute.add_argument("--sign", action="store_true", help="produce a signature (default: plan only)")
    execute.add_argument("--broadcast", action="store_true", help="broadcast; needs --i-understand-the-risk")
    execute.add_argument(
        "--i-understand-the-risk",
        dest="i_understand",
        action="store_true",
        help="explicit consent #3 required to broadcast a real signed transaction",
    )
    execute.add_argument("--keypair", default="", help="solana-keygen JSON path for signer=local-keypair")

    # ---- symbols -----------------------------------------------------------
    symbols_cmd = sub.add_parser("symbols", help="validate or discover the traded universe")
    sc = symbols_cmd.add_subparsers(dest="symbols_command", required=True)
    check = sc.add_parser("check", parents=[common], help="check every configured symbol on the venue")
    check.add_argument("--venue", default="okx_demo", help="which venue section to validate against")
    scan = sc.add_parser("scan", parents=[common], help="discover liquid symbols and print a universe block")
    scan.add_argument("--venue", default="okx_demo")
    scan.add_argument("--quote", default="USDT")
    scan.add_argument("--top", type=int, default=40)
    scan.add_argument("--min-volume", type=float, default=5_000_000.0, help="24h quote volume floor")
    scan.add_argument("--new-days", type=int, default=180, help="listed more recently than this = tier new")
    scan.add_argument("--write", default="", help="optional path to write the block to")

    # ---- data --------------------------------------------------------------
    data_cmd = sub.add_parser("data", help="fetch and inspect local OHLCV history")
    dc = data_cmd.add_subparsers(dest="data_command", required=True)
    fetch = dc.add_parser("fetch", parents=[common], help="download real candles to data/history")
    fetch.add_argument("--venue", default="okx_demo")
    fetch.add_argument("--symbols", default="", help="comma separated; default = whole universe")
    fetch.add_argument("--timeframes", default="15m,1h,4h")
    fetch.add_argument(
        "--source",
        choices=("demo", "live"),
        default="demo",
        help=(
            "which catalogue to read. demo = the simulated-trading catalogue the "
            "runner trades against; live = the real one. They are NOT the same "
            "set: measured 2026-10-08, 21 of 427 symbols answer with real candles "
            "only on live, and 18 more exist in the demo instrument list but have "
            "no candles anywhere. A missing demo series answers `code:0, data:[]` "
            "rather than an error, so it reads as 'no data' instead of 'wrong "
            "catalogue' - live series are therefore written to their own "
            "directory rather than merged into the demo one."
        ),
    )
    fetch.add_argument("--bars", type=int, default=2880, help="per timeframe, per symbol")
    fetch.add_argument("--pause", type=float, default=0.05, help="seconds between paged requests")
    fetch.add_argument("--retries", type=int, default=3, help="attempts per symbol/timeframe")
    fetch.add_argument("--retry-pause", type=float, default=2.0, help="seconds between attempts")
    listing = dc.add_parser("list", parents=[common], help="what is already on disk")
    listing.add_argument("--venue", default="okx_demo")
    coverage = dc.add_parser(
        "coverage",
        parents=[common],
        help="per class: listed by the venue vs actually on disk (demo / live-only / none)",
    )
    coverage.add_argument("--venue", default="okx_demo")
    coverage.add_argument("--timeframes", default="1d,4h,1h")

    # ---- scan: on-chain watch + event study ---------------------------------
    scan_cmd = sub.add_parser("scan", help="watch token snapshots over time and test them")
    sc = scan_cmd.add_subparsers(dest="scan_command", required=True)
    watch = sc.add_parser("watch", parents=[common], help="periodically snapshot tokens to disk")
    watch.add_argument("--mints", default="", help="comma separated mints")
    watch.add_argument("--mints-from", default="", help="watchlist json produced by 'scan discover'")
    watch.add_argument("--interval", type=float, default=300.0, help="seconds between scans")
    watch.add_argument("--iterations", type=int, default=0, help="0 = run until stopped")
    watch.add_argument("--offline", action="store_true", help="use recorded fixtures (no network)")
    watch.add_argument("--store", default="data/onchain/snapshots.jsonl")
    watch.add_argument(
        "--reload-every",
        type=int,
        default=0,
        help=(
            "with --mints-from, re-read the candidate list every N rounds "
            "(0 = never). A list loaded once goes stale within hours."
        ),
    )
    watch.add_argument(
        "--discover-every",
        type=int,
        default=0,
        help=(
            "with --mints-from, re-run discovery into that file every N rounds "
            "(0 = never). Keeps a long-running scanner on tokens that are trading "
            "now instead of the ones that were trading when it started."
        ),
    )
    watch.add_argument("--discover-limit", type=int, default=25)
    watch.add_argument("--min-liquidity", type=float, default=0.0, help="0 = config floor")
    evaluate = sc.add_parser("evaluate", parents=[common], help="event study over the store")
    evaluate.add_argument("--store", default="data/onchain/snapshots.jsonl")
    evaluate.add_argument("--horizons", default="5,60,1440", help="minutes after the signal")
    evaluate.add_argument(
        "--dedupe-seconds",
        type=int,
        default=60,
        help="collapse duplicate writes into one observation per N seconds (0 = off)",
    )
    evaluate.add_argument(
        "--all-mints",
        action="store_true",
        help="study every mint ever written; default only studies the current watchlist",
    )
    evaluate.add_argument("--watchlist", default="data/onchain/watchlist.json")
    evaluate.add_argument(
        "--recent-hours",
        type=float,
        default=24.0,
        help=(
            "also study tokens seen in the store within this many hours, not only "
            "the ones in the current watchlist (0 = current watchlist only). The "
            "promoted feed only ever carries ~12-17 live tokens, which cannot fill "
            "20-distinct-token buckets, so scoping to it alone caps the study."
        ),
    )
    evaluate.add_argument(
        "--bucket-by",
        default="score",
        choices=("score", "liquidity", "turnover", "boost", "age", "txns_m5"),
        help="which dimension to test for forward-return differences",
    )
    evaluate.add_argument(
        "--min-txns-m5",
        type=int,
        default=0,
        help=(
            "drop observations whose token traded fewer than N times in the last 5 "
            "minutes (0 = off). Rows written before the activity fields existed are "
            "reported separately, never silently mixed in."
        ),
    )
    discover = sc.add_parser("discover", parents=[common], help="build the candidate list from public feeds")
    discover.add_argument("--limit", type=int, default=25)
    discover.add_argument("--min-liquidity", type=float, default=0.0, help="0 = use onchain.safety floor")
    discover.add_argument("--write", default="data/onchain/watchlist.json")
    discover.add_argument("--no-enrich", action="store_true", help="skip per-token market data")
    return parser


# --------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------


def load_env_file(path: Path) -> int:
    """Load `KEY=VALUE` lines into os.environ. Returns how many were set.

    Deliberately tiny and setdefault-free: an explicit `--env-file .env` is how a
    launchd unit gets credentials without those credentials being copied into
    ~/Library/LaunchAgents/*.plist. Real environment variables win, so a caller
    can still override any single value.
    """
    import os as _os

    if not path.exists():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in _os.environ:
            _os.environ[key] = value
            loaded += 1
    return loaded


def cmd_run(args: argparse.Namespace) -> int:
    # Secrets stay in the project's own .env (mode 600) instead of being copied
    # into a launchd plist under ~/Library/LaunchAgents, which is a far more
    # exposed location. Load before anything reads a credential.
    if args.env_file:
        env_path = Path(args.env_file)
        if not env_path.is_absolute():
            env_path = Path(args.config_dir).resolve().parent / env_path
        loaded = load_env_file(env_path)
        if loaded == 0:
            print(f"!! --env-file {env_path} produced no variables")

    # A service-managed loop has no shell to record its pid in start_demo.sh, so
    # the loop writes it itself. Removed on exit so a stale file cannot make the
    # watchdog believe a dead loop is alive.
    pid_path: Path | None = None
    if args.pid_file:
        pid_path = Path(args.pid_file)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    config = Config.load(args.config_dir)
    problems = config.validate()
    if problems:
        print("configuration problems:")
        for problem in problems:
            print(f"  - {problem}")
        print("fix config/*.toml before running")
        return 2

    logger = setup(config.log_dir, level=args.log_level)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    dry_run = True
    if args.venue == "paper":
        dry_run = False  # orders go to the local simulator, which is the point
    elif args.enable_trading:
        dry_run = False
        print(f"!! order submission ENABLED for {args.venue}")
        if config.app.mode == "live":
            print("!! app.mode=live - verify the endpoint and key permissions first")
    else:
        print(f"!! {args.venue}: dry-run (orders are logged, not sent). Use --enable-trading to submit.")

    try:
        runner = TradingRunner.build(
            config,
            logger,
            venue_name=args.venue,
            strategy_names=args.strategy,
            symbols=symbols or None,
            iterations=args.iterations,
            interval=args.interval,
            dry_run=dry_run,
        )
    except VenueError as exc:
        print(f"\nvenue not ready: {exc}")
        return 2
    except KeyError as exc:
        print(f"\n{exc}")
        return 2

    metrics_server = None
    if args.metrics_port:
        metrics_server = MetricsServer(runner.metrics, port=args.metrics_port)
        metrics_server.start()
        print(f"metrics: {metrics_server.url()}")

    print(format_header(runner.venue.name, runner.dry_run))
    try:
        report = runner.run()
    finally:
        if metrics_server:
            metrics_server.stop()
        runner.close()

    print("\nrun report")
    for line in report.lines():
        print(f"  {line}")
    blocked = runner.ledger.stats()["blocked_by_reason"]
    if blocked:
        print("\n  blocked orders by reason:")
        for reason, count in sorted(blocked.items(), key=lambda kv: -kv[1]):
            print(f"    {reason:<28} {count}")
    print(f"\n  ledger           {runner.ledger.path}")
    print(f"  audit log        {config.log_dir / 'ta.jsonl'}")
    if pid_path is not None:
        pid_path.unlink(missing_ok=True)
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    config = Config.load(args.config_dir)
    spec = config.strategy(args.strategy)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or (
        config.resolve_symbols(spec)
    )
    paper = config.venue("paper")
    strategy = build_strategy(
        spec,
        config.risk,
        symbols,
        costs={"fee_bps": paper.fee_bps, "slippage_bps": paper.slippage_bps},
        new_symbols=config.universe.symbols_by_tier.get("new", []),
        timeframe=(args.timeframe or None),
    )
    if args.timeframe and args.timeframe != spec.timeframe:
        print(
            f"!! cadence override: {spec.name} declares {spec.timeframe}, "
            f"running at {args.timeframe} (research only; the live config is unchanged)"
        )
    # The strategy's own timeframe is the clock for a backtest, not the universe's.
    # Taking `universe.timeframe` here silently ran every backtest on 15m and only
    # *loaded* the strategy's declared frame as reference data - which is how a
    # strategy declaring 1h produced a 15m result, and how the first perp backtest
    # (README 22.2) ended up measuring the wrong cadence. Falls back to the universe
    # frame only when a strategy does not declare one.
    timeframe = strategy.timeframe or config.universe.timeframe
    timeframes = [timeframe]
    for candidate in (strategy.timeframe, strategy.confirm_timeframe):
        if candidate and candidate not in timeframes:
            timeframes.append(candidate)

    # Funding history for the perp leg (README 24.3). Without this the swap
    # strategy's funding gate silently stays open and the backtest measures the
    # trend skeleton only - the hole README 22 recorded. Loaded from the same
    # history tree the bars come from; absent files simply leave the gate open.
    funding: dict[str, object] = {}
    if args.source == "history":
        from .funding import load_funding_dir

        raw = load_funding_dir(config.data_dir / "history" / "funding")
        by_symbol = {}
        for symbol in symbols:
            base = symbol.replace("/", "-")          # SOL/USDT -> SOL-USDT
            for key, series in raw.items():
                if key.startswith(base):
                    by_symbol[symbol] = series
                    break
        funding = by_symbol

    if args.source == "history":
        from .history import load_frames

        bars_by_symbol = load_frames(
            config.data_dir / "history", args.venue, symbols, timeframes
        )
        if not bars_by_symbol:
            print(f"no local history for {args.venue} under {config.data_dir / 'history'}")
            print(f"run: ta data fetch --venue {args.venue} --timeframes {','.join(timeframes)}")
            return 2
        symbols = sorted(bars_by_symbol)
        print(
            f"source: real candles from disk ({len(symbols)} symbols): "
            + ", ".join(symbols[:8])
            + (" ..." if len(symbols) > 8 else "")
        )
    else:
        feed = SyntheticFeed(
            symbols=symbols,
            timeframe=timeframe,
            timeframes=timeframes,
            warmup_bars=strategy.warmup + 10,
            total_bars=max(args.bars, strategy.warmup + 50),
            seed=args.seed,
        )
        bars_by_symbol = {
            symbol: {tf: feed.full(symbol, tf) for tf in timeframes} for symbol in symbols
        }

    def render(result) -> None:
        summary = result.summary()
        print(
            f"\nbacktest {summary['strategy']}  symbols={summary['symbols']}"
            f"  bars={summary['bars']}  timeframe={summary['timeframe']}"
        )
        for key, value in summary.items():
            if key in ("strategy", "symbols", "bars", "timeframe"):
                continue
            print(f"  {key:<20} {value}")

    def run(frames, *, entry_after_ts: int = 0, fee_multiplier: float = 1.0):
        return run_backtest(
            strategy,
            frames,
            config.risk,
            initial_cash=paper.initial_cash,
            fee_bps=paper.fee_bps,
            slippage_bps=paper.slippage_bps,
            timeframe=timeframe,
            fee_multiplier=fee_multiplier,
            symbol_tiers=config.symbol_tiers,
            entry_after_ts=entry_after_ts,
            funding=funding,
        )

    # ---- walk-forward: in-sample for shape, out-of-sample for the verdict ----
    if args.split and 0.0 < args.split < 1.0:
        base_bars = bars_by_symbol[symbols[0]][timeframe]
        cut_index = max(1, min(len(base_bars) - 2, int(len(base_bars) * args.split)))
        cut_ts = base_bars[cut_index].ts
        in_sample = {
            symbol: {
                tf: [bar for bar in frames if bar.ts < cut_ts] for tf, frames in per_symbol.items()
            }
            for symbol, per_symbol in bars_by_symbol.items()
        }
        # `cut_index` is derived from symbols[0]'s bar count, so a symbol listed
        # more recently has NOTHING before the cut. One such symbol makes the
        # replay's "timestamps every symbol shares" intersection collapse - measured
        # 2026-10-07 with the daily universe: 15 symbols, 11 shared bars, and a
        # ValueError instead of a report. Drop the stragglers from the in-sample
        # slice only; the out-of-sample run still sees every symbol, and the
        # in-sample number is explicitly a shape-only read anyway.
        warmup_min = 20
        in_sample = {
            symbol: per_symbol
            for symbol, per_symbol in in_sample.items()
            if len(per_symbol.get(timeframe, [])) >= warmup_min
        }
        print(f"\nwalk-forward split at ts={cut_ts}")
        if len(in_sample) < 2:
            print(
                f"  -- in-sample skipped: only {len(in_sample)} symbol(s) have "
                f">= {warmup_min} {timeframe} bars before the cut"
            )
        else:
            print(f"  in-sample symbols: {len(in_sample)} of {len(bars_by_symbol)}")
            print("  --- in-sample (shape only; this is where tuning would happen)")
            render(run(in_sample, fee_multiplier=args.fee_multiplier))
        print("\n  --- out-of-sample (the only number that means anything)")
        # `args.fee_multiplier` used to be dropped on this path entirely: the
        # split branch called run() with its defaults, so `--fee-multiplier 0`
        # produced byte-identical output to the default and the cost assumption
        # was silently ignored. The stress line below is deliberately ABSOLUTE
        # (3x the configured fee), not 3x the multiplier.
        oos = run(
            bars_by_symbol, entry_after_ts=cut_ts, fee_multiplier=args.fee_multiplier
        )
        render(oos)
        stressed = run(bars_by_symbol, entry_after_ts=cut_ts, fee_multiplier=3.0)
        print(
            f"\n  out-of-sample at 3x costs: {stressed.total_return_pct:+.2f}% "
            f"(decay {oos.total_return_pct - stressed.total_return_pct:.2f} pp)"
        )
        if oos.trades < 30:
            print(f"  !! only {oos.trades} out-of-sample trades: not enough to conclude anything")
        if oos.total_return_pct <= 0:
            print("  !! out-of-sample is not profitable: do not trade this configuration")
        return 0

    result = run(bars_by_symbol, fee_multiplier=args.fee_multiplier)
    render(result)

    if args.stress:
        stressed = run(bars_by_symbol, fee_multiplier=3.0)
        print("\ncost stress (3x fees + slippage)")
        render(stressed)
        decay = result.total_return_pct - stressed.total_return_pct
        print(f"\n  return decay under 3x costs: {decay:.2f} pp")
        if stressed.total_return_pct <= 0:
            print("  !! not profitable once costs are realistic - do not trade this")
    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    config = Config.load(args.config_dir)
    path = config.path(args.file)
    if not path.exists():
        print(f"snapshot file not found: {path}")
        return 2
    tokens = load_snapshots(path)
    results = TokenScreener(config.onchain).screen_many(tokens)
    print(f"{'symbol':<12} {'verdict':<8} {'score':>6}  reasons")
    for result in results:
        verdict = "PASS" if result.passed else "REJECT"
        label = result.symbol or result.mint[:8]
        print(f"{label:<12} {verdict:<8} {result.score:>6.2f}  {'; '.join(result.reasons)}")
    passed = sum(1 for r in results if r.passed)
    print(
        f"\n{passed}/{len(results)} passed the safety filter "
        "(a pass is permission to consider, never a buy signal)"
    )
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    config = Config.load(args.config_dir)
    ledger_path = config.data_dir / f"ledger.{args.venue}.jsonl"
    ledger = Ledger(ledger_path, venue=args.venue)
    fills = ledger.fills
    book = ledger.rebuild_book(base_ccy=config.app.base_currency, venue=args.venue)
    for fill in fills:
        book.mark(fill.symbol, fill.price)

    print(f"ledger file      {ledger_path}")
    print(f"fills replayed   {len(fills)}")
    print(f"fees replayed    {book.fees_paid:,.4f}")
    print(f"net cash flow    {book.cash:,.4f}   (our trades only)")
    for symbol, position in sorted(book.open_positions().items()):
        print(f"  local position {symbol:<12} qty={position.qty:.8f} avg={position.avg_price:.4f}")

    stats = ledger.stats()
    print(f"events           {stats['events']}")
    print(f"orders submitted {stats['orders_submitted']}")
    print(f"blocked          {stats['blocked_total']} {stats['blocked_by_reason']}")

    if args.venue == "paper":
        print("note: the paper venue is in-memory; use the live run report for that comparison")
        return 0

    from .baseline import from_account, load, save
    from .venues import build_venue

    try:
        venue = build_venue(config, args.venue)
        venue.connect()
        account = venue.get_account()
    except Exception as exc:  # pragma: no cover - depends on credentials
        print(f"venue comparison skipped: {exc}")
        return 0

    baseline_path = config.data_dir / f"baseline.{args.venue}.json"
    baseline = load(baseline_path)
    if args.reset_baseline:
        save(baseline_path, from_account(args.venue, account, note="reset manually"))
        print(f"baseline reset   {baseline_path}")
        return 0

    print(f"venue cash       {account.cash:,.4f}")
    if baseline is not None:
        print(
            f"baseline cash    {baseline.cash:,.4f}  (set {baseline.created_at})  "
            f"expected now {baseline.cash + book.cash:,.4f}"
        )
    for symbol, position in sorted(account.positions.items()):
        ours = book.positions.get(symbol)
        managed = ours.qty if ours else 0.0
        tag = "managed" if abs(managed - position.qty) < max(1e-9, abs(position.qty) * 1e-6) else "unmanaged"
        print(
            f"  venue position {symbol:<12} qty={position.qty:.8f} "
            f"ours={managed:.8f}  [{tag}]"
        )

    report = OMS(venue, ledger).reconcile(
        account, book, cash_baseline=baseline.cash if baseline else None
    )
    print(f"reconciled       {report.ok}")
    for diff in report.diffs:
        print(f"  ! {diff}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the read-only operations dashboard."""
    from .dashboard import DashboardData, serve

    if getattr(args, "env_file", ""):
        env_path = Path(args.env_file)
        if not env_path.is_absolute():
            env_path = Path(args.config_dir).resolve().parent / env_path
        load_env_file(env_path)

    data = DashboardData(
        args.config_dir, venue=args.venue, metrics_port=args.metrics_port
    )
    if not data.ledger_path.exists():
        print(f"还没有账本：{data.ledger_path}")
        print("先跑一次交易循环（scripts/start_demo.sh），看板就有数据了。")
    serve(
        args.config_dir,
        host=args.host,
        port=args.port,
        venue=args.venue,
        metrics_port=args.metrics_port,
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import importlib.util

    print(f"python           {platform.python_version()} ({sys.executable})")
    print(f"package version  {__version__}")
    if sys.version_info < (3, 11):
        print("!! python 3.11+ required (tomllib)")
        return 2

    optional = {
        "ccxt": "live/demo execution on OKX & Binance",
        "pandas": "research notebooks",
        "pytest": "test runner",
    }
    for module, purpose in optional.items():
        found = importlib.util.find_spec(module) is not None
        print(f"{module:<16} {'installed' if found else 'MISSING':<10} {purpose}")

    config = Config.load(args.config_dir)
    print(f"config dir       {config.config_dir}")
    problems = config.validate()
    if problems:
        print("config problems:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("config           OK")

    kill = "PRESENT (the bot will refuse to trade)" if config.kill_switch_path.exists() else "clear"
    print(f"kill switch      {kill}")
    print(f"strategies       {', '.join(available())}")
    timeframes = ", ".join(config.universe.all_timeframes)
    print(f"timeframes       base={config.universe.timeframe}  warm={timeframes}")
    print(f"tiers            {config.risk.tier_names or '-'}  (default {config.risk.default_tier})")
    for tier, group in config.universe.symbols_by_tier.items():
        limits = config.risk.tiers.get(tier)
        cap = f"risk {limits.risk_per_trade_pct}% pos {limits.max_position_pct}%" if limits else "NO LIMITS"
        print(f"  {tier:<8} {len(group):>2} symbols   {cap}")
    print("venues:")
    for name in config.venues:
        status = credential_status(config, name)
        if status["requires_keys"]:
            missing = [k for k, v in status.get("vars", {}).items() if not v]
            state = "ready" if status["ready"] else f"missing {', '.join(missing)}"
        else:
            state = "no keys needed"
        sandbox = config.venue(name).sandbox
        print(f"  {name:<18} sandbox={str(sandbox):<5} {state}")

    print("\nreminder: OKX/Binance do not serve mainland-China residents; where you are")
    print("tax-resident and how the account was opened decide the compliance question.")
    return 0 if not problems else 2


# --------------------------------------------------------------------------------------
# on-chain commands
# --------------------------------------------------------------------------------------


def _onchain_stack(config: Config, args: argparse.Namespace, logger) -> OnchainExecutor:
    offline = bool(getattr(args, "offline", False))
    fixture_dir = config.path(args.fixture_dir) if getattr(args, "fixture_dir", "") else None
    service = CompositeTokenService.from_config(
        config.onchain, config.project_dir, offline=offline or None, fixture_dir=fixture_dir
    )
    client = service.client
    signer = build_signer(
        config.onchain.execution.signer, keypair_path=getattr(args, "keypair", "") or None
    )
    return OnchainExecutor(
        config.onchain,
        service,
        JupiterRouter(config.onchain, client),
        signer,
        rpc=SolanaRpc.from_config(
            config.onchain, client, fallback_url=(PUBLIC_RPC if offline else None)
        ),
        risk=RiskEngine(config.risk, config.kill_switch_path, config.onchain),
        ledger=Ledger(config.ledger_path),
        logger=logger,
        alerts=Alerts(config.log_dir / "alerts.jsonl"),
        rehearsal=offline,
        sol_price_usd=float(getattr(args, "sol_price", 0.0) or 0.0) or None,
    )


def cmd_onchain_screen(args: argparse.Namespace) -> int:
    config = Config.load(args.config_dir)
    logger = setup(config.log_dir, level=args.log_level)
    executor = _onchain_stack(config, args, logger)

    try:
        snapshot = executor.service.snapshot(args.mint)
    except Exception as exc:  # noqa: BLE001
        print(f"could not build a token snapshot: {exc}")
        print("hint: try --offline to run against the recorded fixtures")
        return 2

    print(f"mint          {snapshot.mint}")
    print(f"symbol        {snapshot.symbol or '-'}")
    print(f"age           {snapshot.age_minutes}m")
    print(f"liquidity     ${snapshot.liquidity_usd:,.0f}   24h volume ${snapshot.volume_24h_usd:,.0f}")
    print(f"holders       {snapshot.holder_count}   top10 {snapshot.top10_holder_pct:.2f}%")
    lp = f"{snapshot.lp_locked_pct:.1f}%" if snapshot.has("lp_locked_pct") else str(snapshot.lp_locked)
    print(f"lp locked     {lp}")
    print(f"honeypot      {snapshot.is_honeypot}   freezable={snapshot.freezable}  mintable={snapshot.mintable}")
    print(f"price         ${snapshot.price_usd}")
    print("provenance:")
    for field in sorted(snapshot.sources):
        print(f"  {field:<20} {snapshot.sources[field]}")
    missing = snapshot.missing()
    if missing:
        print(f"missing safety fields: {', '.join(missing)}")
    for note in snapshot.notes:
        print(f"note          {note}")

    result = executor.screener.screen(snapshot)
    print(f"\nverdict       {'PASS' if result.passed else 'REJECT'}   score={result.score}")
    for reason in result.reasons:
        print(f"  {reason}")
    if result.passed:
        print("\nnote: a pass is permission to keep looking, never a buy signal.")
    return 0


def _run_onchain(args: argparse.Namespace, *, allow_signing: bool) -> int:
    config = Config.load(args.config_dir)
    problems = config.validate()
    if problems:
        for problem in problems:
            print(f"  - {problem}")
        return 2
    logger = setup(config.log_dir, level=args.log_level)
    executor = _onchain_stack(config, args, logger)

    if getattr(args, "broadcast", False) and not getattr(args, "i_understand", False):
        print("refused: --broadcast also requires --i-understand-the-risk")
        return 2

    amount_lamports = int(round(args.amount * 1_000_000_000))
    mode = "OFFLINE REHEARSAL" if args.offline else "LIVE DATA"
    print(f"{mode}  signer={config.onchain.execution.signer}  "
          f"dry_run={config.onchain.execution.dry_run}  "
          f"config_allow_broadcast={config.onchain.execution.allow_broadcast}")
    print(f"amount {args.amount} SOL = {amount_lamports} lamports\n")

    result = executor.execute(
        args.mint,
        amount_lamports,
        allow_signing=allow_signing,
        allow_broadcast=bool(getattr(args, "broadcast", False)) and bool(getattr(args, "i_understand", False)),
        equity_usd=float(args.equity_usd) or None,
    )
    for line in result.lines():
        print(f"  {line}")
    counts = executor.service.client.source_counts
    if counts:
        print(f"  data sources      {dict(counts)}")
    if result.status == "blocked":
        print("\n  blocked before any order existed - this is the safety filter working.")
    return 0 if result.ok else 1


def cmd_onchain_quote(args: argparse.Namespace) -> int:
    return _run_onchain(args, allow_signing=False)


def cmd_onchain_execute(args: argparse.Namespace) -> int:
    return _run_onchain(args, allow_signing=bool(getattr(args, "sign", False)))


ONCHAIN_COMMANDS = {
    "screen": cmd_onchain_screen,
    "quote": cmd_onchain_quote,
    "execute": cmd_onchain_execute,
}


# --------------------------------------------------------------------------------------
# symbols: validate what you configured, or discover what is actually liquid
# --------------------------------------------------------------------------------------


def _history_store_venue(venue: str, source: str) -> str:
    """Where a fetch writes, given which catalogue it read.

    Demo and live series never share a directory. They are not the same data for
    a subset of symbols (21 of 427 measured on 2026-10-08 quote only on live), and
    merging them would let a backtest silently mix two catalogues - the exact
    failure this project keeps finding, where the run looks fine and the numbers
    are quietly wrong.
    """
    return venue if source == "demo" else f"{venue}_live"


def _public_exchange(config: Config, venue_name: str, sandbox: bool | None = None):
    """A ccxt client for PUBLIC endpoints only (no API keys required).

    `sandbox` defaults to whatever the venue config says; pass it explicitly to
    read the other catalogue. Note the effect only shows up in the RESPONSE for
    some instruments - the request and the HTTP status are identical.
    """
    section = config.venue(venue_name)
    try:
        import ccxt
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise VenueError('ccxt is not installed: pip install "trading-agent[live]"') from exc
    proxy = (
        os.environ.get("TA_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or ""
    ).strip()
    params: dict = {"enableRateLimit": True, "options": {"defaultType": section.market_type}}
    if proxy:
        # ccxt does not read the proxy env vars itself: pass it explicitly
        params["proxies"] = {"http": proxy, "https": proxy}
    exchange = getattr(ccxt, section.exchange_id)(params)
    if proxy and not getattr(exchange, "proxies", None):
        exchange.proxies = {"http": proxy, "https": proxy}
    if section.sandbox if sandbox is None else sandbox:
        exchange.set_sandbox_mode(True)
    exchange.load_markets()
    return exchange


def _listing_age_days(market: dict) -> float | None:
    created = market.get("created") or (market.get("info") or {}).get("listTime")
    try:
        created_ms = float(created)
    except (TypeError, ValueError):
        return None
    if created_ms <= 0:
        return None
    if created_ms < 1e11:  # seconds, not milliseconds
        created_ms *= 1000.0
    return max(0.0, (time.time() * 1000.0 - created_ms) / 86_400_000.0)


def cmd_symbols_check(args: argparse.Namespace) -> int:
    config = Config.load(args.config_dir)
    try:
        exchange = _public_exchange(config, args.venue)
    except Exception as exc:  # noqa: BLE001
        print(f"could not reach {args.venue}: {exc}")
        return 2

    # The demo environment exposes roughly half the pairs the live venue does.
    # "missing here" is NOT the same as "not listed": check live too, so the
    # answer is actionable instead of a mystery warning.
    live_markets: dict = {}
    if config.venue(args.venue).sandbox:
        try:
            import ccxt

            section = config.venue(args.venue)
            proxy = (
                os.environ.get("TA_PROXY")
                or os.environ.get("HTTPS_PROXY")
                or os.environ.get("https_proxy")
                or ""
            ).strip()
            params: dict = {"enableRateLimit": True, "options": {"defaultType": section.market_type}}
            if proxy:
                params["proxies"] = {"http": proxy, "https": proxy}
            live_client = getattr(ccxt, section.exchange_id)(params)
            live_client.load_markets()
            live_markets = live_client.markets
        except Exception:  # noqa: BLE001 - live check is a bonus, not a requirement
            live_markets = {}
    try:
        tickers = exchange.fetch_tickers()
    except Exception as exc:  # noqa: BLE001
        print(f"ticker fetch failed ({exc}); checking listings only")
        tickers = {}

    min_volume = config.risk.symbols.min_quote_volume_24h
    max_order = config.risk.per_trade.max_order_notional_usd
    missing: list[str] = []
    demo_only: list[str] = []
    problems: list[tuple[str, str]] = []

    print(f"venue {args.venue} ({config.venue(args.venue).exchange_id})")
    print(
        f"{'tier':<8} {'symbol':<14} {'status':<9} {'24h quote vol':>16} {'min order':>10}  notes"
    )
    for tier, group in config.universe.symbols_by_tier.items():
        for symbol in group:
            market = exchange.markets.get(symbol)
            ticker = tickers.get(symbol) or {}
            volume = ticker.get("quoteVolume")
            limits = (market or {}).get("limits") or {}
            min_cost = (limits.get("cost") or {}).get("min")
            status = "ok"
            notes: list[str] = []
            if market is None:
                on_live = symbol in live_markets
                status = "LIVE-ONLY" if on_live else "MISSING"
                if on_live:
                    demo_only.append(symbol)
                    notes.append("listed on the live venue, absent from the demo venue")
                else:
                    missing.append(symbol)
            elif not market.get("active", True):
                status = "INACTIVE"
                notes.append("not tradable right now")
            if volume and volume < min_volume:
                notes.append(f"below min_quote_volume_24h ({min_volume:,.0f})")
            if min_cost and max_order > 0 and min_cost > max_order:
                notes.append(f"venue minimum {min_cost:,.0f} > max_order_notional_usd")
            if notes and status == "ok":
                status = "check"
            if status != "ok":
                problems.append((symbol, "; ".join(notes) or status))
            print(
                f"{tier:<8} {symbol:<14} {status:<9} "
                f"{(f'{volume:,.0f}' if volume else '-'):>16} "
                f"{(f'{min_cost:,.2f}' if min_cost else '-'):>10}  {'; '.join(notes)}"
            )

    print()
    if demo_only:
        print(
            f"{len(demo_only)} symbol(s) exist on live OKX but not in the demo environment: "
            f"{', '.join(demo_only)}"
        )
        print("  -> fine to keep for the live phase; remove them if you only run demo.")
    if missing:
        print(f"{len(missing)} symbol(s) do not exist on this venue: {', '.join(missing)}")
        print(f"remove them from config/app.toml, or run: ta symbols scan --venue {args.venue}")
    if problems:
        print(f"{len(problems)} symbol(s) need attention (liquidity or minimum order size).")
    if not missing and not problems:
        print("all configured symbols are tradable and clear the liquidity floor.")
    return 0 if not missing else 1


def cmd_symbols_scan(args: argparse.Namespace) -> int:
    config = Config.load(args.config_dir)
    try:
        exchange = _public_exchange(config, args.venue)
        tickers = exchange.fetch_tickers()
    except Exception as exc:  # noqa: BLE001
        print(f"could not scan {args.venue}: {exc}")
        return 2

    rows: list[tuple[str, float, dict]] = []
    for symbol, market in exchange.markets.items():
        if market.get("quote") != args.quote or not market.get("spot"):
            continue
        if not market.get("active", True):
            continue
        volume = float((tickers.get(symbol) or {}).get("quoteVolume") or 0.0)
        if volume < args.min_volume:
            continue
        rows.append((symbol, volume, market))
    rows.sort(key=lambda row: -row[1])
    rows = rows[: args.top]
    if not rows:
        print("no symbol cleared the volume floor")
        return 1

    volumes = [row[1] for row in rows]
    floor = volumes[len(volumes) // 2] / 4.0
    buckets: dict[str, list[str]] = {"majors": [], "alts": [], "new": []}
    print(f"{'symbol':<16} {'24h quote vol':>16} {'age(d)':>8}  tier")
    for index, (symbol, volume, market) in enumerate(rows):
        age = _listing_age_days(market)
        if index < 4:
            tier = "majors"
        elif age is not None and age <= args.new_days:
            tier = "new"
        else:
            tier = "alts"
        buckets[tier].append(symbol)
        print(
            f"{symbol:<16} {volume:>16,.0f} {(f'{age:.0f}' if age is not None else '-'):>8}  {tier}"
        )

    lines = [
        f"# generated by: ta symbols scan --venue {args.venue}",
        f"# quote volume floor {args.min_volume:,.0f}, top {args.top}, new <= {args.new_days} days",
        "",
        "[universe.symbols_by_tier]",
    ]
    for name in ("majors", "alts", "new"):
        joined = ", ".join(f'"{v}"' for v in buckets[name])
        lines.append(f"{name} = [{joined}]")
    lines += [
        "",
        "# suggestion for risk.toml [symbols] (4x the weakest of the selected set):",
        f"min_quote_volume_24h = {floor:,.0f}",
    ]
    text = "\n".join(lines)
    print("\n" + text)
    if args.write:
        target = config.path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print(f"\nwritten to {target}")
    return 0


SYMBOLS_COMMANDS = {
    "check": cmd_symbols_check,
    "scan": cmd_symbols_scan,
}


def cmd_data_fetch(args: argparse.Namespace) -> int:
    from .history import fetch_symbol, history_path, write_bars

    config = Config.load(args.config_dir)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or config.traded_symbols
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    source = getattr(args, "source", "demo")
    store_venue = _history_store_venue(args.venue, source)
    try:
        exchange = _public_exchange(config, args.venue, sandbox=(source == "demo"))
    except Exception as exc:  # noqa: BLE001
        print(f"could not reach {args.venue}: {exc}")
        print("hint: export HTTPS_PROXY=http://127.0.0.1:7897 (or your proxy) first")
        return 2

    print(f"venue {args.venue} ({source} catalogue): {len(symbols)} symbol(s) x {timeframes}, "
          f"up to {args.bars} bars each")
    failures: list[str] = []
    for symbol in symbols:
        if symbol not in exchange.markets:
            failures.append(f"{symbol}: not listed on this venue")
            print(f"  {symbol:<14} MISSING - skipped")
            continue
        for timeframe in timeframes:
            bars: list = []
            last_error: Exception | None = None
            # A deep page-through can fail transiently (proxy hiccup, throttle).
            # Silently losing a symbol is how the first 180-day attempt ended up
            # with half the universe missing, so retry before giving up.
            for attempt in range(1, args.retries + 1):
                try:
                    bars = fetch_symbol(exchange, symbol, timeframe, args.bars, pause=args.pause)
                    if bars:
                        break
                    last_error = RuntimeError("no bars returned")
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                if attempt < args.retries:
                    time.sleep(args.retry_pause * attempt)
            if not bars:
                failures.append(f"{symbol} {timeframe}: {last_error}")
                print(f"  {symbol:<14} {timeframe:<4} FAILED  {last_error}")
                continue
            path = history_path(config.data_dir / "history", store_venue, symbol, timeframe)
            write_bars(path, bars)
            span = ""
            if len(bars) > 1:
                span = f"{(bars[-1].ts - bars[0].ts) / 86_400_000:>5.1f}d"
            print(f"  {symbol:<14} {timeframe:<4} {len(bars):>5} bars  {span}")

    print()
    if failures:
        print(f"{len(failures)} problem(s):")
        for item in failures[:20]:
            print(f"  - {item}")
    print(f"written under {config.data_dir / 'history' / store_venue}")
    return 0 if not failures else 1


def cmd_data_list(args: argparse.Namespace) -> int:
    from .history import list_history
    from .util import iso

    config = Config.load(args.config_dir)
    rows = list_history(config.data_dir / "history", args.venue)
    if not rows:
        print(f"nothing on disk under {config.data_dir / 'history' / args.venue}")
        print(f"run: ta data fetch --venue {args.venue}")
        return 0
    print(f"{'symbol':<14} {'tf':<5} {'bars':>6}  {'from':<17} {'to':<17}")
    for row in rows:
        print(
            f"{row['symbol']:<14} {row['timeframe']:<5} {row['bars']:>6}  "
            f"{iso(row['first_ts'])[:16]:<17} {iso(row['last_ts'])[:16]:<17}"
        )
    print(f"\n{len(rows)} file(s) under {config.data_dir / 'history' / args.venue}")
    return 0


def cmd_data_coverage(args: argparse.Namespace) -> int:
    """Listed-by-the-venue vs actually-on-disk, per instrument class.

    "How many symbols do we analyse" has had three different answers in this
    project (listed / held history / traded), and confusing them has already
    produced one wrong conclusion. This prints the gap, and separates the two
    reasons a listed symbol has no series:

      * live-only  - real candles exist, but only outside the simulated-trading
                     catalogue (fetch them with `data fetch --source live`)
      * none       - the instrument sits in the demo instrument list but returns
                     `51001 does not exist` on both catalogues, i.e. it is stale

    Offline: it reads data/universe/inventory.json and the history directories,
    and touches the network only to refresh nothing. Read-only, no orders.
    """
    import json

    from .history import safe_symbol

    config = Config.load(args.config_dir)
    inventory_path = config.data_dir / "universe" / "inventory.json"
    if not inventory_path.exists():
        print(f"no inventory at {inventory_path}")
        print("run: python3 scripts/universe_inventory.py --write data/universe/inventory.json")
        return 1
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    roots = {
        "demo": config.data_dir / "history" / args.venue,
        "live": config.data_dir / "history" / f"{args.venue}_live",
    }

    def ccxt_symbols(ids: list[str]) -> list[str]:
        out = []
        for inst in ids:
            if inst.endswith("-SWAP"):
                out.append(inst[:-5].replace("-", "/") + ":USDT")
            else:
                out.append(inst.replace("-", "/", 1))
        return out

    print(f"venue {args.venue} | timeframes {', '.join(timeframes)}")
    print(f"{'class':<10}{'tf':<5}{'listed':>8}{'demo':>7}{'live-only':>11}{'none':>7}")
    for name, meta in (inventory.get("classes") or {}).items():
        listed = ccxt_symbols(meta.get("ids_usdt") or [])
        for timeframe in timeframes:
            demo = live_only = none = 0
            for symbol in listed:
                key = safe_symbol(symbol)
                # Per timeframe, not "any": a symbol with only a 1d demo series is
                # NOT covered at 4h, and counting it as covered is how the gap
                # stayed invisible in the first place.
                if (roots["demo"] / f"{key}__{timeframe}.jsonl").exists():
                    demo += 1
                elif (roots["live"] / f"{key}__{timeframe}.jsonl").exists():
                    live_only += 1
                else:
                    none += 1
            print(f"{name:<10}{timeframe:<5}{len(listed):>8}{demo:>7}{live_only:>11}{none:>7}")
    print(
        "\nlive-only = candles exist only outside the simulated-trading catalogue;"
        "\nnone      = listed by the demo catalogue but absent from both (stale entry)."
    )
    return 0


DATA_COMMANDS = {
    "fetch": cmd_data_fetch,
    "list": cmd_data_list,
    "coverage": cmd_data_coverage,
}


# --------------------------------------------------------------------------------------
# scan: turn on-chain information into a falsifiable signal test
# --------------------------------------------------------------------------------------


def cmd_scan_discover(args: argparse.Namespace) -> int:
    from .onchain.discovery import discover, save_watchlist
    from .onchain.http import HttpClient

    config = Config.load(args.config_dir)
    client = HttpClient(
        timeout=config.onchain.data.timeout_seconds,
        retries=config.onchain.data.retries,
        cache_dir=config.path(config.onchain.data.cache_dir),
    )
    floor = args.min_liquidity or config.onchain.safety.min_liquidity_usd
    print(f"发现来源: DexScreener 公开 feed（profiles / boosts）"
          f"{'' if args.no_enrich else ' + 逐币行情'} | 流动性下限 ${floor:,.0f}")
    candidates = discover(
        config.onchain,
        client,
        limit=args.limit,
        min_liquidity_usd=floor,
        enrich=not args.no_enrich,
    )
    if not candidates:
        print("没有拿到候选（feed 为空，或全部低于流动性下限）")
        return 1
    print(f"\n{'symbol':<12} {'liquidity':>12} {'24h vol':>12} {'turnover':>9} {'boost':>8}  sources")
    for c in candidates:
        print(
            f"{c.symbol or c.mint[:8]:<12} {c.liquidity_usd:>12,.0f} {c.volume_24h_usd:>12,.0f} "
            f"{c.turnover:>9.2f} {c.boost_amount:>8.0f}  {','.join(c.sources)}"
        )
    target = save_watchlist(config.path(args.write), candidates)
    print(f"\n{len(candidates)} 个候选 -> {target}")
    print(f"接进扫描：python3 -m ta scan watch --mints-from {args.write} --interval 300")
    print("注意：能进清单只说明它池子够深、有人在推 —— 不代表它值得买。")
    return 0


def cmd_scan_watch(args: argparse.Namespace) -> int:
    from .onchain.study import SnapshotStore
    from .onchain.discovery import load_watchlist

    config = Config.load(args.config_dir)
    logger = setup(config.log_dir, level=args.log_level)
    executor = _onchain_stack(config, args, logger)
    store = SnapshotStore(config.path(args.store))
    mints = [m.strip() for m in args.mints.split(",") if m.strip()]
    # Discovery metadata the snapshot itself does not carry. Copied straight into
    # every row so the event study can filter on "was anyone actually trading
    # this" instead of inferring it from a price that never moves.
    meta_by_mint: dict[str, dict[str, Any]] = {}

    def load_from_watchlist() -> int:
        """(Re)read the candidate list. Returns how many mints it produced.

        A long-running scanner must re-read this: the list rotates completely
        within hours (measured 2026-09-30/10-01), so a scanner that loads it once
        and never again spends its life sampling tokens nobody is trading any
        more - which is exactly the confound the event study kept tripping over.
        """
        nonlocal mints, meta_by_mint
        loaded = load_watchlist(config.path(args.mints_from))
        if not loaded:
            return 0
        mints = [c.mint for c in loaded]
        meta_by_mint = {
            c.mint: {
                "boost_amount": c.boost_amount,
                "txns_m5": c.txns_m5,
                "txns_h1": c.txns_h1,
                "volume_m5_usd": c.volume_m5_usd,
                "volume_h1_usd": c.volume_h1_usd,
            }
            for c in loaded
        }
        return len(mints)

    def rediscover() -> int:
        """Refresh the candidate list from the public feeds, in-process.

        The discovery layer and the observation layer used to be two manual steps:
        somebody had to remember to run `ta scan discover` or the scanner spent its
        life sampling tokens that had already stopped trading. A long-running
        service cannot depend on somebody remembering.
        """
        from .onchain.discovery import discover as _discover, save_watchlist
        from .onchain.http import HttpClient

        client = HttpClient(
            timeout=config.onchain.data.timeout_seconds,
            retries=config.onchain.data.retries,
            cache_dir=config.path(config.onchain.data.cache_dir),
        )
        floor = float(getattr(args, "min_liquidity", 0) or 0) or config.onchain.safety.min_liquidity_usd
        candidates = _discover(
            config.onchain,
            client,
            limit=int(getattr(args, "discover_limit", 25) or 25),
            min_liquidity_usd=floor,
            enrich=True,
        )
        if not candidates:
            return 0
        save_watchlist(config.path(args.mints_from), candidates)
        return load_from_watchlist()

    if not mints and args.mints_from:
        print(f"从 {args.mints_from} 载入 {load_from_watchlist()} 个代币")
    if not mints:
        print("需要 --mints（逗号分隔的代币地址）")
        print("或用 --mints-from 指向 'ta scan discover' 生成的清单")
        return 2

    print(f"扫描 {len(mints)} 个代币，每 {args.interval:.0f} 秒一次"
          f"{'' if args.iterations == 0 else f'，共 {args.iterations} 轮'}")
    print(f"落盘: {config.path(args.store)}")
    print(f"{'#':>4} {'symbol':<10} {'score':>6} {'verdict':<7} price")
    iteration = 0
    reload_every = max(0, int(getattr(args, "reload_every", 0)))
    discover_every = max(0, int(getattr(args, "discover_every", 0)))
    try:
        while True:
            if args.iterations and iteration >= args.iterations:
                break
            iteration += 1
            if (
                discover_every
                and args.mints_from
                and iteration > 1
                and (iteration - 1) % discover_every == 0
            ):
                try:
                    count = rediscover()
                    print(
                        f"# 第 {iteration} 轮：已刷新候选清单，{count} 个代币"
                        if count
                        else f"# 第 {iteration} 轮：候选清单刷新未取到结果，沿用现有清单"
                    )
                except Exception as exc:  # noqa: BLE001 - never kill the scanner
                    print(f"# 第 {iteration} 轮：候选清单刷新失败 {type(exc).__name__}: {str(exc)[:80]}")
            if (
                reload_every
                and args.mints_from
                and iteration > 1
                and (iteration - 1) % reload_every == 0
            ):
                count = load_from_watchlist()
                if count:
                    print(f"# 第 {iteration} 轮：清单已刷新，{count} 个代币")
            for mint in mints:
                try:
                    snapshot = executor.service.snapshot(mint)
                except Exception as exc:  # noqa: BLE001 - one bad mint must not stop the watch
                    print(f"{iteration:>4} {mint[:10]:<10} {'-':>6} ERR      {str(exc)[:44]}")
                    continue
                screen = executor.screener.screen(snapshot)
                # carry the discovery metadata the snapshot itself lacks, so the
                # event study can bucket on "how hard was this promoted"
                store.append(
                    snapshot,
                    screen,
                    source="watch",
                    extra=meta_by_mint.get(mint) or {"boost_amount": 0.0},
                )
                print(
                    f"{iteration:>4} {(snapshot.symbol or mint[:8]):<10} {screen.score:>6.2f} "
                    f"{'PASS' if screen.passed else 'REJECT':<7} ${snapshot.price_usd}"
                )
            if args.iterations and iteration >= args.iterations:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n手动停止")
    print(f"\n共写入 {iteration * len(mints)} 条快照 -> {config.path(args.store)}")
    print("要让事件研究有结论：每个分桶至少 20 个**有前瞻数据**的信号")
    return 0


def cmd_scan_evaluate(args: argparse.Namespace) -> int:
    from .onchain.study import SnapshotStore, attach_forward_returns, bucket_report, format_report

    config = Config.load(args.config_dir)
    store = SnapshotStore(config.path(args.store))
    raw_rows = store.rows()
    if not raw_rows:
        print(f"{config.path(args.store)} 里还没有快照")
        print("先跑：ta scan watch --mints <地址,...> --interval 300")
        return 2
    from .onchain.study import dedupe_rows

    rows = dedupe_rows(raw_rows, bucket_seconds=args.dedupe_seconds)
    if len(rows) < len(raw_rows):
        print(
            f"去重：{len(raw_rows)} -> {len(rows)} 条唯一观测"
            f"（{100 * (1 - len(rows) / len(raw_rows)):.0f}% 是重复写入，"
            "说明有多个扫描器在写同一账本）"
        )
    # Scope the study to the CURRENT candidate list. Stale watcher instances keep
    # appending rows for tokens that have since been dropped (dead pools with
    # frozen prices); mixing them in makes "low turnover" buckets mean "dead
    # token" and manufactures a fake effect. Filtering here keeps the study valid
    # without depending on anyone successfully killing those processes.
    if not args.all_mints:
        from .onchain.discovery import load_watchlist

        wanted = {c.mint for c in load_watchlist(config.path(args.watchlist))}
        # Union with tokens observed recently. The promoted feed only carries
        # ~12-17 live tokens at a time (measured 2026-10-01: even --limit 200 with
        # a $1k floor returned 17), so scoping strictly to the CURRENT list means
        # no bucket can ever hold 20 distinct tokens - the study was capped by its
        # own scope, not by the market. A rolling window keeps the tokens that were
        # genuinely trading recently without dragging in long-dead pools.
        recent_hours = float(getattr(args, "recent_hours", 0) or 0)
        if recent_hours > 0:
            cutoff = int((time.time() - recent_hours * 3600) * 1000)
            recent = {
                str(r.get("mint"))
                for r in rows
                if int(r.get("ts") or 0) >= cutoff and r.get("mint")
            }
            wanted |= recent
            print(
                f"范围 = 当前清单 ∪ 最近 {recent_hours:g} 小时出现过的代币："
                f"{len(wanted)} 个代币"
            )
        if wanted:
            before = len(rows)
            rows = [r for r in rows if r.get("mint") in wanted]
            print(
                f"范围限定为 {len(wanted)} 个代币：{before} -> {len(rows)} 条"
                f"（丢弃 {before - len(rows)} 条更早的代币观测）"
            )
    horizons = [int(h.strip()) * 60 for h in args.horizons.split(",") if h.strip()]
    enriched = attach_forward_returns(rows, horizons)
    report = bucket_report(
        enriched,
        horizons,
        bucket_by=args.bucket_by,
        min_txns_m5=args.min_txns_m5,
    )
    print()
    print(format_report(report, len(rows)))
    return 0


SCAN_COMMANDS = {
    "watch": cmd_scan_watch,
    "evaluate": cmd_scan_evaluate,
    "discover": cmd_scan_discover,
}


COMMANDS = {
    "run": cmd_run,
    "backtest": cmd_backtest,
    "screen": cmd_screen,
    "reconcile": cmd_reconcile,
    "doctor": cmd_doctor,
    "dashboard": cmd_dashboard,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "onchain":
        return ONCHAIN_COMMANDS[args.onchain_command](args)
    if args.command == "symbols":
        return SYMBOLS_COMMANDS[args.symbols_command](args)
    if args.command == "data":
        return DATA_COMMANDS[args.data_command](args)
    if args.command == "scan":
        return SCAN_COMMANDS[args.scan_command](args)
    return COMMANDS[args.command](args)
