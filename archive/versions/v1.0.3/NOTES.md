# v1.0.3

Frozen 2026-10-10T16:17:12+08:00. Package version 1.0.3. Tests: 316.

1.0.3 补丁（观测/工具）：把每日任务的补跑清单接进看板（services.daily_job + warn 红旗），补上 v1.0.2 只写不读的那半句；同轮新增 1.2 换腿规则的检测侧（scripts/carry_rotation.py，只读）并接进每日任务。带 launchd 同款环境实测 14/14 步首跑全绿。316 tests。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
