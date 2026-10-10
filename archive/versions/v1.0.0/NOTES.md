# v1.0.0

Frozen 2026-10-10T11:58:24+08:00. Package version 1.0.0. Tests: 290.

第一阶段封板。范围：四腿系统（现货/永续/carry/TradFi）接入实盘配置并运行 + 四道选标闸门（funding/深度/基差/成本）落在下单路径 + 对账（永续逐腿 + cashBal 口径）+ funding 入账 + 数据管道（每日任务含历史刷新、期权数据层、可交易性记录、版本/进度校验）+ 发布管理（VERSION/CHANGELOG/RELEASING/归档/git）。运行态：五对 carry 全部对冲（CRV/ACT/LTC/SUI/NEAR）、权益约 $101.6k、290 tests 全绿。期权策略与期权腿按用户决定移入 1.1（门槛：IV−RV 满 5–7 天新鲜样本且符号稳定）。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
