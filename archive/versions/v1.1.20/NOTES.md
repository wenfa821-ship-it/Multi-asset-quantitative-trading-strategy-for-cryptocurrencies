# v1.1.20

Frozen 2026-10-13T18:44:24+08:00. Package version 1.1.20. Tests: 446.

ta doctor 把「在跑的策略」（6 条，enabled in config）与「已实现但故意关掉的」（perp_trend_funding：swap 适配未做 + 无真实资金费历史 ⇒ 闸门会敞开；random_control：仅存于 config-exp 的随机对照）分开印，并打印实际使用的 config-dir。查证结论：8 vs 6 不是配置漂移，但歧义有真实成本（我自己查了几分钟）。新增 2 个测试（含「关闭理由必须留在配置里」）。446 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
