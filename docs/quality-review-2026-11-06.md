# 阶段一：全量代码审查与功能完整性排查报告

- 审查日期：2026-11-06
- 代码基线：`937e412`（main，工作区干净，与 origin/main 同步）
- 审查性质：**只读审查**，未修改任何业务代码；仅新增只读审计探针 `scripts/code_audit.py`
- 实际执行模型：**DeepSeek-V4.1-Flash**（非用户偏好的 kimi-K3.1pro，无法自行切换，特此声明）
- 结论有效期：以 `937e412` 为准；代码变动后需重新核对行号

---

## 一、结论摘要

| 项 | 结果 |
|---|---|
| **P0 阻断** | **0 条**：未发现可导致资金错误成交、持仓被静默改写或线上不可用的缺陷 |
| **P1 严重** | **5 条**（R-01 ~ R-05） |
| **P2 一般** | **8 条**（R-06 ~ R-13） |
| **P3 建议** | **12 条**（R-14 ~ R-25，统一进 backlog） |
| 测试基线 | **2028 passed / 0 failed / 1 warning，105.32s** |
| 覆盖率 | **82%**（14,044 语句 / 2,577 未覆盖） |
| Lint | **165 errors**（F401×121、F841×22、E402×20、F403×1、F811×1） |
| API 面 | **117 路径 / 126 操作**；前端直接引用 **89 条路径** |
| 未引用操作 | **32 个（25.4%）**：人工复核后 = 12 个用户能力缺口 + 7 个运维/外部集成 + 13 个同能力冗余 |
| 功能覆盖度 | 对照 20 项标杆标准能力，**约 60%**（✅=1、⚠=0.5） |
| 是否具备进入阶段二 | **具备**：5 条 P1 均有明确修复路径，无需推翻架构 |

**最需要优先处理的三件事**

1. **AI 分析会把“没有数据”分析成“真实结论”**（R-01）：无效代码或行情全缺失时，Prompt 里价格/PE/ROE 全部填 0，LLM 仍返回带置信度的买卖建议。这直接违背“缺失必须显示数据缺失”的项目铁律。
2. **LLM/外部文本仍有 4 处未转义进入 `innerHTML`**（R-02）：`reasoning`、多空论点、分析师要点、数据表字符串单元格。管理员 Token 明文存于 `localStorage`，一旦被注入即可窃取，属 P1 安全缺陷。
3. **12 个已实现用户能力没有前端入口**（R-03/R-04/R-05 等）：个股搜索/详情、资金流、市场评估、组合级 AI 建议、策略参数、话术生成等，README 宣称的能力在 UI 上不可达。

---

## 二、审查范围与方法

### 2.1 覆盖范围

| 范围 | 文件数 | 代码行数 | 覆盖方式 |
|---|---:|---:|---|
| `src/` | 65 | 20,149 | 全量静态扫描；`ai_service`、`llm_router`、`data_source_v2`、`risk_manager`、`executor`、`cache`、`backtest` 逐段精读 |
| `web/`（Python） | 24 | 6,987 | 16 个路由全覆盖；`api.py`、`rate_limiter.py`、中间件、`fund.py`、`strategy.py`、`portfolio.py`、`config.py`、`agent.py`、`holdings.py`、`trade.py` 精读 |
| 前端 | 8 | 10,148 | `index.html` 7,357 行 + 5 JS + 2 CSS；XSS 汇聚点、请求封装、状态机、表格渲染逐段核查 |
| 测试 | 86 | 24,955 | 基线执行，不逐文件审阅 |
| 部署与工程 | 8 | — | `render.yaml`、`Procfile`、`fly.toml`、`vercel.json`、`railway.json`、`.github/workflows/ci.yml`、`start.ps1`、`launch.py` |

### 2.2 方法

1. **机械比对**：`scripts/code_audit.py` 导出 OpenAPI 126 个操作，抓取前端全部 `/api/` 字面量（含变量存 URL 写法），去注释后比对，得到未引用清单。
2. **静态模式批扫**：`ruff check`、`compileall`、AST 扫描（async 内阻塞调用、裸 `except`、`except`+`pass`、可变默认参数）。
3. **定向风险检索**：`innerHTML`/模板插值、`event` 依赖、`requests` 超时、全局缓存、`0.0` 兜底、SQL/命令执行、密钥硬编码。
4. **动态只读探针**：`_build_analysis_prompt` 以空数据构造 Prompt；`_calc_sortino_ratio` 验证 `inf` 序列化行为；本地 `TestClient` 读取 OpenAPI。**未发起真实 LLM 调用，未写入任何业务数据。**
5. **标杆对标**：公开资料核实 5 个标杆产品能力（来源见 §6.4）。

---

## 三、质量基线（本报告唯一参照）

| 检查项 | 命令 | 实测 |
|---|---|---|
| 用例 | `D:\dev-tools\Python312\python.exe -m pytest tests/ -q` | **2028 passed / 0 failed / 1 warning，105.32s** |
| 覆盖率 | `pytest --cov=src --cov=web` | **82%**（14,044 语句 / 2,577 未覆盖，131.08s） |
| Lint | `ruff check src web tests --statistics` | **165 errors** |
| 语法 | `compileall -q src web launch.py` | exit 0 |
| API 面 | TestClient `/openapi.json` | **117 路径 / 126 操作** |
| 线上健康 | `GET https://fund-assessment.onrender.com/api/health` | 200，`degraded=false`，`version=937e412`，热态 1.315s |

> 环境陷阱：仓库内 `.venv` 已损坏（指向不存在的 Python310）；唯一可用解释器为 `D:\dev-tools\Python312\python.exe`（3.12.9）。本报告所有 Python 命令均使用该解释器。

---

## 四、问题清单

> 级别定义：**P0** 资金/账户风险或线上不可用；**P1** 明确功能/安全缺陷，有现实触发路径；**P2** 体验、可信度或性能问题；**P3** 可维护性与清理建议。

### 4.1 P1 严重（5 条，阶段二必须 100% 关闭）

#### R-01 [P1] AI 分析在行情/财务全缺失时仍以 0 值构造 Prompt，并返回 200 真实形态结论

- **位置**：`src/core/ai_service.py:116-153`、`src/core/ai_service.py:221-261`、`src/core/ai_service.py:974-992`
- **问题**：`_gather_stock_data()` 只把“成功取到”的数据放进 `data`。当个股行情、K 线、财务、资金流全部失败，但大盘/龙虎榜/全球资讯成功时，`data` 仍非空，`analyze_stock()` 的 `if not stock_data` 守卫失效。随后 Prompt 用 `quote.get('price', 0)`、`financial.get('pe_ttm', 0)`、`roe, 0` 等默认值，把“缺失”写成 0 交给 LLM。
- **证据（实测构造 Prompt）**：对不存在的代码 `000000`、空数据调用 `_build_analysis_prompt`，输出包含：
  ```
  - 当前价格: 0
  - 涨跌幅: 0%
  - PE(TTM): 0
  - 融资余额: 0
  - 户数: 0
  ```
- **影响**：用户输入错误代码或数据源故障时，仍会得到“持有/买入、置信度 X%”的真实形态结论，属于数据真实性红线。
- **修复建议**：
  1. 入场守卫：要求 `quote.price > 0` 且核心数据（K 线 / 财务 / 资金流）至少 2 项非空，否则直接 `_fallback_result(code, "无法获取股票数据")` → 路由层 503。
  2. Prompt 不再用 0 兜底：缺失字段渲染为 `缺失`，并在 Prompt 顶部声明“以下标注缺失的字段不得推断具体数值”。
  3. 结果增加 `data_completeness`（0–100）与 `missing_fields`，低于阈值时置 `degraded=True`。
  4. 为“无效代码 / 仅大盘数据”两类场景补单测。

#### R-02 [P1] LLM/外部文本未转义进入 `innerHTML`（4 处），叠加 `unsafe-inline` CSP 与 localStorage 明文 Token

- **位置**：
  - `web/static/index.html:3743-3745`（分析师 `key_points` 直接插入）
  - `web/static/index.html:3767-3768`（多空辩论 `bull_arguments` / `bear_arguments` 直接插入）
  - `web/static/index.html:3793-3800`（最终决策 `reasoning` 直接插入）
  - `web/static/index.html:6605-6614`（数据页表格字符串单元格 `${c}` 直接插入）
  - `web/api.py:180-186`（CSP `script-src 'self' 'unsafe-inline'`）
- **问题**：上述字段来自 LLM 输出或外部行情/研报接口，均属不可信文本。项目已提供 `escapeHtml()`，但这 4 处未使用。CSP 允许内联脚本，注入的 `<img onerror>` / `<svg onload>` 可直接执行；`api()` 又从 `localStorage.admin_token` 读取令牌，攻击者可一键外传。
- **触发路径**：外部新闻/研报标题（可被上游或代理污染）→ 进入 Prompt → LLM `reasoning`/辩论论点 → `innerHTML` 执行。金融场景下“间接提示注入”现实存在。
- **修复建议**：
  1. 4 处全部改用 `escapeHtml()`；纯文本优先 `textContent`。
  2. 将 `renderDebate` 的论点渲染改为 `document.createElement + textContent`，避免任何拼接。
  3. 阶段五重构后去掉内联 `onclick`，CSP 改为 nonce/hash，移除 `'unsafe-inline'`（与 R-19 前端拆分同批）。
  4. 增加前端注入回归用例：mock 返回 `<img src=x onerror=...>`，断言 DOM 中只出现文本。

#### R-03 [P1] 个股搜索与个股详情无前端入口

- **位置**：`web/routes/market.py:758`（`/api/market/search`）、`web/routes/market.py:838`（`/api/market/stock_detail`）
- **问题**：后端可搜索与查询个股详情，前端仅支持在输入框手填代码后请求 `stock_realtime`，没有搜索结果联想、没有详情聚合页。用户必须记住 6 位代码，且看不到详情数据。
- **用户价值**：高。对标天天基金/雪球/Investbrain 均有“搜索即达”。
- **修复建议**：新增顶部统一搜索框（代码/名称，防抖 300ms，`Escape` 关闭），结果下拉展示代码/名称/现价/涨跌幅；选中后进入“个股详情”面板（现价、K 线、资金流、研报/龙虎榜/融资/股东入口）。数据缺失时逐项标注“数据缺失”，不得显示 0。

#### R-04 [P1] 资金流能力已实现但无前端入口

- **位置**：`web/routes/market.py:412`（`/api/market/sector_flow`）、`web/routes/market.py:942`（`/api/market/capital-flow`）、`web/routes/monitor.py:234`（`/api/monitor/capital_flow`）
- **问题**：主力净流入、超大单/大单/中单/小单、板块资金流均有后端实现，但前端没有任何卡片消费；资金面只能通过 AI 分析间接看到。
- **修复建议**：在“行情总览”新增“资金面”卡片：主力净流入、各单型净流入、板块资金流 Top/Bottom；每项带数据来源与时间戳，缺失显示“数据缺失”。复用已有缓存，不新增外部请求。

#### R-05 [P1] 组合级 AI 建议无前端入口

- **位置**：`web/routes/agent.py:259-271`（`POST /api/agent/portfolio_advice`）
- **问题**：后端支持“传入持仓列表 → 组合级 AI 建议”，前端只对单只基金调用 `fund_analyze`。用户无法获得跨持仓的组合建议。
- **修复建议**：在“持仓/基金”页新增“组合 AI 建议”按钮，POST 当前持仓；全 Provider 失败时按既有约定返回 503，并提供“改用组合规则建议”一键兜底（复用 `fund/advice-v2`）。

---

### 4.2 P2 一般（8 条，阶段二修复率目标 ≥90%）

#### R-06 [P2] 匿名请求共享全局限流桶，单个匿名访客可耗尽全站额度

- **位置**：`web/rate_limiter.py:78-102`
- **问题**：`rate_limit_key()` 对未携带正确 Token 的请求统一返回 `"anon"`。Render 边缘代理下无法拿到稳定客户端 IP，这是当时的刻意取舍；但代价是**任意匿名访客刷 60 次读请求，所有匿名用户一起 429**。
- **触发路径**：匿名用户 A 循环请求公开行情 → 桶满 → 匿名用户 B 首页 429。
- **修复建议**：保留“认证用户独立桶”，匿名侧改为“IP + 请求指纹（UA+Accept-Language）哈希”的弱分桶，并在无法稳定取 IP 时退化为“匿名全局桶 + 更低的每分钟额度（如 20/min）”；同时对公开读接口加短 TTL 内存缓存，降低对桶的依赖。

#### R-07 [P2] `em_get` 在全局锁内做网络 IO，所有东方财富请求被串行化

- **位置**：`src/core/data_source_v2.py:180-196`
- **问题**：`with _em_rate_lock:` 包住 `time.sleep(0.3~0.8s)` 与 `requests.get(timeout=5)`。并发时 N 个请求排队，最坏 `~5.3 × N` 秒；Render Free 仅 3 线程，首页冷态多接口并发会被放大为秒级等待。
- **修复建议**：把“限速时间片”与“网络请求”拆开：锁内只计算并预约下一次发送时间（`next_at = max(now, next_at + interval)`），锁外 sleep + 发请求；或改为信号量 + 异步队列。保持反爬间隔语义不变。

#### R-08 [P2] K 线与搜索结果缺失字段被 `_safe_float` 静默转 0.0

- **位置**：`src/core/data_source_v2.py:234-239`（K 线 OHLCV）、`src/core/data_source_v2.py:702-717`（股票搜索价格）
- **问题**：上游返回 `-`、空串、`null` 时，`_safe_float` 统一给 0.0。K 线 0 价会进入技术指标（MA/RSI）与回测（`收盘` 序列），产生虚假信号；搜索结果价格为 0 会被前端渲染成“0.00”。
- **触发条件**：停牌日、上游字段缺失、接口降级。
- **修复建议**：价格类字段禁止默认 0：解析后 `close > 0` 才保留该行，否则丢弃并计数；搜索价格缺失返回 `None`，前端显示“—”。补 K 线缺失/停牌场景单测。

#### R-09 [P2] AI 链路缺少端到端截止时间：前端等 90s，后端 Provider 串行累加

- **位置**：`web/static/index.html:2560-2568`（`/api/agent/` 超时 90000ms）、`src/core/llm_router.py:588-649`（逐 Provider 串行，超时后才切换）
- **问题**：生产环境通常配置 3–4 个 30s Provider。首个超时 30s、第二个超时 30s、第三个再 30s，前端要等 90s；多智能体基金分析内部还有多次 LLM 调用。用户长时间无结果，且 Render 免费实例长连接易被代理中断。
- **修复建议**：在 `LLMRouter.chat()` 引入总预算（如 25s/单次请求），每个 Provider 的可用超时 = `min(provider.timeout, 剩余预算)`；预算耗尽立即抛 `RuntimeError` → 503。前端 `/api/agent/` 超时同步收紧到 30s，并保留进度提示。

#### R-10 [P2] 市场评估/全市场统计/市场情绪无前端入口

- **位置**：`web/routes/market.py:900`（`market_sentiment`）、`web/routes/market.py:911`（`market_wide_stats`）、`web/routes/market.py:955`（`assessment`）
- **现状**：`/api/market/thermometer` 与 `/api/review/sentiment` 已覆盖部分情绪能力，但“全市场涨跌/成交/两融统计”“综合市场评估”没有独立入口。
- **修复建议**：并入“短线复盘”页顶部“市场温度”卡：涨跌家数、成交额、两融余额、情绪分与评估结论；每项带 `source` 与 `as_of`，缺失显式标注。

#### R-11 [P2] 策略参数配置无前端入口

- **位置**：`web/routes/config.py:180-207`（`GET/PUT /api/config/strategies`）
- **问题**：策略权重、阈值等参数只能改 YAML 文件；前端“设置”页只能改通用设置与风控参数。
- **修复建议**：设置页新增“策略参数”折叠区，读取 `GET`、保存 `PUT`；保存前做数值范围校验，失败返回 422 并在持久错误卡中说明字段。

#### R-12 [P2] 话术库生成与股票话术匹配无前端入口

- **位置**：`web/routes/scripts.py:75`（`categories`）、`web/routes/scripts.py:91`（`generate`）、`web/routes/scripts.py:107`（`match/stock`）
- **现状**：前端只调用 `match/fund` 与 `/api/agent/skills`，股票话术生成/匹配不可达。
- **修复建议**：在“智能分析 → 话术库”增加“股票话术”页签，按分类筛选、生成、复制；生成失败显示原因，不自动下单。

#### R-13 [P2] 策略扫描接口异常时返回 `[]`，UI 显示“暂无信号”而非失败

- **位置**：`web/routes/strategy.py:339-342`、`358-361`、`379-382`
- **问题**：`scan_new_high` / `scan_limit_up` / `scan_cb` 捕获异常后 `return []`，HTTP 仍 200。前端 `loadScan` 据空数组渲染“暂无信号”，用户会误判“今天没有机会”，而不是“数据源失败”。
- **修复建议**：异常时抛 503（结构化 `code=SCAN_DATA_UNAVAILABLE`），前端展示持久错误卡与重试；仅“成功取数且确实为空”才渲染空态。

---

### 4.3 P3 建议（12 条，进 backlog，不阻塞本轮）

| 编号 | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| R-14 | `web/static/index.html:2588-2601` | `apiCached` 先返回旧缓存、后台刷新，但调用方无法得知“当前是缓存值” | 返回 `{data, stale, ts}` 或展示 `Freshness` 徽章；TTL 降到 30s 并随页面可见性暂停刷新 |
| R-15 | `web/static/index.html:3752-3765` | `defaultDebate` 内置“行业景气度持续上行”等示例论点；当前主路径不可达，但契约变化即可伪造 | 删除示例论点，改为 `renderEmpty('暂无辩论数据')` |
| R-16 | `web/routes/config.py:204-206` | `PUT /config/strategies` 异常时返回 200 + `strategies={}` | 异常抛 503/500，不返回成功形态 |
| R-17 | `src/core/backtest.py:58-64`、`537` | Sortino 无下行波动返回 `inf`，`json.dumps(allow_nan=False)` 会抛 `ValueError`；当前 `BacktestResult` 未被路由暴露 | 在结果装配层把 `inf` 转 `None` 或 99.0；保留计算函数单测不变 |
| R-18 | `src/`、`web/`、`tests/` | ruff 165 errors（F401×121、F841×22、E402×20） | 分模块清理；禁止全量 `--fix`（会误删 `__init__` re-export） |
| R-19 | `web/static/index.html`（7,357 行） | 单文件巨石、无构建链、内联样式与内联 `onclick` 遍布 | 阶段五重构时按页面拆 JS、抽设计令牌、移除内联处理器；需单独立项 |
| R-20 | `src/core/llm_router.py:226-229` | 注释称 LRU，实际按插入顺序淘汰（FIFO） | 改为 `OrderedDict.move_to_end` 实现真 LRU，或改注释 |
| R-21 | `web/static/index.html:2722-2735` | `renderStatus`/`renderEmpty` 的 `msg/hint` 未转义；当前调用多为静态文案，但未来可能传入外部错误 | 统一 `escapeHtml(msg/hint)` |
| R-22 | `web/routes/agent.py:350-364` | `GET /api/agent/fund_analyze_steps` 无鉴权；仅返回静态步骤 | 保持公开可接受，或加只读限流；在注释中明确“非敏感” |
| R-23 | `web/routes/holdings.py:237-241` | 图片上传按扩展名 + 客户端 `Content-Type` 判定，未校验文件魔数 | 用 `imghdr`/Pillow 头校验；非图片直接 400 |
| R-24 | `README.md:74`、`README.md:122` | README 宣称“79 文件 1923 用例”，实测 86 文件 2028 用例；技术栈写 Python 3.12，部署基线为 3.14 | 同步更新 README 测试数量、文件数、运行时版本 |
| R-25 | `web/static/index.html:6524-6585` | “数据”页 `configs.*.default` 仍保留 2025-01-15 的假研报/假龙虎榜/假两融数据（当前未渲染，但为注入面） | 删除 `default` 字段，只保留真实空态 |

---

## 五、功能完整性排查

### 5.1 API 面与前端面

| 指标 | 数值 |
|---|---:|
| OpenAPI 路径 / 操作 | 117 / 126 |
| 前端直接引用路径 | 89 |
| 未被前端引用的操作 | 32（25.4%） |
| 其中：用户能力缺口 | 12 |
| 其中：运维/外部集成 | 7（`health/llm`、`llm/probe`、`webhook/scan`、`fund_analyze_steps`、`/` 根跳转等） |
| 其中：同能力冗余 | 13（`fund/advice` vs `advice-v2`、`fund/realtime` vs `positions`、`trade/history` vs `dashboard/trades`、`market/news` vs `news/feed`、`market/heatmap` vs `sector_ranking`、`monitor/northbound` vs `market/northbound` 等） |

> 机械比对存在启发式误差：初筛曾把 `/api/strategy/scan/*`、`/api/market/research_reports` 等“先存 URL 变量再请求”的调用误判为孤儿。本报告已逐条人工复核，**最终只保留 12 个真实用户能力缺口**。

### 5.2 12 个用户能力缺口

| # | 能力 | 后端端点 | 现状 | 优先级 |
|---|---|---|---|---|
| 1 | 股票搜索 | `GET /api/market/search` | 无搜索框/无联想 | **P1（R-03）** |
| 2 | 个股详情 | `GET /api/market/stock_detail` | 无详情页 | **P1（R-03）** |
| 3 | 资金流（主力/单型/板块） | `GET /api/market/capital-flow`、`sector_flow`、`monitor/capital_flow` | 无卡片 | **P1（R-04）** |
| 4 | 组合级 AI 建议 | `POST /api/agent/portfolio_advice` | 无按钮 | **P1（R-05）** |
| 5 | 市场评估 | `GET /api/market/assessment` | 无入口 | P2（R-10） |
| 6 | 全市场统计 | `GET /api/market/market_wide_stats` | 无入口 | P2（R-10） |
| 7 | 市场情绪 | `GET /api/market/market_sentiment` | 部分被 thermometer/review 覆盖 | P2（R-10） |
| 8 | 策略参数配置 | `GET/PUT /api/config/strategies` | 只能改 YAML | P2（R-11） |
| 9 | 话术生成/股票匹配 | `POST /api/scripts/generate`、`match/stock` | 仅基金话术可达 | P2（R-12） |
| 10 | 基金单只实时 | `GET /api/fund/realtime`、`market/fund_realtime` | 持仓列表已含实时估值，独立查询不可达 | P2 |
| 11 | 个股数据质量 | `GET /api/market/data-quality/{code}` | 仅持仓/行情带 `data_quality` 字段 | P3 |
| 12 | 市场状态 | `GET /api/market/status` | 无入口 | P3 |

### 5.3 对照标杆产品的功能矩阵

> 标杆能力按公开资料整理，未逐项实测；来源见 §6.4。

| 标准能力 | QuantFlow Pro | 天天基金 | 雪球 | Ghostfolio | Portfolio Performance | Investbrain |
|---|---|---|---|---|---|---|
| 实时行情/自选 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 持仓录入（手动/文件/OCR） | ✅ | ⚠ | ⚠ | ⚠ | ✅ | ✅ |
| 实时估值/当日盈亏 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 规则/多信号建议 | ✅ | ⚠ | ✅ | ❌ | ❌ | ⚠ |
| 组合风险指标 | ✅ | ⚠ | ⚠ | ✅ | ✅ | ✅ |
| 收益归因 | ✅ | ⚠ | ⚠ | ✅ | ✅ | ✅ |
| 再平衡/目标权重 | ✅ | ⚠ | ✅ | ✅ | ⚠ | ⚠ |
| 交易流水/已实现盈亏 | ⚠ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 基准对比 | ❌ | ✅ | ⚠ | ✅ | ✅ | ⚠ |
| XIRR/TWR | ❌ | ❌ | ❌ | ⚠ | ✅ | ⚠ |
| 基金对比 | ❌ | ✅ | ⚠ | ❌ | — | ❌ |
| 定投计算/计划 | ⚠（仅计算器） | ✅ | ✅ | ❌ | ⚠ | ❌ |
| 回测/样本外验证 | ✅ | ❌ | ❌ | ❌ | ⚠ | ❌ |
| 个股多智能体分析 | ✅ | ❌ | ⚠ | ❌ | ❌ | ⚠（AI 对话） |
| 提醒/推送 | ✅ | ✅ | ✅ | ⚠ | ⚠ | ⚠ |
| 导入导出 | ✅ | ⚠ | ⚠ | ✅ | ✅ | ✅ |
| 社区/分享 | ❌ | ✅ | ✅ | ❌ | ❌ | ⚠ |
| 报告导出 PDF/图片 | ❌ | ⚠ | ⚠ | ✅ | ✅ | ❌ |
| 多币种/海外持仓 | ❌（仅海外行情） | ❌ | ⚠ | ✅ | ✅ | ⚠ |
| 税务/费用分析 | ❌ | ❌ | ❌ | ⚠ | ✅ | ❌ |
| 移动端响应式 | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |

**覆盖度计算**：✅=1、⚠=0.5、❌=0；本项目 **12 / 20 ≈ 60%**。优势是“短线复盘 + 多智能体 + 回测/样本外”三项标杆普遍缺失；短板是“基准对比、交易流水、XIRR/TWR、基金对比、报告导出、多币种、税务”。

### 5.4 新功能建议与初步 Roadmap

| # | 功能 | 用户价值 | 实现难度 | 优先级 | 里程碑 |
|---|---|---|---|---|---|
| G1 | 基准对比（沪深300/中证500/同类平均） | 判断组合是否跑赢市场，是投资决策的第一性问题 | 中（已有净值聚合，加基准序列与对齐） | P1 | M2 |
| G2 | 交易流水与已实现盈亏（FIFO、分红、费用、申赎） | 从“快照持仓”升级为“可审计账本”，解决成本口径争议 | 中高（需账本模型与迁移） | P1 | M2 |
| G3 | XIRR / TWR | 真实反映现金流下的收益，避免“定投收益率虚高” | 中（纯计算，需现金流模型） | P2 | M3 |
| G4 | 基金对比（多基金净值/估值叠加） | 选基与换基的核心工具 | 低中（图表组件已有） | P2 | M2 |
| G5 | 定投计划与执行跟踪 | 从“计算器”升级为“计划-执行-复盘”闭环 | 中（需计划表与提醒） | P2 | M3 |
| G6 | 报告导出 PDF/图片 | 便于存档与分享，提升专业感 | 中（需前端排版/截图） | P2 | M3 |
| G7 | 数据可信度中心 | 统一“缺失/降级/滞后”横幅与原因 tooltip | 低中 | P1 | M1 |
| G8 | 多账户/多组合与现金账户 | 区分实盘/模拟/家庭成员账户 | 中高（数据模型改造） | P2 | M3 |
| G9 | PWA/离线壳 | 移动端可安装、弱网可用 | 中 | P3 | M4 |
| G10 | 税务/费用分析 | 适用于有资本利得申报需求的用户 | 高（规则地域差异大） | P3 | M4 |

---

## 六、阶段二范围建议（待确认后执行）

### 6.1 批次 A：数据真实性与安全（预计 7 个独立 commit）

1. R-01 AI 缺失数据守卫 + Prompt 缺失标注 + `data_completeness`
2. R-02 4 处转义 + 注入回归用例（CSP 收紧留给阶段五）
3. R-06 匿名限流弱分桶
4. R-07 `em_get` 锁外网络 IO
5. R-08 K 线/搜索缺失值处理
6. R-09 LLM 总预算 + 前端 30s 超时
7. R-13 策略扫描失败显式 503

### 6.2 批次 B：功能入口补全（预计 6 个独立 commit）

1. R-03 股票搜索 + 个股详情
2. R-04 资金面卡片
3. R-05 组合 AI 建议
4. R-10 市场评估/统计并入复盘页
5. R-11 策略参数设置
6. R-12 股票话术生成/匹配

### 6.3 验收口径

- 每个 commit 后：模块专项测试 + 全量 `pytest`（当前基线 2028，新增用例后应为 2028+N 全绿）+ `compileall` + `ruff --select E9,F63,F7,F82`。
- P1 关闭 5/5；P2 关闭 8/8（100%，高于 90% 门槛）；P3 不进本轮。
- 新增/修改代码覆盖率 ≥80%，整体覆盖率不低于本轮 82%。
- 不修改任何既有测试断言、不删除失败用例、不调整覆盖率阈值。

---

## 七、是否具备进入阶段二

**具备。** 5 条 P1 均可通过“入场守卫 + 转义 + 新 UI 入口”闭环，无需架构推翻；8 条 P2 均为局部改动。P3 中仅“前端单文件拆分”属结构性重构，已明确不纳入本轮。

---

## 八、复现命令

```powershell
# 基线
D:\dev-tools\Python312\python.exe -m pytest tests/ -q
D:\dev-tools\Python312\python.exe -m pytest tests/ -q --cov=src --cov=web --cov-report=term
D:\dev-tools\Python312\python.exe -m ruff check src web tests --statistics
D:\dev-tools\Python312\python.exe -m compileall -q src web launch.py

# 接口面 vs 前端面（只读）
D:\dev-tools\Python312\python.exe scripts\code_audit.py
```

---

## 九、未覆盖 / 待基线

| 项 | 原因 | 补测条件 |
|---|---|---|
| Firefox / Safari 兼容 | 本机仅 Edge 运行时（阶段三复核） | 安装 Playwright Firefox/WebKit 或真机 |
| 线上带凭据复验 | 本机无 Render ADMIN_TOKEN | 用户提供登录协助后，阶段三/四用内置浏览器复验 |
| Lighthouse / PSI 正式分数 | 本机无 Lighthouse CLI | 阶段四用 Playwright + 线上采样 |
| 依赖 CVE | pip 出网与本机代理未验证 | 阶段三前尝试 `pip-audit` 或 OSV 离线库 |
| 真实 LLM 全链路 | 本阶段为只读审查，未消耗免费额度 | 阶段四每条旅程最多 2 次真实调用 |

---

## 十、标杆来源

| 产品 | 来源 | 关键事实 |
|---|---|---|
| Ghostfolio | [官方首页](https://ghostfol.io/en/home/summary)、[3.0 发布说明](https://www.ghostfol.io/en/blog/2026/04/ghostfolio-3) | 隐私优先开源自托管；资产配置、净值、基准、FIRE、导入导出、多币种；官网标注 9,637 GitHub stars、1,763 MAU |
| Portfolio Performance | [官方帮助](https://help.portfolio-performance.info/en/reference/help/about/)、[RepositoryStats](https://repositorystats.com/trending/topic/portfolio) | 桌面端；FIFO 已实现盈亏、TWR、IRR、分红/费用/税务；EPL-1.0；第三方统计约 4,143 stars |
| 天天基金 | [App 官方页](http://fundact.eastmoney.com/app/)、[帮助中心](https://help.1234567.com.cn/question_1001.html) | 条件选基、基金对比、大数据榜、多图同列、资产配置、持仓行业/重仓股 |
| 雪球 | [模拟持仓 FAQ](https://xueqiu.com/about/faq/3/5)、[基金组合调仓说明](https://danjuanfunds.com/article/541.html) | 模拟交易记录与盈亏、基金组合手动调仓、三分法股债商仪表盘、社区 |
| Investbrain | [官网](https://investbra.in/)、[RepositoryStats](https://repositorystats.com/investbrainapp/investbrain) | 多券商聚合、交易/持仓/绩效、多数据源回退、历史图表、导入导出/备份、协作、可选 LLM 助手；第三方统计 310–927 stars（口径不一，待基线） |

> 星标数为公开页面/第三方统计的**快照值**，非 GitHub API 实时值；阶段六将用官方仓库页面重新核实并标注访问时间。

---

## 附录：本阶段未修改业务代码

- 未修改 `src/`、`web/`、`tests/`、`requirements.txt`、`pyproject.toml`。
- 未调整任何测试断言或阈值。
- 新增文件仅 `scripts/code_audit.py`（只读审计探针，已纳入版本控制）。
- 未发起真实 LLM 调用，未写入持仓/账本/风控数据。
- 本地未启动长驻服务；端口未占用。
