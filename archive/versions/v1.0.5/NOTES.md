# v1.0.5

Frozen 2026-10-10T21:37:23+08:00. Package version 1.0.5. Tests: 336.

1.0.5 补丁（执行路径/韧性）：行情刷新加 120 秒墙钟预算 —— 慢场馆（每次重试才成功）不再能把一个 tick 拖成 86 分钟；超预算即按既有 VenueDegraded 路径降级。336 tests。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
