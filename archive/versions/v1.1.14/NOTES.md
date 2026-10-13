# v1.1.14

Frozen 2026-10-13T17:10:53+08:00. Package version 1.1.14. Tests: 436.

回测读的 15m/1h 停在 10-08：refresh_daily_bars.py 的 --timeframe 默认 1d 且每日任务没传参，所以 v1.1.5 只解冻了日线 —— 门槛 1 的「样本外」表其实在看 5 天前的世界（52 个配置标的：15m 全部 10-08、1h 20 个 09-29、1d 全部 10-12）。修法：每日任务改为 --timeframe 15m,1h,4h,1d --bars 400（脚本接受逗号列表并分帧报数，不再把短板藏进总数）；edge.json 记录 input_coverage，门槛 1 打印行情地平线并在 >1 天时告警。另更正 §70.10 里「独立复现」的说法（同一份冻结输入）。436 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
