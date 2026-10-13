# v1.1.18

Frozen 2026-10-13T18:23:35+08:00. Package version 1.1.18. Tests: 443.

把 15m 历史从 23 天补深到 179 天（16 个 15m 消费方标的，各 17,280 根，2 分 59 秒；走合并不覆盖）。门槛 1 从「0/2 条可判策略为正」变成「0/4」：原来判不了的两条（trend_pullback 0→88 笔、mean_reversion 14→216 笔）现在带着 54~58 天的样本外窗口，全部为负。这不是结论变了，而是原来那一半没被测过。代价：回测 25→109 秒、磁盘 +32MB。443 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
