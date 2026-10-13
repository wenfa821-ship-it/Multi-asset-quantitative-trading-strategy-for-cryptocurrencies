# v1.1.15

Frozen 2026-10-13T17:16:53+08:00. Package version 1.1.15. Tests: 438.

追查 15 个「有合约、无资金费记录」的标的：带 demo 头问场馆它们是 live，但本脚本刻意取主网公共费率，主网对它们返回空 → 口径上无解、不是 bug（代价是这些名字进不了 carry 排序）。同时更正：APT/TIA 冻在 09-30 的真因是模拟盘下架了这两个永续，不是我两天前说的抓取静默失败。顺手把日更摘要的 updated/unchanged/failed 拆出 no-data（'unchanged' 原来把『取不到』也算进去了），并列出 no-data 名单。438 测试绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
