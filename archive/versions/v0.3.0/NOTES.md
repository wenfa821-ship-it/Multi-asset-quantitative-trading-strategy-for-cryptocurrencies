# v0.3.0

Frozen 2026-10-09T21:21:33+08:00. Package version 0.2.0. Tests: 257.

TradFi 腿通电（第一条策略优化）：新增 tradfi tier 13 标的 + [strategies.trend_breakout_daily] 短 warmup(10/20) + [tiers.tradfi] 全库最紧档位 + universe 加 1d timeframe。实测订单往返 21bps（XMU 最小额往返）。对照基线 v0.2.0：这是第一条真正改变交易行为的策略改动。注意：该策略回测 28 笔/-0.14%/PF 0.34，启用目的是按用户要求承风险收执行数据，不是已证实的边缘。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
