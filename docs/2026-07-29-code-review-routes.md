# Fund-Assessment 路由层与数据层代码审查报告

- **审查日期**: 2026-07-29
- **审查范围**: `web/routes/`(12)、`src/strategies/`(7)、`src/monitor/`(2)、`src/utils/`(5)、`src/core/` 数据层(3)
- **审查模式**: 只读分析,未修改任何源代码
- **审查维度**: 逻辑正确性 / 性能 / 安全 / 异常处理 / 可维护性(每项 1-5 分)

---

## 一、写端点鉴权清单

`Depends(require_admin)` 覆盖情况(仅列 POST/PUT/DELETE 等写端点;GET 视为公开读接口):

| 端点路径 | 方法 | 文件:行号 | 是否鉴权 | 风险说明 |
|---|---|---|---|---|
| `/api/config/settings` | PUT | `web/routes/config.py:109` | ✅ 有 | — |
| `/api/config/strategies` | PUT | `web/routes/config.py:124` | ✅ 有 | — |
| `/api/config/user_positions` | POST | `web/routes/config.py:189` | ✅ 有 | — |
| `/api/fund/positions` | POST | `web/routes/fund.py:138` | ✅ 有 | — |
| `/api/fund/positions/add` | POST | `web/routes/fund.py:155` | ✅ 有 | — |
| `/api/fund/positions/{fund_code}` | DELETE | `web/routes/fund.py:166` | ✅ 有 | — |
| `/api/trade/buy` | POST | `web/routes/trade.py:80` | ✅ 有 | — |
| `/api/trade/sell` | POST | `web/routes/trade.py:85` | ✅ 有 | — |
| `/api/trade/cancel` | POST | `web/routes/trade.py:90` | ✅ 有 | — |
| `/api/agent/analyze` | POST | `web/routes/agent.py:34` | ❌ **无** | 触发 LLM 深度分析,高成本 |
| `/api/agent/quick_analysis` | POST | `web/routes/agent.py:74` | ❌ **无** | 触发 LLM 调用 |
| `/api/agent/multi_analyze` | POST | `web/routes/agent.py:80` | ❌ **无** | 触发 LLM 多智能体调用 |
| `/api/agent/portfolio_advice` | POST | `web/routes/agent.py:90` | ❌ **无** | 触发 LLM 组合分析 |
| `/api/agent/fund_analyze` | POST | `web/routes/agent.py:112` | ❌ **无** | 7 角色 LLM 辩论,成本最高;仅有限流,无鉴权 |
| `/api/config/test_notify` | POST | `web/routes/config.py:132` | ❌ **无** | 主动发送钉钉/企业微信通知,可被滥用刷消息 |
| `/api/monitor/watchlist` | POST | `web/routes/monitor.py:186` | ❌ **无** | 修改自选股(写本地文件) |
| `/api/monitor/watchlist/{stock_code}` | DELETE | `web/routes/monitor.py:194` | ❌ **无** | 删除自选股 |
| `/api/scripts/generate` | POST | `web/routes/scripts.py:78` | ❌ **无** | 话术生成(潜在 AI 成本) |
| `/api/scripts/match/fund` | POST | `web/routes/scripts.py:87` | ❌ **无** | 低风险(模板匹配) |
| `/api/scripts/match/stock` | POST | `web/routes/scripts.py:94` | ❌ **无** | 低风险(模板匹配) |
| `/api/strategy/analyze` | POST | `web/routes/strategy.py:221` | ❌ **无** | 策略计算,中等成本 |
| `/api/strategy/backtest` | POST | `web/routes/strategy.py:338` | ❌ **无** | 回测重计算,可被滥用做 DoS |
| `/api/news/search` | POST | `web/routes/news.py:83` | ❌ **无** | 低风险(检索) |

**统计**: 9 个写端点有鉴权,13 个写端点无鉴权。其中 5 个 agent 端点 + `test_notify` + `backtest` 滥用风险最高(LLM 成本 / 消息轰炸 / 计算资源耗尽)。

---

## 二、问题清单(按严重度分级)

### P0 — 严重(必须修复)

#### P0-1 `bspro_quant.py` 回测存在未来函数泄露 + 回撤计算跨股拼接错误
- **文件:行号**: `src/strategies/bspro_quant.py:215-281`
- **描述**:
  1. **未来函数泄露**: `backtest_strategy` 在 234-250 行用**当前**因子(`df.iloc[-1]` 即最新一期)对所有股票打分选出 `top_stocks`,随后在 252-258 行用 `(close.iloc[-1] / close.iloc[-period] - 1)` 计算"收益"——这本质是用**今天**的因子选股,再衡量**过去 N 天**的收益。真实回测应是 T 时刻用 T 及之前的数据选股,再统计 T→T+period 的未来收益。当前实现相当于"用结果解释原因",回测结果完全失真且偏乐观。
  2. **回撤计算错误**: 266-281 行把多只股票的日线收益 `all_returns` 拼成一个一维序列后做 `cumsum().cummax()`,把**不同股票**的收益当成一条连续净值曲线来算最大回撤,数值无任何统计意义。
- **修复建议**:
  - 改为事件驱动回测:按日迭代,在 T 日用 T-1 及之前数据计算因子并选股,T 日收盘买入,T+period 卖出,统计该笔真实收益。
  - 回撤应按"单只股票各自 cummax 再取全局 min",或按组合净值的 cummax 计算,而非跨股拼接。

---

### P1 — 高(应尽快修复)

#### P1-1 13 个写端点缺少 `Depends(require_admin)` 鉴权
- **文件:行号**: 见上方鉴权清单中标 ❌ 的 13 个端点
- **描述**: agent 系列 5 个 LLM 端点、`config/test_notify`、`monitor/watchlist` POST/DELETE、`scripts/generate`、`strategy/analyze`、`strategy/backtest` 均无鉴权。任何匿名访客可触发高成本 LLM 调用、刷企业通知、篡改自选股、耗尽回测算力。
- **修复建议**: 对所有写端点统一加 `dependencies=[Depends(require_admin)]`;对 LLM/回测类高成本端点叠加限流(已有 `fund_analyze` 的 slowapi 限流模式可复用)。

#### P1-2 `auth.py` fail-open:ADMIN_TOKEN 未配置时放行所有受保护端点
- **文件:行号**: `src/utils/auth.py:50-56`
- **描述**: `require_admin` 在 `expected` 为空时仅 `logger.warning` 后 `return`,不抛 401。生产环境若忘记设置 `ADMIN_TOKEN`,则即使端点声明了 `Depends(require_admin)`,实际**任何人都能调用** buy/sell/settings 等敏感操作。这是"默认开放"的反安全模式。
- **修复建议**: 生产环境(`app_env == "production"`)未配置 token 时应 `raise HTTPException(500, "ADMIN_TOKEN 未配置,拒绝服务")`;仅开发环境放行。

#### P1-3 `stock_monitor.py` `_check_eps_surprise` 永真条件 bug
- **文件:行号**: `src/strategies/stock_monitor.py:120`
- **描述**: `if eps > 0 and eps * 1.04 > eps:` 对任意正数 `eps` 恒成立(`1.04*eps > eps`),导致只要 EPS 为正就一定触发"EPS 增长 4% 超阈值"告警。原意应是对比历史 EPS 增长率,但代码并未取历史值。
- **修复建议**: 取上一期 EPS 做同比,如 `prev_eps = float(df.iloc[1].get("每股收益",0))`,判断 `(eps - prev_eps)/abs(prev_eps)*100 > 2`。

#### P1-4 `stock_monitor.py` `_check_sector_rotation` 严重度反转 + 分支不可达
- **文件:行号**: `src/strategies/stock_monitor.py:272-287`
- **描述**: 第一个 `if sector_net_pct > 10 and stock_change > 5` 命中后直接 `return`(severity="high");第二个 `if sector_net_pct > 10 and stock_change > 10` 永远不可达(因为 `>10` 蕴含 `>5`,已被前一条件拦截)。且严重度倒挂:涨幅 5% 标 "high"、涨幅 10%+ 反而标 "medium"。
- **修复建议**: 调整阈值顺序,先判断更极端条件;或合并为单一条件按幅度分级。

#### P1-5 `limit_up.py` `_determine_level` 几乎总返回 FIRST
- **文件:行号**: `src/strategies/limit_up.py:40-64`
- **描述**: `_load_history` 只加载**当日**涨停池(`pd.Timestamp.now()`),`_history[code]` 每只股票至多 1 条记录,`_determine_level` 中 `count = len(...)` 几乎总是 1,因此永远返回 `FIRST`。"二板/三板+"判定形同虚设。
- **修复建议**: 持久化历史涨停记录到 sqlite/文件,`_load_history` 读取近 N 日数据再统计连板数。

#### P1-6 `cb_t0_sniper.py` `monitor_cb` 前收硬编码 10% 涨停假设
- **文件:行号**: `src/strategies/cb_t0_sniper.py:182`
- **描述**: `prev_close = stock_price / (1 + 0.10)` 硬编码正股昨日涨停 10% 来反推前收,再判断 `stock_price < prev_close` 发"正股走弱"信号。对非涨停股、科创板/创业板(20%)、ST 股(5%)均不成立,信号逻辑错误。
- **修复建议**: 直接从行情接口取真实 `prev_close`,不要反推。

#### P1-7 `fund.py` `today_pnl` 在 change_pct==0 时误返总盈亏
- **文件:行号**: `web/routes/fund.py:122-123`
- **描述**: `today_pnl = sum(e["pnl_amount"] - (e["pnl_amount"] / (1 + e["change_pct"] / 100) if e["change_pct"] != 0 else 0) ...)`。当 `change_pct == 0` 时,三项式取 `0`,于是该项贡献为 `pnl_amount - 0 = pnl_amount`(即累计总盈亏),而非"今日盈亏=0"。这会让无波动的基金把全部累计盈亏算进"今日盈亏",汇总值严重虚高。
- **修复建议**: 今日盈亏应 = `market_value * change_pct/100`,与累计盈亏解耦;`change_pct==0` 时该项贡献 0。

---

### P2 — 中(建议修复)

#### P2-1 多策略串行调用 akshare,存在 N+1 / 全量拉取
- **文件:行号**:
  - `src/strategies/bspro_quant.py:201-213` `rank_by_factor` 对 `stock_pool` 逐只调 `compute_factors`(每只 5+ 次 akshare 请求),全程串行
  - `src/strategies/stock_monitor.py:292-317` `check_alerts` 串行跑 7 个 checker,每个又多次拉 akshare
  - `src/strategies/trading_quant.py:228-255` `stock_analysis` 串行调 5 个 `_score_*`
- **描述**: 单次分析可能触发 10-30 次串行网络请求,响应耗时数十秒。
- **修复建议**: 复用 `data_source_v2._parallel_fetch` 的 ThreadPoolExecutor 并发;对全市场快照类接口(`stock_zh_a_spot_em`)结果做内存复用而非每只股票重拉。

#### P2-2 用全市场快照取单股数据(N+1 反模式)
- **文件:行号**:
  - `src/core/data_source.py:112` `AkShareSource.get_realtime_quote` 调 `ak.stock_zh_a_spot_em()`(全市场 ~5000 行)只为取 1 只
  - `src/strategies/stock_monitor.py:48` `_get_spot_data` 同上,且在 `check_alerts` 内被多个 checker 重复调用
  - `src/strategies/trading_quant.py:182` `_score_sentiment` 同上
  - `src/strategies/cb_t0_sniper.py:98-107` `_get_stock_price` 每只涨停股各拉一次全市场快照
- **修复建议**: 单股实时行情改用 `data_source_v2.get_realtime_quote_tencent`(已带缓存/超时);`check_alerts` 入口拉一次快照后传入各 checker。

#### P2-3 `cb_t0_sniper.scan_cb_opportunities` 每只涨停股重复拉全量快照
- **文件:行号**: `src/strategies/cb_t0_sniper.py:109-155`
- **描述**: 对每只涨停股分别调 `_get_cb_price`(拉全量可转债行情)和 `_get_stock_price`(拉全量 A 股行情),N 只涨停股 = 2N 次全量请求。
- **修复建议**: 进入循环前各拉一次,构建 code→price 字典后查表。

#### P2-4 `data_source.py` TushareSource 调用无超时
- **文件:行号**: `src/core/data_source.py:249` `self.pro.daily(...)` 等
- **描述**: tushare SDK 调用未配置 timeout,网络异常时可能长时间挂起。
- **修复建议**: tushare 不支持直接传 timeout,可用 `concurrent.futures` + `as_completed(timeout=...)` 包裹,或切到 `data_source_v2` 的 requests 体系。

#### P2-5 `strategy.py /backtest` 无鉴权重计算可被 DoS
- **文件:行号**: `web/routes/strategy.py:338-364`
- **描述**: 回测对 30 只股票各做 5+ 次 akshare 调用 + 因子计算,单次耗时很长且无鉴权,匿名用户可并发触发耗尽 worker。
- **修复建议**: 加 `Depends(require_admin)` + 限流;或限制并发数。

#### P2-6 `monitor.py` watchlist 文件存储无并发控制
- **文件:行号**: `web/routes/monitor.py:66-86,186-201`
- **描述**: `_WATCHLIST_FILE` 为 `web/user_watchlist.json`,读写无文件锁,并发请求可能写坏 JSON;且文件落在 web 目录下而非 data 目录。
- **修复建议**: 加 `threading.Lock` 或 `filelock`;路径移到 `data/` 下。

#### P2-7 `trade.py` history `limit` 参数无上限
- **文件:行号**: `web/routes/trade.py:152` `limit: int = 50`
- **描述**: 未限制最大值,可传 `limit=999999` 拉取全表。
- **修复建议**: `limit: int = Query(50, ge=1, le=500)`。

#### P2-8 `agent.py` `_decision_history` 进程内全局列表
- **文件:行号**: `web/routes/agent.py:13,38,84`
- **描述**: 多 worker 部署时各进程历史不共享;列表虽 capped=100 但每次 `pop(0)` 是 O(n)。
- **修复建议**: 改 `collections.deque(maxlen=100)`;持久化到 sqlite 或 redis。

#### P2-9 `limit_up.py` 重复拉取涨停池
- **文件:行号**: `src/strategies/limit_up.py:125-153`
- **描述**: `scan_limit_up` 先调 `_load_history()`(内部拉一次 `stock_zt_pool_em`),又在 129 行再拉一次同一接口,当日数据重复请求。
- **修复建议**: 复用 `_load_history` 已拉取的 DataFrame。

#### P2-10 `limit_up.py` `_determine_reason` 拉取但不使用结果
- **文件:行号**: `src/strategies/limit_up.py:66-82`
- **描述**: 调 `ak.stock_board_concept_name_em()`(全量题材板块)仅判断非空就返回 `THEME`,未用到任何具体字段,浪费一次重请求。
- **修复建议**: 删除该调用,或真正用板块数据判断个股是否命中热点题材。

---

### P3 — 低(可选改进)

#### P3-1 `cache.py` `except Exception: pass` 静默吞异常
- **文件:行号**: `src/core/cache.py:94-95, 118-119`
- **描述**: 缓存读写失败被静默吞掉,无日志。虽是缓存可降级,但完全无日志会掩盖持久化故障。
- **修复建议**: 改为 `logger.debug` 记录。

#### P3-2 `config.py` `_strip_masked` 死代码分支
- **文件:行号**: `web/routes/config.py:52-55`
- **描述**: `if not (isinstance(v, dict) and not stripped): result[k] = stripped else: result[k] = stripped` 两个分支执行相同赋值,条件判断无意义。
- **修复建议**: 删除冗余 if/else,直接 `result[k] = stripped`。

#### P3-3 `monitor/alert.py`、`monitor/daily_flow.py` 用 stdlib logging 与全局 loguru 不一致
- **文件:行号**: `src/monitor/alert.py:5,7`、`src/monitor/daily_flow.py:3,8`
- **描述**: 全项目用 loguru,这两个文件用 `logging.getLogger(__name__)`,日志格式/级别/输出位置不统一。
- **修复建议**: 统一改 `from src.utils.logger import get_logger`。

#### P3-4 `data_source_v2.py` 模块级 ThreadPoolExecutor 无 shutdown
- **文件:行号**: `src/core/data_source_v2.py:34`
- **描述**: `_thread_pool = ThreadPoolExecutor(max_workers=6)` 进程退出前未 `shutdown()`,正常情况下解释器会回收,属轻微资源管理瑕疵。
- **修复建议**: 注册 `atexit.register(_thread_pool.shutdown)` 或用 `contextlib`。

#### P3-5 `strategy.py` 调用 `ds2._get_stock_ranking_sina` 私有方法
- **文件:行号**: `web/routes/strategy.py:267`
- **描述**: 访问下划线开头的"私有"函数,违反封装约定,后续重构易被破坏。
- **修复建议**: 在 `data_source_v2` 暴露公开别名,或调整调用方。

#### P3-6 多策略 `_get_kline` 缓存键不含 days
- **文件:行号**: `src/strategies/bspro_quant.py:58-60`、`src/strategies/a_stock_analyst.py:52-54`
- **描述**: 缓存键只含 `stock_code`,先用 `days=250` 调用后再用 `days=60` 会返回 250 条的缓存,与 `trading_quant.py:47` 的 `f"{stock_code}_{period}_{days}"` 复合键不一致。
- **修复建议**: 统一用复合键。

#### P3-7 `trading_quant.py` 市场判断不处理北交所
- **文件:行号**: `src/strategies/trading_quant.py:100,271`
- **描述**: `market="sh" if stock_code.startswith("6") else "sz"` 对 8/4 开头的北交所股票会误判为深市。
- **修复建议**: 增加 `startswith("8") or startswith("4")` 分支。

#### P3-8 `notify.py` 错误日志可能含响应体
- **文件:行号**: `src/utils/notify.py:63,91`
- **描述**: `logger.error(f"... send failed: {result}")` 把整个响应 dict 打进日志,若上游返回敏感字段会被记录。
- **修复建议**: 只记录 `errcode`/`errmsg`。

#### P3-9 `auth.py` 日志记录 token 前 4 字符
- **文件:行号**: `src/utils/auth.py:78`
- **描述**: `f"prefix={token[:4]}..."` 记录令牌前缀,轻微信息泄露;虽 4 字符难以还原,但安全审计建议脱敏。
- **修复建议**: 改为只记录 `len(token)` 或哈希前几位。

---

## 三、各文件评分汇总

评分标准:5=优秀,4=良好,3=合格,2=有缺陷,1=严重问题。

### 路由层 `web/routes/`

| 文件 | 逻辑正确性 | 性能 | 安全 | 异常处理 | 可维护性 | 备注 |
|---|---|---|---|---|---|---|
| `agent.py` | 4 | 3 | 2 | 3 | 4 | 5 个写端点无鉴权;全局列表 |
| `config.py` | 3 | 4 | 4 | 4 | 4 | test_notify 无鉴权;_strip_masked 死代码 |
| `dashboard.py` | 4 | 4 | 5 | 4 | 4 | 仅 GET,结构规范 |
| `fund.py` | 2 | 4 | 4 | 4 | 4 | today_pnl 逻辑错误 |
| `global_market.py` | 4 | 4 | 5 | 4 | 4 | 缓存+meta 规范 |
| `holdings.py` | 4 | 4 | 5 | 4 | 4 | 仅 GET |
| `market.py` | 4 | 4 | 5 | 4 | 3 | 单文件 850+ 行偏长;缓存规范 |
| `monitor.py` | 4 | 3 | 2 | 4 | 4 | watchlist 写端点无鉴权+无锁 |
| `news.py` | 4 | 4 | 5 | 4 | 4 | 风险低 |
| `scripts.py` | 4 | 4 | 3 | 4 | 4 | generate 无鉴权 |
| `strategy.py` | 4 | 3 | 2 | 3 | 4 | backtest 无鉴权;多处 except:return [] |
| `trade.py` | 4 | 4 | 5 | 4 | 4 | 鉴权完整;limit 无上限 |

### 策略层 `src/strategies/`

| 文件 | 逻辑正确性 | 性能 | 安全 | 异常处理 | 可维护性 | 备注 |
|---|---|---|---|---|---|---|
| `a_stock_analyst.py` | 4 | 3 | 5 | 4 | 4 | 串行 akshare;缓存键缺 days |
| `bspro_quant.py` | 1 | 2 | 5 | 4 | 4 | **P0 回测未来函数泄露+回撤错误** |
| `cb_t0_sniper.py` | 2 | 2 | 5 | 4 | 4 | prev_close 硬编码;N+1 全量拉取 |
| `limit_up.py` | 2 | 2 | 5 | 4 | 4 | 连板判定失效;重复请求 |
| `stock_monitor.py` | 1 | 2 | 5 | 4 | 4 | **P1 永真条件+严重度反转** |
| `trading_quant.py` | 4 | 2 | 5 | 4 | 4 | 串行;全量快照取单股 |
| `__init__.py` | — | — | — | — | — | 未含可审查逻辑 |

### 监控层 `src/monitor/`

| 文件 | 逻辑正确性 | 性能 | 安全 | 异常处理 | 可维护性 | 备注 |
|---|---|---|---|---|---|---|
| `alert.py` | 4 | 5 | 5 | 5 | 4 | 设计良好;用 stdlib logging |
| `daily_flow.py` | 4 | 5 | 5 | 5 | 4 | 同上 |

### 工具层 `src/utils/`

| 文件 | 逻辑正确性 | 性能 | 安全 | 异常处理 | 可维护性 | 备注 |
|---|---|---|---|---|---|---|
| `auth.py` | 4 | 5 | 3 | 5 | 5 | **P1 fail-open**;compare_digest 防时序攻击✓ |
| `config.py` | 4 | 5 | 5 | 5 | 5 | yaml.safe_load✓;Pydantic 校验✓ |
| `convert.py` | 5 | 5 | 5 | 5 | 5 | 干净健壮 |
| `logger.py` | — | — | — | — | — | 未深入审查(标准封装) |
| `notify.py` | 4 | 5 | 4 | 4 | 4 | hmac 签名✓;超时✓;日志含响应体 |

### 数据层 `src/core/`

| 文件 | 逻辑正确性 | 性能 | 安全 | 异常处理 | 可维护性 | 备注 |
|---|---|---|---|---|---|---|
| `data_source.py` | 3 | 2 | 5 | 4 | 4 | TushareSource 无超时;全量取单股 |
| `data_source_v2.py` | 4 | 5 | 5 | 4 | 4 | **缓存+并发+降级+超时,设计最佳** |
| `data_validator.py` | 5 | 5 | 5 | 5 | 5 | 多维校验,质量高 |

---

## 四、反模式统计

| 反模式 | 出现次数 | 典型位置 |
|---|---|---|
| 写端点缺少鉴权 | 13 | agent/monitor/scripts/strategy 等 |
| fail-open 鉴权(未配置即放行) | 1 | `auth.py:50-56` |
| 未来函数泄露(回测) | 1 | `bspro_quant.py:251-281` |
| 全市场快照取单股(N+1) | 5+ | data_source/stock_monitor/trading_quant/cb_t0_sniper |
| 串行调用外部 API | 4 | bspro_quant/stock_monitor/trading_quant/a_stock_analyst |
| `except Exception: pass`(静默吞) | 2 | `cache.py:94,118` |
| `except Exception: return []`(吞错返空) | 6+ | strategy.py 多处 |
| 永真/不可达条件 | 2 | stock_monitor.py:120, 272-287 |
| 硬编码业务阈值假设 | 1 | cb_t0_sniper.py:182(10% 涨停) |
| 缓存键缺维度 | 2 | bspro_quant/a_stock_analyst `_get_kline` |
| 裸 `except:` | 0 | —(全项目无裸 except,良好) |
| `eval`/`exec`/`os.system` | 0 | —(无危险动态执行) |
| SQL 注入(f-string 拼接 execute) | 0 | —(execute 均用参数化) |
| 硬编码密钥 | 0 | —(均从环境变量读取) |

---

## 五、总体结论与优先修复建议

### 亮点
1. **`data_source_v2.py`** 是数据层标杆:TTL 缓存 + 线程池并发 + EastMoney 可用性探测降级 + 统一 5s 超时 + `_safe_float` 处理 NaN/Inf,设计成熟。
2. **`data_validator.py`** 多维度数据质量校验(完整性/时效性/合理性/一致性)实现完整,评分机制清晰。
3. **`auth.py`** 用 `secrets.compare_digest` 防时序攻击,支持 Bearer/裸 token 双格式,设计到位(除 fail-open 外)。
4. **`config.py`** 用 `yaml.safe_load` + Pydantic 双配置体系,环境变量覆盖向后兼容。
5. 全项目**无裸 `except:`、无 `eval/exec`、无 SQL 注入、无硬编码密钥**,安全基线良好。
6. 路由层 `market.py`/`global_market.py` 的 `_meta` 响应元数据(数据源/质量分/缓存命中/时间戳)结构统一,前端可一致性消费。

### 优先修复顺序
1. **P0-1**: `bspro_quant.backtest_strategy` 回测失真——影响所有依赖回测结果的决策,必须先修。
2. **P1-1 / P1-2**: 写端点鉴权 + `auth.py` fail-open——安全防线,生产上线前必修。
3. **P1-3 / P1-4 / P1-7**: `stock_monitor` 与 `fund.py` 逻辑 bug——直接导致错误告警与错误盈亏展示。
4. **P1-5 / P1-6**: `limit_up` 连板判定与 `cb_t0_sniper` 前收假设——策略信号失真。
5. **P2 系列**: 性能优化(并发化、消除 N+1)与轻量加固(限流、文件锁、参数上限)。
6. **P3 系列**: 可维护性改进,可结合日常重构逐步消化。

---

## 六、审查覆盖说明

| 范围 | 文件数 | 已通读 | 抽样/grep 覆盖 |
|---|---|---|---|
| `web/routes/` | 12 | 9(agent/config/fund/monitor/scripts/strategy/trade/market/global_market) | dashboard/holdings/news(均为低风险 GET,经 grep 确认无写端点鉴权问题) |
| `src/strategies/` | 7 | 6(含 `__init__`) | — |
| `src/monitor/` | 2 | 2 | — |
| `src/utils/` | 5 | 4(auth/config/convert/notify) | logger(标准封装,未深入) |
| `src/core/` 数据层 | 3 | 3 | data_source.py 仅前 280 行+execute 段,data_source_v2.py 前 130 行+grep 全量请求点 |

**本次审查为静态只读分析,未运行测试;部分性能与并发问题需结合压测进一步确认。**
