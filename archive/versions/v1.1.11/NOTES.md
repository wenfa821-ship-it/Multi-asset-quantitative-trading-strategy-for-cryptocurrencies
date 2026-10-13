# v1.1.11

Frozen 2026-10-13T16:42:03+08:00. Package version 1.1.11. Tests: 428.

门槛 1 补上按腿清点：原文是「至少一条腿/策略」，实现只量了策略那一半，carry/TradFi/期权三条腿原本不在答案里。补全后可见 carry 是唯一扣成本后为正的腿（30 天净 +22.4 bps，已扣 44.9 往返）但只有 6/30 天样本 —— 即第 1 条与第 5 条共用同一个 30 天钟；期权腿的「成交不了」改为引用 data/options/round_trip.jsonl 的 4 次真实失败记录。428 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
