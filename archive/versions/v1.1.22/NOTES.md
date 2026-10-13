# v1.1.22

Frozen 2026-10-13T19:11:42+08:00. Package version 1.1.22. Tests: 453.

换腿规则的单位错了：daily_bps_series 一律按 3 次/天（8h）算，而场馆对 4h 合约是 6 次/天 —— 持仓腿 ACT 被低估一半（1.500 → 真实 3.000），而它原本是离 1.0 floor 最近的那条腿（差 0.5），即这个 bug 会推动一次不必要的换腿（44.9 bps）。新增 detect_settlements_per_day（中位间隔，最近 100 个，钳 [0.5,24]），两侧逐标的用自己那份；加「与 carry_live 同解」测试防分叉。候选池 139→136 未被打瘸。453 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
