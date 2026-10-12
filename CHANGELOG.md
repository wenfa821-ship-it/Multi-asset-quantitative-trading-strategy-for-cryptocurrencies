# Changelog

当前版本：**v1.1.1**（`VERSION` 是唯一事实来源；`pyproject.toml` 与 `ta.__version__` 由 `scripts/version_sync.py --check` 强制一致）。

版本规则见 [`docs/RELEASING.md`](docs/RELEASING.md)：**1.0.0 = 第一阶段封板**（四腿系统接入并运行 + 风控/对账/数据管道/发布管理就位）；1.0 之后按语义化版本 —— MAJOR 表示架构或配置模式不兼容，MINOR 表示新增能力（**1.1 = 期权策略与期权腿**），PATCH 表示修复。每个版本冻结在 `archive/versions/<v>/`（config + changelog + progress + manifest + NOTES）。

## Unreleased（自 2026-10-11T16:30 起，33 条留痕）

- `2026-10-11T16:35` **research** 组合级复盘（README §70）：亏损三块拆开、参数/数据/执行/网络逐条过一遍，并把『切实盘』写成 6 条可度量门槛
- `2026-10-11T16:55` **bug** 更正 §70 的一条参数结论：『费用假设低估一半』是错的 —— 现货场馆早已写入实测的 10 bps，真正的缺口是回测用 8 bps
- `2026-10-11T17:15` **research** 按实盘费率复跑四条策略（README §70.7）：有样本的两条**仍然为负**（trend_breakout −1.70% / 94 笔、perp_trend_funding −1.61% / 180 笔），另三条样本不足
- `2026-10-11T18:05` **bug** `data/funding/income.jsonl` 的口径有三个错：滚动窗口逐行相加会重复计费、`bps_per_day` 少除一个窗口天数、窗口跨度根本没记录
- `2026-10-11T18:20` **research** 非尖峰标的的 carry 真实收入（README §71）：**市场费率口径 2.03 bps/天、44.9 bps 往返 22.2 天回本**；模拟盘到账 CRV 是市场的 **325x**、ACT **6.4x**
- `2026-10-11T19:50` **decision** 新币策略另立项目并已封板 v0.1.0（同级目录 `newcoin-subjective-quant`）；**本项目不改名/不改目录/不改 git 路径，继续按实时数据自我更新**
- `2026-10-11T22:58` **ops** 巡检发现：现金对账残余从「稳定(info)」变成「漂移(warn)」—— 残余 $922.43、偏离 −$75.99 超过 $50 阈值
- `2026-10-11T23:50` **ops** 运营看板改成**两个项目**分开展示：新增「新币主观量化策略」面板（tab 切换），读数方式是**只读对方发布的 JSON**，不 import 对方代码
- `2026-10-12T01:05` **bug** 巡检盲区：`/metrics` **不输出零值计数器**，于是「缺失」与「0」在巡检眼里长得一样 —— 本轮清单里三个指标全部对不上
- `2026-10-12T01:10` **research** 事件研究首次出现**两侧桶都 ≥20 个不同代币且同向**：+1h 换手 1-5x（25 代币，中位 −0.49%）vs 20+（72 代币，−1.70%）
- `2026-10-12T01:15` **ops** 现金对账残余继续漂移，但**速率在收窄**：三点测量 −$922.43 → −$899.28 → −$889.45（≈ +$13/小时）
- `2026-10-12T03:05` **bug** 读数纠错：`ta_drawdown_pct` 已经是**百分数**（0.066 = 0.066%）—— 我上一轮把 0.0570 读成「回撤 5.7%」报给了用户
- `2026-10-12T03:10` **research** +1h 高换手更差**连续第二轮**满足「两侧 ≥20 代币」判据：1-5x（26 代币，中位 −0.60%）vs 20+（77 代币，−1.65%）
- `2026-10-12T05:05` **bug** 对账残余的归因找到了：**期望现金的流水口径漏掉资金费** —— 残余每一次台阶都精确等于当期资金费
- `2026-10-12T07:05` **research** +1h「高换手更差」本轮**没有复现**：20+ 桶回到中位 −0.44%（前三轮 −1.65~−1.70%），单调序断了
- `2026-10-12T07:10` **ops** 资金费口径那条 bug 拿到**镜像证据**：`ta_reconcile_repeat_total` 首次非零（=1）—— 残余只在资金费落地时才动
- `2026-10-12T07:20` **research** 复盘查出唯一真问题：**CRV 的「资金费」隐含费率是公开费率的约 200 倍**，而它占当日收益 78%、占全部资金费 97%
- `2026-10-12T07:25` **ops** 每日复盘（10-11）写进 README §16 + `docs/reviews/2026-10-11.md`；建议清单 5 条已列
- `2026-10-12T08:05` **research** CRV 资金费**定性完成：是模拟盘口径，不是我们的账** —— 频率、幅度、方向三项全不符实盘（账单 248× 实盘）
- `2026-10-12T08:20` **bug** 复盘口径分裂落到工具里：`daily_review.py` 现在同时给「补记」与「实盘费率口径」两行（同一天 $568.49 / $1.18 / 无法判定）
- `2026-10-12T08:45` **ops** 看板自己说出资金费口径差（建议 ①）：`funding_basis()` → 一条 redflag，实测最近 24h 账单 $640.12 vs 公开费率 $0.6304（1015 倍）；**待重启 dashboard 生效**
- `2026-10-12T09:05` **ops** 两处「不需要重启」的改动**已在生产自动生效**：账本开始记 `bill_ts`（已 5 条），复盘新格式随每日任务产出
- `2026-10-12T09:35` **bug** 看板 `/api/snapshot` 被**自己堆死**：页面固定 15s 轮询 + 单次 60~190s → 5 条并发互相拖慢；修成「上一次返回后再排下一次」+ 服务端单飞
- `2026-10-12T09:45` **bug** **我自己**把每日任务弄红了一步：追加 6 条留痕却没重生成 `CHANGELOG.md` → 09:24 的 `changelog_md.py --check` rc=1（重试 3 次）
- `2026-10-12T10:05` **decision** 口径锁定（复盘建议 ③④）：校准输入现在**同时带两个口径** —— `data/funding/n` 每行新增 `live_rate_usd`，并写明校准只能用这一栏
- `2026-10-12T10:10` **bug** 零值计数器不再隐形：`EXPECTED_COUNTERS` 声明式输出（0 也要看得见）—— **代码已改，要重启 runner 生效**
- `2026-10-12T10:30` **bug** 对账残余的**真正根因**：runner 的 Ledger 只读一次文件，看不见别的进程写的资金费行 —— 05:05 那条的机制要更精确一点
- `2026-10-12T12:35` **research** **期权闸门首次开启**：BTC 连续 5 天 RV−IV ≥1.5pp（最新 +6.87pp，序列 5.54→12.41→10.35→9.79→6.87）
- `2026-10-12T13:05` **bug** 我按**过期清单**重做了一次已结案的前置：换 ~$100 USDT→BTC（10-10 就做过、10-11 已卖回）；已把根因修掉并把账户恢复
- `2026-10-12T13:10` **bug** 顺带修掉两个脚本的真缺陷：`connect()` 容忍 `load_markets()` 失败，但它们直接用了 `ex.markets` → AttributeError
- `2026-10-12T13:25` **bug** 看板的「仓位对账不一致」会**粘住**：它读最后一条 mismatch，而 runner 不记成功 → 已加 2 小时新鲜度约束
- `2026-10-12T13:40` **bug** 同一类缺陷还有三处：幽灵卖单 / 接管成交 / 熔断与强平都在**无界扫全量历史**，永久占着 critical → 改为新鲜度决定音量
- `2026-10-12T14:05` **ops** 修掉巡检 automation 指令里的三处误导（`launchctl list` 误报 / 计数器缺失含义 / 常驻 info 重报），并补两条今天踩到的

## v1.1.1 — 2026-10-11T16:30

- 测试 **368** 绿 · 权益 $101,899.26 · 记录时的包版本 1.1.1

carry 真实收入的两个口径对照（README 70.8/71）；修 income.jsonl 的滚动窗口口径错误（重复计费/少除天数/无窗口字段）；新增 scripts/carry_income_report.py 并接进每日任务
its config/ and manifest.json rather than against memory.

## v1.1.0 — 2026-10-11T13:27

- 测试 **356** 绿 · 权益 $101,815.14 · 记录时的包版本 1.1.0

1.1 = 期权腿 venue 适配 + 数据层 + 实测约束（说明书 §4 第 7 条）。策略未接入：这套模拟环境买不到期权——市价被拒(50016)、按卖一价被价格带拒(51006 max buy 0.0245/min sell 0.0001)、挂买价 25 分钟无人成交(README §69)。顺带修掉新进程 clOrdId 撤单失败与 unknown≠cancelled。356 tests。
its config/ and manifest.json rather than against memory.

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
