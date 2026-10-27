# OKX 模拟盘生产系统与实盘前置安全审计（2026-10-27）

## 结论

**模拟盘生产链可用，但不能进入实盘。**

- 运行态：v1.1.27，`runner` / `watcher` / `dashboard` 三个 launchd 服务均 running；
  runner PID 56975、watcher 56979、dashboard 56977；`lifecycle.py verify` PASS。
- 交易安全链：风控闸门、kill switch、订单/成交账本、周期对账、carry 双腿守恒、
  运行态 source/config 指纹、每日复盘和不可变 release 均存在且可复核。
- 实盘门槛：`live_readiness.py` 仍为 **0/5**。当前不是“差一个开关”，而是策略边缘、
  样本、执行、管道和资金费证据都未达到预设门槛。
- 密钥：实盘 key/secret/passphrase 没有进入 Git；但 **OKX Demo API key ID** 曾从
  2026-10-06 的 OKX 错误原文进入公开 changelog，并随 38 个历史 commit / 49 个路径传播。
  当前工作树和归档已脱敏，公开历史仍保留旧值，因此**必须吊销并重建 Demo key**。
- 看板：之前允许 `Origin: null`，恶意 sandboxed iframe 可能读取本机仓位与盈亏；
  v1.1.28 已改为只允许看板自身的精确 loopback origin，并加安全响应头。

## 1. 完整性与闭环性

| 环节 | 当前证据 | 判定 |
|---|---|---|
| 进程与端口 | launchd 三服务 running；9101/9200 仅监听 `127.0.0.1` | 通过 |
| 部署身份 | runtime v1.1.27、Git `d6378fe`、tag v1.1.27、source/config 指纹一致、`deployment_ok=true` | 通过 |
| 交易风控 | 单笔/仓位/总敞口/heat/相关性/日损/峰值回撤/stale data/kill switch | 通过 |
| 账本与对账 | 周期对账、position/cash 分类、carry 两腿守恒、盯市与资金费入账 | 通过，但仍有 28 条历史未解释差异 |
| 观测 | metrics、看板、watchdog、alerts、外部 webhook 接口 | 本机通过；**外部 webhook 未配置** |
| 发布与回滚 | CI verify、release artifact、不可变 tag、provenance、rollback plan | 通过 |
| 自动化测试 | Python 3.11 全量 **513 passed** | 通过 |
| 代码卫生 | 全仓默认 Ruff 规则仍有大量历史告警，CI 当前以 pytest + 一致性/安全门禁为主 | **未闭环，需后续分批清理** |

最新 metrics：equity `$104,165.04`、10 个持仓、fees `$676.42`、kill switch `0`、
venue degraded `0`。runner 刚在 22:23 CST 重启，因此 ticks / fills 计数器从 1 / 0 重新累计。

## 2. 实盘门槛（不可跳过）

`python3 scripts/live_readiness.py`：

| # | 门槛 | 当前 | 状态 |
|---|---|---|---|
| 1 | 至少一条腿/策略样本外扣成本后为正 | 0/4 条可判策略为正 | 未满足 |
| 2 | ≥200 笔成交或 ≥60 个交易日 | 177 笔 / 27 天 | 未满足 |
| 3 | 连续 30 天无未解释对账差异/重复拒单/事故 | 7/30 天 | 未满足 |
| 4 | 每日任务连续 14 天 ≥2 轮且无自身红轮 | 1/14 天 | 未满足 |
| 5 | carry 非尖峰 ≥30 天且换腿触发过 | 9/30 天；换腿已触发 9 次 | 未满足 |
| 6 | 真实滑点/深度/期权流动性 | 只能实盘最小仓验证 | 不可自动判 |

因此下一步仍是模拟盘与只读研究，不应下真钱单。

## 3. API key 与 GitHub 暴露面

### 已确认暴露

- 暴露对象：**OKX Demo API key ID**，不是 secret、不是 passphrase，也不是任何 live key。
- 来源：2026-10-06 故障复盘中引用了 OKX 50110 原文，原文包含 key ID。
- 范围：公开 `main`、公开 `v1.1.27` tag、38 个可达历史 commit、49 个文件路径。
- 风险：没有 secret 无法完成请求签名，实际可利用性低；但 key ID 不是公开信息，
  仍按泄露处理。
- 已做：当前工作树、47 个当前/归档文件和本地日志/备份已替换为
  `[REDACTED_OKX_DEMO_API_KEY]`；看板 `/api/snapshot` 已不再返回该值。
- 待做：**在 OKX 吊销该 Demo key，创建新 Demo key，更新 `.env`，再重启 runner/dashboard。**
  历史 commit/tag 不能在“禁止 force push + 不可变 tag”的前提下安全改写；轮换后旧 key
  自然失效，这比破坏发布历史更可靠。

### 未发现暴露

- `.env.live` 的 key、secret、passphrase：当前树和历史 commit 均无命中。
- `.env` 的 Demo secret、passphrase：无命中。
- AWS/GitHub/Slack/OpenAI/Google token、JWT、PEM 私钥：当前与历史对象扫描均为 0。
- `.env`、`.env.live`、GitHub SSH 私钥权限均为 `600`；`.env*` 已被 `.gitignore` 排除。
- 仓库 Public、fork 数 0；`main` protected，必需 `verify` 生效。

### 当前 key 权限

- OKX Demo key：实际读取为 `read_only,trade`，**没有 withdraw 权限**。
- OKX Live key：请求返回 50110 IP whitelist 拒绝，当前无法完成权限审计。
- 当前代理出口：`209.9.202.79`；用户已加入白名单的是 `42.200.231.241`，两者不一致。
- 结论：动态出口 IP 与 IP 白名单天然冲突。实盘前必须固定专用出口，再只把该 IP
  加入 live key 白名单；不要让 live 交易继续依赖轮换代理。

## 4. 已在 v1.1.28 补上的代码闸门

1. 真实资金下单必须同时满足：
   `app.mode=live` + venue `sandbox=false` + `--allow-live` + metrics 端口 + 外部告警 webhook。
2. OKX live key 启动时读取权限；包含 `withdraw` 或不含 `trade` 时直接拒绝启动。
3. 看板 CORS 不再接受 `Origin: null`，只接受看板自身精确 loopback origin；
   加 `nosniff` / `X-Frame-Options: DENY` / `Referrer-Policy: no-referrer`。
4. `scripts/security_audit.py` 比较 `.env*` 的真实值与全部 tracked files；`--history`
   搜索可达 Git 历史。它进入 `lifecycle.py preflight` 与 CI。
5. provenance 新增 `app_mode`、`venue_sandbox`、`live_confirmed`，部署验收能看出
   当前运行的是 demo 还是经过二次确认的 real-money 模式。

## 5. 实盘前必须由用户完成

| 优先级 | 动作 | 完成判据 |
|---|---|---|
| P0 | 吊销旧 OKX Demo key，创建新 key | `security_audit.py --history` 不再命中当前 `.env` 的 key |
| P0 | 固定代理出口并只给 live key 白名单该 IP | live `/account/config` 成功，perm 只含 read+trade |
| P0 | live key 禁用 withdraw，使用独立低资金子账户 | 审计返回 `read_only,trade`，无 `withdraw` |
| P1 | 配置 `TA_ALERT_WEBHOOK_URL` | 端口/告警故障可推送到手机或外部系统 |
| P1 | 收口 live readiness 0/5 | 至少策略边缘、样本、执行和管道达到脚本门槛 |
| P1 | 部署 v1.1.28 并验收 | `lifecycle.py verify` PASS，provenance 显示 demo/sandbox |

复核命令：

```bash
python3 scripts/lifecycle.py verify
python3 scripts/live_readiness.py
python3 scripts/security_audit.py
python3 scripts/security_audit.py --history
git ls-files '.env*' '*.pem' '*.key' '*.p12' '*.pfx'
```
