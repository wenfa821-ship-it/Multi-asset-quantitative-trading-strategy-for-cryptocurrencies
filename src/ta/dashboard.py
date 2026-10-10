"""Local, READ-ONLY operations dashboard for the trading agent.

Why this exists
---------------
Until now the only visible output was the exchange: you could see the trades in
the OKX app and nothing else. The interesting part - which strategy fired, why an
order was blocked, what the equity curve did between trades, which bug was fixed
and when, what the risk limits actually are - was scattered across
`data/ledger.*.jsonl`, `logs/*.jsonl`, `reports/`, `archive/` and README prose.

This module serves all of that as one page. Two properties matter:

  * **Read-only.** It opens files for reading and never writes state, never talks
    to a venue and never places an order. The live process is untouched.
  * **Zero dependencies.** Stdlib HTTP server + one HTML file, no CDN, so it works
    offline and behind the same proxy as everything else.

    python3 -m ta dashboard --port 9200
    open http://127.0.0.1:9200
"""

from __future__ import annotations

import json
import os
import re
import socketserver
import subprocess
import statistics as st
import threading
import time
import tomllib
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .config import Config

MAX_TIMELINE = 400
WEB_DIR = Path(__file__).resolve().parent / "web"

# Fallbacks when config/alerts.json is absent. Chosen to be *meaningfully* tight
# rather than dramatic: a 5% peak drawdown and a 2% day are already far beyond
# anything this book has produced, so they warn early instead of never.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "max_drawdown_pct": 5.0,
    "daily_loss_pct": 2.0,
    "fees_pct_of_loss": 60.0,
    "unmanaged_notional_usd": 1.0,
    "onchain_zero_change_pct": 80.0,
    # The loop's own liveness, in minutes since the newest ledger equity event
    # (the runner writes one per completed tick). Measured 2026-10-10: the local
    # proxy's upstream died, every market-data call failed for ~25 minutes, and
    # NOTHING alerted - `ta_venue_degraded_total` stayed at 1 because it counts
    # venue-level state transitions while `fetch_bars` fails per symbol. The only
    # number that moved was this one.
    # 15 minutes, not 5: a restart re-warms bars for 64 symbols and the slowest
    # measured warmup was ~12 minutes, so a tighter limit fires on every restart.
    "runner_stale_minutes": 15.0,
    "webhook_min_interval_seconds": 1800,
}

# The page is the operator's single view, so it has to answer "is each moving part
# alive" without them opening a terminal. These are the same jobs the installers
# create; a missing one is reported as missing rather than silently omitted.
KNOWN_SERVICES: tuple[tuple[str, str, int | None, str | None], ...] = (
    ("com.tradingagent.runner", "交易循环", 9101, "logs/ta-run.pid"),
    ("com.tradingagent.watcher", "链上扫描", None, None),
    ("com.tradingagent.dashboard", "运营看板", 9200, None),
)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def read_jsonl(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Read a JSONL file, skipping blank and malformed lines.

    Malformed lines are counted, not raised: a dashboard that 500s because one
    log line was torn by a crash mid-write is worse than useless.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit is not None and len(rows) > limit:
        rows = rows[-limit:]
    return rows


def parse_metrics(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            out[parts[0]] = float(parts[1])
        except ValueError:
            continue
    return out


def round_trips(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate each open POSITION into one closed trade, net of both sides' fees.

    Partial exits belong to the same trade: the strategy banks 60% at 1R and lets
    the ATR trailing stop carry the rest, so treating the bank and the runner as two
    trades would double-count the entry fee and make the win rate meaningless.

    Net = proceeds - cost - every fee paid on that position.
    """
    open_pos: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    for fill in sorted(fills, key=lambda f: f.get("ts") or 0):
        symbol = str(fill.get("symbol") or "")
        qty = float(fill.get("qty") or 0.0)
        price = float(fill.get("price") or 0.0)
        fee = float(fill.get("fee") or 0.0)
        if qty <= 0 or price <= 0:
            continue
        pos = open_pos.setdefault(symbol, _blank_position())
        if fill.get("side") == "buy":
            if pos["qty"] <= 0:
                pos.update(_blank_position())
                pos["opened_ts"] = fill.get("ts")
            pos["qty"] += qty
            pos["entry_qty"] += qty
            pos["cost"] += qty * price
            pos["fees"] += fee
        else:
            if pos["qty"] <= 0:
                continue  # a sell with no local entry: not ours to score
            pos["proceeds"] += qty * price
            pos["fees"] += fee
            pos["qty"] -= qty
            if fill.get("tag"):
                pos["tags"].append(str(fill["tag"]))
            # A full exit never lands on exactly zero: OKX charges spot fees in the
            # base asset, so ~0.01% of the position is left behind as unsellable
            # residue. Without a relative tolerance every trade would stay "open"
            # forever and the scoreboard would be empty.
            residue = max(1e-9, pos["entry_qty"] * 1e-4)
            if pos["qty"] <= residue:
                cost = pos["cost"]
                net = pos["proceeds"] - cost - pos["fees"]
                trades.append(
                    {
                        "symbol": symbol,
                        "opened_ts": pos["opened_ts"],
                        "closed_ts": fill.get("ts"),
                        "cost": round(cost, 4),
                        "proceeds": round(pos["proceeds"], 4),
                        "fees": round(pos["fees"], 4),
                        "net": round(net, 4),
                        "ret_pct": round(100.0 * net / cost, 4) if cost > 0 else 0.0,
                        "tag": ",".join(sorted(set(pos["tags"]))),
                    }
                )
                open_pos[symbol] = _blank_position()
    trades.sort(key=lambda t: t.get("closed_ts") or 0)
    return trades


def _blank_position() -> dict[str, Any]:
    return {
        "qty": 0.0,
        "entry_qty": 0.0,
        "cost": 0.0,
        "proceeds": 0.0,
        "fees": 0.0,
        "opened_ts": 0,
        "tags": [],
    }


def merge_timeline(
    ledger: list[dict[str, Any]], alerts: list[dict[str, Any]], *, limit: int = MAX_TIMELINE
) -> list[dict[str, Any]]:
    """One chronological stream of everything that happened.

    Ledger events carry epoch-millisecond `ts`, alerts carry ISO strings, so
    everything is normalised to epoch ms before merging.
    """
    rows: list[dict[str, Any]] = []
    for event in ledger:
        kind = event.get("kind")
        if kind == "equity":
            continue  # 1000+ rows of noise; the equity curve chart covers it
        ts = int(event.get("ts") or 0)
        if kind == "order":
            rows.append(
                {
                    "ts": ts,
                    "type": "order",
                    "level": "error" if event.get("status") == "rejected" else "info",
                    "symbol": event.get("symbol"),
                    "text": f"{event.get('side')} {event.get('qty')} → {event.get('status')}",
                    "detail": event.get("reason") or event.get("tag") or "",
                }
            )
        elif kind == "fill":
            rows.append(
                {
                    "ts": ts,
                    "type": "fill",
                    "level": "info",
                    "symbol": event.get("symbol"),
                    "text": (
                        f"{event.get('side')} {event.get('qty')} @ {event.get('price')}"
                    ),
                    "detail": f"fee {float(event.get('fee') or 0):.4f} {event.get('fee_ccy') or ''}",
                }
            )
        elif kind == "blocked":
            rows.append(
                {
                    "ts": ts,
                    "type": "blocked",
                    "level": "warn",
                    "symbol": (event.get("request") or {}).get("symbol"),
                    "text": "blocked by risk engine",
                    "detail": str(event.get("reason") or ""),
                }
            )
        elif kind == "risk":
            rows.append(
                {
                    "ts": ts,
                    "type": "risk",
                    "level": "critical",
                    "symbol": event.get("symbol"),
                    "text": str(event.get("action") or ""),
                    "detail": "",
                }
            )
    for alert in alerts:
        ts = alert.get("ts")
        rows.append(
            {
                "ts": _iso_to_ms(ts),
                "type": "alert",
                "level": str(alert.get("level") or "info"),
                "symbol": alert.get("symbol"),
                "text": str(alert.get("title") or ""),
                "detail": str(alert.get("reason") or alert.get("error") or ""),
            }
        )
    rows.sort(key=lambda r: r["ts"] or 0, reverse=True)
    return rows[:limit]


def _iso_to_ms(value: Any) -> int:
    import datetime as dt

    if not value:
        return 0
    try:
        return int(dt.datetime.fromisoformat(str(value)).timestamp() * 1000)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------
CASH_RESIDUAL_DRIFT_USD = 50.0


def cash_residuals(ledger: list[dict[str, Any]]) -> list[float]:
    """`expected - actual` from every periodic cash reconciliation, oldest first.

    The runner's cash check is `venue cash at first start + our flow`, which can
    only reach zero if nothing but this bot ever touched the account. Measured
    2026-10-08: this demo account carries 2,404 venue bills against our 140 fills,
    so the residual settles at a stable ~$1,062 rather than drifting. The useful
    question is therefore **is it stable**, not **is it zero** - and that is what
    this series is for.
    """
    out: list[float] = []
    for event in ledger:
        if event.get("kind") != "risk":
            continue
        if str(event.get("action")) != "reconciliation mismatch":
            continue
        for diff in event.get("diffs") or []:
            text = str(diff)
            if not text.startswith("cash delta"):
                continue
            match = re.search(
                r"expected\s+(-?[\d.eE+]+).*?but venue shows\s+(-?[\d.eE+]+)", text
            )
            if match:
                out.append(float(match.group(1)) - float(match.group(2)))
    return out


def position_diff_count(diffs: list[Any]) -> int:
    return sum(1 for d in diffs if str(d).startswith("position "))


def cash_residual_flag(
    residuals: list[float], threshold: float = CASH_RESIDUAL_DRIFT_USD
) -> tuple[str, str]:
    """(level, detail) for a cash-residual series.

    A residual that does not move is this account's floor and deserves no more
    than an info line; one that MOVES is a real signal, because it means money is
    leaving the account in a way the book does not model.

    "Moves" is judged against the window's MEDIAN, not its range. Measured
    2026-10-09: an excursion from $1,062 to $611 kept the range above the
    threshold for nine further checks (~8 hours) while the last four sat within
    $40 of each other - an alarm that stays on after the thing it reported has
    stopped is a cry-wolf, and this project has already spent effort removing
    those.
    """
    window = residuals[-10:]
    spread = max(window) - min(window)
    drift = residuals[-1] - st.median(window)
    detail = (
        f"残余 ${residuals[-1]:+,.2f}（窗口 ${min(window):+,.0f}..${max(window):+,.0f}，"
        f"波动 ${spread:,.2f}）；相对窗口中位偏离 ${drift:+,.2f}"
    )
    if abs(drift) > threshold:
        return "warn", detail + f"（阈 ${threshold:,.0f} —— 在漂移，说明有未建模的资金流出）"
    return (
        "info",
        detail
        + " —— 账户有账本之外的活动（2,404 账单 vs 140 成交），此差额构造上不可能归零，只看它是否漂移",
    )


class DashboardData:
    """Read-only views over the project's own artefacts."""

    def __init__(
        self,
        config_dir: str | Path = "config",
        *,
        venue: str = "okx_demo",
        metrics_host: str = "127.0.0.1",
        metrics_port: int = 9101,
        max_rows: int = 60_000,
    ) -> None:
        self.config = Config.load(config_dir)
        self.venue = venue
        self.metrics_url = f"http://{metrics_host}:{metrics_port}/metrics"
        self.root = Path(self.config.project_dir)
        self.ledger_path = self.config.data_dir / f"ledger.{venue}.jsonl"
        self.alerts_path = self.config.log_dir / "alerts.jsonl"
        self.max_rows = max_rows
        self._onchain_cache: tuple[float, dict[str, Any]] | None = None
        self._carry_cache: tuple[float, dict[str, Any]] | None = None
        self._monitor_cache: tuple[float, dict[str, Any]] | None = None
        self._services_cache: tuple[float, dict[str, Any]] | None = None
        self._live_cache: tuple[float, dict[str, Any]] | None = None
        self._live_exchange: Any = None

    def _live_client(self) -> Any:
        """Build the venue client ONCE and reuse it.

        Measured 2026-10-01 after a reboot: the first /api/snapshot took **20
        seconds** because every cache miss constructed a new ccxt client and
        called `load_markets()` (which downloads every OKX instrument). The page
        polls every 15s against a 15s cache, so it paid that cost repeatedly and
        the browser showed "看板取数失败". The loop keeps its client alive; the
        dashboard must too.
        """
        if self._live_exchange is not None:
            return self._live_exchange
        import ccxt  # type: ignore

        section = self.config.venue(self.venue)
        api_key = os.environ.get(section.api_key_env, "")
        secret = os.environ.get(section.secret_env, "")
        password = os.environ.get(section.password_env, "") if section.password_env else ""
        if not (api_key and secret):
            raise RuntimeError(f"缺少 {section.api_key_env} / {section.secret_env}")
        params: dict[str, Any] = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": section.market_type},
        }
        if password:
            params["password"] = password
        exchange = getattr(ccxt, section.exchange_id)(params)
        if section.sandbox:
            exchange.set_sandbox_mode(True)
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        if proxy:
            exchange.proxies = {"http": proxy, "https": proxy}
        self._live_exchange = exchange
        return exchange

    # ---- live venue read ---------------------------------------------------
    def live_venue(self, *, ttl: float = 15.0) -> dict[str, Any]:
        """Read the venue directly, so the page can match the exchange app.

        WHY THIS EXISTS: everything else on the page comes from the loop's last
        completed tick. A tick is ~75s, so with an open position the page showed a
        mark-to-market up to a minute and a half old while the OKX app marked
        continuously - the numbers were both "right" and visibly different. This
        reads the venue itself (balance + live tickers) and is still strictly
        read-only: no orders, no cancels, no writes. It degrades to
        `available: false` on any failure so the page always has the tick numbers.
        """
        if self._live_cache and (time.time() - self._live_cache[0]) < ttl:
            return self._live_cache[1]

        result: dict[str, Any] = {"available": False, "error": ""}
        try:
            exchange = self._live_client()
            balance = exchange.fetch_balance()
            totals = balance.get("total") or {}
            base = self.config.app.base_currency
            cash = float((totals.get(base) or 0.0))
            positions: dict[str, float] = {}
            dust: dict[str, float] = {}
            # Same rule the runner uses: a holding the bot could never sell is
            # rounding residue, not a position. OKX charges spot fees in the base
            # asset, so every closed trade leaves ~1e-7 behind - counting those
            # made the page say "6 positions" while the book said 0.
            trivial_usd = self.config.risk.per_trade.min_order_notional_usd / 4.0
            value = 0.0
            dust_value = 0.0
            for ccy, amount in totals.items():
                amount = float(amount or 0.0)
                if ccy == base or amount <= 0:
                    continue
                symbol = f"{ccy}/{base}"
                if symbol not in (exchange.markets or {}):
                    continue
                price = float(exchange.fetch_ticker(symbol).get("last") or 0.0)
                if price <= 0:
                    continue
                notional = amount * price
                value += notional
                if trivial_usd > 0 and notional < trivial_usd:
                    dust[symbol] = amount
                    dust_value += notional
                else:
                    positions[symbol] = amount
            result = {
                "available": True,
                "error": "",
                "cash": round(cash, 6),
                "equity": round(cash + value, 6),
                "positions": positions,
                "position_count": len(positions),
                "dust_count": len(dust),
                "dust_notional": round(dust_value, 6),
                "read_at": int(time.time() * 1000),
            }
        except Exception as exc:  # noqa: BLE001 - the page must survive a venue outage
            result = {"available": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}

        self._live_cache = (time.time(), result)
        return result

    # ---- raw inputs --------------------------------------------------------
    def _ledger(self) -> list[dict[str, Any]]:
        return read_jsonl(self.ledger_path, limit=self.max_rows)

    def _alerts(self) -> list[dict[str, Any]]:
        return read_jsonl(self.alerts_path, limit=self.max_rows)

    def live_metrics(self) -> dict[str, float]:
        try:
            with urlopen(self.metrics_url, timeout=2) as response:  # noqa: S310 - loopback
                return parse_metrics(response.read().decode("utf-8", "replace"))
        except (URLError, OSError, TimeoutError, ValueError):
            return {}

    # ---- views -------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        ledger = self._ledger()
        metrics = self.live_metrics()
        equity = [e for e in ledger if e.get("kind") == "equity"]
        fills = [e for e in ledger if e.get("kind") == "fill"]
        first = equity[0]["equity"] if equity else 0.0
        last = equity[-1]["equity"] if equity else 0.0
        peak = max((e["equity"] for e in equity), default=0.0)
        fees = sum(float(f.get("fee") or 0.0) for f in fills)
        # Fees measured against TRADED NOTIONAL, not against the net loss.
        # `fees_pct_of_loss` moves when the loss moves (it fell from 62.8% to 44%
        # in two hours today purely because the loss grew), so it cannot answer
        # "how much does one round trip cost me". This can: measured 0.100%, which
        # is exactly the 10bps-per-side taker fee the account actually pays.
        traded_notional = sum(
            float(f.get("qty") or 0.0) * float(f.get("price") or 0.0) for f in fills
        )
        # Freshness of the tick-based numbers. The metrics endpoint carries no
        # timestamp, but it is written in the same tick as the last ledger equity
        # record, so that record IS "data as of". Without showing this, the page
        # silently looked wrong next to the exchange app whenever a position was
        # open: both numbers were correct, one was ~75s older.
        as_of = int(equity[-1]["ts"]) if equity else 0
        return {
            "venue": self.venue,
            "equity": metrics.get("ta_equity", last),
            "cash": metrics.get("ta_cash", 0.0),
            "as_of": as_of,
            "age_seconds": round(max(0.0, time.time() - as_of / 1000.0), 1) if as_of else None,
            "live": self.live_venue(),
            "equity_start": first,
            "equity_delta": round(last - first, 2),
            "return_pct": round(100.0 * (last - first) / first, 4) if first else 0.0,
            "peak_equity": peak,
            "drawdown_pct": metrics.get("ta_drawdown_pct", 0.0),
            "daily_pnl_pct": metrics.get("ta_daily_pnl_pct", 0.0),
            "kill_switch": bool(metrics.get("ta_kill_switch", 0.0)),
            "open_positions": int(metrics.get("ta_open_positions", 0)),
            "heat_pct": metrics.get("ta_heat_pct", 0.0),
            "ticks": int(metrics.get("ta_ticks_total", 0)),
            "fills": len(fills),
            "fees": round(fees, 2),
            "traded_notional": round(traded_notional, 2),
            "fee_rate_pct": (
                round(100.0 * fees / traded_notional, 4) if traded_notional > 0 else 0.0
            ),
            "fees_pct_of_loss": (
                round(100.0 * fees / abs(last - first), 1) if last < first else 0.0
            ),
            "unmanaged_notional": metrics.get("ta_unmanaged_notional", 0.0),
            "dust_notional": metrics.get("ta_dust_notional", 0.0),
            "metrics_live": bool(metrics),
        }

    def equity_series(self, *, max_points: int = 720) -> dict[str, Any]:
        equity = [e for e in self._ledger() if e.get("kind") == "equity"]
        if not equity:
            return {"points": [], "count": 0}
        stride = max(1, len(equity) // max_points)
        sampled = equity[::stride]
        if sampled[-1] is not equity[-1]:
            sampled.append(equity[-1])
        return {
            "points": [
                {
                    "ts": int(e.get("ts") or 0),
                    "equity": float(e.get("equity") or 0.0),
                    "cash": float(e.get("cash") or 0.0),
                    "positions": len(e.get("positions") or {}),
                }
                for e in sampled
            ],
            "count": len(equity),
        }

    def trades(self) -> dict[str, Any]:
        fills = [e for e in self._ledger() if e.get("kind") == "fill"]
        closed = round_trips(fills)
        wins = [t for t in closed if t["net"] > 0]
        return {
            "closed": closed[::-1],  # newest first
            "count": len(closed),
            "wins": len(wins),
            "losses": len(closed) - len(wins),
            "win_rate": round(100.0 * len(wins) / len(closed), 1) if closed else 0.0,
            "net_total": round(sum(t["net"] for t in closed), 2),
            "fees_total": round(sum(t["fees"] for t in closed), 2),
            "best": max((t["net"] for t in closed), default=0.0),
            "worst": min((t["net"] for t in closed), default=0.0),
        }

    def timeline(self) -> list[dict[str, Any]]:
        return merge_timeline(self._ledger(), self._alerts())

    def strategies(self) -> list[dict[str, Any]]:
        # Read strategies.toml directly rather than the parsed model: the panel is
        # supposed to show the operator EVERY configured parameter, including any
        # the model does not surface as a field.
        raw_all: dict[str, Any] = {}
        toml_path = Path(self.config.config_dir) / "strategies.toml"
        if toml_path.exists():
            try:
                raw_all = tomllib.loads(toml_path.read_text(encoding="utf-8")).get(
                    "strategies"
                ) or {}
            except (tomllib.TOMLDecodeError, OSError):
                raw_all = {}
        out: list[dict[str, Any]] = []
        for name, spec in self.config.strategies.items():
            raw = dict(raw_all.get(name) or {})
            out.append(
                {
                    "name": name,
                    "enabled": bool(raw.get("enabled", getattr(spec, "enabled", False))),
                    "timeframe": raw.get("timeframe") or getattr(spec, "timeframe", None),
                    "confirm": raw.get("confirm_timeframe"),
                    "description": raw.get("description", ""),
                    "symbols": raw.get("symbols", []),
                    "edge_cost_multiple": raw.get("min_edge_cost_multiple"),
                    "stop_atr": raw.get("stop_atr_multiple"),
                    "take_profit_r": raw.get("take_profit_r_multiple"),
                    "partial_fraction": raw.get("partial_fraction"),
                    "breakeven_at_r": raw.get("breakeven_at_r"),
                    "params": raw,
                }
            )
        out.sort(key=lambda s: (not s["enabled"], s["name"]))
        return out

    def risk_limits(self) -> dict[str, Any]:
        risk = self.config.risk
        per_trade = risk.per_trade
        account = risk.account
        circuit = risk.circuit
        equity = self.summary()["equity"] or 0.0

        def usd(pct: float) -> float:
            return round(equity * pct / 100.0, 2)

        return {
            "absolute_order_cap": per_trade.max_order_notional_usd,
            "order_cap_pct": per_trade.max_order_notional_pct,
            "max_positions": account.max_open_positions,
            "max_gross_pct": account.max_gross_exposure_pct,
            "max_heat_pct": account.max_heat_pct,
            "max_trades_per_day": account.max_trades_per_day,
            "min_cash_buffer_pct": account.min_cash_buffer_pct,
            "daily_stop_open_pct": circuit.daily_loss_stop_open_pct,
            "daily_flatten_pct": circuit.daily_loss_flatten_pct,
            "max_drawdown_kill_pct": circuit.max_drawdown_kill_pct,
            "adopt_existing": account.adopt_existing_positions,
            # The 50/50 split (README 20/28). Rendered as its own block because
            # the account-level numbers above are now the SUM of these, and a
            # reader who only sees "gross 100%" would think risk was doubled.
            "legs": {
                name: {
                    "max_gross_pct": leg.max_gross_exposure_pct,
                    "max_gross_usd": usd(leg.max_gross_exposure_pct),
                    "max_positions": leg.max_open_positions,
                    "max_heat_pct": leg.max_heat_pct,
                    "max_heat_usd": usd(leg.max_heat_pct),
                    "max_trades_per_day": leg.max_trades_per_day,
                }
                for name, leg in risk.legs.items()
            },
            # Stop distance lives in the risk tiers, not in strategies.toml, so the
            # strategy panel needs it from here to avoid showing a bare "-".
            "tier_stops": {
                name: {
                    "stop_atr": getattr(tier, "stop_atr_multiple", None),
                    "max_stop_pct": getattr(tier, "max_stop_pct", None),
                    "max_positions": getattr(tier, "max_positions", None),
                    "max_trades_per_day": getattr(tier, "max_trades_per_day", None),
                }
                for name, tier in risk.tiers.items()
            },
            "in_usd": {
                "max_order_majors": min(
                    usd(per_trade.max_order_notional_pct),
                    usd(risk.tiers["majors"].max_order_notional_pct),
                )
                if per_trade.max_order_notional_usd <= 0
                else per_trade.max_order_notional_usd,
                "gross": usd(account.max_gross_exposure_pct),
                "heat": usd(account.max_heat_pct),
                "daily_stop_open": usd(circuit.daily_loss_stop_open_pct),
                "daily_flatten": usd(circuit.daily_loss_flatten_pct),
                "kill_switch": usd(circuit.max_drawdown_kill_pct),
            },
        }

    def audit(self) -> list[dict[str, Any]]:
        rows = read_jsonl(self.root / "docs" / "changelog.jsonl")
        rows.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        return rows

    def artifacts(self) -> list[dict[str, Any]]:
        """Raw trail: reports, archives and the running logs, newest first."""
        out: list[dict[str, Any]] = []
        for folder, label in (
            ("reports", "报告"),
            ("archive", "归档账本"),
            ("logs", "运行日志"),
        ):
            base = self.root / folder
            if not base.is_dir():
                continue
            for path in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:14]:
                if not path.is_file():
                    continue
                stat = path.stat()
                out.append(
                    {
                        "group": label,
                        "name": path.name,
                        "folder": folder,
                        "bytes": stat.st_size,
                        "mtime": int(stat.st_mtime * 1000),
                    }
                )
        out.sort(key=lambda r: r["mtime"], reverse=True)
        return out

    def refit_history(self) -> list[dict[str, Any]]:
        rows = read_jsonl(self.root / "reports" / "refit-changelog.jsonl", limit=200)
        out = []
        for row in rows:
            out.append(
                {
                    "ts": int(row.get("ts") or 0),
                    "strategy": row.get("strategy"),
                    "objective": row.get("objective"),
                    "decision": row.get("decision"),
                    "mean_delta_pp": row.get("mean_delta_pp"),
                    "reasons": row.get("reasons") or [],
                    "overrides": row.get("overrides") or {},
                }
            )
        out.sort(key=lambda r: r["ts"], reverse=True)
        return out

    def monitor(self) -> dict[str, Any]:
        """Six-dimension environment read, shown for OBSERVATION only.

        Measurement (scripts/monitor_study.py, 2026-10-07) said the framework's
        own claim does not hold here: in both halves of a 180-day sample the
        "risk_off" buckets had the BETTER forward 24h returns (+0.68%/-0.16% and
        +1.31%/+0.38% versus risk_on+neutral). So this panel is deliberately NOT
        wired into position sizing - it is here so the tape stays legible, and so
        a future regime can be compared against this reading.
        """
        if self._monitor_cache and (time.time() - self._monitor_cache[0]) < 300.0:
            return self._monitor_cache[1]
        from .history import read_bars
        from .monitor import detect

        directory = self.config.data_dir / "history" / self.venue
        bars: dict[str, list[Any]] = {}
        newest = 0
        for path in sorted(directory.glob("*-USDT__1h.jsonl")):
            symbol = path.name.split("__")[0].replace("-", "/", 1)
            rows = read_bars(path)
            if len(rows) > 300:
                bars[symbol] = rows
                newest = max(newest, int(rows[-1].ts))
        state = detect(bars)
        payload = {
            "score": state.score,
            "max_score": 30,
            "reds": state.reds,
            "label": state.label,
            "multiplier": state.multiplier,
            "symbols": state.symbols,
            "as_of": newest,
            "dimensions": [
                {
                    "name": d.name,
                    "score": d.score,
                    "value": d.value,
                    "detail": d.detail,
                    "red": d.red,
                }
                for d in state.dimensions
            ],
            "verdict": (
                "已实测：本数据上『环境差』那组未来 24h 收益反而更好"
                "（两半样本同向），故只作观察，不接仓位刹车"
            ),
        }
        self._monitor_cache = (time.time(), payload)
        return payload

    # A real two-leg round trip on the demo venue, 2026-10-07, notional $202:
    # fees 30.0bps (spot 10+10, perp 5+5) + 14.8bps of spread and leg risk =
    # 44.9bps. README section 26 modelled 34bps from fee schedules alone; the
    # measured number is what the panel shows, because carry's breakeven IS this
    # number divided by the funding it collects - a 11bps understatement is a
    # third of the trade.
    CARRY_ROUND_TRIP_BPS = 45.0

    def carry(self) -> dict[str, Any]:
        """Funding-rate carry: what the stream pays, and how long until it pays back.

        Read-only, from the cached funding history in data/history/funding (13 OKX
        perps, 8h settlements). This is the one non-directional path left open
        after README section 25 showed a coin flip beat every factor strategy: it
        forecasts nothing, so a null result cannot make it negative - only cost,
        funding flips, or a violent upside move liquidating the short leg can.
        """
        if self._carry_cache and (time.time() - self._carry_cache[0]) < 300.0:
            return self._carry_cache[1]
        from .funding import load_funding_dir

        series = load_funding_dir(self.config.path("data/history/funding"))
        cutoff = int(time.time() * 1000) - 30 * 86_400_000
        rows: list[dict[str, Any]] = []
        for name, fs in series.items():
            rates = [r for ts, r in zip(fs.timestamps, fs.rates) if ts >= cutoff]
            if len(rates) < 3:
                continue
            daily_bps = (sum(rates) / len(rates)) * 3 * 10_000.0  # 3 settlements/day
            rows.append(
                {
                    "symbol": f"{name.split('-')[0]}/USDT",
                    "samples": len(rates),
                    "bps_per_day": round(daily_bps, 2),
                    "annual_pct": round(daily_bps * 365 / 100.0, 2),
                    "breakeven_days": (
                        round(self.CARRY_ROUND_TRIP_BPS / daily_bps, 1)
                        if daily_bps > 0
                        else None
                    ),
                }
            )
        rows.sort(key=lambda r: -r["bps_per_day"])
        payload = {
            "round_trip_bps": self.CARRY_ROUND_TRIP_BPS,
            "window_days": 30,
            "symbols": rows,
            "positive": sum(1 for r in rows if r["bps_per_day"] > 0),
            "swap_venue_configured": "okx_demo_swap" in self.config.venues,
            "tool": "scripts/carry.py",
        }
        self._carry_cache = (time.time(), payload)
        return payload

    def onchain(self) -> dict[str, Any]:
        # Cached for a minute: the page polls every 15s and the watchdog every
        # minute, and this walks the whole 13k-row store to get zero_change.
        if self._onchain_cache and (time.time() - self._onchain_cache[0]) < 60.0:
            return self._onchain_cache[1]
        watchlist = self.config.path("data/onchain/watchlist.json")
        candidates: list[dict[str, Any]] = []
        if watchlist.exists():
            try:
                payload = json.loads(watchlist.read_text(encoding="utf-8"))
                candidates = list(payload.get("candidates") or [])
            except (json.JSONDecodeError, OSError):
                candidates = []
        for row in candidates:
            liquidity = float(row.get("liquidity_usd") or 0.0)
            row["h1_turnover"] = round(
                (float(row.get("volume_h1_usd") or 0.0) / liquidity), 3
            ) if liquidity else 0.0
        candidates.sort(key=lambda r: -r.get("h1_turnover", 0.0))
        store = self.config.path("data/onchain/snapshots.jsonl")
        payload = {
            "candidates": candidates[:25],
            "candidate_count": len(candidates),
            "store_rows": sum(1 for _ in store.open("r", encoding="utf-8")) if store.exists() else 0,
            "store_mtime": int(store.stat().st_mtime * 1000) if store.exists() else 0,
            "zero_change": None,
        }
        if store.exists():
            try:
                from .onchain.study import dedupe_rows, zero_change_ratio

                wanted = {c.get("mint") for c in candidates}
                rows = [
                    r
                    for r in dedupe_rows(read_jsonl(store, limit=40_000))
                    if not wanted or r.get("mint") in wanted
                ]
                payload["zero_change"] = zero_change_ratio(rows)
            except Exception:  # noqa: BLE001 - the panel is context, never fatal
                payload["zero_change"] = None
        self._onchain_cache = (time.time(), payload)
        return payload

    def redflags(self) -> list[dict[str, Any]]:
        """The same checks the 2-hourly inspection runs, computed live."""
        ledger = self._ledger()
        alerts = self._alerts()
        metrics = self.live_metrics()
        flags: list[dict[str, Any]] = []

        ghost = [
            e
            for e in ledger
            if e.get("kind") == "order"
            and e.get("side") == "sell"
            and float(e.get("qty") or 0.0) in (1.0, 100.0)
        ]
        if ghost:
            flags.append(
                {
                    "level": "critical",
                    "title": "幽灵卖单",
                    "detail": f"账本出现 {len(ghost)} 笔 1.0/100.0 的卖出 —— 可能卖掉手动底仓",
                }
            )

        adopted = [e for e in ledger if e.get("tag") == "adopted"]
        if adopted:
            flags.append(
                {
                    "level": "warn",
                    "title": "出现接管成交",
                    "detail": f"{len(adopted)} 笔 tag=adopted —— 接管逻辑被打开了",
                }
            )

        if metrics.get("ta_kill_switch"):
            flags.append({"level": "critical", "title": "熔断已触发", "detail": "ta_kill_switch=1"})

        blocked = Counter(
            str(e.get("action")) for e in ledger if e.get("kind") == "risk"
        )
        # "reconciliation mismatch" is deliberately NOT in this list: it fires on a
        # schedule, so counting it says nothing. It is judged below on whether the
        # residual is DRIFTING.
        for title in ("killed", "daily loss flatten"):
            if blocked.get(title):
                flags.append(
                    {
                        "level": "warn" if title != "killed" else "critical",
                        "title": title,
                        "detail": f"账本中出现 {blocked[title]} 次",
                    }
                )

        # Position diffs are a HARD zero: a leg the venue does not have (or has at
        # a different size) is a real book error - that is what the phantoms were.
        recent_checks = [
            e for e in ledger
            if e.get("kind") == "risk" and e.get("action") == "reconciliation mismatch"
        ]
        latest_positions = (
            position_diff_count(recent_checks[-1].get("diffs")) if recent_checks else 0
        )
        if latest_positions:
            flags.append(
                {
                    "level": "warn",
                    "title": "仓位对账不一致",
                    "detail": f"最近一次对账有 {latest_positions} 条仓位差异（应为 0）",
                }
            )

        # The cash residual cannot reach zero on this account, so judge stability.
        # Only the CURRENT stance counts: a stretch that still had position diffs is
        # a different regime (it was mid-repair), and folding it in would call a
        # settled account "drifting" for a week.
        current_from = 0
        for index, event in enumerate(recent_checks):
            if position_diff_count(event.get("diffs")):
                current_from = index + 1
        stable_checks = recent_checks[current_from:]
        residuals = cash_residuals(stable_checks)
        if residuals:
            level, detail = cash_residual_flag(residuals)
            flags.append(
                {
                    "level": level,
                    "title": "现金对账残余（稳定）" if level == "info" else "现金对账残余在漂移",
                    "detail": detail,
                }
            )

        degraded = metrics.get("ta_venue_degraded_total", 0.0)
        if degraded:
            flags.append(
                {
                    "level": "info",
                    "title": "网络降级计数",
                    "detail": f"累计 {int(degraded)} 次（每次约暂停开仓 2 分钟）",
                }
            )
        flags.extend(self.alert_breaches())
        order = {"critical": 0, "warn": 1, "info": 2}
        flags.sort(key=lambda f: order.get(f.get("level", "info"), 3))
        return flags

    # ---- threshold watchdog ----------------------------------------------
    @staticmethod
    def _port_alive(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
        """Is anything accepting connections on this loopback port?

        A TCP connect, not `lsof`. The launchd PATH does not include /usr/sbin
        (where lsof lives), so a subprocess lookup silently reported every service
        as "no pid" - a monitoring panel that fails open is worse than none.
        """
        import socket

        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _port_pid(port: int) -> str:
        """Best-effort pid lookup; empty is acceptable, callers use _port_alive."""
        for binary in ("/usr/sbin/lsof", "lsof"):
            try:
                res = subprocess.run(  # noqa: S603 - fixed argv, no shell
                    [binary, "-tiTCP", str(port), "-sTCP:LISTEN"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            line = (res.stdout or "").strip()
            if line:
                return line.splitlines()[0]
        return ""

    def services(self) -> dict[str, Any]:
        """launchd job state + port + pid file for each moving part.

        Liveness is judged by the PORT ANSWERING first and the pid file second:
        `ps`/`kill -0` give false negatives in some sandboxes, which is exactly how
        a dead watchdog got reported as healthy before.

        ONE `launchctl list` call for all three jobs, not three `launchctl print`
        calls: the page polls every 15s and the first version spawned 5+
        subprocesses per refresh, which made the API intermittently time out.
        """
        if self._services_cache and (time.time() - self._services_cache[0]) < 30.0:
            return self._services_cache[1]

        listed: dict[str, str] = {}
        try:
            res = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["launchctl", "list"], capture_output=True, text=True, timeout=8
            )
            for line in (res.stdout or "").splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    listed[parts[2].strip()] = parts[0].strip()  # label -> pid
        except (OSError, subprocess.SubprocessError):
            listed = {}

        jobs: list[dict[str, Any]] = []
        for label, name, port, pid_file in KNOWN_SERVICES:
            job: dict[str, Any] = {"label": label, "name": name, "port": port}
            job["loaded"] = label in listed
            launchd_pid = listed.get(label, "")
            job["launchd_pid"] = launchd_pid if launchd_pid.isdigit() else ""
            # launchctl list prints 0 for "registered but not running" and "-" for
            # "loaded, no pid"; only a non-zero pid means the job has a process.
            job["launchd_state"] = launchd_pid if launchd_pid.isdigit() else "idle"

            job["port_open"] = self._port_alive(port) if port else False
            job["listening_pid"] = ""
            if pid_file:
                path = self.root / pid_file
                job["pid_file_pid"] = (
                    path.read_text(encoding="utf-8").strip() if path.exists() else ""
                )
            else:
                job["pid_file_pid"] = ""
            # A service with a port is alive iff the port answers. The watcher has
            # no port, so it falls back to launchd reporting a live pid.
            job["alive"] = (
                bool(job["port_open"])
                if port
                else bool(job["launchd_pid"])
            )
            jobs.append(job)
        payload = {
            "jobs": jobs,
            "all_alive": all(j["alive"] for j in jobs),
            "watchdog": self.watchdog_state(),
        }
        self._services_cache = (time.time(), payload)
        return payload

    def watchdog_state(self) -> dict[str, Any]:
        limits = self.thresholds()
        breaches = self.alert_breaches()
        url = os.environ.get("TA_ALERT_WEBHOOK_URL", "").strip()
        return {
            "period_seconds": 60,
            "thresholds": limits,
            "breaches": breaches,
            "breach_count": len(breaches),
            "webhook_configured": bool(url),
            "min_interval_seconds": limits.get("webhook_min_interval_seconds", 1800.0),
        }

    def thresholds(self) -> dict[str, float]:
        """Read config/alerts.json, falling back to the built-in defaults.

        Deliberately a data file rather than code: changing a threshold should not
        require touching the dashboard, and a typo should degrade to the default
        instead of taking the page down.
        """
        out = dict(DEFAULT_THRESHOLDS)
        path = Path(self.config.config_dir) / "alerts.json"
        if not path.exists():
            return out
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return out
        for key, value in (payload or {}).items():
            if key in out:
                try:
                    out[key] = float(value)
                except (TypeError, ValueError):
                    continue
        return out

    def alert_breaches(self) -> list[dict[str, Any]]:
        """Which thresholds are currently crossed, worst first."""
        limits = self.thresholds()
        summary = self.summary()
        breaches: list[dict[str, Any]] = []

        def add(key: str, level: str, title: str, value: float, limit: float, unit: str) -> None:
            breaches.append(
                {
                    "key": key,
                    "level": level,
                    "title": title,
                    "value": round(value, 4),
                    "limit": limit,
                    "detail": f"{title}：{value:,.2f}{unit}（阈值 {limit:,.2f}{unit}）",
                }
            )

        if summary["drawdown_pct"] >= limits["max_drawdown_pct"]:
            add(
                "max_drawdown_pct", "critical", "回撤超阈值",
                summary["drawdown_pct"], limits["max_drawdown_pct"], "%",
            )
        daily_loss = abs(min(0.0, summary["daily_pnl_pct"]))
        if daily_loss >= limits["daily_loss_pct"]:
            add(
                "daily_loss_pct", "critical", "单日亏损超阈值",
                daily_loss, limits["daily_loss_pct"], "%",
            )
        if (
            summary["fees_pct_of_loss"]
            and summary["fees_pct_of_loss"] >= limits["fees_pct_of_loss"]
        ):
            add(
                "fees_pct_of_loss", "warn", "手续费占亏损比超阈值",
                summary["fees_pct_of_loss"], limits["fees_pct_of_loss"], "%",
            )
        if summary["unmanaged_notional"] >= limits["unmanaged_notional_usd"]:
            add(
                "unmanaged_notional_usd", "warn", "出现非托管敞口",
                summary["unmanaged_notional"], limits["unmanaged_notional_usd"], " USD",
            )
        zero_change = (self.onchain().get("zero_change") or {}).get("unchanged_fraction")
        if zero_change is not None and zero_change * 100.0 >= limits["onchain_zero_change_pct"]:
            add(
                "onchain_zero_change_pct", "info", "链上样本零变动占比过高",
                zero_change * 100.0, limits["onchain_zero_change_pct"], "%",
            )
        # Liveness last, so it cannot outrank a real risk breach - but critical on
        # its own: a stalled loop makes no decisions at all (no entries, no exits,
        # no stop enforcement) while every other number on the page looks calm.
        as_of = float(summary.get("as_of") or 0.0)
        if as_of > 0:
            age_min = (time.time() * 1000.0 - as_of) / 60_000.0
            if age_min >= limits["runner_stale_minutes"]:
                add(
                    "runner_stale_minutes", "critical", "交易循环停摆（最后一个 tick 过久）",
                    age_min, limits["runner_stale_minutes"], " 分钟",
                )
        return breaches

    def push_alerts(self, breaches: list[dict[str, Any]]) -> bool:
        """POST the breaches to TA_ALERT_WEBHOOK_URL. No-op when it is unset.

        Returns True only when a request was actually attempted and accepted, so
        the caller can decide whether to throttle.
        """
        url = os.environ.get("TA_ALERT_WEBHOOK_URL", "").strip()
        if not url or not breaches:
            return False
        body = json.dumps(
            {
                "source": "trading-agent/dashboard",
                "venue": self.venue,
                "equity": self.summary()["equity"],
                "alerts": breaches,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(  # noqa: S310 - operator-configured webhook
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urlopen(request, timeout=5):
                return True
        except (URLError, OSError, TimeoutError, ValueError):
            return False

    # ---- one payload for the page -----------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": int(__import__("time").time() * 1000),
            "summary": self.summary(),
            "equity": self.equity_series(),
            "trades": self.trades(),
            "timeline": self.timeline(),
            "strategies": self.strategies(),
            "risk": self.risk_limits(),
            "audit": self.audit(),
            "artifacts": self.artifacts(),
            "refit": self.refit_history(),
            "onchain": self.onchain(),
            "carry": self.carry(),
            "monitor": self.monitor(),
            "redflags": self.redflags(),
            "services": self.services(),
        }


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------
class _Server(ThreadingHTTPServer):
    """HTTPServer without the reverse-DNS lookup.

    `http.server.HTTPServer.server_bind()` calls `socket.getfqdn(host)`, which does
    a reverse DNS lookup on every start. On a machine whose resolver is slow or
    blocked that call blocks *after* the socket is bound but *before* `listen()`,
    so the process looks alive while `lsof -sTCP:LISTEN` shows nothing - measured
    2026-10-01 under launchd, where the job sat there for minutes. We only ever
    serve loopback and never use the FQDN, so skip the lookup entirely.
    """

    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class _Handler(BaseHTTPRequestHandler):
    data: DashboardData | None = None
    server_version = "ta-dashboard"

    def log_message(self, *args: Any) -> None:  # noqa: D102 - quiet by default
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # Allow the HTML file to be opened straight from disk (file:// pages
            # send `Origin: null`) and fetched from the running service, which is
            # how this page kept getting opened. Narrow on purpose: only null and
            # loopback origins, never "*" - the payload contains positions and P&L
            # and should not be readable by an arbitrary web page.
            origin = self.headers.get("Origin", "")
            if origin == "null" or origin.startswith(
                ("http://127.0.0.1", "http://localhost")
            ):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            # Headers AND body inside the guard: HTTP writes its header block in
            # end_headers(), which can raise BrokenPipeError on its own - that path
            # used to escape and spray tracebacks into the service log whenever a
            # 20s response outlived the client's patience.
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # The page polls every 15s and an operator navigating away mid-response
            # is normal.
            pass

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        assert self.data is not None
        path = self.path.split("?")[0].rstrip("/") or "/"
        try:
            if path in ("/", "/index.html"):
                html = (WEB_DIR / "index.html").read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
            elif path == "/api/snapshot":
                payload = json.dumps(self.data.snapshot(), ensure_ascii=False, default=str)
                self._send(200, payload.encode("utf-8"), "application/json; charset=utf-8")
            elif path == "/api/health":
                self._send(200, b'{"status":"ok"}', "application/json")
            else:
                self._send(404, b'{"error":"not found"}', "application/json")
        except Exception as exc:  # noqa: BLE001 - never take the page down
            body = json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode()
            self._send(500, body, "application/json")


def serve(
    config_dir: str | Path = "config",
    *,
    host: str = "127.0.0.1",
    port: int = 9200,
    venue: str = "okx_demo",
    metrics_port: int = 9101,
) -> None:
    data = DashboardData(config_dir, venue=venue, metrics_port=metrics_port)
    stop = threading.Event()
    watchdog = threading.Thread(
        target=_watchdog_loop, args=(data, stop), daemon=True, name="ta-alert-watchdog"
    )
    watchdog.start()
    handler = type("BoundDashboard", (_Handler,), {"data": data})
    httpd = _Server((host, port), handler)
    print(f"trading-agent dashboard  http://{host}:{port}")
    print(f"  账本   {data.ledger_path}")
    print(f"  指标   {data.metrics_url}")
    print(f"  留痕   {data.root / 'docs' / 'changelog.jsonl'}")
    thresholds = data.thresholds()
    webhook = os.environ.get("TA_ALERT_WEBHOOK_URL", "").strip()
    print(
        f"  告警   回撤 {thresholds['max_drawdown_pct']:g}% / 单日 {thresholds['daily_loss_pct']:g}%"
        f" / 手续费占比 {thresholds['fees_pct_of_loss']:g}%"
        f" | 推送 {'已配置' if webhook else '未配置（TA_ALERT_WEBHOOK_URL 为空）'}"
    )
    print("  只读：不会写入任何状态，也不会下单。Ctrl-C 退出。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        stop.set()
        httpd.server_close()


def _watchdog_loop(data: DashboardData, stop: threading.Event, *, period: float = 60.0) -> None:
    """Evaluate the thresholds on a timer and push only on state CHANGE.

    A watchdog that only runs when somebody has the page open is not a watchdog,
    so this is a background thread rather than a side effect of a page refresh.
    Pushing on change (not every minute) is the same lesson as the flatten breaker:
    a repeating alert trains the operator to ignore it.
    """
    last_signature = ""
    last_push = 0.0
    # Evaluate once immediately, then on the timer. This also WARMS the live venue
    # read (building the ccxt client costs ~20s on first use) so the first page
    # load after a restart is fast instead of timing out.
    first = True
    while first or not stop.wait(period):
        first = False
        try:
            data.live_venue()
            breaches = data.alert_breaches()
            signature = "|".join(f"{b['key']}={b['value']}" for b in breaches)
            if signature == last_signature:
                continue
            last_signature = signature
            if not breaches:
                continue
            floor = data.thresholds().get("webhook_min_interval_seconds", 1800.0)
            if time.time() - last_push < floor:
                continue
            if data.push_alerts(breaches):
                last_push = time.time()
        except Exception:  # noqa: BLE001 - the watchdog must never take the page down
            continue
