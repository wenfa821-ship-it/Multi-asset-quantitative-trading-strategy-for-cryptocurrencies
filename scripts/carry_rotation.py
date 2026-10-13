#!/usr/bin/env python3
"""Which open carry pair has stopped paying, and what would replacing it cost?

Why this exists (1.2)
---------------------
Carry's five slots are full (spot 5/5), so rotation is the only way a new name can
ever be entered - and rotation is not free: the measured two-leg round trip is
**44.9 bps**, against a median funding of ~1-2 bps/day. That makes "is it paying
enough to keep" a question with a number attached, and it is deliberately NOT the
same question as "which name has the highest rate today":

  * a pair is flagged only when its funding is below the floor on EVERY one of the
    last N days (a single bad print is exactly the churn that cost 31 fills and
    $284 of fees on the first day of live carry),
  * a replacement has to clear the round trip inside the intended hold, i.e.
    breakeven_days = 44.9 / (candidate_bps - current_bps) must be < 30,
  * a pair younger than `min_hold_hours` is never a candidate, however bad the
    number looks.

Read-only. It decides nothing and sends nothing: it prints (and optionally
records) what the rule WOULD do. Acting on it is a separate, user-approved step.

    python3 scripts/carry_rotation.py
    python3 scripts/carry_rotation.py --days 3 --floor-bps 1.0 --write
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import statistics
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from ta.config import load_env_file  # noqa: E402

DEFAULT_ROUND_TRIP_BPS = 44.9


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def daily_bps_series(prints: list[dict], days: int, settlements_per_day: float = 3.0,
                     ) -> list[tuple[str, float, int]]:
    """Per-UTC-day median funding in bps/DAY, for the last `days` days with prints.

    Median of the day's prints x prints/day, not the day's mean: one 20x spike is
    exactly what made the first carry day churn, and the venue's own mean is the
    number that lies about it.
    """
    buckets: dict[datetime.date, list[float]] = {}
    for row in prints:
        ts = row.get("ts")
        rate = row.get("rate")
        if ts is None or rate is None:
            continue
        day = datetime.datetime.fromtimestamp(float(ts) / 1000, datetime.UTC).date()
        buckets.setdefault(day, []).append(float(rate))
    ordered = sorted(buckets.items())[-max(1, days):]
    return [
        (day.isoformat(), round(statistics.median(rates) * settlements_per_day * 10000, 3), len(rates))
        for day, rates in ordered
    ]


def detect_settlements_per_day(prints: list[dict], *, window: int = 100,
                               fallback: float = 3.0) -> float:
    """每标的的**实际**结算频率（次/天），从印之间的间隔量出来。

    2026-10-13 实测：`daily_bps_series` 一直把 `settlements_per_day` 固定成 **3.0**（8 小时），
    而场馆对一部分合约是 **4 小时结算（6 次/天）** —— 持仓腿里的 **ACT 就是 6/天**，
    于是它的 bps/天被**系统性低估一半**；候选里的 CAP 同样是 6/天。
    后果是不对称的：被低估的腿看起来更接近「死掉」（更容易被判 STALE 换掉），
    而被低估的候选看起来更不值钱（该换的不换）。这与 §70.9 查出的「bps_per_day 少除窗口天数」
    是同一类口径错：**单位搞错了，比较就不成立**。

    用中位间隔而不是均值：漏抓几轮会留下大空洞，均值会被它拖走，中位数不会。
    """
    stamps = sorted(float(r["ts"]) for r in prints if isinstance(r.get("ts"), (int, float)))
    if len(stamps) < 3:
        return fallback
    gaps = [(b - a) / 3_600_000.0 for a, b in zip(stamps, stamps[1:]) if b > a]
    gaps = gaps[-window:]
    if not gaps:
        return fallback
    gap_hours = statistics.median(gaps)
    if gap_hours <= 0:
        return fallback
    return round(min(24.0, max(0.5, 24.0 / gap_hours)), 4)


def is_persistently_below(series: list[tuple[str, float, int]], floor_bps: float, days: int) -> bool:
    """True only when EVERY day in the window is below the floor.

    `len(series) == days` matters: a leg with two days of history has not been
    persistently anything yet.
    """
    return len(series) == days and all(bps < floor_bps for _day, bps, _n in series)


def open_since(fills: list[dict], symbol: str, dust: float = 1e-9) -> int | None:
    """When the CURRENT holding streak for `symbol` started (ms), from fills alone.

    The pair state file records the pair, not its age, and the ledger's fills carry
    no pair id - so age is reconstructed the only way it can be: walk the symbol's
    fills in order and remember the first one after the running position last
    returned to (approximately) zero.
    """
    qty = 0.0
    start: int | None = None
    for fill in sorted((f for f in fills if f.get("symbol") == symbol), key=lambda f: f["ts"]):
        size = float(fill.get("qty") or 0.0)
        qty += size if fill.get("side") == "buy" else -size
        if abs(qty) <= dust:
            start = None
        elif start is None:
            start = int(fill["ts"])
    return start


def breakeven_days(round_trip_bps: float, gain_bps_per_day: float) -> int | None:
    """Days for a switch to pay back its round trip. None when it never would."""
    if gain_bps_per_day <= 0:
        return None
    return int(math.ceil(round_trip_bps / gain_bps_per_day))


def screen_rows(screen: dict) -> list[dict]:
    rows = screen.get("rows") if isinstance(screen, dict) else screen
    out = []
    for row in rows or []:
        symbol = str(row.get("symbol") or "")
        daily = row.get("median_daily_bps", row.get("daily_bps"))
        if symbol and daily is not None:
            out.append({"symbol": symbol, "bps": float(daily),
                        "screen_bps": float(daily),
                        "eligible": row.get("eligible"),
                        "depth_ok": bool(row.get("depth_ok"))})
    return sorted(out, key=lambda r: -r["bps"])


def remeasure_candidates(candidates: list[dict], history: Path, days: int,
                         settlements_per_day: float = 3.0) -> list[dict]:
    """候选的 bps 改从**与现任同一把尺子**上重算（本地费率历史，同窗口同口径）。

    2026-10-13（用户批准执行选项 ①）：候选原来读 `screen_live.json` 的 `median_daily_bps`
    —— 那是它最近 `samples` 个印的中位（`samples=100` × 4h 结算 ≈ **17 天**），而现任读的是
    `--days 3` 的中位。两把尺子不一样长，同一个名字能看着比它近期实际支付的好一倍
    （TRIA：快照 11.933 vs 最近 5 天 5.2）。现在两边都用 `daily_bps_series(...)` 的
    同窗口中位；快照那份值只留作对照（`screen_bps`）。

    历史不齐（不足 `days` 天）的候选直接丢掉：宁可少一个候选，也不拿"最近 N 个有印的日子"
    冒充"最近 N 天" —— 那是研究侧日线冻结那一课的同一个形状。深度（`depth_ok`）仍来自快照，
    但在 `plan()` 里只在快照足够新时才作为过滤条件（旧快照的 depth 字段本身也可能过期）。
    """
    out: list[dict] = []
    for cand in candidates:
        base = cand["symbol"].split("/")[0]
        prints = load_jsonl(history / f"{base}-USDT_USDT.jsonl")
        # 每个标的用自己的实际结算频率（4h 合约是 6/天，不是 3/天）—— 见 detect_settlements_per_day
        series = daily_bps_series(prints, days, detect_settlements_per_day(prints))
        if len(series) < days:
            continue
        out.append({**cand, "series": series,
                    "settles_per_day": detect_settlements_per_day(prints),
                    "bps": round(statistics.median([bps for _d, bps, _n in series]), 3)})
    return sorted(out, key=lambda r: -r["bps"])


def plan(held: dict[str, dict], history: Path, candidates: list[dict], fills: list[dict],
         *, days: int, floor_bps: float, round_trip_bps: float, min_hold_hours: float,
         intended_hold_days: float, settlements_per_day: float | None = None,
         now_ms: int | None = None,
         screen_generated_at_ms: int | None = None,
         max_history_age_days: float = 2.0,
         max_screen_age_days: float = 2.0) -> dict:
    """What the rule WOULD do.

    Freshness guards (2026-10-13). Found while asking why this rule never fires: it
    reads its candidate list from a `screen_live.json` snapshot that **no scheduled
    job produces** (only a manual `carry_live.py --write ...` run), and 142 funding
    series on disk include two that froze on 2026-09-30. `daily_bps_series` takes the
    last `days` days *with prints*, so a frozen file yields three perfectly good
    looking buckets from two weeks ago - and `is_persistently_below` never checked
    whether they were recent. Silently acting on either would be the same shape as
    the frozen research bars and the stale candidate that overstated TRIA 2x.

    Both sides are therefore age-checked and refused (with the reason recorded)
    rather than silently trusted.
    """
    now = now_ms or int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    held_bases = {symbol.split("/")[0] for symbol in held}
    free = [c for c in candidates if c["symbol"].split("/")[0] not in held_bases]
    report = {"generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
              "days": days, "floor_bps": floor_bps, "round_trip_bps": round_trip_bps,
              "pairs": [], "rotations": [], "refusals": []}

    # 2026-10-13（选项 ②）：快照的年龄现在只决定**深度过滤要不要用**，不再决定能不能出提议 ——
    # 候选的 bps 已改由本地费率历史同窗口重算（见 remeasure_candidates），对快照不再有依赖；
    # 快照过旧时它的 `depth_ok` 同样过时，此时交给 runner 自己的入场深度闸门兜底。
    screen_age_days = None
    depth_filter = False
    if screen_generated_at_ms:
        screen_age_days = (now - float(screen_generated_at_ms)) / 86_400_000.0
        depth_filter = screen_age_days <= max_screen_age_days
        if not depth_filter:
            report["refusals"].append({
                "what": "candidate depth filter",
                "age_days": round(screen_age_days, 2),
                "why": (f"candidate snapshot is {screen_age_days:.1f} days old "
                        f"(limit {max_screen_age_days:g}) - its depth field is stale, so "
                        f"depth is NOT used to filter candidates; the runner's own "
                        f"entry-depth gate is the backstop. Ranking now comes from the "
                        f"local funding history window, not from the snapshot."),
            })

    for symbol in sorted(held):
        base = symbol.split("/")[0]
        prints = load_jsonl(history / f"{base}-USDT_USDT.jsonl")
        # 同上：现任这一侧也逐标的量结算频率，别假设所有腿都是 8 小时结算
        settles = detect_settlements_per_day(prints) if settlements_per_day is None \
            else settlements_per_day
        series = daily_bps_series(prints, days, settles)
        since = open_since(fills, symbol)
        age_hours = round((now - since) / 3_600_000, 1) if since else None
        low = is_persistently_below(series, floor_bps, days)
        row = {"symbol": symbol, "daily_bps": series, "stale": low,
               "settles_per_day": settles,
               "median_bps": round(statistics.median([bps for _d, bps, _n in series]), 3) if series else None,
               "age_hours": age_hours}
        if series:
            last_day = datetime.date.fromisoformat(series[-1][0])
            today = datetime.datetime.fromtimestamp(now / 1000, datetime.UTC).date()
            row["history_age_days"] = (today - last_day).days
            if row["history_age_days"] > max_history_age_days:
                # `low` is what the proposal branch below actually reads - clearing
                # only `row["stale"]` would have left the proposal standing (caught
                # by test_a_frozen_history_is_refused_not_read_as_persistently_below).
                low = False
                row["stale"], row["stale_history"] = False, True
                report["refusals"].append({
                    "what": f"history for {symbol}",
                    "age_days": row["history_age_days"],
                    "why": (f"last funding print is {row['history_age_days']} days old "
                            f"(limit {max_history_age_days:g}) - cannot call it "
                            f"'persistently below' on two-week-old prints"),
                })
        report["pairs"].append(row)
        if not low or (age_hours is not None and age_hours < min_hold_hours):
            continue
        current = row["median_bps"] or 0.0
        for cand in free:
            if depth_filter and not cand.get("depth_ok"):
                continue
            # 快照新鲜时，它自己的鲁棒性过滤（`eligible`，如 positive_share < 80%）也一起用 ——
            # 那一条是"拒绝只靠尖峰撑起来的名字"，与这里的持续性检验同向，没必要丢掉。
            if depth_filter and cand.get("eligible") is False:
                continue
            # 与现任**对称**的持续性检验（2026-10-13 选项 ① 的配套）：现任要"连续 N 天每天都
            # 低于 floor"才算死；候选就必须"连续 N 天每一天都赢够"，否则一个插针日就能把它送上
            # 候选位（同窗口中位数会被尖峰拉着走 —— 实测 NG 的 2 天中位 135 bps/天）。
            # 回本天数用**最差的一天**算，宁可保守：换腿要付 44.9 bps，一次误换就是净支出。
            cand_series = cand.get("series") or []
            if len(cand_series) != days:
                continue
            worst = min(bps for _d, bps, _n in cand_series)
            if worst <= current:
                continue
            be = breakeven_days(round_trip_bps, worst - current)
            if be is None or be > intended_hold_days:
                continue
            report["rotations"].append({
                "from": symbol, "to": cand["symbol"], "current_bps": current,
                "daily_bps": cand_series,
                "worst_day_bps": round(worst, 3),
                # 回本用最差的一天算，所以 `candidate_bps` 与 `gain_bps_per_day` 也是保守值；
                # 同窗中位（`candidate_bps_median`）与快照值一起留痕，便于下一个人核对口径。
                "candidate_bps": round(worst, 3),
                "candidate_bps_median": cand["bps"],
                # 同一把尺子上的数（bps）与快照那份对照（screen_bps），两个都留痕：
                "candidate_bps_screen": cand.get("screen_bps"),
                "depth_ok": cand.get("depth_ok"),
                "gain_bps_per_day": round(worst - current, 3),
                "breakeven_days": be, "age_hours": age_hours,
            })
            break
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="data/carry/pairs.json")
    ap.add_argument("--screen", default="data/carry/screen_live.json")
    ap.add_argument("--history", default="data/history/funding")
    ap.add_argument("--ledger", default="data/ledger.okx_demo.jsonl")
    ap.add_argument("--days", type=int, default=3, help="consecutive days below the floor")
    ap.add_argument("--floor-bps", type=float, default=1.0, help="bps/day median that counts as unpaid")
    ap.add_argument("--round-trip-bps", type=float, default=DEFAULT_ROUND_TRIP_BPS)
    ap.add_argument("--min-hold-hours", type=float, default=24.0)
    ap.add_argument("--intended-hold-days", type=float, default=30.0)
    ap.add_argument("--max-history-age-days", type=float, default=2.0,
                    help="refuse to judge a pair whose last funding print is older than this")
    ap.add_argument("--max-screen-age-days", type=float, default=2.0,
                    help="refuse to propose from a candidate snapshot older than this")
    ap.add_argument("--out", default="data/carry/rotation.jsonl")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    load_env_file(PROJECT / ".env")  # the pair/history files need no venue, but keep parity
    held = load_json(PROJECT / args.pairs, {})
    screen = load_json(PROJECT / args.screen, {})
    # 选项 ①：候选的 bps 从**和现任同一把尺子**上重算（本地费率历史、同窗口），
    # 快照那份只留作对照（screen_bps）—— 它没有定时产出、也不该决定排名。
    candidates = remeasure_candidates(screen_rows(screen), PROJECT / args.history, args.days)
    fills = [f for f in load_jsonl(PROJECT / args.ledger) if f.get("kind") == "fill"]
    report = plan(held, PROJECT / args.history, candidates, fills,
                  days=args.days, floor_bps=args.floor_bps,
                  round_trip_bps=args.round_trip_bps, min_hold_hours=args.min_hold_hours,
                  intended_hold_days=args.intended_hold_days,
                  screen_generated_at_ms=screen.get("generated_at"),
                  max_history_age_days=args.max_history_age_days,
                  max_screen_age_days=args.max_screen_age_days)

    print(f"{'pair':12s}{'median':>9s}{'age_h':>8s}  last {args.days}d bps/day")
    for row in report["pairs"]:
        days_txt = " ".join(f"{bps:g}" for _d, bps, _n in row["daily_bps"]) or "-"
        age = f"{row['age_hours']:g}" if row["age_hours"] is not None else "?"
        flag = " STALE" if row["stale"] else ""
        hist = row.get("history_age_days")
        if hist is not None and hist > args.max_history_age_days:
            flag = f" HISTORY {hist}d OLD"
        print(f"{row['symbol']:12s}{row['median_bps'] if row['median_bps'] is not None else '-':>9}"
              f"{age:>8s}  {days_txt}{flag}")
    for refusal in report["refusals"]:
        print(f"REFUSED {refusal['what']}: {refusal['why']}")
    if report["rotations"]:
        print("\nrotation proposals (read-only):")
        for rot in report["rotations"]:
            print(f"  {rot['from']} -> {rot['to']}: {rot['current_bps']:g} -> {rot['candidate_bps']:g} bps/day, "
                  f"breakeven {rot['breakeven_days']}d (round trip {args.round_trip_bps:g} bps)")
    else:
        print("\nno rotation proposed: no pair is below the floor on every day of the window")

    if args.write:
        path = Path(args.out)
        if not path.is_absolute():
            path = PROJECT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False) + "\n")
        print(f"appended -> {path}")
    else:
        print("DRY RUN - re-run with --write to record the sample")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
