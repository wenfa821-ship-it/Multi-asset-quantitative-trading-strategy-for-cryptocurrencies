# v1.1.7

Frozen 2026-10-13T16:02:00+08:00. Package version 1.1.7. Tests: 419.

README 70.6 的六条切实盘门槛数字化：scripts/live_readiness.py → reports/live-readiness.md + data/readiness/readiness.json（看板新增「切实盘门槛」面板）；daily_research.py 新增 data/daily_job_history.jsonl 逐轮留痕（门槛 4 原来在磁盘上无历史）；419 测试绿。当前 0/5 条可自动判的门槛满足。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
