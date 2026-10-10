# v1.0.4

Frozen 2026-10-10T20:12:05+08:00. Package version 1.0.4. Tests: 333.

1.0.4 补丁（执行路径）：修掉现货下单偶发 51000 —— tdMode 探针失败不再静默退到 cash，被拒时重解析并重试一次；runner 启动期对未解析状态直接告警。333 tests。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
