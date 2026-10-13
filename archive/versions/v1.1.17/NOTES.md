# v1.1.17

Frozen 2026-10-13T17:43:58+08:00. Package version 1.1.17. Tests: 443.

先手动跑一遍新的四帧刷新（按帧 52/52/52/0、0 失败、约 95 秒），行情地平线从 15m 10-08 变成 15m/1h/4h 都到 10-13；但门槛 1 的数字全变了（trend_breakout −1.70%→−1.12%、trend_pullback 0 笔→14 笔、perp_trend_funding −1.61%→−2.02%），因为切点=序列长度×70%，序列变长则样本外窗口整段平移。结论不变（仍 0/2）。edge.json 与报告新增「样本外起」列，并写明不同策略的样本外长度差一个数量级（1h≈2 个月 / 15m≈7 天）。443 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
