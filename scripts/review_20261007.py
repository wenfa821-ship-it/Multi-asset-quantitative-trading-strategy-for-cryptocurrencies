#!/usr/bin/env python3
"""2026-10-07 daily review data extraction."""
import json
import re
import os
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DAY = "2026-10-07"
DAY_START = datetime(2026, 10, 7, 0, 0, 0, tzinfo=CST)
DAY_END = datetime(2026, 10, 8, 0, 0, 0, tzinfo=CST)
BASE_DIR = "/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"


def norm_ts(ts):
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            try:
                return float(ts)
            except Exception:
                return 0
    ts = float(ts)
    if ts > 1e12:
        ts = ts / 1000.0
    elif ts > 1e10:
        ts = ts / 1000.0
    return ts


def to_cst(ts):
    return datetime.fromtimestamp(norm_ts(ts), tz=CST)


def in_day(ts):
    return DAY_START <= to_cst(ts) < DAY_END


def rec_ts(r):
    for k in ("ts", "time", "timestamp"):
        if k in r:
            return r[k]
    return 0


def fmt_ts(ts):
    return to_cst(ts).strftime("%Y-%m-%d %H:%M:%S")


out = {}

# ---- ledger.okx_demo.jsonl (real ledger) ----
ledger_path = os.path.join(BASE_DIR, "data/ledger.okx_demo.jsonl")
ledger = []
with open(ledger_path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            ledger.append(json.loads(line))
        except Exception:
            continue

today_ledger = [r for r in ledger if in_day(rec_ts(r))]
kinds = defaultdict(int)
for r in today_ledger:
    kinds[r.get("kind", "?")] += 1
out["ledger_kinds_today"] = dict(kinds)
out["today_ledger_total"] = len(today_ledger)

# run_id split
rids = defaultdict(int)
for r in today_ledger:
    rids[r.get("run_id") or "?"] += 1
out["today_run_ids"] = dict(rids)

# per-run first/last events
run_events = defaultdict(list)
for r in today_ledger:
    run_events[r.get("run_id") or "?"].append(r)
run_summary = {}
for rid, evs in run_events.items():
    evs.sort(key=lambda r: norm_ts(rec_ts(r)))
    run_summary[rid] = {
        "count": len(evs),
        "first_cst": fmt_ts(rec_ts(evs[0])),
        "last_cst": fmt_ts(rec_ts(evs[-1])),
        "kinds": dict(
            sorted(
                defaultdict(int, {r.get("kind", "?"): 0 for r in evs}).items(),
                key=lambda kv: -kv[1],
            )
        ),
    }
    # Actually count kinds properly
    ks = defaultdict(int)
    for r in evs:
        ks[r.get("kind", "?")] += 1
    run_summary[rid]["kinds"] = dict(ks)
out["run_summary"] = run_summary

# equity records today
eq_today = [r for r in today_ledger if r.get("kind") == "equity"]
eq_today.sort(key=lambda r: norm_ts(rec_ts(r)))
out["n_equity_today"] = len(eq_today)

morning_baseline_equity = 103029.83  # from morning report

if eq_today:
    first = eq_today[0]
    last = eq_today[-1]
    out["first_equity"] = {
        "cst": fmt_ts(rec_ts(first)),
        "equity": first.get("equity"),
        "cash": first.get("cash"),
        "drawdown_pct": first.get("drawdown_pct") or first.get("drawdown"),
    }
    out["last_equity"] = {
        "cst": fmt_ts(rec_ts(last)),
        "equity": last.get("equity"),
        "cash": last.get("cash"),
        "drawdown_pct": last.get("drawdown_pct") or last.get("drawdown"),
        "realized_today": last.get("realized_today"),
    }
    eqs = [float(r.get("equity", 0)) for r in eq_today]
    peak_v = eqs[0]
    maxdd = 0.0
    for v in eqs:
        peak_v = max(peak_v, v)
        dd = (peak_v - v) / peak_v * 100 if peak_v > 0 else 0
        maxdd = max(maxdd, dd)
    out["equity_max_today"] = max(eqs)
    out["equity_min_today"] = min(eqs)
    out["max_drawdown_pct_today"] = round(maxdd, 6)
    baseline = morning_baseline_equity
    pnl = last.get("equity", 0) - baseline
    out["vs_morning_baseline"] = {
        "baseline_equity": baseline,
        "pnl": round(pnl, 4),
        "pnl_pct": round(pnl / baseline * 100, 6) if baseline else None,
    }
    # current drawdown vs today's peak
    out["current_drawdown_pct"] = round((out["equity_max_today"] - out["last_equity"]["equity"]) / out["equity_max_today"] * 100, 6) if out["equity_max_today"] else 0

# positions from latest position-bearing record today
pos_records = [r for r in today_ledger if r.get("kind") == "equity" and r.get("positions")]
out["last_positions"] = pos_records[-1].get("positions") if pos_records else None

# ---- orders & fills ----
orders = [r for r in today_ledger if r.get("kind") == "order"]
fills = [r for r in today_ledger if r.get("kind") == "fill"]
out["n_orders_today"] = len(orders)
out["n_fills_today"] = len(fills)

buy_notional = sell_notional = 0.0
buy_n = sell_n = 0
fees = 0.0
fill_events = []
for r in fills:
    side = r.get("side") or (r.get("data") or {}).get("side")
    px = float(r.get("price") or (r.get("data") or {}).get("price") or 0)
    qty = float(r.get("qty") or r.get("amount") or (r.get("data") or {}).get("qty") or (r.get("data") or {}).get("amount") or 0)
    fee = float(r.get("fee") or (r.get("data") or {}).get("fee") or 0)
    sym = r.get("symbol") or (r.get("data") or {}).get("symbol")
    fill_events.append({
        "cst": fmt_ts(rec_ts(r)),
        "symbol": sym,
        "side": side,
        "price": px,
        "qty": qty,
        "fee": fee,
    })
    if side == "buy":
        buy_n += 1
        buy_notional += px * qty
    else:
        sell_n += 1
        sell_notional += px * qty
    fees += fee
out["fills_detail"] = fill_events
out["buy_n"], out["sell_n"] = buy_n, sell_n
out["buy_notional"] = round(buy_notional, 2)
out["sell_notional"] = round(sell_notional, 2)
out["fees_today"] = round(fees, 4)

# order status / rejects
order_status = defaultdict(int)
rejects = []
for r in orders:
    st = str(r.get("status") or (r.get("data") or {}).get("status") or "?")
    order_status[st] += 1
    if st.lower() in ("rejected", "canceled", "cancelled", "failed"):
        rejects.append({
            "cst": fmt_ts(rec_ts(r)),
            "symbol": r.get("symbol") or (r.get("data") or {}).get("symbol"),
            "status": st,
            "reason": (r.get("data") or {}).get("note") or (r.get("data") or {}).get("reason") or r.get("reason") or r.get("note"),
        })
out["order_status"] = dict(order_status)
out["order_rejects"] = rejects

# ---- blocked ----
blocked = [r for r in today_ledger if r.get("kind") == "blocked"]
out["n_blocked_today"] = len(blocked)
reasons = defaultdict(int)
for r in blocked:
    reason = r.get("reason") or (r.get("data") or {}).get("reason") or json.dumps({k: v for k, v in r.items() if k not in ("ts", "kind", "run_id")}, ensure_ascii=False)[:120]
    reasons[reason] += 1
out["blocked_reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1]))
blocked_by_sym = defaultdict(int)
for r in blocked:
    s = r.get("symbol") or (r.get("data") or {}).get("symbol") or "?"
    blocked_by_sym[s] += 1
out["blocked_by_symbol"] = dict(blocked_by_sym)

# ---- risk events ----
risk = [r for r in today_ledger if r.get("kind") == "risk"]
out["n_risk_today"] = len(risk)
out["risk_samples"] = [{k: r.get(k) for k in ("ts", "type", "reason")} for r in risk[:20]]

# realized pnl FIFO per symbol
books = defaultdict(deque)
realized = defaultdict(float)
closed_trades = 0
sell_rows = []
for r in sorted(fills, key=lambda x: norm_ts(rec_ts(x))):
    side = r.get("side") or (r.get("data") or {}).get("side")
    px = float(r.get("price") or (r.get("data") or {}).get("price") or 0)
    qty = float(r.get("qty") or r.get("amount") or (r.get("data") or {}).get("qty") or (r.get("data") or {}).get("amount") or 0)
    sym = r.get("symbol") or (r.get("data") or {}).get("symbol")
    fee = float(r.get("fee") or (r.get("data") or {}).get("fee") or 0)
    if side == "buy":
        books[sym].append([px, qty, fee])
    else:
        remaining = qty
        pnl = 0.0
        while remaining > 1e-12 and books[sym]:
            b = books[sym][0]
            take = min(b[1], remaining)
            pnl += (px - b[0]) * take
            b[1] -= take
            if b[1] <= 1e-12:
                books[sym].popleft()
            remaining -= take
        realized[sym] += pnl
        closed_trades += 1
        sell_rows.append({"cst": fmt_ts(rec_ts(r)), "symbol": sym, "pnl_gross": round(pnl, 4)})
out["realized_by_symbol"] = {k: round(v, 4) for k, v in realized.items()}
out["realized_total"] = round(sum(realized.values()), 4)
out["closed_trades"] = closed_trades
wins = sum(1 for v in realized.values() if v > 0)
out["wins"] = wins
out["win_rate_pct"] = round(wins / closed_trades * 100, 1) if closed_trades else None
out["sell_rows"] = sell_rows

# ---- alerts.jsonl ----
alerts_path = os.path.join(BASE_DIR, "logs/alerts.jsonl")
alerts = []
with open(alerts_path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            alerts.append(json.loads(line))
        except Exception:
            continue
alerts_today = [r for r in alerts if in_day(rec_ts(r))]
out["n_alerts_today"] = len(alerts_today)
alert_types = defaultdict(int)
for r in alerts_today:
    t = r.get("type") or r.get("kind") or r.get("level") or "?"
    alert_types[t] += 1
out["alert_types"] = dict(alert_types)
out["alerts_today"] = [
    {
        "cst": fmt_ts(rec_ts(r)),
        "type": r.get("type") or r.get("kind"),
        "msg": (r.get("message") or r.get("msg") or json.dumps({k: v for k, v in r.items() if k not in ("ts", "type", "kind")}, ensure_ascii=False))[:180],
    }
    for r in alerts_today
]

# ---- ta.jsonl ----
ta_path = os.path.join(BASE_DIR, "logs/ta.jsonl")
ta_kinds = defaultdict(int)
ta_today = []
with open(ta_path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not in_day(rec_ts(r)):
            continue
        ta_today.append(r)
        ev = r.get("event") or r.get("kind") or r.get("level") or "?"
        ta_kinds[ev] += 1
out["n_ta_today"] = len(ta_today)
out["ta_event_kinds"] = dict(sorted(ta_kinds.items(), key=lambda kv: -kv[1])[:25])

sym_err = defaultdict(int)
proc_err = 0
err_events = []
reconnects = []
reconcile_events = []
for r in ta_today:
    lvl = str(r.get("level", "")).upper()
    msg = json.dumps(r, ensure_ascii=False)
    ml = msg.lower()
    ts = rec_ts(r)
    if "reconnect" in ml or "disconnect" in ml or "resubscribe" in ml:
        reconnects.append({"cst": fmt_ts(ts), "event": r.get("event"), "msg": msg[:160]})
    if lvl in ("ERROR", "CRITICAL"):
        proc_err += 1
        err_events.append(r.get("event") or msg[:140])
    if "reconciliation mismatch" in ml:
        reconcile_events.append({"cst": fmt_ts(ts), "msg": msg[:200]})
    for s in (
        "ADA/USDT", "SUI/USDT", "SOL/USDT", "BTC/USDT", "ETH/USDT", "OKB/USDT",
        "JUP/USDT", "PYTH/USDT", "ONDO/USDT", "W/USDT", "PENGU/USDT", "INJ/USDT",
        "XRP/USDT", "AVAX/USDT", "DOGE/USDT", "LINK/USDT", "NEAR/USDT", "APT/USDT",
        "TIA/USDT", "PEPE/USDT",
    ):
        if s in msg and ("network" in ml or "timeout" in ml or "54092" in msg):
            sym_err[s] += 1
out["ta_error_count"] = proc_err
out["ta_error_events_top"] = dict(sorted({e: err_events.count(e) for e in set(err_events)}.items(), key=lambda kv: -kv[1])[:10])
out["ta_symbol_net_err"] = dict(sorted(sym_err.items(), key=lambda kv: -kv[1]))
out["ta_reconnect_count"] = len(reconnects)
out["ta_reconnect_samples"] = reconnects[:10]
out["ta_reconcile_events"] = reconcile_events[:10]

# ---- ta-run-service.log today portion (UTC HH:MM:SS, infer dates from run_id anchors) ----
log_path = os.path.join(BASE_DIR, "logs/ta-run-service.log")
# run_id start times from ledger (UTC ts)
run_starts_utc = {}
for rid, evs in run_events.items():
    if rid != "?":
        run_starts_utc[rid] = datetime.fromtimestamp(norm_ts(rec_ts(evs[0])), tz=timezone.utc)

proc_log_stats = {"traceback": 0, "reconnect": 0, "network_error": 0, "fatal": 0, "shutdown": 0, "startup": 0, "ip_whitelist": 0, "reconciliation_mismatch": 0, "order_rejected": 0, "blocked": 0}
startup_lines = []
with open(log_path) as f:
    for line in f:
        # Parse leading HH:MM:SS
        m = re.match(r"(\d{2}):(\d{2}):(\d{2})\s+(.*)$", line.strip())
        if not m:
            continue
        hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        rest = m.group(4)
        # Determine date: default to file mtime date unless we can anchor by run_id in line
        line_dt = None
        # If line contains runner start with run_id, anchor to known UTC start
        ridm = re.search(r"run_id=([a-f0-9]+)", rest)
        if ridm and ridm.group(1) in run_starts_utc:
            base = run_starts_utc[ridm.group(1)]
            line_dt = base.replace(hour=hh, minute=mm, second=ss, microsecond=0)
        else:
            # Approximate: use the most recent run start before this line.
            # We'll assign to file mtime date if time-of-day is close; fallback handled below.
            line_dt = None
        # We'll count only lines whose UTC date falls within today CST window:
        # Today CST = UTC [prev_day 16:00, today 16:00) i.e. 2026-10-06 16:00 UTC to 2026-10-07 16:00 UTC
        if line_dt is None:
            # Use heuristic: if HH:MM:SS >= 16:00:00, assume UTC date = 2026-10-06; else 2026-10-07
            # This works for the visible log segments (starts on 10-06 evening, then 10-07 morning)
            if hh >= 16:
                line_dt = datetime(2026, 10, 6, hh, mm, ss, tzinfo=timezone.utc)
            else:
                line_dt = datetime(2026, 10, 7, hh, mm, ss, tzinfo=timezone.utc)
        cst_dt = line_dt.astimezone(CST)
        if not (DAY_START <= cst_dt < DAY_END):
            continue
        ll = line.lower()
        if "traceback" in ll:
            proc_log_stats["traceback"] += 1
        if "reconnect" in ll:
            proc_log_stats["reconnect"] += 1
        if "networkerror" in ll or "network error" in ll or "timeout" in ll:
            proc_log_stats["network_error"] += 1
        if "fatal" in ll or "critical" in ll:
            proc_log_stats["fatal"] += 1
        if "shut" in ll or "exit" in ll or "stopped" in ll or "terminate" in ll:
            proc_log_stats["shutdown"] += 1
        if "start" in ll and ("run_id" in ll or "listening" in ll or "engine" in ll):
            startup_lines.append({"cst": cst_dt.strftime("%Y-%m-%d %H:%M:%S"), "line": line.strip()[:220]})
            proc_log_stats["startup"] += 1
        if "50110" in line or "whitelist" in ll:
            proc_log_stats["ip_whitelist"] += 1
        if "reconciliation mismatch" in ll:
            proc_log_stats["reconciliation_mismatch"] += 1
        if "order rejected" in ll:
            proc_log_stats["order_rejected"] += 1
        if "blocked" in ll and "symbol_daily_cap" in ll:
            proc_log_stats["blocked"] += 1
out["proc_log_stats_today"] = dict(proc_log_stats)
out["proc_startup_lines"] = startup_lines

# ---- metrics sample cross-check ----
metrics = {}
try:
    import subprocess
    raw = subprocess.check_output(
        ["curl", "-s", "--noproxy", "*", "localhost:9101/metrics"],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    for line in raw.splitlines():
        if line.startswith("ta_"):
            parts = line.split()
            if len(parts) == 2:
                try:
                    metrics[parts[0]] = float(parts[1])
                except Exception:
                    metrics[parts[0]] = parts[1]
except Exception as e:
    metrics["_error"] = str(e)
out["metrics_sample"] = metrics

out_path = os.path.join(BASE_DIR, "scripts/review_20261007_out.json")
with open(out_path, "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
print("written", out_path)
print(json.dumps(
    {k: out[k] for k in (
        "ledger_kinds_today", "today_run_ids", "run_summary", "n_equity_today",
        "first_equity", "last_equity", "max_drawdown_pct_today", "vs_morning_baseline",
        "n_fills_today", "n_orders_today", "order_status", "order_rejects",
        "n_blocked_today", "blocked_reasons", "n_alerts_today", "alert_types",
        "realized_total", "closed_trades", "win_rate_pct", "fees_today",
        "proc_log_stats_today", "proc_startup_lines",
        "ta_reconnect_count", "ta_error_count", "ta_symbol_net_err",
        "ta_reconcile_events",
    )},
    ensure_ascii=False,
    indent=1,
    default=str,
))
