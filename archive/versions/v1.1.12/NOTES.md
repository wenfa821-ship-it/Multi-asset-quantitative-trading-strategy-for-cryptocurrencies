# v1.1.12

Frozen 2026-10-13T16:51:51+08:00. Package version 1.1.12. Tests: 432.

换腿规则的两个输入都可能悄悄过期：候选快照 screen_live.json 3.2 天前且没有任何定时任务在生产它，资金费历史 142 条里 APT/TIA 冻在 09-30；而 daily_bps_series 取「最后 N 个有印的日子」、is_persistently_below 不看日期，所以冻住的序列照样给出正常形状的桶。加时效护栏（两侧默认 2 天）：过期拒绝出提议、保留每对腿读数、原因写进 refusals 并在门槛报告里显示。测试期间抓到 row['stale'] 与局部 low 不一致导致提议未被拦住的 bug。432 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
