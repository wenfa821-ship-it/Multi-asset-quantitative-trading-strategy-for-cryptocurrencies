# v1.1.6

Frozen 2026-10-13T15:50:26+08:00. Package version 1.1.6. Tests: 409.

看板新增「待你决定」面板：DashboardData.decisions() 读 docs/progress.json 的 waiting_decision 行并进 /api/snapshot，面板连同兄弟项目发布的待决项一起显示（缺文件给原因不给 0）。同时把两条只在回复里存在的流程决定登记成行：dashboard-restart-pending（两处 Python 改动要重启才生效）、data-files-in-git（三选一）。当前收件箱 3 + 4 = 7 条。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
