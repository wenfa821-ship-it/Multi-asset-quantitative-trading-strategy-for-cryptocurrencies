# v1.1.8

Frozen 2026-10-13T16:08:38+08:00. Package version 1.1.8. Tests: 422.

门槛数字化的第二轮：查出「网络降级按 critical 记账 → 第 3 条永远不可能满足」（25 次/8 天），改为单独计数；第 4 条的服务日志有 5 个红轮且无时间戳 → 报告明说「补不出历史」；21 条拒单按 sCode 分类并逐类核实是否还会复发（51000/51008/dust/54092 之后均 0 条）；核过没有幽灵腿（unmanaged 4.17e-05）。422 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
