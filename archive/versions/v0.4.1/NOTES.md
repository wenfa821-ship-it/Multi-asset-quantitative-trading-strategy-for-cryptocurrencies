# v0.4.1

Frozen 2026-10-09T23:10:48+08:00. Package version 0.2.0. Tests: 273.

v0.4.0 + 被动入场致命修复：新增 CcxtVenue.order_state/OMS.order_state（读订单自身记录 state+accFillSz 作为唯一权威），删除『不在挂单列表=已撤销』的启发式，查不到→拒绝市价回退。起因：LTC 第一对真仓把现货买成两倍（约 ,977 裸露 33 分钟，净成本 ~.3）。另含 order_state 两条回归测试（273 tests）与 BAND 裸多处置（已平掉并去掉 seeded 配对记录）。config 与 v0.4.0 完全相同。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
