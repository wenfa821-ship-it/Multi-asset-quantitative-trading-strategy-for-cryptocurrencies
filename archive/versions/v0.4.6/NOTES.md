# v0.4.6

Frozen 2026-10-10T11:46:09+08:00. Package version 0.2.0. Tests: 290.

按用户决定扩名额 + 修掉引擎的 tier 持仓计数缺陷：spot/derivatives 上限 3→5、2→5（每日笔数 3→5）、account 12→17（每日笔数 14→16）；carry tier max_positions 5→10（引擎数持仓，一对=两条腿）；RiskEngine 的 tier 上限改为只计本 tier 持仓（此前拿全账户持仓数去比，锁死了 TradFi 腿与所有方向性入场）。效果：重启后 SUI 与 NEAR 两对在同一 tick 开成，账面 5 对逐腿对冲相等。+2 测试（290 全绿）。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
