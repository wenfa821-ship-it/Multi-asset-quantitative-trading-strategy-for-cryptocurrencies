# v1.1.10

Frozen 2026-10-13T16:38:32+08:00. Package version 1.1.10. Tests: 427.

换腿规则「0 次触发」查清：规则没坏（最接近的是 LTC 3 天里 2/3 低于 floor，被 10-11 的 1.508 bps/天挡住），离线 --days 2 能出提议（LTC→TRIA, breakeven 4d）。但查出两把尺子不同长：现任是 --days 3 中位、候选是 screen_live.json 的 ≈17 天中位（samples=100×4h），TRIA 最近 5 天实测仅 5.2 bps/天（快照 11.933），折算回本 ≈9 天；且 TRIA depth_ok=false，规则在提议一个进不去的名字。只报数不动规则，已登记 carry-rotation-bench 待决定。427 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
