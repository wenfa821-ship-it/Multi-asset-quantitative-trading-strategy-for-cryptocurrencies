# v1.1.16

Frozen 2026-10-13T17:37:52+08:00. Package version 1.1.16. Tests: 442.

scan evaluate 的自诊断「11% 是重复写入，说明有多个扫描器」是历史遗留：被折叠的 7,269 行里 91% 来自 09-29/09-30（多扫描器时期），近 24 小时为 0 —— 这台机器上 ps/pgrep 本来就是假阴性，一句过期自诊断正好凑成每轮一次的自制误报。改成三个可测函数：collapse() 是唯一折叠实现（dedupe_rows 与统计共用）、collapse_stats() 按天归属并单独算近 24h、collapse_note() 只在本轮真有重复时才说多扫描器并给出可执行下一步。442 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
