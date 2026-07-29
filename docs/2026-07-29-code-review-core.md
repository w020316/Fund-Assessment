# Fund-Assessment 核心业务模块代码审查报告

- **审查日期**: 2026-07-29
- **审查范围**: `src/analysis/`(13 文件)、`src/agents/`(8 文件)、`src/core/`(12 文件),共 33 个 `.py` 文件
- **审查方式**: 只读静态分析,未修改任何源代码
- **审查员**: 资深 Python 代码审查员

---

## 一、总览表(文件 × 维度评分)

> 评分标准: 5 优秀 / 4 良好 / 3 合格 / 2 有问题 / 1 严重问题

### 1.1 `src/analysis/` 目录

| 文件 | 逻辑正确性 | 性能 | 安全 | 异常处理 | 可维护性 | 行数 |
|---|---|---|---|---|---|---|
| `__init__.py` | - | - | - | - | - | (空) |
| `news_aggregator.py` | 4 | 4 | 4 | 4 | 4 | 372 |
| `fundamental.py` | 3 | 2 | 4 | 3 | 3 | 150 |
| `capital_flow.py` | 3 | 2 | 4 | 3 | 3 | 117 |
| `sentiment.py` | 3 | 3 | 4 | 3 | 3 | 201 |
| `news.py` | 4 | 3 | 4 | 4 | 4 | 157 |
| `technical.py` | **1** | 3 | 5 | 4 | 3 | 260 |
| `multi_agent_fund.py` | 3 | 3 | 4 | 4 | 3 | 546 |
| `fund_advisor_v2.py` | 3 | 3 | 4 | 4 | 4 | 629 |
| `market_assessment.py` | 4 | 4 | 4 | 4 | 4 | 319 |
| `fund_holdings.py` | 3 | 3 | 3 | 3 | 3 | 561 |
| `script_library.py` | 4 | 4 | 5 | 5 | 4 | 405 |
| `fund_advisor.py` | 4 | 3 | 4 | 4 | 4 | 361 |

### 1.2 `src/agents/` 目录

| 文件 | 逻辑正确性 | 性能 | 安全 | 异常处理 | 可维护性 | 行数 |
|---|---|---|---|---|---|---|
| `__init__.py` | - | - | - | - | - | (空) |
| `base.py` | 5 | 5 | 5 | 5 | 4 | 83 |
| `technical_agent.py` | 3 | 2 | 4 | 3 | 3 | 202 |
| `news_agent.py` | 4 | 3 | 4 | 3 | 4 | 94 |
| `sentiment_agent.py` | 4 | 3 | 4 | 3 | 4 | 81 |
| `fundamental_agent.py` | 4 | 2 | 4 | 3 | 4 | 116 |
| `trading_manager.py` | 3 | 3 | 4 | 3 | 3 | 230 |
| `research_team.py` | 3 | 4 | 5 | 4 | 3 | 150 |

### 1.3 `src/core/` 目录

| 文件 | 逻辑正确性 | 性能 | 安全 | 异常处理 | 可维护性 | 行数 |
|---|---|---|---|---|---|---|
| `__init__.py` | - | - | - | - | - | (空) |
| `ai_service.py` | 3 | 3 | 3 | 2 | 2 | 1000+ |
| `cache.py` | 3 | 3 | 4 | **2** | 4 | 120 |
| `backtest.py` | 3 | 3 | 5 | 3 | 3 | 559 |
| `data_source_v2.py` | 3 | 3 | 3 | 3 | 2 | 950+ |
| `data_validator.py` | 4 | 4 | 5 | 4 | 4 | 355 |
| `llm_router.py` | 3 | 3 | 3 | 3 | 3 | 647 |
| `executor.py` | 3 | 3 | 4 | 3 | 3 | 521 |
| `response.py` | 5 | 5 | 5 | 5 | 5 | 22 |
| `scheduler.py` | 4 | 4 | 5 | 3 | 4 | 189 |
| `risk_manager.py` | 3 | 3 | 4 | **2** | 3 | 362 |
| `data_source.py` | 3 | 3 | 4 | 3 | 3 | 100+ |

---

## 二、反模式统计

| 反模式 | 命中数 | 位置示例 |
|---|---|---|
| 裸 `except:` | **0** | 无 |
| `except Exception: pass`(静默吞异常) | **2** | `src/core/cache.py:94-95, 118-119` |
| `except Exception:`(广义捕获,非 pass) | **20** | 见下表 |
| 生产代码 `print(` | **0** | 全部使用 `loguru` |
| `eval(` / `exec(` | **0** | 无 |
| 硬编码密钥(字面量赋值) | **0** | 全部来自环境变量/配置 |
| `TODO/FIXME/XXX/HACK` | **0** | `llm_router.py:330-331` 的 `XXX_API_KEYS` 是文档字符串中的环境变量名,非 TODO |
| 未使用 `import` | **3+** | `fundamental.py:1` `numpy`、`capital_flow.py:1` `numpy`、`sentiment.py:1` `numpy`、`trading_manager.py:3` `random` |

### 广义 `except Exception:` 分布(20 处)

| 文件 | 行号 | 后续处理 |
|---|---|---|
| `core/cache.py` | 42, 94, 118 | 第 94/118 行 `pass` 静默吞 |
| `core/backtest.py` | 365, 495 | 日志或返回默认值 |
| `core/ai_service.py` | 1020, 1026, 1032, 1038 | 返回 `None` 降级 |
| `core/data_source_v2.py` | 83, 87, 922 | 设置 `_EM_AVAILABLE=False` 或返回 `None` |
| `core/executor.py` | 445 | `_pre_sell_snapshot = {}` 兜底 |
| `agents/trading_manager.py` | 216 | 生成 mock opinion |
| `analysis/news_aggregator.py` | 103 | 返回 `None` |
| `analysis/sentiment.py` | 11 | 返回空 DataFrame |
| `analysis/fund_holdings.py`(via 间接) | - | - |

---

## 三、问题清单(按严重度分级)

### P0 - 阻断性 Bug / 安全漏洞(必须立即修复)

#### P0-1: `technical.compute_indicators` 调用不存在的函数,导致 `NameError`

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\technical.py:89-111`
- **问题描述**:
  模块定义的公开函数名为 `sma`、`macd`、`stoch`、`rsi`、`bbands`、`atr`(无下划线前缀,见第 15/37/51/25/65/74 行),但 `compute_indicators` 内部调用的是 `_sma`、`_macd`、`_stoch`、`_rsi`、`_bbands`、`_atr`(带下划线前缀)。这些私有名称在模块中**完全未定义**:

  ```python
  # 实际调用(第 89-111 行)
  result[f"MA{period}"] = _sma(result["close"], length=period)   # NameError
  macd = _macd(result["close"])                                  # NameError
  stoch = _stoch(result["high"], result["low"], result["close"]) # NameError
  result["RSI"] = _rsi(result["close"], length=14)               # NameError
  boll = _bbands(result["close"], length=20)                     # NameError
  result["ATR"] = _atr(result["high"], result["low"], result["close"], length=14)  # NameError
  ```

- **影响**: 一旦 `compute_indicators` 被调用,立即抛出 `NameError`;`score_technical` 也因此永远失败。`agents/technical_agent.py:80` 调用后被外层 `try/except` 捕获而降级为 `_mock_analysis`,意味着**技术面分析功能实际从未生效**,所有技术分析结果都是降级 mock 数据。
- **建议修复**: 将 `compute_indicators` 内部所有 `_sma`/`_macd`/`_stoch`/`_rsi`/`_bbands`/`_atr` 调用去掉下划线前缀,改为调用模块内已定义的 `sma`/`macd`/`stoch`/`rsi`/`bbands`/`atr`。注意第 91 行 `macd = _macd(...)` 还会与函数名 `macd` 冲突,建议改名为 `macd_df`(同理 `stoch` → `stoch_df`)。

---

### P1 - 影响功能正确性(本批次修复)

#### P1-1: `LLMRouter` 多 Key 轮换对 Gemini / Anthropic 不生效

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\llm_router.py:505, 535`
- **问题描述**:
  `LLMProvider.current_api_key` 设计为多 Key 轮换(第 62-68 行),但 `_call_gemini`(第 505 行)和 `_call_anthropic`(第 535 行)直接使用 `provider.api_key`(单 Key),未调用 `provider.current_api_key`。仅 `_call_openai_compatible` 正确使用了轮换。
- **影响**: 配置了 Gemini/Anthropic 多 Key 的用户实际只用了第一个 Key,无法负载均衡,易触发单 Key 限流。
- **建议修复**: 将两处 `provider.api_key` 改为 `provider.current_api_key`。

#### P1-2: `multi_agent_fund` 与 `fund_advisor_v2` 重复抓取数据,浪费网络资源

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\multi_agent_fund.py:448-455, 483-492`
- **问题描述**:
  `analyze_fund_with_agents` 第 1 步并行抓取 `nav_history`/`quotes`/`holdings_data`/`news_data`/`thermometer`,第 3 步又调用 `analyze_fund_five_signals`(第 483 行),而 `analyze_fund_five_signals` 内部(`fund_advisor_v2.py:471-478`)**再次**抓取这 5 份数据。
- **影响**: 同一份基金分析请求会发起 2 倍网络请求(净值历史、行情、持仓、新闻、温度计),延迟翻倍,且外部数据源易触发限流。
- **建议修复**: 让 `analyze_fund_five_signals` 接受可选的 `context` 参数复用已抓取数据;或在 `analyze_fund_with_agents` 内直接计算五信号,不再二次调用。

#### P1-3: `LLMProvider.current_api_key` 属性有副作用

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\llm_router.py:61-68`
- **问题描述**:
  `current_api_key` 是 `@property`,但每次访问都会执行 `self._key_index += 1`。这违反了"属性访问无副作用"的惯例:
  ```python
  @property
  def current_api_key(self) -> str:
      if self.api_keys:
          key = self.api_keys[self._key_index % len(self.api_keys)]
          self._key_index += 1   # 副作用:每次读都改状态
          return key
      return self.api_key
  ```
- **影响**:
  1. 日志/调试时多次访问 `provider.current_api_key` 会快速轮换 Key;
  2. `health_check` 若访问该属性也会扰动轮换索引;
  3. 单元测试断言不稳定。
- **建议修复**: 改为显式方法 `next_api_key()`,或在 `_call_*` 方法内显式调用一次后暂存。

#### P1-4: `cache.py` 静默吞异常导致缓存失效操作不可观测

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\cache.py:94-95, 118-119`
- **问题描述**:
  ```python
  for f in self.cache_dir.glob(f"{safe_prefix}*.json"):
      try:
          f.unlink(missing_ok=True)
          deleted += 1
      except Exception:
          pass   # 静默吞
  ```
- **影响**: 缓存清理失败(权限/文件锁/磁盘满)时无任何日志,运维无法发现"缓存目录膨胀但清理无效"的问题。
- **建议修复**: 至少 `logger.debug(f"unlink cache file failed: {f}, {e}")`。

#### P1-5: `RiskManager` 状态持久化失败被静默吞

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\risk_manager.py:152-154, 183-184, 299-300`
- **问题描述**: `_load_state` / `_save_state` / `record_trade` 中的 DB 异常全部 `except Exception as e: logger.warning(...)`,但 `_save_state` 失败后程序继续运行,内存状态与磁盘状态会持续不一致。
- **影响**: 进程重启后,风控状态(连续止损次数、暂停期)丢失,可能放过本应禁止的交易。
- **建议修复**: `_save_state` 失败应 `logger.error` 并触发 `emergency_stop()` 或至少抛出 `RuntimeError`,让上层决策是否继续交易。

#### P1-6: `executor.execute_signal` 交易记录字段错误

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\executor.py:483-495`
- **问题描述**:
  ```python
  trade = Trade(
      ...
      commission=0.0,    # 错:应为实际佣金
      stamp_tax=0.0,     # 错:应为实际印花税
      net_amount=order.filled_price * order.filled_quantity,  # 错:应为 amount - commission - stamp_tax
  )
  self._record_trade(trade, ...)
  ```
  而 `SimulatedBroker._calc_commission`(第 144 行)已经正确计算了佣金/印花税,但 `execute_signal` 没有从 `order` 中获取这些值,直接硬编码为 0。
- **影响**: 落库的 `trades` 表 `commission`/`stamp_tax`/`net_amount` 全部失真,后续盈亏分析和税务统计错误。
- **建议修复**: 让 `Order` dataclass 携带 `commission`/`stamp_tax`/`net_amount` 字段(由 broker 填充),`execute_signal` 透传。

#### P1-7: `executor._pre_sell_snapshot` 未在 `__init__` 初始化

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\executor.py:441-446, 467`
- **问题描述**: `_pre_sell_snapshot` 仅在 SELL 分支中通过 `self._pre_sell_snapshot = {...}` 设置,`__init__` 中未声明。第 467 行 `hasattr(self, '_pre_sell_snapshot')` 依赖该属性可能不存在。
- **影响**: 多线程/并发场景下,如果一个进程先处理 BUY 再处理 SELL,BUY 路径不设置该属性;若 SELL 分支抛异常导致 `_pre_sell_snapshot` 未赋值,后续 `hasattr` 检查会读到陈旧数据。
- **建议修复**: 在 `__init__` 中显式 `self._pre_sell_snapshot: dict[str, dict] = {}`。

---

### P2 - 影响可维护性 / 性能(本批次修复)

#### P2-1: `technical_agent._real_analysis` 在异步上下文中同步阻塞

- **文件**: `d:\xm\wz\Fund-Assessment\src\agents\technical_agent.py:35`
- **问题描述**: `ak.stock_zh_a_hist(...)` 是同步阻塞调用,若 `TechnicalAgent.analyze` 被异步调度链调用,会阻塞事件循环。
- **影响**: 在多智能体并发分析时,技术面分析会串行阻塞其他 Agent。
- **建议修复**: 调用方 `TradingManager._parallel_analyze` 已使用 `ThreadPoolExecutor`,问题相对可控;但若未来从 `async` 入口调用,需用 `asyncio.to_thread`。

#### P2-2: `news_aggregator._deduplicate` 使用 O(N²) 相似度比较

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\news_aggregator.py:107-122`
- **问题描述**: `difflib.SequenceMatcher` 对每对新闻标题计算相似度,时间复杂度 O(N²)。当新闻数 N=50 时,需 1225 次比较;N=200 时约 2 万次。
- **影响**: 新闻量大时(4 源汇总可能 50+ 条),去重阶段延迟明显。
- **建议修复**: 先用标题长度/前 N 字符做粗筛,或用 SimHash/MinHash 做近似去重。

#### P2-3: `get_news_feed` 中基金重仓股新闻并发抓取未用 `return_exceptions`

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\news_aggregator.py:258`
- **问题描述**:
  ```python
  stock_news_results = await asyncio.gather(*stock_news_tasks)
  ```
  未传 `return_exceptions=True`,任一股票新闻抓取抛异常会导致整个 gather 失败,丢失其他股票的成功结果。
- **建议修复**: `await asyncio.gather(*stock_news_tasks, return_exceptions=True)`,然后过滤 `isinstance(r, Exception)`。

#### P2-4: `fund_holdings._fetch_fund_holdings_em` 用正则解析 HTML,易碎

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\fund_holdings.py:88-126`
- **问题描述**: 东方财富返回的 HTML 表格通过多组正则(`<tr>`、`<td>`、去除标签)解析,一旦东方财富调整页面结构(增加列、改 class、嵌套 div),解析立即失败且无报警。
- **影响**: 持仓数据是基金分析的源头数据,解析失败会导致整个分析链降级。
- **建议修复**: 改用 `lxml`/`BeautifulSoup` 容错解析,或优先用 akshare 的 `fund_portfolio_hold_em`(已有 `_fetch_fund_holdings_ak` 兜底,但当前是 EM 失败才用)。

#### P2-5: `data_source_v2._cache` 全局字典无淘汰策略

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\data_source_v2.py:32-50`
- **问题描述**: `_cache: dict[str, _TTLCacheEntry] = {}` 是模块级全局字典,`_cache_set` 仅写入不淘汰。过期条目仅在 `_cache_get` 命中时才被删除,从未被访问的过期条目会永久驻留。
- **影响**: 长时间运行的服务,缓存字典无限增长,内存泄漏。
- **建议修复**: 加 `maxsize` 上限 + LRU 淘汰,或定期 `gc` 清理过期 key。

#### P2-6: `data_source_v2._thread_pool` 全局线程池未关闭

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\data_source_v2.py:34`
- **问题描述**: `_thread_pool = ThreadPoolExecutor(max_workers=6)` 模块级创建,无 `atexit` 注册 `shutdown`,进程退出时可能残留线程。
- **建议修复**: `atexit.register(_thread_pool.shutdown, wait=False)`。

#### P2-7: `ai_service.py` 模块级 `load_dotenv()` 副作用

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\ai_service.py:32, 38-41`
- **问题描述**:
  ```python
  load_dotenv()
  _TTAPI_API_KEY = settings.ttapi_api_key or os.getenv("TTAPI_API_KEY", "")
  ```
  模块导入时即执行 `load_dotenv()` 并固化 4 个 API Key 到模块常量。环境变量后续修改不会反映到 `_TTAPI_API_KEY`。
- **影响**: 测试场景下 mock 环境变量无效;热更新 Key 不生效。
- **建议修复**: 改为函数内读取,或用 `functools.lru_cache` 包装。

#### P2-8: `trading_manager._history` 列表无上限,内存泄漏

- **文件**: `d:\xm\wz\Fund-Assessment\src\agents\trading_manager.py:163, 189`
- **问题描述**: `self._history: list[TradingDecision] = []` 每次 `run_analysis` 都 append,无上限。
- **影响**: 长期运行的服务(调度器每分钟触发)会持续增长内存。
- **建议修复**: 改用 `collections.deque(maxlen=1000)` 或定期落库。

#### P2-9: `risk_manager.check_order` 在检查器中变更状态

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\risk_manager.py:225-249`
- **问题描述**: `check_order` 名为"检查",但内部会修改 `_is_paused`、`_pause_until`、`_no_new_positions`、`_position_reduction` 并持久化。违反"查询无副作用"原则。
- **影响**: 重复调用 `check_order`(如日志/调试)会重复触发状态变更。
- **建议修复**: 拆分为 `evaluate_risk()`(只读) + `apply_risk_action()`(写)。

#### P2-10: `LLMRouter._call_ollama` 签名与基类不一致

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\llm_router.py:581-589`
- **问题描述**: `_call_ollama` 多了 `**kwargs` 参数,而 `_call_openai_compatible` / `_call_gemini` / `_call_anthropic` 都没有;且 `json_mode` 未传给 Ollama。
- **建议修复**: 统一签名为 `(provider, messages, model, temperature, json_mode, timeout)`,并在 `payload` 中处理 `json_mode`。

---

### P3 - 优化建议(后续迭代)

#### P3-1: 多个文件 `import numpy as np` 未使用

- **文件**:
  - `src/analysis/fundamental.py:1`
  - `src/analysis/capital_flow.py:1`
  - `src/analysis/sentiment.py:1`
- **建议**: 删除未使用的 `numpy` 导入,减少启动时间和认知负担。

#### P3-2: `trading_manager.py` `import random` 未使用

- **文件**: `d:\xm\wz\Fund-Assessment\src\agents\trading_manager.py:3`
- **建议**: 删除。

#### P3-3: `capital_flow.analyze_capital_flow` 市场识别过于简单

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\capital_flow.py:17`
- **问题描述**: `"sh" if stock_code.startswith("6") else "sz"` 漏掉了:
  - `920xxx`(北交所)、`8xxxxx`(新三板)→ 应判 `bj`
  - `4xx/8xx`(北交所老股)
- **建议**: 用完整前缀映射:`6xx→sh, 0xx/3xx→sz, 8xx/4xx/920xxx→bj`。

#### P3-4: `sentiment._score_volume_change` 阈值硬编码且注释误导

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\sentiment.py:112-123`
- **问题描述**: `avg_volume_threshold = 1e12` 变量名为 "avg" 实为固定常量(1 万亿),且针对全市场成交额,而非"平均"值。
- **建议**: 改为常量 `_TOTAL_VOLUME_THRESHOLD = 1e12` 并加注释说明依据。

#### P3-5: `sentiment._score_volatility_index` 使用 `changes.std()` 未指定 `ddof`

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\sentiment.py:143`
- **问题描述**: pandas 默认 `std(ddof=1)`(样本标准差),与 numpy 默认 `ddof=0`(总体标准差)不同。如果数据量小(<30),两者差异显著。
- **建议**: 显式 `changes.std(ddof=1)`,与 `backtest.py` 中的 `_calc_volatility` 一致(后者用 `np.std` 默认 ddof=0,也需要统一)。

#### P3-6: `backtest._calc_metrics` Sharpe 计算与 `volatility` 计算的 `ddof` 不一致

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\backtest.py:504, 107`
- **问题描述**:
  - Sharpe 分母:`np.std(daily_returns_list)` → 默认 `ddof=0`
  - Volatility:`np.std(arr) * np.sqrt(252)` → 默认 `ddof=0`
  - Sortino:`np.std(downside, ddof=1)` → 样本标准差
  三处不统一,虽然不影响排序,但影响绝对值解读。
- **建议**: 统一为 `ddof=1`(样本标准差),并加注释。

#### P3-7: `backtest._calc_sortino_ratio` 返回 `float("inf")` 不利于序列化

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\backtest.py:59, 63, 533`
- **问题描述**: 无下行波动时返回 `float("inf")`,`BacktestResult.sortino_ratio` 也可能为 `inf`,JSON 序列化会失败(`json.dumps` 默认不允许 `inf`)。
- **建议**: 返回一个有限大数(如 `99.0`)并加 `note` 字段说明。

#### P3-8: `multi_agent_fund._parse_fund_analysis_response` 正则贪婪匹配

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\multi_agent_fund.py:350`
- **问题描述**: `re.search(r'\{[\s\S]*\}', text)` 是贪婪匹配,会匹配从第一个 `{` 到最后一个 `}` 的所有内容。若 LLM 返回了前后多余文本包含 `{}`,会引入噪声。
- **建议**: 改为非贪婪 `\{[\s\S]*?\}` + 平衡括号检测,或用 `json5`/`json_repair` 库。

#### P3-9: `research_team._rebuttal_adjust` 反驳逻辑过于简化

- **文件**: `d:\xm\wz\Fund-Assessment\src\agents\research_team.py:125-149`
- **问题描述**: 仅根据"对方论点数量 > 己方"就将 score 调整 ±3,且 `bull_score += 5.0` per argument(第 91-93 行)使分数与论点数量强相关,易被论点堆叠操纵。
- **建议**: 引入论点权重(基于关键词强度),或用 LLM 真实辩论。

#### P3-10: `LLMProvider.api_keys: list` 缺少类型参数

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\llm_router.py:40`
- **问题描述**: `api_keys: list = field(default_factory=list)` 应为 `list[str]`。
- **建议**: 加类型参数,提升静态检查能力。

#### P3-11: `TokenBucket._lock = __import__('threading').Lock()` 反模式

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\llm_router.py:96`
- **问题描述**: 在类初始化中使用 `__import__('threading')` 绕过 import 是反模式,可读性差。
- **建议**: 文件顶部 `import threading`,然后 `self._lock = threading.Lock()`。

#### P3-12: `cache._safe_key` 未处理 Windows 路径长度限制

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\cache.py:28-30`
- **问题描述**: 仅替换非法字符,未截断长度。Windows 默认路径上限 260 字符,若缓存 key 含完整股票代码+日期+参数,可能超限。
- **建议**: 加 `key[:200]` 截断 + hash 后缀。

#### P3-13: `fund_holdings._STOCK_SECTOR_MAP` 优先级依赖列表顺序

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\fund_holdings.py:197-255`
- **问题描述**: `_infer_sector` 顺序遍历列表返回首个匹配。当前把"龙头股名"放在前面、"行业通用关键字"放在后面是正确的,但若未来新增项顺序错乱,会出现"中信银行"被"中"开头的某条规则先匹配的错误。
- **建议**: 用 `dict` 替代 `list[tuple]`,或显式标注优先级。

#### P3-14: `script_library.match_fund_scripts` 取 `market_signal` 字段不存在

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\script_library.py:341-343`
- **问题描述**: `fund_advice.get("market_signal", {})` 假设传入的 dict 是 `generate_fund_advice` 的顶层返回(含 `market_signal`),但函数文档说"传入单只基金的 advice 项",即 `positions_advice[i]`,该结构**不含** `market_signal` 字段。
- **影响**: `index_name`、`index_change_pct` 变量始终为空,相关话术填充为"—"。
- **建议**: 修改函数签名,显式接受 `market_signal` 参数,或在文档中说明传入顶层结构。

#### P3-15: `data_validator.validate_quote` `if price and not (...)` 对 `price=0` 跳过校验

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\data_validator.py:89`
- **问题描述**: `if price and not (self.PRICE_RANGE[0] <= price <= self.PRICE_RANGE[1])` 中,`price=0` 会因 `if price` 为假而跳过范围检查。但 `price=0` 本身就是异常数据(停牌/涨跌停)。
- **建议**: 改为 `if price is not None and not (...)`。

#### P3-16: `scheduler` 盘中任务无交易时段校验

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\scheduler.py:64-82`
- **问题描述**: `intraday_bond_monitor` 配置为 `IntervalTrigger(seconds=30)`,即每 30 秒全天 24 小时运行;`intraday_limit_up_monitor` 用 `CronTrigger(hour="9-11,13-15")` 但未排除周末/节假日。
- **建议**: 加交易时段守卫函数,非交易时段直接 return。

#### P3-17: `data_source.py` 模块级 `import tushare as ts` 强依赖

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\data_source.py:13-16`
- **问题描述**: 模块顶部 `import akshare as ak`、`import tushare as ts` 是硬依赖,若未安装会 ImportError。其他模块用 `try/except ImportError` 兜底,本模块没有。
- **建议**: 改为 try/except 导入,或延迟到类内导入。

#### P3-18: `fund_advisor_v2._calc_fundamental_signal` 加权平均逻辑有缺陷

- **文件**: `d:\xm\wz\Fund-Assessment\src\analysis\fund_advisor_v2.py:122-138`
- **问题描述**:
  ```python
  for h in holdings:
      pe = float(q.get("pe_ttm", 0) or 0)
      if pe > 0:
          pes.append(pe * weight)
          total_weight += weight
      if pb > 0:
          pbs.append(pb * weight)
  avg_pe = sum(pes) / total_weight if total_weight > 0 else 0
  avg_pb = sum(pbs) / total_weight if total_weight > 0 else 0
  ```
  `total_weight` 只在 `pe > 0` 时累加,但 `avg_pb` 也用同一个 `total_weight` 做分母。若某重仓股 PE 缺失但 PB 有效,其 weight 不会进入分母,导致 `avg_pb` 偏高。
- **建议**: PE/PB 分别用 `pe_total_weight` 和 `pb_total_weight`。

#### P3-19: `ai_service.py` 单文件超过 1000 行,可维护性差

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\ai_service.py`
- **问题描述**: 单文件包含数据聚合、LLM 调用、组合分析、市场展望等多职责,行数 > 1000,违反单一职责原则。
- **建议**: 拆分为 `ai_service/` 包,按职责分模块:`aggregator.py` / `portfolio_analyzer.py` / `market_outlook.py`。

#### P3-20: `data_source_v2._check_em_available` 全局状态非线程安全

- **文件**: `d:\xm\wz\Fund-Assessment\src\core\data_source_v2.py:69-91`
- **问题描述**: `_EM_AVAILABLE: bool | None` 是模块级变量,多线程并发调用 `_check_em_available` 时,首个线程未写完前,其他线程可能读到 `None` 重复发起探测请求。
- **建议**: 加 `threading.Lock` 保护首次探测。

---

## 四、整体评价与改进建议

### 4.1 优点

1. **统一的日志体系**: 全项目使用 `loguru`,无 `print` 污染,日志格式统一。
2. **依赖可选化**: `akshare`/`snownlp`/`mootdx` 等通过 `try/except ImportError` 兜底,降级路径清晰(如 `technical_agent._mock_analysis`)。
3. **数据降级设计**: `fund_holdings` EM→akshare 双源、`data_source_v2` 东方财富→新浪双源,容错性好。
4. **熔断与重试**: `llm_router` 实现了 Provider 熔断器(连续 3 次失败熔断 30 秒)、令牌桶限流、指数退避重试,达到生产级可用性。
5. **类型注解普及**: 大部分函数有类型注解,`dataclass` 使用规范。
6. **配置集中化**: `utils/config.py` 使用 `pydantic-settings`,API Key 通过环境变量注入,无硬编码密钥。
7. **缓存设计**: `cache.py` 借鉴 `diskcache` 的 tag 失效设计,提供 `invalidate_by_prefix/suffix` 批量失效。

### 4.2 主要短板

1. **P0 阻断 Bug**: `technical.py` 函数名前缀错误,导致技术面分析全程降级,这是**功能正确性的根本性失败**,说明缺乏端到端测试。
2. **异常处理过于宽泛**: 20 处 `except Exception:`,其中 2 处 `pass` 静默吞,5+ 处仅 `logger.warning` 后继续运行,问题被层层掩盖。
3. **状态持久化可靠性**: `risk_manager` 的 DB 异常被吞,可能导致风控状态丢失——对交易系统是致命的。
4. **重复网络请求**: `multi_agent_fund` 与 `fund_advisor_v2` 重复抓取 5 份数据,延迟与限流风险翻倍。
5. **测试缺失迹象**: P0 Bug 未被发现,说明没有针对 `compute_indicators` 的单元测试,也没有"技术分析实际产出"的端到端验证。

### 4.3 修复优先级建议

| 优先级 | 数量 | 建议工期 |
|---|---|---|
| P0 | 1 | 当日修复(`technical.py` 函数名) |
| P1 | 7 | 本批次(3-5 个工作日) |
| P2 | 10 | 本批次(5-7 个工作日) |
| P3 | 20 | 后续迭代(按需) |

### 4.4 长期改进方向

1. **建立测试体系**: 至少覆盖 `compute_indicators`、`score_technical`、`analyze_fund_with_agents`、`LLMRouter.chat` 的关键路径。
2. **统一异常处理规范**: 禁止 `except Exception: pass`;`except Exception` 必须日志 + 决策(降级/抛出/告警)。
3. **状态持久化审计**: `risk_manager`/`executor` 的 DB 操作失败必须传播到上层,不能静默。
4. **消除重复抓取**: `multi_agent_fund` 与 `fund_advisor_v2` 共享数据获取层。
5. **拆分大文件**: `ai_service.py`、`data_source_v2.py` 拆分为按职责的子模块。
6. **引入静态检查**: `ruff`/`mypy`/`vulture`(检测未使用 import/变量)加入 CI。

---

## 五、附录:审查工具与依据

- **文件列举**: `Glob` 模式 `src/{analysis,agents,core}/**/*.py`
- **代码阅读**: `Read` 工具逐文件读取关键区段
- **反模式搜索**: `Grep` 正则匹配 `except\s*:`, `except.*pass`, `print\(`, `eval\(`, `TODO|FIXME|XXX|HACK`, `password|secret|api_key.*=.*["']` 等
- **评分依据**: 基于代码实际行为,非主观印象;每个 P0/P1 问题均给出文件路径+行号+代码片段

> 本报告为只读审查产物,未修改任何源代码文件。
