# v0.4.4

Frozen 2026-10-10T09:53:13+08:00. Package version 0.2.0. Tests: 283.

断网三级韧性修复：connect() 不再因 load_markets 失败而退出（此前启动即崩 → KeepAlive 每 10 秒重启 → 142 次启动横幅）；_markets_loaded 派生自 ccxt（自愈）；prime() 连续 5 个标的全失败提前返回且不抛；refresh() 连续 5 个失败整 pass 判 degraded（此前一个 pass 磨 42 分钟）。+5 测试（283 全绿）。config 与 v0.4.3 完全相同。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
