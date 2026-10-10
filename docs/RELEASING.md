# 版本与发布规则（RELEASING）

这个项目**没有把自己当脚本**：它每天在真金白银的通道上做决定，所以版本管理必须
回答三个问题 —— *现在的状态是什么*、*上一个状态是什么*、*两者之间改了什么*。

## 1. 三个载体，各司其职

| 载体 | 是什么 | 谁维护 |
|---|---|---|
| `docs/progress.json` → README「路径进度表」 | **下一步做什么**的唯一事实来源（每轮先读、收尾更新） | `scripts/progress.py`（`--check` 失败即过期） |
| `docs/changelog.jsonl` | **append-only 留痕**：每条改动/决定/研究结论一行，含 kind/area/title/detail/verdict/evidence/status | 每轮追加，不改写历史正文 |
| `archive/versions/<v>/` | **冻结快照**：config/*.toml + changelog + progress + manifest.json + NOTES.md；同一版本重复冻结会被拒绝 | `scripts/version_snapshot.py` |
| `CHANGELOG.md` | **人可读的发布说明**：每个冻结版本的日期/测试数/权益 + 自上次冻结以来的 Unreleased 条目 | `scripts/changelog_md.py`（`--check` 失败即过期） |
| `VERSION` | **唯一版本号**，`pyproject.toml` 与 `ta.__version__` 必须与它一致 | `scripts/version_sync.py --check` |
| git tag `v<版本>` | 代码侧的同一件事（可 diff、可回退） | 发布清单第 6 步 |

## 2. 版本号规则

**1.0.0 = 第一阶段（README §34.4 的五步路径）全部完成。**
在此之前一律是 `0.x`：`0.x` 的数字只保证「能追溯到当时的 config 与留痕」，
不承诺兼容性。1.0.0 之后按语义化版本：

| 位 | 什么时候 +1 | 本项目里的例子 |
|---|---|---|
| **MAJOR** | 架构或配置模式**不兼容**地改变（旧 config 不再能加载 / 账本口径改变） | 四腿分仓 30/20/30/20 的引入 |
| **MINOR** | **新增能力**：新的腿、新的策略、新的 venue 适配 | TradFi 腿上线、carry 被动入场 |
| **PATCH** | **修复**：风控口径、执行路径、观测、工具 | 权益口径统一、深度闸门扩到所有入场 |

## 3. 发布清单（每次冻结一个版本都按这个顺序做）

1. `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests` —— 必须全绿；
2. 更新 `docs/progress.json`，并 `python3 scripts/progress.py --write && python3 scripts/progress.py --check`；
3. 追加 `docs/changelog.jsonl`（每条改动/决定/研究结论）；
4. 改 `VERSION`（并让 `scripts/version_sync.py --check` 通过）；
5. `python3 scripts/version_snapshot.py --version vX.Y.Z --notes "..."`；
6. `python3 scripts/changelog_md.py --write`；
7. git：`git add -A && git commit -m "release: vX.Y.Z" && git tag vX.Y.Z`。

第 7 步是代码侧的同一件事：`archive/versions/` 是**可读的**发布记录，git 是**可比对的**发布记录，
两者都要有 —— 前者让人一眼看懂当时是什么状态，后者让 `git diff v0.4.6 v0.5.0` 回答「到底改了什么」。

## 4. 什么进 git，什么不进

`.gitignore` 已经写下这条边界，理由是它们要么是秘密、要么可由代码重建：

| 进 | 不进 |
|---|---|
| `src/`、`tests/`、`scripts/`、`config*/`、`docs/`、`deploy/`、`README.md`、`CHANGELOG.md`、`VERSION`、`pyproject.toml`、`Makefile` | `.env*`（密钥）、`data/`（303MB 行情与账本）、`logs/`、`backups/`、`build/`、`*.jsonl`（除 `archive/versions/**` 的冻结快照） |

## 5. 持续同步

「持续同步」不是靠记性：`scripts/daily_research.py` 每个交易日跑两次（09:10 / 21:10），
其中 `progress.py --check`、`version_sync.py --check`、`changelog_md.py --check` 三个**校验**步骤
会在日志里以失败步骤的形式出现，而不是等到某次盘点才发现 README／CHANGELOG／版本号已经漂移。
