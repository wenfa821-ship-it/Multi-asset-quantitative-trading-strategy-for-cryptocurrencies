# v1.1.3

Frozen 2026-10-13T11:03:21+08:00. Package version 1.1.3. Tests: 404.

新上币采集器不再静默跳过一整类：筛选抽成纯函数 + --categories/--store，并在 added 0 时报出被口径挡掉的分类计数（实测近 8 天 32 个 USDT 上市、8 个成熟的股票代币一个都没记）。顺带给 TradFi 腿另立一本账 data/new_listings/tradfi_records.jsonl（cat=3、60 天窗）并接进每日任务：首入库 48 条，48h 冲高 0/43、中位 +1.98% —— 与加密新币（25%、+8.89%）完全是两种曲线。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
