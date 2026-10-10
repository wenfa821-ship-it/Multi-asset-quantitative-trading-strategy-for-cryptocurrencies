# v0.4.5

Frozen 2026-10-10T10:42:47+08:00. Package version 0.2.0. Tests: 286.

永续盈亏补上合约面值：Position.ct_val（默认 1.0）+ unrealized 乘面值；CcxtVenue.contract_size() 并用于 get_account；runner 重建资金簿后为永续持仓补齐 ct_val。ACT（ctVal 10）盈亏不再被低估 10 倍；现货默认 1.0 零影响。+3 测试（286 全绿）。config 与 v0.4.4 相同。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
