# v0.3.1

Frozen 2026-10-09T21:52:58+08:00. Package version 0.2.0. Tests: 263.

v0.3.0 + 深度闸门修复（sizing.depth_refusal + runner._entry_depth_refused，覆盖所有入场而非仅 carry）+ ccxt_venue 的 post-only 语义注释更正 + 新增 scripts/cancel_path_check.py。**无策略改动**：配置与 v0.3.0 完全相同，变的只是风险代码与测试（257→263）。post-only 与撤单两条前置已在真交易所验证（cancelSource=31 / 1）。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
