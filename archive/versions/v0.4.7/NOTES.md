# v0.4.7

Frozen 2026-10-10T11:50:25+08:00. Package version 0.4.7. Tests: 290.

版本与代码管理专业化：VERSION 单一来源（version_sync --check 强制 pyproject/ta.__version__ 一致，此前三者漂移到 0.2.0）；生成式 CHANGELOG.md（changelog_md.py）；docs/RELEASING.md（1.0.0 = 第一阶段完成；之后 MAJOR/MINOR/PATCH；7 步发布清单）；git init + 首个提交 e4f4864 + tag v0.4.6；每日任务新增 3 个校验步骤。1.0.0 保留给 path-5 期权样本闸门（约 10-13/14）。

Strategy changes after this point belong INSIDE this version: compare against
its config/ and manifest.json rather than against memory.
