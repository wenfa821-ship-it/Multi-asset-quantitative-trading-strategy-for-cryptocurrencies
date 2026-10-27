# 版本与发布规则（RELEASING）

**项目名：多资产组合策略**（2026-10-11 用户定名；代码目录仍为 `trading-agent`，因为 5 个 launchd 单元的
`WorkingDirectory` 指着它）。四条腿共用一份风控/账本/闸门，**当前 100% 仓位都属于它**；将来的
**新币策略另立项目**（预定名 **新币主观量化策略**），不与本项目混在一起。

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
| **1.1** | 期权腿 venue 适配 + 数据层（策略按原文门槛预留） | **已发布 2026-10-11（v1.1.0）**。实交范围 = 适配 + 数据 + 实测约束；策略未接入，因为**这套模拟环境买不到期权**：市价被拒（50016）、按卖一价被价格带拒（51006：max buy 0.0245 / min sell 0.0001）、挂买价 25 分钟无人成交 → 只能当 maker 挂单等（README §69 为完整证据）。闸门（IV−RV 连续 5 天）**闸门已于 2026-10-12 成立（BTC 连续 5/5 天，最新 +6.87pp）**，但开不开门取决于环境而不是判据；v1.1.1~v1.1.5 全是修复/观测/工具（资金费双口径、前置执行闸门、看板面板、研究侧日线解冻…），见 `CHANGELOG.md` |
| **1.2** | carry 换腿规则 + `carry_notional_pct` 校准 | 名额 5/5 已满，轮换是唯一进场方式；换腿成本 44.9 bps ⇒ 只在资金费**持续**低于阈值时触发；notional 校准要等非尖峰样本 |
| **1.3** | 链上：+1h 高换手假设的正式判定 | **2026-10-13 更正**：原来的门槛「低换手桶需 ≥20 个代币（现 6–7）」**等不到** —— 实测研究并集 162 个代币里只有 3 个中位换手比 <1、2 个 <0.5，本发现层结构上产不出安静池。要重开先二选一：① 加「安静池」来源（GT new_pools，代价是 40 个候选额度重分配）② 收窄成「只测高换手侧 vs 全场均值」。另外且任何信号要先跨过 $200 单往返 1.4% 的成本墙 |
| 1.0.x / 1.1.x | 运行中发现的修复 | 随发现 —— 两个早期候选（TradFi tier 收窄、对账脚本的 10 倍单位假差异）**均已结案**；2026-10-13 一天的例子：新上币采集器静默跳过 cat=3、研究侧日线冻结 5.5 天、冻结工具会把红着的树冻成版本、证据表被网络抖动覆盖成空表 |

**优先级与理由**（2026-10-10 定）：1.1 先做，因为它是唯一能**新增收益来源**的一步，
且方向天然是买方（最大损失＝权利金），与"风险最小化"一致；1.2 的收益增量小
（换腿约 30 天才回本）但风险最低，适合在 1.1 等待样本时穿插做；1.3 在执行层面暂时不可得，
保持在研究层。

## 3. 分支与环境策略（trunk-based）

本项目不用三条长期 `develop/test/main` 分支。长期分支会让配置、账本口径和
策略参数互相漂移，而生产只有一个，测试主要靠自动化与独立数据目录完成：

| 阶段 | 落点 | 规则 |
|---|---|---|
| 开发 | `main` + `feat/*` / `fix/*` 短分支 | 小步提交；合并前全量测试与一致性检查 |
| 候选 | `vX.Y.Z-rc.N` tag（可选） | 从 `main` 的确定 commit 构建；不得改 tag |
| 测试 | 独立 worktree/目录、独立 `config-test/`、data、端口 | 禁止与生产 runner 共用账户、账本或端口 |
| 生产 | 正式 `vX.Y.Z` tag 的确定 commit | 部署后 `lifecycle.py verify` 必须通过 |
| 回滚 | 上一个正式 tag + 对应 archive | 数据/配置 migration 必须单独评估 |

`main` 必须随时可部署；功能未完成时不要合入。测试环境不是长期分支，而是一个
独立的运行副本。没有独立 OKX 子账户时，测试只能使用 paper/离线数据，不能与
生产 runner 同时对同一账户下单。

每个 runner 启动都会在 `data/runtime/provenance.json` 写入版本、Git SHA、
dirty 状态、config 指纹、build id、PID 与启动时间；看板 `/api/snapshot` 的
`provenance` 字段读取同一份记录。生产验收要求：

运行中会持续追加 `README.md`、`docs/changelog.jsonl` 和每日复盘；这些**运行态文档**
不会算作代码漂移。provenance 同时记录 `git_dirty` 与更严格的 `source_dirty`：
后者只看 `src/ scripts/ config/ deploy/ .github/ VERSION pyproject.toml Makefile`。
若 HEAD 在最近的 release tag 之后只有文档提交，生产验收仍可通过；任何
代码/配置提交都会使 `source_changed_since_tag=true` 并阻止验收。

```bash
python3 scripts/lifecycle.py status
python3 scripts/lifecycle.py verify
```

## 4. 发布清单（每次冻结一个版本都按这个顺序做）

1. 完成代码/配置修改，追加 `docs/changelog.jsonl`，更新 `docs/progress.json`；
2. 改 `VERSION`、`pyproject.toml`、`src/ta/__init__.py`，**并把 README 头部那句
   「**当前版本 vX**」一起改**（`scripts/version_sync.py --check` 会把四处一起比）；
3. 草稿阶段跑 `python3 scripts/lifecycle.py preflight --allow-dirty`，先消除测试和一致性红灯；
4. 提交候选代码，使工作树干净，再跑 `python3 scripts/lifecycle.py preflight`；
5. `python3 scripts/version_snapshot.py --version vX.Y.Z --notes "..."`；
6. `python3 scripts/changelog_md.py --write && python3 scripts/changelog_md.py --check`；
7. 提交发布快照并打 tag：`git add -A && git commit -m "release: vX.Y.Z" && git tag vX.Y.Z`。

第 7 步是代码侧的同一件事：`archive/versions/` 是**可读的**发布记录，git 是**可比对的**发布记录，
两者都要有 —— 前者让人一眼看懂当时是什么状态，后者让 `git diff v0.4.6 v0.5.0` 回答「到底改了什么」。

### 4.1 生产部署与验收

1. 在干净 worktree checkout 正式 tag；
2. 用仓库自己的 launchd 安装/重启路径部署，不手改生产目录；
3. 等首个完整 tick；
4. 运行 `make deploy-verify`；要求版本、Git SHA、config 指纹、launchd PID、
   metrics 和非 kill-switch 状态全部匹配；
5. 保存部署时间、build id、首 tick 结果与日志位置。

## 5. 回滚

代码回滚使用上一个正式 tag，不使用长期分支头。先看：

```bash
python3 scripts/lifecycle.py rollback-plan
```

随后在独立 worktree checkout 目标 tag，确认：

- `archive/versions/<tag>/manifest.json` 存在且完整；
- 配置格式与当前账本/数据 schema 兼容；
- 该版本需要的 migration 可以执行或明确不需要；
- 部署后首个 tick、对账、kill switch 和配对守恒均通过。

数据 migration 不可逆时，代码回滚不等于状态回滚；必须先备份账本与配置，并保留失败
release 的日志和 provenance，禁止只用 `git checkout` 覆盖生产目录。

## 6. 什么进 git，什么不进

`.gitignore` 已经写下这条边界，理由是它们要么是秘密、要么可由代码重建：

| 进 | 不进 |
|---|---|
| `src/`、`tests/`、`scripts/`、`config*/`、`docs/`、`deploy/`、`README.md`、`CHANGELOG.md`、`VERSION`、`pyproject.toml`、`Makefile` | `.env*`（密钥）、`data/`（303MB 行情与账本）、`logs/`、`backups/`、`build/`、`*.jsonl`（除 `archive/versions/**` 的冻结快照） |

## 7. 持续同步

「持续同步」不是靠记性：`scripts/daily_research.py` 每个交易日跑两次（09:10 / 21:10），
其中 `progress.py --check`、`version_sync.py --check`、`changelog_md.py --check` 三个**校验**步骤
会在日志里以失败步骤的形式出现，而不是等到某次盘点才发现 README／CHANGELOG／版本号已经漂移。
