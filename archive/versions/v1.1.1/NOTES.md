# v1.1.1

Frozen 2026-10-11T16:30:18+08:00. Package version 1.1.1. Tests: 368.

carry 真实收入的两个口径对照（README 70.8/71）；修 income.jsonl 的滚动窗口口径错误（重复计费/少除天数/无窗口字段）；新增 scripts/carry_income_report.py 并接进每日任务

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
