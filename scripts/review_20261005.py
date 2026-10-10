#!/usr/bin/env python3
"""2026-10-05 daily review data extraction."""
import json, sys
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DAY = "2026-10-05"
day_start = datetime(2026, 10, 5, 0, 0, 0, tzinfo=CST)
day_end = datetime(2026, 10, 6, 0, 0, 0, tzinfo=CST)

def norm_ts(ts):
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            try: return float(ts)
            except Exception: return 0
    if ts > 1e12: ts = ts / 1000.0
    elif ts > 1e10: ts = ts / 1000.0
    return ts

def to_cst(ts):
    return datetime.fromtimestamp(norm_ts(ts), tz=CST)

def in_day(ts):
    return day_start <= to_cst(ts) < day_end

out = {}

# ---- ledger.jsonl ----
ledger = []
with open("data/ledger.okx_demo.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except: continue
        ledger.append(r)

today_ledger = [r for r in ledger if in_day(r.get("ts", 0))]
kinds = {}
for r in today_ledger:
    kinds[r.get("kind", "?")] = kinds.get(r.get("kind", "?"), 0) + 1
out["ledger_kinds_today"] = kinds
out["ledger_total_lines"] = len(ledger)

# equity records today
eq_today = [r for r in today_ledger if r.get("kind") == "equity"]
eq_today.sort(key=lambda r: norm_ts(r["ts"]))
out["n_equity_today"] = len(eq_today)
if eq_today:
    first, last = eq_today[0], eq_today[-1]
    out["first_equity"] = {k: first.get(k) for k in ("ts","equity","cash","drawdown")}
    out["last_equity"] = {k: last.get(k) for k in ("ts","equity","cash","drawdown","day_pnl","day_pnl_pct")}
    # intraday min/max equity for drawdown calc
    eqs = [float(r.get("equity", 0)) for r in eq_today]
    peak, maxdd = eqs[0], 0.0
    peak_v = eqs[0]
    for v in eqs:
        peak_v = max(peak_v, v)
        dd = (peak_v - v) / peak_v * 100 if peak_v > 0 else 0
        maxdd = max(maxdd, dd)
    out["equity_max_today"] = max(eqs)
    out["equity_min_today"] = min(eqs)
    out["max_drawdown_pct_today"] = round(maxdd, 4)

# last equity overall (cross-day final state)
all_eq = [r for r in ledger if r.get("kind") == "equity"]
all_eq.sort(key=lambda r: norm_ts(r["ts"]))
if all_eq:
    le = all_eq[-1]
    out["latest_equity_any"] = {k: le.get(k) for k in ("ts","equity","cash","drawdown","day_pnl","day_pnl_pct")}

# positions from latest position-bearing record
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
    fill_events.append({"ts": to_cst(r["ts"]).strftime("%H:%M:%S"), "symbol": sym, "side": side, "price": px, "qty": qty, "fee": fee})
    if side == "buy":
        buy_n += 1; buy_notional += px * qty
    else:
        sell_n += 1; sell_notional += px * qty
    fees += fee
out["fills_detail"] = fill_events
out["buy_n"], out["sell_n"] = buy_n, sell_n
out["buy_notional"] = round(buy_notional, 2)
out["sell_notional"] = round(sell_notional, 2)
out["fees_today"] = round(fees, 4)

# blocked today
blocked = [r for r in today_ledger if r.get("kind") == "blocked"]
out["n_blocked_today"] = len(blocked)
reasons = {}
for r in blocked:
    reason = r.get("reason") or (r.get("data") or {}).get("reason") or json.dumps({k: v for k, v in r.items() if k not in ("ts","kind")}, ensure_ascii=False)[:80]
    reasons[reason] = reasons.get(reason, 0) + 1
out["blocked_reasons"] = reasons

# risk events today
risk = [r for r in today_ledger if r.get("kind") == "risk"]
out["n_risk_today"] = len(risk)

# realized pnl: infer from buy/sell round trips per symbol (simple FIFO per symbol today)
from collections import defaultdict, deque
books = defaultdict(deque)
realized = defaultdict(float)
closed_trades = 0
for r in sorted(fills, key=lambda x: norm_ts(x["ts"])):
    side = r.get("side") or (r.get("data") or {}).get("side")
    px = float(r.get("price") or (r.get("data") or {}).get("price") or 0)
    qty = float(r.get("qty") or r.get("amount") or (r.get("data") or {}).get("qty") or (r.get("data") or {}).get("amount") or 0)
    sym = r.get("symbol") or (r.get("data") or {}).get("symbol")
    fee = float(r.get("fee") or (r.get("data") or {}).get("fee") or 0)
    if side == "buy":
        books[sym].append((px, qty, fee))
    else:
        remaining = qty
        while remaining > 1e-12 and books[sym]:
            bpx, bqty, bfee = books[sym][0]
            take = min(bqty, remaining)
            realized[sym] += (px - bpx) * take - (bfee if bqty - take <= 1e-12 else 0) - bfee * (take / bqty if bqty else 0)
            if bqty - take <= 1e-12:
                books[sym].popleft()
            else:
                books[sym][0] = (bpx, bqty - take, bfee * (1 - take / bqty))
            remaining -= take
        closed_trades += 1
out["realized_by_symbol"] = {k: round(v, 4) for k, v in realized.items()}
out["realized_total"] = round(sum(realized.values()), 4)
out["closed_trades"] = closed_trades
wins = sum(1 for v in realized.values() if v > 0)
out["wins"] = wins
out["win_rate"] = round(wins / closed_trades * 100, 1) if closed_trades else None

# ---- alerts.jsonl ----
alerts = []
with open("logs/alerts.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except: continue
        alerts.append(r)
ts_keys = ["ts", "time", "timestamp"]
def rec_ts(r):
    for k in ts_keys:
        if k in r: return r[k]
    return 0
alerts_today = [r for r in alerts if in_day(rec_ts(r))]
out["n_alerts_today"] = len(alerts_today)
alert_types = {}
for r in alerts_today:
    t = r.get("type") or r.get("kind") or r.get("level") or "?"
    alert_types[t] = alert_types.get(t, 0) + 1
out["alert_types"] = alert_types
out["alerts_today_sample"] = alerts_today[:20]

# ---- ta.jsonl (audit log) ----
ta_kinds = {}
net_err = 0
ta_today = []
with open("logs/ta.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except: continue
        t = rec_ts(r)
        if not in_day(t): continue
        ta_today.append(r)
        ev = r.get("event") or r.get("kind") or r.get("level") or "?"
        ta_kinds[ev] = ta_kinds.get(ev, 0) + 1
        msg = json.dumps(r, ensure_ascii=False)
        if "network" in msg.lower() or "timeout" in msg.lower() or "disconn" in msg.lower() or "reconnect" in msg.lower():
            net_err += 1
out["n_ta_today"] = len(ta_today)
out["ta_event_kinds"] = dict(sorted(ta_kinds.items(), key=lambda kv: -kv[1])[:25])
out["ta_net_related_msgs"] = net_err

# network error detail by symbol from ta.jsonl
sym_err = {}
proc_err = 0
err_events = []
for r in ta_today:
    lvl = str(r.get("level", "")).upper()
    if lvl in ("ERROR", "CRITICAL", "WARNING"):
        msg = json.dumps(r, ensure_ascii=False)
        if lvl in ("ERROR", "CRITICAL"):
            proc_err += 1
            err_events.append(r.get("event") or msg[:120])
        for s in ("ADA/USDT", "SUI/USDT", "SOL/USDT", "BTC/USDT", "ETH/USDT", "OKB/USDT", "JUP/USDT", "PYTH/USDT", "ONDO/USDT", "W/USDT", "PENGU/USDT", "INJ/USDT"):
            if s in msg and ("network" in msg.lower() or "timeout" in msg.lower()):
                sym_err[s] = sym_err.get(s, 0) + 1
out["ta_error_count"] = proc_err
out["ta_error_events_top"] = dict(sorted(dict((e, err_events.count(e)) for e in set(err_events)).items(), key=lambda kv: -kv[1])[:10])
out["ta_symbol_net_err"] = dict(sorted(sym_err.items(), key=lambda kv: -kv[1]))

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
