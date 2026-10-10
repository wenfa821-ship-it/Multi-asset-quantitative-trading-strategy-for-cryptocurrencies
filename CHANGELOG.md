# Changelog

当前版本：**v1.0.6**（`VERSION` 是唯一事实来源；`pyproject.toml` 与 `ta.__version__` 由 `scripts/version_sync.py --check` 强制一致）。

版本规则见 [`docs/RELEASING.md`](docs/RELEASING.md)：**1.0.0 = 第一阶段封板**（四腿系统接入并运行 + 风控/对账/数据管道/发布管理就位）；1.0 之后按语义化版本 —— MAJOR 表示架构或配置模式不兼容，MINOR 表示新增能力（**1.1 = 期权策略与期权腿**），PATCH 表示修复。每个版本冻结在 `archive/versions/<v>/`（config + changelog + progress + manifest + NOTES）。

## Unreleased（自 2026-10-10T22:19 起，10 条留痕）

- `2026-10-10T22:40` **ops** 加入『保留库存』：声明过的持仓不再触发非托管告警与对账差异，但金额照报（ta_reserved_notional）
- `2026-10-10T22:40` **ops** 看板补上两个批量任务：按『多久没跑 + 上次说了什么』判活，而不是端口（closes dashboard-batch-job-coverage）
- `2026-10-10T23:00` **ops** 核对 1.1 的阶段：前置全绿、主工程未开工；把『runner 期权执行接线』写成独立一行（此前它藏在 leg-options 的措辞里）
- `2026-10-10T23:20` **decision** TradFi tier 收窄到 XMU：盘中样本到手，那 12 个标的是『根本不供数据』而不是『时段受限』（v1.0.6）
- `2026-10-10T23:30` **research** 模拟盘期权盘口与实盘不是同一个 book：同一个合约、同一秒，`x-simulated-trading: 1` 下的买价远离卖价（0.0235 vs 0.0385），不带该头则是 0.038/0.0385 —— 1.1 的成本模型必须按『买用实盘 ask、卖用模拟盘 bid』重算
- `2026-10-10T23:35` **ops** 收窄 tier 的副作用：可交易性采集现在只盯 XMU 一个标的，那 12 个被移除的标的没人看了
- `2026-10-10T23:45` **ops** 文档同步：把今天五个补丁与两处架构变化写回 README/进度表，并记下『看板只登记 3 个服务』这个缺口
- `2026-10-11T05:35` **ops** 看门狗第一次把现金残余从『稳定』升级为『在漂移』：8 小时里残余从 $1,150.75 走到 $1,064.71（−$86），期间我们没有成交、只有 2 条 funding（+$1.57）
- `2026-10-11T07:05` **ops** 每日复盘 2026-10-10（完整交易日）：权益 −$157.81（−0.155%）、成交 4 笔、手续费 $5.50、funding +$47.69；README §68 已更新
- `2026-10-11T07:40` **ops** 现金残余漂移已归因：是**未同步的资金费**（CRV 4 次结算 +$163），不是钱丢了 —— 但暴露了『同步频率』这个建模缺口

## v1.0.6 — 2026-10-10T22:19

- 测试 **342** 绿 · 权益 $101,609.90 · 记录时的包版本 1.0.6

1.0.6 补丁：TradFi tier 从 13 个收窄到 1 个（XMU/USDT）—— 盘中观测证明其余 12 个在两个时段都不供数据，属于配置与场馆实际脱节。342 tests。
its config/ and manifest.json rather than against memory.

## v1.0.5 — 2026-10-10T21:37

- 测试 **336** 绿 · 权益 $101,378.14 · 记录时的包版本 1.0.5

1.0.5 补丁（执行路径/韧性）：行情刷新加 120 秒墙钟预算 —— 慢场馆（每次重试才成功）不再能把一个 tick 拖成 86 分钟；超预算即按既有 VenueDegraded 路径降级。336 tests。
its config/ and manifest.json rather than against memory.

## v1.0.4 — 2026-10-10T20:12

- 测试 **333** 绿 · 权益 $101,628.14 · 记录时的包版本 1.0.4

1.0.4 补丁（执行路径）：修掉现货下单偶发 51000 —— tdMode 探针失败不再静默退到 cash，被拒时重解析并重试一次；runner 启动期对未解析状态直接告警。333 tests。
its config/ and manifest.json rather than against memory.

## v1.0.3 — 2026-10-10T16:17

- 测试 **316** 绿 · 权益 $101,577.79 · 记录时的包版本 1.0.3

1.0.3 补丁（观测/工具）：把每日任务的补跑清单接进看板（services.daily_job + warn 红旗），补上 v1.0.2 只写不读的那半句；同轮新增 1.2 换腿规则的检测侧（scripts/carry_rotation.py，只读）并接进每日任务。带 launchd 同款环境实测 14/14 步首跑全绿。316 tests。
its config/ and manifest.json rather than against memory.

## v1.0.2 — 2026-10-10T15:17

- 测试 **302** 绿 · 权益 $101,580.74 · 记录时的包版本 1.0.2

1.0.2 补丁：每日任务加有界重试（3 次/60 秒）与补跑清单 data/daily_job_status.json；.env 加载器统一到 ta.config.load_env_file，修掉 venue_availability 与 refresh_daily_bars 在 launchd 下必然抛 API key is not set。302 tests。
its config/ and manifest.json rather than against memory.

## v1.0.1 — 2026-10-10T12:10

- 测试 **292** 绿 · 权益 $101,600.65 · 记录时的包版本 1.0.1

PATCH：对账脚本单位修一致（场馆侧与账本都用张）—— 此前 _venue_swap_positions 换算成 base 单位，ACT/NEAR（ctVal 10）虚报 10 倍差额，且喂给修正逻辑的数量会被当张数写进账本（错 10 倍）。修后五条永续腿全部 ok、nothing to correct；+2 测试（292 全绿）。
its config/ and manifest.json rather than against memory.

## v1.0.0 — 2026-10-10T11:58

- 测试 **290** 绿 · 权益 $101,603.55 · 记录时的包版本 1.0.0

第一阶段封板。范围：四腿系统（现货/永续/carry/TradFi）接入实盘配置并运行 + 四道选标闸门（funding/深度/基差/成本）落在下单路径 + 对账（永续逐腿 + cashBal 口径）+ funding 入账 + 数据管道（每日任务含历史刷新、期权数据层、可交易性记录、版本/进度校验）+ 发布管理（VERSION/CHANGELOG/RELEASING/归档/git）。运行态：五对 carry 全部对冲（CRV/ACT/LTC/SUI/NEAR）、权益约 $101.6k、290 tests 全绿。期权策略与期权腿按用户决定移入 1.1（门槛：IV−RV 满 5–7 天新鲜样本且符号稳定）。
its config/ and manifest.json rather than against memory.

## v0.4.7 — 2026-10-10T11:50

- 测试 **290** 绿 · 权益 $101,591.24 · 记录时的包版本 0.4.7

版本与代码管理专业化：VERSION 单一来源（version_sync --check 强制 pyproject/ta.__version__ 一致，此前三者漂移到 0.2.0）；生成式 CHANGELOG.md（changelog_md.py）；docs/RELEASING.md（1.0.0 = 第一阶段完成；之后 MAJOR/MINOR/PATCH；7 步发布清单）；git init + 首个提交 e4f4864 + tag v0.4.6；每日任务新增 3 个校验步骤。1.0.0 保留给 path-5 期权样本闸门（约 10-13/14）。
its config/ and manifest.json rather than against memory.

## v0.4.6 — 2026-10-10T11:46

- 测试 **290** 绿 · 权益 $101,598.81 · 记录时的包版本 0.2.0

按用户决定扩名额 + 修掉引擎的 tier 持仓计数缺陷：spot/derivatives 上限 3→5、2→5（每日笔数 3→5）、account 12→17（每日笔数 14→16）；carry tier max_positions 5→10（引擎数持仓，一对=两条腿）；RiskEngine 的 tier 上限改为只计本 tier 持仓（此前拿全账户持仓数去比，锁死了 TradFi 腿与所有方向性入场）。效果：重启后 SUI 与 NEAR 两对在同一 tick 开成，账面 5 对逐腿对冲相等。+2 测试（290 全绿）。
its config/ and manifest.json rather than against memory.

## v0.4.5 — 2026-10-10T10:42

- 测试 **286** 绿 · 权益 $101,578.97 · 记录时的包版本 0.2.0

永续盈亏补上合约面值：Position.ct_val（默认 1.0）+ unrealized 乘面值；CcxtVenue.contract_size() 并用于 get_account；runner 重建资金簿后为永续持仓补齐 ct_val。ACT（ctVal 10）盈亏不再被低估 10 倍；现货默认 1.0 零影响。+3 测试（286 全绿）。config 与 v0.4.4 相同。
its config/ and manifest.json rather than against memory.

## v0.4.4 — 2026-10-10T09:53

- 测试 **283** 绿 · 权益 $101,625.29 · 记录时的包版本 0.2.0

断网三级韧性修复：connect() 不再因 load_markets 失败而退出（此前启动即崩 → KeepAlive 每 10 秒重启 → 142 次启动横幅）；_markets_loaded 派生自 ccxt（自愈）；prime() 连续 5 个标的全失败提前返回且不抛；refresh() 连续 5 个失败整 pass 判 degraded（此前一个 pass 磨 42 分钟）。+5 测试（283 全绿）。config 与 v0.4.3 完全相同。
its config/ and manifest.json rather than against memory.

## v0.4.3 — 2026-10-10T09:25

- 测试 **278** 绿 · 权益 $101,625.29 · 记录时的包版本 0.2.0

看门狗新增『交易循环停摆』判据：runner_stale_minutes=15.0，按账本最新 equity 事件时间戳判活，超时 critical；阈值取 15 覆盖最慢 12 分钟的预热。+3 测试（278 全绿）。已在 2026-10-10 代理上游中断事故中实时验证（看板报 25.50 分钟 critical）。config 与 v0.4.2 完全相同；本轮只重启只读看板。
its config/ and manifest.json rather than against memory.

## v0.4.2 — 2026-10-09T23:23

- 测试 **275** 绿 · 权益 $101,556.19 · 记录时的包版本 0.2.0

权益口径统一：新增 portfolio.marked_value（现货名义 + 永续未实现盈亏），PositionBook.equity 与 runner._prepare_risk_account 共用同一实现，修掉『开一对新 carry 被风控当成日亏 -2% 并暂停入场、名义再大会触发 flatten_all』的定时炸弹，并把 sizing 基数从低估 4.7% 纠正回真实值。新增 2 条测试（275 全绿），含『开对冲腿不改变 risk_equity』回归。config 与 v0.4.1 完全相同。遗留：Position.unrealized 未乘 ctVal（ACT 盈亏被低估 10 倍，已单列）。
its config/ and manifest.json rather than against memory.

## v0.4.1 — 2026-10-09T23:10

- 测试 **273** 绿 · 权益 $101,574.77 · 记录时的包版本 0.2.0

v0.4.0 + 被动入场致命修复：新增 CcxtVenue.order_state/OMS.order_state（读订单自身记录 state+accFillSz 作为唯一权威），删除『不在挂单列表=已撤销』的启发式，查不到→拒绝市价回退。起因：LTC 第一对真仓把现货买成两倍（约 ,977 裸露 33 分钟，净成本 ~.3）。另含 order_state 两条回归测试（273 tests）与 BAND 裸多处置（已平掉并去掉 seeded 配对记录）。config 与 v0.4.0 完全相同。
its config/ and manifest.json rather than against memory.

## v0.4.0 — 2026-10-09T22:37

- 测试 **268** 绿 · 权益 $101,605.15 · 记录时的包版本 0.2.0

carry 被动入场（第一条改变**执行方式**的策略改动）：现货腿先挂 post-only 于买一 → 等 6s → 撤单并确认终态 → 未成交才回退市价。撤单无法确认时拒绝回退（现货腿翻倍风险）。新增 OMS.cancel / OMS+venue.open_order_ids（None=查不到）/ paired._passive_spot / runner._passive_entry_price；config 新增 passive_entry=true、passive_wait_seconds=6.0。对照 v0.3.x：这是 carry 第一次不再无条件市价入场，目标是把单腿 12.6-72.6 bps 的入场价差降到 0。真实交易所验证：cancelSource=31（被交易所杀掉，零成交）与 cancelSource=1（我们撤的）。
its config/ and manifest.json rather than against memory.

## v0.3.1 — 2026-10-09T21:52

- 测试 **263** 绿 · 权益 $101,565.86 · 记录时的包版本 0.2.0

v0.3.0 + 深度闸门修复（sizing.depth_refusal + runner._entry_depth_refused，覆盖所有入场而非仅 carry）+ ccxt_venue 的 post-only 语义注释更正 + 新增 scripts/cancel_path_check.py。**无策略改动**：配置与 v0.3.0 完全相同，变的只是风险代码与测试（257→263）。post-only 与撤单两条前置已在真交易所验证（cancelSource=31 / 1）。
its config/ and manifest.json rather than against memory.

## v0.3.0 — 2026-10-09T21:21

- 测试 **257** 绿 · 权益 $101,572.80 · 记录时的包版本 0.2.0

TradFi 腿通电（第一条策略优化）：新增 tradfi tier 13 标的 + [strategies.trend_breakout_daily] 短 warmup(10/20) + [tiers.tradfi] 全库最紧档位 + universe 加 1d timeframe。实测订单往返 21bps（XMU 最小额往返）。对照基线 v0.2.0：这是第一条真正改变交易行为的策略改动。注意：该策略回测 28 笔/-0.14%/PF 0.34，启用目的是按用户要求承风险收执行数据，不是已证实的边缘。
its config/ and manifest.json rather than against memory.

## v0.2.0 — 2026-10-09T20:41

- 测试 **253** 绿 · 权益 $101,575.88 · 记录时的包版本 0.1.0

四腿分仓（现货30/永续20/TradFi30/期权20）+ carry 四道选标闸门（资金费/深度/基差/实测成本）+ 下单路径复核 + 对账口径(cashBal) + funding 入账。这是策略优化开始前的基线：期权策略与 TradFi 腿尚未产出成交。
its config/ and manifest.json rather than against memory.
