#!/usr/bin/env python3
"""2026-10-06 daily review data extraction."""
import json
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DAY = "2026-10-06"
day_start = datetime(2026, 10, 6, 0, 0, 0, tzinfo=CST)
day_end = datetime(2026, 10, 7, 0, 0, 0, tzinfo=CST)


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
    return day_start <= to_cst(ts) < day_end


def rec_ts(r):
    for k in ("ts", "time", "timestamp"):
        if k in r:
            return r[k]
    return 0


out = {}

# ---- ledger.okx_demo.jsonl (real ledger) ----
ledger = []
with open("data/ledger.okx_demo.jsonl") as f:
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

# run_id split
rids = defaultdict(int)
for r in today_ledger:
    rids[r.get("run_id") or "?"] += 1
out["today_run_ids"] = dict(rids)

# last event of old instance / first of new
old = [r for r in today_ledger if r.get("run_id") == "acc8f3c0e08c"]
new = [r for r in today_ledger if r.get("run_id") == "e59c93c52c0e"]
if old:
    out["old_instance_last_event"] = {"ts": to_cst(rec_ts(old[-1])).strftime("%H:%M:%S"), "kind": old[-1].get("kind")}
if new:
    out["new_instance_first_event"] = {"ts": to_cst(rec_ts(new[0])).strftime("%H:%M:%S"), "kind": new[0].get("kind")}

# equity records today
eq_today = [r for r in today_ledger if r.get("kind") == "equity"]
eq_today.sort(key=lambda r: norm_ts(rec_ts(r)))
out["n_equity_today"] = len(eq_today)
if eq_today:
    first, last = eq_today[0], eq_today[-1]
    out["first_equity"] = {k: first.get(k) for k in ("ts", "equity", "cash", "drawdown")}
    first["ts_str"] = to_cst(rec_ts(first)).strftime("%H:%M:%S")
    out["first_equity_cst"] = first["ts_str"]
    out["last_equity"] = {k: last.get(k) for k in ("equity", "cash", "drawdown", "day_pnl", "day_pnl_pct")}
    out["last_equity_cst"] = to_cst(rec_ts(last)).strftime("%H:%M:%S")
    eqs = [float(r.get("equity", 0)) for r in eq_today]
    peak_v = eqs[0]
    maxdd = 0.0
    for v in eqs:
        peak_v = max(peak_v, v)
        dd = (peak_v - v) / peak_v * 100 if peak_v > 0 else 0
        maxdd = max(maxdd, dd)
    out["equity_max_today"] = max(eqs)
    out["equity_min_today"] = min(eqs)
    out["max_drawdown_pct_today"] = round(maxdd, 4)

# 07:02 morning baseline equity (closest to 07:00)
def closest_eq(h, m):
    tgt = datetime(2026, 10, 6, h, m, tzinfo=CST).timestamp()
    if not eq_today:
        return None
    best = min(eq_today, key=lambda r: abs(norm_ts(rec_ts(r)) - tgt))
    return {"cst": to_cst(rec_ts(best)).strftime("%H:%M:%S"), "equity": best.get("equity"), "cash": best.get("cash"), "positions": best.get("positions")}

out["morning_baseline_0700"] = closest_eq(7, 0)

# latest equity overall
all_eq = [r for r in ledger if r.get("kind") == "equity"]
all_eq.sort(key=lambda r: norm_ts(rec_ts(r)))
if all_eq:
    le = all_eq[-1]
    out["latest_equity_any"] = {k: le.get(k) for k in ("ts", "equity", "cash", "drawdown", "day_pnl", "day_pnl_pct")}
    out["latest_equity_cst"] = to_cst(rec_ts(le)).strftime("%Y-%m-%d %H:%M:%S")

# positions from latest position-bearing record today
pos_records = [r for r in today_ledger if r.get("kind") == "equity" and r.get("positions")]
out["last_positions"] = pos_records[-1].get("positions") if pos_records else None

# orders & fills today
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
    fill_events.append({"ts": to_cst(rec_ts(r)).strftime("%H:%M:%S"), "symbol": sym, "side": side, "price": px, "qty": qty, "fee": fee})
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

# order status distribution (for rejections)
order_status = defaultdict(int)
rejects = []
for r in orders:
    st = str(r.get("status") or (r.get("data") or {}).get("status") or "?")
    order_status[st] += 1
    if st.lower() in ("rejected", "canceled", "cancelled", "failed"):
        rejects.append({"ts": to_cst(rec_ts(r)).strftime("%H:%M:%S"), "symbol": r.get("symbol"), "status": st, "note": (r.get("data") or {}).get("note") or (r.get("data") or {}).get("reason") or r.get("reason")})
out["order_status"] = dict(order_status)
out["order_rejects"] = rejects

# blocked today
blocked = [r for r in today_ledger if r.get("kind") == "blocked"]
out["n_blocked_today"] = len(blocked)
reasons = defaultdict(int)
for r in blocked:
    reason = r.get("reason") or (r.get("data") or {}).get("reason") or json.dumps({k: v for k, v in r.items() if k not in ("ts", "kind", "run_id")}, ensure_ascii=False)[:100]
    reasons[reason] += 1
out["blocked_reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1]))
blocked_by_sym = defaultdict(int)
for r in blocked:
    s = r.get("symbol") or (r.get("data") or {}).get("symbol") or "?"
    blocked_by_sym[s] += 1
out["blocked_by_symbol"] = dict(blocked_by_sym)

# risk events today
risk = [r for r in today_ledger if r.get("kind") == "risk"]
out["n_risk_today"] = len(risk)
out["risk_samples"] = [{k: r.get(k) for k in ("ts", "type", "reason")} for r in risk[:10]]

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
        sell_rows.append({"ts": to_cst(rec_ts(r)).strftime("%H:%M"), "symbol": sym, "pnl_gross": round(pnl, 4)})
out["realized_by_symbol"] = {k: round(v, 4) for k, v in realized.items()}
out["realized_total"] = round(sum(realized.values()), 4)
out["closed_trades"] = closed_trades
wins = sum(1 for v in realized.values() if v > 0)
out["wins"] = wins
out["win_rate_pct"] = round(wins / closed_trades * 100, 1) if closed_trades else None
out["sell_rows"] = sell_rows

# ---- alerts.jsonl ----
alerts = []
with open("logs/alerts.jsonl") as f:
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
    {"ts": to_cst(rec_ts(r)).strftime("%H:%M:%S"), "type": r.get("type") or r.get("kind"), "msg": (r.get("message") or r.get("msg") or json.dumps({k: v for k, v in r.items() if k not in ("ts", "type", "kind")}, ensure_ascii=False))[:160]}
    for r in alerts_today
]

# ---- ta.jsonl ----
ta_kinds = defaultdict(int)
ta_today = []
with open("logs/ta.jsonl") as f:
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
for r in ta_today:
    lvl = str(r.get("level", "")).upper()
    msg = json.dumps(r, ensure_ascii=False)
    ml = msg.lower()
    if "reconnect" in ml or "disconnect" in ml or "resubscribe" in ml:
        reconnects.append({"ts": to_cst(rec_ts(r)).strftime("%H:%M:%S"), "event": r.get("event"), "msg": msg[:140]})
    if lvl in ("ERROR", "CRITICAL"):
        proc_err += 1
        err_events.append(r.get("event") or msg[:120])
    for s in ("ADA/USDT", "SUI/USDT", "SOL/USDT", "BTC/USDT", "ETH/USDT", "OKB/USDT", "JUP/USDT", "PYTH/USDT", "ONDO/USDT", "W/USDT", "PENGU/USDT", "INJ/USDT", "XRP/USDT", "AVAX/USDT", "DOGE/USDT", "LINK/USDT", "NEAR/USDT", "APT/USDT", "TIA/USDT", "PEPE/USDT"):
        if s in msg and ("network" in ml or "timeout" in ml or "54092" in msg):
            sym_err[s] += 1
out["ta_error_count"] = proc_err
out["ta_error_events_top"] = dict(sorted({e: err_events.count(e) for e in set(err_events)}.items(), key=lambda kv: -kv[1])[:10])
out["ta_symbol_net_err"] = dict(sorted(sym_err.items(), key=lambda kv: -kv[1]))
out["ta_reconnect_count"] = len(reconnects)
out["ta_reconnect_samples"] = reconnects[:10]

# ---- ta-run-service.log today portion ----
proc_log_stats = {"traceback": 0, "reconnect": 0, "network_error": 0, "fatal": 0, "shutdown": 0, "startup": 0}
startup_lines = []
with open("logs/ta-run-service.log") as f:
    for line in f:
        if "2026-10-06" not in line:
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
            startup_lines.append(line.strip()[:200])
            proc_log_stats["startup"] += 1
out["proc_log_stats_today"] = proc_log_stats
out["proc_startup_lines"] = startup_lines[:10]

with open("scripts/review_20261006_out.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
print("written scripts/review_20261006_out.json")
print(json.dumps({k: out[k] for k in ("ledger_kinds_today", "today_run_ids", "n_equity_today", "first_equity_cst", "last_equity", "max_drawdown_pct_today", "n_fills_today", "n_orders_today", "n_blocked_today", "n_alerts_today", "realized_total", "closed_trades", "win_rate_pct", "fees_today")}, ensure_ascii=False, indent=1, default=str))
