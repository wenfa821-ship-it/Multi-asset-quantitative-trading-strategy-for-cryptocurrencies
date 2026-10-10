# v1.0.2

Frozen 2026-10-10T15:17:16+08:00. Package version 1.0.2. Tests: 302.

1.0.2 补丁：每日任务加有界重试（3 次/60 秒）与补跑清单 data/daily_job_status.json；.env 加载器统一到 ta.config.load_env_file，修掉 venue_availability 与 refresh_daily_bars 在 launchd 下必然抛 API key is not set。302 tests。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
