#!/usr/bin/env python3
"""Daily trading review aggregator."""
import json
import re
import os
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict
from pathlib import Path

WORKSPACE = Path('/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent')
DATE = '2026-10-06'

# Trading day in Beijing time -> UTC
CST = timezone(timedelta(hours=8))
DAY_START_CST = datetime.fromisoformat(f'{DATE} 00:00:00').replace(tzinfo=CST)
DAY_END_CST = DAY_START_CST + timedelta(days=1)
DAY_START_UTC = DAY_START_CST.astimezone(timezone.utc)
DAY_END_UTC = DAY_END_CST.astimezone(timezone.utc)


def parse_ts(obj):
    ts = obj.get('ts')
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        if ts > 1e12:
            return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    return None


def in_trading_day(dt):
    return DAY_START_UTC <= dt < DAY_END_UTC


def load_jsonl(path):
    items = []
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                obj['_dt'] = parse_ts(obj)
                if obj['_dt'] is None:
                    continue
                items.append(obj)
            except Exception:
                continue
    return items


def load_jsonl_with_run_id(path, default_venue=None):
    """Load jsonl and infer run_id/venue from surrounding equity records if missing."""
    items = load_jsonl(path)
    current_run_id = None
    current_venue = default_venue
    # First pass: propagate run_id/venue forward from equity records
    for obj in items:
        if obj.get('kind') == 'equity':
            if 'run_id' in obj:
                current_run_id = obj['run_id']
            if 'venue' in obj:
                current_venue = obj['venue']
        if 'run_id' not in obj and current_run_id:
            obj['run_id'] = current_run_id
        if 'venue' not in obj and current_venue:
            obj['venue'] = current_venue
    return items


def analyze():
    # Ledger files per venue
    ledger_files = {
        'legacy': WORKSPACE / 'data' / 'ledger.jsonl',
        'okx_demo': WORKSPACE / 'data' / 'ledger.okx_demo.jsonl',
        'paper': WORKSPACE / 'data' / 'ledger.paper.jsonl',
    }

    ta_log = load_jsonl(WORKSPACE / 'logs' / 'ta.jsonl')
    alerts_log = load_jsonl(WORKSPACE / 'logs' / 'alerts.jsonl')

    # Archive files for today
    archive_dir = WORKSPACE / 'archive'
    archive_ledger = []
    archive_ta = []
    archive_alerts = []
    if archive_dir.exists():
        for f in archive_dir.iterdir():
            if f.is_file() and DATE.replace('-', '') in f.name:
                if 'ledger' in f.name:
                    archive_ledger.extend(load_jsonl_with_run_id(f))
                elif 'ta.' in f.name:
                    archive_ta.extend(load_jsonl(f))
                elif 'alerts' in f.name:
                    archive_alerts.extend(load_jsonl(f))

    all_ledger = []
    for venue, path in ledger_files.items():
        if path.exists():
            all_ledger.extend(load_jsonl_with_run_id(path, default_venue=venue))
    all_ledger.extend(archive_ledger)

    # Filter to trading day
    day_ledger = [x for x in all_ledger if in_trading_day(x['_dt'])]
    day_ta = [x for x in ta_log if in_trading_day(x['_dt'])]
    day_alerts = [x for x in alerts_log if in_trading_day(x['_dt'])]
    day_archive_alerts = [x for x in archive_alerts if in_trading_day(x['_dt'])]
    day_alerts_combined = day_alerts + day_archive_alerts

    # Sort by time
    day_ledger.sort(key=lambda x: x['_dt'])

    # Active venue detection (from last runner start / live metrics if available)
    runner_starts = [x for x in day_ta if x.get('msg') == 'runner start']
    venues_today = Counter(s.get('venue', 'unknown') for s in runner_starts)

    # Equity curve per venue
    equity_by_venue = defaultdict(list)
    equity_records = [x for x in day_ledger if x.get('kind') == 'equity']
    for rec in equity_records:
        venue = rec.get('venue', 'legacy')
        equity_by_venue[venue].append(rec)

    # Determine current "primary" venue from latest runner start
    current_venue = None
    if runner_starts:
        current_venue = runner_starts[-1].get('venue')
    elif equity_records:
        # use last equity record venue
        current_venue = equity_records[-1].get('venue', 'legacy')

    # If we can reach metrics endpoint, use those live values
    live = sample_live_metrics()

    # Build final equity/cash/positions from ledger or live
    final_equity = None
    final_cash = None
    final_positions = {}
    final_marks = {}
    if equity_records:
        last = equity_records[-1]
        final_equity = last.get('equity')
        final_cash = last.get('cash')
        final_positions = last.get('positions', {})
        final_marks = last.get('marks', {})

    # Prefer live metrics if available and match current venue
    if live:
        if final_equity is None:
            final_equity = live.get('equity')
        if final_cash is None:
            final_cash = live.get('cash')
        if live.get('open_positions') == 0:
            final_positions = {}

    # Baseline: morning report or first equity of the day per venue
    baseline_report = read_morning_baseline()
    baseline_equity = None
    if baseline_report:
        baseline_equity = baseline_report.get('equity')
    if baseline_equity is None and equity_records:
        baseline_equity = equity_records[0].get('equity')

    # Compute PnL
    pnl = None
    return_pct = None
    if baseline_equity and final_equity:
        pnl = final_equity - baseline_equity
        return_pct = (pnl / baseline_equity) * 100

    # Drawdown from equity records (peak to trough)
    peak = None
    trough = None
    max_dd_pct = 0.0
    for rec in equity_records:
        eq = rec.get('equity')
        if eq is None:
            continue
        if peak is None or eq > peak:
            peak = eq
        if trough is None or eq < trough:
            trough = eq
        if peak and peak > 0:
            dd = (peak - eq) / peak * 100
            if dd > max_dd_pct:
                max_dd_pct = dd

    current_dd_pct = 0.0
    if peak and final_equity:
        current_dd_pct = (peak - final_equity) / peak * 100

    # Trades / fills
    fills = [x for x in day_ledger if x.get('kind') == 'fill']
    orders = [x for x in day_ledger if x.get('kind') == 'order']
    blocked = [x for x in day_ledger if x.get('kind') == 'blocked']
    risk_events = [x for x in day_ledger if x.get('kind') == 'risk']

    fill_count = len(fills)
    buy_fills = [x for x in fills if x.get('side') == 'buy']
    sell_fills = [x for x in fills if x.get('side') == 'sell']
    total_fees = sum(x.get('fee', 0) or 0 for x in fills)

    # Realized PnL: from equity records' realized_today delta or from fills notes
    realized_pnl = 0.0
    if equity_records:
        first_realized = equity_records[0].get('realized_today', 0.0) or 0.0
        last_realized = equity_records[-1].get('realized_today', 0.0) or 0.0
        realized_pnl = last_realized - first_realized

    # Win rate: count completed round-trip trades via equity/trade records or ta.jsonl run reports
    win_rate = None
    closed_trades = 0
    wins = 0
    run_reports = [x for x in day_ta if x.get('msg') == 'run report']
    if run_reports:
        last_report = run_reports[-1]
        win_rate = last_report.get('win_rate_pct')
        closed_trades = last_report.get('trades', 0)
        if win_rate is not None:
            wins = int(round(closed_trades * win_rate / 100))

    # Blocked reasons
    blocked_reasons = Counter()
    for b in blocked:
        reason = b.get('reason', b.get('note', 'unknown'))
        blocked_reasons[reason] += 1

    # Alerts distribution
    alert_levels = Counter(a.get('level', 'unknown') for a in day_alerts_combined)
    alert_titles = Counter(a.get('title', a.get('msg', 'unknown')) for a in day_alerts_combined)

    # Network / process anomalies from logs
    log_path = WORKSPACE / 'logs' / f"ta-run-{DATE.replace('-', '')}.log"
    log_stats = analyze_log(log_path)

    # Runner starts/restarts count
    restart_count = len(runner_starts) - 1 if len(runner_starts) > 0 else 0
    first_start = runner_starts[0]['_dt'] if runner_starts else None
    last_start = runner_starts[-1]['_dt'] if runner_starts else None

    # Kill switch detection
    kill_switch_triggered = any(x.get('killed') for x in run_reports) or bool(live.get('kill_switch'))
    kill_reason = ''
    for r in run_reports:
        if r.get('killed'):
            kill_reason = r.get('kill_reason', '')
            break

    # Position market value
    position_value = 0.0
    for sym, qty in final_positions.items():
        price = final_marks.get(sym, 0)
        position_value += qty * price

    report = {
        'date': DATE,
        'process': {
            'pid': live.get('_pid') if live else None,
            'alive': live.get('_alive') if live else False,
            'pid_file_ok': live.get('_pid_file_ok') if live else False,
            'restarts_today': restart_count,
            'first_start_utc': first_start.isoformat() if first_start else None,
            'last_start_utc': last_start.isoformat() if last_start else None,
            'runner_starts': len(runner_starts),
            'venues_active': dict(venues_today),
            'current_venue': current_venue,
        },
        'overview': {
            'baseline_equity': baseline_equity,
            'final_equity': final_equity,
            'final_cash': final_cash,
            'position_value': position_value,
            'pnl': pnl,
            'return_pct': return_pct,
            'max_drawdown_pct': max_dd_pct,
            'current_drawdown_pct': current_dd_pct,
            'open_positions': len(final_positions),
            'positions': final_positions,
            'marks': final_marks,
        },
        'trading': {
            'fill_count': fill_count,
            'buy_fills': len(buy_fills),
            'sell_fills': len(sell_fills),
            'total_fees': total_fees,
            'realized_pnl': realized_pnl,
            'closed_trades': closed_trades,
            'wins': wins,
            'win_rate_pct': win_rate,
        },
        'risk': {
            'blocked_count': len(blocked),
            'blocked_reasons': dict(blocked_reasons),
            'risk_events': len(risk_events),
            'kill_switch_triggered': kill_switch_triggered,
            'kill_reason': kill_reason,
        },
        'alerts': {
            'total': len(day_alerts_combined),
            'by_level': dict(alert_levels),
            'by_title': dict(alert_titles),
        },
        'stability': {
            'log_lines': log_stats.get('total_lines'),
            'traceback_count': log_stats.get('traceback_count'),
            'exception_count': log_stats.get('exception_count'),
            'network_error_count': log_stats.get('network_error_count'),
            'reconnect_count': log_stats.get('reconnect_count'),
            'kill_switch_mentions': log_stats.get('kill_switch_count'),
            'critical_count': log_stats.get('critical_count'),
            'ssl_errors': log_stats.get('ssl_errors'),
            'http_errors': log_stats.get('http_errors'),
        },
        'live_metrics': live,
    }
    return report


def read_morning_baseline():
    path = WORKSPACE / 'reports' / f'{DATE}-morning.md'
    if not path.exists():
        return None
    result = {}
    text = path.read_text()
    m = re.search(r'权益[：:]\s*([\d,.]+)', text)
    if m:
        result['equity'] = float(m.group(1).replace(',', ''))
    m = re.search(r'现金[：:]\s*([\d,.]+)', text)
    if m:
        result['cash'] = float(m.group(1).replace(',', ''))
    m = re.search(r'基线[：:]\s*([\d,.]+)', text)
    if m:
        result['equity'] = float(m.group(1).replace(',', ''))
    return result


def sample_live_metrics():
    import subprocess, time
    live = {'_alive': False, '_pid': None, '_pid_file_ok': False}
    # Try HTTP metrics
    try:
        out = subprocess.run(
            ['curl', '-s', '--noproxy', '*', 'localhost:9101/metrics'],
            capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip():
            live['_alive'] = True
            for line in out.stdout.splitlines():
                line = line.strip()
                if line.startswith('ta_'):
                    parts = line.split(' ')
                    if len(parts) == 2:
                        key, val = parts[0], parts[1]
                        try:
                            live[key] = float(val)
                        except ValueError:
                            live[key] = val
    except Exception:
        pass

    # PID consistency check
    try:
        pid_port = subprocess.run(
            ['lsof', '-tiTCP:9101', '-sTCP:LISTEN'],
            capture_output=True, text=True, timeout=5
        )
        pid = None
        if pid_port.stdout.strip():
            pid = int(pid_port.stdout.strip().splitlines()[0])
        if not pid:
            pgrep = subprocess.run(
                ['pgrep', '-f', 'ta run --venue'],
                capture_output=True, text=True, timeout=5
            )
            if pgrep.stdout.strip():
                pid = int(pgrep.stdout.strip().splitlines()[0])
        if not pid:
            pid_file = WORKSPACE / 'logs' / 'ta-run.pid'
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                except Exception:
                    pass
        live['_pid'] = pid
        if pid and not live['_alive']:
            # Process exists but metrics not responding? check ps
            ps = subprocess.run(['ps', '-p', str(pid), '-o', 'comm='],
                                capture_output=True, text=True, timeout=3)
            live['_alive'] = bool(ps.stdout.strip())
    except Exception:
        pass

    # Check pid file matches
    pid_file = WORKSPACE / 'logs' / 'ta-run.pid'
    if pid_file.exists() and live['_pid']:
        try:
            file_pid = int(pid_file.read_text().strip())
            live['_pid_file_ok'] = (file_pid == live['_pid'])
            if file_pid != live['_pid']:
                pid_file.write_text(str(live['_pid']))
                live['_pid_file_corrected'] = True
        except Exception:
            pass

    return live


def analyze_log(path):
    stats = {
        'total_lines': 0,
        'traceback_count': 0,
        'exception_count': 0,
        'network_error_count': 0,
        'reconnect_count': 0,
        'kill_switch_count': 0,
        'critical_count': 0,
        'ssl_errors': 0,
        'http_errors': 0,
    }
    if not path.exists():
        return stats

    ssl_pat = re.compile(r'SSLError|SSL|UNEXPECTED_EOF|CERTIFICATE', re.I)
    http_pat = re.compile(r'MaxRetryError|HTTPSConnectionPool|HTTPError|timeout', re.I)
    reconnect_pat = re.compile(r'reconnect|reconn|disconnected|websocket.*close|connection.*reset|conn.*broken', re.I)
    exception_pat = re.compile(r'Exception|Error:', re.I)
    traceback_pat = re.compile(r'Traceback|traceback', re.I)
    kill_pat = re.compile(r'kill switch|killswitch|stop trading|halt', re.I)
    critical_pat = re.compile(r'CRITICAL|FATAL', re.I)

    with open(path, 'r') as f:
        for line in f:
            stats['total_lines'] += 1
            if traceback_pat.search(line):
                stats['traceback_count'] += 1
            if exception_pat.search(line):
                stats['exception_count'] += 1
            if reconnect_pat.search(line):
                stats['reconnect_count'] += 1
                stats['network_error_count'] += 1
            if kill_pat.search(line):
                stats['kill_switch_count'] += 1
            if critical_pat.search(line):
                stats['critical_count'] += 1
            if ssl_pat.search(line):
                stats['ssl_errors'] += 1
            if http_pat.search(line):
                stats['http_errors'] += 1
    return stats


def render_markdown(report):
    date = report['date']
    p = report['process']
    o = report['overview']
    t = report['trading']
    r = report['risk']
    a = report['alerts']
    s = report['stability']
    l = report.get('live_metrics', {}) or {}

    def fmt(v):
        if v is None:
            return '—'
        if isinstance(v, (int, float)):
            if isinstance(v, float):
                return f'{v:.2f}'
            return str(v)
        return str(v)

    def pct(v):
        if v is None:
            return '—'
        return f'{v:.2f}%'

    proc_status = '✅ 运行中' if l.get('_alive') else '❌ 未检测到进程'
    if l.get('_pid_file_ok') is False and l.get('_pid'):
        proc_status += f" (PID {l.get('_pid')}，已修正 pid 文件)"
    elif l.get('_pid'):
        proc_status += f" (PID {l.get('_pid')})"

    pnl = o.get('pnl')
    pnl_str = f"{pnl:+.2f}" if pnl is not None else '—'
    ret = o.get('return_pct')
    ret_str = f"{ret:+.2f}%" if ret is not None else '—'

    md = f"""# {date} 交易复盘报告

## 1. 当日概览

| 项目 | 数值 |
|------|------|
| 交易日 | {date} |
| 进程状态 | {proc_status} |
| 当前 venue | {p.get('current_venue', '—')} |
| 当日启动次数 | {p.get('runner_starts', 0)}（重启 {p.get('restarts_today', 0)} 次） |
| 收盘权益 | {fmt(o.get('final_equity'))} |
| 收盘现金 | {fmt(o.get('final_cash'))} |
| 持仓市值 | {fmt(o.get('position_value'))} |
| 当日盈亏 | {pnl_str} |
| 当日收益率 | {ret_str} |
| 最大回撤 | {pct(o.get('max_drawdown_pct'))} |
| 当前回撤 | {pct(o.get('current_drawdown_pct'))} |
| 持仓数量 | {fmt(o.get('open_positions'))} |

### 持仓明细

| 标的 | 数量 | 标记价 | 市值 |
|------|------|--------|------|
"""
    positions = o.get('positions', {})
    marks = o.get('marks', {})
    if positions:
        for sym, qty in sorted(positions.items()):
            price = marks.get(sym, 0)
            value = qty * price
            md += f"| {sym} | {qty} | {price:.4f} | {value:.2f} |\n"
    else:
        md += "| — | — | — | — |\n"

    # Live metrics cross-check
    if l:
        md += f"""
### 实时指标交叉验证（localhost:9101/metrics）

| 指标 | 实时值 | 账本值 | 差异 |
|------|--------|--------|------|
| ta_equity | {fmt(l.get('ta_equity'))} | {fmt(o.get('final_equity'))} | {fmt((l.get('ta_equity') or 0) - (o.get('final_equity') or 0))} |
| ta_cash | {fmt(l.get('ta_cash'))} | {fmt(o.get('final_cash'))} | {fmt((l.get('ta_cash') or 0) - (o.get('final_cash') or 0))} |
| ta_drawdown_pct | {pct(l.get('ta_drawdown_pct'))} | {pct(o.get('current_drawdown_pct'))} | — |
| ta_open_positions | {fmt(l.get('ta_open_positions'))} | {fmt(o.get('open_positions'))} | {fmt((l.get('ta_open_positions') or 0) - (o.get('open_positions') or 0))} |
| ta_daily_pnl_pct | {pct(l.get('ta_daily_pnl_pct'))} | {pct(o.get('return_pct'))} | — |

"""

    win_rate_str = pct(t.get('win_rate_pct')) if t.get('win_rate_pct') is not None else '—'
    md += f"""## 2. 交易明细统计

| 项目 | 数值 |
|------|------|
| 成交笔数 | {t.get('fill_count', 0)} |
| 买入成交 | {t.get('buy_fills', 0)} |
| 卖出成交 | {t.get('sell_fills', 0)} |
| 总手续费 | {t.get('total_fees', 0):.4f} |
| 已实现盈亏 | {t.get('realized_pnl', 0):+.4f} |
| 已平仓交易数 | {t.get('closed_trades', 0)} |
| 胜率 | {win_rate_str} |

"""

    md += "## 3. 风控与告警事件\n\n"
    if r.get('kill_switch_triggered'):
        md += "**⚠️ Kill switch 已触发"
        if r.get('kill_reason'):
            md += f"：{r['kill_reason']}"
        md += "**\n\n"

    md += f"""| 项目 | 数值 |
|------|------|
| 风控拦截次数 | {r.get('blocked_count', 0)} |
| risk 事件数 | {r.get('risk_events', 0)} |
| 告警总条数 | {a.get('total', 0)} |

### 拦截原因 Top 榜

"""
    if r.get('blocked_reasons'):
        md += "| 原因 | 次数 |\n|------|------|\n"
        for reason, cnt in sorted(r['blocked_reasons'].items(), key=lambda x: -x[1]):
            md += f"| {reason} | {cnt} |\n"
    else:
        md += "当日无 blocked 记录。\n"

    md += "\n### 告警级别分布\n\n"
    if a.get('by_level'):
        md += "| 级别 | 次数 |\n|------|------|\n"
        for level, cnt in sorted(a['by_level'].items(), key=lambda x: -x[1]):
            md += f"| {level} | {cnt} |\n"
    else:
        md += "当日无告警。\n"

    md += "\n### 告警标题分布\n\n"
    if a.get('by_title'):
        md += "| 标题 | 次数 |\n|------|------|\n"
        for title, cnt in sorted(a['by_title'].items(), key=lambda x: -x[1]):
            md += f"| {title} | {cnt} |\n"
    else:
        md += "—\n"

    md += f"""
## 4. 运行稳定性

| 项目 | 数值 |
|------|------|
| 日志行数 | {s.get('log_lines', 0)} |
| Traceback 次数 | {s.get('traceback_count', 0)} |
| Exception 次数 | {s.get('exception_count', 0)} |
| 网络错误次数 | {s.get('network_error_count', 0)} |
| 断连/重连次数 | {s.get('reconnect_count', 0)} |
| SSL 错误次数 | {s.get('ssl_errors', 0)} |
| HTTP 错误次数 | {s.get('http_errors', 0)} |
| CRITICAL 次数 | {s.get('critical_count', 0)} |
| Kill switch 提及 | {s.get('kill_switch_mentions', 0)} |

### 启动/重启时间线

"""
    if p.get('first_start_utc'):
        md += f"- 首次启动：{p['first_start_utc']}\n"
    if p.get('last_start_utc'):
        md += f"- 末次启动：{p['last_start_utc']}\n"
    venues = p.get('venues_active', {})
    if venues:
        md += f"- 活跃 venues：{', '.join(f'{k}({v} 次启动)' for k, v in venues.items())}\n"

    # Stability narrative
    issues = []
    if s.get('traceback_count', 0) > 0:
        issues.append(f"检测到 {s['traceback_count']} 次 traceback（主要为 SSL/HTTP 连接异常）")
    if s.get('network_error_count', 0) > 0:
        issues.append(f"网络错误 {s['network_error_count']} 次")
    if p.get('restarts_today', 0) > 0:
        issues.append(f"进程重启 {p['restarts_today']} 次")
    if r.get('blocked_count', 0) > 0:
        issues.append(f"风控拦截 {r['blocked_count']} 次")
    if a.get('total', 0) > 0:
        issues.append(f"告警 {a['total']} 条")
    if r.get('kill_switch_triggered'):
        issues.append("Kill switch 已触发")

    if issues:
        md += "\n发现的问题：\n" + "\n".join(f"- {i}" for i in issues) + "\n"
    else:
        md += "\n运行稳定，未发现明显异常。\n"

    md += """
## 5. 明天需要关注的问题与改进建议

"""
    suggestions = []
    if o.get('open_positions', 0) == 0 and o.get('final_cash', 0) and o.get('final_equity', 0) and o.get('final_cash', 0) < o.get('final_equity', 0) * 0.9:
        suggestions.append(
            f"当前现金仅 {fmt(o.get('final_cash'))}，权益 {fmt(o.get('final_equity'))}，"
            "绝大多数资金未参与交易（unmanaged）。建议检查仓位/风险预算配置，确认 `cash_limit` 或 `max_heat` 是否设置过保守。"
        )
    if s.get('ssl_errors', 0) > 0:
        suggestions.append(
            f"日志出现 {s.get('ssl_errors')} 次 SSL 连接异常（`UNEXPECTED_EOF_WHILE_READING` / `MaxRetryError`）。"
            "建议为 OKX API 调用增加指数退避重试，并在 `config/` 中检查网络超时设置；如频繁出现可考虑启用请求会话 keep-alive。"
        )
    if p.get('restarts_today', 0) > 3:
        suggestions.append(
            f"当日进程重启 {p.get('restarts_today')} 次，需排查启动参数/配置热重载逻辑，"
            "避免盘中因策略加载失败导致服务抖动。"
        )
    if o.get('max_drawdown_pct', 0) > 5:
        suggestions.append(
            f"最大回撤 {pct(o.get('max_drawdown_pct'))} 超过 5%，建议复核风控阈值与止损参数。"
        )
    if r.get('blocked_count', 0) > 0:
        suggestions.append(
            f"风控拦截 {r.get('blocked_count')} 次，主要原因为：{', '.join(r.get('blocked_reasons', {}).keys())}。"
            "建议根据规则调整头寸或频率限制。"
        )
    if not suggestions:
        suggestions.append("暂无特别需要关注的问题，建议持续监控网络稳定性与资金利用率。")

    for i, sug in enumerate(suggestions, 1):
        md += f"{i}. {sug}\n"

    md += f"""
---
报告生成时间：{datetime.now(timezone(timedelta(hours=8))).isoformat()}
数据来源：data/ledger*.jsonl、logs/ta.jsonl、logs/alerts.jsonl、logs/ta-run-{date.replace('-', '')}.log、archive/*{date.replace('-', '')}*
"""
    return md


if __name__ == '__main__':
    report = analyze()
    # Save JSON snapshot
    json_path = WORKSPACE / 'reports' / f'{DATE}-review.json'
    with open(json_path, 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    md = render_markdown(report)
    md_path = WORKSPACE / 'reports' / f'{DATE}-review.md'
    with open(md_path, 'w') as f:
        f.write(md)

    print(json.dumps({
        'json_path': str(json_path),
        'md_path': str(md_path),
        'summary': {
            'final_equity': report['overview']['final_equity'],
            'return_pct': report['overview']['return_pct'],
            'max_drawdown_pct': report['overview']['max_drawdown_pct'],
            'fill_count': report['trading']['fill_count'],
            'blocked_count': report['risk']['blocked_count'],
            'alive': report.get('live_metrics', {}).get('_alive'),
            'pid': report.get('live_metrics', {}).get('_pid'),
            'kill_switch': report['risk']['kill_switch_triggered'],
        }
    }, ensure_ascii=False, indent=2))
