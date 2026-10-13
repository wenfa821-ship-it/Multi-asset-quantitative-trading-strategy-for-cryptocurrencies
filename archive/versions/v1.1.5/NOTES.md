# v1.1.5

Frozen 2026-10-13T13:08:21+08:00. Package version 1.1.5. Tests: 407.

研究侧日线不再冻结：refresh_daily_bars 新增 universe 模式（配置里全部标的 + 期权标的），并接进每日任务 —— 实测 52 个配置标的里 49 个停在 5.5 天前，而 refit / daily_study 读的正是这些文件。手工跑一次refreshed 49/52 各 +7 根日线，正好补上缺口。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
