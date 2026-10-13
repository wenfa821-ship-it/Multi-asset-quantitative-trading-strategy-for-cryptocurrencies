# v1.1.2

Frozen 2026-10-13T08:20:38+08:00. Package version 1.1.2. Tests: 402.

两次复盘之间积累的 40 笔提交，全部是修复/观测/工具（无新腿、无策略参数变更）：① 资金费口径分清「账单（模拟盘费率，实测是公开费率 200~450 倍）」与「公开费率 × 持仓名义（唯一可引用）」，复盘新增补记行 / 实盘费率口径行 / **可复制部分（下界）**一行；② 现金流与对账：期望线跟着资金费走（残余稳定 ~$1,05x）、换口径不算漂移、历史旗标按 24h 新鲜度分级；③ 前置的执行闸门 config/preconditions.json + scripts/preconditions.py --check（文档里划掉的已结案不算判据，代价是一次 $0.27 的重跑）；④ 看板：期权腿面板、新币面板「对照池点名」、网络降级改按最近 24h 计数（>5 次/24h 才 warn）；⑤ 工具：changelog_md 按解析后的时间比较、version_snapshot 拒绝冻结红着的树。测试 402 绿。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
