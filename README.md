# trading-agent

现货 + 链上新币、个人中低频的交易 Agent 骨架。**核心运行时零依赖**（Python 3.11+ 标准库），
开箱即可跑通「回测 → 模拟盘 → OKX Demo / Binance Testnet」全链路，只需一个文件接入 ccxt。

只有一条不可违反的设计契约：

```
策略只负责"提议" → 风控引擎"决定" → OMS"执行" → Ledger"留痕"
```

策略永远拿不到下单权限；LLM 或任何外部信号（GMGN、新闻、链上指标）都只能走到"提议"这一步。

## 版本与发布

| 载体 | 作用 |
|---|---|
| `VERSION` | **唯一版本号**；`pyproject.toml` 与 `ta.__version__` 由 `scripts/version_sync.py --check` 强制一致（这三个曾经漂移：归档到 v0.4.6 时它们还写着 0.2.0） |
| [`CHANGELOG.md`](CHANGELOG.md) | **人可读发布说明**：每个冻结版本的日期/测试数/权益 + 自上次冻结以来的 Unreleased 条目，由 `scripts/changelog_md.py` 生成、每日校验 |
| [`docs/RELEASING.md`](docs/RELEASING.md) | 版本规则（1.0.0 = 第一阶段完成；之后 MAJOR/MINOR/PATCH）与 7 步发布清单 |
| `archive/versions/<v>/` | **冻结快照**：`config/*.toml` + `changelog.jsonl` + `progress.json` + `manifest.json` + `NOTES.md`；同一版本拒绝重复冻结 |
| git tag `v<版本>` | 代码侧的同一件事：`archive/` 让人一眼看懂当时状态，git 让 `git diff v0.4.6 v0.5.0` 回答「到底改了什么」 |

**当前版本 v1.0.0（第一阶段封板）**。1.0 的范围 = **四腿系统（现货 / 永续 / carry / TradFi）
接入实盘配置并运行 + 风控四道闸门 + 对账 + funding 入账 + 数据管道 + 上文这套发布管理**。

2026-10-10 范围调整（用户决定）：原五步路径的第 5 步**期权策略移入 1.1**（它等的是 5–7 天
IV/RV 新鲜样本，属外部条件）；期权腿的 20% 额度在 1.0 里**保持空置 —— 这是设计，不是故障**。
1.1 的门槛与内容见 [`docs/RELEASING.md`](docs/RELEASING.md) 的"已排期的版本"。

## 路径进度表（唯一事实来源）

<!-- PROGRESS:BEGIN -->

> 生成自 `docs/progress.json`（`python3 scripts/progress.py`），更新于 2026-10-10。
> **每轮先读这张表，收尾时更新它** —— 它是「下一步做什么」的唯一事实来源。

| # | 项目 | 状态 | 闸门 / 依据 | 下一步 |
|---|---|---|---|---|
| `path-1` | 回放改「并集时间轴 + 按标的可用性」 | **✅ 完成** | README §35 | — |
| `path-2` | carry 提升为受风控管理的永续策略 | **✅ 完成** | README §37/38，§43–59 大幅加固 | — |
| `path-3` | 补齐现货 272 / 永续 155 历史 | **✅ 完成** | README §47：4h/1h 167→388；顺带查出 21 个 live-only + 18 个僵尸条目 | — |
| `path-4` | 期权数据层 | **✅ 完成** | README §45：已接进每日任务 | — |
| `path-5` | 期权策略（买权利金的有限风险结构） | **🔜 下一版本** | **用户决定：移至 1.1**（2026-10-10）。理由：这条腿等的是 5–7 天 IV/RV 新鲜样本（不是工作），而 1.0 应当先封板跑起来。当前读数（10-10，同期限对齐后）：BTC −7.25 / ETH +7.18 / SOL −0.24 pp，中位 −0.24、1/3 为正；10-09 是 −12.41 / −1.16 / +2.52 —— **符号既不跨家族一致、也逐日漂移**，所以本来就还需要样本才能下结论 | 1.1 的门槛不变：满 5–7 天新鲜样本后判 IV−RV 符号是否稳定；稳定且扣掉成本仍为正才接策略（`options-rv-horizon` 的期限对齐已完成，样本序列现为 3 天连续） |
| `leg-tradfi-1` | TradFi 腿 · 第 1 步：先把「哪些 X* 真的是 TradFi」定下来 | **✅ 完成** | README §62：三个判别法里两个被证伪（contTdSwTime 非空 122 个含 2Z/ACT；「代币化股票只在模拟盘」假）；`instCategory` 才是资产类别（live: 1 币 / 3 股 / 4 商品币），但**模拟盘那份不可用** —— 它与实盘在 8 个 id 上不一致，把 XRP 标成股票。无法从名称/元数据可靠判定，故回到显式名单：XPO(2024-07-24)/XUC(2024-11-04) 早于 2026-07-15 的股票计划 → 钉现货腿；XCU 有实盘永续且归 4（商品币）→ 留在 TradFi | 已新增 scripts/leg_identity.py（实盘目录 vs 模拟盘目录，逐行判定+失败即 rc=1）并接进每日任务；配置改动随**下次 runner 进程启动**生效 —— 当前无持仓、无 universe 成员受影响，故本轮不重启 |
| `leg-tradfi-book` | TradFi 腿 · 第 2 步：这条腿到底能不能交易（台面/时段/标的质量） | **✅ 完成** | README §63：① **交易所拒绝下单**，sCode **54092**『请先在 Web/App 的 TradFi 页面确认免责声明，主/子账户需分别确认后才能用 API 交易』（加密现货路径不受影响，故非我方代码问题）；② 20 条路由标的里**只有 3 条有双边报价**（XMU 0.9bps、XNVDA 50.3bps、XINTC 4591bps），其余 17 条**只有买盘没有卖盘**，原始接口复核同样（XTSM bids=5/asks=0，XAMD 两边皆空）；③ 原来的 7 个标的是按『有没有 K 线文件』挑的，**6/7 是死线**（收盘价从不变化：XSPCX 100%、XMSTR 100%、XQQQ 98.3%、XMRVL 98.6%、XINTC 90.1%、XNVDA 85.1%）；④ 时段结构：XMU 真 7×24（765/900 根 1h 有成交），XNVDA 只有 33/900 且集中在美股开收盘 | 已把 config-tradfi 的池子换成按『日线真实成交≥80% 且零变动<10%』筛出的 13 个，并删掉落在 [venues.binance_testnet] 下的重复 tradfi 块 |
| `leg-tradfi` | TradFi 腿（30% 额度） | **✅ 完成** | **1.0 范围内完成**：腿已接入实盘配置（13 标的 + 短 warmup 日线策略 + 全库最紧档位）、订单路径已实测（往返 21 bps）、可交易性每日两次自动记录（`venue_availability.py`）。**它至今 0 成交不是未完成，而是外部事实**：13 个标的里当前只有 XMU 同时有 K 线（95 根）与双边盘口，其余 12 个场馆此刻不供数据（磁盘上却有 84+ 根历史） | 成交与执行质量由 `tradfi-fills` / `venue-availability-track` 两行持续跟踪：今晚 21:10 的美股时段记录会判定那 12 个标的是"时段受限"还是"彻底不供数据"；若确认不供数据，1.0.1 里把 tier 收窄到真正能交易的标的 |
| `tradfi-fills` | TradFi 第一批真实成交（执行成本是否真是 21 bps） | **⏳ 等数据** | 策略回测 28 笔 / -0.14% / PF 0.34 —— 启用理由是用户要求承风险收数据，不是已证实的边缘；真实成交几乎都只会落在有双边报价的 XMU（XNVDA 50bps、XINTC 4591bps 都不可用） | 有成交后逐笔对比：实际成交价 vs 当时中间价、手续费、是否被深度闸门误挡；若执行本身有问题（例如滑点远大于 0.5bp）先修执行，而不是调参数 |
| `band-orphan-spot` | BAND 现货腿（2,084.13 ≈ $471）是裸多：处置它（平掉 or 补对冲） | **✅ 完成** | README §61.3 / §62：**已执行**（2026-10-10 01:05）—— 市价平掉 2,084.132066 BAND @0.227898861944（到手 ≈$474.56，手续费 $0.47）→ reconcile_ledger_to_venue.py --yes 补账本 → pairs.json 去掉 "seeded" 记录 → 重启 runner（91076→92241，restored carry pair state [ACT, CRV]） | 已生效：新 run 不再出现 `leg_positions: spot 3 >= 3`，carry 拒绝理由回到业务闸门（BAND 深度不足 / ETHW 冲击 500.6bps / SUI·NEAR 贴水）；对账脚本对 ACT 的 10 倍单位假差异待修 |
| `carry-slot-throughput` | carry 的现货名额吞吐：3/3 被占满导致合格标的进不来 | **✅ 完成** | **已按用户决定执行（2026-10-10 13:50，v0.4.6）**：spot 3→5、derivatives 2→5（含每日笔数 3→5）、account 12→17（每日笔数 14→16）；过程中发现真凶是引擎把 tier 上限拿**全账户持仓数**去比，已修（只看本 tier）+ carry tier 5→10（一对 = 两条腿） | 已生效：重启后同一 tick 内 **SUI 与 NEAR 两对开成**，账面 5 对（CRV/ACT/LTC/SUI/NEAR）逐腿对冲相等，入场 basis −10.2 / +4.5 bps 均过闸门；该修复同时解除了 TradFi 腿与方向性入场的静默锁死 |
| `passive-entry-fill-detection` | 被动入场：判定『挂上/成交/撤掉』必须读订单自身记录，不能靠挂单列表缺席 | **✅ 完成** | README §63：LTC 第一对真仓被买成两倍（约 $1,977 裸露 33 分钟，净成本 ~$6.3）—— 根因是『不在挂单列表里』把成交误判为撤销；已新增 CcxtVenue.order_state/OMS.order_state（state + accFillSz 为唯一权威）、删掉缺席启发式、查不到即拒绝回退；+2 条回归测试（273） | 观察下一次真实的被动入场：日志里应出现同一 client order id 只对应一笔现货单；若回退发生，原因是 order_state 明确回了 canceled 而不是『查不到』 |
| `risk-equity-units` | 统一风险引擎的权益口径（现在把空头按名义扣，和账本差一个对冲腿名义） | **✅ 完成** | README/changelog 2026-10-10T02:55：**已修**（v0.4.2）—— 新增 portfolio.marked_value 并由 PositionBook.equity() 与 runner._prepare_risk_account 共用；两口径原本差 $4,823.79（= 三条对冲腿名义），导致开新对时假报日亏 −2% 并暂停入场 | 已重启生效（92494→93128，entries_paused=0）；新对开出时不再触发暂停；附带把 sizing 基数从低估 4.7% 纠正回真实值 |
| `leg-options` | 期权腿（20% 额度） | **🔜 下一版本** | **移至 1.1**（随 path-5）：无 venue 适配、无策略；数据层（期权面 + IV−RV 每日行）已在每日任务里跑 | 1.1 时先做 venue 适配与最小仓位结构，再谈策略；在此之前期权 20% 额度保持空置（这是设计，不是故障） |
| `carry-postonly` | carry 被动入场（post-only 现货腿） | **✅ 完成** | README §66/68：前置（post-only 会被执行 = cancelSource 31、撤单可用 = cancelSource 1）与实现（OMS.cancel/open_order_ids、paired._passive_spot、runner._passive_entry_price）都完成；四种结局有测试，**撤单无法确认时拒绝市价回退**（否则现货腿翻倍） | 已在 BAND/USDT（真 carry 标的）验证过；下一次 carry 入场会走『挂买一 → 等 6s → 撤 → 回退市价』，日志里找 `passive`/`entry refused` 确认实际走的是哪条分支 |
| `depth-gate-all-entries` | 把深度闸门（max_entry_impact_bps）从 carry 专属提升为**所有入场** | **✅ 完成** | README §67：新增 sizing.depth_refusal() + runner._entry_depth_refused()，carry 分支改为调用同一实现，非 carry 入场提交前也调用；『查不到深度』= 拒绝，cap=0 关闭（纸面/回测不受影响） | 已随重启生效（runner 89479→90224）；新增 6 条测试（257→263），含一条真跑 _handle_signal 的覆盖测试：单边簿 session_orders=0 / 深簿 session_orders=1 |
| `carry-notional` | calibrate carry_notional_pct（现为刻意保守的 2%） | **⏳ 等数据** | changelog 2026-10-10T11:50：已有 **2 天**实测收入，但结构不合格 —— CRV 占两日收入 **97%**，其费率 **1,290.8 bps/天（≈每天 12.9% 名义）** 是池子中位（约 3 bps/天）的 **400 倍**且连续三天不回落，量级在真实市场不可能，**判定为模拟盘费率产物**；ACT 19.7 / LTC 2.0 bps/天才是接近中位的样本 | **明确推迟校准**（不按尖峰调 carry_notional_pct，现为刻意保守的 2%）；等出现跨标的、跨周、非尖峰的样本，或四道闸门筛出的 21 个标的中有若干进入中位区间再谈 |
| `carry-spike` | 验证「资金费尖峰会回落」（20 倍于稳态） | **⏳ 等数据** | changelog 2026-10-10T11:50：CRV 已**连续三天**维持 1,261 → 1,290.8 bps/天（37 次结算 / $347.43 / 2 日窗口），而稳态参照 ACT 19.7、LTC 2.0 bps/天；量级相当于每天 12.9% 名义，**判定为模拟盘产物而非可外推的 edge**，carry 两日收入 +$358 里 CRV 占 97% | 改判方向：不再等它『回落到 $2–3/天』来验证假设，而是把它当作**模拟盘环境事实**记录；真实可用的 carry 证据要看 ACT/LTC 这类中位量级标的的持续收入，以及 21 个合格标的的开仓情况 |
| `housekeeping-status` | changelog 的 status 字段回填（多处修好未更新，导致进度判断出错） | **✅ 完成** | README §69：18 条按证据结清（open→done 9、fixed-pending-restart→done 9），每条加 `resolved_note` 写明判据；只改 status 不改写正文；改前已备份 | 剩下 1 条 open 是真正待用户决定的（10-05 新币池/聪明钱/真钱）；**此后每轮收尾把 status 更新写进清单** —— 这正是误导的来源 |
| `history-freshness` | 磁盘日线历史的每日刷新（研究侧的唯一价格来源） | **✅ 完成** | README §70：所有 1d 文件曾冻在 2026-10-05（`ta data fetch` 是覆盖写），期权 RV 因此是常数；新增 `history.merge_bars()` + `scripts/refresh_daily_bars.py` 并接进每日任务，已把 BTC/ETH/SOL 900→903 根（最新 2026-10-08） | 每日任务里的 `refresh_daily_bars.py` 会在 options_cost 之后跑，所以 RV 最多滞后一天（30 天窗口里可忽略）；要更快就把刷新挪到步骤最前 |
| `options-rv-horizon` | 期权 IV−RV 的期限必须对齐（现在拿 7 天 IV 比 30 天 RV） | **✅ 完成** | README §71：改成先选合约再按该合约期限算 RV，新增 dte_days / rv_window_days / realized_dte_pct，并保留 RV30 与 prem30 两列做参考。SOL 由 −13.59 → **+2.52**（符号翻转），中位 −9.66 → −1.16 | — |
| `version-archive` | 版本号归档机制（策略优化放进版本里做） | **✅ 完成** | **已升级为专业版本管理（v0.4.7）**：`VERSION` 单一来源（`scripts/version_sync.py --check` 强制 pyproject/`ta.__version__` 一致，此前三者漂移到 0.2.0）；生成式 `CHANGELOG.md`（scripts/changelog_md.py）；`docs/RELEASING.md` 定义版本映射与 7 步发布清单；git 仓库 `e4f4864` + tag v0.4.6；归档 archive/versions/ 保留 10 个不可改写的冻结快照 | **1.0.0 保留给第一阶段（五步路径）完成** —— 即 path-5 的期权样本闸门打开（约 10-13/14）；此后每个版本按 RELEASING.md 的 7 步走：测试 → 进度表 → 留痕 → VERSION → 冻结归档 → CHANGELOG → git commit+tag |
| `perp-ctval-in-pnl` | 永续盈亏没有乘合约面值 ctVal（ACT 的盈亏被低估约 10 倍） | **✅ 完成** | README/changelog 2026-10-10T10:55（v0.4.5）：**已修** —— Position 新增 ct_val（默认 1.0）、unrealized 乘面值；CcxtVenue.contract_size() 从行情表读 contractSize 并用于 get_account；runner 重建资金簿后给永续持仓补齐 ct_val。ACT（ctVal 10）那一腿的盈亏不再被低估 10 倍 | 已随重启生效（99064→516）；3 条测试（286）；现货路径默认 1.0、零影响 |
| `runner-liveness-watchdog` | 看门狗判『交易循环是否还在做决策』（最后一个 tick 过久即告警） | **✅ 完成** | README/changelog 2026-10-10T09:35（v0.4.3）：新增 runner_stale_minutes=15.0，用账本最新 equity 事件时间戳算年龄，超时 critical；阈值取 15 是因为重启预热最慢实测约 12 分钟；+3 测试（278） | 已在当前代理中断事故中实时验证：看板报 `runner_stale_minutes critical 25.50 分钟（阈值 15.00）` —— 这类故障今后不再需要人工看时间戳发现 |
| `venue-outage-resilience` | 断网时的三级韧性：启动不崩、预热不磨、tick 快速失败 | **✅ 完成** | README/changelog 2026-10-10T10:00（v0.4.4）：connect() 不再让 load_markets 抛穿（否则 KeepAlive 每 10 秒重启一次，142 次启动横幅）；_markets_loaded 改为从 ccxt 派生（网络恢复即自愈）；prime() 连续 5 个标的全失败就提前返回（不抛）；refresh() 连续 5 个失败就整 pass 判定 degraded。+5 测试（283） | 已生产验证：崩溃循环停止（err.log 不再增长、pid 99064 稳定）；交易恢复仍取决于用户侧代理；恢复后 tick 应在约 1 分钟内重新出数据（而不是等之前那个 40 分钟的 pass） |
| `okx-api-ip-whitelist` | OKX API key 的 IP 白名单不认当前代理出口 IP（50110 复发） | **✅ 完成** | 2026-10-10：用户把 209.9.200.64 加入 OKX API key 白名单后恢复 —— 最后一条 IP 错误 10:40:50、第一个恢复 tick 10:43:28；看门狗自动清空（critical 104 分钟 → 无 breach） | 已恢复；停摆期间的数据已补齐（资金费账单 +$23.00、费率历史 140 更新、日线 903→904）。**防复发**：代理换节点时要同步更新白名单，或固定使用已放行的节点 |
| `venue-availability-track` | 『场馆此刻供不供数据』的每日自动记录（选标的不能只看磁盘） | **✅ 完成** | changelog 2026-10-10T12:15：新增 scripts/venue_availability.py（tradable/one-sided/quote-only/absent/bars-only 五态）并接进每日任务（09:10 时段外 / 21:10 时段内）；基线 tradable=1（XMU）、quote-only=11、absent=1（XAMD）；+2 测试（288） | 看今晚 21:10 那一行：若 12 个标的在美股时段内仍无 K 线/卖盘 → 把 tradfi tier 收窄到真正供数据的标的；若恢复 → 按『时段受限』设计（跳过非交易时段的信号） |
| `release-tooling` | 版本与代码管理的专业化（VERSION/CHANGELOG/RELEASING/git + 每日校验） | **✅ 完成** | changelog 2026-10-10T14:20（v0.4.7）：VERSION 单一来源 + version_sync --check；生成式 CHANGELOG.md + --check；docs/RELEASING.md（1.0.0 = 第一阶段完成；之后 MAJOR/MINOR/PATCH）；git init + e4f4864 + tag v0.4.6；每日任务新增 3 个校验步骤 | 1.0.0 等 path-5 闸门（约 10-13/14）；之后每轮收尾按 RELEASING.md 的 7 步清单执行，不再靠记忆 |
| `tradfi-tier-narrow` | 1.0.x · TradFi tier 收窄到真正能交易的标的 | **⬜ 未开始** | 13 个标的里只有 XMU 同时有 K 线与双边盘口，其余 12 个在 1d/1h/15m 上都是 0 根（11 个有买无卖）。**但所有观测都在盘外**：changelog 2026-10-10T18:55 核对时区后发现，每日任务两个槽位 09:10 / 21:10 CST = 01:10 / 13:10 UTC，而美股常规时段是 13:30–20:00 UTC（21:30–04:00 CST）—— **两个槽位都不在盘中**，所以这条决策链按现有排期永远拿不到输入 | **先解决排期**（等用户决定，见下一行）：把 21:10 挪进盘中，或加一个只跑 `venue_availability.py` 的轻量任务。有了盘中观测后 `scripts/tradfi_tier.py`（已就绪）会直接给出 keep 列表与『该收窄的 N 个』，再改 `[universe.symbols_by_tier].tradfi` 并发 1.0.4 |
| `tradfi-session-slot` | tradfi 决策的前置：在美股时段内加一次采集（现有两个槽位都在盘外） | **✅ 完成** | changelog 2026-10-10T18:55：美股常规时段 = 13:30–20:00 UTC = 21:30–04:00 CST，而每日任务是 09:10 / 21:10 CST —— 21:10 比开盘早 20 分钟，**没有任何一次采集落在盘中**。旧代码的 `13 <= hour` 还会把它误标成盘中（已修） | **已按方案②落地**（changelog 2026-10-10T19:10）：第 5 个 launchd 任务 `com.tradingagent.tradfi-session`，23:10 / 01:10 CST = 15:10 / 17:10 UTC（都在盘中），只跑 `venue_availability.py --write`（~15 秒）。已装好并验证（runs=1、exit 0、store 3→4 行），原有四服务未动。今晚 23:10 落第一条盘中样本 |
| `reconcile-tool-units` | 1.0.x · 对账脚本的 ACT 10 倍单位假差异 | **✅ 完成** | **已修（v1.0.1）**：`_venue_swap_positions` 曾把场馆仓位换算成 base 单位，而账本存的是张 —— ACT（ctVal 10）因此报 `ledger=-22912 venue=-229120` 的假差异。改成两侧统一用张；实测五条永续腿现在全部 ok、脚本报 nothing to correct。+2 测试（292） | 已提交并发 1.0.1；这条通道继续接收运营中发现的工具/风控修复 |
| `options-venue-adapter` | 1.1 · 期权腿 venue 适配（下单路径） | **✅ 完成** | **已完成（2026-10-10）**：① `[venues.okx_demo_option]` + `validate()` 放开 option；② 期权不下发 posSide/reduceOnly（单向持仓）但仍带 tdMode=cross；③ `ContractSpec` 补 `ctMult`（1 张 = ctVal×ctMult = 0.01 BTC，此前差 100 倍）；④ `_venue_symbol()` 做 instId ↔ ccxt 统一符号映射（按目录反查）并覆盖所有 ccxt 调用点；+3 测试（295 全绿） | 下单路径本身已就绪；买第一张之前的两项前置里，`options-book-survey` **已完成**（浅档价差中位 2.16%，不再是阻断项），只剩 `options-fund-premium-coin`（账户没有 BTC/ETH，币本位权利金拿不出币付；`_UM` 美元结算家族已实测无盘口，只剩换币一条路） |
| `options-minimal-structure` | 1.1 · 期权策略（买权利金的有限风险结构） | **🔜 下一版本** | 门槛：IV−RV 满 5–7 天新鲜样本且符号稳定（现 3 天：10-08/09/10；读数 BTC −7.25 / ETH +7.18 / SOL −0.24，逐日漂移）。**方向必须是买方**（最大损失=权利金），与『风险最小化』一致 | 结构已经不用现场设计：`docs/options-sleeve-spec.md`（changelog 2026-10-10T17:40）把买什么、买多远、买多少、什么时候平全部写成了数字。样本够后只需判一件事：`RV(30d) − IV(同到期)` 是否连续 ≥5 天 ≥1.5pp；成立就按说明书 §4 的闸门开门，不成立则 1.1 只交付适配+数据，不交易 |
| `options-sleeve-spec` | 1.1 前置 · 最小结构说明书（结构/闸门/退出/张数） | **✅ 完成** | changelog 2026-10-10T17:40：`docs/options-sleeve-spec.md`。币本位单腿 long call/put、平值 ±2.5%、DTE 25–45、标的 BTC/ETH；成本墙换算成波动率点后门槛定为 **RV−IV ≥1.5pp 连续 5 天**（σ=60% 时 1pp≈1.7% 权利金，而往返价差 1.5–2.2%）；退出 −60%/+100%/DTE≤7；张数单笔 ≤0.5% 权益、合计 ≤2%、首次只 1 张 | 唯一开放项是方向过滤器（日线 EMA20），已注明是最弱一环，1.1.1 用『平值跨式 + 永续对冲 delta』替代；执行仍卡在 `options-fund-premium-coin`（换 ~$100 USDT→BTC） |
| `options-gate-evaluator` | 1.1 前置 · 闸门每天自动判决（把 §4.1 的不等式变成可执行脚本） | **✅ 完成** | changelog 2026-10-10T16:47：`scripts/options_gate.py` 每天按 family 算『当前连续达标天数』（今天坏就归零），追加 `data/options/gate.jsonl`，已接进每日任务。首判：**BTC 连 3/5 天**（5.54 / 12.41 / 7.65），**ETH 连 0/5**（最新 −6.79，符号相反）→ 闸门关闭 | 每天继续累积：BTC 若保持，最早 10-12/13 达到 5 天；ETH 要先让 RV−IV 连续为正。开闸那天 gate.jsonl 会留下时间戳，再按说明书 §4 开门（前置仍是换 ~$100 USDT→BTC） |
| `options-sleeve-config` | 1.1 前置 · 参数落到 `config/options_premium.json`（`enabled=false`），闸门脚本改读它 | **✅ 完成** | changelog 2026-10-10T18:30：说明书 §4 的全部数字（DTE 25–45 / 平值 ±2.5% / 价差 ≤3% 且双边 ≥2 档 / 单笔 ≤0.5% 权益、合计 ≤2% / −60%、+100%、DTE≤7 / 闸门 5 天 1.5pp）只在一处存在。刻意做成数据文件（同 `config/alerts.json` 的理由：改阈值不动代码、写错退化到默认值）；`options_gate.py` 读它并把 `enabled` 与选档规则一起打印 | 开闸日三步都在纸面上：① 把 `enabled` 改 true；② 换 ~$100 USDT→BTC；③ 用 1 张最小单跑通『下单→成交→记录价差/费用→平仓』。测试 `test_the_shipped_config_file_ships_disabled` 会守住第 ① 步不被手滑执行 |
| `carry-rotation-rule` | 1.2 · carry 换腿规则（名额 5/5 满，轮换是唯一进场方式） | **✅ 完成** | changelog 2026-10-10T16:20：**检测侧已落地**（`scripts/carry_rotation.py`，只读、离线、已接进每日任务）。四条硬判定：中位数而非均值、窗口内每一天都要低于 floor、持仓 <24h 不动、`breakeven = 44.9/(候选−当前)` 必须 <30 天。首次实测（3 天窗、floor 1.0）：ACT 1.5 / CRV −0.24 / LTC 0.114 / NEAR 2.485 / SUI 2.269 bps/天 → 不提换腿；CRV 三日序列 3 / −3.004 / −0.24，**中位数已转负** | 第二半是『让 runner 真的执行换腿』—— 属于会改交易行为的策略决定，**等用户拍板**（见下一条）。检测侧会持续记录，CRV 若明天仍为负就第一次满足 stale 判定 |
| `carry-rotation-execution` | 1.2 · 换腿执行：工具已就绪（`scripts/carry_rotate.py`，默认 dry-run），开启与否等用户决定 | **🔜 下一版本** | **执行工具已落地（changelog 2026-10-10T22:10）**：`scripts/carry_rotate.py` 只做『平旧不开新』，三条防护写死（两腿必须齐全 / 先买回永续再卖现货 / 单笔 ≤5% 权益、每日 ≤1 次），默认 dry-run，只有手动 `--yes` 才动仓；开新对交还 runner 的 carry 入场逻辑。实际执行路径与已在 2026-10-08 实跑过的 `carry_unwind_all.py` 相同 | **仍需用户一句话**才能让某次换腿真的执行（脚本不会自动跑，也不在每日任务里）。触发仍由检测器给：连续 3 天中位 <1 bp/天且回本 <30 天；当前报告无提案（CRV 中位已回到 0.9 附近） |
| `onchain-1h-hypothesis` | 1.3 · 链上 +1h 高换手假设的正式判定 | **⏳ 等数据** | 每轮事件研究里『20+ 桶中位 −1.3~−1.6% vs 低桶 ~0%』方向连续同向，但**低换手桶只有 6–7 个代币**，未达『两侧各 ≥20 代币』的判据；+5m 覆盖仅 1%，属结构性不可测 | 等低换手桶累积到 ≥20 个代币再看是否提出；即使成立，方向是做空而 DEX 无借券，执行上暂时不可得 |
| `onchain-cost-wall` | 1.3 · 链上执行的成本墙（$200 单往返 1.4%） | **⏳ 等数据** | 已两次证明频率换不来正收益；链上任何方向性信号必须先跨过 1.4% 的往返成本，目前没有信号做到 | 只把链上当作研究/信号源；除非出现『扣掉 1.4% 仍为正』的样本，否则不进执行层 |
| `options-probe` | 1.1 · 期权合约与最小单的只读探测（写适配器前的事实） | **✅ 完成** | changelog 2026-10-10T16:10：新增 `scripts/options_probe.py`（公开端点、不签名不下单）。事实：BTC-USD 1336 个合约；币本位 spec ctVal=1/ctMult=0.01/minSz=1/tick 0.0001/settle=BTC；最小单 0.0006 BTC ≈ $49.52；ETH 同类 ≈ $2–18；盘口 5x5 双边。**并修掉我自己第一版把 ctVal 当乘数导致的 100 倍高估** | 适配器按这些事实写；`_UM` 美元结算家族的报价待核 |
| `options-book-survey` | 1.1 前置 · 模拟盘期权盘口调查（宽度中位与尾部） | **✅ 完成** | changelog 2026-10-10T15:50：**已量完**（`scripts/options_book_survey.py`，60 个报价）。BTC/ETH 在 ±10% 行权价、13–48d 上：**全部双边**，spread/mid 中位 **2.16%**、p90 5.13%、买方单边中位 1.08%（BTC 2.26% / ETH 1.96%）。早先那个『≈59%』的单点是 −17.7% 的深实值档，不在这个带内 —— 浅档是 2% 量级，深档宽一个数量级 | 1.1 接策略时用这条临时阈值：**只做单边价差 ≤ 2% 的档**（≈ spread/mid ≤ 4%，覆盖约 90% 的样本），深度档与深实值/深虚不进候选；阈值等有了 IV−RV 判据再算成与模型收益挂钩的数 |
| `options-fund-premium-coin` | 1.1 前置 · 备好币本位权利金（账户没有 BTC/ETH） | **✅ 完成** | 账户非零币种只有 USDT（$90.7k）与 carry 腿的币，**没有 BTC/ETH**；而 OKX 期权是币本位，最小一单约 0.0006 BTC ≈ $50 必须以 BTC 支付。**二选一的 (b) 已实测否决**（changelog 2026-10-10T15:55）：美元结算的 `BTC-USD_UM` 有 634 个 live 合约，但抽 6 个到期的认购**没有一个有卖价**（2 个只有 1 档 5 美元的买价，其余空簿），同一时刻币本位对照合约是 4x4 档、价差 1.65% | **已完成（2026-10-10T19:30）**：`scripts/options_fund_premium.py` 买入 **0.00120705 BTC @ 82,843.90**，手续费 $0.099997（≈10 bps），账户现有 0.0012070525 BTC ≈ 够买 2 张最小单。**副作用（已知且刻意）**：这份库存暂不属于任何策略 → 看板常挂 `unmanaged_notional ≈ $99.97` warn + 每轮一条 `position BTC/USDT` 对账差异；把它记进账本会更糟（策略会把它当仓位卖掉，见 changelog 19:30） |
| `daily-job-retry` | 每日任务的有界重试与补跑清单（10-10 09:10 五步 rc≠0、无自动补跑） | **✅ 完成** | changelog 2026-10-10T15:15：一次代理抖动就把当天那一步判死。现在每步 3 次 / 间隔 60 秒（`--attempts` / `--retry-sleep`，或 `TA_DAILY_ATTEMPTS` / `TA_DAILY_RETRY_SLEEP`），异常也算一次失败；收尾把仍失败的写进 `data/daily_job_status.json` 并在日志打 `NEEDS RERUN`。步骤清单上移到模块级 `INPROCESS_STEPS` / `SCRIPT_STEPS` | 已生效于 09:10 / 21:10 两个槽位（下次自动运行即带重试）。若某步连续多天进 `needs_rerun`，那是确定性故障而不是抖动，按点名去修 —— 本轮就是这么做才挖出 `.env` 加载器那条 |
| `daily-job-env-loader` | 定时步骤的凭据加载：凡 `build_venue` 的步骤必须自带 `.env` 加载器 | **✅ 完成** | changelog 2026-10-10T15:18：launchd 单元只导出代理、不导出密钥，`venue_availability.py`（今天才进任务）与 `refresh_daily_bars.py` 因此**必然**抛 `API key is not set`。加载器统一到 `ta.config.load_env_file`，两个脚本补 `--env-file` 并在 `Config.load` 前调用；回归测试改走 `daily_research` 自己的步骤表，凡 `build_venue` 必检 `load_env_file` 且必须是同一个函数对象 | 已实测：带 launchd 环境整轮 14/14 步首跑全绿（含新接入的 carry_rotation）；`venue_availability` 已追加一行（XMU tradable / 11 quote-only / 1 absent）。今晚 21:10 是第一次带美股时段的记录，直接喂 `tradfi-tier-narrow` |
| `daily-job-visibility` | 把『需补跑清单』接进看板（v1.0.2 只写了状态文件，没有读它的一方） | **✅ 完成** | changelog 2026-10-10T17:05：看板新增 `services.daily_job`（缺文件/坏 JSON 都退化到 None）+ 一行渲染 + redflags 的 warn『每日任务需补跑』。重启后立刻读出修复前那次运行留下的真实状态（needs_rerun=[refresh_daily_bars.py]），随后带 launchd 环境重跑 14/14 全绿、红旗消失 | 以后任何一天只要重试后仍有失败步骤，看板会直接点名；`data/daily_job_status.json` 的 `age_hours` 也一并显示，避免把『上次运行』误读成『现在』 |
| `reserved-inventory` | 1.1 前置 · 让系统认识『保留库存』（$100 BTC 现在无人认领，看板常挂 warn） | **✅ 完成** | changelog 2026-10-10T19:30：手工买来的 BTC 未入账 → `ta_unmanaged_notional 99.97` 触发看门狗 warn，且每轮对账报 `position BTC/USDT: local=2.5e-09 venue=0.0012070525`。两条告警都是**真的**（确实有 $100 不在任何策略名下），但会一直挂着直到期权腿能自己持有它 | **已实现（changelog 2026-10-10T22:40，方案②）**：`config/reserved_inventory.json`（symbol → reason + max_usd），runner 把声明内且不超上限的持仓记成 `ta_reserved_notional` 并从告警里剔除（`unmanaged_notional` 数值不变），对账按符号跳过、金额照报；实测 breaches 归零。仍是**不要把它记进账本**（那会让策略把它当仓位卖出）。1.1 期权腿接管后，这个条目可以从文件里删掉 |
| `spot-td-mode-51000` | 1.0.x · 现货下单偶发 51000：runner 偶尔拿着 tdMode=cash 在 acctLv=3 账户上下单（原判断『reduce_only 导致』已推翻） | **✅ 完成** | changelog 2026-10-10T20:05（修正 19:35 的判断）：现货**卖出路径本身是好的** —— 同形状请求（sell + reduce_only）在探针里 12:00:19 成交。真实缺陷在运行期状态：`spot_td_mode` 由 `connect()` 从 acctLv 解析，**解析不到时兜底为 `cash`**，而 cash 在 acctLv=3 账户上被 OKX 拒（51000）—— 同一 sCode 今天出现三次：04:43:18 / 04:45:54（OKB/USDT **buy**）与 11:16:08（BTC/USDT sell） | **已修（changelog 2026-10-10T20:35）**：`resolve_spot_td_mode()` + `td_mode_resolved` 状态位（解析失败不再静默退到 cash）、`_create_order()` 在现货 tdMode 被拒时重解析并重试一次、runner 启动期对未解析状态直接告警；+5 测试（333 全绿）；重启后实测无告警、ticks 恢复。若将来再出现 51000，日志会直接给出一行『spot tdMode was NOT resolved』——那时看的是探针为何失败（代理/权限），而不是再去猜下单参数 |
| `refresh-pass-budget` | 1.0.x · 行情刷新轮次的墙钟预算（慢场馆不再能把一个 tick 拖成 86 分钟） | **✅ 完成** | changelog 2026-10-10T21:45：21:30 那次 86 分钟停摆里**一次失败都没有** —— 每个符号都在第二次尝试（~40s 超时）成功，所以 `consecutive_failure_limit` 永远不触发，pass 走完 63 个符号用掉 86 分钟。新增 `LiveFeed.pass_budget_seconds`（默认 120s，0=不限）：refresh 进入每个符号前检查墙钟，超预算即 `VenueDegraded`（复用既有降级路径，暂停新入场） | 已上线（runner 24658 重启后单 tick ~1 分钟）。仍待观察：预算触发时会否与 carry 被动入场的 6 秒等待叠加成『假降级』；若频繁触发，下一步是把预算做成配置项并按实测调（当前 120s 是从 63 符号 × 单符号最坏 40s 推出来的） |

**合计**：✅ 完成 39 · 🔜 下一版本 4 · ⬜ 未开始 1 · ⏳ 等数据 5。
<!-- PROGRESS:END -->

上表由 `docs/progress.json` 生成（`python3 scripts/progress.py`；`--check` 会检查过期）。
之所以**生成**而不是手写：2026-10-09 的一次进度盘点直接读了 changelog 的 `status` 字段，
结果得出错误结论 —— 有几十条早已修好的条目从没回填那一列。**手维护的状态表会以同样的方式漂移。**

## 0. 本轮改动：15m 提频 + 分层币种池 + 收紧风控

OKX 模拟盘接通后暴露了一批必须先修的问题，修完才谈优化：

| 问题 | 症状 | 修复 |
|---|---|---|
| 循环被网络抖动打死 | `NetworkError` 从 tick 抛出，`finally` 里的报告二次调用又把原始错误盖掉 | `resilience.py`：读 3 次/写 2 次退避重试 → `VenueDegraded`；tick 级异常隔离（连续 10 次才退出）；降级时只停新开仓、进程保活 |
| 账本混了多个 run / venue | 报警里出现 `cash: local=102638 venue=5000`（拿 paper 账本对 OKX 账户） | 每条事件带 `run_id` + `venue`；账本按 venue 分文件（`data/ledger.<venue>.jsonl`）；对账改成 **delta 口径** + `baseline.<venue>.json` |
| 模拟盘永远 `no_room` | 账户自带 1 BTC / 100 OKB / 1 ETH / 5000 USDT，占掉 95% 权益敞口，任何开仓都被拒 | 区分"机器人持仓"与"账户历史余额"；风控基准改为 `risk_equity = cash + 托管仓位`，历史余额只计入展示 |
| 卖不掉、被灰尘卡死 | 风控把**离场单也按开仓上限缩量**，卖出少了一点 → 剩余灰尘低于交易所最小额 → 每个 tick 重试一次 | **风控只拦"增加风险"，绝不拦"减少风险"**：reduce-only 单直接放行、不缩量；分批止盈若会留下小于最小额的残仓则整笔平掉 |
| 一个币走坏冻结全部 | 连续亏损暂停是账户级的，5 连亏后整个组合停摆 | 亏损连击改为**按币种**记账 + 币种级长冷却；账户级只保留日内亏损与回撤熔断 |

| 180 天回测 20 分钟跑不完 | `atr()`/`ema()` 每次遍历整条序列，策略里 `closes(bars)` 每次重建 17k 元素列表 → O(n²) | 指标按周期取窗口；策略只看 `history_window`（默认对齐实盘 300 根）。180 天三联回测：20 分钟+ → **2 分 45 秒** |
| 回测和实盘出场规则不一致 | 回测在策略未设止盈时自己补一个 tier 止盈，runner 不补 | 去掉回测里的兜底：两边都尊重"策略没给止盈就没有止盈"——典型的"研究≠实盘"bug |
| 抓取静默丢币 | OKX 深度翻页偶发失败，一次就丢掉 10 个最主流的币 | 单币重试 3 次；实测确认 `since` 分页能拿满 180 天，而 `after` 游标会被 ~1440 根深度上限截断（不要用） |
| 配置写 `take_profit_r_multiple = 0` 会静默停掉整个策略 | 只有 `trend_pullback`/`new_listing` 实现了"0 = 不设硬止盈"，`mean_reversion` 没有 → 止盈价等于入场价 → 成本闸门拒掉每一笔（1m 上 0 笔成交） | 统一约定：所有策略 `rr <= 0` 都表示"没有硬止盈" |
| **机器人试图卖你手动持有的底仓** | 首次实跑 OKX 模拟盘，日志出现 `sell BTC qty=1.0` / `sell ETH qty=1.0` / `sell OKB qty=100` —— 历史账本里 3 条 `adopted` 合成记录，把手动持仓算成了"机器人持仓" | `_managed_account` 在 `adopt_existing_positions=false` 时**排除 `adopted` 成交**；接管改为显式 opt-in（默认关） |
| OKX 现货任何离场单都会被拒 | `InvalidOrder: sCode 51205 Reduce Only is not available` —— 现货没有 reduceOnly 概念，导致**根本平不掉仓**（上面那三笔就是被这个误拒才没成交） | `CcxtVenue` 只在合约市场发送 `reduceOnly`；现货卖出本身就是减仓 |
| 报告收益率算出 +1985% | 分母用"机器人可控权益"（5000 现金）、分子用"账户总权益"（含 1 BTC 的 104,299） | 收益率与回撤统一按**风险权益**计算，报告里单列 `risk equity`；总权益仍照实展示 |
| paper 第二次运行必然对账失败 | 账本跨 run 延续、但模拟器每次重置 → "机器人有持仓、交易所没有" | `PaperVenue.seed()`：模拟器从账本续跑，与实盘语义一致 |

### 提频做了什么

| 项 | 之前 | 现在 |
|---|---|---|
| 决策周期 | 1h | **15m**（1h / 4h 做确认） |
| 策略数 | 2（突破 + 回归） | **4**（+ 回踩 trend_pullback、+ 新币 new_listing_breakout） |
| 币种池 | 4 个（BTC/ETH/SOL/OKB） | **27 个，分三层**（majors 4 / alts 13 / new 10） |
| 每 bar 决策 | 每个 tick 都重算 | **每根收盘 bar 只决策一次**（60s 轮询不会在同一根 15m K 线上重复下单） |
| 出场 | 固定止盈 + 移动止损 | **1R 分批止盈 + 保本止损 + 移动止损 + 时间止损** |

7 币种、31 个模拟日实测：**13.6 个信号/天，9.6 笔成交/天**（原 1h 配置约 1–2 笔/天），
298 次下单 / 298 次成交 / **0 次交易所拒单**，剩下的瓶颈是收紧后的每日笔数上限（这是想要的结果）。

### 风控怎么"更严"

| 层 | risk/笔 | 单币上限 | 每日笔数 | 点差上限 | 止损冷却 |
|---|---|---|---|---|---|
| majors | 0.30% | 12% | 6 | 12 bps | 60 min |
| alts | 0.20% | 8% | 8 | 25 bps | 90 min |
| new | **0.10%** | **3%** | 4 | 45 bps | **180 min** |

另外新增：组合**热手数**上限 3.5%（Σ\|现价-止损\|×数量 ÷ 权益）、单币种每日 2 笔上限、
点差闸门、**陈旧数据闸门**（数据落后超过 2 根 bar 就不交易）、亏损连击按币种长冷却。
总敞口从 60% 降到 45%，日内停开仓从 -2% 收到 -1.5%。

### 真实数据：抓取 → 分段回测（walk-forward）

合成行情只能验证机制，验证不了优势。所以真实 K 线现在会落盘，回测可以离线反复跑：

```bash
# ccxt 不读代理环境变量，必须显式给（或用 TA_PROXY）
export TA_PROXY=http://127.0.0.1:7897

python3 -m ta data fetch --venue okx_demo --timeframes 15m --bars 2880   # 30 天 15m
python3 -m ta data fetch --venue okx_demo --timeframes 1h,4h --bars 720  # 30 天 1h / 4h
python3 -m ta data list  --venue okx_demo

# 前 70% 调参区间 / 后 30% 样本外，同时给样本外做 3 倍成本压力
python3 -m ta backtest --source history --strategy trend_pullback --split 0.7
```

| 设计点 | 为什么 |
|---|---|
| 落盘 `data/history/<venue>/<SYMBOL>__<timeframe>.jsonl`，一根 K 线一行 | 可离线复跑、可版本管理，不必每次回测都联网 |
| 抓取按 300 根/页向后翻页，并**丢弃未收盘的那根** | OKX 单次上限 300；未收盘 K 线会"重绘"，回测里用它就是作弊 |
| `--split` 的样本外**照常喂足 warmup 历史**，但只在切点之后才允许开仓（`entry_after_ts`） | 指标需要历史热身，但收益只能算切点之后的，否则样本外被污染 |

**抓取过程暴露了一个容易搞错的区别**：`ta symbols check` 默认连的是**模拟环境**，
而 OKX 模拟环境只提供 ~2,395 个交易对，实盘有 ~4,646 个。所以"这里没有"不等于"OKX 没有"。
现在 `symbols check` 会同时查实盘并分两类报告：

| 币种 | 实盘 | 模拟盘 | 处理 |
|---|---|---|---|
| `ARB` `ENA` `MEW` `EIGEN` `VIRTUAL` | 有 | **无** | 从模拟池移除，切实盘时再加回来（已写在 config 注释里） |
| `TON` `POPCAT` | **无** | 无 | 彻底删除，不要再加 |

教训：**别猜币种名**。正确做法是用 `ta symbols scan`（按真实 24h 成交量 + 上线天数生成清单）
产出配置，再把 `ta symbols check` 当作提交前的门禁。
当前币种池：**20 个**（majors 4 / alts 11 / new 5：JUP、PYTH、ONDO、W、PENGU），全部与模拟环境核对过。

### 真实数据的 walk-forward 结果

> **结论先行：把样本从 30 天扩到 180 天之后，四个策略的样本外收益全部为负，30 天里看到的
> "正收益"是噪声。** 这一节记录两次测量的对比，以及为什么下一步不该继续调参。

#### 180 天样本（前 70% 内 / 后 30% 外，样本外 ≈ 54 天，OKX 真实 K 线）

| 策略 | 样本外笔数 | 样本外收益 | 胜率 | 盈亏比 | 每笔期望 | 在场时间 | 3× 成本后 |
|---|---|---|---|---|---|---|---|
| `trend_breakout` | 114 | -1.08% | 47.0% | 0.57 | -1.39 | 35.1% | -1.75% |
| `trend_pullback` | 115 | -0.54% | 24.6% | 0.45 | -0.57 | 1.4% | -1.31% |
| `new_listing_breakout` | 279 | -0.60% | 37.0% | 0.51 | -0.26 | 3.8% | -1.39% |
| `mean_reversion` | 227 | -0.76% | 45.9% | 0.69 | -0.29 | 9.5% | -2.24% |

**四个全负，且每一个在 3 倍成本下进一步恶化。**

#### 30 天 vs 180 天：为什么短样本会骗人

| 策略 | 30 天样本外 | 180 天样本外 | 说明 |
|---|---|---|---|
| `trend_breakout` | -0.22% | **-1.08%** | 变差，而且新加的"回踩确认"没有救回来 |
| `trend_pullback` | -0.46% | -0.54% | 基本一致地亏 |
| `new_listing_breakout` | **+0.18%** | **-0.60%** | **30 天唯一为正的，180 天转负** |
| `mean_reversion` | -0.07% | -0.76% | 放宽过滤后交易变多，但期望仍为负 |

30 天只覆盖一个市场状态。当时看起来"改进有效"的那一版（回踩确认、去掉硬止盈、提高量能门槛），
在 180 天上没有兑现 —— 这正是不该在 30 天样本上调参的理由。

#### 真正的瓶颈是成本，不是信号

180 天样本外的费用是 33–75 USD / 10,000 USD 本金（≈0.33%–0.75% / 54 天），
而同一期的净收益是 -0.54% ~ -1.08%。**费用和净收益同一个量级**，也就是说：

> 这些策略的**毛收益约等于零**（扣费前接近抛硬币），是手续费决定了符号。
> 3× 成本压力测试的衰减（0.67–0.79pp）比收益本身还大，也印证了这一点。

所以继续在 15m 上调参数、加过滤器，数学上很难翻越这道成本墙。**最高杠杆的下一步是把
开仓从市价（taker，8bps）改成挂单（maker，约 2bps）**，成本直接降四倍 —— 这是机械优化，
不需要新的预测能力。其次是换更慢的周期（让每笔的预期波动远大于成本），或者换边缘来源
（链上、跨品种动量），而不是继续磨同一批信号。

### 曾经的 30 天结果（保留作对照）

下面这张表是扩样前的测量，保留它是为了说明"短样本 + 反复迭代"有多容易自欺：

| 策略 | 样本外笔数 | 样本外收益 | 胜率 | 盈亏比 | 每笔期望 | 3× 成本后 | 结论 |
|---|---|---|---|---|---|---|---|
| `trend_breakout` | 39 | -0.22% | 52.2% | 0.79 | -0.75 | -0.41% | 负 |
| `trend_pullback` | 81 | -0.46% | 25.6% | 0.44 | -0.78 | -0.97% | 负 |
| `mean_reversion` | **14** | -0.07% | 25.0% | 0.35 | -0.59 | -0.17% | **样本不足，无法判断** |
| `new_listing_breakout` | 79 | **+0.18%** | 50.0% | **1.90** | **+0.53** | -0.05% | 唯一结构合理，但优势落在成本噪声里 |

怎么读这张表（这些是诊断，不是调参借口）：

- **四个策略的样本外收益全在 ±0.5% 以内**，而 30 天只覆盖**一个市场状态**。正确结论是
  "**没有证据表明有优势**"，不是"亏了所以失败"；
- `trend_breakout` 胜率 52% 但盈亏比 0.79 → **赢的比输的小**；出场分布 `止损 13 / 止盈 3`，
  说明大多数突破是假突破。这是结构问题，不是参数问题；
- `trend_pullback` 频率最高（81 笔）但胜率只有 25.6%，且**在场时间仅 3.8–5%** ——
  信号多不等于暴露多，大部分时间它空仓；
- `mean_reversion` 的过滤器（必须震荡 + 波动分位 <75% + 1h 确认）太严，30 天只出 14 笔，
  **笔数不足 30 就不该下结论**；
- `new_listing_breakout` 是唯一期望为正的（出场 `止盈 8 / 止损 4`，结构健康），
  但 3 倍成本后转负 —— **优势幅度 ≈ 成本幅度**，这是提频策略的典型死法。

> **对钱的含义**：现在**没有**任何一个策略通过"样本外 + 3 倍成本"这一关。
> 下一步不是调参，而是**先拉更长历史**（≥90–180 天，覆盖涨/跌/震荡），
> 再看这四个里有没有谁能在多段行情里活下来。用 30 天结果去调参数只会得到过拟合。

### 别被合成行情的回测数字骗了

合成行情是带状态切换的随机游走，**没有任何策略在随机数据上有优势**，所以回测结论必然是"扣费后不赚"。
它的用途是验证机制而不是收益：

- `trend_pullback` 在 5 币 × 6000 根 15m 上 384 笔，毛收益约 -0.7%，费用 1.5% → **成本决定符号**，
  所以 `min_edge_cost_multiple` 这类"入场前先算成本"的闸门是提频时最该加的东西；
- 出场分布是 `时间止损 134 / 止损 38 / 信号 7 / 止盈 0` → 15m 上 1.6R 固定止盈基本打不到，
  于是改为 **1R 分批 + 移动止损接管**（实测两种设置结果完全一致，因为硬止盈从未触发，它只会截断赢家）。

**真实结论只能来自真实数据。** 下一步应该用你 OKX 模拟盘的历史 K 线跑一次，
而不是相信随机行情上的任何数字。

### 超高频（1m）实测：成本墙在哪

结论写在最前面：**在 OKX 零售费率下，"超高频"不是更快的赚钱方式，而是更快的亏钱方式。**
这不是判断，是在你的交易所、你的费率、真实盘口上量出来的。一句命令可复跑：

```bash
export TA_PROXY=http://127.0.0.1:7897
python3 scripts/cost_wall.py --timeframe 1m     # 需先 ta data fetch --timeframes 1m
python3 scripts/cost_wall.py --timeframe 15m
```

它只回答一个问题：**一笔交易要抓住多大的波动才够付自己的成本？**

| 周期 | 中位每根波动 | 往返成本（10+1 bps/边） | 需要抓住 |
|---|---|---|---|
| **1m** | **4.71 bps** | 22 bps | **4.67× 中位波动**（BTC 要 11.1×） |
| 15m | 15.67 bps | 26 bps | 1.66× 中位波动 |

**点差本身也不给你机会**：实测 OKX 现货点差中位数只有 **0.86 bps**（BTC 0.01、ETH 0.04），
而 maker 手续费是 8 bps。"挂单赚点差"这条做市路径在数学上不存在 —— 能赚的点差比
要付的手续费小两个数量级。

#### 强行做 1m 的实测结果（14 天 × 4 个主流币，真实 K 线）

```bash
ta data fetch --config-dir config-hf --timeframes 1m,5m --bars 20160
ta backtest --config-dir config-hf --source history --strategy trend_breakout
```

| 变体 | 成交笔数 | 手续费 | 净收益 | **毛收益（扣费前）** | 净% | 毛% |
|---|---|---|---|---|---|---|
| 均值回归，成本闸门开着 | 71 | $25.58 | -$25.16 | **+$0.42** | -0.25% | **+0.00%** |
| 均值回归，成本闸门放开 | 917 | $320.14 | -$344.21 | -$24.07 | **-3.44%** | -0.24% |
| 突破，成本闸门放开 | 1059 | $341.51 | -$410.80 | -$69.28 | **-4.11%** | -0.69% |

三个变体的**毛收益全都约等于零**（+0.00% / -0.24% / -0.69%），而手续费随笔数线性增长：
71 笔 → $25.58，1059 笔 → $341.51。净收益 = 毛收益 − 手续费，**所以笔数越多，亏得越多。**

> 这推翻了"提频能赚钱"的直觉：**毛收益≈0 时，成交笔数是成本的乘数，不是利润的乘数。**
> 生产配置里那道成本闸门（`min_edge_cost_multiple` + `min_stop_cost_multiple`）把 1m 的
> 1059 笔砍到 71 笔 —— 它本身就在保护你。

#### 什么条件下超高频才成立

要满足"毛收益 > 往返成本"，只有三条路：

1. **费率级别**：拿到 maker 返佣（负手续费）或极低费率 —— 需要 VIP 高档或做市商计划，
   个人零售户没有。这一条就决定了绝大多数人做不了。
2. **点差可赚**：需要点差显著大于手续费。实测 BTC 0.01 bps vs maker 8 bps，差两个数量级，
   **被动做市不成立**。
3. **真·微观结构优势**：托管机位、亚毫秒延迟、订单簿失衡预测。你现在是
   `代理 + ccxt + 60s 轮询`，往返 100–300ms，连门槛都够不到。

**所以我没有写"超高频策略"** —— 写了也只是把上面那张表再跑一遍，多亏 3–4%。
唯一值得重新评估的前提是第 1 条（拿到 maker 返佣），并且要用你的真实费率
重跑一次 `scripts/cost_wall.py`。`config-hf/` 那套 1m 配置是**测量工具**，不是拿来实盘的。

### 有纪律的迭代：refit 协议（`scripts/refit.py`）

"边跑边调整、调到最优"这个需求本身没问题，问题在于**用什么判据决定"更好了"**。
凭眼睛看回测曲线迭代，在这个项目上已经失败两次（30 天 +0.18% → 180 天 -0.60%）。
所以判据被固化成一个工具：

```bash
# 候选参数改得更严一点，到底算不算进步？
python3 scripts/refit.py --strategy trend_pullback --set rsi_entry_max=35 --folds 3
```

**目标函数**：默认最大化**样本外收益**（`--objective` 可换 `calmar` / `expectancy`）。
"收益最大化 + 风险最小化"是**多目标问题，没有唯一最优解** —— 必须给约束。本工具默认：
样本外最大回撤 ≤ 15%、每折样本外笔数 ≥ 30、换手受每日笔数上限约束。

**接受规则**（四条全部满足才 ACCEPT）：

1. 在 **≥2/3 的折**上优于现有版本；
2. 折间**平均改善 ≥ 0.5pp**（大于成本噪声才认）；
3. 每一折的样本外笔数 ≥ 30（否则"没亏钱"可能只是"没交易"）；
4. 没有任何一折的最大回撤超过预算。

每次试验（**包括被拒绝的**）都追加到 `reports/refit-changelog.jsonl`，让你事后能看见
自己跑过的"跑步机"，而不是只记住成功那次。

**真实运行示例**（2026-09-29，6 个币 × 180 天 × 3 折）：

```
strategy trend_pullback | objective return | folds 3 | symbols 6 | base bars 17280
candidate: {'rsi_entry_max': 35}

fold                    incumbent                  candidate     delta
1           -0.305 n=83   dd=0.39        -0.114 n=14   dd=0.11      +0.191
2           -0.446 n=65   dd=0.46        -0.074 n=13   dd=0.07      +0.372
3           -0.468 n=68   dd=0.47        -0.092 n=17   dd=0.09      +0.376

REJECT: mean delta +0.313pp, improved 3/3 folds
  - fold(s) [1, 2, 3] have < 30 out-of-sample trades -> inconclusive
  - mean improvement +0.313pp < 0.5pp
  -> do not edit the config; this is noise, not edge
```

**这个例子值得记住**：候选版本 **3 折全部改善**，肉眼一定会接受它。但它把每折交易数
从 65–83 笔砍到 13–17 笔 —— "亏得更少"只是因为"交易得更少"，不是找到了优势；
改善幅度也落在噪声区间内。**工具拒绝了一个看起来更好的版本，这就是它存在的意义。**

**调整的杠杆顺序**（按实测影响力，不要跳步）：

| 顺序 | 手段 | 量级 |
|---|---|---|
| ① | 挂单开仓（maker vs taker） | 实测 Lv1 = **maker 8bps / taker 10bps**，往返 20→18bps；样本外 **+0.11pp** |
| ② | 放大周期（1m → 15m → 1h） | 每根中位波动 4.7bps → 15.7bps |
| ③ | 换边缘来源（链上、跨品种动量、资金费率） | 换赛道，不是调参数 |
| ④ | 参数微调 | 上限 ≈ 噪声（≈0.5pp） |

### 挂单开仓实测：能省多少，值不值

先纠正一个我先前的错误：我在杠杆表里写过"挂单能把往返成本从 22bps 降到 ~6bps"，
那是 **VIP 档（maker 2bps）**的假设。用 `privateGetAccountTradeFee` 查你的真实账户：

```
level=Lv1   maker=8.00bps   taker=10.00bps   （负号在 OKX 里表示"收取"，没有返佣）
```

所以 Lv1 的挂单只能把往返从 **20bps 降到 18bps（省 10%）**。回测已实现挂单模型
（挂在收盘价内侧 + `maker_offset_bps`，只有价格回落触碰才成交，超时 `maker_timeout_bars`
未成交则撤单并计入 `maker_missed`，成交按 maker 费率计费）：

```bash
# 策略参数即可切换，无需改配置结构
entry_style = "maker"      # 或 "market"
maker_offset_bps = 0.5     # 挂在收盘价下方多少
maker_timeout_bars = 3
```

**180 天样本外实测（6 个币，后 30%）**：

| 策略 | 下单方式 | 成交 | 未成交 | 样本外收益 | 手续费 | 胜率 |
|---|---|---|---|---|---|---|
| `trend_pullback` | 市价 | 64 | 0 | -0.41% | $28.74 | 18.2% |
| `trend_pullback` | **挂单** | 57 | 2 | **-0.30%** | **$23.36** | 17.2% |
| `mean_reversion` | 市价 | 292 | 0 | -0.82% | $109.79 | 46.5% |
| `mean_reversion` | **挂单** | 273 | 12 | **-0.71%** | **$92.76** | 45.3% |

**结论**：挂单在**两个策略上都稳定改善 +0.11pp**，机制清楚（手续费降 15–19%，且成交价更好，
代价是 2–12 笔未成交）。但它**没有把任何一个策略变成正收益**，而且 +0.11pp
**低于本仓库自己的 0.5pp 接受阈值** —— 按 refit 协议，这个改动应当判定为"方向正确但不足以采纳"。

原因还是那个比例问题：省下 15–19% 的成本，遇上一个需要改善 100%+ 才能转正的策略。
**所以我没有去建实盘那套挂单机制**（挂单/超时撤单/部分成交/未成交回退）——
在一个实测为负的策略上增加执行复杂度，只会增加生产事故面，不会改变符号。

### 链上成本墙实测：换了一堵墙，而且它**可以翻越**

CEX 侧的结论是"成本高到数学上不可能"（1m 需要抓住 4.67 倍中位波动）。链上是另一堵墙，
而且**形状完全不同：成本随交易规模线性上升**，所以"能不能做"必须按单笔规模回答。

```bash
export TA_PROXY=http://127.0.0.1:7897
python3 scripts/onchain_cost_wall.py --sizes 50,250,1000
```

工具做的是**真实往返报价**：用 SOL 买入代币，再把你实际收到的代币卖回 SOL，
差额就是这一趟的全部成本（含价格冲击），再加上优先费。只报价，不签名、不广播。

**2026-09-29 实测（SOL = $121.41，优先费 0.0005 SOL/笔）**：

| 代币 | 池子流动性 | $50 往返 | $250 往返 | $1000 往返 |
|---|---|---|---|---|
| USDC | $27.3M | -0.01% | -0.01% | 0.00% |
| BONK | $433k | 0.06% | 0.07% | 0.15% |
| PYTH | $431k | 0.12% | 0.21% | 0.44% |
| JTO | $1.4M | 0.31% | 0.35% | 0.42% |

（SOL/JUP 报价失败是预期的：SOL→SOL 是自成交，Jupiter 会拒；脚本按失败跳过，不猜。）

**三条从数据里读出来的规则**：

1. **深池近乎免费**（$27M 池子往返 ≈ 0.00%）—— 所以链上的成本问题只存在于**薄池**，
   也就是新币所在的地方。
2. **薄池的惩罚随规模线性放大**。把测得的斜率投影到我们的安全下限 `min_liquidity_usd = 50k`
   （即新币的典型池深）：

   | 单笔规模 | 往返成本（含优先费） |
   |---|---|
   | $200 | **≈1.40%** |
   | $500 | ≈3.35% |
   | $1000 | **≈6.61%** |

3. **存在一个最优规模窗口**。成本 = 优先费/规模（固定项，随规模**下降**）+ 冲击×规模/流动性
   （线性项，随规模**上升**）。在 $50k 池子上，两项在 **$100–200** 附近交叉：
   单笔小于 $100 时优先费成了主要成本（0.0005 SOL ≈ $0.06 ≈ 0.12%），
   大于 $500 时价格冲击开始主导。**所以链上不是"越大越划算"，也不是"越小越好"。**

**关键对比**：

| | 往返成本 | 典型波动 | 判定 |
|---|---|---|---|
| CEX 1m | 0.22% | 0.047%（中位） | **需要 4.67×中位波动 → 数学上不可能** |
| CEX 15m | 0.26% | 0.157%（中位） | 需要 1.66× → 极难 |
| **链上 $50k 池，$200 单** | **≈1.40%** | 新币常见 10%+ / 小时 | **可以翻越** |

> **这是本次调查唯一一个"墙可以被翻过"的结论。** CEX 上成本墙是死的（成本与波动同量级，
> 无论怎么调参都过不去）；链上往返 1.4% 面对新币动辄两位数的波动，**在成本上留出了空间** ——
> 于是约束从"成本"转移到了"**预测**"：你能不能在新币上做出比 1.4% 更好的方向判断。
> 而这正是下一节事件研究要回答的问题。

**由此得到的执行约束（写进 `config/onchain.toml` 的依据）**：

- 单笔规模控制在 **$100–200**，不要超过 $500（$50k 池子上 $1000 单要付 6.6%）；
- `sizing.max_position_pct_per_token = 2%` 与"同时 ≤2 个币"配合，天然把单笔压在几百美元量级；
- **优先费要单独记账**：它在小额单里是主要成本项，不能混进"手续费"里看不见。

### 把 GMGN 类信息变成**可验证**信号：`ta scan`

前面说过三次的坑：GMGN 的聪明钱/狙击/bundler 数据是**他人的历史行为**，
等你看到时你是在给它提供退出流动性。所以这些数据只有两种合法用法：

1. **安全过滤**（已有：`TokenScreener`，fail-closed）；
2. **打分排序**——但它是否真有前瞻性，**必须用事件研究证明，不能靠道理说服自己**。

`ta scan` 就是干第 2 件事的：

```bash
# 0) 产出候选清单（发现层）
python3 -m ta scan discover --limit 25 --write data/onchain/watchlist.json

# 定时扫描并落盘时间戳快照（每 5 分钟一轮）
python3 -m ta scan watch --mints-from data/onchain/watchlist.json --interval 300

# 事件研究：把快照与"同一代币在 +5m/+1h/+24h 的价格"对齐，按分数分桶
python3 -m ta scan evaluate --horizons 5,60,1440
```

**发现层用哪些来源，为什么**：

| 来源 | 用途 | 要凭据吗 |
|---|---|---|
| DexScreener `token-profiles/latest` + `token-boosts/top` | 公开 feed：此刻有人在推的代币——正是新币策略要交易的那批 | **不需要** |
| DexScreener `latest/dex/tokens/{mint}` | 逐币补真实流动性/价格/24h 量，让清单按**我们自己的成本规则**预过滤 | 不需要 |
| GMGN（已在 `config/onchain.toml` 配好） | 聪明钱/内部人/bundler 情报 | 需浏览器会话 cookie（`GMGN_COOKIE`）；没有就跳过，不影响其它来源 |

明确不做的事：**不狂刷任何站点**。请求走共享 `HttpClient`（缓存 + 退避重试 + 限速）——
一份候选清单不值得把 IP 刷进黑名单，能不能抓本来也取决于对方条款。

**2026-09-29 实测**（流动性下限用我们自己的 $50k）：

```
symbol          liquidity      24h vol  turnover    boost  sources
HOOKEDCAT          61,371    3,538,622     57.66      100  dexscreener:boosts
INUINK             95,139    5,027,230     52.84      100  dexscreener:boosts
PARASITE          177,465    2,232,536     12.58      100  dexscreener:boosts
Pumpoween          96,613      276,896      2.87      100  dexscreener:boosts
PAIR              118,543      178,576      1.51        0  dexscreener:profiles
```

把这些地址喂给 `watch`（真实多源快照 + 安全过滤）：

```
HOOKEDCAT  78.85 PASS
INUINK     83.61 PASS
PARASITE   76.01 REJECT   <- REJECT lp locked 0.0% < 90%
Pumpoween  79.37 PASS
PAIR       85.75 PASS
```

两点值得注意：**没有 GMGN cookie 也能跑**（GoPlus + RugCheck 的公开数据已够解析安全字段，
5 个里 4 个通过）；**安全过滤当场见效**（PARASITE 因 LP 锁定 0.0% 被拒）。
另外 HOOKEDCAT 的**换手率 57.7×**（24h 量是池子的 57 倍）是刷量/拉盘的典型特征，
这类指标应当进入打分而不是被放行。

> ⚠️ 能进清单**只说明池子够深、有人在推**，不代表它值得买。这是"被营销的横截面"，
> 不是"有优势的样本"—— 是否真有优势，只能由事件研究回答。

**关键设计：前瞻收益来自仓库自身。** 扫描器每轮都为每个代币记录价格，
所以"信号后收益"就是同一代币在后一个时间戳的价格 —— 不做任何事后重构，
**死亡代币（后续没有观测）计入缺失而不是被悄悄丢掉**，生存者偏差进不来。

它还会**主动拒绝**在样本不足时给结论。真实快照（1 秒间隔、没有 +5m 观测）的输出：

```
--- +5m ---  有前瞻数据的信号 0/9 (0%)
  ⚠️ 覆盖率 <30%：样本不足，下面的分桶**不能**作为结论
  分数桶            n       中位%       均值%     胜率%
  80+            0         -         -       -
=== 判定 ===
  样本不足以判定（每个分桶至少需要 20 个有前瞻数据的信号）
```

数学本身已用构造样本验证过（高分币 +20%、低分币 -10%、第二组无后续观测按缺失处理，
分桶结果正确 +20.00% / -10.00%）。

**判定口径**（写在工具输出里）：只有当**高分桶显著优于低分桶，且扣掉链上往返成本
（约 1.4% @ $50k 池）后仍为正**，这个打分才配叫信号。在此之前它只是排序。

**要跑多久才有结论**：每个分桶至少 20 个**有前瞻数据**的信号。以 5 分钟一轮、
几十个代币计，大约需要**几天到两周**的持续积累。这就是"边跑边验证"的真实成本 ——
没有捷径，因为任何提前下结论都只是又一次过拟合。

---

## 1. 目录结构

```
trading-agent/
├── config/                     ← 唯一的真相来源，全部行为都在这里
│   ├── app.toml                  运行模式、15m 基频 + 1h/4h 确认、分层币种池
│   ├── risk.toml                 风控规则（账户 / 单笔 / 持仓 / 熔断 / 板块 / 三层限额）
│   ├── strategies.toml           策略参数与启用开关
│   └── onchain.toml              链上新币安全过滤、仓位、执行、黑名单
├── fixtures/tokens.json       链上代币快照样例（换成 GMGN / Birdeye / GoPlus 真实数据）
├── fixtures/onchain/          四个数据源 + Jupiter 报价/构建 + RPC 模拟的录制样本
├── src/ta/
│   ├── cli.py                   命令行：run / backtest / screen / reconcile / doctor
│   │                              / onchain / symbols check|scan / data fetch|list
│   ├── config.py                TOML 加载 + 校验（缺失或矛盾的配置直接拒绝启动）
│   ├── models.py                Bar / Signal / OrderRequest / Order / Fill / Position / AccountState
│   ├── util.py                  时钟（含虚拟时钟）、周期换算、ID 规范化
│   ├── log.py                   控制台可读 + 磁盘 JSONL 审计日志
│   ├── indicators.py            sma / ema / atr / rsi / zscore / donchian / 量比 / 波动率
│   ├── regime.py                盘面分类：趋势|震荡 + 波动率分位（极端波动不做）
│   ├── marketdata.py            多周期 LiveFeed / SyntheticFeed + 时间框架聚合
│   ├── resilience.py            ★ 重试 → 降级：24/7 循环不再被一次网络抖动打死
│   ├── baseline.py              按 venue 冻结起始现金/持仓，现金对账才有意义
│   ├── history.py               ★ 本地真实 K 线仓库：抓一次，离线回测无数次
│   ├── strategies/              base(MarketView) + 突破 / 回踩 / 回归 / 新币突破
│   ├── risk/engine.py           ★ 风控闸门：分层限额、热手数、冷却、每日笔数、点差
│   ├── execution/
│   │   ├── sizing.py            风险优先仓位、波动率目标化、ATR 止损、移动止损
│   │   └── oms.py               幂等下单（clientOrderId 永不复用）、状态跟踪、对账
│   ├── portfolio.py             持仓账本 + 追加式 Ledger（run_id/venue 隔离，重启重放）
│   ├── backtest.py              K线回放（多周期），**复用同一个 RiskEngine 与出场逻辑**
│   ├── onchain/                 链上层（见第 7 节）
│   │   ├── providers.py           DexScreener / GoPlus / RugCheck / GMGN 归一化 + 多源合并
│   │   ├── screener.py            安全过滤：未知字段 = 拒绝（fail-closed）
│   │   ├── jupiter.py             报价 + 构建 swap 交易（不签名）
│   │   ├── signers.py             refuse / mock / local-keypair / KMS / 硬件钱包
│   │   ├── rpc.py                 Solana JSON-RPC：模拟 + 双重同意后的广播
│   │   ├── executor.py            编排 snapshot→screen→risk→quote→build→simulate→sign
│   │   ├── http.py                带重试 / 缓存 / 离线模式的可注入 HTTP 层
│   │   └── models.py              TokenSnapshot / Quote / SwapPlan / ExecutionResult
│   ├── monitoring.py            Prometheus 文本指标 + JSONL 告警 + webhook
│   └── runner.py                ★ 主循环：行情 → 熔断 → 止损 → 信号 → 风控 → 下单 → 对账
├── scripts/smoke_test.py      离线端到端自检（43 项检查，不联网、不下单、不需要密钥）
├── scripts/cost_wall.py        成本墙测量：一笔交易要抓多大波动才够付成本
├── scripts/onchain_cost_wall.py 链上成本墙：真实 Jupiter 往返报价，按规模算成本
├── src/ta/onchain/study.py     快照仓库 + 事件研究（覆盖率不足时拒绝下结论）
├── src/ta/onchain/discovery.py 发现层：公开 feed → 候选清单（按自家成本规则预过滤）
├── scripts/refit.py            有纪律的参数迭代：3 折样本外 + ACCEPT/REJECT 判据
├── reports/refit-changelog.jsonl  每次调参试验的记录（含被拒绝的）
├── tests/                     90 个单元 / 集成测试（test_core.py + test_onchain.py）
├── .env.example               密钥模板（永不提交真实值）
└── Makefile                   make smoke / test / paper / backtest / onchain-*
```

---

## 2. 依赖

| 层 | 依赖 | 说明 |
|---|---|---|
| 核心运行时 | **无**（仅 Python 3.11+ 标准库） | tomllib、asyncio 不需要、http.server、urllib |
| 实盘/Demo | `ccxt>=4.3` | `pip install "trading-agent[live]"` 或 `pip install ccxt` |
| 真实数据抓取 | 同 ccxt + **本机代理** | ccxt 不读代理环境变量，需 `TA_PROXY`（见第 4 节） |
| 研究 | pandas / numpy / pyarrow / duckdb | `[research]`，只在做数据分析和因子研究时用 |
| 链上数据 | **无**（urllib + 标准库） | 行情/安全数据用 HTTP，重试与缓存自己实现 |
| 链上真实签名 | solders（可选 solana / web3） | `[onchain]`。不装也能跑通报价与 mock 签名全流程 |
| 开发 | pytest / ruff / mypy | `[dev]` |
| 运维（可选） | systemd 或 launchd、Grafana、nginx+TLS | 进程守护、看板、指标端点的外部暴露 |

```bash
cd trading-agent
python3 scripts/smoke_test.py          # 零依赖，先确认整套链路是通的
python3 -m unittest discover -s tests   # 或 make test
pip install ".[live]"                   # 需要接入 OKX Demo / Binance Testnet 时
```

---

## 3. 五分钟跑通

### 怎么跑起来（本轮实跑验证过的两条路径）

```bash
cd trading-agent && export PYTHONPATH=src

# A) 本地模拟盘 —— 不需要网络、不需要密钥，能立刻看到完整成交与对账
python3 -m ta run --venue paper --iterations 3000 --interval 0 \
        --strategy trend_pullback --strategy mean_reversion --config-dir config

# B) OKX 模拟盘 —— 需要代理 + 密钥（脚本会自动 source .env）
export TA_PROXY=http://127.0.0.1:7897
TA_STRATEGY=all ./scripts/start_demo.sh          # 后台常驻，默认跑全部策略
# 或前台有限周期演练（推荐先这样验证）：
set -a; source ./.env; set +a
python3 -m ta run --venue okx_demo --iterations 2 --interval 20 \
        --enable-trading --config-dir config
```

本轮实跑（2026-09-29）的结果：

| 路径 | 结果 |
|---|---|
| paper 3000 周期（31 天） | 236 单全部成交、`reconciled True`、热手数 0.21%、被每日笔数上限拦 85 次、收益 -1.63%（其中手续费 0.85%） |
| okx_demo 2 周期（真实账户） | 连接正常、`sandbox=True`、账户读取正常、**`orders_submitted 0`**、`managed 0 / unmanaged 99,299`、`reconciled True` |

> ⚠️ **跑之前先确认 `[account] adopt_existing_positions`**：默认 `false`，
> 意味着模拟盘里那 1 BTC / 100 OKB / 1 ETH（以及任何你手动持有的币）**不会被机器人动**。
> 设成 `true` 会给它们挂上止损止盈，**并在触发时卖掉它们** —— 只有当你确实想把一笔底仓交给
> 机器人保护时才打开。这条标志位是首次实跑时差点卖掉手动持仓之后加的。

```bash
export PYTHONPATH=src          # 未安装为包时使用；make 已内置

python3 -m ta doctor --config-dir config                 # 环境 / 配置 / 密钥自检
python3 -m ta symbols check --venue okx_demo              # 逐币校验（需代理，无需密钥）
python3 -m ta symbols scan  --venue okx_demo --top 40     # 按真实成交量生成可粘贴清单

# 真实 K 线：抓一次落盘，然后离线做 walk-forward 回测
export TA_PROXY=http://127.0.0.1:7897
python3 -m ta data fetch --venue okx_demo --timeframes 15m --bars 2880
python3 -m ta data fetch --venue okx_demo --timeframes 1h,4h --bars 720
python3 -m ta backtest --source history --strategy trend_pullback --split 0.7

# 合成行情：只用来验证机制，不用来判断优势
python3 -m ta backtest --strategy trend_pullback --bars 3000 --stress
python3 -m ta run --venue paper --strategy trend_pullback --iterations 3000 --interval 0
python3 -m ta screen --file fixtures/tokens.json          # 链上新币安全过滤
python3 -m ta reconcile --venue paper --config-dir config # 从账本重放持仓

# 链上：合并四个数据源 → 安全过滤 → 风控 → Jupiter 报价 → 模拟 → mock 签名
python3 -m ta onchain screen --mint 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU --offline
python3 -m ta onchain execute --mint 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU \
        --offline --amount 0.2 --equity-usd 5000 --sign
```

> `--stress` 会用 3 倍手续费加滑点再跑一遍并给出收益衰减。**合成行情上的收益率没有任何参考价值**，
> 它只用来验证代码路径、风控是否生效、成本是否吃掉策略。

---

## 4. 接入 OKX Demo Trading

> 中国大陆网络下需要代理：`export TA_PROXY=http://127.0.0.1:7897`。
> `scripts/start_demo.sh`、`ta data fetch`、`ta symbols check` 都走这个变量
> （ccxt 自己**不读** `HTTPS_PROXY`，必须显式传，代码里已处理）。

1. 在 OKX 里切到 **模拟交易（Demo Trading）** 环境，**在该环境内**创建 API Key。
2. 权限只勾选 **读取 + 交易**。**提现权限必须关闭**，并绑定 IP 白名单（你的 VPS 出口 IP）。
3. 记下 API Key / Secret / **Passphrase**，写入环境变量（不要写进仓库）：

```bash
cp .env.example .env && ${EDITOR:-vi} .env
set -a; source .env; set +a
export PYTHONPATH=src

# 先空跑：只拉数据、只做风控判断，不发送任何订单
python3 -m ta run --venue okx_demo --strategy trend_breakout

# 确认日志里的报价、余额、下单参数都对，再真正发到模拟盘
python3 -m ta run --venue okx_demo --strategy trend_breakout --enable-trading
```

`config/app.toml` 里的 `sandbox = true` 会让 ccxt 带上 `x-simulated-trading: 1` 头，
请求打到模拟环境而不是实盘。**没有 `--enable-trading` 时永远只记录不下单**，
这是防手滑的第一道闸门（`OMS` 的 `dry_run`）。

## 5. 接入 Binance Testnet

1. 到 <https://testnet.binance.vision>（现货）或 <https://testnet.binancefuture.com>（U 本位合约）申请测试密钥。
2. 填 `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_SECRET`。
3. 合约测试网把 `config/app.toml` 里 `venues.binance_testnet.exchange_id` 改成 `binanceusdm`。
   本骨架是**现货**骨架，`market_type` 改成非 spot 时 `doctor` 会直接报错提示。

---

## 6. 风控规则怎么读

`config/risk.toml` 全部字段都有实际代码路径，逐个生效：

| 配置 | 作用点 | 效果 |
|---|---|---|
| `per_trade.risk_per_trade_pct` | `sizing.risk_based_qty` | 入场价与止损价之间的距离决定手数 |
| `per_trade.max_order_notional_*` | `RiskEngine.pre_trade` | 超限不是拒绝，而是**自动缩量**（缩到低于最小名义额才拒绝） |
| `per_trade.max_price_deviation_pct` | `pre_trade` | 限价单挂太远直接拒绝（防胖手指与插针） |
| `per_trade.max_orders_per_minute` | `pre_trade` | 死循环保护；用注入的时钟计时，模拟盘不会误触发 |
| `positions.max_position_pct_per_symbol` | `pre_trade` | 单标的市值上限 |
| `positions.stop_atr_multiple` / `take_profit_r_multiple` | 策略默认值 | ATR 止损与 R 倍数止盈 |
| `positions.trailing_stop_*` | `runner._manage_exits` | 移动止损，只上移不下移 |
| `positions.atr_target_pct` | `sizing.vol_target_scale` | 波动率目标化，高波动自动降手数（0.3~1.5 倍夹逼） |
| `account.max_gross_exposure_pct` | `pre_trade` | 组合总敞口上限 |
| `account.max_open_positions` | `pre_trade` | 同时在场的标的数 |
| `account.min_cash_buffer_pct` | `pre_trade` | 永远留一部分现金，不允许满仓 |
| `account.max_leverage_pct = 100` | `pre_trade` | 现货：不可能花超过可用现金 |
| `clusters` | `pre_trade` | 板块（majors / layer1…）合计敞口上限，防"三倍做多同一个 beta" |
| `circuit_breakers.daily_loss_stop_open_pct` | `on_tick` | 触发即暂停开新仓 + 冷却期 |
| `circuit_breakers.daily_loss_flatten_pct` | `on_tick` | 触发即全部平仓 |
| `circuit_breakers.max_drawdown_kill_pct` | `on_tick` | 触发即 **kill switch**：写 `KILL_SWITCH` 文件、撤单、平仓、进程退出 |
| `symbols.whitelist` / `blacklist` | `pre_trade` | 未在白名单的标的一律拒绝 |

`KILL_SWITCH` 是落盘文件，重启也不会自动解除——必须人工删掉文件并确认行情，
这是有意的设计（防"重启一下继续亏"）。

紧急停止：

```bash
touch KILL_SWITCH          # 下一个 tick 就会撤单 + 平仓 + 退出
rm KILL_SWITCH             # 人工确认后解除
```

---

## 7. 链上部分：数据接入 + 报价 + 模拟签名

### 7.1 固定顺序（每一环都能单独喊停）

```
多源数据       DexScreener(流动性/成交量/价格/币龄) + GoPlus(权限/持币分布/锁池)
              + RugCheck(风险扫描/锁池比例) + GMGN(聪明钱/内部人/rug 比例)
   ↓ 合并
TokenSnapshot  每个字段带来源(provenance)；缺失的安全字段 = 直接拒绝，绝不"默认安全"
   ↓
TokenScreener  蜜罐 / 未锁池 / 税率 / 持仓集中 / 流动性 / 币龄 / 成交量比
   ↓
RiskEngine     与现货共用同一个闸门：单币仓位上限、同时持仓数、最小名义额
   ↓
Jupiter.quote  真实报价；价格冲击或滑点超过上限 → 直接否掉
   ↓
build swap     用 Jupiter 构建交易（base64，未签名）
   ↓
RPC simulate   模拟必须成功；失败就绝不签名
   ↓
sign           默认 mock（占位签名，永不可广播）；真实签名需要完整同意链
   ↓
broadcast      四条同意条件全部满足才会发生
```

### 7.2 三条命令

```bash
# 1) 只做筛：合并四个数据源，打印每个字段来自哪个源，再给结论
python3 -m ta onchain screen --mint 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU --offline

# 2) 筛 + 风控 + 报价 + 模拟（不签名）
python3 -m ta onchain quote --mint <MINT> --offline --amount 0.2 --equity-usd 5000

# 3) 完整演练：再加一个 mock 签名（依然不可广播）
python3 -m ta onchain execute --mint <MINT> --offline --amount 0.2 --equity-usd 5000 --sign
```

`--offline` 用 `fixtures/onchain/*.json` 的录制样本跑通全流程，**不联网、不需要任何密钥**。
去掉 `--offline` 就是真实数据（需要网络；GMGN 若要登录态先 `export GMGN_COOKIE=...`）。

内置四个样本，正好覆盖四种结局：

| 样本 | 结局 | 停在哪一环 |
|---|---|---|
| `7xKXtg2C…AsU` (CLEAN) | SIMULATED | 一路走完，mock 签名，不可广播 |
| `3xKXtg2C…AsV` (THIN) | BLOCKED | 筛阶段：流动性 $12k < $50k |
| `9n4nbM75…J9E` (RUG) | BLOCKED | 筛阶段：蜜罐 + 锁池 0% + 前 10 持有 91% + 流动性 $4.2k |
| `4mKXtg2C…AsW` (IMPACT) | REJECTED | 报价阶段：价格冲击 4.5% > 3% |

### 7.3 关于 GMGN 的实话

**GMGN 没有公开、稳定、带 SLA 的交易 API。** 代码用的是它网页端的内部端点，随时可能变。
所以这一层是按"可替换的数据源"设计的：

- 端点在 `config/onchain.toml` 的 `[data.sources.gmgn]`，改配置不用改代码；
- 字段用容错路径解析（同一字段尝试多个 key，兼容 0.28 与 28 两种百分比写法）；
- 单个源挂掉不中断流程，只在 `notes` 里留 `source failures:`，并让缺失字段触发拒绝。

缓存语义是刻意收窄的：**真实运行永远是网络优先**，只有在网络彻底失败时才回退到缓存，
并把这个事实记进 `source_counts`（命令输出里的 `data sources` 一行）。
`--offline` 既不读也不写缓存——录制样本绝不会通过缓存泄漏进真实运行。

它的定位是**筛选与情报**（聪明钱数、狙击数、bundler 比例、rug 比例、dev 持仓），
执行走自建的 Jupiter + RPC 链路。

### 7.4 同意链：为什么不可能"手滑上链"

真实广播必须同时满足：

1. `execution.signer` 是真实签名器（不是 `refuse` / `mock`）
2. `execution.dry_run = false` **且** `execution.allow_broadcast = true`
3. 命令行显式加 `--i-understand-the-risk`
4. 用本地私钥时还需 `TA_ALLOW_LOCAL_KEY=1` 且 keypair 文件权限为 `600`

缺任何一条，流程停在 `signed` 并打印缺了哪个条件（`details.consents`）。
`mock` 签名的产物结构上像签名，但**在代码层面不可广播**（`broadcast_allowed=False`，
`Rpc.send_transaction` 直接拒绝）。

### 7.5 从演练切到真实签名

```bash
pip install "trading-agent[onchain]"        # solders，真实签名要用
export SOLANA_RPC_URL="https://<helius-or-quicknode-endpoint>"
```

| signer | 说明 |
|---|---|
| `mock` | 默认。演练用，永远不可广播 |
| `kms` | **需你自己实现**：把交易 message 交给 KMS 签名，私钥不出 KMS（代码里留了实现清单） |
| `hardware` | **需你自己实现**：在 Ledger 上人工确认，配多签使用 |
| `local-keypair` | 最后手段。需要 `TA_ALLOW_LOCAL_KEY=1` + 文件权限 600，只适合小额试验 |

链上第一笔真实交易的建议：钱包里只放"亏光也不影响生活"的钱，金额取风控上限的十分之一，
第一次广播前把 `simulate_transaction` 的日志逐行读完。

---

## 8. 上线阶梯（每一级都要满足条件才进下一级）

| 阶段 | 命令 | 通过标准 |
|---|---|---|
| 0 自检 | `python3 scripts/smoke_test.py` | 43/43（另有 `ta doctor` 配置校验） |
| 0.5 币种校验 | `ta symbols check --venue okx_demo` | 无 MISSING、无低于流动性下限的币种 |
| 1 真实数据回测 | `ta data fetch` → `ta backtest --source history --split 0.7 --stress` | **样本外**在 3 倍成本下仍为正，且样本外笔数 ≥ 30 |
| 2 模拟盘 | `ta run --venue paper --iterations 3000 --interval 0` | 无未解释的拒单；对账一致；熔断路径被测过 |
| 3 交易所模拟 | `TA_STRATEGY=all ./scripts/start_demo.sh` | 连续跑满 1~2 周；滑点与模型假设偏差可解释；`blocked by` 里的约束你能逐条解释 |
| 4 小额实盘 | 风险预算 1%（单笔 0.05%~0.1%） | 对账零差异；告警可达；kill switch 演练成功 |
| 4.5 链上演练 | `ta onchain execute --offline --sign` | mock 签名链路跑通；四类拒绝都能复现 |
| 5 阶梯放大 | 每 4~8 周且指标达标升一档 | 出现对账差异或滑点系统性偏离，退回上一档 |

上线前的检查清单：

- API Key 关闭提现 + IP 白名单 + 按策略分子账户
- 密钥只在环境变量 / Vault / 1Password CLI，`.env` 已在 `.gitignore`
- `KILL_SWITCH` 演练过至少一次（含持仓状态下）
- 对账一致（本地账本 vs 交易所账单 vs 链上余额）
- 告警能到人（Telegram / 飞书 / PagerDuty），且试过静音与恢复
- 灾难恢复：进程 kill -9 后重启不重复下单（clientOrderId 幂等 + 账本重放）
- 交易所 ToS 允许算法交易；记录留存与税务出口可用（`ledger.export_trades_csv`）

---

## 9. 运维与可观测性

```bash
python3 -m ta run --venue paper --iterations 1000 --interval 0 --metrics-port 9101
curl -s localhost:9101/metrics | grep ta_
```

指标：`ta_equity`、`ta_cash`、`ta_drawdown_pct`、`ta_daily_pnl_pct`、`ta_open_positions`、
`ta_orders_submitted_total`、`ta_orders_blocked_total`、`ta_fills_total`、`ta_fees_total`、
`ta_kill_switch`、`ta_heat_pct`、`ta_unmanaged_notional`、`ta_entries_today`、
`ta_venue_degraded`、`ta_stop_outs_total`、`ta_blocked_exits_total`、`ta_tick_errors_total`。

产物：

- `data/ledger.<venue>.jsonl` — 按 venue 分开的追加式事件账本，每条带 `run_id` + `venue`；
  重启后自动重放（同一条 `clientOrderId` 永不复用），`ta reconcile` 会按 delta 口径对账
- `data/baseline.<venue>.json` — 首次连上该 venue 时冻结的现金/持仓，现金对账的基准；
  换了账户或出入金后要 `ta reconcile --reset-baseline`
- `data/history/<venue>/*.jsonl` — 真实 K 线仓库（`ta data fetch` 写入，回测读取）
- `logs/ta.jsonl` — 结构化日志（审计）
- `logs/alerts.jsonl` — 告警记录（可配 webhook）

真实运行时挂在 systemd/launchd 下守护；指标端点默认只绑 `127.0.0.1`，
要远程看就套一层 nginx + TLS + 认证，别把 `/metrics` 直接暴露到公网。

**降级怎么读**：`run report` 里的 `venue degraded` 和 `blocked by` 是最该盯的两行。
降级 > 0 表示那段区间内网络断过、只停新开仓没有平仓；`blocked by` 里出现
`no_allowlist_configured` 说明你没给币种池（风控是 fail-closed，会拒掉一切）。

---

## 10. 本骨架故意不包含的东西

- **帮你偷懒的签名**：`MockSigner` 永远签不出可广播的东西；KMS / 硬件签名器只留接口和实现清单
- **HFT / 做市**：毫秒级需要托管机、专线、FPGA，本骨架是分钟~小时级
- **合约与杠杆**：`max_leverage_pct = 100` 且 venue 只接受 spot，改之前先想清楚强平与资金费率
- **托管与合规**：不做代客理财、不做资金托管、不做税务申报
- **任何收益承诺**：合成行情的回测只用于验证代码；真实数据的 walk-forward 也尚未得出可用结论
- **帮你上真钱**：`refuse` 是默认签名器；KMS / 硬件签名器需要你自己实现并承担后果

---

## 11. 扩展点

**加一个策略**（三步）：

```python
# src/ta/strategies/my_alpha.py
class MyAlpha(Strategy):
    name = "my_alpha"
    @property
    def warmup(self) -> int: return 60
    def on_bar(self, symbol, bars, position): ...   # 返回 Signal 或 None
```

在 `strategies/__init__.py` 的 `REGISTRY` 注册，在 `config/strategies.toml` 加参数段，
并在 `risk.toml` 的白名单里放行对应交易对。

**加一个交易所**：实现 `venues/base.py` 的 `Venue` 接口（`fetch_bars` / `refresh_marks` /
`get_account` / `place_order` / `cancel_all` / `drain_fills`），其余层完全不需要改。

**接一个新数据源**：产出 `Bar` 列表（行情）或 `TokenSnapshot`（链上），塞进 `marketdata.py`
或 `onchain.py` 的适配器即可，风控与执行层不用动。

---

## 12. 合规提醒

OKX、Binance 均不向中国大陆居民提供服务；GMGN 是链上终端，但虚拟货币交易炒作活动本身
在中国大陆同样被禁止。工具的选择改变不了属地合规结论。本仓库是技术骨架，不构成投资建议，
也不能替代针对你所在地的法律与税务意见。

---

## 13. 当前状态与待办（交接清单）

**已经跑通并有证据的**

- 离线自检 43/43、单元/集成测试 **114** 个全绿、`ta doctor` 配置校验通过；
- **本地只读运营看板已上线**（`python3 -m ta dashboard`，第 15 节）：这次不只是"交易所里看到结果"，
  中间过程、风控拦截、策略参数、bug 修复与决策都有页面和结构化留痕（`docs/changelog.jsonl`）。
- 模拟盘 31 天（15m × 3000）：13.6 信号/天、9.6 成交/天，298 单 298 成交、0 次交易所拒单，
  对账一致，保本止损与分批止盈都被触发过；
- 真实数据管线跑通：20 个币种 × 15m/1h/4h 共 60 个文件已落盘，可离线反复回测；
- **真实数据的 walk-forward 已跑完**（四个策略，结果与解读见第 0 节）——结论是
  **暂无证据表明有优势**，且没有任何策略能扛住 3 倍成本。
- **三个 P0 修复已在生产循环里验证**（2026-09-30 干净重启后）：重启首单
  `buy OKB/USDT` 7 秒后落 `fill` 记录，`ta_fills_total=1`、`ta_orders_submitted_total=1`，
  `ta_unmanaged_notional` 从 $98,650 降到 0.0000664，假亏损 -3.998% 变成 -0.001%，
  `daily loss flatten` 刷屏消失（详见第 14 节）。
- **第四个 bug（手续费币种）已修并验证**：OKX 现货在基础币扣手续费，旧代码按
  `fee_bps` 估成 USDT，导致账本持仓比交易所多一个手续费 → 退出单报 `51008
  insufficient balance` 被拒、仓位卖不掉。改从交易所回执读真实手续费后，
  **账本与交易所逐位一致**（OKB 差 0.0000000000）。
- **全量 bug 复盘又补上 3 个**（2026-09-30）：对账只在关机时跑、灰尘占满仓位额度、
  卖单可能超过可用余额。三者都是"账本与交易所漂移且无哨兵"这一族。
  现在：手续费取交易所真值、卖出按可用余额截断、每 30 tick 强制对账一次。
  完整 12 个 bug 台账与收官实测见第 14.9 节。
- **链上研究补上两个诚实性指标**（2026-09-30）：零变动占比、活跃度过滤
  （`--min-txns-m5`，并把 `txns_m5/h1`、`volume_m5/h1` 整包写进快照）。
  实测零变动占比 **97.9%** → 此前所有链上"苗头"判定为**样本不成立**（第 14.10 节）。
- **资金费率/基差是第一个活下来的方向**（2026-09-30）：一次往返 34bps，
  但资金费是**可提前观测的现金流**、不需要预测方向。盈亏平衡天数 BTC 21 天、
  DOGE 17 天、SOL 37 天；持有 1 天净 -32bps（胜率 0%），持有 90 天 13 个币种全正
  —— 但 180 天里只有约 2 个独立观测，所以**还不能算结论**（第 14.11 节）。
- **取消 $600 绝对上限**（2026-09-30，用户决定）：`max_order_notional_usd = 0` 现在表示
  "未设上限"而不是"上限为零"（直接填 0 会让 `min()` 恒为 0，静默拦掉所有订单）。
  实测 majors 单笔上限 $5,168（8.6×）。组合级约束未动（5 仓 / 45% 敞口 / 3.5% heat /
  12 笔每日）。风险如实记录在第 14.6 节。
- **消除"跑错版本"的雷**（2026-09-30）：`site-packages` 里曾装着一份 9/29 的旧代码
  （今天 4 个修复全无、连 `scan` 子命令都没有），任何不带 `PYTHONPATH` 的
  `python3 -m ta ...` 都会跑到修复前的版本。已改成 `pip install -e .`，
  `ta` 现在恒定解析到 `src/`。

**待办（按优先级）**

1. **把开仓改成挂单（maker），不要继续磨信号。** 180 天样本里费用（0.33%–0.75%/54 天）
   和净收益（-0.54% ~ -1.08%）同量级 —— 毛收益≈0、手续费决定符号。市价 8bps 改挂单约 2bps，
   成本直接降四倍，这是**唯一不需要新预测能力就能改变结论**的改动。
   OMS 已支持限价单，需要加的是：策略给出挂单价、未成交超时转市价或撤单、把 maker/taker 分别记账。
2. **换周期或换边缘来源**，而不是在 15m 上继续加过滤器：要么回到 1h/4h 让每笔的预期波动
   远大于成本，要么换到链上/跨品种动量这类与"单币择时"不同的边缘。
3. **`mean_reversion` 的 signal 出场是净亏的**（180 天：signal_loss 35 vs signal_win 22）：
   即"回到均值就走"这条规则本身在扣费后不成立，值得单独验证。
4. 用 `ta symbols scan` 的真实结果替换手写候选池（已逐个核对存在性，但没按成交量筛选过）。
5. 链上部分：KMS / 硬件钱包签名器仍是接口 + 实现清单，**未实现**（这是有意的，见第 7.5 节）。

**已经证伪的假设（不要重复走一遍）**

- ❌ 15m 提频能靠"更多信号 × 风控收紧"变成正收益 —— 180 天样本四个策略全负；
- ❌ 回踩确认能救 `trend_breakout` —— 加完后样本外从 -0.22%（30 天）变成 -1.08%（180 天）；
- ❌ 去掉硬止盈、让移动止损接管能提高期望 —— 改了，样本外仍为负；
- ❌ 30 天样本能判断策略优劣 —— `new_listing_breakout` 在 30 天是 +0.18%，180 天是 -0.60%。

**已知的诚实边界**

- 合成行情的任何收益数字都不代表真实表现（随机游走上没有优势可言）；
- 合成行情的成交量现在与波动幅度相关（原来是与价格无关的噪声，导致所有带量能门槛的策略
  在离线测试里永远不成交——那是"尺子坏了"，不是"策略没信号"）；
- **闸门重的策略需要足够长的样本才会成交**：`trend_breakout`（突破+回踩确认+量能门槛）
  在 3000 根合成 K 线上常常 0 笔，`tests/` 与 smoke 因此统一用 8000 根；
- 1h/4h 是由 15m 聚合而来，**起点对齐方式会让同一策略的交易数显著变化**（实测同一策略
  65 根 vs 80 根 warmup 下从 15 笔变 0 笔）。合成测试据此选样本量，不要用单一种子下结论；
- 30 天只覆盖**一个市场状态**，样本外笔数 14–81 笔 —— 笔数不足 30 时不要下结论；
- 四个策略的样本外收益全在 ±0.5% 内，3 倍成本下全部转负 ⇒ **成本是第一阶变量**，
  任何"扣费前好看"的结构在 15m 上都不成立；
- 模拟盘不模拟滑点冲击、部分成交、资金费率和交易所停机，实盘前必须打折看结果。

---

## 14. 仓位清算与干净重启（2026-09-30）

> 用户决定：把模拟盘里所有"手动底仓"卖出，让机器人从一张白纸上按既定风控重新建仓。

### 14.1 卖出了什么

| 币种 | 数量 | 成交均价 | 金额 |
|---|---:|---:|---:|
| BTC/USDT | 1.000000 | 83,742.10 | $83,742.10 |
| OKB/USDT | 100.000 | 120.4908 | $12,049.08 |
| ETH/USDT | 1.000000 | 2,673.71 | $2,673.71 |
| INJ/USDT | 25.5933 | 7.67666 | $196.47 |
| PYTH/USDT | 1,237.016 | 0.077976 | $96.46 |

这五个仓位是 OKX 模拟账户自带/手工买入的，`adopt_existing_positions=false` 让它们一直躺在
风控之外（第 13 节与每日复盘都记过这笔账）。清算脚本内置安全断言：**只有** BTC=1、ETH=1、
OKB=100 且 USDT<6,000 时才执行，防止误对真实账户下手。

随后机器人自己在**旧代码**下买的那笔 4.9711 OKB（$600 上限那一单）也一并卖出，
让账户干净到只剩 USDT。

### 14.2 账户现在长什么样（实测核对，非推算）

| | 清算前 | 现在 |
|---|---|---|
| USDT | $4,704.35 | **$103,361.13** |
| BTC / ETH / OKB / INJ / PYTH | 1 / 1 / 100 / 25.59 / 1,237 | 只剩灰尘 |
| 交易所实际持仓 | 5 个 | 0 个 |

剩余灰尘：`PYTH 0.000745`、`INJ 0.00000108`、`OKB 0.00000092`，合计不到 1 美分，
低于任何交易所最小下单量。它们被写进 baseline，因此排除在风控之外。

### 14.3 本地账本必须跟着重置（否则数字是假的）

清算**不会**自动出现在本地账本里：账本还记着 `adopted` 的 1 BTC / 100 OKB / 1 ETH。
如果不处理，机器人下次启动会把 ~$98,650 的现金流入当成巨额盈利，把仓位消失当成对账差异。
旧文件全部**归档**（可恢复，未删除）：

| 归档文件 | 内容 |
|---|---|
| `archive/ledger.okx_demo.pre-flush.20260930-165215.jsonl` | 旧账本 2,049 行，含 393 条 `daily loss flatten` |
| `archive/baseline.okx_demo.pre-flush.20260930-165215.json` | 旧的 $5,000 + BTC/OKB/ETH baseline |
| `archive/alerts.pre-flush.20260930-165215.jsonl` | 旧告警 1,617 行 |
| `archive/ta.pre-flush.20260930-165215.jsonl` | 旧结构化日志 2,861 行 |

重启后 baseline 自动按**真实账户**重建：`cash=103,361.13`、`positions={灰尘}`。

### 14.4 重启后的第一轮：三个 P0 修复在生产里生效了

`./scripts/start_demo.sh` 拉起新实例（pid 34763，run_id `a13472df3025`）。第一次 tick：

```
08:53:12 INFO  runner start  [venue=okx_demo strategies=[trend_breakout, trend_pullback, mean_reversion, new_listing_breakout]]
08:53:24 order  buy OKB/USDT 4.98 (market)
08:53:28 fill   OKB/USDT qty=4.979831 price=120.466 fee=0.5999
```

| 指标 | 旧代码（重启前） | 新代码（重启后） |
|---|---|---|
| 市价单是否落账 | ❌ 永远 `status=open, filled=0` | ✅ 7 秒后落 `fill` |
| `ta_unmanaged_notional` | $98,650+ | **0.0000664** |
| `ta_daily_pnl_pct` | -3.998%（假亏损） | **-0.001%** |
| `daily loss flatten` 告警 | 每 tick 一条，旧账本 393 条 | **0 条** |
| `ta_fills_total` / `ta_orders_submitted_total` | 1 / 7（对不上） | 1 / 1 |
| `ta_kill_switch` | 0 | 0 |

提交与成交第一次真正对上了，非托管敞口归零。

### 14.5 重新规划的仓位结构（由 `config/risk.toml` 决定，不是拍脑袋）

账户权益 ≈ **$103,361**，100% USDT。生效的约束：

| 约束 | 配置值 | 在当前权益上等于 |
|---|---|---|
| 单笔名义上限 | min(权益 6%, **$600 硬顶**) | **$600** ← 真正的约束 |
| 同时持仓数 | 5 | — |
| 单币上限 | new 3% / alts 8% / majors 12% | $3,100 / $8,269 / $12,403（都被 $600 压住） |
| 组合总敞口 | 45% 权益 | $46,512 |
| 组合 heat（所有止损同时被打到） | 3.5% 权益 | $3,618 |
| 最小现金缓冲 | 可用现金 15% | — |
| 开仓预算 | 12 笔/天（组合）、2 笔/天（单币） | — |
| 单日 -1.5% | 停止开新仓 | -$1,550 |
| 单日 -3.0% | 全部平仓 | -$3,100 |
| 峰值回撤 -12% | 永久 kill switch | -$12,403 |

**所以实际会怎么跑**：最多 5 个持仓、每个约 $600，总名义 ≈ $3,000（约权益的 2.9%）。
这是小额试跑，不是在 10 万美元账户上重仓 —— 这是**故意的**，不是配置漏了。

### 14.6 决议：取消 $600 绝对上限（2026-09-30，用户决定）

$600/笔 是当初为 5,000 美元本金设的；本金涨到 20 倍后，它把风控的相对比例全压扁了
（majors 的 12% 单币上限，实际生效的只有 0.58%）。用户决定**取消这个绝对上限**，
让相对上限重新成为约束。

**实现方式（重要）**：`max_order_notional_usd = 0.0` 表示**未设上限**，不是"上限为零"。
风控里原来的写法是 `min(绝对上限, 权益×pct, 权益×tier pct)` —— 直接把 0 填进去会让
`min()` 恒为 0，**静默拦掉所有订单**，和"取消"正好相反。现在 0 会被排除在 `min()` 之外，
并加了回归测试钉住这个语义。

**生效后单笔上限（实测，权益 $103,359）**

| 层级 | 单笔上限 | 相对旧值 |
|---|---:|---:|
| majors（BTC/ETH/SOL/OKB） | **$5,168** | 8.6× |
| alts | $4,134 | 6.9× |
| new | $2,067 | 3.4× |

组合级约束**没有动**，仍然是：最多 5 个持仓、总敞口 45% 权益、组合 heat 3.5%、
每日 12 笔开仓、单币每日 2 笔、单日 -1.5% 停止开仓 / -3.0% 全平。

**风险提示（如实记录）**：CEX 四个策略在 180 天真实数据上样本外全负（-0.54% ~ -1.08%），
费用与净收益同量级。取消上限后，同样的负期望会按 8.6 倍的速度和规模体现。
这个决定换到的是**更贴近真实资金规模的执行样本**（部分成交、滑点、资金占用），
代价是模拟跑不再便宜。**账本可信 ≠ 策略为正**，这一点没有因为取消上限而改变。

### 14.7 复现这套重置

```bash
cd /Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent

# 1) 停掉旧循环（旧代码）
kill "$(cat logs/ta-run.pid)"

# 2) 归档账本与 baseline（保留可恢复副本）
mv data/ledger.okx_demo.jsonl   "archive/ledger.okx_demo.pre-flush.$(date +%Y%m%d-%H%M%S).jsonl"
mv data/baseline.okx_demo.json  "archive/baseline.okx_demo.pre-flush.$(date +%Y%m%d-%H%M%S).json"

# 3) 用修好的代码重启（会自动按真实账户重建 baseline）
TA_STRATEGY=all TRADING=1 ./scripts/start_demo.sh
```

第 2 步**必须**在机器人停止的状态下做，否则它会从"还没卖掉的仓位"重新生成 baseline。

### 14.8 追加：第二个账本 bug —— 手续费币种记错，导致仓位卖不掉

这是在你指出"OKB 还有仓位"之后查出来的第二处账本错误。

先说那个 OKB 的来历：账户在 08:52:06 已经被清成纯 USDT，机器人自己在 08:53:24
按 `mean_reversion` 信号买了 4.98 OKB（$600 上限）——**不是遗留仓位**。
但它暴露出一个问题。

**现象**：账本记 OKB = 4.979831，交易所实际只有 4.97485209，差 0.004979 OKB（约 $0.60）。

**根因**：`CcxtVenue._track()` 不用交易所的手续费，而是自己按 `fee_bps` 估：

```python
fee=notional * self.fee_bps / 10_000.0, fee_ccy=self.base_ccy
```

OKX 现货的手续费是**以基础币扣除**的（"用 OKB 抵扣手续费"）：订单回执是
`fee = {cost: 0.004979831, currency: 'OKB'}`，实际入账 4.97485209 OKB。
账本却记成 `0.5999 USDT` —— 折算金额对、**币种错**，于是账本持仓比交易所多出恰好一个手续费。

**后果不是"记账不好看"，而是仓位卖不掉**（生产日志实测，这才是关键）：

```
09:01:00 INFO order [OKB/USDT side=sell qty=4.979831 status=rejected tag=trend_pullback:exit
                     sCode=51008  "Your available OKB balance is insufficient"]
```

退出单按 4.979831 报，实际只有 4.97485209 → 报单被拒 → 仓位卡住。
这是同一类"平不掉仓"的 bug **第二次**出现（第一次是 51205 reduceOnly）。

**修复**

- `Order` 新增 `fee` / `fee_ccy` 两个字段，`_to_order()` 从交易所原始回执读取**真实**手续费与币种；
- `_track()` 按币种分流：基础币扣费 → 成交量记为**净入账**量、手续费折算成报价币计入成本；
  报价币扣费 → 原样透传；交易所没给手续费 → 才退回原来的 `fee_bps` 估算；
- 新增 `_fee_tracker`，分批成交时手续费不会重复计。

**端到端验证**

| | 账本 | 交易所 | 差 |
|---|---|---|---|
| OKB | 4.983562541 | 4.9835625410 | **0.0000000000** |
| USDT | 102,758.367252 | 102,758.36725239 | 0.00000039 |

这是本项目的账本第一次与交易所**逐位一致**。新增 5 个回归测试（含"分批成交不重复计费"），
测试总数 90 → **95**，smoke 43/43 仍全绿。

**为什么必须再重置一次账本**：旧账本里那条手续费记录就是"仓位卖不掉"的直接原因，
留着它等于把已知错误的持仓带进新一轮。所以 09:04 把 OKB 卖回纯 USDT，
归档（`archive/*.pre-fee-fix.20260930-170410.*`）后用修好的代码重启
（run_id `812caa1b1bd4`，baseline cash = **$103,358.47**）。

**注意**：`mean_reversion` 目前对 OKB 一直有信号，所以重启后机器人**又开了 OKB**
（09:04:48 买 4.99 OKB）——这一次账本是准的。如果你不想让机器人碰 OKB，
在 `config/risk.toml` 的 `[symbols] blacklist` 里加 `"OKB/USDT"` 即可。

### 14.9 全量 bug 复盘（2026-09-30，用户要求"重新梳理 bug"）

仓位已经统一（一个账本、一个 venue、和交易所逐位一致），所以做了一次从零开始的
bug 清点：既复查已知的，也主动找**同一类**还没暴露的。又找到 3 个。

#### 14.9.1 新找到的 3 个

**Bug #10：对账只在退出时跑，运行中漂移无人报警。**
`reconcile_and_report()` 只在 `finally`（关机路径）里被调用。所以一个持续运行的循环
**从不**和交易所对比；`kill`（SIGTERM）连 `finally` 都不会跑。
实测证据：账本高估 OKB 0.1% 持续一个多小时、退出单被拒，而
`reconciliation mismatch` 告警在整个 `pre-fee-fix` 告警文件里是 **0 条**。
→ 修：循环内每 30 个 tick（`TA_RECONCILE_EVERY_TICKS` 可调）跑一次同样的对账；
同一组差异只告警一次（复用熔断去重的思路，避免把刚修好的告警刷屏又造回来）。
新增指标 `ta_reconcile_mismatch_total` / `ta_reconcile_repeat_total`。

**Bug #11：灰尘被当成持仓，会静默占满仓位额度。**
OKX 现货手续费按基础币扣，所以一次"全平"永远留下一个小于最小下单量的残渣
（实测平掉 4.983562 OKB 后剩 4.5e-07，而 OKB/USDT 最小下单量是 0.01）。
这笔残渣**永远卖不掉**，但 `max_open_positions` 判的就是 `len(account.positions)` ——
几次往返之后 5 个仓位额度会被残渣占满，机器人**静默停止开新仓**。
→ 修：`_managed_account()` 把"卖不掉的残渣"从持仓里摘出来，单独记为
`dust_positions` / `dust_notional`，并暴露指标 `ta_dust_notional`（可见，不静默）。
判据故意保守（误判会丢掉真仓位的止损）：低于交易所最小下单量，或市值低于
`min_order_notional_usd / 4`。

**Bug #12：卖单可能超过实际可用余额。**
账本一旦高估持仓，卖单就会大于持仓 → 被拒（这次实测的 51008）。
→ 修：`CcxtVenue.place_order()` 在下卖单前按交易所的**可用余额**截断
（用 `amount_to_precision` 截尾，避免四舍五入又把量顶回去）；可用余额为 0 时
本地直接拒单，不去打交易所。这样即使将来再有漂移，**退出也一定能发出去**。

#### 14.9.2 全部 bug 台账（12 个）

| # | bug | 症状 | 状态 |
|---|---|---|---|
| 1 | 成交漏记（`sync` 只看挂单） | 市价单永久 `filled=0` → 仓位非托管、无止损、平不掉 | ✅ 修 + 生产验证 |
| 2 | 熔断死循环 | `daily loss flatten` 每 tick 重复，393 条 | ✅ 修，重启后 0 条 |
| 3 | 现货发 `reduceOnly` | OKX 现货 51205 拒单 → 无法平仓 | ✅ 修 |
| 4 | 接管手动底仓 | `adopt` 默认开，会卖用户底仓 | ✅ 修（默认 false） |
| 5 | 收益率/回撤基数 | 用总权益当分母，算出 +1986% | ✅ 修（`risk_equity`） |
| 6 | 模拟盘不能续跑 | 重启后账本丢失 | ✅ 修（`PaperVenue.seed`） |
| 7 | 1h/4h 聚合对齐 | 真实数据回测 IndexError | ✅ 修（按共同时间戳对齐） |
| 8 | 指标 O(n²) | 180 天回测 20 分钟 | ✅ 修（2 分 45 秒） |
| 9 | 手续费币种记错 | OKB 扣费记成 USDT → 持仓多 0.1% → 51008 平不掉 | ✅ 修 + 逐位一致 |
| 10 | 对账只在退出时跑 | 运行中漂移无人报警（实测 0 条） | ✅ 修（周期性 + 去重告警） |
| 11 | 灰尘算持仓 | 残渣占满 5 个仓位额度 → 静默停止开仓 | ✅ 修（灰尘过滤 + 指标） |
| 12 | 卖出超量 | 账本高估 → 卖单被拒 | ✅ 修（按可用余额截断） |

**12 个里有 4 个（#9、#10、#11、#12）是同一个根因族**：账本与交易所之间的漂移
没有权威来源、也没有哨兵。这个族现在被三件事堵住了 —— 手续费取交易所真值、
卖出按交易所可用余额截断、每 30 个 tick 强制对账一次。

#### 14.9.3 收官状态（实测）

| 检查项 | 结果 |
|---|---|
| 账本 cash vs 交易所 USDT | `103359.097137` vs `103359.0971367006` ✅ |
| 持仓 | 0（灰尘 5.4e-05 USD，单列不占额度） |
| `ta_open_positions` / `ta_dust_notional` | `0` / `5.43e-05` |
| `ta_kill_switch` / `ta_daily_pnl_pct` | `0` / `0.0` |
| 对账告警 | 0 条 |
| 测试 / 离线自检 | **99 个**（90→95→99）/ **43 项全绿** |

#### 14.9.4 还没做、且是有意不做的

- **挂单（maker）开仓**：实测只稳定改善 +0.11pp，低于自定的 0.5pp 接受阈值，未上线；
- **链上 KMS / 硬件钱包签名器**：仍是接口 + 实现清单（第 7.5 节）；
- **不调策略参数**：CEX 四策略 180 天样本外全负、费用与净收益同量级。
  账本现在可信了，但"账本可信"不等于"策略为正"——这两件事必须分开看。

### 14.10 盯盘与链上研究：补上"零变动占比"和活跃度过滤

用户要求"所有策略按既定策略执行，盯盘也一样"。盯盘侧照计划补齐两件欠账。

#### 14.10.1 先把多实例收干净

实测同时有 **4 个** `ta scan watch` 在往同一个账本写：pid 17216（默认 store）、
17293 / 17321（30 秒一轮）、17371（300 秒一轮）。这就是第 0 节里"55% 是重复写入"的来源。
清掉 3 个重复实例后，剩下的是**用户自己终端启动的那一个**（pid 36270，300 秒一轮）——
没有再起第二个，避免又把重复写回来。

#### 14.10.2 新指标：零变动占比（这是这次最有价值的一行）

之前报告的"98% 覆盖率、中位数 0.00%、胜率 0.8%"其实自相矛盾：**有覆盖率却没有波动**，
只可能出现在没人交易的代币上（5 分钟后的"价格"就是现在这个数）。
现在这行指标直接把它摆到报告首行：

```
快照总数 2363 | 分桶维度 turnover
零变动占比 97.9%（2295/2345 组相邻观测价格一字未动）| 全程不动的代币 0/11 (0%)
  ⚠️ 零变动占比 ≥50%：样本主体是没人交易的代币，中位数必然是 0.00%，下面的分桶不能当结论。
```

**97.9% 的相邻观测价格完全没动。** 所以第 0 节里那些"低换手桶 +1h 均值 +0.28%"之类的
苗头，现在有了一句话的判决：**样本不成立**。不是"方向可能对但不显著"，是"这批观测
根本没在成交"。

#### 14.10.3 活跃度过滤：把"有没有人在交易"变成可过滤字段

发现层早就在抓 `txns_m5 / txns_h1 / volume_m5 / volume_h1`（用来筛候选），
但**扫描器落盘时把它们丢了**——所以账本里没有"这个币当时在不在成交"这个维度。
现在整包写进每一行，并加了过滤器：

```bash
python3 -m ta scan evaluate --bucket-by txns_m5 --min-txns-m5 5 --horizons 5,60,1440
```

两个刻意的设计：

1. **历史行不会被当成"0 笔成交"悄悄删掉**。加字段之前的行没有这个字段，
   报告会单独写"有该字段的行 N 条，保留 M 条"，M=0 时直接提示
   "本账本的历史行是加字段之前写的"，而不是给一张空表假装有结论。
2. **`bucket_by` 现在支持 `txns_m5`**，可以直接检验"这段时间有人在交易的币，
   后续收益是否真的不同"——这才是链上真正要验证的那个问题。

#### 14.10.4 现状与下一步

| 项目 | 值 |
|---|---|
| 账本行数 | 13,106（去重后 5,868；限定当前清单后 2,363） |
| 零变动占比 | **97.9%** → 结论不成立 |
| 活跃度字段 | 从 2026-09-30 17:39 起的新行开始有 |
| 盯盘实例 | 1 个（300 秒一轮），重复写入问题已消除 |

**下一步只有一个动作**：让带活跃度字段的账本累积够样本（每个分桶 ≥20 个**有前瞻数据**的信号），
再跑 `--bucket-by txns_m5`。在那之前，任何链上结论都不要下。

测试 99 → **103** 个（新增零变动占比、活跃度过滤、"缺字段不当作 0"、"默认关闭不改变结果"），
smoke 43/43 仍全绿。

### 14.11 资金费率 / 基差：第一个**活下来**的东西（2026-09-30）

前面测的所有东西都是**方向性**的：需要预测价格往哪走。四策略、1m 提频、链上事件研究，
全部死在同一个地方 —— 要么波动不够付成本，要么**根本没有成交**。

永续资金费率结构上不一样：它是**可以提前观测到的现金流**。做多现货 + 做空永续
（delta 中性），不需要对价格有任何观点，只收（或付）资金费。

工具：`scripts/funding_basis.py`（公开接口，不需要密钥，只读不下单）。
报告存档：`reports/funding-basis-20260930.txt`。

#### 14.11.1 成本墙：一次往返 34bps（全 taker）

`2×现货(10+1) + 2×永续(5+1) = 34 bps`。资金费每天付 3 次。

| 币种 | 中位/8h | 年化 | 负费率占比 | 日均 bps | **盈亏平衡天数** |
|---|---:|---:|---:|---:|---:|
| BTC | 0.0055% | 5.79% | 7.9% | 1.59 | **21.4** |
| DOGE | 0.0075% | 7.13% | 8.6% | 1.95 | **17.4** |
| LINK | 0.0082% | 6.66% | 13.6% | 1.83 | 18.6 |
| SUI / NEAR | 0.0100% | 6.77% / 6.67% | 15.4% / 16.4% | 1.85 / 1.83 | 18.3 / 18.6 |
| ETH | 0.0038% | 4.39% | 16.4% | 1.20 | 28.3 |
| SOL | 0.0031% | 3.32% | 28.9% | 0.91 | 37.4 |
| INJ | 0.0034% | **-0.87%** | 40.4% | -0.24 | ∞ |

**这张表是这次最重要的产出**：持有 1 天，净收益是 **-32bps**（胜率 0%）——
你付 34bps 去收 1.6bps。持仓低于盈亏平衡天数，就是在给交易所交手续费。

换挂单（maker）后往返降到 24bps，盈亏平衡天数变成 **12–26 天**，量级不变。

#### 14.11.2 净收益：短窗口全负，长窗口才转正

滚动窗口、扣掉一次往返成本、70/30 时间切分：

| 持有 | 结果 |
|---|---|
| **1 天** | 全币种 **-32bps**，胜率 0% —— 纯成本 |
| **7 天** | 仍在成本线附近或以下 |
| **30 天** | 分化：BTC/DOGE/LINK/SUI/NEAR 样本内外都稳定为正（BTC 中位 +16~+19bps）；SOL/INJ/APT 为负；ETH/XRP/AVAX/ADA **样本内为负、样本外为正** ⇒ 这是换regime，不是边缘 |
| **90 天** | 13 个币种**全部**为正，样本外也是正（BTC +108bps、DOGE +142bps、LINK +130bps） |

#### 14.11.3 为什么我不把"90 天全正"当结论

1. **重叠窗口不是独立样本。** 180 天历史里 90 天窗口只有约 **2 个**独立观测。
   "胜率 100%" 的真实含义是"同一件事被数了很多遍"，不是"很可靠"。
2. **只覆盖了一个资金费为正的 regime。** 这张表里没有一段"资金费持续为负"的行情。
   负费率占比 8%–40% 的币种（SOL/INJ）已经在 30 天窗口里翻负了。
3. **资金效率要打对折。** delta 中性要占用两条腿的资本，年化 ~4–6%（对名义本金）
   对应到**占用资本**大约是 2–3%，还没算永续腿的强平风险、现货/永续价差、交易所风险。
4. **它是另一个产品。** 盈亏平衡 ~3 周意味着这是"持有数周"的策略，
   和当前机器人（5 个仓位、$600/笔、日内止损、每天 12 笔预算）表达的不是一件事。
   把它硬塞进现有循环只会得到一堆被止损打掉的 carry。

#### 14.11.4 结论与下一步

**这是目前唯一"扣掉成本之后仍然存在正数"的方向**，也是唯一一个不依赖方向预测的。
但它同时提出了一个和之前完全不同的样本要求：**以月计，而不是以天计。**

下一步（按顺序，不跳步）：

1. 先补**多 regime 数据**：把资金费历史拉长到两年以上，至少要覆盖一段负费率行情；
2. 把"负费率 regime 下会亏多少"单独量出来 —— 这是这个策略真正的风险，不是价格波动；
3. 只有在 1、2 做完之后，才谈第二本账（carry book）的仓位与执行，
   **不影响**当前这本日内账的配置。

---

## 15. 运营看板（本地只读前端）

### 15.1 为什么有这个东西

在此之前，**唯一能看到的是交易所里的成交结果**。中间过程——哪条策略触发、为什么被风控拦下、
两次交易之间权益怎么走、今天修了什么 bug、风控限额到底是多少——散落在
`data/ledger.*.jsonl`、`logs/*.jsonl`、`reports/`、`archive/` 和 README 的散文里，
既没有监控，也没有留痕管理。

现在一条命令：

```bash
python3 -m ta dashboard --port 9200     # 或 make dashboard / ./scripts/start_dashboard.sh
# http://127.0.0.1:9200
```

### 15.2 两个硬约束

1. **只读。** 它只以读方式打开文件，不写任何状态、不连交易接口、不下任何单。
   和交易循环并行跑不会互相影响（代码里也没有任何写路径）。
2. **零依赖。** stdlib HTTP 服务器 + 一个静态 HTML 页面，没有 CDN、没有 npm，
   在同一个代理环境里离线可用。

### 15.3 页面上有什么

| 区块 | 内容 | 数据来源 |
|---|---|---|
| 关键指标 | 权益、累计盈亏、回撤、持仓/heat、成交笔数、**累计手续费（占亏损比）**、非托管敞口、灰尘 | metrics :9101 + 账本 |
| 红旗与告警 | 幽灵卖单 / 接管成交 / 熔断 / 对账不一致，实时算，非事后 grep | 账本 + alerts |
| 权益曲线 | 全部权益点（自动抽样到 720 点） | 账本 `kind=equity` |
| 风控限额 | 每个限额的**美元口径**（组合 heat 3.5% = $3,615 这种） | `config/risk.toml` |
| 分层限额 | majors / alts / new 各自的止损、最大仓位、每日笔数 | `config/risk.toml` |
| 策略与参数 | 四个策略的用途、周期、标的、止损止盈、分批保本、边缘/成本门槛 | `config/strategies.toml` |
| 已平仓交易 | 按**持仓**配对（分批止盈算同一笔），净额已扣双边手续费 | 账本 `kind=fill` |
| 运行时间线 | 订单 / 成交 / 风控拦截 / 告警合成一条时间轴 | 账本 + alerts |
| 链上研究 | 候选清单、快照行数、最近扫描时间、按 1h 量/池子排序 | `data/onchain/` |
| **常驻服务** | 三个 launchd 服务各自的 label / 状态 / pid / 端口，绿点=alive | `launchctl print` + 端口探测 |
| **告警阈值** | 五项阈值各自的设定值与是否越界，以及看门狗周期与推送是否已配置 | `config/alerts.json` |
| **留痕** | bug 修复 · 策略调整 · 决策 · 研究结论，可按类型筛选 | `docs/changelog.jsonl` |
| 参数迭代 | refit 的 ACCEPT/REJECT 与判据 | `reports/refit-changelog.jsonl` |
| 原始留痕文件 | reports / archive / logs 的文件清单与时间 | 文件系统 |

### 15.4 留痕管理：`docs/changelog.jsonl`

这是本次新增的**结构化审计台账**（追加式 JSONL）。README 里的散文仍然保留，
但机器可读的版本在这里，看板直接渲染它。字段：

```json
{"ts":"2026-09-30T17:00:00+08:00","kind":"bug","severity":"P0","area":"venues",
 "title":"手续费币种记错","detail":"...","evidence":"...","status":"fixed"}
```

`kind` 取值：`bug` / `decision` / `research` / `ops` / `strategy`。
`decision` 额外带 `actor`（谁拍的板），`research` 额外带 `verdict`（结论）。
**约定：每次改代码、改配置、下决策或得出研究结论，就往这里追加一条。**

### 15.5 一个实现细节值得记住

看板的"已平仓交易"最初用 FIFO 逐笔配对，结果 **PENGU 那笔分批止盈被拆成了两笔**，
手续费也被重复计入（$49.48 vs 实际 $38.34）。原因是 OKX 按基础币扣手续费，
一次"全平"永远留 4.5e-07 的残渣，持仓永远不到精确的 0，于是每笔交易都"没平"。

改成**按持仓配对**（分批止盈属于同一笔），并给残渣留 0.01% 的相对容差之后，
交易的净额之和与账本手续费**逐分对上**（$38.34）。这跟第 14.9 节那个
"灰尘被当成持仓"的 bug 是同一个教训：**在这套系统里，任何"精确等于零"的判断都是错的。**

### 15.6 常驻：做成 launchd 服务（开机自启 + 挂了自动拉起）

`nohup ... &` 起的进程会随终端会话一起死，重启也不回来。要做"常驻"就得交给 launchd：

```bash
./scripts/install_dashboard_service.sh            # 安装 + 立即启动 + 自检
./scripts/install_dashboard_service.sh status     # 看状态
./scripts/install_dashboard_service.sh uninstall  # 卸载
```

| 项 | 值 |
|---|---|
| Label | `com.tradingagent.dashboard` |
| plist | `~/Library/LaunchAgents/com.tradingagent.dashboard.plist` |
| 模板 | `deploy/com.tradingagent.dashboard.plist.template`（装机时替换绝对路径） |
| 策略 | `RunAtLoad` + `KeepAlive` → 登录即起、退出即拉起 |
| 日志 | `logs/ta-dash-service.log` / `.err.log` |

**实测验证**：`kill -9` 掉正在监听的那个进程（pid 49771），15 秒内 launchd 自动拉起为
49796，`/api/health` 返回 200。

**范围说明（重要）**：这里**只对只读看板**做自启，**不包含交易循环**。
看板不写状态、不下单，无人值守没有交易风险；而一个会下单的循环自动重启完全是另一个
风险决策（比如机器重启后无人看管地开始交易），所以没有一并做。

#### 15.6.1 踩到的坑：launchd 下看板卡死不监听端口

装完后进程活着、`ps` 显示 `STAT=S`，但 `lsof -sTCP:LISTEN` 什么都看不到，端口就是不通。

根因：`http.server.HTTPServer.server_bind()` 会调用 `socket.getfqdn(host)` 做**反向 DNS**。
解析器慢或被阻断时，这个调用正好落在"socket 已经 bind、但还没 listen"的窗口里 ——
于是进程看着正常，端口却从未进入监听状态。实测在 launchd 下卡了好几分钟。

看板只服务 loopback、从不需要 FQDN，所以直接跳过这次查询（`_Server.server_bind`）。
同类问题在所有"用 HTTPServer + 国内网络环境 + launchd"的场景里都会出现，
值得记一笔。

### 15.7 交易循环的常驻服务（同样的机制，但**故意不同的语义**）

看板之后，用户明确要求把交易循环也做成常驻。两件事必须分开看，所以两个 job 的
重启语义**故意不一样**：

| | 看板 `com.tradingagent.dashboard` | 交易循环 `com.tradingagent.runner` |
|---|---|---|
| KeepAlive | `true`（任何退出都拉起） | **`SuccessfulExit=false`（只拉起崩溃/被 kill）** |
| 为什么 | 只读、无副作用，永远在最好 | 干净退出是**有意的**：熔断或连续报错放弃时不该被定时唤醒重来 |
| 密钥 | 不需要 | **不写进 plist**，由 `--env-file .env` 在启动时读（.env 权限 600） |

```bash
./scripts/install_runner_service.sh             # 安装 + 启动 + 自检
./scripts/install_runner_service.sh status
TA_TRADING=0 ./scripts/install_runner_service.sh   # 只观察不下单的常驻
./scripts/install_runner_service.sh uninstall
```

**紧急刹车（实测有效）**：

```bash
touch KILL_SWITCH      # 引擎每 tick 检查；触发后立即平掉所有仓位并以 0 退出
rm KILL_SWITCH
launchctl kickstart -k gui/$(id -u)/com.tradingagent.runner   # 确认安全后手动重启
```

实测记录：`touch KILL_SWITCH` 后一个 tick 内（约 75 秒）进程自行平仓退出，
并把原因写进文件（`reason=kill switch file present on disk`）；launchd 显示
`job state = exited`、`last exit code = 0`、`runs = 1` —— **没有自动拉起**。
这正是想要的行为：刹车踩下去就停在原地，等人来确认。

#### 15.7.1 防止两个循环互相打架

如果服务在跑、又手敲 `./scripts/start_demo.sh`，那个脚本会杀掉 launchd 拥有的进程、
再起一个自己的，然后 launchd 发现 job 挂了又拉起来 —— 两个循环抢同一个 metrics 端口、
同时交易同一个账户。所以 `start_demo.sh` 现在会**先检测 launchd job，发现是服务托管就
直接拒绝启动**（退出码 3），并打印正确的重启/停止命令。

#### 15.7.2 踩到的坑：launchd 起不了这个 shell 脚本

第一版把 `scripts/run_demo_foreground.sh`（mode 755、`#!/bin/zsh`、在终端里跑得好好的）
直接交给 launchd，结果是：

```
/bin/zsh: can't open input file: .../scripts/run_demo_foreground.sh
last exit code = 127
```

把解释器显式写成 `/bin/zsh` + argv[1] 仍然是同样的错。而**同一目录**下的看板 job
（`<python> -m ta dashboard`）一直正常 —— 说明问题出在"让 launchd 去跑一个脚本"
本身，不是权限也不是路径。

结论是别跟它较劲：**把逻辑挪进被调用的程序里，让 launchd 只负责跑一个二进制**。
于是给 `ta run` 加了两个参数：

```bash
ta run ... --env-file .env --pid-file logs/ta-run.pid
```

- `--env-file` 把原来 shell 包装脚本做的事（读 .env、导出密钥）搬进 Python，
  **密钥因此不会被写进 `~/Library/LaunchAgents/*.plist`**；已有的环境变量优先，
  单值仍可被覆盖。
- `--pid-file` 让循环自己写 pid、退出时删掉 —— 服务托管时没有 shell 来替它做这件事，
  而 `start_demo.sh`、巡检脚本都依赖这个文件。

plist 里只留 `PYTHONPATH` / `PATH` / `TZ` / 代理变量，没有任何凭据。

### 15.8 链上扫描器也常驻了（第三个服务）

```bash
./scripts/install_watcher_service.sh              # 300 秒一轮
TA_WATCH_INTERVAL=60 ./scripts/install_watcher_service.sh
./scripts/install_watcher_service.sh status
./scripts/install_watcher_service.sh uninstall
```

做成服务的理由很直接：它**已经从终端手动起过三次、也死过三次**，每次死掉都意味着
"连续采样断档"，而 +5m / +1h 的研究窗口必须靠连续采样才有样本。

KeepAlive 用的是 `true`（和交易循环相反）：它是只读研究，没有"有意义的干净退出"这回事 ——
停下来就是丢样本，而丢样本正是前几天反复踩的坑。

顺带给 `ta scan watch` 加了 `--reload-every N`：**长跑扫描器必须定期重读候选清单**。
清单几小时内就会整体换血（实测 2026-09-30 → 10-01 多次全量轮换），只读一次清单的
扫描器会一直采样那些早已停止交易的代币 —— 这正是事件研究一直在踩的混淆项。
常驻服务用 `--reload-every 12`（300 秒一轮 ≈ 每小时重读）。

### 15.9 一次清理（2026-10-01）

| 动作 | 说明 |
|---|---|
| 停掉只读观察者 | pid 15716，跑了 1 天 18 小时。它的账本早于两次账本重置，**已不可能再作为对照**（dry-run 单量 58k vs 真实 200k，因为基线不同） |
| 归档它的账本 | `archive/observer-dryrun-ledger.20261001.jsonl` + 对应 baseline |
| 归档 walk-forward 原始日志 | `reports/walkforward/ta-wf-180*.log` —— README 第 0 节那张表的原始证据，之前只躺在 /tmp 里 |
| 删除 /tmp 遗留 | 9 个 Sep-29 临时文件 + 2 个临时目录（ta-verify / ta-scan-test / ta-scan-synth / ta-wf-180* / ta-observe-* / ta-trial-*） |
| 删除失效 pid 文件 | `logs/ta-watch.pid` 指向早已不存在的进程 |

清理的原则：**先归档有证据价值的东西，再删可再生的临时件**。walk-forward 日志属于前者
（它是结论的原始数据），扫描测试文件属于后者（fixture 可重跑）。

### 15.10 现在一共三个常驻服务

```
$ launchctl list | grep tradingagent
51237  com.tradingagent.watcher      # 链上扫描，只读，KeepAlive=true
49796  com.tradingagent.dashboard    # 运营看板，只读，KeepAlive=true
50768  com.tradingagent.runner       # 交易循环，会下单，KeepAlive=SuccessfulExit=false
```

前两个只读，无人值守零交易风险；第三个会下单，所以语义不同、并且有实测过的紧急刹车。

### 15.11 候选清单自动刷新（扫描器不再依赖"有人记得"）

`ta scan watch` 新增 `--discover-every N`：**长跑扫描器每 N 轮自己重跑一次发现层**，
重建候选清单并立刻改用新清单。常驻服务用 `--discover-every 24`（300 秒一轮 ≈ 每 2 小时）。

这一条解决的是一个很具体的失败模式：**发现层和观察层原本是两个手工步骤**，
没人记得跑 `ta scan discover`，扫描器就会一直采样那些早已停止交易的代币 ——
而这正是事件研究反复踩的混淆项（"研究的是没成交的代币"）。

实测：在第 2 轮强制触发时，清单被就地重建（13 → 7 个代币），扫描无缝继续。

### 15.12 告警阈值与推送

看板现在带一个**后台看门狗线程**（不是"打开页面才算"的伪告警），每 60 秒评估一次阈值。
阈值是**数据不是代码**，写在 [`config/alerts.json`](config/alerts.json)，改完无需重启：

| 阈值 | 默认 | 触发级别 |
|---|---:|---|
| `max_drawdown_pct` | 5.0% | critical |
| `daily_loss_pct` | 2.0% | critical |
| `fees_pct_of_loss` | 60% | warn |
| `unmanaged_notional_usd` | $1 | warn |
| `onchain_zero_change_pct` | 80% | info |
| `webhook_min_interval_seconds` | 1800 | — |

超阈值的条目会**排在最前**（页面上的红旗区已经按级别排序），同时在满足间隔的前提下
POST 一个 JSON 到 `TA_ALERT_WEBHOOK_URL`（在 `.env` 里，看板通过 `--env-file .env` 读取；
**没配就完全不发**，只显示）。

两个刻意的设计：

1. **只在状态变化时推送**（同一组越界值不重复发）。这是熔断刷屏那次留下的教训：
   重复告警会训练人忽略它。
2. **阈值文件坏了就退回默认值**，不会让看板挂掉 —— 一个监控工具本身不该成为新的故障点。

当前实测：回撤 0.017%、单日 0%、手续费占比 0%、非托管 $0.000078、链上零变动 21% ——
**全部未越界**，所以红旗区现在是空的（这正是"没有消息就是好消息"该有的样子）。

#### 15.12.1 一个"监控面板静默失效"的坑

服务面板第一版用 `lsof` 查监听端口。在终端里跑得好好的，但**在 launchd 下 pid 一列永远是空的** ——
因为 launchd job 的 PATH 里没有 `/usr/sbin`，而 `lsof` 就装在那里。异常被 `except` 吞掉，
面板不报错，只是永远显示"没有 pid"。

**监控面板静默失效，比没有面板更糟**——它会让人以为一切正常。所以改成不依赖任何外部命令：
直接用 **TCP connect** 判断端口是否有服务在监听（`socket.create_connection`），
`lsof` 只作为可选的 pid 补充，查不到就算了。三个 plist 的 PATH 也顺手补上了 `/usr/sbin`。

同一处还有第二个坑：第一版对三个服务各跑一次 `launchctl print`，加上 `lsof`，
**每次页面刷新要起 5 个以上子进程**，导致 API 间歇性超时（页面每 15 秒刷一次）。
改成 **一次 `launchctl list` 解析全部三个 job**，并给结果加 30 秒缓存 ——
现在连续三次请求稳定在 **0.24 秒**（`http=200, 133 KB`）。

### 15.13 看板与 OKX 的"不实时对齐"：一个估值时点问题（2026-10-01）

用户反馈看板数字与 OKX 模拟盘对不上。实测下来，**不是数据源错，是估值时点差**：

| 场景 | 结果 |
|---|---|
| 空仓时 | 看板、指标端点、交易所三方**完全一致**（$103,258.8743，差 0.000000） |
| 持仓时 | 看板差出约 **75 秒**的价格变动 |

原因：页面上除权益外的所有东西（成交、时间线、留痕）都来自**主循环上一个完成的 tick**，
而一个 tick 约 75 秒；OKX 客户端则在连续标记。同一个仓位，两边取的不是同一时刻的价格。
两个数字都"对"，只是一个是 75 秒前的。

**修复**

1. 看板新增**只读直读交易所**（读余额 + 实时 ticker），头条权益改为**实时值**；
2. 把机器人账面值、它的**数据年龄**、以及两者的**差额**并排显示
   （实测年龄 11–68 秒）；
3. 页头显示新鲜度：`实时直读交易所 · 账面数据 11s 前 · 交易所另有 6 笔灰尘（$0.0002）`。

**仍然是只读**：不下单、不撤单、不写任何状态；交易所不可达时自动退回 tick 数据并在页头说明原因。

顺带修了一个同源问题：直读第一版把**灰尘也算成持仓**，页面显示 6 个仓位而账面是 0
（OKX 按基础币扣手续费，每次平仓留 ~1e-7）。现在套用与 runner 相同的灰尘规则，
持仓数归 0，灰尘单独展示。

**这条留痕本身也说明了这套系统的用法**：用户的"看板不准"不是一个 bug 报告，
而是一个**需要量化的问题** —— 量化之后才发现空仓时完全一致、持仓时才差，
于是定位到"tick 延迟"而不是"数据源错误"。

### 15.14 与 OKX 对齐到什么程度（2026-10-01 追加）

"看板要和 OKX 对齐"这句话要拆成两半，因为**有一半永远不可能相等**：

| 项目 | 是否与 OKX 一致 | 说明 |
|---|---|---|
| 权益 / 现金 / 持仓 | ✅ **实时一致** | 看板直读交易所余额 + 实时 ticker |
| 累计盈亏 | ⚠️ **口径不同** | 看板算的是"自本次账本基线起"；OKX 算的是账户开户至今 |
| 成交 / 手续费 / 已平仓 | ⚠️ **口径不同** | 看板只含**机器人自己**的成交；你手动下的单不在里面 |
| 权益曲线 | ⚠️ **口径不同** | 起点是账本基线，不是交易所开户日 |

这不是 bug，是**两本账**：一本是交易所账户，一本是机器人自己的账本。
分不清这两本账，就会把"机器人亏了 $99"误读成"账户亏了 $99"。

所以页面上把口径**直接标出来**了：

- 累计盈亏下写「自本次账本基线 $103,358 起（非交易所开户至今）」
- 成交表下写「只含机器人自己的成交；你在 OKX 手动下的单不会出现在这里」

#### 15.14.1 双击打开 HTML 也能用了

用户一直在从磁盘双击 `src/ta/web/index.html`（`file://`），而相对路径的
`fetch('/api/...')` 会变成 `file:///api/...`，必然失败。

与其反复提醒"请用 http 打开"，不如让它直接能用：服务端加了**窄口径 CORS** ——
只放行 `Origin: null`（file:// 页面）和 loopback 来源，其他一律不放
（实测从 `https://evil.example` 发起时**没有任何** `Access-Control-Allow-Origin`，
因为这份 payload 含持仓与盈亏，不该被任意网页读到）。

配套改动：页面在非 http 环境下改用**绝对地址**访问 API，并用**查询串**做缓存穿透
（用请求头会触发 CORS 预检，查询串不会）；取数失败才退回说明卡片。

#### 15.14.2 刷新时机

浏览器会**节流后台标签页的定时器**，所以一个放着不管的标签切回来时可能显示旧快照。
现在：每 15 秒轮询 + **标签重新可见时立刻刷新** + 页头一个「立即刷新」按钮。

（巡检不需要"顺便刷一遍看板"：看板每次请求都重新读账本与交易所，它没有需要预热或
定时重建的缓存 —— 唯一的缓存是服务列表 30 秒、链上面板 60 秒、实时行情 15 秒。）

### 15.15 首次真实重启：自启过了，但看板 API 有 20 秒的坑（2026-10-01 夜）

**好消息**：主机在 21:05 重启，**没有任何人工干预**，launchd 把三个服务全部拉起来
（watcher 1474 / runner 1477 / dashboard 1485）。这是 `RunAtLoad` 自启第一次被真实事件验证。
顺带一个意外收获：runner 启动于 21:08，而灰尘修复提交于 20:28 ——
**重启自动把修复带上线了**，省掉一次手动 kickstart。

**坏消息**：重启后看板首页 200，但 `/api/snapshot` 超时，页面显示"看板取数失败"。

根因：**直读交易所的客户端每次缓存失效都新建一个**，并调用 ccxt 的 `load_markets()`
—— 那会拉取 OKX 的全部合约信息，秒级开销。而页面每 15 秒轮询、缓存也是 15 秒，
等于**每次刷新都重付这 20 秒**。

修复：**复用交易所客户端**（和主循环的做法一致），并让看门狗线程在启动时先热一次
（所以服务起来后第一屏就是快的）。实测三次请求：**0.57s / 0.34s / 0.26s**。

顺带修掉第二处：HTTP 的响应头块是在 `end_headers()` 里写出的，之前的断管保护只包住了
`wfile.write(body)`，于是 20 秒的响应超出客户端耐心时，`end_headers()` 会把
`BrokenPipeError` 抛到服务日志里。现在头块与正文一起保护。

**教训**：一个"偶尔很慢"的接口会被前端当成"坏了"。缓存 TTL 与轮询间隔相等时，
缓存等于没有 —— 要么让每次请求都便宜（复用客户端），要么让缓存活得比轮询久。

### 15.16 链上研究被自己的范围限定卡死（2026-10-02）

上一节加的"每桶需 ≥20 个**不同代币**"判据之后，报告每轮都在报"代币不足"。追下去发现：
**不是市场给不出样本，是取数范围把它锁死了。**

| 尝试 | 结果 |
|---|---|
| `--limit 25`（默认） | 12–15 个候选 |
| `--limit 60` | **12 个** |
| `--limit 200` + 流动性下限压到 $1,000 | **17 个** |

发现层（DexScreener profiles/boosts）在任意时刻只有约 12–17 个代币同时满足
"流动性 ≥ $10k 且 1 小时 ≥10 笔"。而每个桶要 20 个不同代币 × 3–4 个桶 = **60–80 个**。

所以只要把研究范围限定在"当前清单"，**样本永远不可能达标，等多久都一样**——
我们过去几天一直在等一个结构上不会到来的样本。

**修复**：`ta scan evaluate` 新增 `--recent-hours`（默认 24），范围改为
**「当前清单 ∪ 最近 N 小时出现过的代币」**。滚动窗口保留了"最近确实在交易"的币，
又不会被早已停止交易的池子污染。

| | 修复前 | 修复后 |
|---|---:|---:|
| 代币数 | 12 | **111** |
| 观测数 | 420 | **1,313** |
| 中间桶代币数 | 2–3 | **27–29**（首次达标） |

**这同时暴露了一条待验证假设**：换成并集口径后，+1h 高换手桶的中位数
**连续三轮为负且方向一致**（-4.33% / -1.24% / -2.46%），本轮 20+ 桶 -2.46%
对 0-1 桶 +2.74%，差 **-5.20pp**，已经超过 1.4% 的往返成本。但三条不能忘：

1. 两端极端桶的代币数仍只有 2–13 个，**样本仍不达标**；
2. 方向是**做空**，而 DEX 新币没有借券 —— 这套系统**无法执行**；
3. 均值被单个离群值拉动（20+ 桶均值 +69.91%，中位 -2.46%，p90 仅 +18%）。

结论仍是"记为待验证假设"。下一步该做的是**扩数据源到 60+ 代币**，而不是继续等。

### 15.17 扩源 + 得出了第一个**负结论**（2026-10-02）

#### 15.17.1 第二个发现源：候选池 12–17 → 80

新增 **GeckoTerminal trending pools**（公开、无需密钥、可翻页）作为第二个来源，
字段直接映射到 `Candidate`：`base_token` 取 mint、`reserve_in_usd` 作流动性、
`volume_usd.h1/h24`、`transactions.h1/m5` 作活跃度。

| | 修复前 | 修复后 |
|---|---:|---:|
| 候选数 | 12–17 | **80** |

其中 70 来自新源、9 来自 boosts、6 来自 profiles，`txns_h1` 最低 55。

#### 15.17.2 一个测出来的上限：候选数 = 40

扩池与采样频率**直接冲突**。实测单轮扫描约 **4.5 秒/代币**：

| 候选数 | 单轮耗时 | 能否塞进 300 秒节奏 |
|---:|---:|---|
| 40 | 181s | ✅ |
| 80 | 628s | ❌（会把节奏推到 +5m 容忍度之外） |

既然滚动并集已经把"不同代币数"从 12 提到 141，扩池的价值在于**更快累积**而不是单轮全都要，
所以 watcher 定为 `--discover-limit 40`。**这是一个测出来的上限，不是偏好。**

#### 15.17.3 并集确实会带回死币 —— 活动过滤正是为此存在

并集把范围扩到 141 个代币后，**零变动占比从 9.1% 反弹到 48.9%**。
这不是 bug，而是并集的代价：24 小时窗口内确实包含了一些后来停止交易的代币。

叠加 `--min-txns-m5 5`（只保留最近 5 分钟真在成交的观测）后保留 1,315 / 2,745 行。

#### 15.17.4 结论（负）：+5m 的"买高换手"没有超过成本的边缘

**这是第一次两个极端桶都达到 ≥20 个不同代币**：

| +5m 桶 | n | 代币 | 中位 | 均值 | 胜率 |
|---|---:|---:|---:|---:|---:|
| 0-1 | 26 | 4 | 0.00% | +0.99% | 26.9% |
| 1-5 | 213 | 12 | -0.14% | -3.43% | 39.9% |
| 5-20 | 503 | **35** | 0.00% | -0.06% | 44.5% |
| 20+ | 335 | **21** | **-0.40%** | +46.66% | 42.7% |

高换手桶既不更好也不更差，且**所有差值都远低于 1.4% 的往返成本**。
判定：**+5m 方向没有可交易边缘** —— 这是一个负结论，但终于是结论，而不是"样本不足"。

（20+ 桶的均值 +46.66% 仍是单个离群值贡献的，中位数才是信号。）

#### 15.17.5 +1h 仍同向，但仍不可用

高换手桶在 +1h 依旧更差（-0.29 / -2.23 / -4.27%），连续第四轮同向。
但：极端桶只有 10–14 个代币（未达门槛），而且**方向是做空 —— DEX 新币没有借券，
这套系统无法执行**。所以它仍然只是假设。

### 15.18 两件事：一个观测盲区被补上，一条假设被升级（2026-10-02 晨）

#### 15.18.1 「0 信号」和「循环卡死」看起来一模一样

重启后循环跑了 **425 tick / 9.3 小时、信号数 0**。按历史 0.64 信号/小时应有约 6 个，
概率约 0.2% —— 值得查。

查下来：日志显示它在正常拉 K 线（有 fetch 重试与降级记录），
`stale_data_skips` / `tick_errors` / `blocked` 三个计数器**都不存在**（= 从未触发），
所以不是已知的静默停摆路径。但**也无法证明策略真的被评估过** ——
`ta_signals_total` 在"评估了但没信号"和"根本没评估"两种情况下**都是缺失**。

补上 `ta_bars_evaluated_total`（每次对一根新收盘 bar 评估一个策略就 +1）。
paper 冒烟实测：**400 次评估 → 2 个信号**，计数器有效。下次重启生效。

> 这是同一类问题的第三次出现（前两次：`ta_fees_total` 恒为 0、`ta_venue_degraded` 锁存）。
> **一个只能表达"有结果"、无法表达"跑过了"的指标，在安静的时候等于没有。**

#### 15.18.2 +1h「换手越高越差」升级为单调关系（样本大了）

并集口径 + 活跃度过滤跑了一夜后：**148 个代币、3,747 条观测**（过滤后 2,271 条）。
+1h 的两个大桶首次都远超门槛：

| +1h 桶 | 代币 | 中位 |
|---|---:|---:|
| 0-1 | 7 | -1.23% |
| 1-5 | 15 | -1.59% |
| **5-20** | **48** | **-3.12%** |
| **20+** | **36** | **-4.42%** |

换手从低到高，中位数**单调下降**。但两点必须同时说：

1. **所有桶中位数全为负** —— 这是"被推广/趋势新币整体衰减"的总体现象，
   不是"低换手好、高换手坏"的择券信号；
2. **方向是做空，DEX 新币无借券，这套系统无法执行。**

另外 5-20 桶均值 +171.73% 而中位 -3.12%，仍是离群值主导。严格按判据
（两侧极端桶都需 ≥20 代币）**仍未达标**（0-1 桶只有 7 个代币）。

要把"整体衰减"变成可用信号，得换成**回避规则**（不买高换手新币），而不是做空。

#### 15.18.3 更正：上一轮那条"单调关系"不稳定

样本继续长大（5-20 桶 56 个代币、20+ 桶 46 个代币）后，**排序翻转了**：

| +1h 桶 | 代币 | 上一轮中位 | 本轮中位 |
|---|---:|---:|---:|
| 0-1 | 7 | -1.23% | -0.25% |
| 1-5 | 18 | -1.59% | -0.47% |
| 5-20 | **56** | -3.12% | **-3.64%** |
| 20+ | **46** | -4.42% | **-2.97%** |

最高的两个桶**互换了位置**，所以"单调下降"不是稳定结论，**撤回**。

稳定下来的部分是：**换手 ≥5 的两个桶中位都在 -3% 附近，而换手 <5 的两个桶在 -0.3% 附近**，
差约 2.7pp（超过 1.4% 往返成本），而且**高换手侧的样本已经扎实（56 / 46 个代币）**。
低换手侧仍只有 7 / 18 个代币，未达门槛。

结论形态没变：方向是做空、DEX 不可执行；可执行的形态是**回避规则（不买高换手新币）** ——
而这条现在在高换手侧有 46–56 个代币支持。

> 这是本次研究里第二次"看起来成立、样本变大后不成立"。第一次是零变动占比 97% 时那些
> "低换手桶 +0.28%" 的苗头。**结论要等到代币数够，而且要经得起下一轮。**

### 15.19 那个 15.8 小时的静默，自己结束了（2026-10-02 中午）

上一节留下的疑问（"0 信号"和"循环卡死"分不清）**实质上被回答了**：

```
10-01 18:30 OKB sell  ->  10-02 10:16 JUP buy     间隔 15.8 小时
```

**同一个进程自己恢复了** —— 卡死的循环不会自己活过来，所以策略循环一直在评估。
（严格证明仍要等 `ta_bars_evaluated_total` 随下次重启生效。）

但这件事暴露了一个**新事实**：回测给出的信号密度是 **13.6 个/天（≈0.57/小时）**，
15.8 小时里应该有约 **9 个**信号，实际是 **0 个，然后 1 个**。

**实盘信号密度显著低于回测。** 这和"回测毛收益≈0"是两件事：
如果连信号都很少出现，**样本积累速度会比预期慢得多**。先记为待观察，不下结论。

#### 15.19.1 链上「换手 ≥5 在 +1h 明显更差」第三次复现

| +1h 桶 | 代币 | 中位 |
|---|---:|---:|
| 0-1 | 7 | -0.24% |
| 1-5 | 18 | -1.05% |
| **5-20** | **59** | **-3.91%** |
| **20+** | **51** | **-2.93%** |

高换手侧两个桶都**远超** 20 代币门槛，差额约 **2.7pp > 1.4% 往返成本**，连续第三轮同向。
同时再次确认**最高两桶之间不是单调**（5-20 比 20+ 更差），所以只能说
**"换手 ≥5 明显更差"，不能说"越高越差"**。

方向仍是做空、DEX 不可执行；可执行形态为**回避规则**。

### 15.20 「手续费占亏损比」是个会自己漂移的指标（2026-10-02 下午）

看门狗挂了两轮的那个 `fees_pct_of_loss`，两小时内从 **62.8% 掉到 44%** ——
而同期手续费还从 $71.36 **涨到** $77.49。

原因很简单：**分母是亏损**。亏得多，比值就小。它回答不了"一次往返到底花我多少钱"。

看板改为同时给出**有效费率 = 累计手续费 / 累计成交额**：

```
手续费 $79.55 ｜ 成交额 $79,513 ｜ 有效费率 0.100% ｜ 占亏损 45.1%
```

**0.100%** 正好等于账户实际的 **10bps/边 taker 费率**，与交易所费率表一致。
这个数字**不随盈亏漂移**，可以直接拿去和策略的毛收益比较 ——
而"占亏损 45%"这种数只会让人以为自己今天赚了一笔。

#### 15.20.1 交易恢复后 2 小时：2 笔止损，-$65.40

15.8 小时静默结束后交易恢复，10-02 已实现 2 笔，**都是止损**：

| 标的 | 成本 | 净额 | 收益 |
|---|---:|---:|---:|
| PENGU/USDT | $2,062.52 | **-$47.05** | -2.28% |
| JUP/USDT | $2,059.18 | -$18.35 | -0.89% |

合计 **-$65.40**（手续费 $8.19）。

**注意这里和昨天相反**：今天价格波动是主因（-$57.21，占 87.5%），手续费只占 12.5%；
昨天是手续费占 81.6%。两天的构成**完全不同** —— 说明单日小样本的归因没有稳定性，
但两天的合计方向一致为负。

另外 JUP 止损后触发了 62 分钟冷却，风控正常拦截了一次重开（`blocked` 记录在案）。

### 15.21 扩池的代价：+5m 时间窗已经不可靠（2026-10-02 傍晚）

把候选数从 12–17 提到 40 之后：**单轮扫描 ≈180 秒 + 300 秒间隔 ≈ 8 分钟一轮**。

而 +5m 的容忍度只到 **7.5 分钟**（target 5 分钟 + 50% 容差）——
"5 分钟后的下一个观测"经常落在容忍窗口之外被丢掉：

| 时间 | +5m 覆盖率 |
|---|---:|
| 10-02 02:00 | 89% |
| 10-02 10:30 | 33% |
| 10-02 14:28 | **26%** |

含义有两层，必须分开说：

1. **前面那个"+5m 高换手买入没有边缘"的负结论仍然成立** —— 它是在覆盖率 82–89% 时测的；
2. **但今后无法再用现有节奏监控 +5m。**

要么把候选数降到 ~20 把节奏压回 5–6 分钟，要么承认 **+1h 是唯一可靠的时间窗**。
这是一次明确的取舍，先记录待定 —— 我的建议是**默认只以 +1h 下结论**，
因为链上那条"换手 ≥5 明显更差"的证据本来就长在 +1h 上。

### 15.22 结论升级：+1h 那条现在是**两侧都有样本**的约束（2026-10-02 傍晚）

| +1h 桶 | 代币 | 中位 |
|---|---:|---:|
| 0-1 | 10 | 0.00% |
| **1-5** | **28** | **-0.82%** |
| **5-20** | **77** | **-2.94%** |
| **20+** | **72** | **-4.29%** |

除最低桶（10 个）外，其余三桶都远超 20 代币门槛；**1-5（28 个）与 20+（72 个）这对比较
两侧都达标**，中位数差 **3.47pp > 1.4% 往返成本**。这是连续第 4 轮同向，
也是第一次**两侧参照都够样本**。

性质必须同时说清：

1. 方向是**做空** —— DEX 新币无借券，**不可执行**；
2. 所以可执行的形态是**过滤条件**：如果将来建链上新币策略，**不应买入换手 ≥5 的标的**；
3. 这条约束只影响**未来**的链上策略，**不改变当前 CEX 账本** ——
   它交易的是 OKX 现货主流币，不是链上新币。

#### 15.22.1 10-02 至今：4 笔平仓，-$86.79（BTC 也止损了）

| 标的 | 成本 | 净额 | 收益 |
|---|---:|---:|---:|
| PENGU/USDT | $2,062.52 | -$47.05 | -2.28% |
| BTC/USDT | $5,163.54 | -$22.05 | -0.43% |
| JUP/USDT | $2,059.18 | -$18.35 | -0.89% |
| PYTH/USDT | $2,061.85 | **+$0.65** | +0.03% |

合计 **-$86.79**（价格 -$64.15 占 74%、手续费 $22.64 占 26%）。
BTC 那笔是 $5,163 买入、**14 分钟后止损**。

累计有效费率仍稳定在 **0.100%**。连续两天：10-01 费用主导（81.6%）、
10-02 价格主导（74%）——**单日构成不稳定，但合计持续为负**，与 180 天样本外全负的结论一致。

### 15.23 「以新币为主、放大风险、博弈大收益」——该不该做（2026-10-02）

用户提出这个方向。这是一个**方向性问题**，所以用数据回答，而不是用感觉。

#### 15.23.1 先说结论：方向本身是对的，但现在不能照着做

"允许较大回撤博弈较大收益"在数学上只有一种成立方式：**正期望 + 肥右尾**。
而我们的数据**确实**显示存在肥右尾（2.01% 的观测 >+50%、0.70% >+100%）。
所以这个问题问得对。

**但那张表里有假数据，而且假数据正好长在右尾上。**

#### 15.23.2 一个 +88,183% 的"收益"，和它背后的机制

按 +1h 前瞻收益排序，最大的一条是 **e/acc +88,183%**。

但把 e/acc 自己的完整序列打出来看，它全程在 **±15%** 内波动 ——
**+88,183% 在物理上不可能**。这是数据错误。

机制找到了：DexScreener 的一个 mint 可能对应**多个池子**，而我们的 provider
每次调用都选"流动性最大的那个池子"（`max(pairs, key=liquidity)`）。
当"最深的池子"发生切换时，同一个币的价格会在两次扫描之间**跳变几十个百分点**，
而市场上什么都没发生。

| 证据 | 数值 |
|---|---|
| e/acc 单条最大收益 | +88,183% |
| e/acc 自身序列的实际波动 | ±15% |
| PUZZLE 的极端观测 / 其余观测中位 | 9 条 / **-40.76%** |
| SACC 的极端观测 / 其余观测中位 | 7 条 / **-23.91%** |

极端值分散在 12 个币上，**但每个币其余观测的中位数都极负** ——
这正是"价格源在池子之间跳"的指纹，而不是"这些币真的暴涨过"。

#### 15.23.3 这个错误有多大：它一个人扛起了整个均值

| 口径 | n | 代币 | 均值 | 中位 |
|---|---:|---:|---:|---:|
| 原始（滚动并集） | 10,070 | 173 | **+8.68%** | 0.00% |
| 去掉最大 **1** 个 | 10,069 | — | **-0.08%** | — |
| 去掉最大 6 个 | 10,064 | — | -1.69% | — |
| 去掉 70 个 \|收益\|>100% | 10,000 | — | -2.70% | — |
| **+ 只保留真在成交的（txns_m5≥5）+ 清洗** | **4,010** | **165** | **-4.47%** | **-1.86%** |

最后一行是**最干净的口径**，也是回答"买下每一个符合条件的新币"的那个数：

```
毛收益 均值 -4.47% / 中位 -1.86%
扣掉 1.4% 链上往返成本后：**-5.87% / -3.26%**
胜率 39.3%   赢的均值 +15.6%   输的均值 -17.5%
```

**一个符号会随着"怎么处理 0.7% 的数据"而翻转的测量，不能用来决定仓位。**

#### 15.23.4 已修：让价格可追溯

- provider 现在记录价格来自**哪个池子**（`pair_address`），并写进每一条快照；
- 评估器在计算前瞻收益时，**两端来自不同池子的直接丢弃**并计数
  （`pool_switch_dropped`）；
- 老数据没有这个字段，按"未知"容忍（不能因为加了字段就让历史样本全废）。

**注意**：这个修复只对**今后**的数据生效。历史那批无法追溯价格来源，
所以表 15.23.3 里"均值 -4.47%"这个数字仍然带着同样的不确定性 ——
**它足够说明"不能放大风险"，但不足以说明"这个方向是负的"**。

#### 15.23.5 那么该怎么做（按顺序，不跳步）

**不能做的**：直接按现在这张表放大仓位 —— 那等于把测量误差变成真实损失。

| 优先级 | 动作 | 理由 |
|---|---|---|
| **P0** | 让带 `pair_address` 的新数据累积 2–3 天，**重算尾部** | 尾部的真假决定这个方向成不成立 |
| P1 | CEX 侧测**真正的新上币**：用 OKX instruments 的 `listTime` 动态构建 universe | 我们现在的 `tier:new` 只是 JUP/PYTH/ONDO/W/PENGU 五个**固定**币，早就不是新币了 —— 真正的"新上币池"从没被测过 |
| P1 | 若尾部为真，**也先按单笔上限下注**，而不是一次性放大 | 肥尾策略的破产风险来自单笔极端亏损（rug），不是来自回撤深度 |

**一句话**：你要的形态（以新币为主、吃大波动）是目前唯一在数学上说得通的路，
但**先修尺子，再谈加仓** —— 现在这把尺子在最关键的那一段是坏的。

---

### 15.24 「60% 打新 / 10% 合约 / 30% 主力」—— 哪些有数据，哪些没有（2026-10-05）

用户给出了一份完整的仓位方案（60% Solana 打新 / 10% 低杠杆合约 / 30% 主流币短线波段）。
这是一份**执行方案**而不是一个问题，所以这里只做一件事：把它的每一块和我们手上的
测量结果对上，标出**哪一块已经有数字、哪一块还是零数据**。

先说一句实话：**这份方案的骨架是对的**。分账户隔离、单笔 1–2% 风险、
假设单笔可 100% 归零、分批止盈、时间止损、"高胜率不重要、活下来重要" ——
这些风控条款**没有任何一条和我们的数据冲突**。
问题不在框架，在**期望值**：三块里只有一块真的被测过，而它测出来是负的。

| 方案板块 | 我们的测量 | 结论 |
|---|---|---|
| **60% 打新**（pump.fun / LaunchLab，复制聪明钱） | **零数据**。我们测过的是"按换手/流动性筛完就买下每一个"（DEX 新币，最干净口径 n=4010 / 165 代币，均值 **-4.47%**、中位 -1.86%，扣 1.4% 成本后 **-5.87% / -3.26%**）——**不是**"复制一个筛过的聪明钱钱包" | 方案里唯一**从没被测过**的核心假设 |
| **10% 合约**（BTC/ETH/SOL，2–5x，分钟–小时级） | **零数据**（我们只做过现货）。唯一相关的可执行数字是资金费率 carry：往返 34bps，盈亏平衡 **BTC 21 天 / DOGE 17 天 / SOL 37 天**；持有 **1 天 = -32bps、胜率 0%** | "分钟级到小时级"与唯一有数据的形态**方向相反** |
| **30% 主力**（BTC/ETH/SOL 短波段） | **正是现在在跑的那套**。180 天样本外四策略全负（-0.54% ~ -1.08%）；实盘 25 笔已平仓、胜率 **24%**、净 **-$392.74** | 已经测过，而且**已经被证伪** |

金额换算：$103k 下，方案说的单笔 1–3% ＝ **$618–$1,854/笔**；单笔风险 1–2% ＝ $1,032–$2,066。
当前 majors 单笔上限是 $5,148 —— 比方案里写的还要激进。

#### 15.24.1 为什么"复制聪明钱"不能直接上

它是全套方案里**最合理、也最没被验证**的一环。它和我们已经测过的东西完全不是同一个总体：

- 我们测过的：**规则选币**（按换手 / 流动性筛）→ 负。
- 方案要做的：**跟单**（跟随一个经过筛选的钱包）→ **零数据**。

"规则选币是负的"**不能**推出"跟单是负的"，但同样**不能**推出它是正的。
在没有任何测量之前把 60% 资金放进去，等于把整份风控的精度
用在一个**符号未知**的期望值上 —— 风控做得再好，方向错了也是慢慢亏。

#### 15.24.2 有一个我们能自己测的入口

现有 `tier:new` 只是 JUP/PYTH/ONDO/W/PENGU **五个固定币**，早就不是新币了 ——
所以"新上币"这个类别**从来没有被真实测量过**。
可以用 OKX instruments 的 `listTime` 动态构建真正的"上市 0–30 天"池子，抓真实 K 线做
walk-forward。这是我们**唯一能靠自己补上的空白**，不需要新资金、新钱包或新账号。

"聪明钱跟单"要变成可测的，需要新建一条数据管道（记录候选钱包的进场时刻 → 之后的价格），
这是**新增工作量**，需要单独决定要不要做。

#### 15.24.3 需要用户决定的三件事

| # | 决定 | 说明 |
|---|---|---|
| 1 | 要不要先测"真新币池"（OKX `listTime` + walk-forward） | 能自己测，不需要新资金 |
| 2 | "聪明钱跟单"要不要立项做数据管道 | 需要新增工作量 |
| 3 | 60/10/30 要不要动**真钱** | 当前不建议；本仓（OKX 模拟盘）先做验证 |

**一句话**：框架留着，但 60% 那块的期望值现在是**一个问号**。
在把这个问号变成数字之前按 60% 下注，等于"风险控制得很好，但押在一个符号未知的方向上"。

---

## 16. 每日复盘（OKX 模拟盘）

> 本节由每日 21:00 的自动复盘追加，按日期倒序累积。

### 2026-10-06 复盘：零成交日（10-07 07:00 CST 补记）

**一句话**：**全天 0 笔成交、权益分文未动（+$0.00）**。不是策略问题——是账户侧被三道锁堵死，
策略连一次下单的机会都没有。

| 项目 | 数值 |
|---|---|
| 期初 → 期末权益 | $103,029.83 → **$103,029.83** |
| 当日盈亏 | **+$0.00（0.000%）** |
| 成交 | **0 笔**（上一笔成交是 10-05 的 11 笔） |
| 被拒订单 | **2 笔**（11:01、23:01，全部 AVAX/USDT） |
| 当日回撤 | 0.0% |

#### 当天真正发生的事（一条时间线）

| 时间(CST) | 事件 |
|---|---|
| 全天 | **IP 白名单 50110**：代理出口漂移，OKX 拒掉包括 `get_account` 在内的账户调用 → 循环**静默停摆**（进程活着、心跳在跳、但什么都做不了） |
| ~21:00 | 用户更新白名单 → **复验通过**：`venue_degraded` 归零、账户可读、权益恢复正常 |
| 11:01 / 23:01 | AVAX/USDT 两次 **54092**（TradFi 免责声明未接受）→ 无法开仓 |
| 当日 | 账户模式被反复切换 1→3→2→3，触发 **1 天合约冷静期** |

#### 今天最值得记住的一条：**观测盲区**

这一天暴露了一个监控缺陷：**"没有信号" 与 "被账户锁死" 在所有指标上长得一模一样**。
`ta_entries_today=0`、`open_positions=0`、`heat=0%`、看板全绿、watchdog 无越界——
但真实情况是机器人**一次都没试成功**。如果不是手工去查日志里的 `50110/54092`，
这一天会被误读成"行情没机会"。

**建议（优先级）**

| 优先级 | 动作 | 为什么 |
|---|---|---|
| **P0** | 新增指标区分「0 信号」与「信号被拒」：`ta_entries_rejected_total` + `ta_signals_seen_total` | 否则"没信号"和"被堵死"永远分不开——这正是本次要修的盲区 |
| **P0** | 用户去 OKX 模拟盘点掉 TradFi 免责声明 | 不点它就一直开不了仓（三次拒单全因它） |
| P1 | 任何账户侧变更（模式/白名单/免责声明）**预留 1 天缓冲** | 冷静期+审批链的教训 |
| P1 | 验证 AVAX 是否被归入 TradFi 分类 | 三次拒单全在它身上，不像巧合 |
| — | **不要因为零成交就去调策略参数** | 零成交的原因 100% 在账户侧，与策略质量无关 |

#### 累计（09-30 起，账本口径）

| 项目 | 数值 |
|---|---|
| 权益 | $103,029.83（起点 $103,357.89，**-0.317%**） |
| 已平仓 | 26 笔，胜率 26.9%，最好 +$64.10 / 最差 -$63.28 |
| 已实现净额 / 手续费 | **-$328.64** / **$176.73**（有效费率 0.1001%） |
| 最大回撤 | 0.0%（自 10-05 收盘后未动） |

### 2026-10-05 全天结算（10-06 07:00 CST 补记，覆盖当天早些时候的"进行中"版本）

**一句话**：运行以来**第一个正收益日**（+$37.40），但这是"价格赚、费用亏"的混合体，不是策略变好。

| 项目 | 数值 |
|---|---|
| 期初 → 期末权益 | $102,992.43 → **$103,029.83** |
| 当日盈亏 | **+$37.40（+0.036%）** |
| 已平仓 / 胜率 | 当日 3 胜 3 负（全天口径见下） |
| 全天结束持仓 | 空仓 |

**当天最重要的一件事不是盈亏，是第一次走完完整出场链条**：PEPE/USDT 按
「进场 → 分批止盈（+0.5 仓 @4.47e-6）→ 止损移到保本 → trend_pullback 出场」四步跑完，
净 **+$64.10** —— 这是账本里第一笔进入 `best` 的交易（此前 best 一直是 +$8.49）。
换句话说：**修复后的出场机制第一次真的赚到钱**，而不是只把亏损切碎。

#### 累积（09-30 起，账本口径）

| 项目 | 数值 |
|---|---|
| 权益 | $103,029.83（起点 $103,357.89，**-0.317%**） |
| 已平仓 | **26 笔，胜率 26.9%**，最好 +$64.10 / 最差 -$63.28 |
| 已实现净额 | **-$328.64** |
| 累计手续费 | **$176.73**（成交额 $176,547 → 有效费率 **0.1001%**） |
| 手续费占亏损 | **53.9%** |
| 最大回撤 | 0.211% |

#### 当日运行事件

- **02:29 一条 critical**：ADA/USDT 行情连续重试 3 次失败、累计 25 tick，风控自动
  「暂停新开仓」；约 2 分钟后自愈，当时空仓，无持仓受影响。此后 2 小时**零新增降级**，
  确认是代理链路的一次瞬时抖动（不是 ADA 本身的问题）——**不建议为它改 universe**。
- 其余全绿：三服务常驻、看板 200、watchdog breaches 空、0 Traceback、kill switch=0、
  无幽灵卖单、无 `adopted` 成交；对账仍是那 3 条灰尘差异（金额 < $0.10）。
- 链上：候选 40，snapshots 39,621 条；+5m 覆盖率仍 <30%，+1h 各组中位
  （-0.74/-1.90/-3.01%）与"低换手桶仅 7 个代币"的判据均无变化。

#### 结论与建议（依然不是调参）

| 优先级 | 动作 | 为什么 |
|---|---|---|
| **P0** | 继续观察 PEPE 这类"走完全链条"的交易能否重复 | 一笔 +$64 不能证明出场机制有正边际，只能证明它**能正常工作** |
| P1 | 把"费用占亏损 53.9%"作为长期主指标之一 | 有效费率稳定在 0.1001%，费用仍是最大的确定性支出 |
| — | **不要因为一天转正就调参数** | 26 笔、胜率 26.9%、累计 -$328.64；方向性证据一个都没变 |

> 口径说明：本节权益与累积数字取自**账本口径**（看板 `/api/snapshot`）。
> 按成交记录逐笔 FIFO 归因得到的日内拆分与之有出入（同一批成交会因持仓跨日而落到不同天），
> 因此日内拆分仅供参考，**以账本权益为准**。

### 2026-10-05（今日进行中，截至 11:15 CST）

**一句话**：运行以来少见的形态 —— **价格是正的，被手续费翻成负的**。

| 项目 | 数值 |
|---|---|
| 当日盈亏 | **-$26.70（-0.026%）** |
| 价格盈亏 / 手续费 | **+$12.42 / -$39.13** |
| 往返 / 胜率 | 4 / **3 胜 1 负（75%）** |
| 已平仓 | 全部是 SOL/USDT（$5,148/笔） |

今天 4 笔往返全是 SOL：三笔赚、一笔亏，价格合计 **+$12.42**，
但双边手续费 **$39.13** —— **手续费是价格盈亏的 3.15 倍**，直接把正的价格盈亏翻成净亏。
这是"费用决定符号"最干净的一次样本：**不是方向选错了，是成本结构不允许这么小的价差**
（单笔 $5,148 往返 = 约 $10.3 手续费 = 0.2%；要打出正收益，毛价差必须 > 0.2%）。

#### 巡检（2026-10-05 11:15 CST）

- 三个 launchd 服务全部 `state = running`（runner pid 1477 / watcher 21249 / dashboard 14624），
  看板首页 200、`/api/snapshot` 正常，`services.watchdog.breaches` **为空**。
- 代理 200；`ta_kill_switch=0`、`ta_open_positions=0`、`ta_heat_pct=0`、`ta_entries_paused=0`。
- 幽灵卖单 / 新增 `tag=adopted` 成交 / 新增 kill switch：**0**。
- `logs/ta-run-service.err.log` 里那串 `can't open .../run_demo_foreground.sh` 是
  **10-01 15:08 的旧记录**（mtime 一直没动），**不是**当前问题。
- ⚠️ 有 **3 条 reconciliation mismatch**（最后一条 **10-03 02:14 UTC**），
  全部是**灰尘级**（INJ 5e-6、PYTH 2.1e-4，金额 < $0.10）—— 与已修的"灰尘"问题是同一批残留，
  **不是**新的账实不符，且 10-03 之后无新增。
- 0 次 Traceback。

#### 链上（本轮）

- 候选 **40 个**（DexScreener profiles/boosts + GeckoTerminal trending）；
  最近一小时最活跃：**Human**（vol_h1 $2.56M）、**XEET**（$1.12M，txns_m5 2820）、**Ash**（$783k）。
- `data/onchain/snapshots.jsonl` **34,330 条**、mtime 11:15（新鲜，未超 10 分钟）。
- 事件研究（带活动过滤 `--min-txns-m5 5`）：+5m 覆盖率只有 **8%** → 仍不可用；+1h 覆盖率 79%。

| +1h 桶 | n | **代币** | 中位 | 均值 |
|---|---:|---:|---:|---:|
| 0-1 | 21 | 4 | +0.01% | -8.85% |
| 1-5 | 462 | **20** | -1.03% | +1.66% |
| 5-20 | 2179 | 55 | -2.04% | +40.75% |
| 20+ | 4023 | **78** | -1.78% | +9.71% |

- 1-5 vs 20+ 的中位差只有 **0.75pp**（10-02 同口径是 **3.47pp**），**已跌破 1.4% 往返成本**；
  而且 5-20 反而比 20+ 更差 → "越换手越差"不成立。均值被 p90（+35%）离群值拉高，不可用。
- 结论：方向仍在（高换手更差），但**幅度两轮差 4.6 倍**。这条从
  "有两侧样本支持的约束"**退回"假设"** —— 暂时不足以支撑"不买高换手新币"的硬规则。

### 2026-10-04（复盘 10-04 全天，CST）

**一句话**：三天里最差的一天 —— **0 胜 3 负**。

| 项目 | 数值 |
|---|---|
| 期初 → 期末权益 | $103,036.09 → $102,992.43 |
| **当日盈亏** | **-$43.65（-0.042%）** |
| 价格盈亏 / 手续费 | -$40.57 / -$4.47（**价格占 93%**） |
| 往返 / 胜率 | 3 / **0 胜 3 负** |
| 止损出场 | W/USDT @0.014872 |

#### 当日结论

这一天是**价格主导**（93%），和 10-05 的形态正好相反 ——
说明单日归因不稳定，但两天的合计依然为负。**不调参**：3 笔样本什么都证明不了。

### 2026-10-03（复盘 10-03 全天，CST）

**一句话**：区间里唯一正的一天 —— **2 胜 0 负，+$3.73**，但只有 2 笔，没有信息量。

| 项目 | 数值 |
|---|---|
| 期初 → 期末权益 | $103,033.71 → $103,037.95 |
| **当日盈亏** | **+$4.24（+0.004%）** |
| 价格盈亏 / 手续费 | +$9.90 / -$6.18 |
| 往返 / 胜率 | 2 / **2 胜 0 负** |

#### 累计（09-30 起，快照 2026-10-05 11:15 CST）

| 项目 | 数值 |
|---|---|
| 权益 | $102,965.73（起点 $103,357.89，**-$392.16 / -0.379%**） |
| 已平仓 | 25 笔，**胜率 24%**（6 胜 19 负），最好 +$8.49 / 最差 -$63.28 |
| 累计手续费 | **$168.43**（成交额 $168,341.76 → 有效费率 **0.1001%**） |
| 手续费占亏损 | 42.9% |
| 开仓中止 / 网络降级 | 0 / 累计 16 次（每次约暂停开仓 2 分钟） |

#### 每日复盘的总建议（仍然不是调参）

| 优先级 | 动作 | 为什么 |
|---|---|---|
| **P0** | 决定要不要测"真新币池"（OKX `listTime` + walk-forward） | 唯一还没测过、且我们能自己测的方向（§15.24） |
| P1 | 把"聪明钱跟单"变成可测的（新建数据管道） | 用户方案的核心假设，目前**零数据** |
| P2 | 链上"高换手更差"降级为假设，继续攒样本 | 本轮差额已跌破成本线，不能当硬规则用 |
| — | **不要调 CEX 策略参数** | 180 天样本外全负；10-05 又复现一次"费用决定符号" |

### 2026-10-02（复盘 10-01 全天，CST）

**一句话**：今天既没变好也没变坏 —— **-0.047% 基本就是手续费**。

#### 当日真实盈亏

| 项目 | 数值 |
|---|---|
| 期初 → 期末权益 | $103,295.60 → $103,247.53 |
| **当日盈亏** | **-$48.07（-0.047%）** |
| 价格盈亏 / 手续费 | **-$8.86 / -$39.21（手续费占 81.6%）** |
| 开仓 / 已平仓 | 5 / 5 |
| 胜率 | **1 胜 4 负（20%）** |
| 止损出场 | 2 次（JUP、PENGU） |

逐笔（净额已扣双边手续费）：

| 标的 | 成本 | 净额 | 收益 |
|---|---:|---:|---:|
| PENGU/USDT | $2,063.37 | **+$8.49** | +0.41% |
| OKB/USDT | $5,158.39 | -$3.54 | -0.07% |
| OKB/USDT | $5,157.35 | -$11.35 | -0.22% |
| SOL/USDT | $5,159.73 | -$18.03 | -0.35% |
| JUP/USDT | $2,061.36 | -$23.65 | -1.15% |

**手续费决定了当天的符号。** 价格盈亏只有 -$8.86（约 4.5bps，摊在 $19,600 的成交额上，
基本是噪声），而手续费 $39.21（往返约 20bps）。这正是 180 天回测那句
**"毛收益≈0、费用决定符号"**在实盘上的复现。

顺带一条对照：另一天（09-30）10 笔成交、手续费 $30.09；今天 11 笔、$39.21 ——
**每笔约 $3.5 的费用，两天都是同一量级**。

#### 当日最重要的事（不是盈亏）

1. **主机重启，三个 launchd 服务全部自动恢复** —— 自启第一次被真实事件验证，
   而且顺带把当天提交的灰尘修复带上了线。
2. **重启暴露看板 API 20 秒超时**（每次缓存失效都重建 ccxt 客户端 + `load_markets()`）。
   已改为复用客户端 + 看门狗预热，现在 0.26–0.57 秒。
3. **灰尘拒单复发**（第 2 笔，tag 都是 `mean_reversion:exit`），定位到策略退出路径，
   修了 `min_qty` 回退到交易所 `amount` 精度。
4. **链上研究被自己的范围限定卡死**（单源上限 12–17 个代币）——
   加了第二个发现源（候选 12→80）+ 滚动并集口径（代币 12→148）。
5. **得出链上第一个负结论**：+5m「买高换手」没有超过成本的边缘。

#### 优化建议（按优先级，**仍然不是调参**）

| 优先级 | 动作 | 为什么 |
|---|---|---|
| **P0** | 重启循环，让 `ta_bars_evaluated_total` 与灰尘修复生效 | 前者让"0 信号"能自证、后者止住灰尘拒单 |
| P1 | 把链上的结论改成**回避规则**：不买高换手新币 | +1h 中位随换手单调下降（-1.23 → -4.42%），样本已达 48/36 个代币 |
| P1 | 继续扩链上数据源 | 两端极端桶仍未达 20 个代币 |
| — | **不要调 CEX 策略参数** | 180 天样本外全负；今天又一次复现"费用决定符号" |

#### 当日结论

今天的 -0.047% 没有信息量 —— 它等于手续费。**真正的进展全在测量系统上**：
服务能自恢复了、看板不再谎报、灰尘不会伪装成持仓、"0 信号"即将可自证。

策略层面**一个字都没变好**，而且现在的账本已经可信到能证明这一点了。

### 2026-10-01（复盘 2026-09-30 18:31 CST → 10-01 07:01 CST 的实盘）

**一句话**：账本第一次完全可信；但实盘表现和 180 天回测的签名**一模一样** ——
**4 次往返 0 胜 4 负，手续费占亏损 45%**。

#### 当日实盘（交易所实测，run `7145e8598228`）

| 项目 | 数值 |
|---|---|
| 期初 → 期末权益 | $103,359.10 → $103,295.60 |
| **净盈亏** | **-$63.50（-0.061%）** |
| 价格盈亏 / 手续费 | -$34.61 / **-$28.89**（手续费占 45%） |
| 往返次数 / 胜率 | 4 / **0%**（0 胜 4 负） |
| 止损出场 | 2 次（PENGU @0.010231、JUP @0.333787） |
| 单笔规模 | PENGU $2,066、OKB $5,162、JUP $2,067 |

这也是"取消 $600 上限"第一次在实盘体现：**OKB 单笔 $5,162 = 旧值的 8.6 倍**，
正好压在新上限 $5,168 上（第 14.6 节）。

#### 账本完整性：第一次全部对上

| 检查 | 结果 |
|---|---|
| 下单 ↔ 成交 | 8 单 ↔ 8 成交（本 run） |
| 账本现金 vs 交易所 | 逐位一致（差 0.00000039） |
| 幽灵卖单 / `adopted` 接管 | 0 / 0 |
| 对账不一致告警 | 0（对账现在每 30 tick 在循环内跑，不再只在关机时跑） |
| Kill switch | 0 |

#### 风控按设计工作

2 次拒单，原因都正确：`symbol_daily_cap: OKB/USDT 2/2 today`、
`symbol_cooldown: JUP/USDT for 152m after a stop-out`。

#### 运行稳定性

- 10 次 `venue degraded` **全部是网络问题**（OKX K 线接口超时，ADA/USDT 最频繁），
  每次只暂停开仓约 2 分钟，自动恢复；**0 次 Traceback**。
- 12:48 UTC 之后约 **9.5 小时无信号**。已核对**不是**静默故障：
  `ta_stale_data_skips_total` 从未递增（数据不陈旧）、0 报错、0 降级、
  权益记录每约 74 秒照写 —— 是严格过滤下真的没有触发条件。

#### 今天复盘新查出的两个问题（已修，**等重启生效**）

1. **`ta_fees_total` 恒为 0 —— 真 bug。** 指标读的是 `AccountState.fees_paid`，
   而实盘 venue 从来不填这个字段。手续费是今天亏损的 **45%**，却没有任何实时监控
   能看到它。改成直接读账本（`Ledger.fees_paid`，从成交记录重算，重启后仍在）。
2. **`ta_venue_degraded` 是个锁存器**：代码里只有 `set(1.0)`、**没有任何归零的地方**，
   所以第一次网络抖动之后就永远显示"降级"。这让我前两次巡检误报过"当前降级、
   暂停开仓"。改成**每 tick 重置**，并新增 `ta_entries_paused` 表示
   "此刻是否真的在拦开仓"（这是之前**完全没有**的一个状态指标）。

新增 2 个回归测试，测试 104 → **106**。

#### 待办与建议（按优先级，**仍然不是调参**）

| 优先级 | 动作 | 为什么 |
|---|---|---|
| **P0** | 重启循环，让上面两个指标修复生效 | 否则明天复盘依旧看不到手续费 |
| P1 | 重新挂上盯盘扫描器：`TA_WATCH_INTERVAL=300 ./scripts/start_watch.sh` | 已停摆约 12 小时，+5m/+1h 研究断档 |
| P1 | 链上研究改口径：范围改成"最近 N 天出现过的代币**并集**" | 清单轮换比 24 小时还快，+24h 覆盖率被结构性清零（第 14.10 节） |
| — | **不要因为 4 笔亏损就调策略参数** | 4 笔样本什么都证明不了；180 天样本已经证伪这四个策略的方向性（第 0 节） |

#### 当日结论

今天证明了**测量系统**是可信的：账本、限额、止损、对账、拒单理由全部对得上。
也第一次把"费用主导"从回测搬到了实盘（手续费 = 亏损的 45%）。

但**"策略为正"依然没有证据**。账本可信 ≠ 策略为正 —— 这两件事必须继续分开看。

### 2026-09-30

**当日真实盈亏：-$2.70 / $5,000（-0.05%）** —— 注意：这个数字**只能用交易所数据算**，
本地账本根本算不出来（原因见下）。

| 项目 | 数值 |
|---|---|
| 机器人买入 | INJ 25.5933、PYTH 1237.0167 |
| 花掉现金 | **$295.65**（USDT 5,000 → 4,704.35） |
| 当前市值 | $292.95（INJ $195.25 @ $7.629 + PYTH $97.70 @ $0.079） |
| 浮动盈亏 | **-$2.70（-0.05%）** —— 基本等于手续费+点差，方向性尚未体现 |
| 手动底仓 | BTC 1.0 / OKB 100 / ETH 1.0 —— 仍被正确排除在风控之外 |

**账本侧：这两笔成交完全没有记录。**

```
orders: {rejected: 4, open: 2}      <- 两笔"open"就是上面这两笔市价单，filled=0.0
fills : {adopted: 3}                <- 只有历史合成记录，没有任何真实成交
```

#### ⚠️ 当日最严重问题：成交漏记，并由此引发熔断死循环

1. **根因**：`CcxtVenue.sync()` 只调用 `fetch_open_orders()`。OKX 现货市价单常先返回
   `status=open, filled=0` 再异步成交；一旦离开挂单簿，我们**再也不回查它** → 成交永久丢失
   （`_track()` 本身能处理增量成交，缺的只是数据源）。
2. **后果一**：仓位对账本不可见 → 被归类为**非托管** → 没有止损、不计入风险限额、
   `flatten_all` 也平不掉它们（实测 `open_positions 0`，而交易所实际持有 2 个仓位）。
3. **后果二**：买币花掉的 $295.65 现金，在账本看来是**纯亏损** →
   `daily loss flatten`（-3.998%）**每个 tick 重复触发**（00:47–00:50 告警日志连续 5 条），
   机器人事实上已停止开新仓，且告警持续刷屏。

#### 策略优化建议（按优先级，且**不是**调参）

| 优先级 | 动作 | 为什么 |
|---|---|---|
| **P0** | 修 `sync`：回查 `fetch_order(id)` / `fetch_closed_orders`，喂给现成的 `_track()` | 唯一能恢复可信账本的动作；在它修好之前，任何收益/风险数字都不可信 |
| **P0** | 熔断去重：同一 `flatten`/`pause` 状态只告警一次，不每 tick 重复执行 | 现在会无限刷 critical 告警并冻结交易 |
| P1 | 链上研究加「零变动占比」指标 + 交易频率过滤（`txns_m5 ≥ 5`） | 98% 覆盖率下中位数仍是 0.00%、胜率 0.8% —— 当前在研究"没成交"，不是研究价格 |
| P2 | 处理 INJ/PYTH 两个遗留仓位（手动卖出，或纳入管理） | 它们现在既不在风控里，也不会被自动平仓 |
| — | **不调策略参数** | CEX 四策略 180 天样本外全负、费用与净收益同量级；已证伪的假设不再重复（提频、回踩确认、30 天样本判定） |

#### 当日结论

**今天无法评估策略优劣** —— 账本不可信（漏记两笔真实成交）、熔断空转（flatten 重复触发）。
真实盈亏 -0.05% 说明方向性收益还没体现，费用与点差已开始显现。

**"收益最大化、风险最小化"的下一步是修 bug，不是调策略。** 当前状态下每次调参都是在
一个连持仓都数不清的系统上做实验。

---

## 17. 模拟仓测试方案（2026-10-05 起）

> 用户决定：**先测模拟仓**，不动真钱。本章记录"用什么测、现在能测到哪、结果是什么"。

### 17.1 三条腿各自怎么测

| 腿 | 模拟手段 | 现状 |
|---|---|---|
| **60% 打新**（Solana 新币） | `ta onchain quote`（真实取数 + 安全过滤 + 模拟报价；`signer=mock` / `dry_run=true` / 不允许广播）+ 新的出价前漏斗 `scripts/onchain_funnel.py` | **可跑**（只读）。链上**没有撮合模拟仓**，所以"真成交"只能靠已记录的快照做纸面回放 |
| **10% 合约**（BTC/ETH/SOL） | OKX Demo 永续 | **品种可用**：实测 demo 有 **485 个 USDT 永续**，BTC/ETH/SOL/DOGE 全部 `live`（BTC 面值 0.01 / SOL 面值 1 / DOGE 面值 1000）。但 runner **目前只有现货**（`venues.okx_demo.market_type = "spot"`），要测合约得先加永续支持 |
| **30% 主力**（BTC/ETH/SOL 短线） | 就是现在跑的 OKX Demo 现货循环；也可用 `ta run --venue paper`（本地模拟器）+ `ta backtest --source history --split 0.7` 做 walk-forward | **已在跑**（已测过，样本外全负） |

### 17.2 今天的第一个模拟结果：打新腿的"出价前漏斗"

新增 `scripts/onchain_funnel.py`（只读）：对当前候选逐个跑**和实盘执行完全同一个**
`TokenScreener`，统计通过率与拒绝原因。

```
python3 scripts/onchain_funnel.py --limit 40
```

**结果：40 个候选，17 个（42.5%）通过安全门槛。**

| 拒绝原因（分桶） | 条数 |
|---|---:|
| 流动性 < $50,000 | 17 |
| top10 持仓 > 40% | 14 |
| LP 锁定不足 | 4 |
| 蜜罐状态未知（无扫描结果） | 2 |

通过的例子（按分数）：`AGENTCAT` 97.3 / `e/acc` 94.9 / `biketyson` 91.0 /
`COCKROACH` 87.6 / `swordcat` 87.3。

**这条结果的含义要说清楚**：安全门槛**不是**瓶颈 —— 42.5% 能过，说明打新腿
**有票可买**，不是"结构性买不到"。真正的瓶颈仍然是**边际**：把符合条件的新币全买下来，
最干净口径的均值是 **-4.47%**、扣 1.4% 往返成本后 **-5.87%**。
**通过安全过滤 ≠ 值得买** —— 脚本自己也会打印这句。

报告落盘：`reports/onchain-funnel-<时间戳>.json`。

### 17.3 还差什么（按顺序，先易后难）

| 顺序 | 动作 | 为什么是这个顺序 |
|---|---|---|
| 1 | **打新腿纸面组合**：把扫描器记录的快照当成交价，按明确规则模拟"买入 → 持有 → 卖出"，用 1.4% 往返成本，输出虚拟权益曲线 | 不需要新数据源，今天的数据就能算；直接回答"60% 放在这里会怎样" |
| 2 | **合约腿**：给 runner 加永续支持，先用最小面值在模拟盘跑 | 品种这关已经通了，剩下是代码 |
| 3 | **真新币池**：用 OKX `listTime` 建"上市 ≤30 天"池（今天有 CT 5 天 / XDP 7 天 / PONS、VVV 20 天，约 13 个 USDT 对），做 walk-forward | CEX 侧唯一没测过的类别 |
| 4 | **聪明钱跟单**：新建数据管道（记录候选钱包进场 → 之后价格） | 工作量最大，且是用户方案里唯一零数据的一环 |

### 17.4 约束（不变）

- 不下真单、不广播：链上 `dry_run = true` / `signer = "mock"` / `allow_broadcast = false` 三重锁。
- 巡检类的"不改配置、不重启进程"约束继续有效；**动配置前会先问**。

---

## 18. 新币研究：CT 这类币能不能"挑"出来（2026-10-05）

> 用户问：CT 这种新币能不能研究参数、建模型，按策略调成"挑到这种币"。

### 18.1 先纠正一个前提：CT 的 +475% 不在你能交易的地方

CT-USDT 的上市参数：`openType = "call_auction"`，`listTime` = 2026-09-30 10:00 UTC，
`auctionEndTime` = 11:00 UTC。也就是说 **10:00–11:00 是集合竞价，11:00 才切连续交易**。

| 时刻 | 价格 |
|---|---:|
| 竞价开始（首根 1H 的开盘） | 0.075 |
| 首根 1H 收盘 = 连续交易起点 | **0.431（+475% 已经发生完了）** |
| 上市至今最高 | 0.63868 |
| 现在 | 0.48311 |

所以"挑到 CT"要先拆开：**+475% 发生在集合竞价里**，一个按行情触发的机器人拿不到。
机器人能拿到的最早起点是 **0.431**，从那之后 CT 最多再涨 **+48%**。

### 18.2 先把样本建起来：OKX 上"新加密币"到底有多少

- 403 个 USDT 现货对里，上市 ≤180 天的有 130 多个 —— 但**绝大多数是代币化美股**。
- 干净的区分字段是 `instCategory`：**`1` = 加密币，`3` = 代币化股票**（XAAPL、XMUU…）。
- 按 `instCategory = 1` 过滤后，**近 180 天真正的新加密币只有 27 个**（其中 4 个是稳定币）。

新增 `scripts/new_listing_study.py`（只读：公开行情，无 key、无签名、无订单）。

### 18.3 基准率（入场 = 上市后第一根 1H 收盘价，即机器人真能付的价）

n = 27（含 4 个稳定币）；剔除稳定币后 n = 23。

| 指标 | 中位 | 均值 | p90 | 最大 |
|---|---:|---:|---:|---:|
| 48h 内最大上冲 | +4.93% | +12.26% | +41.4% | **+89.2%** |
| 48h 内最大回撤 | **-14.02%** | -16.53% | — | — |
| 持有 1h | -1.39% | -3.95% | — | — |
| 持有 24h | -2.12% | -5.97% | — | — |
| 持有 48h | -0.88% | -2.76% | — | — |

- 48h 内上冲 ≥ +20%：**5/27（19%）**（剔除稳定币 5/23 = 22%）
- 48h 内上冲 ≥ +50%：**1/27（4%）**
- 先摸 +20% 而不是先摸 -20%：**3/27（11%）**；反向（先摸 -20%）有 8 个
- 策略「+1h 全部买入、+48h 卖出」扣 0.2% 费用：**中位 -1.08%、均值 -2.96%、胜率 30%**

**先买下全部 = 亏。** 这和链上那条结论是同一个形状。

### 18.4 唯一能把两组分开的因子（以及它的全部弱点）

把 23 个加密新币按"首小时是否收涨"和"首小时成交额是否过中位数（$1.20M）"切：

| 组 | n | 48h 内 ≥ +20% | 持有 48h 中位 |
|---|---:|---:|---:|
| 首小时收涨 | 14 | **5（36%）** | -10.4% |
| 首小时收跌 | 9 | **0（0%）** | -0.9% |
| 首小时成交额 ≥ $1.20M | 12 | **5（42%）** | -13.1% |
| 首小时成交额 < $1.20M | 11 | **0（0%）** | -0.9% |

**会拉的那 5 个，全部同时满足"首小时收涨 + 成交额过中位"**（Fisher 单侧 p ≈ 0.024）。

弱点必须写全：

1. 这是**多重切分里的一条**（试了涨幅、成交额两个维度），n=23 下这个 p 值经不起再切。
2. 只有 **5 个正样本** —— 去掉任何一个，结论就崩。
3. **即使在"好"那一组里，持有 48h 的中位也是 -13.1%。** 上冲是真的，能拿住是假的 ——
   钱在上冲里，不在持有里。这决定了出场规则（分批 / 跟踪止盈）必须单独测。
4. 它是**事中**因子（要等首小时走完才知道），不是事前预测。

### 18.5 结论与下一步

- 「挑到 CT 这种」的基准率是 **19%（≥+20%）/ 4%（≥+50%）**，把新币全买下来是负的。
- 唯一能分开两组的因子是**首小时的涨幅与成交额**，样本刚够"≥20 个代币"的门槛，
  但正样本只有 5 个 —— 记为**假设**，继续攒样本（这个池子每月新增约 2–5 个）。
- **不要现在照着它改参数。** 23 个样本、一条切分，不足以支撑仓位。

下一步（仍按 §17.3 的顺序）：

1. 让 `new_listing_study.py` **每日自动累积**：新币一上市就记录首小时指标，攒到
   ≥20 个正样本，再看这条因子还在不在。
2. "上冲兑现"的规则（分批止盈 / 跟踪止盈）单独测 —— 因为"会拉"和"能拿住"是两件事。

### 18.6 同日补充：出场规则全为负，因子在更大样本上降级（2026-10-05 下午）

#### 18.6.1 出场规则：没有一条能把"上冲"变成钱

对 23 个非稳定币新币（入场 = 首根 1H 收盘，扣 0.2% 费用）比较 6 种出场：

| 规则 | 中位 | 均值 | 胜率 | 最好 | 最差 |
|---|---:|---:|---:|---:|---:|
| 死拿 48h（基准） | **-4.78%** | -3.42% | 35% | +78.2% | -65.4% |
| TP +20% / SL -20% / 48h | -6.38% | -5.60% | 30% | +19.8% | -20.2% |
| TP +50% / SL -20% / 48h | -12.17% | -7.32% | 26% | +33.1% | -20.2% |
| TP +20% 半仓 + 余仓跟踪 10% | -5.06% | -4.81% | 22% | +14.2% | -20.2% |
| 跟踪止损 10% / SL -20% / 48h | -5.06% | -5.05% | 22% | +14.0% | -20.2% |
| 跟踪止损 20% / SL -20% / 48h | -10.17% | -7.93% | 22% | +14.0% | -20.2% |

**每一条都比"死拿"更差。** 加止损 / 止盈只是把本来就负的分布切得更碎，切不出正的。
（保守约定：同一根 bar 内止损先于止盈触发，不给自己乐观顺序。）

#### 18.6.2 因子在更大样本上降级：样本 23 → 43 → 61

把窗口从 180 天扩到 400 天，样本一路变大（累积器上线后又自动补齐）：

| 样本 | 因子内 ≥+20% | 因子外 ≥+20% |
|---|---:|---:|
| n=23（180 天） | 5/12（42%） | **0/11（0%）** |
| n=43（400 天） | 8/17（47%） | 5/26（19%） |
| **n=61（400 天，累积器补齐）** | **10/23（43%）** | **5/38（13%）** |

两件事同时成立，必须一起说：

1. **§18.4 那句"5 个赢家全在因子内"是小样本假象** —— 样本变大后，赢家里始终有
   **5 个在因子外**（RE、KITE、SENT、DASH、OFC；其中 KITE 首小时还是 **-29.5%**）。
2. **但两组的比例差一直保持在 3 倍左右（43% vs 13%）。**

所以因子的正确表述是：**它是一个"提高命中率"的概率过滤，不是"挑出赢家"的判定器**。
叠加 18.6.1（出场全负），当前结论是：**这条路的入场和出场都还没有正边际。**

这恰好证明了"每日累积"值得做 —— 一个 23 个样本的干净结论，到 43 个样本就换了形状。

#### 18.6.3 已上线的自动化

- `scripts/new_listing_track.py`：每日累积器，幂等（同一 `instId` 只记一次），写
  `data/new_listings/records.jsonl`。
- `deploy/com.tradingagent.newlisting.plist.template` + `scripts/install_newlisting_service.sh`：
  独立的 launchd 批处理任务（每日 09:10 / 21:10，`KeepAlive=false`），**不碰** runner / watcher / dashboard。
- 查看：`./scripts/install_newlisting_service.sh status`

首个服务轮次已跑通（exit code 0），基线回填 **67 条**记录（61 个非稳定币 + 6 个稳定币），
此后每个交易日 09:10 / 21:10 自动续写。

---

## 19. 期权放进 10% 那条腿（2026-10-05 决定）

> 用户决定：期权放到 **10%** 仓位里（原计划是低杠杆永续）。

### 19.1 这个位置是对的，而且几乎是唯一能放的位置

- **60% 打新**是 Solana 链上新币 —— **根本没有期权市场**，链上期权不可执行；
- **30% 主力**是现货短线；
- 所以期权只能落在 **10% 的衍生品腿**上。结构上自洽。

### 19.2 期权市场实测（OKX，USDT 保证金 `_UM` 系列）

| | BTC-USD | ETH-USD | SOL-USD |
|---|---:|---:|---:|
| 双边报价合约数 | 1114 | 897 | **77** |
| 到期覆盖 | 到 2027-09 | 到 2026-10+ | **只有 3 个到期（≤9 天）** |
| 30 天平值 IV | 33.3% | 45.9% | 51.6% |
| ATM call 成本 | 3.42% | 4.64% | 2.65% |
| **24 天跨式成本** | **7.1%** | **9.8%** | 5.4%（3 天） |
| 回本需要的涨跌幅 | ±3.6% | ±4.9% | ±2.7% |
| ATM 相对买卖价差 | 5.0% | 4.2% | **44%** |

两条结论：

1. **成本墙比现货高两个数量级**：跨式一趟 7–10% 名义本金，而现货往返 0.2%、链上往返 1.4%。
2. **SOL 期权现在不可用**：只有 ≤9 天到期、双边价差 44% —— 所以"用 SOL 期权对冲打新仓"
   这条路**目前走不通**（想对冲只能退而用 SOL 永续，那是另一种风险）。

### 19.3 关键测量：IV 到底比 RV 贵还是便宜

| 标的 | RV30 | IV | IV − RV30 |
|---|---:|---:|---:|
| BTC | 34.3% | 33.3% | **-0.9pt** |
| ETH | 38.5% | 45.9% | **+7.4pt** |
| SOL | 56.1% | 51.6% | **-4.5pt** |

这条**修正了直觉**：

- **不是"期权太贵"** —— BTC/SOL 的 IV 甚至略低于近 30 天实际波动；
- **ETH 明显偏贵**（+7.4pt）→ 买 ETH 权利金就是给做市商送钱；
- 所以"卖波动"在这里**没有**普遍优势，"买波动"也**不是**普遍亏 —— **必须按标的分别看**。

### 19.4 放进 10% 的具体样子（$103k 本金 → 期权腿约 $10,300）

| 用法 | 单次支出 | 控制的名义本金 | 最大亏损 |
|---|---:|---:|---:|
| 买 BTC 24 天平值跨式 | 整条腿 $10,300 | ≈ $145k | **$10,300（100%）** |
| 买 BTC 单腿（call 或 put） | $1,030（总资金 1%） | ≈ $30k | $1,030 |
| 买 BTC 跨式 | $1,030 | ≈ $14.5k | $1,030 |

**关键换算**：期权腿的最大亏损是 **100% 的权利金**（不像现货能扛）。
所以要按"单笔风险不超过总资金 1%"（= **$1,030**）来买**权利金**，
而不是按名义本金控制 —— 这正好对上方案里"单笔风险 1–2%"那条纪律。

### 19.5 现在还不能做的事（诚实清单）

- **模拟盘能不能交易期权还没验证**：ccxt 在沙箱模式下 `load_markets()` 通过代理
  `RequestTimeout`，待重试。
- 无论结果如何，**runner 目前只有现货**（`venues.okx_demo.market_type = "spot"`），
  要测期权必须先把期权支持加到 venue —— 和"合约腿"是同一项工作，不是两件。
- 期权**不能**放进 60%：链上新币没有期权市场。

### 19.6 已上线的监控

`scripts/vol_monitor.py`：每天记录 RV7 / RV30 / RV90、30 天平值 IV、跨式成本与回本幅度，
写 `data/vol/rv_iv.jsonl`。已并入 `com.tradingagent.newlisting` 的每日任务
（`scripts/daily_research.sh` 串起来跑，仍然只有一个服务、不碰其他三个）。

判据很清晰：**IV − RV30 持续为正 → 少买权利金；持续为负 → 买波动才有依据。**

---

## 20. 「50% 主流货币 + 50% 合约期权」的风控映射（2026-10-06，待用户确认后才改配置）

用户决定把仓位结构调整为 **50% 主流货币（现货）+ 50% 合约与期权**，新币留到下一步接 GMGN。
本节把这句话翻译成 `config/risk.toml` 里能执行的数字，**并指出一个必须先说清的歧义**。

### 20.1 必须先说清的歧义：50% 是按什么算？

同一句「50% 放在合约」，三种口径的风险差 3–10 倍：

| 口径 | 含义 | 对 $103k 账户 |
|---|---|---|
| 按**保证金** | 占用 50% 权益做保证金 | $51.5k 保证金，3x → **$154k 名义** |
| 按**名义本金** | 合约名义 ≤50% 权益 | $51.5k 名义，保证金仅需 ~$17k |
| 按**权利金**（期权） | 买期权花掉 50% 权益 | $51.5k 权利金，**最大亏损就是全额** |

**建议采用「按保证金 + 杠杆≤3x」，并且给期权单列（按权利金）** —— 否则"50%"名义上是五成，实际敞口是三倍。

### 20.2 现值 → 建议值 对照表

| 参数 | 现在 | 建议（50/50 拆账） | 理由 |
|---|---|---|---|
| `max_gross_exposure_pct` | 45%（全账，≈$46.4k） | 现货 **50%**（≈$51.5k）+ 衍生品保证金 **50%** | 拆成两条腿，各自独立计时 |
| `max_open_positions` | 5 | 现货 ≤4 / 衍生品 ≤2（合计 6） | 衍生品要留独立额度，避免被现货占满 |
| `max_heat_pct` | 3.5%（≈$3.6k） | 现货 2.5% + 衍生品 1.5%，**合计仍 ≤4%** | heat 是唯一的"真风险"尺度，不要线性翻倍 |
| `max_trades_per_day` | 12 | 现货 8 + 衍生品 4 | 衍生品换手要更慢 |
| 杠杆 | 100%（=现货无杠杆） | 合约 **2–3x 封顶** | 10% 腿的原始设计就是"低杠杆" |
| 期权单笔 | — | **权利金 ≤ 总资金 1%**（≈$1,030）；期权腿合计 ≤5% | 买期权的最大亏损=全额权利金 |

### 20.3 我必须说的一句话

按现有证据，把**一半**仓位放进衍生品，是**风险放大**而不是收益优化：

1. CEX 四个现货策略 180 天样本外全负（-0.54% ~ -1.08%），费用与净收益同量级；
2. 永久合约方向的边际**从未测过**（我们只做过现货 + 资金费率 carry）；
3. 期权唯一可执行形态是买权利金，而 24 天跨式成本 **7.1%(BTC)/9.8%(ETH)** 名义本金
   —— 在方向没有边际的前提下，买权利金等于按 IV 持续付费。

所以本节给出的是一张**能安全执行的映射表**，不是"这样能赚钱"的背书。
**建议先按最小规模上线（衍生品腿 ≤10%、杠杆 2x），跑出真实成交与费用数据后再考虑放大到 50%。**

### 20.4 前置条件（未满足则无法执行）

| 前置 | 状态 |
|---|---|
| 模拟盘账户切到可开永续的模式 | ❌ 未完成（`acctLv=1`；API 返回 51070，需用户在官网/App 切换） |
| 期权需切到组合保证金 | ❌ 未完成（同上） |
| venue 支持 `market_type = "swap"` | ❌ 代码未写 |
| 本表经用户确认后再改 `config/risk.toml` | ⏳ 待确认 |

---

## 21. 合约腿交付定义（2026-10-06，用户指定）

用户要求：**代码更新完成后，README 要更新，并且功能要同步到看板上。**
把这句话落成可核对的验收清单，避免"代码写完了但没人知道"。

### 21.1 交付三件套（缺一不算完成）

| # | 交付物 | 验收标准 |
|---|---|---|
| 1 | **代码** | `CcxtVenue` 支持 `market_type = "swap"`：`tdMode`、`posSide`、出场 `reduceOnly`、按张数下单；配套单测通过（当前基线 136 全绿） |
| 2 | **README** | 新增「合约腿」章节：如何配置、如何回滚、与现货腿的额度关系；第 0 节的策略结论表同步更新 |
| 3 | **看板** | `src/ta/dashboard.py` + `src/ta/web/index.html` 增加合约腿区块：张数/名义/保证金占用/未实现盈亏/强平价；`/api/snapshot` 暴露对应字段 |

### 21.2 已知的前置条件与风险

| 项 | 状态 |
|---|---|
| 模拟盘账户模式 | ✅ `acctLv=3`（跨币种保证金），永续可用（SOL-USDT-SWAP 约 2534 张） |
| 期权 | ❌ 模拟盘不支持（最高只到 3），已确定走**纸面研究**（方案 B） |
| 改 `ccxt_venue.py` 会触碰**运行中的代码** | ⚠️ 必须先出上线清单，且部署需要空仓窗口重启 |
| 代理 IP 漂移 | ⚠️ 仍需根治（固定出口 IP），否则白名单会再次失效 |

### 21.3 顺序

```
swap_math.py（已完成，9 单测）
   → CcxtVenue market_type=swap（进行中）
      → 单测 + 纸面/最小额验证
         → README 第 21 章回填 + 第 0 节结论更新
            → 看板新增合约腿区块
               → 上线清单 → 用户确认 → 空仓窗口重启
```

### 21.4 时间约束：合约冷静期（2026-10-06 用户告知）

**切换账户模式后 OKX 有 1 天合约冷静期**，期内不能交易合约。

| 项 | 影响 |
|---|---|
| 合约下单验证 | ⏸ **顺延到 10-07**（今天验不了，不是故障） |
| 现货腿 | ✅ 不受影响，主循环照常（本日复验：权益 103,029.83、空仓、`venue_degraded=0`） |
| 代码工作 | ✅ 不受影响，继续推进 |

**今天的排期因此变成"代码先行"**：`swap_math.py`（已完成）→ `CcxtVenue` 的 swap 支持 →
**10-07 做合约的最小额下单验证** → 通过后再谈 README 回填与看板同步。

⚠️ 顺带记一条教训：**账户模式的任何切换都要预留至少一天的缓冲**，
以后不要把它安排在需要当天验证的节点上。

---

## 22. 新合约策略的首次回测：骨架也是负的（2026-10-07）

按用户要求**重新写**了永续策略（不沿用现货那套），走主流趋势回踩骨架 + 资金费率闸门：
`src/ta/strategies/perp_trend_funding.py`（+6 单测，合计 152 全绿），并注册进策略表、
在 `config/strategies.toml` 以 **`enabled = false`** 加入配置块。

### 22.1 样本外结果（walk-forward 0.7，1 万本金）

| 指标 | 数值 |
|---|---:|
| 期末权益 | **9,712.15（-2.88%）** |
| 最大回撤 | 2.89% |
| 交易笔数 | **501** |
| 胜率 | **15.5%** |
| 盈亏比（profit factor） | **0.18** |
| 每笔期望 | **-0.77** |
| 手续费 | 187.47 |
| 出场构成 | 时间止损 211 / 时间止盈 38 / 止损 1 / 分批止盈 1 |
| 3 倍成本压力测试 | **-6.51%** |
| 系统判定 | **`do not trade this configuration`** |

### 22.2 但这次回测有两个必须说清的限制

1. **资金费率闸门是空转的。** 我们手上的历史是现货 OHLCV，**不含 funding**，
   策略因此回退到 `funding_rate_bps = 0` → 闸门永远开着。
   **本次测到的只是"趋势+回踩"骨架，不是我们真正的假设。**
   （真实 funding 历史在 `data/history/funding/`，10 个币、8 小时一条 —— 接进去是下一步工作。）
2. **实际跑的是 15m，不是策略声明的 1h** —— 回测用的是 config 的 `universe.timeframe`。
   这会让"回踩 + ATR 止损"的比例失真，属于已知的口径差异。

### 22.3 结论

- 即便只看骨架，**样本外依然是负的**，而且比现货四策略更差（-2.88% vs -0.54%~-1.08%）；
- 出场构成很说明问题：**211 次时间止损 vs 38 次时间止盈** —— 回踩进场大多等不到趋势延续，
  最后被持仓上限时间扫出去，费用照付；
- **因此它继续留在 `enabled = false`。** 这次回测的价值不在"发现一个能赚钱的策略"，
  而在于**它在我们上模拟盘之前就把一个负的配置挡住了** —— 这本就是回测的作用。

**下一步**：把 `data/history/funding/` 接进回测，再判一次"资费闸门打开 vs 关闭"的差异。
如果接上真实 funding 之后仍然为负，这条腿就该像现货四策略一样结案，而不是继续调参。

---

## 23. 【标签记忆】TradFi（代币化美股/ETF）可用性分级与挂起待办（2026-10-07）

> 用户要求把这条"做成标签记忆"，因为它**极易被误判**——我已经错过两次，记在这里防止第三次。

### 23.1 一句话结论

**TradFi 不是"有/没有"的问题，而是三个层级**，混在一起谈就一定会得出错误结论：

```
① 实盘有、模拟盘没有      82 个   例：XAAPL / XTSLA / XMSFT / XEWY / XSPY
② 模拟盘"列着"但没有行情  15 个   例：XAMD / XASML / XAVGO / XDELL / XGME / XPLTR
③ 模拟盘真能用            7 个   见下表
```

### 23.2 命名陷阱（我踩了两次的那一脚）

| | 命名规则 | 例子 |
|---|---|---|
| **实盘** | `X` **前缀** | `XAAPL`、`XTSLA` |
| **模拟盘** | `X` **后缀**（部分） | `AAPLX`、`TSLAX`、`AMZNX` |
| 两边同名 | 少数 | `XNVDA`、`XQQQ` |

**拿实盘名字去查模拟盘 → 会得到"not listed"的假结论。**
另注：模拟盘的 `instCategory` **不可靠**——`AVAX`/`BCH`/`SCR`/`XRP` 也被标成 `3`（加密币）。

### 23.3 当前唯一可用的 7 个标的（`data/history/okx_demo/`）

| 标的 | 日线根数 | 周线根数 |
|---|---:|---:|
| XMU | 90 | 12 |
| XSPCX | 83 | 11 |
| XNVDA | 74 | 10 |
| XINTC | 71 | 9 |
| XMRVL | 71 | 9 |
| XQQQ | 60 | 8 |
| XMSTR | 56 | 7 |

**样本极短**：日线 56–90 天、周线 7–12 根。**只够"看一眼"，不够下结论。**

### 23.4 挂起待办（写在这里，否则半年后没人记得）

| # | 待办 | 触发条件 |
|---|---|---|
| **T1** | 把 ① 类（82 个实盘独有）与 ② 类（15 个无行情）纳入池子 | **接到实盘时**（用户 2026-10-07 明确说明） |
| **T2** | ETF 周线重测（≥30 周样本才有统计意义） | **约 2027-04** |
| **T3** | 个股日线 walk-forward（6 个标的，H=3/5/7 对照） | 下一执行步骤（用户已同意） |

### 23.5 相关的既有结论（不要重测）

- 1H 周期下 TradFi **在成本线以下**（XAAPL 1.15x、XQQQ 0.98x、XSPY 0.66x，成本 0.26%）
  → 低波动**不等于**更容易赚，成本固定而可赚幅度按比例缩小；
- **日线**下转为可交易（XAAPL 2.68x、XTSLA 3.57x、XNVDA 3.62x，H=1）；
- 持有期越长越好（XAAPL：H=1 的 2.68x → H=7 的 8.25x）→ **降换手是有效方向**；
- **杠杆 ETF（XSOXL）必须单列**：29x 的成本空间来自杠杆放大，但存在波动率衰减。

### 23.6 T3 执行结果（2026-10-07）：不是"负"，是"**不可评估**"

隔离配置目录 `config-tradfi/` 建立成功（**主 `config/` 未被污染**：`main validate -> []`，
且 `trend_breakout_daily` 不存在于主配置中——实盘 feed 不必为 20 个标的多取日线）。

| 标的 | 结果 |
|---|---|
| XMU / XSPCX / XNVDA | **trades 0**（in-sample 与 out-of-sample 都是 0） |
| XINTC / XMRVL / XMSTR / XQQQ | `not enough common history`（39–49 根日线） |

**原因不是策略差，而是数据太短**：XMU 最长也只有 90 根日线，而策略 warmup 就要 **35 根**
（`ema_slow=30`+5），剩下 55 根里还要同时满足「20 日唐奇安突破 + 量能 1.5× + 成本过滤 ≥3×」——
一个都没满足。

**结论：TradFi 日线归入"等样本"，与 T2（ETF 周线）同类。**
需要 **≥200 根日线**（约 10 个月）才可能产生有意义的样本量。

**明确不做的**：不把 warmup 调到 5、不关掉量能过滤——那是"为了让结果出现而放松条件"，
这个项目已经证伪过无数次这类做法。**样本不够就是不够。**

### 23.7 【归档挂起】模拟交易暂停（2026-10-07，用户指示）

用户指示："完毕后归档挂起模拟交易"。

**挂起时的状态**（最后一次实测）：

| 项 | 值 |
|---|---|
| 权益 | $103,029.83（自起点 -0.317%） |
| 持仓 | 空仓 |
| 已平仓 | 26 笔、胜率 26.9%、已实现净额 **-$328.64** |
| 累计手续费 | $176.72（有效费率 0.1001%） |
| 挂起原因 | ① IP 白名单漂移（已修）② TradFi 免责声明未接受 ③ **账户切到跨币种保证金后现货单因缺 `tdMode=cash` 被拒（sCode 51000）** |

**挂起的实质**：CEX 侧自 10-05 20:30 起**一笔成交都没有**，且三个阻塞都在账户/参数层，
不在策略层。**继续跑不会产生任何新信息**，所以挂起是当前最优选择，而不是止损。

**恢复方式**：`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagent.runner.plist`
（plist 文件保留，未删除）——但恢复前必须先解决上述 ③。

---

## 24. 合约腿上线规格（2026-10-07，用户确认"合约策略肯定做合约标的"）

用户要求把合约策略部署运行。**规格先写死，再照单执行**，避免边想边改。

### 24.1 原则（用户 2026-10-07 明确）

> **合约策略只做合约标的。**

这条排除一个危险的默认做法：直接把 `perp_trend_funding` 的 `enabled` 翻成 `true`。
它当前的 `symbols = ["tier:majors"]` 指向 **BTC/ETH/SOL/OKB 现货** —— 翻开关
等于"用合约策略跑现货"：没有资金费率、没有做空、没有杠杆，
而且跑的是我们已经测出**为负**的骨架（README 22：-2.88% / -3.73%）。

### 24.2 四处改动（缺一不可）

| # | 文件 | 改什么 | 为什么 |
|---|---|---|---|
| 1 | `src/ta/venues/ccxt_venue.py` | 新增 `market_type="swap"` 分支：市场加载、`ctVal` 换算、`posSide`、出场 `reduceOnly`、`tdMode=cross` | 现在只有现货；`swap_math.py` / `swap_orders.py` 是无引用的独立模块，必须真正接上 |
| 2 | `config/app.toml` | 新增合约 venue 段 + `universe.symbols_by_tier.swap`（**SWAP 合约名**，不是现货名） | 现货与合约必须分开配置；只改 `market_type` 会连带改掉现货腿 |
| 3 | `config/strategies.toml` | `perp_trend_funding.symbols`：`["tier:majors"]` → `["tier:swap"]` | 落实 §24.1 的原则 |
| 4 | `config/risk.toml` | 新增 `[tiers.swap]`（杠杆上限、保证金上限、强平缓冲） | 合约的风险尺度与现货不同，不能沿用现货 tier |

**关键**：`validate()` 要求 ②③④ **同时存在且互相一致**
（"symbols 必须在 universe 里" + "tier 必须在 risk 里有定义"）。
上一轮 TradFi 就是只加了一半被挡下的 —— 所以这四处**必须一次改完再验证，不能分批**。

### 24.3 上线前必须先回答的问题

**先做 funding 回测**（把 `data/history/funding/` 接进 `backtest.py`），再动 ①②④。
理由：若接上真实资金费率后它仍为负，则 ①②④ 全部白做，**直接结案**。
这一步的成本远低于写 venue 集成。

### 24.4 风险与回滚

| 项 | 说明 |
|---|---|
| 改 `ccxt_venue.py` | 触碰 **495 行运行代码**；但新分支只在 `market_type="swap"` 时生效，现货路径不受影响 |
| 部署 | 空仓窗口 + `kickstart -k`；**改配置与重启分两步做**，便于定位 |
| 回滚 | 配置层：`enabled` 改回 `false` + 重启；代码层：本项目非 git 仓库，**改动前先 `cp` 一份 `.bak-<时间戳>`** |
| 杠杆 | 首个版本**不超过 2x**，单笔保证金 ≤ 权益 1% |

---

## 25. 因子有效性裁决：随机对照证明四策略因子劣于掷硬币（2026-10-07）

用户要求"继续调整现货与合约策略、优化因子"。在做第十轮优化之前，先补了这个项目
一直缺的**对照组**——因为"优化因子"这个动作有一个从未被检验的前提：
**这些因子到底有没有方向性信息？**

### 25.1 为什么要做对照

把边际/成本门槛从 1.5× 提到 3×、再到 5×，四个现货策略**全部单调改善**，
但**全部收敛到略低于零**（最好 -0.08%）。这个形状有两种解释，原有数据分不开：

| 解释 | 含义 |
|---|---|
| (a) 因子有信息，只是不够覆盖成本 | 继续优化因子有意义 |
| (b) 因子没有方向性信息 | 优化是徒劳——收敛于零是因为入场≈随机 |

区分 (a)/(b) 的唯一办法：**同样出场、同样区间、入场换成"固定种子的掷硬币"**。
新增 `src/ta/strategies/random_control.py`（确定性、可复现）。

### 25.2 对照结果（同配置、同区间、样本外）

| 策略 | 收益 | 交易数 | 胜率 | **盈亏比** |
|---|---:|---:|---:|---:|
| **随机入场（每 bar 2%）** | **-0.29%** | 312 | **50.5%** | **1.01** |
| **随机入场（每 bar 5%）** | **+0.03%** | 403 | **51.7%** | **1.05** |
| trend_breakout（5×） | -1.20% | 87 | — | 0.42 |
| mean_reversion（5×） | -0.31% | 57 | — | 0.63 |
| new_listing_breakout（5×） | -0.08% | 33 | — | 0.63 |

**随机入场全面优于四个"优化过"的因子策略。**

### 25.3 裁决

> **这些因子不携带正信息，而且是负的。**

随机入场是"零信息"的基准，其结果≈0（PF≈1.0）——**这恰好是"扣费后无边际"的定义**。
真实策略**低于**该基准，说明它们**不是"信息不足"，而是选出了比随机更差的入场**。

机制上可解释：突破 / 回踩 / 抄底三类形态，本质都是**"在价格已经移动之后进场"**，
而在 15m–1h 尺度上短期均值回归对"追已动过的价格"不利——形成**系统性反选**。

### 25.4 这条结论排除了什么、保留了什么

| 方向 | 是否被排除 |
|---|---|
| 基于**价格形态**预测短期方向（四策略 + 合约趋势骨架） | ✅ **被排除**（劣于随机） |
| **资金费率 carry**（不预测方向，赚结构性收益） | ❌ 未被排除（12/13 为正，年化 2.8–5.75%） |
| **链上 / 打新**（不同信息源，非价格形态） | ❌ 未被排除 |

**注意边界**：随机对照交易数（312/403）多于真实策略（33–87），不是严格频率匹配；
但 **PF 1.0 vs 0.42** 的差距过大，不是频率能解释的，且方向是"随机更好"。

### 25.5 与既有结论的一致性（三重独立证据）

1. 180 天样本外四策略全负（-0.54% ~ -1.08%）；
2. 提高边际门槛只能收敛到零、无法穿正（§25.2 上半）；
3. **随机对照证明因子劣于随机**（本节）——把"再优化一下"从选项里拿掉。

**行动**：停止"优化价格形态因子"，把预算转向 §25.4 里未被排除的两条线。

---

## 26. 资金费率 carry 的实盘化路径（2026-10-07，并更正 §24 里我说过的一句话）

### 26.1 更正

§24.3/§25.5 我说过"§24.2 的四处改动不需要做"。**那句话只对趋势策略成立，对 carry 不成立。**

原因很直接：**carry = 现货多 + 永续空**，delta 中性靠的是那条**永续空头腿**。
没有 swap venue，就只能做裸空（那是方向性押注，不是 carry）。所以：

| 结论 | 适用范围 |
|---|---|
| §24.2 的四处改动**不需要做** | ✅ 仅针对**趋势策略**（它被 §25 的随机对照证伪了） |
| venue 仍需要 swap 支持 | ✅ **carry 需要**，但形态比趋势策略简单得多 |

### 26.2 carry 需要的最小 venue 能力（比趋势策略少一半）

| 需要 | 不需要 |
|---|---|
| 加载 SWAP 市场、`ctVal` 换算 | 止损 / 止盈 / 跟踪止损 / 分批离场 |
| 开一张**市价空单**（`tdMode=cross`、`posSide=short`） | 策略层的信号逻辑（carry 没有信号） |
| 平掉这张空单（`reduceOnly`） | 复杂的 `[tiers.swap]` 风控 |
| 读持仓与强平价 | 多标的轮动（初期可以只做 1–2 个） |

**换句话说：`swap_math.py` 和 `swap_orders.py` 这两个已完成的模块，正好就是 carry 需要的全部**——
它们当初是为趋势策略写的，但趋势策略用不上了，**carry 反而正好用得上**。

### 26.3 carry 的已知数字与已知风险

| 项 | 值 |
|---|---|
| 往返成本 | **34 bps**（现货 20 + 永续 10 + 滑点） |
| 盈亏平衡 | **17–57 天**（中位 24 天） |
| 年化（93 天样本，13 币） | **2.8% – 5.75%**，中位 3.5% |
| 正收入标的 | **12 / 13**（INJ 为负） |
| basis 贡献 | 中位 **-0.8 bps**（长期可忽略） |

**三个必须盯住的风险**（都不在历史样本里充分体现）：

1. **资费翻转**：INJ 那行证明资费会转负，持仓过反向期就从收钱变付钱；
2. **空头腿被强平**：价格暴涨时空永续会被强平——**这是回测里看不到的尾部风险**；
3. **单 regime 样本**：93 天都是资费偏正的环境，最坏情况没有被数据覆盖。

### 26.4 下一步

```
1. 给 CcxtVenue 加最小的 swap 支持（只做 market short / reduceOnly close）
2. 写一个 carry 脚本（不接策略层）：选标的 → 开空 → 持有 → 到期平
3. 在模拟盘跑最小额（1 个标的、最小面值），验证：能开空 / 能读到资金费率 / 能平掉 / 强平价可得
4. 若通过，再谈多标的与仓位规模
```

**这条路径与趋势策略的区别**：它不预测方向，所以 **§25 的随机对照否不掉它**——
随机入场证伪的是"用价格形态猜方向"，而 carry 赚的是"持有空头腿收取的资金费率"。

---

## 27. carry 双腿在模拟盘跑通（2026-10-07），顺带挖出真正卡住现货单的 bug

§26.4 的五步全部做完，并且**在真模拟盘上跑了一次完整的两腿往返**（开→读→平）。
过程中发现了一个一直在生效、但一直没有被发现的故障——它同时解释了"为什么主循环从
2026-10-06 之后再没开出过任何现货仓"。

### 27.1 根因：`tdMode` 必须跟随**账户等级**，不是市场类型

2026-10-06 账户切到**跨币种保证金模式**（`acctLv=3`）以便开永续。切完之后，
每一次现货下单都被 OKX 拒掉。2026-10-07 14:12 那次"修复"把 `tdMode` 硬编码成
`cash`——而这个值**正是被拒的那一个**，所以等于没修。当天在真模拟盘上做的参数矩阵：

| 下单参数 | OKX 回应 |
|---|---|
| 不带 tdMode | `51000 Parameter tdMode error` |
| `tdMode=cash` | `51000 Parameter tdMode error` |
| `tdMode=cross` | **通过**（继续做数量校验） |
| `tgtCcy=base` / `quote` | `51000 Parameter tgtCcy error`（只认 `base_ccy` / `quote_ccy`） |

结论：`acctLv<=1`（简单交易）只认 `cash`，`acctLv>=2`（单币种/跨币种/组合保证金）
只认 `cross`。代码现在在 `connect()` 里读一次 `acctLv` 自动选择，
读不到时回退 `cash`（保持简单模式账户的旧行为），并加了 5 条单测覆盖这条分支。

### 27.2 连带陷阱：改 `cross` 会让市价买单的"单位"从币变成 USDT

ccxt 只在 `tdMode=cash` 时才会带上 `tgtCcy`。一旦改成 `cross`，请求里没有 `tgtCcy`，
OKX 就按自己的默认把市价买单的 `sz` 当**计价币**读。实测（2026-10-07）：

```
下单 "买 1.0 LTC"  →  实际成交 0.014814 LTC = 1.00 USDT
```

也就是说"看起来修好了、其实每一单都小了一两个数量级"。修复是在买单上显式带
`tgtCcy=base_ccy`（卖单不带，卖单本来就是币本位）。同一路径复测：
下单 2.962 LTC → 到手 2.96205 LTC（$200.00），量级正确。

### 27.3 carry 两腿端到端实测（LTC，$202 名义）

`scripts/carry.py open → status → close`，四个验证点全部通过：

| 验证点 | 结果 |
|---|---|
| 能开空永续 | ✅ `tdMode=cross` + `posSide=short`，成交 3 张 = 3 LTC |
| 能读资金费率 | ✅ `0.0001`/8h → 空头收 **0.0300%/天** |
| 能平掉（reduceOnly） | ✅ 残余合约 0 |
| 强平价可得 | ✅ `LIQUIDATION_PX = 34049`（杠杆 3x，保证金 $67.5） |

两腿开好后 **DELTA = -0.000633 LTC（-$0.04）**，残差恰好等于现货腿手续费——
delta 中性的精度受限于现货手续费以币扣收，这一点如实写出来而不是四舍五入成 0。

**实测往返成本 44.9 bps**（比 §26 按费率表估的 34 bps 高）：

| 组成 | bps |
|---|---|
| 手续费（现货 10+10，永续 5+5） | 30.0 |
| 价差 + 两条腿之间的时间差 | 14.8 |
| **合计** | **44.9** |

按这个成本重算回本天数：DOGE 20.1 天、SUI 21.7、ADA 22.7、…、TIA 83.8 天。
**§26 写的"中位 24 天"要上修到 30 天左右**——11 bps 的差异是这条交易三分之一的经济性。

### 27.4 交付物与后续

| 项 | 内容 |
|---|---|
| 新增 | `scripts/carry.py`（`plan` / `open` / `status` / `close`，默认 dry-run） |
| 新增 | `scripts/carry_probe.py`（单标的四问探针，最小额、结束即平） |
| 修改 | `src/ta/venues/ccxt_venue.py`（按 `acctLv` 自动选 `tdMode`；买单显式 `tgtCcy`） |
| 看板 | 新增「资金费率中性（carry）」面板：实测往返成本、各标的 bps/天、年化、回本天数 |
| 测试 | **163 全绿**（新增 5 条：cross/cash 分支、买单单位、卖单不带 tgtCcy、探测失败回退） |
| 验证代价 | 账户权益 103029.83 → 103027.58，**-$2.25**（探针 + 一次误操作的合计成本） |
| 待办 | ⚠️ **修复要重启 runner 才生效**；看板新面板同样要重启 dashboard 才显示 |

### 27.5 闭环确认：真实下单被接受（2026-10-07 20:42）

14:12 修完、又重启了 runner 之后，主循环连续 161 个 tick 一次下单尝试都没有——
既没成功也没被拒，因为就是没有信号。这条修复因此一直是"看起来对"。
用户授权后做了一次**真实最小额下单**（`scripts/order_path_check.py`），
用的是**生产配置**（`config/` + `okx_demo`，与运行中的 runner 完全一致）：

| 检查项 | 结果 |
|---|---|
| acctLv → tdMode | `acctLv=3` → 解析出 **`cross`**（14:12 写死的 `cash` 正是被拒的值） |
| 订单是否被接受 | ✅ 无 `51000 Parameter tdMode error` |
| 数量单位 | ✅ **1.0000x**——下单 0.197578 SOL，成交 0.197578 SOL（tgtCcy 陷阱仍在的话只会剩 ~0.0002 SOL） |
| 成交价 / 费用 | 116.41 买、116.40 卖，手续费各 0.0230 USDT，往返 **$0.046** |
| 是否复原 | ✅ 余额回到探针前的 9.03e-07 SOL 灰尘，仓位归零 |
| 副作用 | ✅ 账本 fill 计数 56→56 未变（探针**不写生产账本**）、无新 alerts、权益 103,027.58 |

**这一段能证明什么、不能证明什么**：能证明"信号一旦出现，订单能真正到达 OKX 并以正确单位成交"；
不能证明"策略会产生信号"——那只能等自然入场。至此 §27.1 的 bug 正式闭环。

---

## 30. 主流现货策略切日线：成本塌下来了，收益没有（2026-10-07）

用户要求：主流货币的现货策略按日线跑，压缩换手率，看能否降成本同时提高收益。
**结论先说**：**成本按天算降了约 60 倍，亏损速度降了约 50 倍，但没有变成正收益；
而且样本量小到不能下任何结论。** 详细数字在 30.4。

### 30.1 先说两个挡路的数据/工具 bug（都已修）

**(a) 深度不足的币会被回填成"最老的一段"而不是最近的。** 请求 1500 根日线，而 SUI 只有约
590 天历史时，OKX 返回的是它**最老**的那一窗；`fetch_symbol` 里"chunk 不满就认为没有更多历史"
的保护让分页立刻停下——于是落盘的文件看起来完全正常，却停在错误的年代：

| 币 | 修前 | 修后 |
|---|---|---|
| SUI / PEPE | 51 根，停在 **2023-06-23** | 599 根，到 **2026-10-06** |
| TIA | 12 根，停在 **2025-07-13** | 462 根，到今天 |
| BTC 等周线 | **882 天**（同样被截断） | **1596 天** |

后果不是"缺数据"，是"有数据但是错的"——日线回测直接报 `symbols share only 11 1d timestamps`。
修法：分页结束后若最新一根离现在超过 3 根，就丢弃并从**现在往回**重新翻页。
新增 2 条回归测试（模拟 OKX"since 早于上市日就返回最老一页"的行为）。

**(b) `--split` 在混合历史长度的标的池上直接崩。** 切点索引是按 `symbols[0]` 的 K 线数算的，
所以只要池子里有一个上市晚的币（TIA/SUI/PEPE），它的 in-sample 为空，回放要求的
"所有标的共享时间戳"交集就塌成十几根。修法：in-sample 只保留切点前至少有 20 根的标的，
并在日志里写明保留了几个；out-of-sample 仍然看到全部标的。in-sample 本来就只是"看形状"。

### 30.2 实验怎么做的

| 版本 | 内容 |
|---|---|
| `config-daily` | 只把三个主流策略的时间框架换成 `1d` + 确认帧 `1w`（朴素照搬） |
| `config-daily2` | 在 daily 基础上，把**按小时标定**的止损上限按实测日线 ATR 换算 |
| `config-daily3` | 在 daily2 基础上关掉"回踩确认"与 regime 过滤器，用来定位 0 笔的原因 |

换算依据是**实测**而不是拍脑袋：中位 ATR% —— 1 小时：主流 0.87 / 山寨 0.96；
日线：主流 **5.20** / 山寨 **6.84**。原来的止损上限 3.0%/4.0% 是"约 3–4 倍小时 ATR"，
按同一倍数换算到日线即 16% / 21%（`config-daily2` 用的是这两个数）。
生产目录 `config/` 未改动。

### 30.3 关键发现：日线上"回踩确认"会把主策略过滤成 0 笔

朴素日线版（daily2）的两个趋势策略**一笔都没开**，而且 `blocked_orders=0`——
说明不是风控拦的，是策略自己没提案。关掉回踩确认（`retest_window_bars 15→0`）后
立刻出现 13 笔。原因很直白：`retest_window_bars=15` 在 1h 上是"15 小时≈1 天"，
搬到日线上就变成"突破后 15 天内必须回踩确认"，条件苛刻到几乎不成立。
**这印证了一条方法结论：周期不能只换时间框架，按"根数"计数的窗口必须一起换算。**

### 30.4 三个版本的样本外数字

| 版本 | 策略 | 样本外 | 笔数 | 手续费 | 每天费用 |
|---|---|---|---|---|---|
| 现状 1h/15m（53 天 OOS） | trend_breakout | **-1.91%** | 125 | $35.53 | **$0.67** |
| 现状 1h/15m | trend_pullback | -0.91% | 113 | — | — |
| 现状 15m | mean_reversion | -2.07% | 227 | — | — |
| 日线 D1/D2（138 天 OOS） | trend_breakout | **0 笔** | 0 | $0.00 | $0.00 |
| 日线 D1/D2 | trend_pullback | **0 笔** | 0 | $0.00 | $0.00 |
| 日线 D1/D2 | mean_reversion（仅主流） | +0.01% | 16 | $0.75 | $0.005 |
| 日线 D3（关回踩/关regime） | trend_breakout | -0.09% | 13 | $1.47 | $0.011 |
| 日线 D3 | trend_pullback | +0.05% | 3 | $0.64 | $0.005 |

**答案**：

1. **成本确实塌了**：trend_breakout 从每天 $0.67 降到每天 $0.011（约 **1/60**），
   换手从 2.36 笔/天降到 0.094 笔/天（约 **1/25**）。这一条用户的判断是对的。
2. **亏损速度也跟着塌**：从 -1.91%/53 天（约 -12%/年）降到 -0.09%/138 天（约 -0.24%/年），
   约 **1/50**。这说明**之前的亏损主要就是被换手吃掉的**，和 §25 的结论一致。
3. **但收益没有变成正的**：最好的两档是 +0.05%（3 笔）和 +0.01%（16 笔）——
   样本量比 §26 定的 20 条底线还小，**连"不亏"都不能声称**。
4. 因此当前**不切日线**：不是因为它更差，而是因为它还没被证明更好，而且日线的入场条件
   需要按日线重设（回踩窗口、regime 周期、突破回看），那是下一轮的工作。

### 30.5 留档

`config-daily` / `config-daily2` / `config-daily3` 三个实验目录保留，方便复跑；
生产 `config/` 一行未改。新增数据：20 个标的的 `1d`（最多 1500 天）与 `1w`（最多 1596 天）。

---

## 31. 日线重标定：全部变体转正，但**没有一个在两个子区间都为正**（2026-10-07）

§30 的结论是"成本塌了但没转正"。本轮做的是它该有的下一步：**按日线重设入场条件**，
而不是照搬 1h 的参数。做法与结果如下。

### 31.1 重标定的原则：指标周期留在"根"，时间窗口换成"天"

| 类别 | 处理 | 理由 |
|---|---|---|
| EMA20/100、RSI14、ATR14 | **保持根数不变** | 这些是通用指标周期，在任何周期上都这么用 |
| `retest_window_bars` | 15 根（=15 小时）→ **1 天** | 它编码的是时间：突破后等多久回踩 |
| `max_holding_bars` | 64 根（15m，=16 小时）→ **0/2/8 天** | 同样是时间止损 |
| `confirm_timeframe` | 4h/1h → 1w **或直接取消** | 4h 的 EMA50 = 8 天；1w 的 EMA50 = **350 天**，不是同一个过滤器 |

标的池也从 15 个缩到有完整历史的 13 个（含 INJ/ONDO/W，750 天）与 10 个（1500 天），
因为把 TIA（462 天）留在池子里会把所有人的共同窗口拖到 461 天。

### 31.2 全网格结果：**每一个变体都是正的**

10 个标的、1500 根日线、样本外 449 天：

| 策略 | 变体 | 笔数 | 收益 | PF | 胜率 |
|---|---|---|---|---|---|
| trend_breakout | 回踩关 + 无周线确认 | 10 | **+1.70%** | 128.7 | 66.7% |
| trend_breakout | 回踩关 + 周线确认 | 6 | +0.23% | 75.2 | 50.0% |
| trend_breakout | 回踩 1 天 / 3 天 | **0** | 0.00% | — | — |
| trend_pullback | 持有 0/2/8 天 + 周线确认 | 12–13 | +0.33 ~ +0.45% | 12–16 | 57–63% |
| trend_pullback | 持有 0/2/8 天 + 无确认 | 19–20 | +0.26 ~ +0.38% | 2.4–2.9 | 55–58% |
| mean_reversion | 周线确认 | 25 | **+0.82%** | 3.50 | 66.7% |
| mean_reversion | 无确认 | 27 | +0.74% | 2.81 | 62.5% |

对照 §30 的 1h/15m（全负）与朴素日线（0 笔），这个网格看上去是"找到了"。
**但先别下结论，下面这一节才是重点。**

### 31.3 决定性检验：把时段固定，正收益立刻分家

把同一批 10 个标的的切点从 70%（449 天）挪到**近 224 天**，只改时段、不改标的：

| 策略 | 全窗口 449 天 | 同一批标的的近 224 天 | 读法 |
|---|---|---|---|
| trend_breakout（回踩关/无确认） | 10 笔 +1.70% | **10 笔 +1.70%** | 全部交易都在近 224 天，前 225 天**一笔没有** |
| trend_pullback（周线确认） | 13 笔 +0.45% | **0 笔** | 全部交易都在前 225 天，近 224 天**完全停摆** |
| trend_pullback（无确认） | 20 笔 +0.38% | 4 笔 **-0.18%** | 近 224 天是亏的 |
| mean_reversion（周线确认） | 25 笔 +0.82% | **0 笔** | 同上，全在前半段 |
| mean_reversion（无确认） | 27 笔 +0.74% | 2 笔 **-0.08%** | 近 224 天是亏的 |

**没有一个策略在两个子区间里都为正。** trend_breakout 是"只在近期有交易且赚钱"，
另两个是"只在早期有交易且赚钱、近期停摆或小亏"。

### 31.4 结论与建议

1. **日线重标定确实把成本结构翻了过来**：日线 ATR 约 5.2%，往返成本约 0.3%，比值 **~17×**；
   1 小时只有 0.87% / 0.3% = **~2.9×**。这也是为什么日线上"看起来能赚"——不是找对了方向，
   而是**成本不再吃掉全部波动**。
2. **但正收益经不起时段检验**：所谓正收益全部来自单一半段。按项目一贯的规矩
   （§25 随机对照、§26 样本量底线），这**不能作为上线依据**。
3. **PF 128 / 75 这类数字没有意义**：10 笔里的一个赢家就能造出 PF 三位数。
   真正该看的笔数是 0–27 笔，远低于能下结论的量级。
4. **不建议把生产配置切到日线。** 下一步若要做，价值不在继续回测（回测已经给不出更多信息），
   而在于**把日线版放到模拟盘上跑，用真实成交收集样本**——那才是这段样本之外的新证据。

### 31.5 留档

新增 `scripts/daily_study.py`（网格 + 时段对照，一次跑完，报告所有变体而不是最优变体）。

---

## 32. 标的池到底几个？——以及 §31 的池子错误已修正（2026-10-07）

用户指出两件事：①"现货标的应该是**所有**标的，不是我挑的十几个"；②"我看标的池里远不止 20 个"。
两件都成立，分别说明。

### 32.1 两个不同的"标的数"，都不是统计错误

| 口径 | 数量 | 来源 |
|---|---|---|
| **交易池**（runner 实际会下单的） | **20** | `config/app.toml` 的 `[universe.symbols_by_tier]`：majors 4 + alts 11 + new 5 |
| **数据池**（`data/history/okx_demo/` 里躺着多少标的） | **124** | 其中 **105 个是 TradFi 代币化美股**（XAAOI/XAAPL… 以及带 X 尾缀的 AAPLX/AMZNX/TSLAX），来自 §23 那次研究；加密 19 个 + XRP = 20 |
| `config-tradfi/` 那份配置的池子 | **27** | 在 20 个之外额外定义了 7 个 `tradfi` 标的 |

已核对三个常驻服务（runner / watcher / dashboard）的 launchd 参数，**全部指向 `config`**，
也就是 20 个那条。所以"远不止 20 个"看到的是**数据目录或 TradFi 研究池**，不是交易池——
但这恰恰说明我上一节的做法有问题（见下）。

### 32.2 §31 的池子错误：我把交易池砍成了"有足够历史的那些币"

§31 为了让共同回放窗口长一点，把池子缩到 10/13 个币。这是**改了问题本身**：
回放窗口是"全池时间戳的交集"，真正的约束来自上市最晚的 TIA（462 根日线），
把 TIA 踢掉等于人为延长时间轴、把样本做大。**这是错的，已改。**

同时发现 `scripts/daily_study.py` 还有一个更隐蔽的错误：它把"整个池子"当成策略的标的列表传进去，
于是 **mean_reversion 被拿去交易山寨币**（它配置里只交易主流 4 个）。两处都已修正：
现在用 `config.traded_symbols`（全池 20）建回放窗口，用 `config.resolve_symbols(spec)`
保留每个策略**自己的**标的范围。

### 32.3 全池（20 个）重跑的结果：只剩一个策略有交易

回放窗口 = 全池交集 = **461 根日线**（被 TIA 限定），70% 切分 → 样本外 **138 天**：

| 策略 | 变体 | 笔数 | 收益 | PF | 胜率 | 手续费 | 敞口 |
|---|---|---|---|---|---|---|---|
| trend_breakout | 回踩关 + 无周线确认 | 13 | **+1.40%** | 2.97 | 57.1% | $3.20 | 42.4% |
| trend_breakout | 回踩关 + 周线确认 | 9 | +0.26% | 75.8 | 50.0% | $0.96 | 28.6% |
| trend_breakout | 回踩 1 天 / 3 天 | **0** | — | — | — | — | — |
| trend_pullback | 持有 0/2/8 天 + 周线确认 | **0** | — | — | — | — | — |
| trend_pullback | 持有 0/2/8 天 + 无确认 | 4 | **-0.15 ~ -0.18%** | 0.00 | 0% | $1.28 | — |
| mean_reversion | 周线确认 | **0** | — | — | — | — | — |
| mean_reversion | 无确认 | 6 | **-0.15%** | 0.01 | 33.3% | $0.98 | — |

把切点推到 85%（样本外 69 天）做时段对照：trend_breakout 仍是 **13 笔 +1.40%** ——
说明它的**全部交易都发生在最近 69 天**，更早 369 天一笔没有；trend_pullback 仍全为 0；
mean_reversion 0 / 4 笔 -0.12%。

### 32.4 结论

在全池口径下，三个主流策略里**只有 trend_breakout 有交易（13 笔）且为正**，
另外两个是 0 笔或小亏；而 trend_breakout 的正收益又完全来自最后 69 天。
**13 笔、单一时段，仍然不足以支持"日线更好"这个结论**，§31.4 的判断不变：不切生产。

区别在于：这次的口径是对的——**全池、策略各守自己的标的范围**。

---

## 33. 四类标的的完整盘点：交易所 2,416 个 vs 我们分析 20 个（2026-10-07）

用户指出：标的池分**现货 / 合约 / TradFi / 期权**四类，每一类里的标的都远不止我给的数字，
全样本分析应该四类都纳入。这个判断是对的。下面是把三个口径并排摆出来的硬数字
（`scripts/universe_inventory.py`，对模拟盘公开接口逐类清点，带 `x-simulated-trading` 头）。

### 33.1 三类口径并排

| 类别 | 交易所 live | 其中 USDT 线 | 我们盘上有历史 | 配置纳入 |
|---|---|---|---|---|
| SPOT 现货 | 567 | **272** | 42（20 加密 + 22 TradFi） | **20** |
| SWAP 永续 | 191 | **155** | **0** | **0** |
| FUTURES 交割 | 126 | 12（USDT 结算） | 0 | 0 |
| OPTION 期权 | **1,532**（BTC 662 / ETH 784 / SOL 86） | —（币本位） | 0 | 0 |
| **合计** | **2,416** | | | |

另外两套容易混淆的数字：

- `data/history/okx_demo/` 里有 **124 个标的**，其中 **102 个是 TradFi**（只有日线）
  ——这是 §23 那次研究的产物，不是交易池；
- `config-tradfi/` 那份配置另外定义 7 个 `tradfi` 标的（池子 27）；
- 三个常驻服务的 launchd 参数**全部指向 `config`**，即 20 个那条。

### 33.2 要真正"四类全样本"，每类各缺什么

| 类别 | 差距 | 性质 |
|---|---|---|
| 现货 272 | 缺 230 个标的的历史（现只有 20 个在交易池、42 个有数据） | **纯工作量**：可自动化补齐，估计 30–60 分钟网络抓取 |
| 永续 155 | 缺全部 155 个的历史 | 工作量 + **策略**：venue 代码今天刚补上，但没有任何永续策略通过验证（唯一验证过的是 carry，且是脚本不走风控） |
| 交割 12 | 缺全部历史 | **代码缺失**：venue 层目前只支持 spot/swap，不支持交割合约 |
| 期权 1,532 | 缺全部历史 | **本质限制**：期权**无法用 K 线回测**——需要 IV 曲面 + 希腊字母 + 到期结构，那是一套独立项目，不是"多拉点数据"能解决的 |
| TradFi 102 | 有日线（仅 30–84 根/标的） | **时间不够**：§23 已结论，要到 ~2027-02 才有 200 根以上可用 |

### 33.3 结论

**"四类全样本"里有两类现在做不了，而且是不同原因**：期权是方法论上做不了（K 线不够用），
交割合约是代码没写。真正能立刻做的是**现货 272 个 + 永续 155 个**的历史补齐——
这一步能把"我们分析的标的数"从 20 提到 400+，也是把 §30/§31/§32 的日线结论
从 20 个标的扩到全样本的唯一途径。

**但这会实质性改变数据资产规模**（预计新增 300+ 标的 × 多周期），
且抓取耗时以小时计，所以先确认范围再动手：是否只抓**有流动性**的（配置里 `min_quote_volume_24h = 5M`）、
抓哪些周期（建议 1h + 4h + 1d + 1w，15m 对日线研究无用且最占空间）。

---

## 34. 四腿分仓 + 全样板数据摸底 + 实施路径（2026-10-07，用户指定）

用户指定新的分仓结构，并要求三条"样本不够/策略没跑通"的腿**承风险先跑数据**：

| 腿 | 占比 | 现在的状态 |
|---|---|---|
| 现货 + 永续 | **50%**（现货 30 / 永续 20） | 现货 3 策略在跑（§25 已证伪，保留以收集真实费用）；永续**没有任何策略通过验证** |
| TradFi | **30%** | 有 101 只日线，但**样本短到跨品种无法对齐**（见 34.2） |
| 期权 | **20%** | 无 venue 代码、无策略、无历史；**K 线回测对它不适用**（§33） |

### 34.1 分仓已落地（代码 + 配置 + 看板）

风控从"两条腿"扩成**四条腿**，每条腿独立限额，互不挤占：

| 腿 | 总敞口 | 最多持仓 | heat | 每日开仓 | 识别方式 |
|---|---|---|---|---|---|
| spot | 30% | 3 | 1.5% | 6 | 无 `:`、非期权形状 |
| derivatives | 20% | 2 | 1.0% | 3 | 带 `:USDT` 结算后缀 |
| tradfi | 30% | 4 | 1.0% | 3 | `symbol_prefix = "X"` + 3 个 X 尾缀显式列出 |
| options | 20% | 3 | 0.5% | 2 | instId 形状（`BTC-USD-261008-80000-C`） |
| **账户级（=四腿之和）** | **100%** | **12** | **4.0%** | **14** | 校验规则会拒绝"四腿之和超账户级" |

**实现时踩到并修掉的两个坑**（都写进注释了）：
1. **XRP 差点被当成 TradFi**：`symbol_prefix = "X"` 会吞掉 XRP/USDT。所以 XRP 被显式钉在
   spot 腿（成员判断先于前缀）。实测：101 只 TradFi 里混进 XRP 之后，
   整个池子的日线交集直接变成 **0** —— 因为加密日线开在 00:00 UTC、TradFi 开在 **16:00 UTC**，
   两套网格永远对不上。这条坑不是假设，是实测踩到的。
2. **X 尾缀的 TradFi**：模拟盘里有 3 只用尾缀命名（`AAPLX/AMZNX/TSLAX`），前缀规则抓不到，
   已在配置里显式列出。

### 34.2 TradFi 全样板摸底：不是"样本少"，是"跨品种无法对齐"

对 101 只 TradFi 做全池日线回测（`trend_breakout_daily`，0.7 切分）：

| 口径 | 结果 |
|---|---|
| 每只样本量 | 30–84 根，中位 **56**；≥100 根的只有 **1** 只 |
| 101 只的**共同窗口** | **1 根日线**（`bars=1`，0 笔交易） |
| 7 只（config-tradfi 现有池）的共同窗口 | 49 根，5 笔，-0.07%，PF 0.51 |

**结论：TradFi 当前卡住的不是"策略不行"，而是 `_normalise` 要求"所有标的共享完全相同的时间戳"。**
101 只上市日期各不相同（最早 2022-08、最晚 2026-09-07），交集就只剩 1 天。
要让这条腿真的跑起来，需要把回放改成**"并集时间轴 + 按标的可用性交易"**：
某只标的够 warmup 就参与，不够就跳过——而不是要求全池从同一天开始。
这是 34.4 路径里的第 1 步，也是唯一能立刻解锁 30% TradFi 额度的改动。

### 34.3 三条"没跑通"的腿分别卡在哪

| 腿 | 卡点 | 性质 | 解法 |
|---|---|---|---|
| 永续 20% | 没有任何永续策略通过验证（`perp_trend_funding` 计入 funding 后仍为负）；唯一验证过的是 carry，但它是脚本不走风控 | 策略 | 把 carry 提升为**受风控管理的策略**（它有实测正收益：13/13 标的资金费率为正、往返 44.9bps、回本中位 ~30 天） |
| TradFi 30% | 回放要求全池同一时间轴；101 只交集=1 根 | 引擎 | 改并集时间轴（34.2） |
| 期权 20% | 无 venue 适配、无策略、**且 K 线回测对期权不适用** | 数据模型 | 需要 IV 曲面 + 希腊字母 + 到期结构；OKX 期权蜡烛要按 uly/strike/expiry 逐条抓（BTC 一家 662 个合约） |

### 34.4 实施路径（按解锁难度排序）

| # | 步骤 | 解锁 | 工作量 |
|---|---|---|---|
| 1 | 回放改**并集时间轴 + 按标的可用性** | TradFi 30% 可回测 | 中（`_normalise` + 回放循环） |
| 2 | 把 **carry 提升为风控管辖的永续策略** | 永续 20% 有第一个候选 | 中（已有 `swap_math`/`swap_orders`/`funding` 三个模块） |
| 3 | 补齐现货 272 / 永续 155 的历史 | 全样本口径（§33） | 纯抓取，1–2 小时 |
| 4 | 期权模块：先只做**数据层**（按 uly 抓期权链 + 反推 IV 曲面） | 期权 20% 有数据可谈 | 大 |
| 5 | 期权策略：仅做**买权利金**的有限风险结构 | 期权 20% 可交易 | 大，且需 §20.3 的成本论证 |

**注意**：步骤 1、2 是"把我们已有的东西接上"，3 是"补数据"，4、5 是真正的新建。
按用户"承风险先跑数据"的要求，1 和 2 应当先做——它们让 30%+20% 的额度**今天就有东西占用**。

---

## 35. 实施路径第 1 步完成：回放改成"并集时间轴 + 按标的可用性"（2026-10-07）

§34.2 定位到 TradFi 卡在引擎：`_normalise` 要求"所有标的共享完全相同的时间戳"，
于是 101 只上市日期从 2022-08 跨到 2026-09 的池子，交集只剩 1 天。本次把它改成**并集**。

### 35.1 改了什么

| 位置 | 改前 | 改后 |
|---|---|---|
| `_normalise` | 主时钟 = 所有标的日线的**交集**；每个标的按主时钟切片对齐 | 主时钟 = **并集**；每个标的保留自己的完整序列 |
| `_ReplayFeed` | base 帧的"已收盘根数"写死为 `range(1, base_len+1)`，隐含"第 k 根 = 主时钟第 k 个点" | 对每个标的、每个周期，按**它自己的时间戳**与主时钟逐点比较，算出"此刻已收盘几根" |
| 回放主循环 | 5 处 `series[symbol][base_tf][index]` 直取（时钟取 `symbols[0]`） | 时钟取主时钟戳；取标的行情统一走 `feed.bars(symbol)`，**没数据就跳过该标的这一拍** |

**语义变化**：一个上市晚的标的，在它上市前的那几拍"没有数据"，策略会像"没暖机"一样跳过它——
而不是让整个池子等它、或者把它踢出去。这正是"按标的可用性交易"。

### 35.2 效果：101 只 TradFi 从"跑不了"变成"能跑"

| | 改前 | 改后 |
|---|---|---|
| 101 只 TradFi 全池 | **共同窗口 1 根**，0 笔（`bars=1`） | 样本外 **49 根**，5 笔，21 笔被风控拦 |
| 结果 | 无法回测 | -0.00%，PF 0.51，胜率 50%，敞口 100% |

注意样本外仍只有 5 笔——**解锁的是"能跑"，不是"能下结论"**。这与用户"承风险先跑数据"的
要求一致：先让这条腿动起来，用真实成交积累样本。

同时**所有既有结论不受影响**：177 个测试全绿（合成数据的网格本来就一致，并集=交集），
§30–§32 的日线结论用的是同一批标的、同一套数据，只是那时候被交集限制在 461 天。

---

## 36. 实施路径第 2 步（部分完成）：carry 的执行器已验收，筛选器**被自己否掉**（2026-10-07）

第 2 步的原文是"把 carry 提升为受风控管辖的永续策略"。做下来分成两半：
**执行器那一半已经验收**（§27.3），**筛选器那一半产出了不可信的数字，因此没有接线**。

### 36.1 新增：`scripts/carry_live.py`（全合约实时筛选）

扫描模拟盘**全部 150 个 USDT 永续**，读每个合约的资金费率、按实测往返成本 44.9bps 算回本天数。
跑了两轮，两轮都暴露了真问题：

| 轮次 | 做法 | 结果 | 问题 |
|---|---|---|---|
| 第 1 轮 | 用**当前瞬时费率**年化 | AAVE 显示年化 **1642%** | 瞬时值顶在费率上限，不可年化 |
| 第 2 轮 | 改用**近 30 天均值** | LINEA 均值 0.015（正好是上限），年化 13140% | 硬编码"每天 3 次结算"对 1 小时结算合约错了 8 倍 |
| 第 3 轮 | 从历史时间戳**自动推断结算间隔** | 发现 OKX 结算间隔**不统一**：BTC 8h、部分新合约 **1h**、另有 4h | 数字更离谱，说明问题在数据语义 |
| 第 4 轮 | 加两道**可信度闸门**（均值 <0.5%/次、结算间隔 ≥4h） | 仍有 82 个"通过"的合约，最高年化 **415%** | 真实永续的合理量级是 5–50%，**这个量级本身就不对** |

### 36.2 结论：**筛选器的输出不可信，因此不接线**

两个待查的根因（都写进脚本注释和闸门里）：

1. **模拟盘的资金费率可能不代表真实市场**：demo 盘没有套利资金把费率压回合理区间，
   而真实永续的费率会被套利迅速抹平。这是最可能的解释，也意味着**基于模拟盘资金费率的
   carry 决策在搬到实盘前没有参考价值**。
2. **1h / 4h 结算合约的历史语义未验证**：同一接口下 BTC 是 8 小时一条，LINEA/BARD 是逐小时一条，
   且这些合约的均值恰好等于**费率上限**（0.015），更像是"顶格"而不是"平均"。

脚本因此加了两道闸门：均值 ≥0.5%/次结算的**只展示不建议**；结算间隔 <4h 的**不进入候选**。
当前它会明确打印"没有通过可信度闸门的候选——不给出开仓建议"。

### 36.3 第 2 步真正缺的是什么

即使在数据可信之后，carry 也**还不能作为一个策略接进 runner**，原因是结构性的：

| 缺口 | 说明 |
|---|---|
| runner 是**单 venue** 的 | carry 需要同时持有**现货多头 + 永续空头**，而 runner 只连一个 venue（`okx_demo` spot） |
| 策略接口是**现货形状**的 | `Signal(side="buy")` 表达"做多"。只有永续空头腿 = **裸空**，而 §26 明确写过：那不是 carry，是方向性押注 |
| 风控的 derivatives 腿 | 已经就位（§34），但它管的是**名义敞口**，配对执行需要 runner 先支持"一个信号 → 两条腿两个 venue" |

**所以第 2 步的准确状态是**：执行器（开/查/平）已在模拟盘验收；筛选器已写但**被自己的数据否掉**；
配对执行（一个信号驱动两个 venue）**尚未实现**——这是下一个具体动作，而不是"再调调参数"。

### 36.4 问题已解决：模拟盘费率不可信，实盘公开费率可信

§36.2 留下两个待查根因，已用**同一批合约的实盘 / 模拟盘对照**判定：

| 合约 | 实盘均值（/8h） | 模拟盘均值（/8h） | 实盘年化 | 判定 |
|---|---|---|---|---|
| BTC | **+0.000046** | **-0.000643** | **5.0%** | 模拟盘连**符号都是反的** |
| ETH | +0.000042 | +0.000173 | 4.6% | 模拟盘高 4 倍 |
| SOL | +0.000026 | +0.000100 | 2.8% | 模拟盘高 4 倍 |
| DOGE | +0.000073 | -0.000122 | 8.0% | 符号相反 |
| BARD | +0.000050 | **-0.001952** | 5.4% | 模拟盘是实盘的 **39 倍**且符号相反 |

**结论：实盘资金费率完全正常（年化 2.8–8.0%，与 §26 的 2.8–5.75% 一致）；模拟盘的费率是坏的。**
这也解释了 §36.1 那些"0.015 恰好等于上限"的怪值——不是真实的持续费率，是模拟盘数据本身的问题。

因此**筛选器改为读实盘公开接口**（不需要 API key），**只有执行仍留在模拟盘**：

```
python3 scripts/carry_live.py --days 30 --funding-source live --top 12
```

改完立刻正常了（139 个合约、117 个正费率 = 84%）：

| 合约 | 结算间隔 | 均值/8h | bps/天 | 年化 | 回本 |
|---|---|---|---|---|---|
| NG | 8h | 0.000655 | 19.66 | **71.8%** | **2.3 天** |
| GPRO | 8h | 0.000459 | 13.76 | 50.2% | 3.3 天 |
| TRIA | 4h | 0.000228 | 13.68 | 50.0% | 3.3 天 |
| WLFI | 4h | 0.000143 | 8.59 | 31.3% | 5.2 天 |
| NES | 4h | 0.000140 | 8.40 | 30.7% | 5.3 天 |

**这才是真实永续的量级**（10–70% 年化），全部通过两道可信度闸门。
含义：carry 的**决策数据源**问题已解决，剩下的是**执行结构**（配对两腿）。
高费率本身也说明那个合约正处在单边行情里——这正是空头腿会被急涨强平的场景，风险提示不变。

---

## 37. 配对执行与 carry 策略本体建成，只剩 runner 接线（2026-10-07）

§36.3 说第 2 步缺的是"配对执行"。本轮把它连同 carry 的决策逻辑一起建完了，
**三件套齐备且各自有测试**：

| 组件 | 文件 | 职责 | 测试 |
|---|---|---|---|
| 筛选器 | `scripts/carry_live.py` | 用**实盘**公开费率扫全部永续，两道可信度闸门 | 4 轮实测迭代 |
| 执行器 | `src/ta/execution/paired.py` | 现货多 + 永续空，两条腿一个 pair_id | **6 条** |
| 策略 | `src/ta/strategies/carry_funding.py` | 决定"值不值得 carry"，只提案现货腿 | **5 条** |

### 37.1 执行器的四条不变量（每一条都是测试）

carry 的失败方式很具体，所以模块把安全性质写成了不变量而不是注释：

1. **两条腿同量**：永续空的量 = 现货**实际成交**量（不是请求量），所以部分是部分成交也能对齐；
2. **现货腿没成交 → 绝不发空头**：没有多头就没有空头，一次都不行；
3. **永续腿失败 → 立刻平掉现货腿**：这是最危险的一半状态（裸多），必须马上反向冲掉；
4. **平仓用 `reduce_only`**：止损式平仓**不可能**把 pair 翻成新的多头。

另外平仓顺序是**先永续后现货**：如果买回永续失败，多头仍在、组合仍近似中性；
先卖现货则会留下裸空，是两种半状态里更危险的那个。

### 37.2 策略为什么只提案现货腿

`CarryFunding` 只提案 `<base>/USDT` 的买入，**不提案空头**。因为策略接口是现货形状的，
而"只有空头腿"就是 §26 明确否定过的裸空押注——那不是 carry。空头腿由 runner 通过
`execution/paired.py` 配对发出（一个信号 → 两条腿）。

它的失败方向也是刻意选的：**没有资金费率数据就什么都不提案**（fail-closed）。
`perp_trend_funding` 的"没有费率数据就让闸门开着"对**闸门**是对的，对**carry**恰恰是错的——
因为 carry 的收益本身就是费率。

开仓判据就是一行：`日费率 × 预期持有天数 - 44.9bps 往返成本 ≥ 20bps 边际`。

### 37.3 现在唯一剩下的一步

**runner 接线**：识别 `tag="carry"` 的信号 → 调 `PairedExecutor.open_pair`；出场信号 → `close_pair`；
并在配置里加 hedge venue（第二个 venue）。

`config/strategies.toml` 里 `carry_funding` **已注册但 `enabled = false`**，注释写明了原因：
在 runner 能同时发两条腿之前打开它，只会开出一条**裸多**——那和当初把 `tdMode` 接成
OKX 拒收的 `cash` 是同一类错误（配置看起来接上了，实际语义是错的）。

---

## 38. 第 2 步完成：carry 已作为受风控管理的策略上线（2026-10-07）

§37.3 剩下的 runner 接线，本轮做完并**已在运行中生效**（0 条降级警告，策略列表含 `carry_funding`）。

### 38.1 补完的最后一环：实盘 feed 的资金费率源

接线时发现一个会让整件事"看起来接好、实际空转"的缺口：**只有回测 feed 有 `funding_rate`，
实盘 feed 没有**。于是 `CarryFunding` 永远 fail-closed、永远不提案——配置全对、一笔不出。
补了两处：

| 位置 | 改动 |
|---|---|
| `CcxtVenue.fetch_funding_rate` | 现货市场直接返回 None（现货没有资金费率）；合约则读 `fetch_funding_rate`，取不到就返回 None 而不是抛进循环 |
| `LiveFeed.funding_rate` | 逐 tick 缓存地转调 venue；**拿不到就返回 None，不返回 0.0**——0.0 读作"费率恰好中性"，那是一个陈述而不是缺口 |

### 38.2 接线里暴露并修掉的两个真 bug

1. **对冲腿构造位置错误**：我一开始把 hedge venue 建在 primary venue 旁边，而 `ledger` 在那之后才创建
   → `UnboundLocalError: cannot access local variable 'ledger'`。**第一次重启时安全网救了场**：
   循环照常启动、只把 carry 标记为禁用，日志里留下一条 warning。这正是 §38.3 那条设计要保护的行为。
2. **对冲腿建不起来会拖垮整个循环**：修法是把构造挪到 ledger 之后，并用 try/except 包起来——
   "没有 swap venue"是正常状态（没密钥、venue 挂了、名字写错），后果不是崩溃，而是
   **carry 本轮禁用、现货策略照跑**。集成测试正是靠这条才从 8 个失败回到全绿。

### 38.3 三处 fail-safe（每一处都决定了"错误时会发生什么"）

| 情形 | 行为 |
|---|---|
| 没配 hedge venue | carry 信号被**拒绝**，不会开出一条裸多 |
| hedge venue 建不起来 | warning + 本轮禁用 carry，现货策略不受影响 |
| 永续腿没成交（现货已成交） | **立刻平掉现货腿**，然后报错（`paired.py` 不变量 3） |

### 38.4 配置现状与当前状态

| 项 | 值 |
|---|---|
| `[app] hedge_venue` | `okx_demo_swap` |
| `[venues.okx_demo_swap]` | 已加进 `config/app.toml`（与 `okx_demo` 同账户，只有 `market_type` 不同） |
| `strategies.carry_funding` | **enabled = true**（标的 `tier:majors`，8h 结算，往返成本 44.9bps，边际门槛 20bps） |
| 运行状态 | runner 已重启，**0 条 hedge 降级警告**，策略列表含 `carry_funding` |
| 会不会立刻开仓 | 大概率不会：BTC 这类主流币的实盘年化约 5%，30 天只能收约 41bps，**低于 44.9bps 的往返成本**——按规则就不该开。要开仓得有资金费率明显更高的标的（§36.4 那张表里 30–70% 年化的那些） |

**所以现在的状态是"通路打通、但按规则不开仓"**，这与 §26 的成本结论一致，不是接线失败。

---

## 39. 实施路径第 3 步启动：永续历史第一次到位（2026-10-07）

第 3 步是"补齐全样本口径"。**先做永续，因为它是最空的那条腿**：在本次之前，
155 个 USDT 永续**一条历史都没有**——而 carry 已经上线（§38），任何永续策略也都无从验证。

| 项 | 结果 |
|---|---|
| 请求 | 155 个 USDT 永续 × 1d + 4h，各 900 根 |
| 成功 | **147 个**（8 个未在模拟盘挂牌：H / SKY / SPK / TEST01 / TEST3103 / TEST3110 / TESTPR1 等测试合约） |
| 落盘 | `data/history/okx_demo/*_USDT__1d.jsonl` 与 `*_USDT__4h.jsonl` |
| 样例 | BTC 永续 1d **900 根**（2024-04-19 → 2026-10-05）；SOL 永续 4h 900 根（2026-05-10 → 2026-10-07） |

**意义**：分析口径从"20 个现货标的"扩到"**20 现货 + 147 永续**"，
永续那条 20% 的额度第一次具备了做真实样本研究的条件。

**第 3 步还剩两半**：

1. **现货：20 → 272**（约 252 个标的，配置的交易池目前只覆盖 20 个）；
2. **永续的 1h 周期**（本轮只拉了 1d/4h；1h 是最耗时的，因为每只需要翻更多页）。

### 39.5 第 3 步完成：数据口径 20 → 482（2026-10-08）

两半都做完了：

| 动作 | 结果 |
|---|---|
| 永续 1h | **147 个** × 900 根（BTC 永续 1h：2026-08-31 → 2026-10-07）→ 永续的 1d/4h/1h 齐了 |
| 现货全 USDT 线 1d | 请求 272 条，绝大多数成功；少数返回空（MUBARAK / SPELL / SYRUP / XAMD / XASML / TEST02 等，多为测试合约或已下架） |

**最终数据资产**：

| 周期 | 覆盖标的 |
|---|---|
| **1d** | **482**（本轮开始前是 124） |
| 4h | 167 |
| 1h | 167 |
| 15m / 1w | 20 / 30 |

标的总数 **124 → 271 → 482**（364 个非 X 开头 + 118 个 X 开头的 TradFi）。

**这是本项目第一次让"全样本口径"成为可执行的事实**——此前所有结论（§25 的随机对照、
§30–§32 的日线研究、§26 的 carry）都建立在 20 个标的、单一现货腿上。

剩下的不是补数据，而是**用新样本重跑**：现货目前只有 20 个标的配了 4h/1h，
若要把小时级策略也放到全样本上，还需补现货的 4h/1h 两个周期。

---

## 40. 全样本复验：§31 那张"日线转正"的表作废（2026-10-08）

补数据的意义就在于能重问一次。用 **482 个标的**（1d、并集时间轴 985 根、70% 切分 →
样本外约 296 天）跑 §31 的关键变体：

| 策略 / 变体 | 笔数 | 收益 | **PF** | 胜率 | 手续费 | 敞口 |
|---|---|---|---|---|---|---|
| trend_breakout（回踩关 + 无周线确认） | 27 | +0.90% | **0.88** | 40.0% | $5.02 | 44.3% |
| trend_pullback（无确认） | 4 | -0.18% | 0.00 | 0% | $1.28 | 1.7% |
| mean_reversion（周线确认） | 0 | — | — | — | — | — |

把切点推到 85%（样本外 74 天）做时段对照：trend_breakout 17 笔 +0.85%、**PF 0.80**、
胜率 44.4% —— 同样 **PF < 1**。

### 对照就说明了一切

| 样本 | 同一配置的 PF | 胜率 |
|---|---|---|
| 20 个标的（§31） | **2.97** | 57.1% |
| **482 个标的（本轮）** | **0.88** | 40.0% |

同一个配置、同一套规则，**样本一放大就从"3 倍盈亏比"掉到"小于 1"**。
PF < 1 且收益为正，意思是**盈利依赖少数离群赢家，而输的次数更多**——那不是可依赖的边际。

### 结论

1. **§31 的"日线全部变体转正"主要是小样本产物**（20 个标的、单一时段），应作废；
2. **§30 的"成本塌了但没转正"反而是更接近事实的描述**——换到 482 标的后，这个描述成立；
3. 只有 trend_breakout 还在交易（27 笔），另两个策略在全样本上几乎不出手；
4. 这条复验本身也是本轮补数据的价值证明：**同样的代码、同样的规则，样本从 20 扩到 482 才看得出哪张表是假的。**

---

## 41. carry 全样本分布：139 个永续（2026-10-08）

§26 的 carry 结论建立在 **13 个标的**上。数据补齐后，用**实盘费率**（§36.4 已证明模拟盘费率不可信）
在全永续上重测：147 个有历史，其中 139 个有 ≥20 条费率历史。

| 指标 | 结果 |
|---|---|
| 费率为正 | **117 / 139 = 84%** |
| 费率为负（空头要付钱） | 18 个，最差 **-30.9 bps/天** |
| 年化分布 | min 0.0% · p25 4.6% · **中位 7.0%** · p75 9.9% · max 80.4% |
| 回本天数（按实测往返 44.9bps） | min 2.0 · **中位 23.5** · p75 35.9 · max 7617 |
| **回本 ≤30 天（按规则值得做）** | **75 个合约** |

前 8 名：

| 合约 | 结算间隔 | 日均 | 年化 | 回本 |
|---|---|---|---|---|
| NG | 8h | 22.0 bps | **80.4%** | **2.0 天** |
| TRIA | 4h | 13.7 bps | 50.0% | 3.3 天 |
| GPRO | 8h | 13.6 bps | 49.7% | 3.3 天 |
| WLFI | 4h | 8.6 bps | 31.3% | 5.2 天 |
| NES | 4h | 8.4 bps | 30.7% | 5.3 天 |
| COAI | 4h | 8.1 bps | 29.5% | 5.5 天 |
| ZHONGJI | 8h | 7.6 bps | 27.8% | 5.9 天 |
| SLX | 4h | 5.7 bps | 20.8% | 7.9 天 |

### 与 §26 的 13 标的对照

| | §26（13 标的） | 本轮（139 合约） |
|---|---|---|
| 正费率比例 | 92% | 84%（样本更大，纳入更多负费率案例） |
| 中位年化 | 3.5% | **7.0%**（纳入了更多新上市/热门合约的高费率） |
| 中位回本 | 24 天 | 23.5 天（一致） |

顺带核对：§26 当年的 `funding_basis.py` 用的是**实盘**接口（没有模拟盘头），所以那组数字成立 ——
之前错的只是我自己那个筛选器的默认值。

**限制（不变）**：这是**横截面快照**（当前 30 天均值），不是历史回测。高费率合约
（NG / TRIA / GPRO）多是新上市或正处在单边行情里，费率会均值回归；§26 的两条尾部风险
（费率翻转、空头腿被急涨强平）依旧成立。

---

## 42. carry 标的池：4 → 48（2026-10-08）

§41 之后 carry 的唯一问题是**没东西可做**：主流 4 个币年化约 5%，30 天只能收约 41bps，
打不过 44.9bps 的往返成本，按规则一单不开。所以下一步是把标的池换成真正值得做的那些。

### 42.1 筛出来 75 个候选，然后被一道必要过滤砍到 48

| 步骤 | 剩余数量 |
|---|---|
| 有 ≥20 条费率历史的永续 | 139 |
| 过两道可信度闸门 + 费率为正 | 117 |
| **回本 ≤30 天** | **75** |
| **其中两腿都齐（现货 + 永续都有）** | **48** |

**最后那道过滤是关键，而且过滤掉的恰好是最好的那几个**：

| 被过滤掉（纯永续挂牌，配不成对） | 年化 |
|---|---|
| NG | 80.4% |
| TRIA | 50.0% |
| GPRO | 49.7% |
| NES | 30.7% |
| COAI | 29.5% |

carry 是**配对**交易，只有永续挂牌的合约没有现货腿可买。如果先不做这一步过滤就写进配置，
轻则每轮被拒，重则开出一条**裸空** —— 而裸空恰恰是这个项目一直拒绝称之为 carry 的东西。

48 个两腿都齐的候选里，前几名：

| 合约 | 年化 | 回本 |
|---|---|---|
| WLFI | 31.3% | 5.2 天 |
| SLX | 20.8% | 7.9 天 |
| XCU | 12.6% | 13.0 天 |
| ACT | 11.5% | 14.3 天 |

### 42.2 落地的配置

| 位置 | 改动 |
|---|---|
| `[universe.symbols_by_tier]` | 新增 `carry` tier（48 个）——单列一个 tier 是因为它是**另一类交易**：入场判据是资金费率，不是价格 |
| `[tiers.carry]` | 新增风控段：`max_positions = 5`、每日 3 笔、**无价格止损、无目标位**（有价格止损反而说明这不是 carry） |
| `carry_funding.symbols` | `["tier:majors"]` → **`["tier:carry"]`** |

**预算占用要说清楚**：carry 是两条腿，现货那一半走 `[legs.spot]` 30%、永续那一半走
`[legs.derivatives]` 20% —— 它**同时占用两条腿的额度**，并与方向性现货策略竞争那 30%。
这是刻意的（那两条腿确实都被占用），但值得知道。

### 42.3 状态与一个待观察的副作用

runner 已重启加载（universe 从 20 扩到 **61** 个标的）。

**副作用**：runner 现在要为 61 个标的拉 K 线（原来 20 个），每轮 tick 的取数时间大约会涨 3 倍。
carry 的策略逻辑其实**只读资金费率、不看 K 线**，所以这里有一个明确的优化空间：
对 carry 标的只取最新价（ticker）而不拉三个周期的 K 线。这需要在 runner 里区分"决策标的"与
"仅定价标的"，本轮先记录，不仓促改交易循环。

### 42.4 那个副作用：**测了，然后决定不做**

先测量再优化，测量结果推翻了预估：

| | 实测 |
|---|---|
| 20 标的时 | 约 1 分钟/轮 |
| **61 标的时** | **约 2.2 分钟/轮**（00:28:59 启动 → 00:33:32 时 `ticks_total=2`） |

**是 2 倍，不是预估的 3 倍；而 runner 的基准时钟是 15 分钟 K 线**，2.2 分钟的循环周期
完全来得及处理每一根 15m K 线。所以：

**这个优化现在不做。** 它需要改三处——在交易循环里区分"决策标的 vs 仅定价标的"、
给 `LiveFeed` 加 ticker-only 路径、确认策略循环对"没有 K 线的标的"处理无误——
换来的是缩短一个本来就够用的周期，而风险落在**唯一会真下单的循环**上。
设计留档，等周期真的成为瓶颈（标的再翻倍、或基准时钟降到 1m）再做。

### 42.5 抓到一个**完全静默**的接线 bug：carry 永远开不了仓

48 个候选上线、runner 重启之后，指标是 `open_positions=0`、`entries_today=0`——
**而按规则 WLFI 这类早就该开仓**（8.6 bps/天 × 30 天 − 44.9 = **+213 bps** 边际，门槛是 20）。

根因：carry 策略拿到的是**现货符号**（`WLFI/USDT`），而 feed 的资金费率走的是**主 venue（现货）**。
现货没有资金费率 → `fetch_funding_rate` 返回 `None` → 策略按设计 **fail-closed** → 永远不提案。

**整个过程没有任何报错、没有告警，只有 0 笔交易。** 这正是 §38 里警告过的那类
"配置看起来接上了、实际在空转"的缺陷——只不过上一次（08:55 那次 tdMode）会报 51000，这次连报错都没有。

| 修法 | 内容 |
|---|---|
| `LiveFeed.funding_venue` | 新增；`funding_rate` 从它取值，并把现货符号映射成永续符号（`SOL/USDT` → `SOL/USDT:USDT`） |
| `runner.build` | 对冲腿建好后把 `feed.funding_venue` 指过去 |

新增 4 条测试（含"没有 funding_venue 时现货符号必须是 None""必须问永续符号""按 tick 缓存"
"venue 抛错要报成无数据而不是抛出"）→ **192 测试全绿**。

**顺带观察**：重启过程中撞到一次代理瞬断（加载 swap 合约列表时 `NetworkError`），
**安全网按设计生效**——循环照跑、carry 本轮禁用、日志留 warning；再重启一次后 0 条警告。
这就是 §38.3 那张表存在的意义：一个会瞬断的外部依赖不该让交易循环陪葬。

---

## 43. 事故：第一对 carry 开成了裸多 $2,969（2026-10-08，账户已平）

carry 上线后第一次真实下单就出事故。**已处理、已定位、已修，账户已恢复无仓。**

### 43.1 发生了什么

| 步骤 | 事实 |
|---|---|
| 现货腿 | ACT/USDT 成交 289,694 ACT = **$2,969** |
| 永续腿 | **从未建立** |
| 结果 | 一条**裸多**在账户里待到被手动平掉 |
| 平仓 | 分三次卖出平干净（289694 → 119218 → 2135 → 0），末次状态 `spot 0 / perp FLAT / DELTA 0` |

### 43.2 根因：异步成交被当成了失败

日志原话：

```
carry pair failed [symbol=ACT/USDT error=spot leg not filled (open: ); no hedge was sent]
```

OKX 市价单返回的是**异步的 `status=open, filled=0`**，而 `PairedExecutor.open_pair` 把这个
当成"没成交"直接抛错。但订单**一秒后成交了**，此时：

- 对冲腿没发；
- 而"现货腿失败"那条分支**只假设"没有多头"、不做任何平仓**。

也就是说，我在 §37.1 标为**不变量 2**（"现货腿没成交 → 绝不发空头"）的那条，
在异步语义下**方向反了**：它保护了"没有多头就没有空头"，却没保护"订单其实会成交，所以必须等"。

### 43.3 修法

| 修改 | 内容 |
|---|---|
| `_await_fill` | 提交后**轮询 `sink.sync()`** 直到真实成交或超时，再据实判断——不再相信提交瞬间的响应 |
| "未成交"分支 | 改为先查**实际持仓**，有仓就立刻反向平掉；只有真的没有多头才安全返回 |

新增 2 条测试：**异步成交必须被等到且对冲照发**；**异步永不成交则不发对冲**。

### 43.4 同一批还发现两个 bug

1. **配对 ID 每轮重置**：`executor` 每次新建、内部序号从 1 开始，于是第一对之后
   **12 次全部被 `duplicate_client_order_id` 拒掉**。已改为带毫秒时间戳。
2. **hedge OMS 从未被 `sync()`**：永续腿的成交**永远进不了账本**（现货腿在账本里、永续腿不在），
   这会让一个中性对在账本里看起来像裸多。已加同步。

### 43.5 当前状态与规矩

**carry 已 `enabled = false`**，配置里写明了原因：

> 一个**既可能留下裸仓、又会跳过自己守卫**的机制，不能无人值守运行。

重新开启的前置条件很明确：**受控的单次双腿验证**——在有人看着的情况下开一对、确认两条腿同量、
确认永续腿成交进了账本，再平掉。§27.3 那次双腿验证是**通过**的（现货+永续都成交、DELTA −0.0006），
所以 venue 本身没问题；断的是 runner/OMS 这条路径对"异步成交"的表述。

---

## 44. 每日复盘 · 2026-10-08（08:00 CST）

### 44.1 当日数字

| 项 | 值 |
|---|---|
| 成交 | **6 笔**（ACT 现货腿、JUP 往返、PYTH 分批往返） |
| 当日手续费 | **$11.35** |
| 权益 | 103,027.53 → **102,957.40** = **−$70.13** |
| 期末持仓 | 账本 1 个（ACT 幽灵仓位）；交易所实际 **0** |

按标的拆：

| 标的 | 动作 | 净额（含费） |
|---|---|---|
| JUP | 买 $2,113 → 全平 $2,089 | **−$27.5** |
| PYTH | 买 $2,076 → 部分止盈 +$6.7 → 止损平 +$0.2 | **+$2.6** |
| ACT | carry 事故的现货腿，手动平仓 | **−$44.0** |

**−$70 里 −$44 是事故、−$25 是策略正常交易结果**（与"这些现货策略期望为负"一致）。

### 44.2 今天真正发生的事（都已在正文留痕）

1. **tdMode 修复闭环并线上验证**：10-06 切跨币种保证金后，现货单全被 OKX 拒（51000）。
   修好并重启后，**今天开出并平掉了 10-06 以来第一批真实仓位** —— JUP 与 PYTH，
   这正是"修复有效"的最终证据（不只是手工探针）。
2. **分仓从两条腿扩成四条腿**：现货 30 / 永续 20 / TradFi 30 / 期权 20，各自独立限额。
3. **数据口径 20 → 482 个标的**：补了 147 个永续（1d/4h/1h）与现货全 USDT 线日线。
4. **全样本复验推翻了 §31**：20 标的时 PF 2.97 的"日线转正"，在 482 标的上掉到 **PF 0.88** —— 小样本假象。
5. **carry 上线当天出事故并回滚**：第一对开成裸多 $2,969，已平；根因是异步成交被当失败，
   连带查出配对 ID 每轮重置、hedge OMS 未 sync、以及"张↔币"单位混淆四个独立问题（三个已修）。

### 44.3 优化建议（按优先级）

| # | 建议 | 理由 |
|---|---|---|
| 1 | **清掉 ACT 幽灵仓位** | 账本记 289,693 vs 交易所 0，占着 100% 账本持仓并持续产生对账告警；真去平仓会被拒 |
| 2 | **给 `paired.py` 加"张↔币"换算** | carry 重开的最后一个已知阻碍，复用 `swap_math.ContractSpec` 即可 |
| 3 | carry 重开后**先只放 1–2 对** | 事故刚发生，先用最小样本验证"两条腿同量 + 永续腿进账本" |
| 4 | 不要在现货方向性策略上再花力气 | 今天 JUP/PYTH 的 −$25 与 180 天样本外全负、随机对照劣于随机，是同一结论的又一次复现 |

### 44.4 一句话

**今天的主线不是收益，而是"把一直没被验证的东西逐个验证掉"**：tdMode 修复被真实成交确认、
日线结论被全样本推翻、carry 的四层缺陷被受控复现。代价是 −$70（含一次事故），
换来的是一张能站得住的清单。

**账户当前无裸仓**，其余三个策略（现货方向性）与四腿分仓不受影响。

### 43.6 受控验证（`scripts/pair_check.py`）：修好三条，暴露第四条

为解除封锁做了受控验证：**走 runner 的真实路径**（`PairedExecutor` + 两个真 `OMS` + 真 venue，
账本用临时文件以免污染生产对账），开一对、核对、再平掉。

**第一轮：复现了同一现象**，但这次能看清原因，而且是我上一版修复的两个漏洞：

| 漏洞 | 说明 |
|---|---|
| `_await_fill` 盯错了地方 | 它只看 OMS 的**订单记录**，而 OMS 在提交时记一次就不再更新 `filled_qty`——**成交是通过 `sync()` 单独返回的**。于是等了 15 秒仍判"未成交"，而现货其实已全额到账 |
| `_filled_base` 没有真读取器 | 只能退回 0，所以"有仓就平掉"这条又没触发 |

修法：`_await_fill` 同时看 `sync()` 返回的 fills（按 `client_order_id` 匹配求和）；
`_filled_base` 直接问 venue 的 `_free_base`——**账上的余额才是"有没有裸多"的判据**。

**第二轮：前三条全部通过 ✅**

| 检查 | 结果 |
|---|---|
| 现货腿成交且被**等到** | 1690.140845 DOGE |
| 永续腿发出 | ✅ |
| **两腿成交都进账本** | ✅ `['DOGE/USDT', 'DOGE/USDT:USDT']`（此前永续腿进不了） |

**第四轮检查暴露第四条**：`perp filled 1.69` —— 那是**张数**（DOGE 永续每张 1000 DOGE），
而 `paired` 模块全程按**币数**比较：

- `residual()` 报出假的 **+1688**；
- `close_pair` 把 1.69 当币数发出去 → 被 OKX 以
  `1.69 DOGE -> 0.0 contracts is below minSz` 拒绝。

**注意这一对仍在且是对冲状态**（现货多 1690 DOGE + 永续空 1.69 张 = 同一敞口），不是裸仓；
已用 `scripts/carry.py` 平干净（它用 `swap_math` 做了单位换算，单位是对的）：
`residual contracts 0.0`、`spot left 0`、`DELTA 0`。

**下一步很明确且局部**：给 `paired` 模块加上「张 ↔ 币」换算（复用 `swap_math.ContractSpec` /
`contracts_to_base`），然后把 `pair_check` 再跑一遍。carry 在那之前保持 disabled。

---

## 28. 50/50 分账上线（2026-10-07，用户确认）

用户确认：**新币（GMGN）接进来之前，按 50/50 使用策略**——50% 主流货币（现货）、
50% 合约与期权。§20.1 里那个"50% 按什么算"的歧义，本次按 **名义本金** 落地
（§20.1 建议的口径），理由写在下面第 3 点。

### 28.1 代码：风控第一次有了"腿"的概念

原来的风控只有一套组合级限额，"五五开"无法表达——两条腿会用同一个口袋，
谁的信号先到谁就先把预算花掉。本次加了 per-leg 限额：

| 文件 | 改动 |
|---|---|
| `src/ta/config.py` | 新增 `LegLimits` + `RiskConfig.legs`；校验"两腿之和 ≤ 账户级上限" |
| `src/ta/risk/engine.py` | `leg_of(symbol)`：带 `:` 的是衍生品腿，其余是现货腿；四个闸门按腿各算一次（总敞口 / 持仓数 / heat / 当日开仓数），并各自计数 |
| `config/risk.toml` | 新增 `[legs.spot]` / `[legs.derivatives]`；账户级改成两腿之和 |
| 看板 | 风控面板新增"50/50 分账"一行，并把组合级数字标注为"=两腿之和" |

**为什么按名义本金而不是保证金**：风控引擎只看得到持仓名义，看不到保证金；
而 50% 名义在 3x 下只占约 17% 保证金，名义口径更严格。杠杆留在 venue 层，
**没有**新增一个"看起来接线、其实没被读取"的配置键（§25 的教训）。

### 28.2 落地的数字（$103k 账户）

| 腿 | 总敞口 | 最多持仓 | heat | 每日开仓 |
|---|---|---|---|---|
| 现货 | 50% = $51.5k | 4 | 2.5% = $2.6k | 8 |
| 衍生品 | 50% = $51.5k | 2 | 1.5% = $1.5k | 4 |
| **账户级（=两腿之和）** | **100%** | **6** | **4.0%** | **12** |

账户级数字从"唯一起作用的限制"退化成"两腿之和的后备兜底"，
校验规则会拒绝任何"两腿之和超过账户级"的配置。

### 28.3 必须说清楚的一件事：那 50% 现在还没有东西去用

分账是**额度**，不是**收益**。当前能落地的只有：

- 现货腿：4 个策略（§25 已证伪，仍在跑，用来收集真实费用与成交）；
- 衍生品腿：**没有可跑的策略**。永续趋势策略 `perp_trend_funding` 已结案并禁用（计入 funding 后仍为负）；
  期权连 venue 和策略都还没有；唯一验证过的是 **carry 工具**（§27），而它是脚本，不经过风控引擎。

所以这一节的准确含义是：**"给衍生品留了 50% 的额度，等有正期望的东西来填"**。
§20.3 的那句话仍然成立——在方向没有边际的前提下，把钱放进衍生品是放大风险而不是优化收益；
现在多了一层保护，即两条腿不可能互相挤占额度。

---

## 29. 借鉴「三层投资框架」的落地评估（2026-10-07）

用户给了一套股票市场的三层框架：**六维监测（择时/定仓位）→ 五维赛道（择方向）→
五因子利润公式（择个股/定买卖点）**。下面逐层说清楚哪一层能落到加密数据上、
哪一层不能，以及**能落的那层实测出来是什么结果**。

### 29.1 逐层翻译：哪些维度有对应物，哪些没有

第一层的六个维度在股票里读的是 GDP/PMI/CPI、货币政策、北向资金/两融、PE/PB 分位、
均线系统、换手率/涨停家数。加密里这些**大多不存在**，只能找"最接近的可观测量"，
而不是换个名字继续用：

| 框架维度 | 股票里的读法 | 加密里的替代物 | 可测？ |
|---|---|---|---|
| 趋势面 | 大盘均线系统 | 全市场站上 EMA50 的比例（breadth） | ✅ |
| 周期位置 | GDP/PMI | 全市场中位 24 根 1h 收益 | ✅ |
| 风险偏好 | 无风险利率 | 已实现波动率的历史分位 | ✅ |
| 资金面 | 北向资金/两融 | 永续资金费率均值（拥挤的多头在付费） | ✅（需 funding 序列） |
| 情绪/拥挤 | 换手率/涨停家数/恐贪 | 中位 RSI（45–60 为不拥挤） | ✅ |
| 量能 | 成交量能 | 中位成交量 / 20 根均量 | ✅ |

第二层的"市场空间千亿级 / 3 年 CAGR>20% / CR5 集中度 / 政策支持 / 商业化落地"——
**这五个在加密里没有一个能用公开行情数据算出来**。能算的只有"叙事篮子"的价格与成交量，
那不是产业验证，是价格动量的另一件外衣。**所以这一层不实施**：宁可说做不到，
也不搭一个用价格冒充基本面的框架。

第三层五个因子里，**胜率与赔率是可算的**（回测直接给出），**时间效率与交易摩擦**
我们早在 §26 的 cost wall 里算过，**仓位管理**就是凯利公式——本节的 29.3 把它算了一遍。

### 29.2 第一层的实测：**它没有预测力，而且方向是反的**

实现：`src/ta/monitor.py`（六个维度各打 1–5 分，综合 6–30 分，≥3 个红灯即框架定义的
risk-off；输出一个仓位系数）。检验：`scripts/monitor_study.py` 用 20 个主流币 180 天
1h 历史，**每 24 根 bar 用"当时已知"的数据打分**，再看全市场未来 1 天/4 小时的收益。

| 桶 | 未来 +24h 中位 | 均值 | 胜率 |
|---|---|---|---|
| risk_on（≥25 分） | +0.53% | +1.15% | 59.7% |
| neutral（20–24） | -0.28% | -0.13% | 40.7% |
| cautious（15–19） | +0.00% | -0.11% | 49.7% |
| **risk_off（≥3 红灯）** | **+0.42%** | **+0.91%** | **56.8%** |

**"环境最差"那一组，未来一天反而更好**（比 risk_on+neutral 的 +0.11% 高 8 倍）。
分半检验同向，不是单段噪声：

| 区间 | risk_off 均值 / 胜率 | risk_on+neutral 均值 / 胜率 |
|---|---|---|
| 前 90 天 | +0.68% / 54.0% | -0.16% / 46.4% |
| 后 90 天 | +1.31% / 55.2% | +0.38% / 44.3% |

评分与未来收益也**不单调**：最低分档（6–14）+1.08%、中间档（20–24）-0.13%、
最高档（25–30）+1.15%——两头好、中间差。

**结论**：如果把六维监测接成"环境差就减仓"的刹车，在这段样本上它会**恰好砍掉收益最好的那段**。
所以：

- **不接线**——`monitor` 没有进入 runner 的 sizing 路径，只作为看板上的观察面板；
- 机制解释与 §25 一致：加密 1h–1d 尺度上均值回归占主导，"看起来最糟"的时刻
  （波动率分位高、breadth 低、缩量）往往就是恐慌低点；
- 反向使用（risk_off 加仓）是一个**方向性假设**，样本只有 180 天、单一 regime，
  **按 §25 的规矩不作为结论**，更不落地。

### 29.3 第三层的实测：凯利公式给的答案是"别做"

只剩胜率与赔率可算，就按框架自己的方法算一遍（`execution/sizing.py` 的
`kelly_fraction` / `payoff_ratio`，赔率由 PF 反推：R = PF×(1-W)/W）：

| 策略 | 样本外收益 | 胜率 | PF | 赔率 R | 凯利 f* |
|---|---|---|---|---|---|
| trend_breakout | -1.91% | 40.5% | 0.31 | 0.46 | **0** |
| trend_pullback | -0.91% | 21.7% | 0.27 | 0.97 | **0** |
| mean_reversion | -2.07% | 31.6% | 0.25 | 0.54 | **0** |
| new_listing_breakout | -0.60% | 37.0% | 0.51 | 0.87 | **0** |

四个全部为 0（负边际被夹到 0，因为"做空自己的策略"不是这张桌子上的动作）。
对照：胜率 55%、PF 1.8 的组合凯利给 24.4%。**框架第三层用我们自己的数字算，
输出的不是仓位比例，是"不要交易"这个判决**——这与 §25 的随机对照是同一个结论的两种算法。

### 29.4 这套框架里真正留下的是什么

| 层 | 处置 | 理由 |
|---|---|---|
| 六维监测 | **只作观察面板**，不接仓位 | 实测无预测力且方向相反（29.2） |
| 五维赛道 | **不实施** | 五个维度在加密里都无法用公开数据量化（29.1） |
| 五因子利润 | 保留两个可算的：**凯利**（结果是 0）与**摩擦/时间效率**（cost wall 已有） | 29.3 |
| 退出纪律 | **已经在跑**：止损/移动止损/破位/逻辑证伪（regime）→ 对应框架的三种卖出 | 现有实现 |
| "动作必须由系统信号触发" | 已经在跑：策略提案、风控裁决、订单唯一入口 | §15 的架构 |

一句话：**这套框架的"分工"（防守/方向/进攻）是对的，但填进去的"防守"维度在加密里测不出边际**。
真正有用的部分是它逼出来的两个可计算项——凯利与摩擦——而这两个都把结论推向同一个方向：
在找到正边际之前，仓位不该放大。

---

## 45. 实施路径第 4 步收口：期权数据层接入每日任务（2026-10-08）

§43 把期权曲面做通了，但它当时是一个**手动跑的一次性脚本**——样本只有 1 天，而
"IV 和 RV 谁贵"这个问题恰恰只有按天重复才能回答。本次把它挂进已有的每日任务。

### 45.1 挂点：不新开服务

`scripts/daily_research.py` 已经由 `com.tradingagent.newlisting` 每天 09:10 拉起，
原来跑 2 步（新币追踪、RV/IV 监控），每步都是"只读抓取 + 追加一条 JSONL"。
期权这两步是同一类东西，于是步骤从 2 变 4：

| # | 步骤 | 落盘 | 说明 |
|---|---|---|---|
| 1 | `new_listing_track` | `data/new_listings/records.jsonl` | 原有 |
| 2 | `vol_monitor` | `data/vol/rv_iv.jsonl` | 原有 |
| 3 | `options_surface` | `data/options/surface.json` | **新增**，实盘 `opt-summary` 曲面 |
| 4 | `options_cost` | `data/options/cost_history.jsonl` | **新增**，每天 1 行 × 3 家族 |

第 3、4 步有顺序依赖：成本步读的是上一步刚写的曲面文件。整个任务**只在 4 步全挂时**才返回 1，
所以一次代理抖动不会把服务染红，也不会掩盖到底是哪一步真的坏了。

### 45.2 接线时撞到的两个缺陷（都属"看着在跑、其实少做事"）

**缺陷 1：解释器里没有 httpx。** `options_surface.py` 原本用 `httpx` 发请求。
手工跑的时候用的是 anaconda 的 python3（有 httpx），所以一直是好的；但跑这个每日任务的
launchd 用的是 `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`，
实测 `import httpx` 直接 `ModuleNotFoundError`。也就是说，**照原样接进去，从第二天早上 09:10 起
每天都会静默失败**，而失败信息只会躺在 `logs/ta-newlisting-service.err.log` 里。
已改成 stdlib `urllib`（与隔壁 `vol_monitor.py` 完全同一套写法，支持 `--proxy` / `TA_PROXY`），
这个脚本从此不依赖任何第三方包。

**缺陷 2：一个家族抓失败，文件就少一个家族，没人吭声。** 拉取按家族循环，任何异常都被
`except` 吞掉然后 `continue` —— 结果是 `surface.json` 里少一家，`options_cost.py` 跟着少写一行，
**两处都不报错**。这不是假设：2026-10-08 有一次运行里 BTC/ETH 在同一个代理上成功、SOL 失败，
当天 SOL 就没有行。已加每个家族 3 次重试（2 秒 / 4 秒退避），三次都失败才放弃并显式打印
`GIVING UP`；成本步发现曲面缺家族时也会打 `WARNING`。

### 45.3 验证

| 检查 | 结果 |
|---|---|
| 全量测试 | **202 passed** |
| `daily_research` 端到端（用 launchd 那个 3.14 解释器） | **4/4 `rc=0`** |
| 曲面合约数 | BTC **1,272** / ETH **1,152** / SOL **134** |
| 成本序列 | 1 天 × 3 家族（`data/options/cost_history.jsonl`） |

手动跑（任何解释器都行）：

```bash
python3 scripts/options_surface.py   # 刷新曲面（实盘目录，不带模拟盘表头）
python3 scripts/options_cost.py      # 追加当日 IV vs RV
```

### 45.4 第 5 步（期权策略）的闸门

第 4 步只解决"有没有数据"。要不要建策略，取决于 **IV 与 RV 的符号差是否稳定**：

| 日期 | BTC IV−RV | ETH IV−RV | SOL IV−RV |
|---|---|---|---|
| 2026-10-08 | **−5.8pp** | **+5.0pp** | **−12.1pp** |

一天不够。按 §34.4 的定级，第 5 步在产品上只做**买权利金**的有限风险结构（卖方要保证金、
裸卖尾部风险不可接受），而它的先决条件是**在同一标的上反复测得 IV 稳定低于 RV**。
BTC 和 SOL 现在看起来符合，但那是 1 天的观测——**先攒 5–7 天再看**。

路径上仍然开着的是第 3 步（补齐现货 272 / 永续 155 的历史，纯抓取）。

---

## 46. 重启会吃掉周期对账：为什么"今天该对账却没对账"（2026-10-08 修复）

### 46.1 顺着 51008 查出来的

**发现过程**：巡检 `logs/ta-run-service.log` 时看到两类"每 15 分钟重试一次、永不成功"的退出——
NEAR/USDT 卖 504.8768201 被 OKX 以 `51008 InsufficientFunds` 拒、SUI/USDT 卖 1.4e-06 被本地灰尘闸门拦下。
顺着 51008 去查对账记录，同时看到两件对不上的事：一是账本与交易所**其实已经对齐**
（只读逐币比对：ACT/CRV/BAND/NEAR/W/PEPE 差值都 <1e-11，`scripts/reconcile_ledger_to_venue.py`
的 9 条 `manual-reconcile` 补记生效了）；二是 `ta_ticks_total` 只有 **20**。

### 46.2 "只有 20" 不是卡死，是清零

ledger 里 equity 记录的间隔稳定在 **101–108 秒**，说明循环一直在跑；20 tick × ~103 秒 ≈ 34 分钟，
而当前进程正是 **16:46 CST** 起跑的。也就是说计数器**每次重启清零**——本日志窗口（15:09 CST 起）
里就有 4 次 `runner start`（07:21 / 08:03 / 08:31 / 08:46 UTC）。

### 46.3 真正的问题：重启比对账周期更频繁

periodic 对账要攒满 `reconcile_every_ticks = 30`（≈**52 分钟**）才跑一次。
**只要进程活得比 52 分钟短，这个检查一次都不会执行**——不报错、不告警，只是没跑。
今天 03:26 / 05:59 UTC 那两条 reconciliation mismatch 反而印证了这点：只有活过 52 分钟的那几轮才检查过。

**修复**：计数器初值从 `0` 改成 `reconcile_every_ticks - 1`，
即**重启后的第一个成功 tick 就做一次对账**，之后回到正常的 30 拍周期。代价是每次启动多一次只读对账调用。

| 检查 | 结果 |
|---|---|
| 新增测试 | `tests/test_core.py::ReconcileCadenceTests` |
| 全量测试 | **203 passed**（原 202） |
| 生效时点 | 下一次 runner 重启（KeepAlive 拉起时自然带上） |

---

## 47. 实施路径第 3 步收口：补历史时挖出「模拟盘与实盘不是同一份数据」（2026-10-08）

§34.4 的第 3 步是「补齐现货 272 / 永续 155 的历史」。抓完之后发现：**这 427 个标的里，
有 39 个根本抓不到，而且原因不是「没有数据」。**

### 47.1 覆盖面的变化（按磁盘实测）

| 目录 | 周期 | 抓之前 | 抓之后 |
|---|---|---|---|
| `data/history/okx_demo`（模拟盘） | 4h / 1h | **167** | **388** |
| `data/history/okx_demo`（模拟盘） | 其中现货 | 20 | **241** |
| `data/history/okx_demo_live`（实盘，本轮新建） | 1d/1w/4h/1h | — | **84**（21 标的 × 4 周期） |

260 个标的 × 两个周期 × 900 根（4h 覆盖约 **150 天**，1h 覆盖约 **37 天**），一次跑约 35 分钟。
永续那 147 个本来就是齐的 —— 缺的一直是**现货**。

### 47.2 39 个标的为什么抓不到：不是「没数据」，是「走错了目录」

拿 `XAMD-USDT` 做对照，同一个 URL、同一个代理，只差一个请求头：

```
GET /api/v5/market/candles?instId=XAMD-USDT&bar=4H&limit=3

  带 x-simulated-trading: 1（模拟盘）→ {"code":"0","data":[],"msg":""}      ← HTTP 200，code 0
  不带该头          （实盘）→ {"code":"0","data":[[1791446400000,...]]}      ← 有 K 线
```

模拟盘目录对这类标的**返回空数组而不是错误**，所以在 `ta data fetch` 眼里，它和
「这个标的没有历史」长得一模一样，只会打印一句 `no bars returned`。
逐个比对两个目录之后，39 个分成两类：

| 类别 | 数量 | 表现 | 结论 |
|---|---|---|---|
| **live-only** | **21**（18 现货 + 3 永续） | 模拟盘空、实盘有 | 实盘抓得到，模拟盘抓不到 |
| **僵尸条目** | **18**（13 现货 + 5 永续） | 两边都 `51001 does not exist` | instrument 列表里有，市场里没有 |

这与 README 36.4（模拟盘资金费率符号是反的）、43（模拟盘 IV 全 0）是同一类事实：
**同一个 API 在模拟盘与实盘目录下不是同一份数据，而且差异不体现在状态码上。**

### 47.3 探测方式本身也会骗人

排查过程中先用 python 的 `urllib` 裸探这 39 个，结果**全部 403**，一度像是「代理挂了」。
换成 `curl` 或带上浏览器 UA 就是 200。这个坑项目里早就踩过 ——
`vol_monitor.py` 与 `options_surface.py` 顶部都写死了浏览器 UA，理由就是这一条。
所以：**「整批请求全失败」时要先换一种客户端重发一次，再下结论。**

### 47.4 顺带修掉：inventory 的「SWAP 我们有历史 0」

`scripts/universe_inventory.py` 用
`path.name.split("__")[0].replace("-", "/", 1)` 把文件名反推成 symbol。
但永续文件名是 `BTC-USDT_USDT__1h.jsonl`（`safe_symbol` 把 `:` 写成 `_`），
反推得到 `BTC/USDT_USDT`，永远匹配不上 `BTC/USDT:USDT`。

于是这张表的「我们有历史」一栏长期显示 **SWAP = 0**，而磁盘上躺着 **147** 条永续序列 ——
又是一个「报告数字静默为错」。改成按文件名 stem 与 `safe_symbol(symbol)` 比较后，
同一张表立刻显示 **147**。

### 47.5 新增 `ta data coverage`：把「标的池到底几个」变成可核对的一行

「我们分析多少个标的」在这个项目里有过三个不同答案（交易所列出 / 我们有历史 / 策略在交易），
混用已经产生过一次错误结论（§33）。新命令离线读 inventory + 历史目录，按类别 × 周期打印四列：

```bash
python3 -m ta data coverage
```

```
class     tf     listed   demo  live-only   none
SPOT      4h        272    241         18     13
SWAP      4h        155    147          3      5
FUTURES   4h         12      0          0     12
```

`live-only` = 只有实盘目录有 K 线；`none` = 两边都没有（僵尸条目）。
**注意是「按周期」而不是「任一周期」**：只有 1d 而没有 4h 的标的**不算覆盖 4h** ——
这正是缺口一直看不见的原因。

### 47.6 还没做完的

| 项 | 状态 |
|---|---|
| 12 个交割合约（FUTURES） | **完全没有历史**，从来没抓过 |
| 18 个僵尸条目 | 仍留在模拟盘 instrument 列表里，inventory 会照抄 |
| 21 个 live-only | 模拟盘**不可交易**（没有 K 线就没有信号）——只能进研究样本，不能进交易池 |

抓取命令（实盘序列务必单独放，不要与模拟盘合并）：

```bash
python3 -m ta data fetch --symbols "XAMD/USDT,CC/USDT,..." --timeframes 4h,1h --source live
```

---

## 48. carry 的对冲腿为什么"永远不存在"：账本把「开空」当成「卖出超量」丢掉了（2026-10-08 修复，待生效）

巡检时发现 BAND 永续腿在**自我增殖**：每 30–50 分钟被再买一次，交易所确实成交，
而仓位读数始终是 0，对账告警每 52 分钟响一次。追下去是一条完整的因果链，根因只有一行代码。

### 48.1 那行代码

`PositionBook.apply_fill` 的卖出分支原本是：

```python
sell_qty = min(qty, position.qty)
```

对**现货**这是对的 —— 不能卖没有的东西。对**永续**这是静默错误：永续的卖出**就是开空**。

### 48.2 一条链把它全部串起来

| 时刻 | 发生了什么 | 账本记成了什么 |
|---|---|---|
| 14:43:24 | carry 配对成立：买现货 4,431 + 卖永续 4,431 | 现货腿入账；**永续腿 `min(4431, 0) = 0`，被丢掉** |
| 每次重启（今天 6 次） | 配对状态从 `data/carry/pairs.json` 恢复 | 看不到对冲腿 → 再补一次买入 |
| 16:04 / 16:32 / 16:47 / 18:04 / 19:55 / 20:04 | 补了 1,249 + 1,253 + 1,253 + 404 + 413 + 2,220 | 账本里 BAND 永续 = **买单之和** |

于是对账器报的 local 值 **4159 = 1,249+1,253+1,253+404**、**6792 = 再加 413+2,220** ——
逐一吻合。这也解释了为什么 `ta_heat_pct` 一直是 0：**空头的符号是负的，而所有视图都在筛 `qty > 0`**。

### 48.3 修复（`src/ta/portfolio.py`，4 处，互相依赖）

1. **永续的卖出允许把仓位做成负数**（开空），并正确维护空头开仓均价与平仓已实现盈亏；
2. **永续与现货的现金口径分开** —— 现货按名义金额划账，永续只划已实现盈亏与手续费。
   这条不是锦上添花：历史 25 笔永续卖出名义 **$3.42M**，按现货口径会让 $101k 的账户现金流变成 **+$3.4M**；
3. **`equity()` 对永续计未实现盈亏而不是名义** —— 否则光是历史空头就会从权益里扣掉 $3.4M；
4. **`open_positions()` 返回净额非零（多空都要）**，`_is_unsellable()` 改用绝对值 ——
   否则刚修好的空头会在下一轮被当成"灰尘"删掉。

### 48.4 验证

| 检查 | 结果 |
|---|---|
| 新增测试 | 开空 / BAND 事故序列回归 / 平空实现盈亏 / 现货超卖仍被夹住 |
| 全量测试 | **208 passed** |
| 真实账本重放 · 永续腿 | 被全部丢弃 → **11 条带符号的腿**（10 空 + BAND 多 2,361） |
| 真实账本重放 · 现金流 | **+$3,418,010 → −$4,872** |
| 真实账本重放 · 永续未实现 | **−$131.29**（数量级终于合理） |

### 48.5 为什么"修好了"却还没上线

修好之后，账本会**如实**显示出 10 条历史上并不存在于交易所的空头腿（合计名义约 $3.4M）。
如果此时让它随重启生效，runner 会去**平这些不存在的仓位** —— 正是先前 NEAR/SUI 那类
`51008 InsufficientFunds` 拒单的成因。

所以顺序必须是：

1. 先把 `scripts/reconcile_ledger_to_venue.py` 从"只查现货余额"扩展到 `fetch_positions`（含 ctVal 换算），
   dry-run 看清差异，再把幽灵腿对掉；
2. **然后**让这份修复随下一次自然重启生效。

> **更新（同日晚些时候）**：这两步已经做完并上线，见 §49。

---

## 49. 永续对账收口：幽灵腿清零，顺带记下我自己踩的三个坑（2026-10-08）

### 49.1 结果

| 项 | 之前 | 现在 |
|---|---|---|
| 账本永续腿 | 11 条（10 条只存在于账本的幽灵空头 + BAND 多 2,361） | **1 条：CRV/USDT:USDT −7,387** |
| 交易所永续仓 | — | CRV short 7,387 张（ctVal=1，名义 $2,913.58） |
| runner 周期对账 | 每个周期 11 条仓位差异 | **仓位差异 0 条**，只剩一条现金差异 |

**落地顺序很重要**：先写账本修正，**立刻**重启 runner，让新账本代码与修正同时生效。
两个改动单独上线各有危害 —— 只改账本，旧代码会把修正买单当现货计入现金流（−$3.4M）；
只重启，10 条幽灵空头会按未实现盈亏虚增权益（约 +$3.7 万）。

### 49.2 我在这件事上自己踩的三个坑

**坑 1：修正成交按市价定价，凭空创造了 $35,956 利润。**
第一版用市价平掉幽灵空头。可那条腿**从未在交易所存在**，按市价"平仓"就等于把它和市价之间的差额记成已实现盈亏。
结果：假利润 **$35,956**、现金流 +$31,765。改成按**账本自己的开仓均价**定价后，
现金流与 realized 与修正前**完全一致**（−$4,872.27 / −$680.77）。
已提取 `plan_swap_fix()` 并加 6 个测试锁住这条规则。

**坑 2：修正成交漏了 `venue` 字段，运行中的 runner 根本看不见。**
我手写的 11 条修正行没有 `venue`，而 runner 重建账本走的是 `fills_for(venue="okx_demo")` ——
**缺 venue 的行会被静默过滤**。于是离线重放、对账脚本全都报"已修好"，
而**活着的那个进程**照旧带着幽灵腿跑，下一轮对账继续报警。
教训：**离线重放的结果不能当作"运行时也看到了"**，要用与运行时相同的过滤条件验证一次。

**坑 3：venue 快照在现货模式下看不见永续仓。**
runner 的对账把 `account_snapshot()` 当作交易所那一侧，而它在 `market_type=spot` 时只读余额，
于是任何永续腿都会被报成 `venue=0.0` —— 拿一个"看不见永续仓的视图"去对账永续仓。
已让 `get_account()` 无论哪种模式都合并 `fetch_positions()`（按 contractSize 换算、带符号），
并修掉 `_managed_account` 里把负仓位强制成 0 的那一行。

### 49.3 剩下那条现金差异：**不是 funding 造成的**（我猜错了，实测推翻）

现金差异 expected 98,486.20 vs 交易所 97,423.84 = **$1,062.36**。我先把它归因于"账本不记 funding"，
**这个归因是错的**：

| 实测项 | 数字 |
|---|---|
| funding 账单（交易所 `type=8`） | **11 笔，合计 +$12.63** |
| 全量账户账单 | **2,404 条** |
| 我们账本里的成交 | **140 笔** |

12.63 与 1,062 差 84 倍，funding 解释不了它。真正的解释是：
`baseline + 我们的现金流 == 交易所现金` 这个恒等式**只有在"账户自始至终只被本 bot 碰过"时才成立**，
而这个模拟盘账户有 **2,393 条成交账单**——大量账本完全不知道的活动。
所以残余现金差异是**结构性噪声地板**，会稳定存在而不会漂移。

**后续判断标准要改**：不看"是否为零"，看"**残余是否稳定**"。

### 49.4 顺带的正向确认：永续现金口径是对的

相邻两笔永续成交账单的 `bal` 差值恰好等于 **(pnl + fee)**，**不包含名义金额** ——
这正好验证了 §48.3 里"永续只按已实现盈亏与手续费划账"的新口径与交易所一致
（我原本担心它和交易所口径不符，实测一致）。

新增只读开关：`python3 scripts/reconcile_ledger_to_venue.py --explain-cash`
—— 用交易所**自己的账单**拆解现金差异，把 funding 与外部账单分别列出，不再靠猜。

---

## 50. funding 入账：账本开始记录资金费（2026-10-08）

### 50.1 为什么做

永续每 8 小时结算一次资金费，交易所**真实收付**，而账本一笔都没记。
量级小（实测 11 笔 = +$12.63），但它是"账本 vs 交易所"里唯一**确定属于我们**的缺口 ——
其余差额来自账户里账本之外的活动（§49.3）。确定的缺口就该补，不该混在不确定里。

### 50.2 四处改动

| # | 改动 | 为什么 |
|---|---|---|
| 1 | `PositionBook.apply_funding()` | 资金费是已实现现金，**不动仓位** |
| 2 | `Ledger.record_funding()` | 写 `kind=funding` 事件，带交易所 `billId` 做幂等键 |
| 3 | `rebuild_book()` 改走 `events_for()` | **原来只重放 fill，看不到 funding —— 记了也白记** |
| 4 | `scripts/sync_funding.py` | 从账单 type=8 拉取、按 billId 幂等补写，默认 dry-run |

第 3 条是重点：这与昨天"修正行漏了 `venue` 字段导致 runner 看不见"是**同一类错误** ——
数据写进去了，但重建路径不读它。所以这次专门写测试锁住（`FundingAccountingTests`）。

### 50.3 落地结果

| 检查 | 结果 |
|---|---|
| 首次同步 | **11 笔，+$12.625197** |
| 账面现金流 | −$4,872.2665 → **−$4,859.6413**（差值正好 +12.6252） |
| 重跑 | **0 new**（幂等成立） |
| 测试 | **220 passed** |

### 50.4 接进每日任务时又撞到同一个坑

`daily_research.py` 跑的是 python.org 的**裸 3.14**，没有 ccxt ——
直接 importlib 调 `sync_funding` 会每天静默失败，**这正是 §45.2 那个 httpx 坑的同型**。
所以新增 `run_script()`：需要第三方依赖的步骤交给 `/opt/anaconda3/bin/python3` 作为子进程跑
（已验证在裸解释器下 rc=0）。现在每日任务 5 步：

```
new_listing_track → vol_monitor → options_surface → options_cost → sync_funding.py
```

**接完还没完**：用任务自己的解释器跑真实参数时 rc=1 —— `VenueError: API key is not set`。
`launchd print com.tradingagent.newlisting` 显示它只导出 `TA_PROXY / HTTPS_PROXY / HTTP_PROXY /
PATH / PYTHONPATH / TZ`，**没有任何 API key**（密钥平时由 runner 的 `--env-file .env` 加载）。
所以这一步会每天 09:10 失败一次，只往 `logs/ta-newlisting-service.err.log` 留一行，没人会看。
修复：`sync_funding.py` 自己读 `.env`（已存在的环境变量优先）。

> 教训：**新增自动化步骤，必须用它实际运行时的解释器 + 实际运行时的环境跑一次**，
> 在自己的 shell 里跑通不算数。这个项目已经因此栽过三次（httpx、venue 字段、这次的 API key）。

### 50.5 生效范围

**不需要重启 runner**。但运行中的进程仍是 fills-only 重放，所以 funding 只会在**下一次自然重启后**
进入它对账时用的现金流。在此之前：新代码与离线重放看到 −$4,859.64，旧进程看到 −$4,872.27。

---

## 51. 对账判据换掉：从"是否为零"改成"是否漂移"（2026-10-08）

### 51.1 问题

§49 已经证明现金残余**构造上不可能归零**（账户有 2,404 条账本之外的账单），
但看板仍按"出现过多少次 reconciliation mismatch"报 warn —— 一个已经 settled 的账户会永远挂警告。

### 51.2 新判据

| 项 | 判据 | 理由 |
|---|---|---|
| **仓位差异** | **仍然是硬零** | 交易所没有的腿就是真账本错误（幽灵腿正是这一类） |
| **现金残余** | 看**稳定性**，不看是否为零 | 静止的差额是这个账户的地板；**漂移**才意味着有未建模的资金流出 |

细节：现金的稳定性只取"**最近一次出现仓位差异之后**"的干净窗口 —— 否则修复中途的中间态
（残余一度摆动 ±$4 万）会把一个已经安定的账户判成漂移好几天。窗口内 max−min ≤ $50 记 info，
> $50 记 warn。同时把 `reconciliation mismatch` 从"按次数计警告"的名单里删掉（计数不含信息量）。

### 51.3 结果

看板红线区现在是**干净的**，只剩一行信息：

```
[info] 现金对账残余（稳定）
       残余 $+1,062.36；干净窗口 1 次波动 $0.00
       —— 账户有账本之外的活动（2,404 账单 vs 140 成交），
          此差额构造上不可能归零，只看它是否漂移
```

实现是三个纯函数（`cash_residuals` / `position_diff_count` / `cash_residual_flag`）+ 4 个测试，**225 passed**。
"干净窗口 1 次"会随对账周期（约 52 分钟一次）累积，波动值过几天才有统计意义。

---

## 52. 全样本 1h 复跑：把标的池从 20 扩到 241，符号没有翻（2026-10-08）

补完 4h/1h 历史（§47）的目的就是这一步 —— 在**更全的样本**上重跑四策略。

### 52.1 一个先要纠正的口径

有 1h+4h 历史的符号是 388 个（241 现货 + 147 永续）。但 **388 与 241 两次跑出来的 8 个指标逐位相同** ——
永续符号在这条现货策略上贡献 0。所谓"388"对这个策略其实**是 241**。

### 52.2 结果（trend_breakout @ 1h，walk-forward 0.7）

| 口径 | 笔数 | 收益 | PF | 胜率 | 手续费 | 暴露 |
|---|---|---|---|---|---|---|
| 样本内 70% | 445 | −4.02% | 0.58 | 42.0% | $112.46 | 31.1% |
| **样本外 30%** | **43** | **−0.98%** | **0.21** | **25.0%** | $11.87 | 31.0% |
| 样本外 @3× 成本 | — | −1.22% | — | — | — | — |

工具自己的判定：`out-of-sample is not profitable: do not trade this configuration`。

### 52.3 两条值得记的观察

1. **把标的池从 20 扩到 241，符号没有翻。** 这与 §25（因子劣于随机）、
   §40（日线"转正"是小样本假象）同向 —— 再次指向"不是样本量的问题"。
2. **放宽标的池并不自动买到统计力**：样本外只有 **43 笔**（约 30 天窗口 × 241 个标的），
   因为这条策略的信号本来就稀疏。**"样本够不够"既取决于标的多不多，也取决于信号频不频。**

另注：这次样本外手续费占总亏损约 **12%**，与实盘那个 60%（主要来自 carry/现货换手）不是一回事。

### 52.4 其余三条复跑的前置条件

`trend_pullback` / `mean_reversion` / `new_listing_breakout` 声明的周期是 **15m**，
而 15m 历史只有 20 个标的 —— 要复跑它们，必须先补 15m 全样本
（约 427 标的 × 900 根 ≈ 30 分钟，`ta data fetch --timeframes 15m`）。

---

## 53. 补齐 15m 并复跑另外三条：全部样本外为负，手续费占亏损 55–87%（2026-10-09）

### 53.1 先花了十分钟做了一个便宜的负结论

补数据之前先试了个替代方案：给 `ta backtest` 加 `--timeframe` 覆盖（**仅研究用**，
runner 不变），把三条 15m 策略强行放到已有的 1h 全样本上跑。结果它们**几乎不下单**：

| 策略 @1h（不是它设计的周期） | 样本外笔数 |
|---|---|
| mean_reversion | **0** |
| trend_pullback | 10 |
| new_listing_breakout | 9 |

门槛是按 15m 调的，**换周期不能用** —— 这个负结论本身值钱，省掉了以后重复尝试。

### 53.2 补数据

**241 个现货符号 × 1800 根 15m（18.7 天）**，exit=0，约 9 分钟。

### 53.3 在自己周期上的结果（15m，walk-forward 0.7，样本外约 5.6 天）

| 策略 | 样本外笔数 | 收益 | PF | 胜率 | 手续费 | 费用占亏损 |
|---|---|---|---|---|---|---|
| trend_pullback | 28 | −0.15% | 0.34 | 26.7% | $8.31 | **55%** |
| mean_reversion | 54 | −0.21% | 0.65 | 50.0% | $14.08 | **67%** |
| new_listing_breakout | 44 | −0.07% | 0.71 | 40.0% | $6.08 | **87%** |
| （@3× 成本） | — | −0.31% / −0.49% / −0.19% | — | — | — | — |

工具对三条都判 `not profitable`。**费用占亏损 55% / 67% / 87%**，与实盘观测到的
"手续费约占已实现亏损 60%" 完全同一个量级，而且在三条互不相同的策略上重复出现。

### 53.4 结论 —— **本节已被 §54 更正，请以 §54 为准**

本节最初写的是"这些策略不是被方向打败的，主要是被手续费吃掉的"。
把费用真正归零重跑之后，**这个框架是错的**：四条在零手续费下**仍然是负的**。
见 §54。

---

## 54. 零手续费的对照实验：四条策略**在零成本下也全是负的**（2026-10-09）

### 54.1 先修掉一个把实验做废的缺陷

用 `--fee-multiplier 0` 重跑时，输出与默认**逐位相同**。查代码：走 `--split`（walk-forward）分支时，
样本内与样本外两次调用**都没有把 `args.fee_multiplier` 传进去**，而下面那行 3× 压力测试写死了 3.0。
于是 `--fee-multiplier` 只在非 split 路径生效，**split 路径下你给的成本假设被悄悄丢掉**，
而 3× 那行照常打印，看起来一切正常。已修（两处补上传参），修复后 `fees_paid` 才真的变成 0.0。

### 54.2 对照结果（样本外）

| 策略 | 有手续费 | **零手续费** | PF @fee=0 | 费用占亏损 |
|---|---|---|---|---|
| trend_breakout @1h | −0.98% | **−0.86%** | 0.22 | 12% |
| trend_pullback @15m | −0.15% | **−0.06%** | 0.50 | 55% |
| mean_reversion @15m | −0.21% | **−0.07%** | 0.83 | 67% |
| new_listing_breakout @15m | −0.07% | **−0.00%** | 0.95 | 87% |

### 54.3 正确的关系

1. **扣费前期望已经 ≤0**（new_listing_breakout 的毛期望基本就是 0）；
2. 手续费**放大**一个已经在亏的信号，而不是造成它；
3. "费用占亏损 55–87%"这个比例是真的，但它衡量的是**放大倍数**，不是因果 ——
   把费用砍到 1/3 只能把 −0.21% 变成约 −0.14%，**仍然为负**。

分档看：**15m 那三条更接近"成本受限"**（毛期望 ≈ 0，砍成本最好的结果是回到零）；
**1h 的 trend_breakout 是信号本身为负**（毛 −0.86%）。

### 54.4 下一步

**先解决策略，再谈成本。** 在毛期望为 0 的信号上做成本优化，最好结果也只是打平 ——
这与 §25（因子劣于随机）一致：把这四条的时间和参数再调，不会产生正期望；
需要的是**不同的收益来源**（例如 carry 的资金费结构，它是唯一一个收益不来自价格方向的想法）。

**样本量警告**：样本外 5.6 天（15m）/ 28–54 笔，工具自己提示 `not enough to conclude`。
能站住的是"零成本下仍为负"这个**方向性**判断。

---

## 55. 资金费全样本扫描：27/29 中位为正，但中位回本 29.9 天（2026-10-09）

### 55.1 先补数据（这次没跑完，可续跑）

资金费历史原本只有 **13 个永续**，而全样本有 **155 个** —— 任何"全样本 carry 评估"都做不了。
新增 `scripts/fetch_funding_history.py` 做回填（OKX 每天结算 3 次、每页 100 条，约 100 天 = 3 次请求/标的）。

**这次没跑完**：本地代理在持续负载下大量 `SSL UNEXPECTED_EOF_WHILE_READING`，
加了两轮重试后仍有约 1/3 失败，目前写了 **37/155**。
脚本**可重入**（按时间戳合并、已存在的不重写），直接重跑就是续跑。

### 55.2 结果 —— **样本从 29 个扩到 139 个之后，一个数字变了**

回填推进后，覆盖从 29 个永续涨到 **139 个**（≥90 条打印）。同一个口径重算：

| 项 | 29 个样本 | **139 个样本** |
|---|---|---|
| 中位资金费为正 | 27/29 = **93%** | **94/139 = 68%** |
| 中位回本 | 29.9 天 | **29.9 天** |
| 回本 ≤30 天 | 21/29 | 88/139 |
| 回本 ≤14 天 | 0/29 | **2/139** |

**正利率从 93% 掉到 68%，少了 25 个百分点。** 原因很简单：那 29 个里主流币权重过高，
而全样本里大量新上市与冷门永续的中位资金费是负的 ——
**这正是本项目反复遇到的小样本偏差，只不过这次是我自己在同一步里踩的。**

稳住的部分：**中位回本仍是 29.9 天**，且 ≤14 天的只有 2 个。
最好的两个（NG 7.77 bps/天、TRIA 5.64）都是上市不久的新标的，历史仅约 100 天，不可外推。

所以修正后的表述是：**资金费为正的永续约占三分之二，其中绝大多数要拿约一个月才回本**；
"随便挑一个永续做 carry"是不够的，**必须按中位资金费筛选**。

### 55.3 均值口径是陷阱（本项目反复强调的那条）

按**均值**算，回本只要 **4–13 天**，看起来好得多。但 p90 普遍是 3.00 bps/天、中位只有 1.5–3.0 ——
均值被少数极端结算拉高。最极端的例子：**BRETT 中位 1.50 bps/天、均值 10.13**，
回本从 30 天变成 4.4 天，**差的 7 倍全是离群值**。

### 55.4 结论与 §54 的对照

**carry 的诚实画像是：资金费是真实的正期望，但它是慢钱** ——
一次往返（实测 44.9 bps）要拿约**一个月**的资金费才能还清。

| | 四条方向性策略（§54） | carry（本节） |
|---|---|---|
| 毛期望 | **≤ 0** | **> 0**（27/29 中位为正） |
| 绑定约束 | 信号本身 | **成本**（≈ 一个月资金费） |
| 砍成本的作用 | 救不回来 | **直接决定回本周期** |

所以 carry 值得继续投入，但必须以"**低换手 + 数周持有**"的方式做。
下一步要回答的是**能不能把往返成本压下来**（挂单进场 / 减少再平衡 / 挑资金费更高的标的），
而不是继续调方向性因子。

---

## 56. 每日复盘 · 2026-10-09（07:00 CST，覆盖 10-08 交易日）

### 56.1 当日数字

| 项 | 值 |
|---|---|
| 权益 | 103,027.53 → **101,101.31** = **−$1,926.22**（此后回一部分，当前 101,311.98） |
| 成交 | **63 笔**（另 21 笔为账本对账修正，手续费 $2.96） |
| 当日手续费 | **$452.98** |
| 手续费 / 当日亏损 | **23.5%** |
| 告警 | 36 条（对账 17 / 拒单 10 / 网络降级 6 / 保护性退出 2 / 部分止盈 1） |
| 期末持仓 | 4 条腿：CRV 现货 + ACT 现货 + BAND 现货 + **CRV 永续空 −7,387** |

**手续费大头全部来自清尾与对账，不是日常换手**：
ADA $119（10 笔）、DOGE $59、SUI $55、LINK $42、BNB $33 —— 都是 carry 池标的
在事故清尾与账本对账过程中产生的，方向性策略当天几乎没有正常成交。

### 56.2 这一天真正发生的事

主线仍然**不是收益，而是把"看着在跑、其实做错事"的东西逐个挖出来**：

1. **期权数据层接入每日任务**（§45）—— 顺手修掉两个会让样本静默变少的缺陷。
2. **全样本历史补齐**（§47）：4h/1h 从 167 → 388；并发现**模拟盘与实盘不是同一份数据**
   （21 个只在实盘有 K 线、18 个两边都没有的僵尸条目）。
3. **永续腿第一次真正被对账**（§48–49）：账本把"开空"当成"卖出超量"丢掉，
   导致 carry 对冲腿永远不存在、每次重启重发对冲单。修复后 **11 条幽灵腿清零**，
   账本与交易所逐腿相等。
4. **funding 入账**（§50）：账本开始记录资金费，并接进每日任务第 5 步。
5. **对账判据改写**（§51）：从"是否为零"改成"是否漂移"——因为现金差额在构造上不可能归零。
6. **全样本复跑四策略**（§52–54）：样本外全负；做了零手续费对照实验，
   结论是**信号本身没有正期望**，手续费只是放大器（也给 `--fee-multiplier` 修了一个在 split 模式下被静默忽略的缺陷）。
7. **资金费全样本扫描**（§55）：68% 的永续中位资金费为正，但**中位回本 29.9 天**。

### 56.3 优化建议（按优先级）

| # | 建议 | 理由 |
|---|---|---|
| 1 | **carry 按它的真实约束做**：低换手 + 数周持有 + **按中位资金费筛选**，并优先压往返成本（挂单进场） | §55：毛期望为正、成本才是绑定约束；三分之一的永续中位资金费是负的 |
| 2 | **修掉对账口径**：现金一侧改用 `total` / 并入未实现盈亏，阈值与持仓名义挂钩 | 现在的"现金对账"在持仓期间随标记价漂移，量级可达 ±$500 |
| 3 | **清掉 ACT 现货腿（50,179.961）** | 它是 10-08 事故遗留，却**一直占着现货腿名额**——`leg_positions: spot 3 >= 3` 已连续多轮挡掉新的 carry 入场 |
| 4 | 把 funding 回填跑满（142/155，每日任务会自己续），并把资金费纳入持有的成本模型 | 否则 carry 的持有成本仍然只有"往返手续费"这一项 |
| 5 | **不要再在方向性因子上花力气** | §25（因子劣于随机）+ §54（零成本下仍为负）指向同一结论 |

### 56.4 一句话

**昨天一天花掉 $1,926，其中 $453 是手续费，而手续费几乎全部来自"修 bug 的代价"而不是策略交易。**
换来的是一张更干净的账：永续腿第一次对得上、funding 开始入账、四条策略在更全样本上被再次判定为无边际、
以及唯一一个有正毛期望的收益来源（carry）终于有了可执行的约束条件。**下一步的收益来源应该来自 carry 的成本结构，而不是再来一轮因子调参。**

---

## 57. 对账现金口径修好了：改用 `cashBal`，残余不再随标记价漂移（2026-10-09）

这是 §56.3 的第 ② 条。根因终于量清楚了：

**ccxt 把 OKX 的 `free` 与 `total` 都映射成 `eq`，而 `eq = cashBal + upl`。**
同一时刻实测：

| 字段 | 值 |
|---|---|
| `cashBal`（不含未实现盈亏） | **97,708.60** |
| `eq`（ccxt 的 free/total） | 97,934.29 |
| `upl`（CRV 永续空头未实现盈亏） | **+228.65** |
| `initialMargin` | 889.39 |

也就是说旧口径把**未实现盈亏**混进了现金比较，残余自然跟着标记价走 ——
前几轮观测到的 1062→611→768 那种几百美元摆动，正是 `upl` 的摆动。
而账本那侧的 cash 模型**不含未实现盈亏**（它在 equity 里），两边从一开始就不是同一个量。

**修复**：`CcxtVenue.get_account` 从原始 payload 取 `cashBal` → 新的 `AccountState.cash_bal`；
`account_snapshot` 增加 `cash_bal` 键（缺省回落 `cash`，调用方可无条件使用）；
`OMS.reconcile` 与基线两侧都改用 `cash_bal`；`VenueBaseline` 增加同名字段并兼容旧文件。

**这不是把残余变小，而是让它不再乱动**：实测 残余 OLD(eq) $804.08 → NEW(cashBal) **$1,032.73**，
新的更大，但去掉了 `upl` 那一项。剩下这 $1,033 正是此前查明的"账户里账本之外的活动"地板
（2,404 条账单 vs 账本 140 笔成交）。

新增 2 个测试（优先用 `cash_bal`、缺失时回落 `cash`），**228 tests pass**。
改动要等 runner 下次重启才生效（与 funding 入账同理），本次没有重启交易进程。

---

## 58. carry 选标的：现成的筛选器会把你送进"尖峰币"（2026-10-09）

§56.3 的第 ① 条。项目里已经有现成的筛选器 `scripts/carry_live.py`（扫全目录、按**均值**年化、减去往返成本）。
它今天给出的前三名是 **NG（年化 114%、回本 1.4 天）、TRIA、GPRO** —— 看起来很诱人。**但它们是假的。**

### 58.1 一个字段就拆穿了

把同一批合约的历史打印拿出来看分布：

| 合约 | 打印数 | 均值 | **中位数** | 正数占比 | 最常见值 |
|---|---|---|---|---|---|
| **NG** | 306 | 0.000411 | **0.000000** | 39% | `0.0000000` × **174 次** |
| **GPRO** | — | 高 | **0.00** | 45% | — |
| **ZHONGJI** | — | 高 | **0.00** | 47% | — |
| H | 103 | 0.000141 | **0.000073** | 100% | `0.0000500` × 45 次 |
| OP | 101 | 0.000040 | **0.000100** | 72% | `0.0001000` × 52 次 |

**NG 的"114% 年化"完全来自少数几次尖峰结算**：它 306 次结算里有 174 次正好是 **0**，
正数占比只有 39%。也就是说，典型情况下你**收不到钱**，那份年化是把几次尖峰按天数摊出来的。
按均值排名 = **在追尖峰**。

### 58.2 但也不能反过来只用中位数（这条是更正我自己 §55 的表述）

§55 我用"中位数"算回本，那是把**典型单次结算**当成了**每天的收益**，两者不是一回事：

- **每天实际收到的钱 = 均值**（这个窗口内所有结算之和 ÷ 天数）—— 这才是收益的正确估计量；
- **中位数 + 正数占比 = 稳健性检验**，用来判断"这份均值是不是靠少数尖峰撑起来的"。

### 58.3 正确的筛选规则（三道，全过才算）

1. **均值 > 0**（期望收益为正）；
2. **中位数 > 0 且正数占比 ≥ 80%**（收益不是靠尖峰）；
3. **按均值算的回本 ≤ 30 天**（往返成本 44.9 bps 能被合理摊掉）。

在 ≥90 条打印的 **140 个永续**上：

| 条件 | 通过数 |
|---|---|
| 均值 > 0 | 114 |
| 均值 > 0 且中位数 > 0 | 83 |
| **再加正数占比 ≥80% 且回本 ≤30 天** | **48** |

**被这道规则挡掉的，恰好是均值榜的前三名**：GPRO（均值 13.57 / 中位 **0.00**）、
NG（12.38 / **0.00**）、ZHONGJI（7.66 / **0.00**）。

### 58.4 结果与产物

合格名单前 8：**H、NES、TRIA、COAI、SLX、BRETT、SYRUP、ETHW**（30 天净收益 211→43 bps）。
完整名单（含每项的均值/中位/正数占比/回本天数/30 天净收益）已落盘 **`data/carry/candidates.json`**，
并附上"被挡掉的均值榜前十"以便复核。

**一句话**：carry 的筛选不能只看年化 —— 现成的均值榜前三名，典型结算都是 **0**。

### 58.5 已经接进筛选器（同日收口）

`scripts/carry_live.py` 原来只按**均值**排名，然后直接给一条开仓命令 —— 而均值榜第一名正是尖峰币。
现在：

1. 每行新增 **`funding_rate_median` / `median_daily_bps` / `positive_share`** 三列；
2. 新增纯函数 **`carry_eligible()`** 实现三道闸门，返回 `(是否合格, 原因)`；
3. 表格打印 `均值bps/天 / 中位bps/天 / 正数% / 年化% / 回本天 / 三道闸门`，不合格的标 **`挡下 (原因)`**；
4. 旧的"可信候选"改名为 **[旧口径·仅按均值] 首位**，被挡下时**显式写"不要照这条开仓"**；
5. 新增 **`=== 三道闸门 ===`** 区块，列出合格数量与前 N 名（含 30 天净收益），**开仓命令只从合格名单里取**。

实测输出（139 个合约）：

```
NG/USDT:USDT  日 31.312 bps  年化 114.29%  回本 1.4 天   ← 旧口径首位
              **但三道闸门挡下：positive share 64% < 80%** —— 不要照这条开仓。

=== 三道闸门 ===  合格 57 / 139 个合约
  TRIA/USDT:USDT   13.35   回本 3.4 天   30 天净 355.7 bps
  WLFI / NES / COAI / SLX / BRETT / GRASS / ETHW / SYRUP / AUCTION ...
```

新增 5 个测试锁住每一道闸门，**233 tests pass**。
（线上窗口是最近 100 次结算，与我离线用 ~100 天文件算出的 48 个略有差异：57 vs 48，形状一致。）

---

## 59. carry 重新入场：第一对按闸门选出来的配对开成（2026-10-09）

### 59.1 先清掉挡路的遗留

ACT 是 10-08 事故留下的现货腿（50,179.961，约 $512），**不属于任何 carry 配对**，却占着现货腿 3 个名额之一 ——
`leg_positions: spot 3 >= 3` 已连续多轮挡掉 carry 入场（最近一次挡下的是 **ETHW**，而它正是合格标的）。

市价平掉（成交 0.00984，余额 50,179.961 → 0），然后补正账本。
**补正时发现脚本一个缺陷并修掉**：`MARKER` 原本写死成 `MANUALRECON20261008`，
于是 `done` 集合变成**永久排除名单** —— ACT 明明刚被手工平掉，脚本仍报 `nothing to correct`。
改成**按日期生成**后立刻正确报出并落盘；当天内幂等，跨天会重新核对（已补平的行重核只会得到 `ok`）。

### 59.2 重启后它自己开了一对

现货腿名额腾出后重启 runner，**它立刻开了一对 ACT carry**：

```
fill  buy  ACT/USDT        229,123.85      ← 现货腿
fill  sell ACT/USDT:USDT   22,912 张        ← 永续腿（ctVal=10 → 22,912 张 = 229,120 币）
```

两腿同量、delta 中性。选 ACT 的原因：它**同时**在配置的 carry 池里、且通过 §58 的三道闸门
（均值 2.88 / 中位 3.01 / 正数占比 95% / 回本 15.6 天）。名义约 $2.25k（权益 ~2.2%），在 carry 腿 30% 限额内。

### 59.3 顺带修掉一个我自己引入的单位错误

新加的"venue 侧合并永续仓"把张数乘了 `contractSize` 换成**币**，而账本存的是**成交回报的单位（张）**。
于是对账报出 `position ACT/USDT:USDT: local=-22912 venue=-229120` —— 一个 **10 倍假象**
（CRV/BAND 一直对得上，只是因为它们的 `ctVal` 恰好是 1）。
统一用**张**（与每笔永续成交、以及 `paired.perp_filled` 的换算前提一致）后，**仓位差异归零**。

### 59.4 现在的账

| 项 | 值 |
|---|---|
| 账簿持仓 | 5 条腿：CRV 现货 / **ACT 现货 229,123.85** / BAND 现货 / **ACT 永续空 −22,912 张** / CRV 永续空 −7,387 张 |
| 对账 | **仓位差异 0 条**，只剩现金残余 expected 96,963.30 vs venue 95,952.66 = **$1,010.64** |
| 权益 / 风控 | 101,567.08 · `kill_switch=0` · `venue_degraded=0` |

那条现金残余正是此前查明的"账户里账本之外的活动"地板，而且现在已经是 **`cashBal` 口径**（不再含未实现盈亏）。

**一点诚实的备注**：这次开仓是我为落地修复而重启**触发**的，标的不是我手工挑的；
之后要再开新对，应当先从合格名单里选，并确认仓位大小符合"小规模验证"的意图 ——
这次 ~$2.25k 比原计划的 $200 大了一个量级。

### 59.5 持仓证据：24 小时资金费收入 **$298.94**

用交易所自己的账单（`type=8`）统计最近 24 小时：

| 合约 | 24h 资金费 | 结算次数 |
|---|---|---|
| CRV-USDT-SWAP | **+$293.3462** | 16（**小时结算**） |
| BAND-USDT-SWAP | +$5.5905 | 6（4 小时） |
| **合计** | **+$298.94** | — |

相对永续腿名义（CRV 7,387 张 ≈ $2.88k + BAND ≈ $0.49k，共约 **$3.4k**），
这相当于**一天 8.8% 名义** —— 远高于 §55/§58 用历史算出的"中位 3 bps/天、回本 15–30 天"。

**这个差距本身就是结论**：最近 24 小时正处在**资金费尖峰区间**（CRV 单次结算约 $18），
而尖峰恰恰是 §58 判定"不能用来选标的"的那部分收益。所以：

1. carry 确实在赚钱，而且**真金白银进了账单** —— 从"有结论"走到"有持仓证据"；
2. 但**不能**把 $299/天 当成可持续收益率：它对应的是均值那根尾巴，我们选标的时特意用中位数剔掉了；
   可持续的部分仍然是 **~3 bps/天**；
3. 一个**可检验的推论**：尖峰退去后，CRV 的日均收入应回落到 **$2–3 量级** ——
   未来几天用账单就能直接验证。这条同时检验 §58 的选标规则和"尖峰不可持续"这个判断。

### 59.6 另一个疑问：配对为什么是 $2.25k 而不是 $200

查清楚了 —— **两条路径的定仓哲学不同**：

| 路径 | 定仓方式 |
|---|---|
| `scripts/carry_live.py` 的提示 | 手工脚本的**固定名义**（$200） |
| runner 实际走的 | `risk_based_qty = equity × risk_pct% / |entry − stop|` |

ACT 属于 `tiers.carry`：`risk_per_trade_pct=0.20`、`max_order_notional_pct=4.0`、`max_position_pct=5.0`。代入：

- 风险预算 = 101,500 × 0.20% = **$203**
- 下单量 402,735.93 ACT ⇒ 隐含止损距离 **$0.000504**（入场价 0.009771 的约 5.1%）
- 402,735 × 0.0098 = **$3.93k = 权益的 3.9%** —— **正好卡在 4% 名义上限之下**

所以**真正决定仓位大小的是名义上限，不是风险预算**。另外现货腿只成交了 229,123.85 / 402,735.93（OKX 市价部分成交），
对冲腿按**实际成交额**下（22,912 张 × ctVal 10 = 229,120 币）—— 配对仍同量、delta 中性 ✓。

**但这里暴露一个设计问题**：对一个 delta 中性的配对，**用"价格止损距离"定仓在概念上就是错的** ——
这种仓位的风险不是价格方向，而是**资金费翻转 / 基差走阔**。于是仓位大小被 ACT 的历史波动率（ATR）决定
（ATR 小 → 止损近 → 名义大），与"这个配对能赚多少资金费、要付多少往返成本"毫无关系。
`tiers.carry` 的 `max_position_pct=5.0` 注释写的是 *per symbol, in pair notional*，说明当初就想过按名义定仓，
只是定仓公式没跟上。

**结论：不是 bug，是定仓口径不匹配。** carry 配对应当按"配对名义占比"或"资金费净收益/成本"来定仓 ——
这是下一步可执行的改动点。

### 59.7 已改：carry 定仓口径 → 配对名义（2026-10-09）

三处改动：

| # | 改动 | 说明 |
|---|---|---|
| 1 | `TierLimits` 新增 `carry_notional_pct` | >0 时 carry 入场按**权益的该百分比**折成配对名义；0 回落到 `max_order_notional_pct`（旧行为，向后兼容） |
| 2 | `execution/sizing.py` 新增 `carry_pair_qty()` | runner 的 carry 分支改用它；**其他策略仍走 `risk_based_qty`** —— 方向性风险确实由止损距离刻画，那条公式是对的 |
| 3 | `[tiers.carry] carry_notional_pct = 2.0` | 约 $2.0k/对，首几对自证期间刻意保守 |

效果（同样在权益 101,500 / ACT 价 0.0098 下）：

| 口径 | 数量 | 名义 |
|---|---|---|
| 旧（按止损距离，5.1%） | 402,736 ACT | $3.93k（顶到 4% 上限） |
| **新（按配对名义 2%）** | **207,143 ACT** | **$2,030** |

新增 4 个测试，其中一条专门锁住"**与波动率无关**"：同一价格下，1% 止损与 10% 止损在旧公式里差 10 倍、
在新公式里完全相同 —— 这正是这个改动要消除的东西。**237 tests pass**。

改动对**新开仓**生效，需要 runner 重启；当前现货腿 3/3 满，本来也开不了新对，所以不急。

### 59.8 已实现收入：账户里出现了一个天然对照

新增 `scripts/funding_income.py`：从交易所**账单**（`type=8`）按合约汇总**实际收到**的资金费，
除以当前永续名义得到 bps/天，按 UTC 日幂等落盘到 `data/funding/income.jsonl`，并接进每日任务。

它补的是此前一直缺的那一侧 —— **研究的都是费率，没人统计实际到账多少**。第一次运行就给出一个天然对照：

| 配对 | 已实现收入 | 结算次数 | 名义 | **bps/天** |
|---|---|---|---|---|
| CRV-USDT-SWAP（较老） | +$297.01 | 19 | $2,586 | **1,148.73** ← 尖峰 |
| **ACT-USDT-SWAP（今晨刚开）** | +$0.72 | 1 | $2,266 | **3.19** ← 稳态 |

**同一时间、同一交易所、同一资金费机制**，差别只来自标的当前的资金费水平。而
**ACT 的 3.2 bps/天 正是 §55/§58 用中位数预测的稳态量级**，CRV 的 1,149 bps/天 则是均值那根尾巴。

这让"尖峰不是常态"从断言变成了**同一账户内的对照观测**，也让 `$299/24h` 那个数字有了正确解读：
**它属于 CRV 这一对的尖峰，不属于 carry 策略的常态收益。**

新增 2 个测试（收入/bps 换算、无持仓时不除以不存在的名义），**239 tests pass**。

**carry 的收益画像现在两侧都有数**：稳态 **~3 bps/天**（ACT 实测）、尖峰可以**两个数量级**（CRV 实测）。
下一步等 `income.jsonl` 累积几天，用**已实现收入**校准 `carry_notional_pct`，同时验证尖峰是否回落。

### 59.9 成本端实测：真实的入场成本是 **1.46%**，不是假设的 0.449%

拿新开的 ACT 配对把成交价与**同一分钟的 K 线**对照（入场时刻 10-09 03:46 UTC）：

| 腿 | 我们的成交 | 当时市场 | 差 |
|---|---|---|---|
| 现货买 | 均价 **0.0099004** | ≈0.00977（1h bar o .00963 / h .00977 / c .00977） | **+1.33%** ❌ |
| 永续卖 | **0.0097711** | ≈0.00976 | +0.11%（略好的价）✓ |

**成本几乎全部来自现货腿的市价冲击**，不是手续费，也不是基差。总账（配对名义约 $2,250）：

| 项 | 金额 | 占比 |
|---|---|---|
| 滑点/冲击 | ~$29.8 | **1.33%** |
| 手续费 | $3.39 | 0.15% |
| **合计** | **$33.2** | **1.46%** |

而项目里所有 carry 结论所依据的往返成本假设是 **44.9 bps（0.449%）** —— **实测是假设的 3.3 倍**。

后果直接：按 ACT 实测的稳态资金费 3.19 bps/天（$0.72/天 on $2,266），
**光回本就需要约 46 天**；而按 44.9 bps 假设只要 ~14 天（§55/§58 的"回本 15 天"正是基于后者）。

原因大概也能看清：§41 那个 44.9 bps 是按**价差 + 费率**估的，没有计入**市价单在小币种、
低流动性时段吃掉的深度**（本次入场发生在 03:46 UTC，正是流动性低谷）。

**这不是记账错误，是测量对象的错误**：我们一直用"往返成本"这个抽象常数做决策，
而它在本账户上取决于标的深度与下单方式。

**立即可做的两件事**：① 现货腿改**限价/PostOnly**，把 1.33% 的冲击换成交风险；
② 选标的时加一道**深度闸门**（按盘口估算 $2k 名义的冲击 > 50 bps 就不做）。

### 59.10 已做②：深度闸门（2026-10-09）

三处改动：

1. `execution/sizing.py` 新增纯函数 **`book_impact_bps(levels, qty, mid, side)`** —— 按档位吃单、
   算量价加权成交价相对中点的偏离；**吃不满整笔就返回 `None`**
   （"测不出来"必须当成"太薄"，不能当成零冲击 —— 1.33% 那次就是这么漏过去的）；
2. `CcxtVenue.entry_impact_bps(symbol, qty, side)` 读盘口（OKX 返回 `[价, 量, 笔数]`，取前两项）；
3. runner 的 carry 入口在**风控闸门与配对数上限之后**加一道深度闸门：
   `per_trade.max_entry_impact_bps = 50`，估算冲击 >50bps 或返回 `None` 即拒绝并打警告（0 关闭）。

**上线前实盘验算**（按 `carry_notional_pct=2%`、权益 $101,550 估算每对的量）：

| 标的 | 价位 | 每对数量 | 冲击 | 判定 |
|---|---|---|---|---|
| **ETHW/USDT** | 0.2696 | 7,533 | **n/a（盘口吃不满）** | **挡下** |
| **NOT/USDT** | 0.00044 | 4,615,909 | **325.6 bps** | **挡下** |
| LTC/USDT | 64.19 | 32 | 1.6 bps | 通过 |
| GRAM/USDT | 1.406 | 1,445 | 3.6 bps | 通过 |
| BCH/USDT | 280.86 | 7 | 1.8 bps | 通过 |
| BTC/USDT | 82,578 | 0.02 | 0.2 bps | 通过 |

两个发现值得单独记住：

1. **runner 反复尝试入场的 ETHW，恰好是被判"太薄"的那一个** ——
   此前 `leg_positions: spot 3 >= 3` 一直挡着它，掩盖了它**本来也进不去**；
2. **"合格名单"里存在深度不合格的标的**（NOT 的 $2k 冲击是 **3.25%**）——
   说明 §58 那三道闸门只管"资金费值不值得做"，**"做不做得进去"必须另有一道**，现在补上了。

新增 6 个测试，**245 tests pass**。需要 runner 重启才生效（当前现货腿 3/3 满，本来也不会开新对，所以不急）。

### 59.11 把深度闸门接进筛选器：资金费与深度是**反向**的

`carry_live.py` 新增 `--pair-notional`（默认 $2,000/对），现在同时给两个独立结论：
「值不值得做」（资金费三闸门）与「做不做得进去」（深度闸门）。

**结果非常干净地对立**：

| 闸门 | 通过数 |
|---|---|
| 资金费三闸门 | **57 / 139** |
| 再过深度闸门（$2k/对） | **21 / 57** |

而被深度挡下的**恰恰是资金费榜的前六名**：
**TRIA、WLFI、NES、COAI、SLX、BRETT 全部 `n/a`（盘口根本吃不满 $2k）**。

能进去的 21 个是资金费的**中游**、但盘口真实：

| 合约 | 均值 bps/天 | 回本天 | 冲击 bps |
|---|---|---|---|
| AUCTION | 3.00 | 15.0 | 20.4 |
| BAND | 2.82 | 15.9 | 20.1 |
| GRAM | 2.82 | 15.9 | 3.6 |
| NEO | 2.45 | 18.3 | 9.0 |
| DOGE | 2.15 | 20.9 | 2.9 |
| COMP | 2.15 | 20.9 | 12.3 |

**这个反向关系本身是有含义的**：资金费高往往正因为盘口薄（深度差 → 波动大 → 资金费偏），
所以「按资金费排名选标的」在结构上就是在**选最进不去的那些**。

另注：AUCTION/BAND 的约 20 bps 冲击已接近 carry 腿 `max_spread_bps=25` 的量级 ——
真实的约束其实是**三道**：资金费、深度、价差，三者都会继续压小可做集合。

这也顺带解释了一件事：实盘只开了 CRV/BAND/ACT 这类**中游资金费**标的，并不是因为系统在追资金费榜。

### 59.12 用**实测往返成本**替代 44.9 bps 假设：排序被翻过来了

上一步发现"资金费越高越进不去"；这一步把**成本也实测**：筛选器现在对每个候选算
**现货腿冲击 + 永续腿冲击（按 ctVal 换成张后的盘口）+ 2×费率**，作为"实测往返"，
再用 `均值 bps/天 × 持有天数 − 实测往返` 排序（进出各记一次盘口成本，是一项**明示的假设**）。

| 闸门 | 通过数 |
|---|---|
| 资金费三闸门 | 57 / 139 |
| + 深度闸门 | 21 / 57 |
| **+ 实测成本为正** | **13 / 57** |

**排序被翻过来了** —— 按资金费排是 AUCTION 3.00 / GRAM 2.82 在前，按**净收益**排则是：

| 合约 | 均值 bps/天 | **实测往返 bps** | **30 天净 bps** |
|---|---|---|---|
| **DOGE** | 2.15 | 16.4 | **48.2** |
| SUI | 1.83 | 13.6 | 41.4 |
| BNB | 1.74 | 12.6 | 39.6 |
| NEAR | 1.74 | 14.2 | 37.9 |
| LTC | 2.03 | 28.2 | 32.6 |
| AUCTION | 3.00 | **57.8** | 32.1 |
| GRAM | 2.82 | **72.6** | 12.0 |

原因是实测往返的跨度很大：**BNB 12.6 / SUI 13.6 / NEAR 14.2 / DOGE 16.4 bps 都在旧假设 44.9 之下**，
而 AUCTION 57.8、GRAM 72.6 **远在其之上** —— 那个常数对深盘标的偏保守、对薄盘标的**严重低估**。

**绝对值也要看清**：30 天净 12–48 bps，折算到 $2k/对 是**每对每月 $2.4–9.6**。
这就是 carry 稳态的真实量级 —— 小，但为正，且是**扣掉实测成本之后**为正。

**选标的现在有四道**：资金费（值不值得做）→ 深度（进不进得去）→ 实测往返（做完赚不赚）。
下一步开新对应从这个 13 个净收益名单里取（当前首选 **DOGE**），而不是看资金费榜。

### 59.13 逐对结算：carry 到底赚没赚，以及钱是从哪来的

第一次把每对 carry 的**三项**放在一起算：**资金费（账单）+ 手续费（成交）+ 基差损益（两腿盯市差）**。

| 配对 | 基差损益 | 手续费 | 资金费 | **净** |
|---|---|---|---|---|
| **CRV** | **−$54.10** | −$4.35 | **+$311.39** | **+$252.95** |
| **ACT** | **−$28.48** | −$3.39 | +$0.84 | **−$31.03** |
| 合计 | −$82.58 | −$7.74 | +$312.23 | **+$222.35** |

**两条结论**

1. **carry 赚钱时，钱几乎全部来自资金费尖峰**：CRV 的 +$311 撑起了 +$253 的净收益；
   CRV 现货这期间从 0.39237 跌到 0.3412（约 −13%），两腿 delta 中性让方向不影响结果，
   但**永续相对现货从 −0.16% 走到 +1.96% 溢价**，这一项基差就吃掉了 $54。
2. **基差是第二类风险，两对都在它上面亏钱**（−$54 / −$28）——
   而 ACT 那 −$28 **正是 59.9 测到的 1.33% 入场冲击的直接后果**：
   按它现在 $0.7/天 的资金费，要**约 44 天**才能爬出来，与 59.9 估算的 46 天吻合。

**要让它可持续，必须同时做两件事**：把入场成本压到远小于稳态资金费（深度闸门已做、限价未做），
以及**减少基差敞口**（当前两对合计 −$82，比任何一对的月度稳态资金费还大）。

**注意**：+$222 里占比最大的那一项来自一个尖峰，**不可外推**。

### 59.14 第四道闸门：基差（2026-10-09）

59.13 显示**基差是两对合计 −$82 的最大可归因损失项**，于是补上第四道闸门。

原理一句话：carry 配对是"现货买在 S + 永续空在 P"，两腿收敛时**恰好实现 P − S** ——
所以**永续高于现货是收入、低于现货是进场就先付的钱**。

实测复现：**ACT 进场时永续比现货低 130.6 bps**（就是那 $29.8 滑点）；CRV 的溢价从 −0.16% 一路走到 **+1.96%**，反向吃掉 $54。

改动：`carry_live.py` 新增纯函数 `basis_bps(spot, perp)` 与 `--max-basis-discount-bps`（默认 10），
报告加"基差 bps"列与独立区块，**四项闸门全过才给开仓命令**。

| 闸门 | 通过数 |
|---|---|
| 资金费三闸门 | 54 |
| + 深度 | 17 |
| **+ 基差** | **11** |

被基差挡下的 6 个全是永续贴水：COMP −12.6、ETHFI −11.5、SHIB −20.6、ADA −25.3、SUSHI −13.2、THETA −55.5。

最终 11 个按 30 天净排序：

| 合约 | 均值 bps/天 | 实测往返 | **基差 bps** | 30 天净 |
|---|---|---|---|---|
| DOGE | 2.12 | 17.2 | −7.0 | **46.4** |
| LTC | 1.98 | 13.2 | −9.4 | 46.0 |
| SUI | 1.79 | 16.6 | −9.4 | 37.0 |
| NEAR | 1.68 | 14.2 | **+4.2** | 36.2 |
| ETC | 1.77 | 19.4 | **+8.6** | 33.8 |
| LINK | 1.56 | 13.2 | 0.0 | 33.7 |
| AUCTION | 3.00 | 63.2 | +2.7 | 26.7 |
| GRAM | 2.82 | 73.0 | −6.2 | 11.6 |
| HBAR | 1.94 | 49.8 | −7.7 | 8.5 |
| NEO | 2.45 | 80.0 | +12.7 | **−6.4** |

NEO 是最好的反例：资金费 2.45 bps/天、基差还是正的，但**实测往返 80 bps** 把它压成负的 ——
正是这道闸门该拦的样子。

新增 4 个测试（含**复现真实 ACT 进场 −130.6 bps**），**249 tests pass**。

**选标的现在四道**：资金费（值不值得做）→ 深度（进不进得去）→ 基差（进场先付不付钱）→ 实测成本（做完赚不赚）。

### 59.15 四道闸门全部落到**下单路径**（2026-10-09）

收口动作。此前只有"资金费"在策略里、"深度"在半路（59.10 加的），而**基差只活在离线筛选器**——
可**筛选是快照、下单是现在**，这正是 59.9 那 1.33% 的根因。本次：

1. `basis_bps` 从脚本移到 `src/ta/execution/sizing.py` —— **runner 与筛选器共用同一条规则**
   （脚本侧保留 re-export，不破坏既有调用）；
2. `PerTradeLimits` 新增 `max_basis_discount_bps`（默认 10，0 关闭），`config/risk.toml` 显式写出；
3. runner 的 carry 入场在**深度闸门之后**再加基差闸门：取对冲场地的永续最新价与现货标记比，
   **永续低于现货超过 10 bps 就拒绝**并打警告。

**一个刻意的不对称**（已写进注释，免得日后被当成笔误）：

| 闸门 | 读不到数据时 |
|---|---|
| 深度 | **拒绝** —— 未知深度可能是无界的 |
| 基差 | **放行** —— 读不到永续价时对冲腿本身也下不出去，配对执行器会按自己的逻辑失败 |

新增测试：配置确实暴露该阈值、且 **runner 与筛选器走的是同一个函数**。**250 tests pass**。
需要重启才生效（当前现货腿 3/3 满，本来也开不了新对）。

至此 carry 的入场在下单那一刻同时复核：**资金费**（策略层）、**深度与基差**（runner）、**配对名义**（定仓）。
筛选器的结论与实际下单之间不再有未被检查的窗口。

### 59.16 一个需要拍板的结构问题：选出来的前四名不在 carry 池里

顺着"池子里到底有没有该做的标的"查下去，发现一件比参数更重要的事：

**四道闸门算出的净收益前四名 —— DOGE(46.4)、SUI(37.0)、NEAR(36.2)、LINK(33.7) —— 一个都不在 `tier: carry` 的 31 个池子里。**
它们现在属于 `alts`（方向性策略池）。

**原因不是遗漏，而是一条为真实事故写的安全规则。** `Config.validate` 明确禁止其他策略与 carry 共用标的，
注释记着 2026-10-08：ADA/DOGE/LINK/LTC/NEAR/PEPE/SUI 同时躺在两个池子里，于是 `trend_pullback` 平掉"它的 ADA"
只卖了**现货腿**，把 carry 的永续空头留成**裸空**。所以这些标的是**被主动移出 carry 池**的。

**代价现在可以量化**：

| | |
|---|---|
| 当前 carry 池 31 个中，同过四道闸门的 | 约 **5 个**（LTC 46.0、ETC 33.8、AUCTION 26.7、GRAM 11.6、HBAR 8.5） |
| 净收益最高的四个 | **被排除在外** |
| 占用它们的策略 | 方向性四条 —— **全样本样本外全负**（−0.54%…−1.08%，§54 还证明零成本下也为负） |

**在证据上这是"一边倒"的**：把标的从没有正期望的腿让给有正期望的腿。但**这是一次策略配比决定，不是参数微调** ——
它改变两条腿各交易什么、需要重启，并且必须在**保持互斥规则**的前提下改（把标的从 `alts` 移到 `carry`，
而不是让两边共用）。

**本轮没有动配置**，留作决定。若同意，改动很局部：`config/app.toml` 的 `tiers.alts` 删掉这四个、
`tiers.carry` 加进这四个，`Config.validate` 会自行校验互斥与四腿之和，然后重启 runner。

---

## 61. 每日复盘 · 2026-10-09（23:15 CST，覆盖 10-09 交易日）+ 今日改动汇总

### 61.1 当日数字

| 项 | 值 |
|---|---|
| 权益 | 101,067.79 → **101,594.23** = **+$526.44（+0.52%）** |
| 资金费收入 | **+$322.38**（CRV +320.76 / 21 次结算；ACT +1.62 / 3 次） |
| 成交 | **3 笔**：11:39 ACT 事故遗留 50,179.96 @ 0.00984（对账修正）；11:46 ACT 现货买 229,123.85 @ 0.0099 + ACT 永续空 22,912 张 @ 0.009771（新 carry 对开成） |
| 当日手续费 | **$3.39**（占当日收益 0.6%） |
| 告警 | 对账现金残余（结构性地板，~$1,010，稳定不漂移）／0 kill switch／OKX `50001` 维护 3 次 |
| 期末持仓 | 5 条腿：CRV 现货+永续空、ACT 现货+永续空、**BAND 现货（无对冲）** |

**与昨天对照**：昨天 $452.98 手续费几乎全是"修 bug 的代价"；今天只有 $3.39，
而 **+$322 是 funding 赚来的** —— 这是本项目第一次出现"收益来自 carry 的成本结构，
而不是来自交易次数"。

### 61.2 今天真正发生的事（= changelog 里 §62–§71 那些引用的实体）

| # | 改动 | 结果 |
|---|---|---|
| 1 | 免责声明生效 → **TradFi 腿第一次可交易** | 最小额往返实测 **21 bps**（两笔各 10 bps taker + 每边 0.5 bp），比 carry 的 44.9 bps 便宜一半 |
| 2 | **TradFi 腿上线**：13 标的 + 短 warmup(10/20) 日线 Donchian + `[tiers.tradfi]` 全库最紧档位；`universe` 加 `1d` | 长 warmup 在 85 根日线上永远不出信号（回测 49 根 → 0 笔），短 warmup 给 28 笔 / −0.14% / PF 0.34。**它是为了收执行数据，不是已证实的边缘** |
| 3 | **标的身份问题关闭**：`instCategory` 只在实盘目录可信（模拟盘把 XRP 标成股票）；OKX 股票计划始于 2026-07-15 | XPO/XUC 钉回现货腿、XCU 归 TradFi；新增 `scripts/leg_identity.py` 每日复核（29 行 / 0 问题） |
| 4 | **深度闸门从 carry 专属扩到所有入场** | 下单前"查不到深度 = 拒绝"；新增 6 条测试，含真跑 `_handle_signal` 的覆盖测试 |
| 5 | **carry 被动入场上线**：买一挂 post-only → 等 6s → **必须撤单并确认** → 未成交才回退市价 | 测试当场抓到"回退复用了同一个 client order id"→ 会被 OMS 幂等判成重复而**静默不成交** |
| 6 | **post-only 的真实语义被纠正** | 不是"下单即拒"，而是"接受后由交易所杀掉"（`cancelSource=31`、零成交）；撤单可用（`cancelSource=1`） |
| 7 | changelog `status` 一次性回填 **18 条** | 只剩 1 条真 open（待你决定的那条），看板不再把已解决的事显示成待办 |
| 8 | **磁盘日线历史冻在 10-05** → 期权 30 天已实现波动率是常数 | 新增 `merge_bars()` + 每日 `refresh_daily_bars.py`（900→903 根） |
| 9 | **IV−RV 期限对齐** | SOL 拿 7 天 IV 比 30 天 RV → 修正后 **−13.6pp 翻成 +2.5pp**；中位 −9.66 → −1.16 |

### 61.3 优化建议（按优先级）

| # | 建议 | 理由 |
|---|---|---|
| 1 | **处置 BAND 现货腿（2,084.13 BAND ≈ $471）**：建议照 10-09 早上 ACT 的先例——平掉 + 从 `data/carry/pairs.json` 去掉那条 "seeded" 记录 + 重启 | 它的永续在 10-08 晚清尾时已被平掉，**从那时起就是裸多**：不产生 funding、占着现货腿 3 个名额之一、而 `pairs.json` 仍记着"配对开着"。**平掉 vs 重新对冲是策略选择，需你点头** |
| 2 | 让 carry 名额真正可用 | 与第 1 条同源：现货 3/3 被 CRV/ACT/BAND 占满，SUI/NEAR/ETHW/LTC 整晚被 `leg_positions: spot 3 >= 3` 挡下，而它们是通过四道闸门的 |
| 3 | 期权：等 10-13/14 的新鲜样本，期间**不要动参数** | 今天刚修掉 RV 冻结与期限错配两个偏差，样本要重新起算 |
| 4 | TradFi：保持最小仓位，别急着调参数 | 13 个标的里只有 1–3 个有双边报价，信号大多会被深度闸门挡掉，数据先天稀疏 |
| 5 | 继续**逐字段**核对"看起来在攒数据"的东西 | 今天两次收获（RV 冻结、期限错配）都是这么挖出来的；文件在、行数在，不代表那个字段在 |

### 61.4 一句话

**今天第一次是 funding 在赚钱（+$322）而不是手续费在烧钱（−$3.39）；
但一条事故遗留的裸多占着名额，carry 一整天没开出第二对新仓。**

### 61.5 关于 §62–§71 的引用

今天 changelog 里出现的 "README §62 … §71" 属于**先行引用**（留痕先写、章节后落）。
本节 61.2 就是它们的实体；后续改动继续按此格式追加，已写下的编号不回溯修改。

---

## 62. 执行 §61.3 第 1 条：BAND 裸多处置完毕（2026-10-10 01:05 CST）

### 62.1 为什么它是个问题

BAND 的永续在 **10-08 晚清尾**时就被平掉了，但现货腿 2,084.132066（≈$478）
从那时起一直是**裸多**：

- **不产生任何 funding**（没有空头可收）；
- **占着现货腿 3 个名额之一** —— 整晚 `leg_positions: spot 3 >= 3` 把 SUI / NEAR /
  ETHW / LTC 全挡在门外，而它们都是过了四道闸门的标的；
- `data/carry/pairs.json` 里 10-08 18:10 用 `--seed-state` 种下的记录仍写着它与空头成对
  （**当时对确实是全的**：spot 2,084 对 short 3,182 张），于是 runner 一直以为它对冲完好，
  退出路径还会去平一个不存在的空头。

### 62.2 执行（照 10-09 早上处置 ACT 遗留的先例）

| 步骤 | 动作 | 结果 |
|---|---|---|
| 1 | 市价卖出全部 BAND | 成交 **2,084.132066 @ 0.227898861944**，手续费 **$0.474971**，到手 ≈ **$474.56**；BAND 余额归零 |
| 2 | `scripts/reconcile_ledger_to_venue.py --yes` | 只追加一笔向下修正 sell @0.2278（账本 append-only，先备份） |
| 3 | `data/carry/pairs.json` 去掉 BAND 那条（先备份） | 剩 **ACT / CRV** 两对 |
| 4 | 重启 runner（91076 → **92241**） | 日志 `restored carry pair state [pairs=['ACT/USDT', 'CRV/USDT']]` |

### 62.3 效果（下一个 tick 就能看见）

`leg_positions: spot 3 >= 3` **不再出现**。carry 重新逐个评估标的，而拒绝理由回到业务闸门本身：
BAND 深度不足（`impact_bps=None`）、ETHW 冲击 500.6 bps > 50、SUI/NEAR 永续贴水
（−18.9 / −10.4 bps，超基差闸门）。**这正是想要的形状：被挡是因为标的不过关，而不是因为名额被一条死腿占着。**

### 62.4 顺带发现

对账脚本自身在 ACT 那条报 `ledger=-22912 venue=-229120` —— **10 倍的合约单位差**
（ccxt 的 `contracts` 与账本记的"张"口径不同；runner 侧已统一为张，这个脚本没跟上）。
它只做向下修正，所以不会误伤，但报表里 ACT 那行是假差异，待修。

---

## 63. 被动入场的第一次真仓就翻车：LTC 现货被买成两倍（2026-10-10 01:45 CST）

### 63.1 事故

名额释放后 carry 立刻开出第三对（LTC），而**第一次真跑的"被动入场"把现货腿下错了**：

| 时刻 | 动作 | 结果 |
|---|---|---|
| 23:03:11 | 提交 post-only 买单 31.16579 LTC @ 63.34 | **23:03:14.7 成交**（fee 1.5792） |
| 23:03:16.5 | **又**发市价买 31.161523 @ 63.3187 | 成交（fee 1.9731） |
| — | 现货合计 **62.327313 LTC**，永续空只有 **31.1 张（=31.1 LTC）** | **约 31.23 LTC ≈ $1,977 裸露** |

### 63.2 根因（实现缺陷，不是交易所）

`_passive_spot` 用"**挂单列表里还有没有这个 client order id**"判断挂单是否还在，
并把"连续两次不在列表里"当作"已撤销"→ 于是走市价回退。

**但成交的单子同样不在挂单列表里** —— 撤销与成交在挂单列表上完全同形。
当一笔**已成交**的单还没有被 `sync()` 抽干（或被 runner 自己的 tick 先抽走）时，
探测就会把它读成"撤掉了"，然后买第二次。
原来的"连续两次缺席"只防住了"刚挂上还没被列出"这一侧，**完全没防住"已成交所以不在列表里"这一侧**。

### 63.3 处置

1. **立刻平掉多余的现货**：市价卖 31.227313 LTC @ 63.2413（fee 1.9749）→ LTC 回到 **31.10**，与对冲等量。
   本次事故净成本约 **$6.3**（价差 ~$2.4 + 手续费 ~$3.95），裸露持续约 33 分钟。
2. **补账本**：`reconcile_ledger_to_venue.py --yes` 追加那笔修正卖；`pairs.json` 里 LTC 的
   `base_qty` 从 31.161523 改成实际的 **31.1**（否则将来平仓会按 31.16 去卖，而持仓只有 31.1）。
3. **修代码**（v0.4.1）：新增 `CcxtVenue.order_state()` / `OMS.order_state()`，
   直接读**订单自己的记录**（`state` + `accFillSz`）作为唯一权威；删掉"缺席即撤销"的启发式；
   **"查不到" = 拒绝回退**（`unknown` → 中止这一对，不发市价、不发永续）。

### 63.4 教训

**"不在挂单列表里"是二义信号**（撤销与成交同形），不能拿来当判定依据 —— 这和 §62 里
"刷新脚本刷新的不是你读的那份文件"属于同一类错误：**验证必须针对你要确认的那件事本身，
而不是一个看起来相关、实则多义的旁证。** 补的两条测试正好覆盖本次场景
（成交但不在挂单列表 → 只准发一次单；订单状态读不出来 → 拒绝回退）。

---

## 64. 权益口径统一：开一对新 carry 不再被自己的风控当成日亏 −2%（v0.4.2，2026-10-10 02:55 CST）

### 64.1 事故（巡检发现）

2026-10-09 15:05:56Z 日志出现 `trading paused: daily loss -2.00%` —— **而今天权益是涨的**
（101,067.79 → ~101,590）。查清后是两个权益口径在打架：

| 口径 | 永续空头怎么算 | 实测值 |
|---|---|---|
| 账本 `PositionBook.equity()` | 只算**未实现盈亏** | **101,544.17** |
| runner `_prepare_risk_account()` | `cash + Σ(所有持仓 qty×标记价)` → 空头按**名义**全额扣 | **96,720.38** |

两者相差 **$4,823.79**，正好等于 CRV/ACT/LTC **三条对冲腿的名义合计**。
而日亏熔断拿 runner 这个数去比它自己的日内基线，于是：

**每开一对新 carry，就凭空多出一个对冲腿名义的"当日亏损"。**
23:03 新开 LTC 对（对冲腿名义 $1,969 / 权益 $101,600 = **1.94%**）→ 越过 −1.5% 的
`daily_loss_stop_open_pct` → 新入场被全部暂停。**名义再大一点就会跨过 −3% 的
`daily_loss_flatten_pct`，触发 `flatten_all` —— 开一对新仓会导致整个账本被强制平掉。**

### 64.2 修法：一条规则，一处实现

新增 `portfolio.marked_value(symbol, position, price)`（**现货按名义、永续只算未实现盈亏**），
让 `PositionBook.equity()` 与 runner 的 `_prepare_risk_account()` **共用同一实现** ——
这是防它再次漂移的办法，而不是把两个公式各自改对一遍。

附带纠正：`effective_risk_equity` 也是同一口径，所以此前持仓期间的下单规模一直按
**被低估约 4.7%** 的权益在算，现在回到真实值。

### 64.3 测试与生效

- `marked_value` 单测：空头 2 @60 标到 63 = **−6.0 的盈亏**，不是 −126 的名义。
- **回归测试**：开一条与现货等量的对冲腿（在标记价上开、现金相应减少）**不得改变 `risk_equity`**
  —— 旧公式下这里掉 $126，正是那个假亏损的缩影。
- 275 tests 全绿；已重启（92494 → **93128**，`restored carry pair state [ACT, CRV, LTC]`），
  重启后 `ta_entries_paused = 0`、日志再无 `trading loss`/`daily loss`，
  carry 的拒绝理由回到深度与基差闸门。

### 64.4 还留了一处同族问题

`Position.unrealized()` = `(标记价 − 均价) × 张数`，**没有乘合约面值 `ctVal`**：
CRV/LTC（ctVal=1）看不出问题，但 **ACT 是 ctVal=10**，所以它那一腿的盈亏被低估约 10 倍
（约 $10 量级）。这与 10-09 修过的"张↔币"是同一族单位问题，已单列一行待修 ——
它影响的是权益/盈亏数字，不影响下单量。

---

## 65. 每日复盘 · 2026-10-10 07:00 CST（覆盖 10-09 **完整**交易日）

> §61 是当晚 23:15 写的**当日中途版**；本节是交易日收口后的完整版，补上最后 45 分钟发生的
> 两起事故（BAND 裸多处置、LTC 双倍现货）与随之而来的风控修复。

### 65.1 当日数字

| 项 | 10-09 | 对照 10-08 |
|---|---|---|
| 权益 | 101,067.79 → **101,556.61** = **+$488.82（+0.484%）** | −$1,926.22 |
| 日内区间 | 峰值 101,611.04 / 谷值 101,062.81（极窄） | — |
| 资金费收入 | **+$322.38**（CRV +320.76 / 21 次；ACT +1.62 / 3 次） | — |
| 成交 | **8 笔**（其中 3 笔是账本对账修正；真实下单 5 笔） | 63 笔 |
| 当日手续费 | **$7.92** | **$452.98**（−98%） |
| 手续费 / 当日损益 | **1.6%** | 23.5% |
| 告警 | 34 条（对账 32 / 网络降级 1 / **trading paused 1**） | 36 条 |
| 期末持仓 | 6 条腿：CRV、ACT、**LTC** 三对（现货多 + 永续空，逐腿对齐） | 4 条腿（含 BAND 裸多） |

**两句话对照昨天**：昨天 $452.98 的手续费几乎全是"修 bug 的代价"、当天亏 $1,926；
今天手续费只有 **$7.92**，而**收益的 66% 来自 funding**（322/489）。
**这是本项目第一次由 carry 的结构而不是交易次数决定损益。**

### 65.2 当天真正发生的事

主线是**把"看起来在跑、其实做错事"的东西逐个挖出来**，其中三件是当天新挖的：

1. **TradFi 腿通电**（§63）：免责声明生效后订单路径打通，实测往返 **21 bps**；
   13 标的 + 短 warmup 日线策略以全库最紧档位上线（回测 28 笔 / −0.14%，**为收数据而非证实边缘**）。
2. **标的身份问题关闭**（§62 段）：`instCategory` 只在实盘目录可信、股票计划始于 2026-07-15，
   XPO/XUC 钉回现货、XCU 归商品币；新增 `leg_identity.py` 每日复核（29 行 / 0 问题）。
3. **两处"数据在攒、其实没攒"**：磁盘日线冻在 10-05（期权 RV 成了常数）→ `merge_bars` + 每日刷新；
   IV−RV 期限错配 → SOL 的"溢价"由 −13.6pp 翻成 **+2.5pp**。
4. **BAND 裸多处置**（§62）：它的永续在 10-08 晚清尾时已被平掉，现货腿却从那时起是**裸多**
   （不产 funding、占着现货名额、`pairs.json` 还写着成对）。市价平掉 $474.56 → 补账本 →
   去掉 seeded 记录 → 重启；**名额立刻释放**，当晚就开出了 LTC 那一对。
5. **LTC 被动入场把现货买成两倍**（§63）：post-only 挂单成交后，市价回退又买了一次，
   **约 $1,977 裸露 33 分钟**（净成本约 $6.3）。根因是"不在挂单列表里"被当成"已撤销"——
   **成交的单子同样不在挂单列表里**。改为读订单自身记录（`order_state`）并规定"查不到就拒绝回退"。
6. **权益口径的定时炸弹**（§64）：runner 把每条永续空头按**名义**扣进权益，与账本差
   **$4,823.79**（＝三条对冲腿名义），于是**开一对新 carry 就假报日亏约 −2% 并停掉入场**
   （当晚 15:05:56Z 已发生一次），名义再大就会跨过 −3% 触发 `flatten_all`。
   已统一为一条规则（现货名义 + 永续未实现盈亏）并加回归测试。

### 65.3 优化建议（按优先级）

| # | 建议 | 理由 |
|---|---|---|
| 1 | **让 carry 的入场不被名额卡死**：现在现货 3/3 被 CRV/ACT/LTC 占满，SUI/NEAR 整晚被 `leg_positions` 挡下 | 三对都在产 funding（当天 +$322），但**合格标的进不来**等于收益被上限锁住；要么提高现货腿名额，要么给 carry 定"换腿"规则（资金费转负才换） |
| 2 | **修 `Position.unrealized()` 缺 `ctVal`** | ACT 是 ctVal=10，它那一腿的盈亏被低估约 10 倍；影响权益/盈亏读数，不影响下单量 |
| 3 | **TradFi 保持最小仓位、先别调参数** | 13 个标的里只有 1–3 个有双边报价，信号大多会被深度闸门挡掉 —— 当天 0 成交是**设计** |
| 4 | **期权：等 10-13/14 的新鲜样本** | 当天刚修好 RV 冻结与期限错配两个偏差，样本要重新起算 |
| 5 | 继续**逐字段**核对"看起来在攒数据"的东西 | 今天三处收获（RV 冻结、期限错配、权益口径）都是这么挖出来的 |

### 65.4 一句话

**今天第一次是 funding 在赚钱（+$322）而不是手续费在烧钱（−$7.92）；
代价是晚上连续踩了两个自己埋的雷（双倍现货、假日报亏），都在 30 分钟内定位并修掉。**
真正值钱的不是那 +0.48%，而是**三个"会静默做错事"的机制当天被拆穿并上了回归测试**。

---

## 66. 看门狗补上"交易循环是否还在做决策"（v0.4.3，2026-10-10 09:35 CST）

### 66.1 事故暴露的盲区

2026-10-10 09:10 CST 起，**本地代理端口活着（verge-mihomo 在 LISTEN）但上游断了**：
每一笔行情请求都 `NetworkError`（19 分钟内 1,155 次调用失败 / 371 条 degraded，单个符号
`consecutive_failures` 一路涨到 82）。后果是 **runner 卡在"逐个符号重试→全失败→下一个"里，
25 分钟没有完成任何一个 tick** —— 期间**没有任何决策发生**（不开仓、不平仓、不检查止损），
而 09:10 那班每日研究任务同样整片 `FAILED URLError: SSL: UNEXPECTED_EOF`。

**最危险的是：当时看板上一切"看起来正常"** ——
`ta_venue_degraded_total = 1`（该计数器只统计 venue 级状态迁移，而 `fetch_bars` 是逐符号失败）、
`kill_switch = 0`、equity 正常、持仓 6 条腿。**唯一变了的数字是"最后一个 equity 事件的时间戳"。**

### 66.2 改动

| 项 | 内容 |
|---|---|
| 新阈值 | `runner_stale_minutes = 15.0`（写在 `DEFAULT_THRESHOLDS`，可被 `config/alerts.json` 覆盖） |
| 判据 | 用账本里最新 `equity` 事件的时间戳（runner **每个完成的 tick 写一条**）算年龄，超阈值报 **critical** |
| 为什么是 15 不是 5 | **重启要为 64 个标的重新预热 K 线，实测最慢约 12 分钟**；更紧的阈值会在每次重启时误报。正常 tick 约 2 分钟，15 分钟仍远小于"真停摆"的量级 |
| 空账本 | `as_of = 0`（全新安装、还没有 tick）**不报**，避免误报 |

### 66.3 实时验证

3 条新测试（30 分钟陈旧 → critical 命中；2 分钟新鲜 → 不报；空账本 → 不报）后重启看板
（72981 → 98368），`/api/snapshot` 的 watchdog **立刻报出**：

```
runner_stale_minutes  critical  交易循环停摆（最后一个 tick 过久）：25.50 分钟（阈值 15.00 分钟）
```

——同一时刻其它指标全都正常，正是这次事故最危险的地方。
本轮**只重启了只读的看板**，没有动 runner、没有改配置。

---

## 67. 一次代理中断暴露的三处"断网即失控"缺陷（v0.4.4，2026-10-10 10:00 CST）

同一场事故（本地代理上游中断）在三个层次上把系统推向失控，逐层都比上一层更糟。
三处都已修 + 测试，并在生产上验证了最关键的那一处。

### 67.1 ① 启动崩溃循环（最严重）

`CcxtVenue.connect()` 直接 `self.ex.load_markets()`：代理死时 ccxt 抛 `NetworkError`，
一路穿出 `TradingRunner.build()` → **进程在第一个 tick 之前就退出 → launchd KeepAlive 每约 10 秒重启一次**
（实测 **142 次启动横幅**、err.log 涨到 **522 KB**）。也就是说，一次代理中断不是让循环"降级"，
而是**让循环彻底消失**。

修法：`connect()` 用 try/except 包住，失败只记 `last_connect_error` 并**以降级状态继续启动**
（下游本来就容忍：`min_notional`/`min_qty` 有回退；ccxt 自己会在 `fetch_*` 里惰性加载行情表）；
并把 `_markets_loaded` 从"自己记的布尔"改成**从 ccxt 派生**（`bool(self.ex.markets)`）——
网络恢复时这个标志**自己就变真**，不会永远停在 False。

### 67.2 ② 预热无限磨

`LiveFeed.prime()`（启动预热）做了逐符号隔离但**没有快速失败**：全断时它会把
**64 个标的 × 4 个周期**全试一遍，进程永远到不了 `runner start`
（实测：pid 在换、metrics 端口起不来、日志里只有逐符号失败）。

修法：连续 5 个标的全失败就**提前返回**——**不能抛异常**（那会回到 ① 的崩溃循环，
因为 `prime()` 跑在 `build()` 里）；返回则留下一个部分预热的 feed，循环照常启动，
由每 tick 的重试自愈。

### 67.3 ③ tick 逐个符号磨 40 分钟

`LiveFeed.refresh()` 同样只有逐符号隔离：全断时一个 pass 要跑 `64 × 4 × 3` 次重试，
实测**最后一个完成的 tick 停在 08:59，42 分钟没有新 tick**。

修法：连续 5 个标的失败就**让整个 pass 以 `VenueDegraded` 失败**（runner 里已有
`_degrade("market data")` 的处理）。循环回到约 **1 分钟**的节奏 —— 附带好处是
**网络恢复后约 1 分钟就重新开始交易，而不是等那个 40 分钟的 pass 跑完**。
散发的单个坏标的（2026-10-08 的 A/USDT 案例）不受影响：中间只要有一个成功就把连续计数清零。

### 67.4 测试与生产验证

| 测试（共 5 条，283 全绿） | 断言 |
|---|---|
| `connect()` 断网不抛 | 标志为 False、记录 `last_connect_error`；ccxt 有行情表后**自愈**为 True |
| `prime()` 全失败 | **提前返回且不抛异常**，并记录失败符号 |
| `refresh()` 全失败 | 抛 `VenueDegraded`（整 pass 判定 degraded） |
| `refresh()` 单个坏标的 | 仍被跳过、其余照常取到（不回归 10-08 的修复） |
| `refresh()` 少于阈值的一串失败 | 不触发 |

**生产验证**：重启后进程稳定（pid 99064，err.log 自 09:46 起**不再增长** → 崩溃循环结束），
循环在降级状态下持续运行（每 tick 因行情不可得而降级、不写 equity 事件，
所以 §66 那条 `runner_stale_minutes` **仍然如实报警**）。

**仍未恢复交易**：代理上游依旧是断的（外部依赖），需要用户侧处理；
恢复后 tick 应在约 1 分钟内重新产出数据。

### 59.17 已执行：四个标的换腿（2026-10-09）

做法是**把标的从 `alts` 移除、加进 `carry`**，而**不是放宽互斥规则** ——
`Config.validate` 仍会拒绝任何"方向性策略与 carry 共用标的"的配置，
所以 2026-10-08 那次"方向性平仓只卖现货腿、把 carry 永续留成裸空"的路径依然封死。

| 项 | 改动 |
|---|---|
| `tiers.alts` | 11 → **7**（留下 XRP/ADA/AVAX/APT/INJ/TIA/PEPE） |
| `tiers.carry` | 31 → **35**（+DOGE/LINK/SUI/NEAR） |
| `validate()` | **OK** |
| 重启后 tier 映射 | `'DOGE/USDT': 'carry'` / `'LINK/USDT': 'carry'` / `'SUI/USDT': 'carry'` / `'NEAR/USDT': 'carry'` |

ADA 与 PEPE 刻意留在 `alts`：ADA 的永续在贴水 25 bps（过不了基差闸门），PEPE 不在净收益名单里。

**已重启生效**（新 pid 87062），而且 carry 策略**立刻开始提出 SUI 与 NEAR** ——
当前被 `leg_positions: spot 3 >= 3` 挡下（现货腿仍是 CRV/BAND/ACT 三对占满）。
250 tests pass。

这是把"有正期望的腿"接到"最好的标的"上的那一步：四个标的在四道闸门下的 30 天净收益是
**46.4 / 33.7 / 37.0 / 36.2 bps**，而原来占着它们的方向性策略是样本外全负、且**零成本下也为负**。

下一步不需要人工干预：等现货腿腾出名额（三对里任一对资金费转负而退出）时，
系统会按四道闸门优中选优地开出下一对。

### 59.18 被动入场第一步：券底层支持 `post_only`（能力就位，**默认不启用**）

carry 最后一件没做的事：让现货腿**先试被动成交**。所有成本数字都指向它 ——
实测入场冲击 **12.6–72.6 bps**（单腿最坏 133 bps），而稳态资金费只有约 **2 bps/天**：
**省下的价差等于好几天的收入**。

本轮先做**自包含、可测试、不改变现有行为**的那一层：

| # | 改动 |
|---|---|
| 1 | `OrderRequest` 新增 `post_only`（默认 `False`） |
| 2 | `CcxtVenue.place_order` 置位时下发 `ordType=post_only` —— OKX 语义：**会吃流动性的单子直接拒**，而不是成交 |

"被拒的单子零成本"正是它适合做第一枪的原因。

新增 3 个测试：标志默认关闭、置位时确实下发 `ordType`、普通单**不带**该字段（避免误伤其它路径）。**253 tests pass**。

**刻意没有接线。** 把 post-only 接进 `paired.open_pair` 需要
"挂单 → 有界等待 → **必须撤单** → 再回退市价"这一串，而
**撤单失败会留下一张可能成交的挂单 = 无人对冲的现货腿**。
这个新的失败模式值得单独一轮做完并验证（`cancel_order` 已存在，可以先跑一次受控验证）。

所以本轮交付的是**能力，不是行为变更**：没有任何路径会用到 `post_only`，除非显式接线。

---

## 60. 路径规划整体进度盘点（2026-10-09）

按 README 章节与**实时配置/账户**核对（不看 changelog 的 `status` 字段 —— 那一列有不少条目修好之后没回填，不能用来判断）。

### 60.1 §34.4 五步

| # | 步骤 | 状态 |
|---|---|---|
| 1 | 回放改**并集时间轴 + 按标的可用性** | ✅ §35 |
| 2 | **carry 提升为受风控管理的永续策略** | ✅ §37/38，并在 §43–59 大幅加固 |
| 3 | 补齐现货 272 / 永续 155 历史 | ✅ §47（4h/1h 167→388；顺带查出 21 个 live-only + 18 个僵尸条目） |
| 4 | **期权数据层** | ✅ §45（已进每日任务） |
| 5 | **期权策略** | ⏳ **等样本**：IV/RV 自 10-08 起每天一行，需 5–7 天 → 约 **10-13/10-14** 可判 |

### 60.2 四腿实时占用（用户指定 50 / 30 / 20）

| 腿 | 额度 | 已用 | 说明 |
|---|---|---|---|
| 现货 | 30% | **3 / 3 满** | CRV / BAND / ACT，全部是 carry 配对 |
| 永续 | 20% | 2 | CRV 空、ACT 空（对冲腿） |
| **TradFi** | **30%** | **0 / 4** | **空** |
| **期权** | **20%** | **0 / 3** | **空** |

**50% 的额度（TradFi 30 + 期权 20）至今没有成交**，原因是结构性的：

- **TradFi** 有 121 个日线文件，但**样本先天短** —— min 30 / 中位 **71** / max 900 根，只有 **18 个 ≥100 根**。
  71 根中位意味着**任何 120 根 warmup 的日线策略都出不了信号**。所以这条腿只能按用户要求
  "**承风险先跑数据**"：用一个 **warmup 更短的最小日线策略**开仓收集真实成交，
  而不是等一个能通过验证的策略（那需要几年样本）。
- **期权** 既无 venue 适配、也无策略；数据层在跑，策略闸门在等 IV/RV 样本。

### 60.3 一句话

**研究与执行层已经收口**（四道选标闸门、下单路径复核、对账口径、funding 入账、carry 池换腿），
**两条腿是空的**。下一步选 **TradFi**：把 121 个日线标的里样本最长的 18 个接一个短 warmup 日线策略，
按最小仓位开跑，先让 30% 额度产生真实成交与数据。
`tiers.carry` 加进这四个，`Config.validate` 会自行校验互斥与四腿之和，然后重启 runner。
