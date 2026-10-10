# v0.4.3

Frozen 2026-10-10T09:25:22+08:00. Package version 0.2.0. Tests: 278.

看门狗新增『交易循环停摆』判据：runner_stale_minutes=15.0，按账本最新 equity 事件时间戳判活，超时 critical；阈值取 15 覆盖最慢 12 分钟的预热。+3 测试（278 全绿）。已在 2026-10-10 代理上游中断事故中实时验证（看板报 25.50 分钟 critical）。config 与 v0.4.2 完全相同；本轮只重启只读看板。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
