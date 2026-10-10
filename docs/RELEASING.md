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

**1.0.0 = 第一阶段封板：四腿系统（现货 / 永续 / carry / TradFi）接入实盘配置并运行，
风控四道闸门、对账、funding 入账、数据管道与发布管理全部就位。**

2026-10-10 范围调整（用户决定）：原五步路径的第 5 步**期权策略移入 1.1** ——
它等的是 5–7 天 IV/RV 新鲜样本（外部条件，不是工作），而 1.0 应当先封板跑起来。
期权腿的 20% 额度在 1.0 里**保持空置**，这是设计而不是故障。

`0.x` 阶段只保证「能追溯到当时的 config 与留痕」，不承诺兼容性（本项目从 `0.2.0`
一路走到 `0.4.7`，共 11 个冻结版本）。1.0.0 之后按语义化版本：

| 位 | 什么时候 +1 | 本项目里的例子 |
|---|---|---|
| **MAJOR** | 架构或配置模式**不兼容**地改变（旧 config 不再能加载 / 账本口径改变） | 四腿分仓 30/20/30/20 的引入 |
| **MINOR** | **新增能力**：新的腿、新的策略、新的 venue 适配 | **1.1 = 期权策略 + 期权腿**；TradFi 腿上线、carry 被动入场 |
| **PATCH** | **修复**：风控口径、执行路径、观测、工具 | 权益口径统一、深度闸门扩到所有入场、tier 持仓计数 |

### 已排期的版本

| 版本 | 内容 | 门槛 |
|---|---|---|
| **1.0.0** | 四腿 + 风控/对账/数据/发布管理（本次封板） | 已完成 |
| **1.1** | 期权策略（买权利金的有限风险结构）+ 期权腿 venue 适配 | IV−RV 满 5–7 天新鲜样本、符号稳定且扣成本仍为正（`path-5` / `leg-options` 两行） |
| **1.2** | carry 换腿规则 + `carry_notional_pct` 校准 | 名额 5/5 已满，轮换是唯一进场方式；换腿成本 44.9 bps ⇒ 只在资金费**持续**低于阈值时触发；notional 校准要等非尖峰样本 |
| **1.3** | 链上：+1h 高换手假设的正式判定 | 低换手桶需 ≥20 个代币（现 6–7）；且任何信号要先跨过 $200 单往返 1.4% 的成本墙 |
| 1.0.x | 运行中发现的修复 | 随发现（当前两个候选：TradFi tier 收窄、对账脚本的 10 倍单位假差异） |

**优先级与理由**（2026-10-10 定）：1.1 先做，因为它是唯一能**新增收益来源**的一步，
且方向天然是买方（最大损失＝权利金），与"风险最小化"一致；1.2 的收益增量小
（换腿约 30 天才回本）但风险最低，适合在 1.1 等待样本时穿插做；1.3 在执行层面暂时不可得，
保持在研究层。

## 3. 发布清单（每次冻结一个版本都按这个顺序做）

1. `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests` —— 必须全绿；
2. 更新 `docs/progress.json`，并 `python3 scripts/progress.py && python3 scripts/progress.py --check`
   （`progress.py` 直接渲染，没有 `--write`；只有 `changelog_md.py` 需要显式的 `--write`）；
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
