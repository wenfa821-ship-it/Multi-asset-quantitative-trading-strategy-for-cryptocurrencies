# v0.4.0

Frozen 2026-10-09T22:37:06+08:00. Package version 0.2.0. Tests: 268.

carry 被动入场（第一条改变**执行方式**的策略改动）：现货腿先挂 post-only 于买一 → 等 6s → 撤单并确认终态 → 未成交才回退市价。撤单无法确认时拒绝回退（现货腿翻倍风险）。新增 OMS.cancel / OMS+venue.open_order_ids（None=查不到）/ paired._passive_spot / runner._passive_entry_price；config 新增 passive_entry=true、passive_wait_seconds=6.0。对照 v0.3.x：这是 carry 第一次不再无条件市价入场，目标是把单腿 12.6-72.6 bps 的入场价差降到 0。真实交易所验证：cancelSource=31（被交易所杀掉，零成交）与 cancelSource=1（我们撤的）。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
