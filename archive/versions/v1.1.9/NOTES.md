# v1.1.9

Frozen 2026-10-13T16:13:38+08:00. Package version 1.1.9. Tests: 425.

第 3 条门槛的第三处不可满足：带 position 就算非结构性，会把 BTC 库存实验（tag 余额，实测 19:10 买 0.00120705 / 20:00 卖 0.0001 / 21:36 场馆 0.00110705 完全吻合）与两侧皆零的灰尘一起算成事故。改为三桶（库存窗口 4 / 灰尘 3 / 未解释 14），只有未解释打断连击；口径写死为「豁免只给已知外部条件与自己的记账口径，缺陷只随时间退出」。读数 1/30 → 3/30 天，最早 11-11 → 11-09。425 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
