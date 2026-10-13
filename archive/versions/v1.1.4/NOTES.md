# v1.1.4

Frozen 2026-10-13T13:02:36+08:00. Package version 1.1.4. Tests: 406.

TradFi 可交易性采样扩到新上市的股票代币（最近 20 个，--listings-cap）：池子 10-10 收窄到 XMU 后，「场馆哪天开始供数据」只对旧名单成立；新上市才是池子长回来的来源。采样器每天两次都在美股时段内，下一轮盘内运行就会回答。写测试时抓到两个自己的坑：先截后筛吃掉配额、tier 报告缺失时提前 return 静默跳过新上市段。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
