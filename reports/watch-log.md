# trading-agent 盯盘滚动日志

> 每次巡检追加一段，保留最近 168 段（约 7 天）。判定口径见 `WATCH_PROMPT.md` 。

> 注：2026-10-06 22:39 / 23:45 / 23:55 三次巡检段落在 23:59 写盘时丢失（文件回退到 21:28 版，28 段），结论已存档于巡检自动化记忆，未在此重建。

## 2026-10-07 20:58 (CST) · CRITICAL（当日拒单 5 ≥ 3 维持 + F3 实锤升级：repeat_total 3→5，同一 cash mismatch 五次对账复现、未自愈）

- 服务：runner ✅ pid=4500（runs=4，16:55:44 起 ~4h3m 无新重启，run_id=c1dffbfbab3f —— **18:07 sizing.py 变更仍未加载**） / dash ✅ pid=10136（runs=4，:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（runs=3，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 17 项（ticks=189 / bars=522 / equity/cash=103,027.58 / fees=176.73 / dust=0.00043 / entries_today=0 / kill_switch=0 / open_pos=0 / unmanaged≈0.00004 / heat=0 / drawdown=0 / venue_degraded=0 / **reconcile_mismatch_total=1 + reconcile_repeat_total=5（3→5，新增 2 次静默复现）**；orders_submitted/fills 仍未暴露=F6） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103,027.58 · 现金 103,027.58 · 持仓 0 · 灰尘 0.00043 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · **entries_today 0**（F4 因 16:55 重启清零，非修复——连续第 14 次巡检记录）
- 增量：okx_demo +52（52 equity 心跳，10-07 CST 19:51–21:00）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / **拒单 +0** / **对账 ❌（F3 升级：同一 cash mismatch −2.24914903 五次对账全部复现；mismatch_total=1 未新增告警，repeat_total=3→5 证明防刷屏静默累计，差异持续存续未自愈）**
- blocked by Top3：**本窗口 0 新增**；当日累计 6 条全为 `symbol_daily_cap: OKB/USDT 2/2 today`  
  判读：正常拦截（OKB 当日 2 笔额度用完，风控正确拦住）
- 告警增量：warning +0 / critical +0（alerts 50 行不变，与 repeat 防刷屏语义一致）；无新 Traceback/Exception/ERROR（run-service.log / err.log mtime 14:12 未变；dash err.log 10-01 旧 BrokenPipe 无新增）
- 进度：config|src|scripts|README 相对上次巡检写盘（19:55）**无新变更**；refit 仍 1 条；链上 snapshots 46141 行（+129），evaluate 未跑 → 样本不足以判定
- 异常：①**CRITICAL 维持：当日累计拒单 5 ≥ 3**（03:45 AVAX 54092 F2；10:00/10:16/12:43/12:45 OKB 51000 tdMode ×4——本窗口零新增）；②**F3 升级实锤：cash mismatch −2.24914903 五次对账全部复现**（repeat_total 3→5，mismatch_total 1 不变，alerts 不涨系防刷屏设计，差异持续存续未自愈）；③F4 重启清零非修复；④F6 维持（orders_submitted/fills 计数器间歇不暴露）；⑤F7 维持同根因；⑥F1 根因未修（出口 IP 轮换随时复发）
- 建议（人决定）：①**人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config`（需先 `export OKX_DEMO_API_KEY=...`）** 核对 venue 端 2.25 USDT 流出根因，确认无实际资损后由人决定 `--reset-baseline`——repeat 语义已排除一次性灰尘，差异将持续存在；②确认 18:07–18:13 部署为本人操作，决定 runner 何时重启加载 sizing 变更（若含 F4 修复）；③OKX demo 完成 TradFi 免责声明（F2，约 1 分钟人工动作）+ 等下一笔 OKB/AVAX 信号验证 51000/54092；④修 entries_today CST 日边界；⑤修 newlisting plist 指向 daily_research.py

## 2026-10-07 19:55 (CST) · CRITICAL（当日拒单 5 ≥ 3 维持 + F3 实锤升级：repeat 1→3 证明 mismatch 三次对账均复现、未自愈）

- 服务：runner ✅ pid=4500（runs=4，16:55:44 起 ~3h0m 无新重启，run_id=c1dffbfbab3f —— **18:07 sizing.py 变更仍未加载**） / dash ✅ pid=10136（runs=4，:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（runs=3，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 17 项（ticks=135 / equity / cash / fees=176.73 / dust=0.00044 / entries_today=0 / kill_switch=0 / open_pos=0 / unmanaged≈0.00004 / heat=0 / drawdown=0 / venue_degraded=0 / **reconcile_mismatch_total=1（未涨）+ reconcile_repeat_total=1→3**；orders_submitted/fills 仍未暴露=F6） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103,027.58 · 现金 103,027.58 · 持仓 0 · 灰尘 0.00044 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · **entries_today 0**（F4 因 16:55 重启清零，非修复——连续第 13 次巡检记录）
- 增量：okx_demo +54（54 equity 心跳，全落 10-07 CST 18:41–19:50）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / **拒单 +0** / **对账 ❌（F3 关键证据：tick30/60/90 三次对账全部不通过——repeat_total 1→3；对照 runner.py `check_reconciliation` 语义：repeat_total=同签名 mismatch 重复出现（静默计数防刷屏），mismatch_total=新签名才 +1 并告警。即 cash mismatch −2.24914903（expected 103,029.8295 vs venue 103,027.58）在当前 run 三次对账中全部复现，alerts 不涨是防刷屏设计而非恢复，F3 持续实锤）**
- blocked by Top3：**本窗口 0 新增**；当日累计 6 条全为 `symbol_daily_cap: OKB/USDT 2/2 today`  
  判读：正常拦截（OKB 当日 2 笔额度用完，风控正确拦住）
- 告警增量：warning +0 / critical +0（alerts 50 行不变，与 repeat 防刷屏语义一致）；无新 Traceback/Exception/ERROR（run-service.log / err.log mtime 14:12 未变；dash log 尾部正常启动横幅）
- 进度：config|src|scripts|README 相对上次巡检写盘（18:39）**无新变更**（18:07–18:12 第二轮部署文件均早于上次写盘时点）；refit 仍 1 条；链上 snapshots 46012 行（+187），evaluate 未跑 → 样本不足以判定
- 异常：①**CRITICAL 维持：当日累计拒单 5 ≥ 3**（03:45 AVAX 54092 F2；10:00/10:16/12:43/12:45 OKB 51000 tdMode ×4——本窗口零新增）；②**F3 持续实锤：cash mismatch −2.24914903 三次对账全部复现**（16:50:59 上 run + 17:33/18:1x/18:4x 当前 run tick30/60/90，跨 run 持续，非一次性灰尘，未自愈）；③F4 重启清零非修复（18:08 sizing.py 变更未加载，是否即修复手段未知）；④F6 维持（orders_submitted/fills 计数器间歇不暴露）；⑤F7 维持同根因；⑥F1 根因未修（出口 IP 轮换随时复发）
- 建议（人决定）：①**人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config`** 核对 venue 端 2.25 USDT 流出根因（demo 账户费用/资金活动？），确认无实际资损后由人决定 `--reset-baseline` 重置基准——repeat 语义已排除"一次性灰尘"，该差异将持续存在；②确认 18:07–18:13 第二轮部署为本人操作；若 sizing.py 变更含 F4 修复，需人工决定何时重启 runner 使其生效；③OKX demo 完成 TradFi 免责声明（F2，约 1 分钟人工动作）+ 等下一笔 OKB/AVAX 信号验证 51000/54092；④修 entries_today CST 日边界；⑤修 newlisting plist 指向 daily_research.py

## 2026-10-07 18:39 (CST) · CRITICAL（当日拒单 5 ≥ 3 维持 + F3 复发实锤：cash mismatch 跨 run 持续）

- 服务：runner ✅ pid=4500（runs=4，16:55:44 起 ~1h44m 无新重启，run_id=c1dffbfbab3f） / dash ✅ pid=10136（**runs 3→4，18:13:12 重启**，:9200 `{"status":"ok"}`，err 无新增，伴随 18:12 dashboard.py/index.html 变更 → 判读人工部署重启） / watcher ✅ pid=747 / newlisting ❌ exited（err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 17 项（ticks=81 / bars=237 / equity / cash / fees=176.73 / dust=0.00044 / entries_today=0 / kill_switch=0 / open_pos=0 / unmanaged≈0.00004 / heat=0 / drawdown=0 / venue_degraded=0 / **reconcile_mismatch_total=1.0（首次非零暴露）+ reconcile_repeat_total=1**） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103,027.58 · 现金 103,027.58 · 持仓 0 · 灰尘 0.00044 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · **entries_today 0**（F4 因 16:55 重启清零，非修复）
- 增量：okx_demo +57（56 equity + 1 risk）/ paper +0；order +0 / fill +0 / blocked +0 / risk +1 / **拒单 +0** / **对账 ❌（F3 复发实锤：17:33:38 CST 当前 run tick30 首次对账再次报同一笔 cash delta——expected 103,029.82953782（baseline 103358.46688886615 + flow −328.63735105）vs venue 103,027.58038879，差 −2.24914903，与 16:50:59（上 run）完全同值——跨进程重启持续存在，非一次性灰尘；本地 cash gauge 已 adopt venue 值但 baseline+flow 口径仍恒差 2.25）**
- blocked by Top3：**本窗口 0 新增**；当日累计 6 条全为 `symbol_daily_cap: OKB/USDT 2/2 today`  
  判读：正常拦截（OKB 当日 2 笔额度用完，风控正确拦住）
- 告警增量：warning +1（17:33:38 reconciliation mismatch）/ critical +0（alerts 49→50 行）；无新 Traceback/Exception/ERROR（run/dash err 无新增；RECONCILE_EVERY_TICKS 未显式配置=默认 30 tick，当前 run 81 ticks 仅完成 1 次对账，tick60 未见第二次复报，待下巡观察）
- 进度：**第二轮部署（18:07–18:13 CST）**：18:07 src/ta/monitor.py → 18:08 src/ta/execution/sizing.py + scripts/monitor_study.py（新增）→ 18:12 src/ta/dashboard.py + src/ta/web/index.html + README.md；18:13:12 dashboard 重启加载（runs 3→4）。**⚠️ runner 仍为 16:55 加载的旧代码，18:07 之后的 sizing.py/monitor.py 变更未生效**（是否重启 runner 是人的决定）。refit 仍 1 条；链上 snapshots 45825 行（+192），evaluate 未跑 → 样本不足以判定
- 异常：①**CRITICAL 维持：当日累计拒单 5 ≥ 3**（03:45 AVAX 54092 F2；10:00/10:16/12:43/12:45 OKB 51000 tdMode ×4——本窗口零新增）；②**F3 复发确认：cash mismatch −2.24914903 跨 run 持续**（16:50:59 + 17:33:38 两次同值，mismatch counter 首次非零暴露=1）；③F4 重启清零非修复（18:08 sizing.py 变更未加载，是否即修复手段未知）；④F7 维持同根因；⑤F1 根因未修（出口 IP 轮换随时复发）
- 建议（人决定）：①**人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config`** 核对 venue 端 2.25 USDT 流出根因（demo 账户费用/资金活动？），确认无实际资损后由人决定 `--reset-baseline` 重置基准；②确认 18:07–18:13 第二轮部署与 18:13:12 dashboard 重启是本人操作；若 sizing.py 变更是 F4 修复，需人工决定何时重启 runner 使其生效；③OKX demo 完成 TradFi 免责声明（F2）+ 等下一笔 OKB/AVAX 信号验证 51000/54092；④修 entries_today CST 日边界（若 18:08 sizing.py 即此修复，重启前 F4 依旧）；⑤修 newlisting plist 指向 daily_research.py

## 2026-10-07 17:28 (CST) · CRITICAL（当日拒单 5 ≥ 3 维持 + F3 对账 mismatch 新类型 + runner 16:55 重启）

- 服务：runner ✅ pid=4500（runs=4，16:55:44 重启至今 ~33m，run_id=c1dffbfbab3f；上 run 607d19eb9ec0 正常退出 exit=0 非崩溃） / dash ✅ pid=4503（runs=3，同期重启，:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 15 项（ticks=28 / bars=126 / equity / cash / fees=176.73 / dust=0.00044 / entries_today=0 / kill_switch=0 / open_pos=0 / unmanaged≈0.00004 / heat=0 / drawdown=0 / venue_degraded=0；orders_submitted/fills/reconcile 计数器未暴露=F6） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103,027.58 · 现金 103,027.58 · 持仓 0 · 灰尘 0.00044 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · **entries_today 0**（F4 因 16:55 重启清零，非修复）
- 增量：okx_demo +52（51 equity + 1 risk）/ paper +0；order +0 / fill +0 / blocked +0 / **risk +1 = 16:50:59 periodic reconciliation mismatch** / 拒单 +0 / **对账 ❌（F3 新类型：cash delta——expected 103,029.82953782 vs venue 103,027.58038879，差 −2.24914903；本地已 adopt venue 值，当前 cash=venue；新 run 28 ticks 尚未触发首次 30-tick 对账，是否复报待观察）**
- blocked by Top3：**本窗口 0 新增**；当日累计 6 条全为 `symbol_daily_cap: OKB/USDT 2/2 today`  
  判读：正常拦截（OKB 当日 2 笔额度用完，风控正确拦住）
- 告警增量：warning +1（16:50:59 reconciliation mismatch）/ critical +0（alerts 48→49 行）；无新 Traceback/Exception/ERROR（run err.log mtime 14:12 未变；17:28 CST ADA fetch_bars attempt=1 retry = F5 噪声未致 degraded）
- 进度：**重大变更——50/50 双账本拆分配置上线**：config/risk.toml 重写（新增 [legs.spot] 50%/4仓/heat2.5%/8笔日 + [legs.derivatives] 50%/2仓/heat1.5%/4笔日；账户级改为 legs 之和 gross 100%/6仓/heat4.0%/12笔UTC日，注释注明"2026-10-07 user decision, README §20/§28"），src/ta/config.py、risk/engine.py、venues/ccxt_venue.py、dashboard.py、web/index.html、scripts/carry.py（新增）、carry_probe.py（新增）、README.md 变更；16:55:44 runner+dashboard 同步重启 = 疑似人工部署（与 12:43 模式一致，需人确认）。新 run 策略列表仍 4 个，random_control 未入启用列表（16:00 仅注册类，config 未启用）。refit 仍 1 条；链上 snapshots 45633 行（+158），evaluate 未跑 → 样本不足以判定
- 异常：①**CRITICAL 维持：当日累计拒单 5 ≥ 3**（03:45 AVAX/USDT 54092 F2；10:00:34 / 10:16:17 / 12:43:18 / 12:45:54 CST OKB/USDT 51000 tdMode ×4——本窗口零新增）；②**F3 复发·新类型 cash mismatch**：16:50:59 periodic reconciliation 报 cash delta −2.24914903，与历史 PYTH/INJ 持仓灰尘不同族；本地已按 venue 修正，若 30-tick 后复报同差异需人工追 venue 端 2.25 USDT 流出根因；③runner/dashboard 16:55:44 重启（exit=0 正常退出，紧随 50/50 配置改版，疑似人工部署）；④F4 因重启清零（非修复，CST 日边界逻辑仍未改）；⑤F7 维持同根因；⑥F1 根因未修（出口 IP 轮换随时复发）
- 建议（人决定）：①确认 16:55:44 重启是本人 50/50 部署动作；②关注新 run 首次 30-tick periodic reconcile——若复报 cash mismatch，人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 核对 venue 端 2.25 USDT 流出（demo 账户费用/资金活动？）；③OKX demo 完成 TradFi 免责声明（F2 解 AVAX/XRP）+ 等下一笔 OKB 信号验证 51000 修复；④修 src 风控 entries_today CST 日边界；⑤修 newlisting plist 指向 daily_research.py。50/50 拆分后首次运行（28 ticks 无信号），derivatives leg 行为尚无样本

## 2026-10-07 16:22 (CST) · CRITICAL（维持：当日拒单 5 ≥ 3，本窗口零新增）

- 服务：runner ✅ pid=87587（runs=3，~14:11 重启至今 ~2h11m） / dash ✅（:9200 `{"status":"ok"}`） / watcher ✅ / newlisting ❌ exited（err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 15 项（ticks=98 / bars=285 / equity / cash / fees=176.73 / dust=0.00044 / entries_today=0 / kill_switch=0 / open_pos=0 / unmanaged≈0 / heat=0 / drawdown=0 / venue_degraded=0；orders_submitted/fills/reconcile 计数器未暴露=F6） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103,029.83 · 现金 103,029.83 · 持仓 0 · 灰尘 0.00044 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · **entries_today 0**（F4 因 14:11 重启清零，非修复）
- 增量：okx_demo +49（全 equity 心跳，15:17–16:22 CST）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / **拒单 +0** / 对账倾向 ✅（当前 run 无 "periodic reconciliation skipped"，`ta_reconcile_mismatch_total` 未暴露=counter 0；仍未人工验证归零）
- blocked by Top3：**本窗口 0 新增**；当日累计 6 条全为 `symbol_daily_cap: OKB/USDT 2/2 today`  
  判读：正常拦截（OKB 当日 2 笔额度已用完，风控正确拦住后续同标信号）
- 告警增量：warning +0 / critical +0（alerts.jsonl 48 行无变化，最后 5 条 = OKB 51000×4 + AVAX 54092）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾）
- 进度：**src/ta/strategies/random_control.py 新增（16:00）并注册到 __init__.py（16:00）**——种子随机控制策略，用于 A/B 验证"因子是否携带信息"；README.md mtime 16:16 更新（内容待人工核对）。config/strategies.toml 无变更；refit 仍 1 条无新增；README §13/§14 关键决议不变（maker 未上线、$600 取消决议不变）。链上 snapshots 45475 行（+134），onchain_cache 3070 文件（+51），evaluate 未跑 → 样本不足以判定
- 异常：①**CRITICAL 维持：当日累计拒单 5 ≥ 3**（03:45:58 CST AVAX/USDT 54092 F2 TradFi 免责声明未确认；10:00:34 / 10:16:17 / 12:43:18 / 12:45:54 CST OKB/USDT 51000 Parameter tdMode error ×4——本窗口零新增，但未解）。②**F4 因重启清零（非修复）**——`ta_entries_today=0` 是 14:11 重启结果，CST 日边界重置逻辑仍未修复；③F7 维持：newlisting exited 同根因；④F6：orders_submitted/fills/reconcile 计数器未暴露（已知间歇问题）；⑤F1 根因未修：50110 本窗口无新触发，出口 IP 轮换随时复发
- 建议（人决定）：①**验证 OKB 51000 修复**：14:11 重启后至今无 OKB 信号落地，等下一笔 OKB 信号即知 tdMode=cash 修复是否生效；若仍拒单，核对 ccxt_venue.py 参数路径与 account mode；②OKX demo 网页/App 完成 TradFi 免责声明（AVAX/XRP 解阻）；③修 `src/` 风控 entries_today CST 日边界重置逻辑；④修 newlisting plist 指向 `scripts/daily_research.py` 后由人重装；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 15:17 (CST) · CRITICAL（维持：当日拒单 5 ≥ 3，本窗口仍无新增，但 runner 再次重启）

- 服务：runner ✅ pid=87587（runs=1，~14:11 重启至今 ~1h06m，run_id=607d19eb9ec0） / dash ✅ pid=87936（:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 11 项（ticks=49 / equity / cash / fees / dust / entries_today=0 / kill_switch=0 / open_pos=0 / unmanaged≈0 / heat=0 / drawdown=0 / venue_degraded=0；orders_submitted/fills/reconcile 计数器未暴露=F6） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103,029.83 · 现金 103,029.83 · 持仓 0 · 灰尘 0.00044 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · **entries_today 0**（F4 因 14:11 重启清零，非修复）
- 增量：okx_demo +50（全 equity 心跳，14:11–15:17 CST）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / **拒单 +0** / 对账倾向 ✅（新 run 49 ticks 无 "periodic reconciliation skipped"，`ta_reconcile_mismatch_total` 未暴露=counter 0；仍未人工验证归零）
- blocked by Top3：**本窗口 0 新增**；当日累计 6 条全为 `symbol_daily_cap: OKB/USDT 2/2 today`（最后一条在 12:45 前）  
  判读：正常拦截（OKB 当日 2 笔额度已用完，风控正确拦住后续同标信号）
- 告警增量：warning +0 / critical +0（alerts.jsonl 48 行无变化，最后两条 = 12:43/12:45 CST OKB 51000 拒单）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾）
- 进度：**runner 于 ~14:11 CST 再次重启**（pid=80375→87587，run_id=84fc6e2aeaa0→607d19eb9ec0），无 crash/Traceback 证据，launchctl runs=1 never exited = 人工重新 bootstrap（同 10-06 20:11 模式）。12:10–12:39 部署的 ccxt_venue.py `tdMode=cash` 修复在 12:43/12:45 仍拒单，14:11 重启后至今无 OKB 信号落地，**修复生效与否仍待验证**。config/strategies.toml、src/ta/backtest.py、src/ta/cli.py、README.md mtime 较 14:09 无新增变更（backtest.py 13:42 为研究态）；refit 仍 1 条无新增；README §13/§14 关键标记无实质变化；链上 snapshots 45341 行（+120），onchain_cache 3019 文件，evaluate 未跑 → 样本不足以判定
- 异常：①**CRITICAL 维持：当日累计拒单 5 ≥ 3**（03:45:58 CST AVAX/USDT 54092 F2；10:00:34 / 10:16:17 / 12:43:18 / 12:45:54 CST OKB/USDT 51000 tdMode ×4）——本窗口无新增拒单，但 12:45 后至 14:11 前无信号，14:11 重启后至今亦无 OKB 信号，修复验证仍悬置；②**F4 因重启清零（非修复）**——`ta_entries_today=0` 是 14:11 重启结果，CST 日边界重置逻辑仍未修复；③F7 维持：newlisting exited 同根因；④F1 根因未修：50110 本窗口无新触发，出口 IP 轮换随时复发
- 建议（人决定）：①**优先验证 OKB 51000 修复**：14:11 重启已加载最新 ccxt_venue.py，等下一笔 OKB 信号落地即知生效与否；若仍拒单，需核对 params 覆盖路径与 account mode；②修 `src/` 风控 entries_today 的 CST 日边界重置逻辑（避免天天靠重启清零）；③修 newlisting plist 指向 `scripts/daily_research.py` 后重装；④OKX demo 完成 TradFi 免责声明（AVAX/XRP 解阻）；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 14:09 (CST) · CRITICAL（维持：当日拒单 5 ≥ 3，本窗口无新增）

- 服务：runner ✅ pid=80375（runs=1，12:43 重启至今 ~1h26m） / dash ✅ pid=763（:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 18 项（orders_submitted=2 / orders_blocked=1 / signals=3 / ticks=68；fills/reconcile 计数器未暴露=F6） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00044 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **2**（含 10-06 结转 1，F4 持续见异常②）
- 增量：okx_demo +51（全 equity 心跳，13:05–14:09 CST）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / **拒单 +0** / 对账倾向 ✅（新 run 68 ticks 无 "periodic reconciliation skipped"，`ta_reconcile_mismatch_total` 未暴露=counter 0；最后 skipped 仍为 10-06 16:06 CST F1 波段；仍未人工验证归零）
- blocked by Top3：**本窗口 0 新增**；当日累计 6 条全为 symbol_daily_cap: OKB/USDT 2/2 today（最后一条在 12:45 前）  
  判读：正常拦截（OKB 当日 2 笔额度已用完，风控正确拦住后续同标信号）
- 告警增量：warning +0 / critical +0（alerts.jsonl 48 行无变化，最后两条 = 12:43/12:45 CST OKB 51000 拒单）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾）
- 进度：**src/ta/backtest.py mtime 13:42 CST 变更**（13:04 巡检后，研究态回测代码，不影响在跑 runner 主链路）+ 对应 __pycache__；refit 仍 1 条无新增；README §13/§14 关键标记无实质变化（maker 未上线、$600 取消决议不变）；链上 snapshots 45221 行（+160），evaluate 未跑 → 样本不足以判定
- 异常：①**CRITICAL 维持：当日累计拒单 5 ≥ 3**（03:45:58 CST AVAX/USDT 54092 F2；10:00:34 / 10:16:17 / 12:43:18 / 12:45:54 CST OKB/USDT 51000 tdMode ×4）——本窗口无新增拒单，但 12:45 后 orders_submitted 停在 2（无新信号测试），12:39 部署的 ccxt_venue.py `tdMode=cash` 修复**仍未被验证生效**；②**F4 持续（WARNING，连续 12 次巡检）**——`ta_entries_today=2` 含 10-06 结转 1，CST 日边界未重置，未修复；③F7 维持：newlisting exited 同根因；④F1 根因未修：50110 本窗口无新触发，出口 IP 轮换随时复发
- 建议（人决定）：①为验证 OKB 51000 修复，可人工核对 ccxt_venue.py 参数构造并加日志（create_order 前 params）；等下一笔 OKB 信号落地即知生效与否；②修 `src/` 风控 entries_today 的 CST 日边界重置逻辑；③修 newlisting plist 指向 `scripts/daily_research.py` 后重装；④OKX demo 完成 TradFi 免责声明（AVAX/XRP 解阻直接解法）；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 13:04 (CST) · CRITICAL（维持并升级：当日拒单 5 ≥ 3，OKB tdMode 修复未生效）

- 服务：runner ✅ pid=80375（新 run_id=84fc6e2aeaa0，~12:43 重启，无 crash/Traceback 证据） / dash ✅ pid=763（:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 18 项（fills/reconcile 计数器未暴露=F6） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00044 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **2**（含 10-06 结转 1，F4 持续见异常②）
- 增量：okx_demo +34（29 equity 心跳 + 2 order + 2 risk + 1 blocked）/ paper +0；本窗口新增 2 拒单（OKB/USDT 51000，12:43/12:45）/ 对账未触发（新 run 18 ticks 尚无 periodic reconciliation 记录，`ta_reconcile_mismatch_total` 未暴露=counter 0）
- blocked by Top3：**symbol_daily_cap: OKB/USDT 2/2 today → 1**  
  判读：正常拦截（OKB 当日 2 笔额度已用完，后续同标信号被正确拦住）
- 告警增量：warning +2 / critical +0（alerts.jsonl 46→48，两条均为 12:43/12:45 CST OKB 51000 拒单）；增量窗口无新 Traceback/Exception/ERROR
- 进度：config/app.toml、config/strategies.toml、src/ta/strategies/trend_breakout_daily.py、src/ta/strategies/__init__.py、src/ta/venues/ccxt_venue.py、README.md mtime 较 11:57 更新，12:10–12:39 变更后 12:43 手动重启 runner；refit 仍 1 条无新增；README §13/§14 关键标记无实质变化（maker 未上线、$600 取消决议不变、KmsSigner 仍未实现）；链上 snapshots 45061 行（+147），onchain_cache 3019 文件，evaluate 未跑 → 样本不足以判定
- 异常：①**CRITICAL 维持并升级：当日累计拒单 5 ≥ 3**——03:45:58 CST AVAX/USDT 54092 F2（TradFi 免责声明未确认）；10:00:34 / 10:16:17 / 12:43:18 / 12:45:54 CST OKB/USDT 51000 Parameter tdMode error（4 次）。12:39 部署的 ccxt_venue.py `tdMode=cash` 修复未阻止 12:43/12:45 拒单，待查：代码路径是否命中 / ccxt 是否覆盖 params / 账户 mode 与 market 类型是否仍为 spot；②**F4 持续（WARNING，连续 11 次巡检）**——`ta_entries_today=2` 含 10-06 结转 1，CST 日边界未重置，未修复；③F7 维持：newlisting exited 同根因；④F1 根因未修：50110 本窗口无新触发，出口 IP 轮换随时复发
- 建议（人决定）：①**优先排查 OKB 51000**：核对 ccxt_venue.py 新修复是否被实际加载、market_type 是否为 spot、OKB market 是否被识别为 contract、params 是否传到位；必要时加日志打印 create_order 前 params；②核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复；③修 newlisting plist 指向 `scripts/daily_research.py` 后重装；④OKX demo 网页/App 完成 TradFi 免责声明；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 11:57 (CST) · CRITICAL（维持，拒单≥3）

- 服务：runner ✅ pid=750（runs=1 never exited，~15h45m） / dash ✅ pid=763（:9200 `{"status":"ok"}`，err.log BrokenPipe 已知噪声） / watcher ✅ pid=747 / newlisting ❌ exited（runs=3，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 19 项（orders_submitted=4 / orders_blocked=5；fills/reconcile 计数器未暴露=F6） | kill switch 0 | degraded 0（degraded_streak 0，累计 6） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00044 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **2**（含 10-06 结转 1 + 本日提交 1，F4 持续见异常②）
- 增量：okx_demo +54（51 equity 心跳 + 3 blocked）/ paper +0；order +0 / fill +0 / blocked +3 / risk +0 / 拒单 +0 / 对账倾向 ✅（`ta_reconcile_mismatch_total` 未暴露=counter 0；当前 run 无 "periodic reconciliation skipped"；仍未人工验证归零）
- blocked by Top3：**symbol_daily_cap: OKB/USDT 2/2 today → 3**  
  判读：正常拦截（OKB 当日 2 笔已用完，后续同标信号被正确拦住）
- 告警增量：warning +0 / critical +0（alerts.jsonl 46 行无变化，最后一条 = 10:16:17 CST OKB 拒单）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾）
- 进度：config|src|scripts 无变更；README.md mtime 更新（§13/§14 关键标记无实质变化：maker 未上线、$600 取消决议不变、KmsSigner 仍未实现）；refit 仍 1 条无新增；链上 snapshots 44914 行（+133，最新写入 12:00），onchain_cache 3019 文件，evaluate 未跑 → 样本不足以判定
- 异常：①**CRITICAL 维持：当日累计拒单 3 ≥ 3**（03:45:58 CST AVAX/USDT 54092 F2 TradFi 免责声明未确认；10:00:34 / 10:16:17 CST OKB/USDT 51000 Parameter tdMode error，与 10:01–10:14 strategies.toml+funding.py+perp_trend_funding.py 变更同期，属参数事故）；②**F4 持续（WARNING，连续 10 次巡检）**——`ta_entries_today=2` 含 10-06 结转 1，CST 日边界未重置，未修复；③F7 维持：newlisting exited 同根因（plist 引用不存在的 `scripts/daily_research.sh`）；④F1 根因未修：50110 本窗口无新触发，出口 IP 轮换随时复发
- 建议（人决定）：①**优先处理 OKB 51000 tdMode 参数错误**：检查 strategies.toml / perp_trend_funding.py / funding.py 最近变更是否将 OKB/USDT 现货标的发成了 perp/cross 模式；②核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复；③修 newlisting plist 指向 `scripts/daily_research.py` 后重装；④OKX demo 网页/App 完成 TradFi 免责声明（AVAX 拒单直接解法）；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 09:39 (CST) · WARNING

- 服务：runner ✅ pid=750（runs=1 never exited，13h28m） / dash ✅ pid=763（:9200 `{"status":"ok"}`，err.log BrokenPipe 已知噪声） / watcher ✅ pid=747 / newlisting ❌ exited（runs=2，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 18 项（fills/reconcile 计数器未暴露=F6；orders_submitted=2 / signals=2 无新增） | kill switch 0 | degraded 0（degraded_streak 0，累计 6） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00045 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **2**（=10-06 结转 1 + 本日提交 1（AVAX 拒单），F4 持续见异常①）
- 增量：okx_demo +51（全 equity 心跳，全部落在 10-07 CST 08:34–09:39）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 +0 / 对账倾向 ✅（`ta_reconcile_mismatch_total` 未暴露=counter 0；当前 run 无 "periodic reconciliation skipped"，最后 skipped 仍为 10-06 16:06 CST F1 波段；仍未人工验证归零）
- blocked by Top3：增量 0 条；当日（10-07）累计 blocked 0 条、拒单 1（03:45 AVAX 54092）—— 本窗口无信号落地、无风控拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 44 行无变化，最后一条 = 03:45:58 CST AVAX 拒单）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾；09:02/09:22 CST ADA fetch_bars NetworkError attempt=1 自恢复，未致 degraded）
- 进度：config|src|scripts|README 无变更（find -newer 空）；README §13 待办 1-5 无进展（maker 未上线、$600 取消决议不变、KmsSigner 仍未实现）；refit 仍 1 条无新增；链上 snapshots 44643 行（+182），onchain_cache 3019 文件，evaluate 未跑 → 样本不足以判定
- 异常：①**F4 持续（WARNING，连续 9 次巡检）**——`ta_entries_today=2` 含 10-06 结转 1，CST 日边界未重置，未修复；②F7 维持：newlisting exited 同根因（plist 引用不存在的 `scripts/daily_research.sh`）；③F2 维持：当日累计拒单 1（03:45:58 CST AVAX 54092，1 < 3 不升级），TradFi 免责声明人工确认仍未完成；④F1 根因未修：50110 本窗口无新触发，出口 IP 轮换随时复发
- 建议（人决定）：①核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复（代码动作）；②修 newlisting plist 指向 `scripts/daily_research.py` 后重装；③OKX 控制台将出口 IP 加入密钥白名单或固定 TA_PROXY 出口；④**OKX demo 网页/App 完成 TradFi 免责声明（约 1 分钟人工动作，当前拒单直接解法）**；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 08:33 (CST) · WARNING

- 服务：runner ✅ pid=750（runs=1 never exited，err.log 有 14 条旧 `run_demo_foreground.sh` 不可读噪声，不影响当前运行） / dash ✅ pid=763（:9200 `{"status":"ok"}`，err.log BrokenPipe 已知噪声） / watcher ✅ pid=747 / newlisting ❌ exited（runs=2，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因，err.log 无新增）
- 进程：alive（12h21m） | metrics ✅ 18 项（ticks 553；fills/reconcile 计数器未暴露=F6；orders_submitted=2 / signals=2 无新增） | kill switch 0 | degraded 0（degraded_streak 0，累计 6） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00045 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **2**（含 10-06 结转 1，F4 持续见异常①）
- 增量：okx_demo +52（全 equity 心跳，全部落在 10-07 CST 07:29–08:33）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 +0 / 对账倾向 ✅（`ta_reconcile_mismatch_total` 未暴露=counter 0；当前 run（20:13 CST 起，553 ticks）无 "periodic reconciliation skipped"，最后 skipped 仍为 10-06 16:06 CST F1 波段；仍未人工验证归零）
- blocked by Top3：增量 0 条；当日（10-07）累计 blocked 0 条、拒单 1（03:45 AVAX 54092）—— 本窗口无信号落地、无风控拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 44 行无变化，最后一条 = 03:45:58 CST AVAX 拒单）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾；06:31 CST 后无 ADA 网络噪声）
- 进度：config|src|scripts|README 无变更（find -newer 空）；README.md mtime 07:02 已在上巡记录，§13/§14 标记无实质变化（maker 未上线、$600 取消决议不变、KmsSigner 仍未实现）；refit 仍 1 条无新增；链上 snapshots 44461 行（+240），onchain_cache 3019 文件，evaluate 未跑 → 样本不足以判定
- 异常：①**F4 持续（WARNING，连续 8 次巡检）**——`ta_entries_today=2` 含 10-06 结转 1，CST 日边界未重置，未修复；②F7 维持：newlisting exited 同根因（plist 引用不存在的 `scripts/daily_research.sh`）；③F2 维持：当日累计拒单 1（03:45:58 CST AVAX 54092，1 < 3 不升级），TradFi 免责声明人工确认仍未完成；④F1 根因未修：50110 本窗口无新触发，出口 IP 轮换随时复发
- 建议（人决定，同上巡）：①核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复（代码动作）；②修 newlisting plist 指向 `scripts/daily_research.py` 后重装；③OKX 控制台将出口 IP 加入密钥白名单或固定 TA_PROXY 出口；④**OKX demo 网页/App 完成 TradFi 免责声明（约 1 分钟人工动作，当前拒单直接解法）**；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 07:27 (CST) · WARNING

- 服务：runner ✅ pid=750（runs=1 never exited） / dash ✅ pid=763（:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（runs=2，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因，err.log 无新增）
- 进程：alive（11h15m） | metrics ✅ 18 项（ticks 500；fills/reconcile 计数器未暴露=F6；orders_submitted=2 / signals=2 无新增） | kill switch 0 | degraded 0（degraded_streak 0，累计 6） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00046 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **2**（含 10-06 结转 1，F4 持续见异常①）
- 增量：okx_demo +51（全 equity 心跳，全部落在 10-07 CST 06:23–07:27）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 +0 / 对账倾向 ✅（`ta_reconcile_mismatch_total` 未暴露=counter 0；当前 run（20:13 CST 起，500 ticks）无 "periodic reconciliation skipped"，最后 skipped 仍为 10-06 16:06 CST F1 波段；仍未人工验证归零）
- blocked by Top3：增量 0 条；当日（10-07）累计 blocked 0 条、拒单 1（03:45 AVAX 54092）—— 本窗口无信号落地、无风控拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 44 行无变化）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾）
- 进度：config|src|scripts 无变更；README.md mtime 变更（10-07 07:02），抽查 §13/§14 关键标记无实质变化（maker 未上线、$600 取消决议不变、KmsSigner 仍未实现）；refit 仍 1 条无新增；链上 snapshots 44221 行（+211），evaluate 未跑 → 样本不足以判定
- 异常：①**F4 持续（WARNING，连续 7 次巡检）**——`ta_entries_today=2` 含 10-06 结转 1，CST 日边界未重置，未修复；②F7 维持：newlisting exited 同根因（plist 引用不存在的 `scripts/daily_research.sh`）；③F2 维持：当日累计拒单 1（03:45:58 CST AVAX 54092，1 < 3 不升级），本窗口无新触发，TradFi 免责声明人工确认仍未完成；④F1 根因未修：50110 本窗口无新触发，出口 IP 轮换随时复发
- 建议（人决定，同上巡）：①核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复（代码动作）；②修 newlisting plist 指向 `scripts/daily_research.py` 后重装；③OKX 控制台将出口 IP 加入密钥白名单或固定 TA_PROXY 出口；④**OKX demo 网页/App 完成 TradFi 免责声明（约 1 分钟人工动作，当前拒单直接解法）**；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 06:22 (CST) · WARNING

- 服务：runner ✅ pid=750（runs=1 never exited） / dash ✅ pid=763（:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（runs=2，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 18 项（ticks 450；fills/reconcile 计数器未暴露=F6；orders_submitted=2 / signals=2 无新增） | kill switch 0 | degraded 0（degraded_streak 0，累计 6） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00046 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **2**（含 10-06 结转 1，F4 持续见异常①）
- 增量：okx_demo +52（全 equity 心跳，全部落在 10-07 CST 05:17–06:22）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 +0 / 对账倾向 ✅（`ta_reconcile_mismatch_total` 未暴露=counter 0；当前 run（20:13 CST 起）无 "periodic reconciliation skipped"，最后 skipped 仍为 10-06 16:06 CST F1 波段；仍未人工验证归零）
- blocked by Top3：增量 0 条；当日（10-07）累计 0 条 —— 本窗口无信号落地、无风控拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 44 行无变化，最后一条 = 03:45:58 CST AVAX 拒单）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾）
- 进度：config|src|scripts|README 无变更（find -newer 空）；refit 仍 1 条无新增；README §13 待办 1-5 无进展（maker 未上线，$600 上限已取消决议不变）；链上 snapshots 44010 行（+214），evaluate 未跑 → 样本不足以判定
- 异常：①**F4 持续（WARNING，连续 6 次巡检）**——`ta_entries_today=2` 含 10-06 结转 1，CST 日边界未重置，未修复；②F7 维持：newlisting exited 同根因（plist 引用不存在的 `scripts/daily_research.sh`）；③F2 维持：当日累计拒单 1（03:45:58 CST AVAX 54092，1 < 3 不升级），本窗口无新触发，TradFi 免责声明人工确认仍未完成；④F1 根因未修：50110 本窗口无新触发，出口 IP 轮换随时复发
- 建议（人决定，同上巡）：①核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复（代码动作）；②修 newlisting plist 指向 `scripts/daily_research.py` 后重装；③OKX 控制台将出口 IP 加入密钥白名单或固定 TA_PROXY 出口；④**OKX demo 网页/App 完成 TradFi 免责声明（约 1 分钟人工动作，当前拒单直接解法）**；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 05:16 (CST) · WARNING

- 服务：runner ✅ pid=750（runs=1 never exited） / dash ✅ pid=763（:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（runs=2，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 18 项（fills/reconcile 计数器未暴露=F6；orders_submitted=2 无新增） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00046 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **2**（含 10-06 结转 1，F4 持续见异常②）
- 增量：okx_demo +55（全 equity 心跳，全部落在 10-07 CST 04:07–05:16）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 +0 / 对账倾向 ✅（`ta_reconcile_mismatch_total` 未暴露=counter 0；当前 run（20:13 CST 起）无 "periodic reconciliation skipped"，最后 skipped 仍为 10-06 16:06:22 CST F1 波段；仍未人工验证归零）
- blocked by Top3：增量 0 条；当日（10-07）累计 0 条 —— 本窗口无信号落地、无风控拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 44 行无变化，最后一条 = 03:45:58 CST AVAX 拒单）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾）
- 进度：config|src|scripts|README 无变更（find -newer 空）；refit 仍 1 条无新增；README §13 待办 1-5 无进展（maker 未上线，$600 上限已取消决议不变）；链上 snapshots 43796 行（+255），onchain_cache 2950 文件，evaluate 未跑 → 样本不足以判定
- 异常：①F2 维持（WARNING）——03:45:58 CST AVAX/USDT 拒单（54092 TradFi 免责声明）为本日唯一拒单（1 < 3），本窗口无新触发，人工确认动作仍未完成；②**F4 持续（WARNING，连续 5 次巡检）**——`ta_entries_today=2` 含 10-06 结转 1，CST 日边界未重置，未修复；③F7 维持：newlisting exited 同根因；④F1 根因未修：50110 本窗口无新触发，出口 IP 轮换随时复发
- 建议（人决定，同上巡）：①核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复（代码动作）；②修 newlisting plist 指向 `scripts/daily_research.py` 后重装；③OKX 控制台将出口 IP 加入密钥白名单或固定 TA_PROXY 出口；④**OKX demo 网页/App 完成 TradFi 免责声明（约 1 分钟人工动作，当前拒单直接解法）**；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 04:07 (CST) · WARNING

- 服务：runner ✅ pid=750（runs=1 never exited） / dash ✅ pid=763（:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（runs=2，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 18 项（ticks 343；fills/reconcile 计数器未暴露=F6；orders_submitted=2 / signals=2） | kill switch 0 | degraded 0（degraded_streak 0，累计 6） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00045 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **2**（=10-06 结转 1 + 本日提交 1，F4 持续见异常②）
- 增量：okx_demo +52（51 equity 心跳 + 1 order + 1 risk，全部落在 10-07 CST 03:03–04:06）/ paper +0；fill +0 / blocked +0 / **拒单 1** / 对账倾向 ✅（`ta_reconcile_mismatch_total` 未暴露=counter 0，当前 run 343 ticks 无 "periodic reconciliation skipped"，get_account 正常；仍未人工验证归零）
- blocked by Top3：增量 0 条；当日（10-07）累计 0 条 —— 风控无拦截，本次拒单发生在交易所侧（F2）
- 告警增量：warning **+1**（03:45:58 CST "order rejected" AVAX/USDT）/ critical +0（alerts.jsonl 43→44 行）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾；watcher/dashboard err.log 干净）
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；README §13 待办 1-5 无进展（maker 未上线）；链上 snapshots 43541 行（+194，最新写入 04:08），onchain_cache 2950 文件，evaluate 未跑 → 样本不足以判定
- 异常：①**F2 复发（WARNING）**——10-07 03:45:58 CST AVAX/USDT buy 拒单，okx sCode=54092 TradFi 免责声明未确认（clOrdId `TAetrendAVAXUSDTb4f655e8e8`，策略 trend）。AVAX 第 2 次、系列第 3 次（10-05 XRP、10-06 AVAX、10-07 AVAX），alts/new 信号持续无法落地；当日拒单 1 < 3 不升级 CRITICAL；②**F4 结转持续（WARNING，连续 4 次巡检）**——`ta_entries_today=2` 含 10-06 结转 1，CST 日边界未重置，未修复；③F7 维持：newlisting exited 同根因；④F1 根因未修：50110 本窗口无新触发，但出口 IP 轮换随时复发
- 建议（人决定，同上巡）：①核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复（代码动作）；②修 newlisting plist 指向 `scripts/daily_research.py` 后重装；③OKX 控制台将出口 IP 加入密钥白名单或固定 TA_PROXY 出口；④**OKX demo 网页/App 完成 TradFi 免责声明（本次拒单即此待办未完成的直接后果，约 1 分钟人工动作）**；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 03:02 (CST) · WARNING

- 服务：runner ✅ pid=750（runs=1 never exited） / dash ✅ pid=763（:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（runs=2，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 18 项（ticks 293；fills/reconcile 计数器未暴露=F6） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00045 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **1**（F4 结转持续，见异常①）
- 增量：okx_demo +51（全 equity 心跳，全部落在 10-07 CST 01:58–03:03）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 0 / 对账倾向 ✅（`ta_reconcile_mismatch_total` 未暴露=counter 0，当前 run 293 ticks 无 "periodic reconciliation skipped"，get_account 正常；仍未人工验证归零）
- blocked by Top3：增量 0 条；当日（10-07）累计 0 条 —— 今日无信号落地、无风控拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 43 行无变化）；增量窗口无新 Traceback/Exception/ERROR（02:28/02:35/02:52 CST ADA fetch_bars NetworkError attempt=1/2 重试自恢复，未致 degraded —— F5 已知噪声不计数）
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；README §13 待办 1-5 无进展（maker 未上线）；链上 snapshots 43347 行（+154，最新写入 03:04），onchain_cache 2869 文件，evaluate 未跑 → 样本不足以判定
- 异常：①**F4 结转持续（WARNING）**——10-07 当日 0 成交/0 blocked，`ta_entries_today=1` 仍未在 CST 日边界重置（10-06 23:01 AVAX 拒单结转），连续 3 次巡检同状态=未修复；②F7 维持：newlisting exited 同根因（plist 指向不存在的 `scripts/daily_research.sh`）；③F1/F2 根因均未修：本窗口无 50110/54092 新触发，但出口 IP 轮换与 TradFi 免责声明两项人工动作仍未完成
- 建议（人决定，同上巡）：①核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复（代码动作）；②修 newlisting plist 指向 `scripts/daily_research.py` 后重装；③OKX 控制台将出口 IP 加入密钥白名单或固定 TA_PROXY 出口；④OKX demo 完成 TradFi 免责声明；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 01:56 (CST) · WARNING

- 服务：runner ✅ pid=750（runs=1 never exited） / dash ✅ pid=763（:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（runs=2，err: `can't open input file: scripts/daily_research.sh` —— F7 维持同根因）
- 进程：alive | metrics ✅ 18 项（orders_submitted 暴露=1，fills/reconcile 计数器未暴露=F6） | kill switch 0 | degraded 0（degraded_streak 0） | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00045 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **1**（F4 结转持续，见异常①）
- 增量：okx_demo +48（全 equity 心跳，全部落在 10-07 CST）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 0 / 对账倾向 ✅（`ta_reconcile_mismatch_total` 未暴露=counter 0，当前 run 242 ticks 无 "periodic reconciliation skipped"，get_account 正常；仍未人工验证归零）
- blocked by Top3：增量 0 条；当日（10-07）累计 0 条 —— 今日无信号落地、无风控拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 43 行无变化）；增量窗口无新 Traceback/Exception/ERROR（run-service 最后 ERROR 仍为 10-06 20:20 CST 50110 波段尾；01:55 CST ADA fetch_bars NetworkError attempt=1 为已知噪声 F5，未造成 degraded）
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；README §13 待办 1-5 无进展（maker 未上线）；链上 snapshots 43193 行（+121，最新写入 01:58），onchain_cache 2869 文件，evaluate 未跑 → 样本不足以判定
- 异常：①**F4 结转持续（WARNING）**——10-07 当日 0 成交/0 blocked，`ta_entries_today=1` 仍未在 CST 日边界重置（10-06 23:01 AVAX 拒单结转），与上巡一致=未修复；②F7 维持：newlisting exited 同根因（plist 指向不存在的 `scripts/daily_research.sh`）；③F1/F2 根因均未修：本窗口无 50110/54092 新触发，但出口 IP 轮换与 TradFi 免责声明两项人工动作仍未完成
- 建议（人决定，同上巡）：①核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复（代码动作）；②修 newlisting plist 指向 `scripts/daily_research.py` 后重装；③OKX 控制台将出口 IP 加入密钥白名单或固定 TA_PROXY 出口；④OKX demo 完成 TradFi 免责声明；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 验证 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-07 00:51 (CST) · WARNING

- 服务：runner ✅ pid=750（runs=1 never exited） / dash ✅ pid=763（:9200 `{"status":"ok"}`） / watcher ✅ pid=747 / newlisting ❌ exited（runs=2，err: `can't open input file: scripts/daily_research.sh` —— F7 维持，plist 引用不存在的脚本）
- 进程：alive | metrics ✅ 18 项（orders_submitted 暴露=1，fills 未暴露=F6） | kill switch 0 | degraded 0（gauge 归零，degraded_streak 0） | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 灰尘 0.00045 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today **1**（见异常①）
- 增量：okx_demo +39（全 equity 心跳：10-06 +2 / 10-07 +37）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 0 / 对账倾向 ✅（`ta_reconcile_mismatch_total` 未暴露=counter 0，当前 run 194 ticks 无 "periodic reconciliation skipped" 记录，get_account 正常；仍未人工验证归零）
- blocked by Top3：增量 0 条；当日（10-07）累计 0 条。10-06 对照：`daily_trade_cap: 4 entries already today` ×3（正常拦截，文案本身反映 F4 结转口径）、拒单 2（AVAX/USDT sCode=54092，F2）
- 告警增量：warning +0 / critical +0（alerts.jsonl 43 行无变化）；本窗口（上巡 23:55 后）四个 service.log 无新 Traceback/Exception/ERROR（run-service 最后 ERROR 为 20:20 CST 50110 波段尾部，此后 get_account 正常）
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；README §13 待办 1-5 无进展（maker 未上线）；链上 snapshots 43072 行（+121，最新写入 00:53），evaluate 未跑 → 样本不足以判定
- 异常：①**F4 复发确认（WARNING）**——10-07 当日 0 order/0 fill/0 blocked，但 `ta_entries_today=1`：10-06 23:01 AVAX 拒单被计入 entries 计数且未在 CST 日边界重置，靠 20:11 重启清零=未修复；②F7 维持：newlisting 第二次退出同根因（plist 指向 `scripts/daily_research.sh` 不存在）；③F1 根因未修：IP 白名单（50110）20:20 CST 后未再触发，但出口 IP 会轮换随时复发；④F2 未修：TradFi 免责声明仍未人工确认，new/alts 信号无法落地
- 建议（人决定）：①核对 `src/` 风控 entries_today 的 CST 日边界重置逻辑并修复（代码动作）；②修 newlisting plist 指向 `scripts/daily_research.py` 后 `scripts/install_newlisting_service.sh` 重装；③OKX 控制台将出口 IP 加入密钥 [REDACTED_OKX_DEMO_API_KEY] 白名单或固定 TA_PROXY 出口；④OKX demo 网页/App 完成 TradFi 免责声明；⑤人工跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 确认 mismatch 归零。不自动重启 runner、不删 KILL_SWITCH、不改 config/src

## 2026-10-06 21:28 (CST) · CRITICAL（维持）

- 进程：alive pid=750（20:11:59 CST 重启，run_id=e59c93c52c0e） | metrics ✅ 16 项（get_account 已恢复） | kill switch 0 | degraded 0（gauge 归零，degraded_streak 5→0） | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today 0
- 增量：okx_demo +51（1 risk：20:18 CST venue degraded 里程碑 degraded_ticks=5；50 equity）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持，mismatch_total=3，PYTH/INJ 灰尘）
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3（05:15 / 06:15 / 06:30）
  判读：全部为正常拦截（new 档每日 4 笔上限，风控在压笔数）
- 告警增量：warning +0 / critical +1（alerts.jsonl 41→42：20:18:53 CST "venue degraded - new entries suspended"，degraded_ticks=5，IP 42.200.166.221 白名单；该告警 ts 早于上次巡检 20:20 但此后落盘）；增量窗口 service.log 无新 Traceback/Exception
- 进度：`src/ta/venues/swap_math.py`(21:13)、`README.md`(21:24) mtime 变更，config|scripts 无变更；refit 仍 1 条无新增；README §13 待办 1-5 均无进展（strategies.toml 无 entry_style=maker；app.toml timeframe 仍 15m）；链上 onchain_cache 2869 文件（最新 21:27），evaluate 未跑 → 样本不足以判定
- 变化：20:13/20:18 CST 因 OKX IP 白名单（code 50110，IP 42.200.166.221）degraded 后已自恢复，metrics 由 2 项恢复到 16 项（equity/cash/drawdown 等 gauge 有数据），`ta_venue_degraded=0` → degraded_streak 5→0，WARNING 解除（但根因 IP 白名单未修，出口 IP 会轮换，随时可再次降级）
- 异常：①对账 ❌ 维持（CRITICAL）— mismatch_total=3（PYTH/USDT + INJ/USDT 灰尘）；新进程（20:11:59 重启）已加载 `oms.py` 步长截断修复，但本次增量窗口未见 periodic reconciliation 日志/mismatch 事件，`ta_` 指标亦无 reconcile 计数器暴露，无法确认是否归零，待下次巡检核对。②当日拒单累计 1（AVAX/USDT sCode=54092，11:01 CST，TradFi 免责声明未确认，<3 未达 CRITICAL）。建议（人决定）：①在 OKX 账户控制台将出口 IP（42.200.166.221 或当前实际 IP）加入密钥 [REDACTED_OKX_DEMO_API_KEY] 白名单，或固定 TA_PROXY 出口 IP；②核对下一次 periodic reconcile 是否使 mismatch_total 归零，仍为 3 则清理 PYTH/INJ 灰尘残仓；③在 OKX demo 网页/App 完成 TradFi 免责声明确认；④确认 20:11 CST 重启是本人操作。不自动重启、不删 KILL_SWITCH、不改 config/src。

## 2026-10-06 20:20 (CST) · CRITICAL（维持）

- 进程：alive pid=750（5m，20:11:59 CST 重启，run_id=e59c93c52c0e） | metrics ⚠️ 仅 2 项（get_account 失败致 equity/cash 等 gauge 无数据） | kill switch 0 | degraded 1（进行中） | 看板 ✅ :9200
- 权益/现金/持仓：本次 metrics 不可用（get_account 白名单失败，gauge 无数据）；上巡值 权益 103029.83 · 持仓 0（空仓无敞口）
- 增量：okx_demo +2（2 risk：venue degraded 里程碑 degraded_ticks=250/1）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持；新进程首次 periodic reconcile 预计 ~20:44 CST）
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3
  判读：全部为正常拦截
- 告警增量：warning +0 / critical +2（alerts.jsonl 39→41：16:13:38 CST 旧进程 degraded_ticks=250、20:13:48 CST 新进程 degraded_ticks=1，均 IP 白名单 50110）；增量窗口 0 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 snapshots 42529 行（+180，最新 20:20），evaluate 未跑 → 样本不足以判定
- 🔴 重大变化：交易进程被重启（非崩溃）。旧 pid=1477（run_id acc8f3c0e08c）最后日志 16:28:15 CST；新 pid=750 于 20:11:59 CST 启动、20:13:37 CST runner start（新 run_id=e59c93c52c0e）；launchd `runs=1 / last exit=(never exited)` = 重新 bootstrap（人工/脚本重启），16:28–20:13 CST 约 3h45m 空窗。重启后出口 IP 变更（209.9.201.229 → 42.200.166.221），但 OKX code 50110 IP 白名单错误依旧 → 新进程首个 tick 即 degraded，只停新开仓、不平仓
- ⚠️ WARNING（degraded_streak 5，≥3）：venue degraded 跨进程持续 >8h（12:18 CST 起），根因 OKX API 密钥 IP 白名单（code 50110）；metrics 因 get_account 失败只剩 ta_venue_degraded 2 项（equity/cash/drawdown 等无数据）
- 异常：①对账 ❌ 维持 — mismatch_total=3（PYTH/USDT + INJ/USDT 灰尘差异）；注意：本次重启使早已写好的 `src/ta/execution/oms.py` 步长截断修复随新进程加载（src 无新变更，代码本来就在），待 get_account 恢复后由 periodic reconciliation 验证 mismatch 是否归零。②venue degraded 持续且 IP 白名单未修。建议（人决定）：①在 OKX 账户控制台将新出口 IP 42.200.166.221 加入密钥 [REDACTED_OKX_DEMO_API_KEY] 白名单，或固定 TA_PROXY 出口 IP —— 这是当前 degraded + metrics 饥饿的唯一根因；②确认 20:11 重启是本人操作（若非本人操作请检查机器访问）；③白名单恢复后核对下一次 reconcile 是否消除 mismatch_total=3；不自动重启、不删 KILL_SWITCH

## 2026-10-06 15:40 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d18h，since 10-01 21:08） | metrics ✅ 21 项 | kill switch 0 | degraded 1（进行中） | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73 · entries_today 1
- 增量：okx_demo +1（1 risk：venue degraded 里程碑 degraded_ticks=200）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持，VenueDegraded 跳过对账、repeat 119、skipped 7）
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3
  判读：全部为正常拦截
- 告警增量：warning +0 / critical +1（alerts.jsonl 38→39：15:14:57 CST "venue degraded - new entries suspended"，degraded_ticks=200 里程碑）；增量窗口 service.log 显示降级原因已由 ADA fetch_bars 转为 get_account IP 白名单错误（code 50110，自 15:28 CST 起），无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 snapshots 42349 行（+248，最新 15:40），evaluate 未跑 → 样本不足以判定
- ⚠️ WARNING（degraded_streak 4，≥3）：venue degraded 自 12:18 CST 起已持续 >3h，且 15:28 CST 起降级原因切换为 OKX API get_account IP 白名单错误（code 50110，当前 IP 209.9.201.229 不在密钥 73c760da-... 的白名单内），只停新开仓、不平仓；当前空仓（持仓 0、热手 0.00%）无实际敞口；periodic reconciliation 持续被跳过
- 异常：①对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（PYTH/USDT + INJ/USDT 灰尘差异，`oms.py` 修复未部署，进程自 10-01 21:08 未重启）。②venue degraded 持续且原因恶化（IP 白名单错误导致 get_account 连续失败）。③当日拒单累计 1（AVAX sCode=54092，OKX demo TradFi 免责声明未确认）。建议（人决定）：①当前空仓是部署 `src/ta/execution/oms.py` 修复 + 重启的好时机（一次重启同时解决对账 CRITICAL 与降级恢复后的对账跳过）；②在 OKX 账户控制台将当前出口 IP 209.9.201.229 加入 API 密钥 [REDACTED_OKX_DEMO_API_KEY] 的白名单，或调整 TA_PROXY 使出口 IP 固定到白名单 IP；③在 OKX demo 网页/App 完成 TradFi 免责声明以解除 AVAX 交易限制；不自动重启、不删 KILL_SWITCH

## 2026-10-06 14:33 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d17h，since 10-01 21:08） | metrics ✅ 24 项 | kill switch 0 | degraded 1（进行中） | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73 · entries_today 1
- 增量：okx_demo +2（2 risk：venue degraded 里程碑）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持，VenueDegraded 跳过对账、repeat 停在 119）
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3
  判读：全部为正常拦截
- 告警增量：warning +0 / critical +2（alerts.jsonl 36→38：13:30:51 / 14:22:54 CST "venue degraded - new entries suspended"，degraded_ticks=100/150 里程碑）；增量窗口无新 Traceback/Exception
- ⚠️ WARNING（degraded_streak 达 3）：ADA fetch_bars + get_account 自 12:18 CST 起连续失败未自恢复（consecutive_failures=137 @14:34，持续 >2h16m），只停新开仓、不平仓；当前空仓（持仓 0、热手 0.00%）无实际敞口；periodic reconciliation 持续被跳过
- 异常：①对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（PYTH/USDT + INJ/USDT 灰尘差异，`oms.py` 修复未部署，进程自 10-01 21:08 未重启）。②venue degraded 持续 >2h。建议（人决定）：①检查本机代理链路（TA_PROXY 127.0.0.1:7897）与 OKX 连通性；②当前空仓是部署 `src/ta/execution/oms.py` 修复 + 重启的好时机（一次重启同时解决对账 CRITICAL 与降级恢复）；③OKX demo TradFi 免责声明确认仍未做；不自动重启、不删 KILL_SWITCH


## 2026-10-06 13:28 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d16h，since 10-01 21:08） | metrics ✅ 24 项 | kill switch 0 | degraded 1（进行中） | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73 · entries_today 1
- 增量：okx_demo +1（1 risk：venue degraded）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3（W/USDT 05:15、PYTH/USDT 06:15/06:30）
  判读：全部为正常拦截（new 档每日 4 笔上限，风控在压笔数）
- 告警增量：warning +1 / critical +0（alerts.jsonl 35→36：AVAX/USDT order rejected sCode=54092，ts=11:01 CST，文件追加略有滞后）；service.log 12:18–13:28 CST ADA fetch_bars + get_account 持续 venue degraded（consecutive_failures 已至 72 且仍在计数），无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 snapshots 41860 行（+269，最新 13:30），evaluate 未跑 → 样本不足以判定
- 异常：①对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat 119；PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署，进程自 10-01 21:08 未重启。②venue degraded **进行中** — 12:18 CST 起 ADA/USDT fetch_bars 连续失败，get_account 亦失败，gauge=1，degraded_streak 1→2（再连续 1 次升级为 WARNING）。③当日拒单累计 1（AVAX sCode=54092，OKX demo TradFi 免责声明未确认）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启（重启前确认空仓）；②在 OKX demo 网页/App 完成 TradFi 免责声明确认；③若下次巡检 degraded 仍 >0，检查本机代理 TA_PROXY 与 OKX 连通性；不自动重启、不删 KILL_SWITCH


## 2026-10-06 12:25 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d15h，since 10-01 21:08） | metrics ✅ 23 项 | kill switch 0 | degraded 1（进行中） | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73 · entries_today 1
- 增量：okx_demo +44（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3（05:15 / 06:15 / 06:30）
  判读：全部为正常拦截（风控在压笔数）
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 35 行）；增量窗口无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 snapshots 41591 行（+290，最新 12:23），evaluate 未跑 → 样本不足以判定
- 异常：①对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat→119；PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。②venue degraded **进行中** — 12:18 CST 起 ADA fetch_bars 与 get_account 连续失败（consecutive_failures=7 @ 12:23 CST），periodic reconciliation 已被跳过（VenueDegraded）；前一波 11:43–11:52 CST 曾自恢复。degraded_streak 0→1（连续 3 次巡检 degraded>0 才升级 WARNING）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启（重启前确认空仓）；②在 OKX demo 网页/App 完成 TradFi 免责声明确认；③若降级持续到下次巡检仍 >0，检查本机代理（TA_PROXY 127.0.0.1:7897）与 OKX 连通性；不自动重启、不删 KILL_SWITCH


## 2026-10-06 11:16 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d14h） | metrics ✅ 16 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73 · entries_today 1
- 增量：okx_demo +52（50 equity + 1 order + 1 risk）/ paper +0；order +1 / fill +0 / blocked +0 / 拒单 1 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：daily_trade_cap "4 entries already today" ×3
  判读：全部为正常拦截（分层每日笔数上限，entries_today=1 但历史累计已触 new 档上限，风控在压笔数）
- 告警增量：warning +1 / critical +0（alerts.jsonl 34→35：11:01:19 CST AVAX/USDT order rejected sCode=54092）；增量窗口 service.log 有 ADA/XRP/ONDO/PEPE fetch_bars venue degraded 瞬时错误，均为已知慢性噪声且当前 gauge=0，无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 snapshots 41301 行（+320），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。新增 WARNING：AVAX/USDT 拒单 sCode=54092，与 XRP 同为 OKX demo 账户 TradFi 免责声明未确认（10-05 XRP、10-06 AVAX 各 1 次，当日累计 1 次，未达 3 次 CRITICAL 阈值）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；②在 OKX demo 网页/App 完成 TradFi 免责声明确认，以解除 XRP/AVAX 等永续合约的交易限制；不自动重启、不删 KILL_SWITCH

## 2026-10-06 06:55 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d9h） | metrics ✅ 23 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73 · entries_today 4
- 增量：okx_demo +52（50 equity + 2 blocked）/ paper +0；order +0 / fill +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：daily_trade_cap "4 entries already today" ×2（06:15 PYTH、06:30 PYTH）；当日累计 Top5：daily_trade_cap ×3（02:00 W、06:15/06:30 PYTH）
  判读：全部为正常拦截（分层每日笔数上限，entries_today=4 已到 new 档上限，风控在压笔数，符合预期）
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 34 行，无新增；当日唯一 risk 事件为 02:29 CST ADA fetch_bars 降级，已自恢复；:29 定点失败模式 03:29 后未复现）；增量窗口无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 snapshots 39987 行（+313），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat 108→111，PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；②在 OKX demo 网页/App 完成 TradFi 免责声明确认以解除 XRP 交易限制；不自动重启、不删 KILL_SWITCH


## 2026-10-06 03:41 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d6h） | metrics ✅ 16 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73
- 增量：okx_demo +50（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：无（10-06 尚未产生 blocked 事件）
  判读：无
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 34 行，无新增；02:29 CST ADA fetch_bars 为上次巡检已记录事件）；增量窗口无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 cache 2641 文件（+55）/ snapshots 39063 行（+282），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；②在 OKX demo 网页/App 完成 TradFi 免责声明确认以解除 XRP 交易限制；不自动重启、不删 KILL_SWITCH


## 2026-10-06 02:37 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d5h） | metrics ✅ 23 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73
- 增量：okx_demo +48（47 equity + 1 risk）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：无（10-06 尚未产生 blocked 事件）
  判读：无
- 告警增量：warning +0 / critical +1（alerts.jsonl 33→34：02:29:25 CST `venue degraded - new entries suspended`，ADA/USDT fetch_bars 连续 3 次失败）；为已知慢性模式的第 3 次按小时定点复现（00:28 / 01:29 / 02:29 CST 均在 :29，consecutive_failures=1，每次下一 tick 自恢复，检时 gauge=0，degraded_streak=0）；无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 cache +216 文件 / snapshots 38781 行（+320），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat 103→104；PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；②在 OKX demo 网页/App 完成 TradFi 免责声明确认以解除 XRP 交易限制；③ADA fetch_bars 每小时 :29 定点失败若持续，考虑将其移出主扫池或排查 OKX 该分钟限流（不自动改 config）


## 2026-10-06 01:33 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d4h） | metrics ✅ 23 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73
- 增量：okx_demo +46（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：无（10-06 尚未产生 blocked 事件）
  判读：无
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 33 行）；增量窗口无新 Traceback；01:29 CST ADA/USDT fetch_bars 瞬时降级已自恢复（当前 gauge=0，不计 degraded_streak）
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 cache 2586 文件（+0）/ snapshots 38461 行（+296），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat=103，+3 重复检出；PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；②在 OKX demo 网页/App 完成 TradFi 免责声明确认以解除 XRP 交易限制；不自动重启、不删 KILL_SWITCH


## 2026-10-06 00:27 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d3h） | metrics ✅ 16 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73
- 增量：okx_demo +44（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：无（10-06 尚未产生 blocked 事件）
  判读：无
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 33 行）；增量窗口无新 ERROR/Traceback
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 cache 2586 文件（+34）/ snapshots 38165 行（+304），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；②在 OKX demo 网页/App 完成 TradFi 免责声明确认以解除 XRP 交易限制；不自动重启、不删 KILL_SWITCH


## 2026-10-05 22:15 (CST) · CRITICAL（维持）

- 进程：alive pid=1477 | metrics ✅ 23 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0（全部平仓）· 热手 0.00% · 回撤 0.21% · 当日 PnL +0.036% · fees 176.73
- 增量：okx_demo +92（90 equity 快照 + 1 order + 1 fill）/ paper +0；PEPE/USDT 二段减仓退出（reduce_only uncapped exit，467,133,342 @ 4e-06，fee 2.10），当日买卖数量对平 934,266,683，无卖手动底仓痕迹；blocked +0 / risk +0 / 增量拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：`symbol_daily_cap: SOL/USDT 2/2 today` ×1、`daily_trade_cap: 4 entries already today` ×1
  判读：全部为正常拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 33 行）；增量窗口（20:12–22:15）无新 ERROR/Traceback，ADA/LINK/INJ fetch_bars 重试均成功未致 degraded
- 进度：仅 scripts/review_20261005.py（巡检脚本）mtime 更新，config|src|README 无变更；refit 仍 1 条无新增；链上 cache 2516 文件（+22）/ snapshots 37563 行（+582），evaluate 未跑，样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat=98，+4 重复检出；PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。当日拒单累计 1（XRP sCode=54092 TradFi 免责声明）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；②在 OKX demo 网页/App 完成 TradFi 免责声明确认以解除 XRP 交易限制；不自动重启、不删 KILL_SWITCH


## 2026-10-05 20:12 (CST) · CRITICAL（维持）

- 进程：alive pid=1477 | metrics ✅ 23 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103032.40 · 现金 100932.16 · 持仓 1 · 热手 0.03% · 回撤 0.21% · 当日 PnL +0.039% · fees 174.63
- 增量：okx_demo +47（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：`symbol_daily_cap: SOL/USDT 2/2 today` ×1、`daily_trade_cap: 4 entries already today` ×1
  判读：全部为正常拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 33 行）；ta-run-20261005.log 不存在，无新增 Traceback
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 cache 2494 文件（+5）/ snapshots 36981 行（+314），evaluate 未跑，样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；不自动重启、不删 KILL_SWITCH


## 2026-10-05 19:12 (CST) · CRITICAL（维持）

- 进程：alive pid=1477 | metrics ✅ 23 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103037.07 · 现金 100932.16 · 持仓 1 · 热手 0.04% · 回撤 0.20% · 当日 PnL +0.043% · fees 174.63
- 增量：okx_demo +46（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：`symbol_daily_cap: SOL/USDT 2/2 today` ×1、`daily_trade_cap: 4 entries already today` ×1
  判读：全部为正常拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 33 行）；ta-run-20261005.log 不存在，无新增 Traceback
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 cache 2489 文件（+26）/ snapshots 36667 行（+286），evaluate 未跑，样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat=93，+3 重复检出；PYTH/USDT local=0.000211 venue=0.000497 + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；不自动重启、不删 KILL_SWITCH


## 2026-10-05 18:12 (CST) · CRITICAL（维持）

- 进程：alive pid=1477 | metrics ✅ 16 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103045.94 · 现金 100932.16 · 持仓 1 · 热手 0.04% · 回撤 0.20% · 当日 PnL +0.052% · fees 174.63
- 增量：okx_demo +47（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / risk +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：`symbol_daily_cap: SOL/USDT 2/2 today` ×1、`daily_trade_cap: 4 entries already today` ×1
  判读：全部为正常拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 33 行）；ta-run-20261005.log 不存在，无新增 Traceback
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 cache 2463 文件（+34）/ snapshots 36381 行（+305），evaluate 未跑，样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（PYTH/USDT local=0.000211 venue=0.000497 + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；不自动重启、不删 KILL_SWITCH


## 2026-10-05 15:12 (CST) · CRITICAL（维持）+ WARNING（新增 1 笔拒单）

- 进程：alive pid=1477 | metrics ✅ 23 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103034.73 · 现金 100932.16 · 持仓 1 · 热手 0.04% · 回撤 0.21% · 当日 PnL +0.041% · fees 174.63
- 增量：okx_demo +50（equity 44 / order 2 / fill 1 / risk 3）/ paper +0；blocked +0 / 拒单 +1 / 对账 ❌（维持）
- **新拒单**：14:15 CST XRP/USDT trend_pullback 市价买入（qty 2720.27）被 OKX 拒（sCode **54092**）："请先在 Web/App 完成 TradFi Perpetuals 或现货免责声明确认（主/子账户需分别确认）才能启用 API 交易" → 判读为**账户配置问题**，非代码回归。当日累计拒单 1 次（<3，WARNING）
- blocked by：增量 0；当日累计 1 条（`symbol_daily_cap: SOL/USDT 2/2 today`）
  判读：正常拦截
- 告警增量：+3（warning order rejected XRP ×1、info partial take profit PEPE ×1、info stop moved to breakeven PEPE ×1），critical +0；运行日志无新增 Traceback（service.log 最后活动 15:09 PEPE 保本止损）
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 cache 2391 文件（+6）/ snapshots 35501 行（+280），evaluate 未跑，样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat 86→87，+1 重复检出；PYTH/USDT local=0.000211 venue=0.000497 + INJ/USDT 灰尘 diff 1.08e-06 结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：① OKX App/Web 完成 XRP（TradFi/现货）免责声明确认后 XRP 才能恢复 API 交易；② 择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；不自动重启、不删 KILL_SWITCH


## 2026-10-05 23:22 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d2h） | metrics ✅ 23 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL +0.036% · fees 176.73
- 增量：order +0 / fill +0 / blocked +0 / 拒单 +0 / 对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat 98→100，+2 重复检出；PYTH/USDT + INJ/USDT 灰尘差异结构性存续，`oms.py` 修复仍未部署，进程自 10-01 21:08 未重启）
- blocked by 增量：无。当日累计 Top：daily_trade_cap 4 entries ×1、symbol_daily_cap SOL 2/2 ×1 — 判读：均为正常拦截
- 拒单当日累计 1（XRP/USDT 14:15，sCode=54092 TradFi 免责声明；需人在 OKX Web/App 确认后恢复）
- 告警增量：warning +0 / critical +0（alerts.jsonl 33 行无新增）
- 日志：增量窗口无 Traceback/Exception；23:10 CST XRP fetch_bars 瞬时降级 1 次（已知网络噪声，已自恢复，gauge=0，degraded_streak=0）
- 进度：config|src|scripts|README 无变更；refit 1 条无新增；链上 cache 2552 文件（+36）/ snapshots 37861 行（+298），evaluate 未跑 → 样本不足以判定
- 异常：无新增（对账存续项见上；建议同 22:15 段：① OKX 确认 XRP 免责声明；② 择机部署 oms.py 修复并重启、清理灰尘；均由人决定，不自动执行）


## 更早巡检（压缩摘要）

- 2026-10-05 17:12 · CRITICAL（维持）— okx_demo +47（equity 46 / blocked 1），mismatch_total=3 未变，blocked 当日 2 正常拦截，alerts 33 无新增，refit/进度无变化，链上 cache 2429 / snapshots 36076。
- 2026-10-05 12:12 · CRITICAL（维持）— pid 1477 alive，metrics 18 项，degraded=0，看板 ✅；okx_demo +46（equity 45 / blocked 1）/ paper +0；mismatch_total=3，alerts 29→30 +1 warning（onchain token blocked），链上 cache 2336 / snapshots 37604。
- 2026-10-03 11:12 · CRITICAL（升级：新增 PYTH 灰尘差异）— mismatch_total 2→3，新增 PYTH/USDT local=0.000211 venue=0.000497，INJ 存续；alerts 25→26 +1 warning。
- 2026-10-03 01:12 · CRITICAL（维持）— mismatch_total=2 未变，INJ 灰尘差异存续；okx_demo +48（equity 44 / blocked 4）。
- 2026-10-02 23:12 · CRITICAL（维持）— mismatch_total=2，INJ 灰尘差异；okx_demo +45 全 equity。
- 2026-10-02 06:12 · OK — okx_demo +48 全 equity，mismatch_total=2，alerts 15 无新增。
- 2026-10-02 04:12 · OK — okx_demo +45 全 equity，alerts 15。
- 2026-09-30 20:12 · OK — 进程 37499 于 18:31 重启，order 6 / fill 6，degraded 1（ADA/USDT）。
- 2026-10-01 12:12 · OK — pid 45083，order 1 / fill 1（PENGU）。
- 2026-10-02 03:12 · OK — pid 1477，okx_demo +49 全 equity，README/src onchain discovery mtime 变更。


## 2026-10-06 04:44 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d7h，since 10-01 21:08） | metrics ✅ | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73
- 增量：okx_demo +48（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：无（10-06 尚未产生 blocked 事件）
  判读：无
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 34 行）；增量窗口无新 Traceback/Exception；ADA :29 定点失败模式已停止（日志 mtime 02:29 CST，03:29 / 04:29 两个定点未再复现）
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 snapshots 39368 行（+305），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat 104→108；PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；②在 OKX demo 网页/App 完成 TradFi 免责声明确认以解除 XRP 交易限制；不自动重启、不删 KILL_SWITCH


## 2026-10-06 05:48 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d8h，since 10-01 21:08） | metrics ✅ 16 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73
- 增量：okx_demo +51（50 equity 快照 + 1 blocked）/ paper +0；order +0 / fill +0 / blocked +1 / risk +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：`daily_trade_cap: W/USDT 4 entries already today` ×1（05:15 CST）
  判读：当日累计 Top5 亦为同一条，属于正常拦截
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 34 行）；增量窗口无新 Traceback/Exception；ADA :29 定点失败模式未复现（上次 02:29 CST）
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 snapshots 39674 行（+306），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启，清理灰尘残仓；②在 OKX demo 网页/App 完成 TradFi 免责声明确认以解除 XRP 交易限制；不自动重启、不删 KILL_SWITCH


## 2026-10-06 07:56 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d11h，since 10-01 21:08） | metrics ✅ 23 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73
- 增量：okx_demo +51（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3（W/USDT ×1 + PYTH/USDT ×2）
  判读：全部为正常拦截（new 档每日 4 笔上限）
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 34 行）；增量窗口无新 Traceback/Exception；ADA :29 定点失败未复现（上次 02:29 CST）
- 进度：README.md mtime 07:01 有变更但 §13 五条待办内容无变化（非 git 仓库无法精确 diff）；refit 仍 1 条无新增；链上 snapshots 40301 行（+314），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat 111→113；PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启（重启前确认空仓）；②在 OKX demo 网页/App 完成 TradFi 免责声明确认；不自动重启、不删 KILL_SWITCH


## 2026-10-06 09:05 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d11h，since 10-01 21:08） | metrics ✅ 10 项 | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73 · entries_today 4
- 增量：okx_demo +52（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3（W/USDT 05:15、PYTH/USDT 06:15/06:30）
  判读：全部为正常拦截（new 档每日 4 笔上限，风控在压笔数）
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 34 行，critical 8 行无新增）；service.log 07:56–08:06 CST 有 2 次 ADA fetch_bars 网络重试并瞬时 venue degraded（07:56:56、08:06:39），均已自恢复，当前 gauge=0；增量窗口无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 snapshots 40636 行（+335），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启（重启前确认空仓）；②在 OKX demo 网页/App 完成 TradFi 免责声明确认；不自动重启、不删 KILL_SWITCH


## 2026-10-06 10:09 (CST) · CRITICAL（维持）

- 进程：alive pid=1477（4d13h，since 10-01 21:08） | metrics ✅ | kill switch 0 | degraded 0 | 看板 ✅ :9200
- 权益 103029.83 · 现金 103029.83 · 持仓 0 · 热手 0.00% · 回撤 0.21% · 当日 PnL 0.00% · fees 176.73 · entries_today 4
- 增量：okx_demo +52（全 equity 快照）/ paper +0；order +0 / fill +0 / blocked +0 / 拒单 0 / 对账 ❌（维持）
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3（W/USDT 05:15、PYTH/USDT 06:15/06:30）
  判读：全部为正常拦截（new 档每日 4 笔上限，风控在压笔数）
- 告警增量：warning +0 / critical +0（alerts.jsonl 仍 34 行）；service.log 09:58 CST 1 次 ADA fetch_bars 网络重试并瞬时 venue degraded（01:58 UTC，consecutive_failures=1），已自恢复，当前 gauge=0；增量窗口无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；链上 snapshots 40981 行（+345），evaluate 未跑 → 样本不足以判定
- 异常：对账 ❌ 维持 — `ta_reconcile_mismatch_total=3`（repeat→116；PYTH/USDT + INJ/USDT 灰尘差异结构性存续），`oms.py` 修复仍未部署（进程自 10-01 21:08 未重启）。建议（人决定）：①择机部署 `src/ta/execution/oms.py` 修复并重启（重启前确认空仓）；②在 OKX demo 网页/App 完成 TradFi 免责声明确认；不自动重启、不删 KILL_SWITCH


## 2026-10-06 22:35 (CST) · WARNING

- 服务：runner ✅ pid=750（run_id=e59c93c52c0e，20:13:37 CST 起，已 2h22m）| dashboard ✅ pid=763（:9200 health ok）| watcher ✅（22:33 仍在写链上快照）| **newlisting ❌ NOT RUNNING（exited）**
- 进程：alive | metrics ✅ 16 项 | kill switch 0 | degraded gauge 0（累计 6）| 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103,029.83 · 现金 103,029.83 · 持仓 0 · 灰尘 0.00045 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today 0
- 增量：okx_demo +47（**全 equity 心跳** 21:29:15→22:34:09）/ paper +0；order 0 / fill 0 / blocked 0 / risk 0 / 拒单 0
- blocked by Top3：增量 0 条；当日累计仍为 `daily_trade_cap: 4 entries already today` ×3（W/USDT 13:15、PYTH/USDT 14:15/14:30）
  判读：正常拦截，但**计数口径存疑** —— 当日实际 0 成交却显示"已开 4 笔"，属 F4（跨日结转，重启才清零 = 未修复）
- 告警增量：warning +0 / critical +0（alerts.jsonl 维持 42 行）
- 进度：config|src|scripts 无变更；refit 仍 1 条无新增；§13 待办 1–5 无进展（无 entry_style=maker、timeframe 仍 15m）；链上快照 85,632 行（watcher 持续写入 22:33），evaluate 未跑 → 样本不足以判定
- 异常：
  1. **F7 `newlisting` 服务停摆** —— `launchctl` 显示 `state = NOT RUNNING`（job state = exited）。
     证据：`logs/ta-newlisting-service.err.log` → `/bin/zsh: can't open input file: scripts/daily_research.sh`（仓库里只有 `scripts/daily_research.py`）。
     影响：新币上市跟踪 `new_listing_track` 与波动率监控 `vol_monitor` 不再产出，链上候选池研究侧数据断更。
     建议（人工决策，不代改）：要么改 plist 指向 `daily_research.py`，要么补一个 `daily_research.sh` 包装脚本，然后重新 install 服务。
  2. **F3 对账仍无法确证**（维持）—— 今日 `ta.jsonl` 有 **12 条** `periodic reconciliation skipped`（04:22–16:06 CST），
     原因全部是 `VenueDegraded: get_account failed`（先是网络错误，07:29 起叠加 OKX 50110 IP 白名单）。
     20:13 重启后本轮 run 尚无 skipped 记录，且 `ta_reconcile_mismatch_total` 未暴露（counter=0 不输出），
     → **倾向于 reconcile 已恢复正常且 mismatch 归零，但这是推断不是验证**，需人工 `python3 -m ta reconcile --venue okx_demo --config-dir config` 实锤。
  3. **F1 IP 白名单未修**（根因未解）—— 今日出口 IP 在 `209.9.201.229` 与 `42.200.166.221` 间漂移，均不在 OKX key 白名单。
     当前 gauge 已自恢复，但同一 rticket 会再犯。这是今日零成交的结构性原因之一。
  4. **F2 TradFi 免责声明**（XRP/AVAX 已中招）—— 今日唯一 1 笔下单（AVAX/USDT）即被 54092 拒。
- 今日定性：**连续第二个零成交交易日**。进程健康、账本可信、无资金风险，但交易链路被「人侧待办」卡死（IP 白名单 + 免责声明），策略信号无法落地。


## 2026-10-06 22:39 (CST) · WARNING

- 服务：runner ✅ pid=750（run_id=e59c93c52c0e，20:13:37 CST 起）| dashboard ✅ pid=763（:9200 health ok）| watcher ✅ | **newlisting ❌ exited（err: plist 引用不存在的 scripts/daily_research.sh）→ F7**
- 进程：alive | metrics ✅ 16 项 | kill switch 0 | degraded gauge 0（累计 6）| 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103,029.83 · 现金 103,029.83 · 持仓 0 · 灰尘 0.00046 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today 0
- 增量：okx_demo +5（全 equity 心跳 22:34:09→22:39:03）/ paper +0；order 0 / fill 0 / blocked 0 / risk 0 / 拒单 0
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3（W/USDT 13:15、PYTH/USDT 14:15/14:30）
  判读：正常拦截，但**计数口径存疑** —— 当日实际 0 成交却显示"已开 4 笔"，属 F4（跨日结转，重启才清零 = 未修复）
- 告警增量：warning +0 / critical +0（alerts.jsonl 维持 42 行）；增量窗口 service.log 无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；§13 待办 1–5 无进展；链上 onchain_cache 2871 文件（+2，watcher 持续写入），evaluate 未跑 → 样本不足以判定
- 异常：
  1. **F7 `newlisting` 服务停摆** —— `launchctl` 显示 `state = not running / job state = exited`。证据：`logs/ta-newlisting-service.err.log` → `/bin/zsh: can't open input file: scripts/daily_research.sh`（仓库只有 `scripts/daily_research.py`）。建议（人决策）：改 plist 指向 `daily_research.py` 或补包装脚本后重装服务；本服务只读监控，不自动拉起。
  2. **F3 对账无法确证归零**（维持）—— 20:13 重启后本轮 run 尚无 `periodic reconciliation skipped` 记录，`ta_reconcile_mismatch_total` 未暴露（counter=0 不输出），**倾向 reconcile 已恢复且 mismatch 归零，但未人工验证**。建议：恢复后跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 实锤。
  3. **F1 IP 白名单未修**（根因未解）—— 今日出口 IP 在 `209.9.201.229` 与 `42.200.166.221` 间漂移，均不在 OKX key 白名单；当前 gauge 已自恢复，但会再犯。建议：OKX 控制台加白名单或固定 `TA_PROXY` 出口。
  4. **F2 TradFi 免责声明** —— 今日唯一 1 笔下单（AVAX/USDT 11:01 CST）被 54092 拒；XRP 此前已中招。建议：OKX demo 网页/App 完成免责声明确认。
  5. **F6 计数器未暴露** —— metrics 仍无 `ta_orders_submitted_total` / `ta_fills_total` / `ta_reconcile_*` 计数器，属已知间歇问题，日报提一句。
- 今日定性：**连续第二个零成交交易日**。进程健康、账本可信、无资金风险，交易链路被人侧待办（IP 白名单 + 免责声明）卡死，策略信号无法落地。不自动重启 runner、不删 KILL_SWITCH、不改 config/src。


## 2026-10-06 23:45 (CST) · WARNING

- 服务：runner ✅ pid=750（run_id=e59c93c52c0e，20:13:37 CST 起）| dashboard ✅ pid=763（:9200 health ok）| watcher ✅ | **newlisting ❌ exited（err: plist 引用不存在的 scripts/daily_research.sh）→ F7**
- 进程：alive | metrics ✅ 18 项 | kill switch 0 | degraded gauge 0（degraded_streak 0）| 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103,029.83 · 现金 103,029.83 · 持仓 0 · 灰尘 0.00046 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today 1
- 增量：okx_demo +48（46 equity + 1 order + 1 risk）/ paper +0；order 1（AVAX/USDT 23:01 CST 被拒）/ fill 0 / blocked 0 / 拒单 1 / 对账 ❓（metrics 无 reconcile_mismatch，无 skipped 记录，倾向归零未验证）
- blocked by Top3：增量 0 条；当日累计 Top5：`daily_trade_cap: 4 entries already today` ×3（05:15 / 06:15 / 06:30 CST）
  判读：正常拦截，但**当日实际 0 成交却显示"已开 4 笔"** → F4 跨日结转口径 bug（重启才清零 = 未修复）
- 告警增量：warning +1 / critical +0（alerts.jsonl 42→43：23:01:17 CST AVAX/USDT order rejected 54092）；增量窗口 service.log 无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更；refit 仍 1 条无新增；§13 待办 1–5 无进展；链上 onchain_cache 2871 文件（121 个 mtime 更新），snapshots.jsonl 42,951 行，evaluate 未跑 → 样本不足以判定
- 异常：
  1. **F7 `newlisting` 服务停摆** —— `launchctl` 显示 `state = not running / job state = exited`。证据：`logs/ta-newlisting-service.err.log` → `/bin/zsh: can't open input file: scripts/daily_research.sh`（仓库只有 `scripts/daily_research.py`）。建议（人决策）：改 plist 指向 `daily_research.py` 或补包装脚本后重装服务；本服务只读监控，不自动拉起。
  2. **F3 对账无法确证归零**（维持）—— 20:13 重启后本轮 run 尚无 `periodic reconciliation skipped` 记录，`ta_reconcile_mismatch_total` 未暴露（counter=0 不输出），**倾向 reconcile 已恢复且 mismatch 归零，但未人工验证**。建议：跑 `python3 -m ta reconcile --venue okx_demo --config-dir config` 实锤。
  3. **F1 IP 白名单未修**（根因未解）—— 今日出口 IP 在 `209.9.201.229` 与 `42.200.166.221` 间漂移，均不在 OKX key 白名单；当前 gauge 已自恢复，但会再犯。建议：OKX 控制台加白名单或固定 `TA_PROXY` 出口。
  4. **F2 TradFi 免责声明** —— 今日第 2 笔下单（AVAX/USDT 23:01 CST）再被 54092 拒；今日累计拒单 2 笔（<3 未达 CRITICAL）。建议：OKX demo 网页/App 完成免责声明确认。
  5. **F4 `entries_today` 跨日结转** —— 当日 0 成交，但 blocked by 显示 daily_trade_cap 已用 4 笔；计数器未在 CST 日边界重置，靠重启清零 = 未修复。
  6. **F6 计数器未暴露** —— metrics 仍无 `ta_fills_total` / `ta_reconcile_*` 计数器，但 `ta_orders_submitted_total` 已出现（=1），属已知间歇问题。
- 今日定性：**连续第二个零成交交易日**。进程健康、账本可信、无资金风险，交易链路仍被人侧待办（IP 白名单 + 免责声明）卡死，策略信号无法落地。不自动重启 runner、不删 KILL_SWITCH、不改 config/src。


## 2026-10-06 23:55 (CST) · WARNING（一页纸节点巡检）

- 服务：runner ✅ pid=750（run_id=e59c93c52c0e，20:13:37 CST 起）| dashboard ✅ pid=763（:9200 health ok）| watcher ✅ | **newlisting ❌ exited（err: plist 引用不存在的 scripts/daily_research.sh）→ F7**
- 进程：alive | metrics ✅ 18 项 | kill switch 0 | degraded gauge 0（degraded_streak 0）| 看板 ✅ :9200 | 无 KILL_SWITCH
- 权益 103,029.83 · 现金 103,029.83 · 持仓 0 · 灰尘 0.00046 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · entries_today 1
- 当日账本（okx_demo）：equity 713 / order 2 / fill 0 / blocked 3 / risk 10；paper +0；拒单 2（AVAX 54092）/ 对账 ❓（20:11 重启后无 skipped，mismatch 计数器未暴露，倾向归零未人工验证）
- blocked by Top3：`daily_trade_cap: 4 entries already today` ×3（W/USDT 05:15、PYTH/USDT 06:15/06:30）
  判读：正常拦截，但当日 0 成交却显示 4 entries → F4 跨日结转口径 bug（靠 20:11 重启清零 = 未修复）
- 告警当日：critical 8 / warning 2（`venue degraded - new entries suspended` ×8、`order rejected` ×2）；增量窗口 service.log 无新 Traceback/Exception
- 进度：config|src|scripts|README 无变更（仅 `scripts/daily_review.py` mtime 因本自动化更新）；refit 仍 1 条无新增；§13 待办 1–5 无进展；链上 onchain_cache 2,869 文件，snapshots.jsonl 42,970 行（+306 今日），evaluate 未跑 → 样本不足以判定
- 一页纸已生成：`reports/2026-10-06-one-pager.md`；watch-state.json 已更新 last_date / one_pager_generated
- 异常：F1 OKX IP 白名单 50110 根因未修；F2 AVAX 54092 TradFi 免责声明；F3 reconcile 归零待人工验证；F4 entries_today 跨日结转；F7 newlisting 服务退出。明日 3 件事见人定：① OKX 加白名单/固定代理；② OKX demo 确认免责声明；③ 修 newlisting plist + 跑 reconcile 验证。


## 2026-10-07 10:54 (CST) · CRITICAL（当日拒单 3 次 ≥3）

- 服务：runner ✅ pid=750（run_id=e59c93c52c0e，10-06 20:13 CST 起，已运行 14h40m）| dashboard ✅ pid=763（`:9200/api/health` = `{"status":"ok"}`）| watcher ✅ pid=747 | **newlisting ❌ exited → F7**
- 进程：pid 750 alive | metrics ✅ 17 项 | kill switch 0（无 KILL_SWITCH 文件）| degraded gauge 0（streak 0）| 无 KILL_SWITCH
- 权益 103,029.83 · 现金 103,029.83 · 持仓 0 · 灰尘 0.00044 · 热手 0.00% · 回撤 0.00% · 当日 PnL 0.00% · fees 176.73 · **entries_today 2** · unmanaged_notional 3.8e-05≈0（§14 基线：起 103,361.13，无手动底仓 ✅）
- 增量：okx_demo 6753→6815 **+62**（equity 56 / order 2 / risk 2 / blocked 2）/ paper 6596 **+0**；**fill 0**、tag=adopted 已排除；增量窗口拒单 2；**当日累计拒单 3**
- 当日（CST 边界 1791302400 s）：equity 504 / order 3（全 rejected）/ risk 3 / blocked 2 / **成交 0**；equity 波动 103029.830051–103029.830074（≈0）
- blocked by Top3（增量）：`symbol_daily_cap: OKB/USDT 2/2 today` ×2（10:30、10:45 CST）
  当日累计 Top5：同上 2 条 —— 判读：**正常拦截**（OKB 单标的日上限 2 笔，非配置事故）。⚠️ 本次**无** `daily_trade_cap: N entries already today` → **F4 未复发**（与 09:39 段"连续 9 次"不同）；但 metrics `ta_entries_today=2` 仍含 10-06 结转，口径待核
- **拒单 3（触 CRITICAL 阈值）**：① 03:45 AVAX/USDT 市价买 360.97 → sCode **54092** TradFi 免责声明未确认（**F2**）；② 10:00 OKB/USDT → sCode **51000** `Parameter tdMode error`；③ 10:16 OKB/USDT → sCode **51000** 同上。②③ 为**新增参数事故**（tdMode 传参与该账户/标的模式不匹配），与今日 10:01–10:14 的 `config/strategies.toml` + `src/ta/strategies/perp_trend_funding.py` + `src/ta/funding.py` 变更同期出现，倾向新策略/新标的参数未对齐，属**配置事故而非正常拦截**
- 告警增量：alerts 44→46（warning **+2** / critical **+0**）；两条均为 `order rejected OKB/USDT 51000`（02:00、02:16 UTC）。当日 warning 3 / critical 0
- 日志：今日 `logs/ta.jsonl` 无 ERROR / Traceback（0）；四个 service.log 近 200 行无新 Traceback（`ta-run-service.log` 最后 ERROR 仍为 10-06 20:17–20:20 CST 的 F1 50110 旧波）；ADA/USDT `fetch_bars` 网络重试 2 次（10:54 CST，已知噪声，degraded=0，不计数）
- 对账：❓ `ta_reconcile_mismatch_total` 未暴露（counter=0，F6），当前 run 无 `periodic reconciliation skipped`（历史 8 条，最后 10-06 16:06 CST）→ 倾向归零，**未人工验证**
- 进度：`config/strategies.toml`(10:10)、`src/ta/funding.py`(10:14)、`src/ta/strategies/perp_trend_funding.py`(10:11)、`README.md`(10:01) 在窗口内变更（疑新增资金费趋势策略 + OKB mean_reversion 配置）；refit 仍 1 条无新增；§13 五条待办无进展、§14 决议未变（maker 未上线 / $600 上限已取消）；链上 `data/onchain/snapshots.jsonl` 44,781 行（+138 vs 上巡 44,643），`evaluate` 未跑 → **样本不足以判定**
- 异常清单：
  1. **F2（维持）+ 参数事故（新）** —— 当日拒单 3 达 CRITICAL。建议（人决策）：① OKX demo 网页/App 完成 TradFi 免责声明确认（解 AVAX 54092）；② 核对 OKB/USDT 下单 `tdMode`（cross/isolated/cash）与账户模式是否匹配，必要时回滚 10:01–10:14 的 strategies.toml / perp_trend_funding 变更
  2. **F7 newlisting 停摆（维持）** —— `logs/ta-newlisting-service.err.log`：`can't open input file: scripts/daily_research.sh`（仓库只有 `daily_research.py`）。建议：改 plist 指向 `.py` 或补包装脚本后重装；只读监控，不自动拉起
  3. **F6 counter 未暴露（维持）** —— metrics 无 `ta_reconcile_mismatch_total` / `ta_fills_total`；`ta_orders_blocked_total=2` 与账本当日 blocked 2 一致，但与历史累计口径仍不同（已知间歇注册问题）
  4. **F1 根因未修（自愈中）** —— 50110 最后一次 10-06 20:20 CST，此后 ~14.5h 零复发；出口 IP 漂移风险仍在，建议加白名单或固定 `TA_PROXY`
  5. **F4 本次未复发** —— 当日无 `daily_trade_cap` blocked；但 `entries_today=2` 未随 CST 日界清零，口径仍待修
- 今日定性：**连续第三个零成交交易日**。进程/账本健康、无资金风险，交易链路卡在人侧（TradFi 免责声明 + 新增 OKB tdMode 参数）。不重启 runner、不改 config/src、不删 KILL_SWITCH
