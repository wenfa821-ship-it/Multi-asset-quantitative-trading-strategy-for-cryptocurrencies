# v1.1.13

Frozen 2026-10-13T16:55:17+08:00. Package version 1.1.13. Tests: 434.

门槛 1 自己的测量不再被降级运行顶掉：--backtest 原来无条件覆盖 edge.json，五条回放全挂时好数据会变成五个 None 而门槛照旧输出 0/2。现在一条数字都没跑出来时保留上一份、打印原因、rc=1（每日任务记 needs_rerun）。实测 --strategies no_such_strategy_xyz 得 rc=1 且 edge.json sha1 前后一致；门槛 1 另加「测量超过 3 天没更新」的自检警告。按每日任务方式复跑 --write --backtest 得 rc=0。434 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
