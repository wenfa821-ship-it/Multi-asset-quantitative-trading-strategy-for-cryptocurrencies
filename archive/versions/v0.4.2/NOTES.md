# v0.4.2

Frozen 2026-10-09T23:23:14+08:00. Package version 0.2.0. Tests: 275.

权益口径统一：新增 portfolio.marked_value（现货名义 + 永续未实现盈亏），PositionBook.equity 与 runner._prepare_risk_account 共用同一实现，修掉『开一对新 carry 被风控当成日亏 -2% 并暂停入场、名义再大会触发 flatten_all』的定时炸弹，并把 sizing 基数从低估 4.7% 纠正回真实值。新增 2 条测试（275 全绿），含『开对冲腿不改变 risk_equity』回归。config 与 v0.4.1 完全相同。遗留：Position.unrealized 未乘 ctVal（ACT 盈亏被低估 10 倍，已单列）。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
