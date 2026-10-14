"""把「每日任务某一轮红了」分成**已知外部条件**与**我们自己的缺陷**（2026-10-14）。

为什么需要它：门槛 4（数据管道连续 14 天两轮全绿）与门槛 3 曾经得的是同一种病 ——
只要把**网络抖动**也当成"红轮"，那条连击就**永远不可能**满足（门槛 3 当年实测：8 天里 8 天在抖）。
门槛 3 后来改成分桶（已知外部条件不计入打断），门槛 4 还没做这件事。

分类的判据只有一条：**这条失败还会不会再发生** ——

* `external`：重试能过的网络/场馆抖动（NetworkError / 限流 / 5xx / 超时 / SSL / 连接重置）；
* `own`：我们自己的缺陷（检查类脚本 rc=1、缺 key、参数错、字段口径错）；
* `unknown`：读不出原因 —— **按 own 处理**（保守：不知道就不许豁免）。

**不猜**：分类只看失败文本里的特征串，命不中就落到 `unknown`。
"""

from __future__ import annotations

EXTERNAL_MARKERS = (
    "NetworkError", "URLError", "ConnectionError", "RemoteDisconnected",
    "ConnectionResetError", "IncompleteRead", "SSLError", "SSL:",
    "TimeoutError", "timed out", "timeout", "Too many requests", "RateLimit",
    "Service Temporarily Unavailable", "Bad Gateway", "Gateway Time-out",
    "HTTP Error 429", "HTTP Error 500", "HTTP Error 502", "HTTP Error 503",
    "HTTP Error 504", "ExchangeNotAvailable", "RequestTimeout",
    "temporarily unavailable", "venue call failed", "重试", "拿不到",
)

# 这些**看着像外部，其实是我们自己的问题**：先判它们，避免被上面的宽模式吃掉。
OWN_MARKERS = (
    "is not set", "not configured", "未配置", "missing key",
    "rc=1", "No such file", "KeyError", "AttributeError", "SyntaxError",
    "TypeError", "ValueError", "AssertionError", "does not match", "不一致",
)


def classify_failure(text: str | None) -> tuple[str, str]:
    """返回 `(class, why)`：`external` / `own` / `unknown`，以及命中的特征。"""
    blob = str(text or "")
    if not blob.strip():
        return "unknown", "没有失败文本"
    for marker in OWN_MARKERS:
        if marker in blob:
            return "own", marker
    for marker in EXTERNAL_MARKERS:
        if marker in blob:
            return "external", marker
    return "unknown", "没有已知特征"


def classify_run(needs_rerun: list[str], failures: dict | None) -> dict:
    """一轮的判定：`{external_only, classes, own, external, unknown}`。

    `external_only=True` 只在"**所有**失败步骤都是外部条件"时成立 ——
    只要有一条是我们自己的缺陷（或读不出原因），这一轮就不能被豁免。
    """
    failures = failures or {}
    classes: dict[str, str] = {}
    whys: dict[str, str] = {}
    for name in needs_rerun or []:
        entry = failures.get(name) or {}
        text = entry.get("text") if isinstance(entry, dict) else str(entry)
        rule, why = classify_failure(text)
        if rule == "unknown":
            rule = "unknown"
        classes[name] = rule
        whys[name] = why
        failures[name] = {"class": rule, "why": why, "text": str(text or "")[-600:]}
    counts = {kind: sum(1 for v in classes.values() if v == kind)
              for kind in ("external", "own", "unknown")}
    external_only = bool(needs_rerun) and counts["own"] == 0 and counts["unknown"] == 0
    return {"external_only": external_only, "classes": classes, "whys": whys, **counts}
