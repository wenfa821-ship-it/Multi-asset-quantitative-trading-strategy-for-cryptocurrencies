# v1.1.0

Frozen 2026-10-11T13:27:27+08:00. Package version 1.1.0. Tests: 356.

1.1 = 期权腿 venue 适配 + 数据层 + 实测约束（说明书 §4 第 7 条）。策略未接入：这套模拟环境买不到期权——市价被拒(50016)、按卖一价被价格带拒(51006 max buy 0.0245/min sell 0.0001)、挂买价 25 分钟无人成交(README §69)。顺带修掉新进程 clOrdId 撤单失败与 unknown≠cancelled。356 tests。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
