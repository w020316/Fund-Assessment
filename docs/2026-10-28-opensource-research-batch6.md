# 开源项目调研报告(第六批)

> 调研时间: 2026-10-28
> 项目: QuantFlow Pro (Fund-Assessment)
> 调研目的: 第二轮质量收口后,针对剩余功能 roadmap(#6 定投计划、#7 再平衡提醒、#10 回测闭环、#11 数据源健康度)与质量短板(API 契约测试、生产异常可观测、无障碍自动化、后端性能回归、通知渠道单一)补充参考
> 前序: 第一批 10 个 + 第二批 7 个 + 第三批 8 个 + 第四批 8 个 + 第五批 8 个 = 累计 41 个
> 本批: 新增 10 个(8 个深度分析 + 2 个简评),累计 **51 个**
> 数据说明: star / 最近 push / license / 创建时间均于 **2026-10-28** 通过 GitHub API 实测(`https://api.github.com/repos/<owner>/<repo>`),未使用记忆数据;star 数随时间变化,引用请注明日期

---

## 0. 前五批已调研项目汇总(41 个,避免重复)

| 批次 | 项目清单 |
|------|---------|
| **第1批(10个)** | akshare/akshare、mootdx/mootdx、microsoft/qlib、vnpy/vnpy、shidenggui/easytrader、ai-bot-classroom/Qbot、ZhuLinsen/daily_stock_analysis、myhhub/stock(PythonStock)、ricequant/rqalpha、OpenBB-finance/OpenBB |
| **第2批(7个)** | virattt/ai-hedge-fund、TauricResearch/TradingAgents、hsliuping/TradingAgents-CN、tradingview/lightweight-charts、shadcn/ui、langchain-ai/langgraph、Kaktana/kaktana-react-lightweight-charts |
| **第3批(8个)** | mementum/backtrader、polakowo/vectorbt、isnowany/snownlp、grantjenks/python-diskcache、kevin1024/vcrpy(pytest-vcr)、lundberg/respx、pydantic/pydantic-settings、gruns/furl |
| **第4批(8个)** | la-deps/slowapi、hynek/structlog、unionai-oss/pandera、prometheus/client_python、astral-sh/ruff、astral-sh/uv、great-expectations/great_expectations、will-ockmore/httpx-retry |
| **第5批(8个)** | GoogleChrome/workbox、TypeError/secure、open-telemetry/opentelemetry-python、omnilib/aiosqlite、msiemens/tinydb、HypothesisWorks/hypothesis、locustio/locust、twopirllc/pandas-ta |

> 后续调研请勿重复以上 41 个项目。
> 另:第一轮阶段六调研(`deliverables/gstack/stage6-research-2026-10-26.md`)分析过 ghostfolio、leek-fund、WonderTrader,其中 ghostfolio / leek-fund 不在上述 41 个内;本批同样未重复。
> 边界说明:zvt 曾在第一轮阶段六"补充观察名单"一句话带过、Riskfolio-Lib 曾在第五批"附加候选"简评,均未做过实测与展开;本批首次给出 GitHub API 实测数据与借鉴点。

---

## 1. 本轮短板诊断

来源:第二轮最终报告 §7/§8 与阶段 2 backlog(`stage1-round2-review-2026-10-28.md` §4)。

| # | 短板 | 当前实现 | 影响 | 对应本批项目 |
|---|------|---------|------|-------------|
| 1 | **定投计划缺失(#6)** | 只有手动补录/导入,无周期性计划 | 用户需自行记账,容易漏投 | freqtrade(计划/执行分层、dry-run) |
| 2 | **再平衡提醒缺失(#7)** | X-Ray 只看重叠,不看目标权重偏离 | 组合漂移无提示 | PyPortfolioOpt、Riskfolio-Lib |
| 3 | **回测闭环缺失(#10)** | 只有模拟盘,无历史策略回放 | 无法验证"再平衡/定投"规则 | bt、freqtrade |
| 4 | **数据源健康度不可见(#11)** | 失败只进日志,页面无状态 | 数据缺口无法发现 | zvt(recorder 状态/增量更新) |
| 5 | **API 契约测试缺失** | 1791 个用例以手写场景为主,无基于 OpenAPI 的生成式测试 | 5xx 路径覆盖不全(R2-24 即为例证) | schemathesis |
| 6 | **生产异常不可观测** | 仅 Render 文本日志,靠人工翻 | 线上异常发现慢 | sentry-python |
| 7 | **无障碍无自动化** | 阶段 3/5 矩阵覆盖布局、对比度、触摸目标,但无 ARIA/landmark/label 规则 | a11y 回归无门禁 | axe-core |
| 8 | **后端性能无回归基线** | CWV 只测前端;净值/LLM 接口无基准 | 性能退化只能靠感知 | pytest-benchmark |
| 9 | **通知渠道单一** | 仅 Webhook 推送 | 定投/再平衡提醒触达弱 | apprise |
| 10 | **风险度量只有简版** | 波动率、最大回撤、夏普等 | 尾部风险(CVaR/CDaR)不可见 | Riskfolio-Lib |

---

## 2. 新增项目总览

| # | 项目 | Star(2026-10-28) | 最近 push | License | 核心能力 | 对应短板 | 契合度 |
|---|------|-----------------|-----------|---------|---------|---------|--------|
| 42 | PyPortfolio/PyPortfolioOpt | 6,129 | 2026-07-07 | MIT | 均值方差/Black-Litterman/HRP/离散份额分配 | #7 再平衡 | 高 |
| 43 | freqtrade/freqtrade | 56,078 | 2026-10-27 | GPL-3.0 | 交易机器人:dry-run/回测/加仓回调/Telegram | #6 定投、#10 回测 | 中 |
| 44 | pmorissette/bt | 3,012 | 2026-10-25 | MIT | 算法组合式回测、组合树、绩效统计 | #10 回测 | **极高** |
| 45 | zvtvz/zvt | 4,317 | 2026-07-01 | MIT | 模块化量化框架:数据录制/增量更新/因子/标签 | #11 数据健康度 | 中-高 |
| 46 | caronc/apprise | 17,659 | 2026-10-25 | BSD-2-Clause | 统一 URL 语法的多通道通知 | 通知单一、#6 提醒 | 高 |
| 47 | schemathesis/schemathesis | 3,718 | 2026-10-28 | MIT | 从 OpenAPI/GraphQL 生成输入、状态化测试 | 契约测试 | 高 |
| 48 | getsentry/sentry-python | 2,218 | 2026-10-28 | MIT | 异常/性能追踪、FastAPI 集成、PII 脱敏钩子 | 生产可观测 | 中-高 |
| 49 | dequelabs/axe-core | 7,722 | 2026-10-26 | MPL-2.0 | WCAG 2.0/2.1/2.2 自动化规则引擎 | 无障碍门禁 | 高 |
| 50 | dcajasn/Riskfolio-Lib | 4,577 | 2026-10-22 | BSD-3-Clause | 26 种凸风险度量、风险平价 | #7、风险口径 | 中(简评) |
| 51 | ionelmc/pytest-benchmark | 1,458 | 2026-08-23 | BSD-2-Clause | pytest 基准测试与回归比较 | 后端性能基线 | 中(简评) |

**筛选标准**:与前 41 个项目无重复;优先服务第二轮最终报告已列出的 roadmap 与质量风险;license 明确;标注与 Render Free(512MB、无编译工具链、pip 受限)的兼容性。

---

## 3. 逐项目分析

### 3.1 PyPortfolio/PyPortfolioOpt — 组合优化与离散份额分配

| 实测指标 | 值 |
|---|---|
| Stars | **6,129** |
| 最近 push | 2026-07-07(约 3.7 个月未更新,边缘;方法成熟,不影响参考) |
| License | MIT |
| 语言 / 创建 | GitHub 主语言 Jupyter Notebook(实际为 Python 库) / 2018-05 |
| 数据来源 | https://api.github.com/repos/PyPortfolio/PyPortfolioOpt |

- **功能模块**:经典均值方差优化(最大夏普、最小波动、目标收益/风险)、Black-Litterman 配置、协方差收缩(手动 / Ledoit-Wolf / OAS,三种收缩目标)、层次风险平价(HRP)、`DiscreteAllocation` 把连续权重按最新价格转成可执行份额。
- **对本项目的借鉴价值**:⭐⭐⭐(3/5,算法参考,不引入依赖)。

| # | 借鉴点 | 当前 QuantFlow Pro | PyPortfolioOpt 做法 | 迁移到本项目的形态 |
|---|--------|-------------------|--------------------|-------------------|
| 1 | **权重→建议单** | 无再平衡 | `DiscreteAllocation(weights, latest_prices, total_value)` 输出"买/卖多少份" | 基金无"手"概念,取 0.01 份精度;生成 `/api/portfolio/rebalance/preview` 建议单,不自动下单 |
| 2 | **协方差收缩** | RiskManager 用样本协方差/波动 | Ledoit-Wolf 收缩,缓解样本少导致的极端权重 | 基金周频样本通常 3–5 年,直接实现 Ledoit-Wolf 常量相关收缩(约 30 行 numpy) |
| 3 | **HRP 层次风险平价** | 无 | 按相关性聚类递归二分分配风险,无需预期收益输入 | 适合"用户不提供收益预测"的场景;先用聚类树 + 递归二分最小实现 |
| 4 | **权重约束** | 无 | `weight_bounds` 限制单资产权重、行业上下限 | 再平衡建议加"单基金 ≤ 30%""科技主题合计 ≤ 60%"约束,超限时给提示而非静默截断 |

- **接入方式与边界**:PyPortfolioOpt 依赖 CVXPY(二次规划),在 Render Free 上安装体积大、编译风险高,本机 pip 又被代理拦截,**不引入依赖**;只借鉴 1/2/4 三个可手写公式(HRP 聚类成本较高,列 P2)。MIT 允许复制代码,但建议重写以贴合基金申赎费与 T+1 口径。
- **契合度**:高(直接服务 #7 再平衡提醒)。

### 3.2 freqtrade/freqtrade — 交易机器人流程参考

| 实测指标 | 值 |
|---|---|
| Stars | **56,078** |
| 最近 push | 2026-10-27(活跃) |
| License | **GPL-3.0(强传染)** |
| 语言 / 创建 | Python / 2017-05 |
| 数据来源 | https://api.github.com/repos/freqtrade/freqtrade |

- **功能模块**:dry-run(不花钱跑策略)、backtesting、hyperopt、Telegram/WebUI 控制、`position_adjustment_enable` + `adjust_trade_position` 加仓回调(官方文档明确标注 DCA 用法)、FreqAI。
- **对本项目的借鉴价值**:⭐⭐⭐(3/5,只借鉴流程与交互,不复制代码)。

| # | 借鉴点 | 当前 QuantFlow Pro | freqtrade 做法 | 迁移到本项目的形态 |
|---|--------|-------------------|---------------|-------------------|
| 1 | **计划/执行/流水分层** | 无定投计划 | 策略回调只负责"是否加仓",订单由框架执行并落库 | `InvestmentPlan`(周期/金额/标的/状态) → `PlanRun`(每次执行结果) → 复用现有交易流水;失败可重试、可暂停 |
| 2 | **dry-run 优先** | 有模拟盘 | 默认不接真实资金,先跑 dry-run 观察 | 定投计划创建后默认"模拟执行 N 期",确认偏差后再启用;降低误操作 |
| 3 | **配置校验与回显** | 表单校验为主 | `new-config` / `show-config` 生成并回显完整配置 | 保存前展示"下次执行时间、预计金额、扣款顺序",保存后可复制配置 JSON |
| 4 | **结果分析流水线** | 无回测 | `backtesting → backtesting-analysis → plot-profit` | 回测结果必须能导出(JSON/CSV)并复现同一参数,前端展示净值、回撤、换手 |

- **接入方式与边界**:GPL-3.0 传染性强,**不复制任何代码、不引入依赖**;只借鉴"计划状态机 + dry-run + 配置回显"三个产品化做法。加密交易与 A 股基金在交易时段、费用、T+1 上差异大,不引入其交易所抽象。
- **契合度**:中(服务 #6 定投计划)。

### 3.3 pmorissette/bt — 算法组合式回测

| 实测指标 | 值 |
|---|---|
| Stars | **3,012** |
| 最近 push | 2026-10-25(活跃) |
| License | MIT |
| 语言 / 创建 | Python / 2014-06 |
| 数据来源 | https://api.github.com/repos/pmorissette/bt |

- **功能模块**:把策略拆成可组合算法——调度(`RunMonthly`)、选择(`SelectAll`)、权重(`WeighEqually`)、再平衡(`Rebalance`);支持策略树(组合里嵌套子策略);结果包含收益、权重、交易、回撤时间序列,统计由 `ffn` 完成。
- **对本项目的借鉴价值**:⭐⭐⭐⭐(4/5,#10 回测的最小可行参考)。

| # | 借鉴点 | 当前 QuantFlow Pro | bt 做法 | 迁移到本项目的形态 |
|---|--------|-------------------|--------|-------------------|
| 1 | **四类算法拆分** | 无回测 | 调度/选择/权重/再平衡独立组合 | 自研 200 行最小引擎:`Schedule`(月/周)、`Select`(基金池)、`Weight`(等权/固定权重)、`Rebalance`(日历或阈值) |
| 2 | **结果必须含时间序列** | 模拟盘只有当前持仓 | 输出 weights/transactions/drawdowns | 前端画"权重漂移 + 回撤"双图,支持导出;避免只给一个总收益率 |
| 3 | **组合树** | 单一组合 | 策略可嵌套(核心-卫星) | P2:母组合下挂子策略,先不做 |
| 4 | **阈值再平衡** | 无 | `Rebalance` 可与调度解耦 | 与 #7 复用同一套"偏离阈值"逻辑,回测与提醒口径一致 |

- **接入方式与边界**:纯 Python + pandas/numpy(项目已具备),可选择性引入或自研;MIT 允许借鉴,但基金需处理 T+1 确认、申赎费与净值日期对齐,建议自研并复用其测试思路。
- **契合度**:**极高**(#10 回测闭环首选参考)。

### 3.4 zvtvz/zvt — 数据录制与增量更新

| 实测指标 | 值 |
|---|---|
| Stars | **4,317** |
| 最近 push | 2026-07-01(约 4 个月未更新,标注为"参考其数据模型") |
| License | MIT |
| 语言 / 创建 | Python / 2019-04 |
| 数据来源 | https://api.github.com/repos/zvtvz/zvt |

- **功能模块**:模块化量化框架(data / factor / trader);`record_data(provider=...)` 一行完成抓取、持久化、**增量更新**;实体(股票/指数/基金)+ 时间区间 + provider 的数据契约;动态标签系统(tag)支撑"AI + 人工干预"。
- **对本项目的借鉴价值**:⭐⭐⭐(3/5,数据健康度与适配器拆分)。

| # | 借鉴点 | 当前 QuantFlow Pro | zvt 做法 | 迁移到本项目的形态 |
|---|--------|-------------------|---------|-------------------|
| 1 | **recorder 状态模型** | 失败只进日志 | 每个 recorder 有更新区间与执行状态 | `/api/data/health` 返回每个数据源:`last_success_at`、`last_error`、`covered_range`、`missing_days`、`stale` |
| 2 | **增量更新** | 部分路径全量拉取 | 按 entity + 区间补齐,断点续传 | 历史净值/行情按"最后成功日期"增量拉取;失败记录断点,下次续传 |
| 3 | **数据契约分层** | `data_source_v2` 39% 覆盖,适配器未拆 | entity/domain/record 分层,provider 可替换 | 借其"实体 + 区间 + provider"三要素拆分适配器,正好对应最终报告 §8 的补测计划 |
| 4 | **标签系统** | 五信号 + 手工分组 | 动态 tag 组合 | 暂不引入;可作为"自选池/主题"标签的长期方向 |

- **接入方式与边界**:框架依赖 SQLAlchemy 等较重组件,且 4 个月未更新,**不引入**;只借鉴状态字段与增量语义。与现有 SQLite/JSON 存储并不冲突。
- **契合度**:中-高(服务 #11 数据源健康度)。

### 3.5 caronc/apprise — 统一通知抽象

| 实测指标 | 值 |
|---|---|
| Stars | **17,659** |
| 最近 push | 2026-10-25(活跃) |
| License | BSD-2-Clause |
| 语言 / 创建 | Python / 2017-11 |
| 数据来源 | https://api.github.com/repos/caronc/apprise |

- **功能模块**:用统一 URL 语法(`telegram://`、`discord://`、`mailto://` 等)对接大量通知服务;支持附件、CLI 与 API;官方服务清单见 https://appriseit.com/services/ 。
- **对本项目的借鉴价值**:⭐⭐⭐(3/5,通知 provider 化)。

| # | 借鉴点 | 当前 QuantFlow Pro | apprise 做法 | 迁移到本项目的形态 |
|---|--------|-------------------|-------------|-------------------|
| 1 | **URL scheme 抽象** | 仅 `webhook` 字段 | `provider://token@target` 统一解析 | `NotificationTarget` 表:`kind`(webhook/telegram/email)+ `config` JSON;前端按 kind 渲染表单 |
| 2 | **能力矩阵** | 无 | 每个服务声明支持的标题/附件/重试 | 配置页根据能力矩阵隐藏不支持的字段;发送层统一返回 `sent/skipped/failed` |
| 3 | **失败重试与去重** | 单次 POST,失败即丢 | 统一重试与限流 | 同一事件 5 分钟内只发一次;失败指数退避 3 次后落"待重发" |
| 4 | **SSRF 防护(自建)** | Webhook 仅校验 URL 前缀(需复核) | apprise 只管发送,不管网络边界 | 只允许 https;解析后拒绝内网/回环/云元数据地址;不跟随重定向 |

- **接入方式与边界**:BSD-2 可商用,但会新增依赖且本机 pip 受限;建议先自研约 100 行 provider 注册表,URL 语法与 apprise 对齐,未来可无痛切换到库。**SSRF 防护必须自己做**,不能依赖通知库。
- **契合度**:高(现有 Webhook + #6 定投提醒)。

### 3.6 schemathesis/schemathesis — API 契约与状态化测试

| 实测指标 | 值 |
|---|---|
| Stars | **3,718** |
| 最近 push | 2026-10-28(活跃) |
| License | MIT |
| 语言 / 创建 | Python / 2019-08 |
| 数据来源 | https://api.github.com/repos/schemathesis/schemathesis |

- **功能模块**:从 OpenAPI/GraphQL schema 生成输入并自适应响应;stateful 测试按 schema 链路串联操作;CLI + pytest 集成;可把"响应不符合 schema""500"作为失败。
- **对本项目的借鉴价值**:⭐⭐⭐(3/5,质量收口)。

| # | 借鉴点 | 当前 QuantFlow Pro | schemathesis 做法 | 迁移到本项目的形态 |
|---|--------|-------------------|------------------|-------------------|
| 1 | **以 OpenAPI 为测试源** | FastAPI 自动产出 `/openapi.json`,但测试手写 | 直接读 schema 生成边界输入 | CI 加一步 `schemathesis run http://127.0.0.1:8140/openapi.json`,断言无 5xx |
| 2 | **状态化链路** | 导入/导出/删除分别测试 | 按 operation links 串成真实工作流 | 覆盖"导入 → 查询 → 导出 → 删除"账本链路,正好补 R2-03 类原子性场景 |
| 3 | **5xx 门禁** | 无系统化 5xx 断言 | 5xx 即失败 | 与 R2-24(500 缺安全头)呼应:先把"无 5xx"作为硬门槛 |
| 4 | **pytest 集成** | 已有 1791 用例 | `from_wsgi/from_asgi` 生成测试类 | 在可联网环境安装后,把生成的用例挂在 tests/contract/ 下,与本机可跑的纯手写契约用例分层 |

- **接入方式与边界**:需安装第三方库(本机 pip 被代理拦截,当前无法落地);下一轮在可联网环境接入,过渡期可用 `httpx` 手写约 20 条 OpenAPI 边界用例。MIT 无传染。
- **契合度**:高(质量收口方向)。

### 3.7 getsentry/sentry-python — 生产异常与性能追踪

| 实测指标 | 值 |
|---|---|
| Stars | **2,218** |
| 最近 push | 2026-10-28(活跃) |
| License | MIT |
| 语言 / 创建 | Python / 2018-06 |
| 数据来源 | https://api.github.com/repos/getsentry/sentry-python |

- **功能模块**:异常捕获 + breadcrumbs;性能追踪(`traces_sample_rate`);`profiles_sample_rate` 性能剖析;官方 FastAPI/Starlette 集成;PII 控制(`send_default_pii` / `data_collection`)与 `before_send` 脱敏钩子(源码 `sentry_sdk/consts.py` 实测)。
- **对本项目的借鉴价值**:⭐⭐⭐(3/5,生产可观测)。

| # | 借鉴点 | 当前 QuantFlow Pro | sentry-python 做法 | 迁移到本项目的形态 |
|---|--------|-------------------|-------------------|-------------------|
| 1 | **异常聚合** | Render 文本日志人工翻 | 同类异常聚合、带调用栈与请求上下文 | 接入 Sentry SaaS 免费档;若不想外部依赖,先做"错误聚合 + 每日摘要"最小版 |
| 2 | **采样与配额** | 无 | `traces_sample_rate` 控制追踪比例 | 生产 0.05–0.1;错误全采、性能抽样,避免免费额度耗尽 |
| 3 | **PII / 凭据脱敏** | R2-02 已做日志脱敏 | `before_send` + PII 开关 | 把现有 `mask_secret` 复用到 `before_send`,禁止 token/webhook secret 进入事件 |
| 4 | **与 trace ID 打通** | 已有请求 trace 中间件 | 事件可带 tag | 把 trace_id 写入 Sentry tag,日志与异常可互查 |

- **接入方式与边界**:新增依赖 + DSN 配置(Render 控制台手工维护 env,`render.yaml` 不生效,需同步文档)。免费额度有限;自托管 Sentry 对 512MB 实例过重,可评估轻量替代(如 GlitchTip)。MIT 无传染。
- **契合度**:中-高。

### 3.8 dequelabs/axe-core — 无障碍自动化规则

| 实测指标 | 值 |
|---|---|
| Stars | **7,722** |
| 最近 push | 2026-10-26(活跃) |
| License | MPL-2.0(文件级 copyleft) |
| 语言 / 创建 | HTML / JavaScript / 2015-06 |
| 数据来源 | https://api.github.com/repos/dequelabs/axe-core |

- **功能模块**:覆盖 WCAG 2.0/2.1/2.2 A/AA/AAA 规则与 best practices;官方称平均可自动发现 **57%** 的 WCAG 问题,其余以 `incomplete` 返回供人工复核;可在 Playwright/Puppeteer 中注入运行。
- **对本项目的借鉴价值**:⭐⭐⭐(3/5,把现有视觉验收扩展为语义验收)。

| # | 借鉴点 | 当前 QuantFlow Pro | axe-core 做法 | 迁移到本项目的形态 |
|---|--------|-------------------|--------------|-------------------|
| 1 | **注入 Playwright 矩阵** | 99 组合只查布局/对比度/触摸 | `axe.run()` 返回 violations/incomplete | 每个场景跑 `wcag2a/wcag2aa/wcag21aa/wcag22aa` 标签,结果入矩阵 JSON |
| 2 | **规则分层** | 无 | 规则按 WCAG 等级分组 | 门禁只拦 serious/critical;moderate 进报告;incomplete 列人工清单 |
| 3 | **target-size 规则** | 阶段 5 手写 44/48px 检查 | WCAG 2.2 target-size 规则 | 与现有 48px 设计系统对齐,减少手写探针 |
| 4 | **ARIA/label/landmark** | 未覆盖 | 检查表单 label、landmark、aria 误用 | 正好补 HUD 单页多视图的语义缺口(视图切换后 `aria-hidden`、焦点管理) |

- **接入方式与边界**:MPL-2.0 为文件级 copyleft;作为测试工具注入运行、不修改其源码、不分发修改版,风险可控。CDN 引入需 CSP 放行,或把 `axe.min.js` 作为测试资产放在 `deliverables/`(不入库)。不替代人工键盘/读屏走查。
- **契合度**:高(与阶段 3 矩阵天然衔接)。

### 3.9 dcajasn/Riskfolio-Lib — 风险度量参考(简评)

| 实测指标 | 值 |
|---|---|
| Stars | **4,577** |
| 最近 push | 2026-10-22(活跃) |
| License | BSD-3-Clause |
| 语言 / 创建 | GitHub 主语言 C++(实际为 Python 库) / 2020-03 |
| 数据来源 | https://api.github.com/repos/dcajasn/Riskfolio-Lib |

- **功能**:26 种凸风险度量的均值-风险优化(CVaR、CDaR、EDaR、Ulcer Index、最大回撤/Calmar 等),22 种风险平价;依赖 CVXPY。
- **借鉴**:RiskManager 可新增 **CVaR(95%)** 与 **CDaR** 两个历史模拟口径(无需 CVXPY,numpy 排序即可),让"尾部风险"可见;与现有最大回撤/夏普并列展示,口径写入文档。
- **不引入**:CVXPY 编译/体积与 Render Free 不兼容;组合优化仍走"建议单"而非自动求解。
- **契合度**:中。

### 3.10 ionelmc/pytest-benchmark — 后端性能基线(简评)

| 实测指标 | 值 |
|---|---|
| Stars | **1,458** |
| 最近 push | 2026-08-23(活跃) |
| License | BSD-2-Clause |
| 语言 / 创建 | Python / 2014-10 |
| 数据来源 | https://api.github.com/repos/ionelmc/pytest-benchmark |

- **功能**:`benchmark` fixture 自动校准轮次;`--benchmark-json` 落基线;`--benchmark-compare` / `--benchmark-compare-fail` 做回归门禁(官方 usage 文档实测)。
- **借鉴**:给 3 条高频路径加基准——`/api/dashboard/summary`、基金历史(命中缓存)、`/api/portfolio/risk`;基线 JSON 入库,比较阈值放宽到 **20%**(Render Free 波动大,阈值过紧会误报);只在本机/CI 跑,不部署。
- **边界**:需安装第三方库,当前本机 pip 不可用,列下一轮。
- **契合度**:中。

---

## 4. 借鉴清单(按优先级)

### P0(下一迭代立即做,低风险)

| # | 改进 | 来源 | 落点 | 预估 |
|---|------|------|------|------|
| 1 | 数据源健康度接口 | zvt 状态模型 | `/api/data/health` + 行情页状态卡 | 0.5–1d |
| 2 | 通知 provider 注册表 + SSRF 防护 | apprise URL 语法 | 现有 Webhook 配置页与发送层 | 1d |
| 3 | axe-core 注入 99 组矩阵 | axe-core | `deliverables/gstack/_stage3_matrix_r2.py` 增补规则 | 0.5d |

### P1(下一迭代,中价值)

| # | 改进 | 来源 | 落点 | 预估 |
|---|------|------|------|------|
| 4 | 再平衡建议单(目标权重 + 偏离阈值) | PyPortfolioOpt 权重→份额、bt 阈值再平衡 | `/api/portfolio/rebalance/preview` + 持仓页卡片 | 1–1.5d |
| 5 | 定投计划(计划/执行/流水 + dry-run) | freqtrade 流程 | `InvestmentPlan`/`PlanRun` + 交易页 | 1.5d |
| 6 | 回测最小闭环(四类算法 + 结果序列) | bt | 策略中心新增"历史回放" | 2–3d |
| 7 | OpenAPI 契约测试(无 5xx 门禁) | schemathesis | tests/contract/ | 1d |

### P2(中长期)

| # | 改进 | 来源 | 说明 |
|---|------|------|------|
| 8 | CVaR/CDaR 风险口径 | Riskfolio-Lib | numpy 历史模拟,先上 CVaR |
| 9 | 生产异常聚合 | sentry-python | SaaS 免费档或轻量自托管 |
| 10 | 后端性能回归门禁 | pytest-benchmark | 阈值 20%,3 条高频路径 |
| 11 | HRP 风险平价 | PyPortfolioOpt | 聚类 + 递归二分,暂不做 |

---

## 5. 三个可立即落地的方案(代码级草案)

### 5.1 数据源健康度(借鉴 zvt recorder 状态)

```python
# GET /api/data/health 响应(每个数据源一条)
{
  "sources": [
    {
      "name": "fund_history",
      "last_success_at": "2026-10-28T08:30:00+08:00",
      "last_error": null,
      "covered_range": ["2023-01-03", "2026-10-27"],
      "missing_days": 0,
      "stale": false
    }
  ]
}
```

状态写入点:数据源适配器成功/失败回调各写一次 `data/health.json`(原子写);页面每 60s 轮询,stale 显示黄色,连续 2 次失败显示红色。

### 5.2 再平衡建议单(借鉴 PyPortfolioOpt `DiscreteAllocation` + bt `Rebalance`)

```python
# POST /api/portfolio/rebalance/preview
# 请求: {"targets": {"000001": 0.3, ...}, "threshold": 0.05}
# 响应:
{
  "as_of": "2026-10-28",
  "drifted": [{"code": "000001", "current": 0.38, "target": 0.30, "drift": 0.08}],
  "suggestions": [
    {"code": "000001", "action": "sell", "shares": 1234.56, "amount": 2050.00, "reason": "超目标 +8%"}
  ],
  "skipped": [{"code": "000002", "reason": "偏离 1.2% < 阈值 5%"}]
}
```

规则:偏离低于阈值不生成建议;金额按最新净值估算;基金卖出需提示"按 T+1 确认份额";**不自动执行**,仅展示与复制。

### 5.3 axe-core 注入现有矩阵(不新增依赖)

```js
// Playwright page.evaluate 中执行(axe.min.js 由测试侧 add_script_tag 注入)
const results = await axe.run(document, {
  runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
});
const serious = results.violations.filter(v => ["serious", "critical"].includes(v.impact));
return {
  violations: serious.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
  incomplete: results.incomplete.map(v => ({ id: v.id, nodes: v.nodes.length })),
};
```

门禁:serious/critical 为 0;incomplete 输出到矩阵报告人工复核。

---

## 6. License 与部署兼容性

| 项目 | License | 传染性 | 建议引入方式 | Render Free 兼容 |
|------|---------|--------|-------------|-----------------|
| PyPortfolioOpt | MIT | 无 | 只借鉴算法,自研 numpy 版 | 不安装(CVXPY) |
| freqtrade | GPL-3.0 | **强** | 不复制代码、不引入 | 不安装 |
| bt | MIT | 无 | 可选依赖或自研 200 行引擎 | 可安装(纯 Python) |
| zvt | MIT | 无 | 只借鉴状态模型 | 不安装(SQLAlchemy 等) |
| apprise | BSD-2-Clause | 无 | 先自研 provider 注册表 | 可安装,但非必需 |
| schemathesis | MIT | 无 | 仅开发/CI 依赖 | 运行在本机/CI |
| sentry-python | MIT | 无 | 生产可选依赖 | 可安装(需外部 DSN) |
| axe-core | MPL-2.0 | 文件级 | 测试注入,不分发修改版 | 运行在测试端 |
| Riskfolio-Lib | BSD-3-Clause | 无 | 只借鉴风险口径 | 不安装(CVXPY) |
| pytest-benchmark | BSD-2-Clause | 无 | 开发依赖 | 运行在本机/CI |

---

## 7. 结论

- 本批 10 个项目中,**最值得立即动手**的是:bt(回测结构)、PyPortfolioOpt(再平衡建议单)、apprise(通知抽象)、axe-core(无障碍门禁);前三者都不需要引入重量级依赖。
- **明确不引入**的四类:freqtrade(GPL 传染 + 交易域不匹配)、Riskfolio-Lib 与 PyPortfolioOpt 的优化器(CVXPY 重)、zvt(SQLAlchemy 重 + 更新停滞)。只借鉴思想,避免给 Render Free 增加体积与编译风险。
- 与本项目现有能力衔接:**模拟盘/交易流水**可承接定投执行记录;`RiskManager` 可扩展 CVaR/CDaR;**阶段 3 矩阵**可低成本接入 axe-core;**静态缓存与 CWV 脚本**已具备,后端性能基线只差 pytest-benchmark。
- 下一批(第七批)建议关注:基金申赎费/分红数据处理、组合归因 tear sheet、以及离线可用性(PWA)在真实弱网下的降级策略。

---

## 附:数据复核方式

```bash
# 任一项目(以 bt 为例)
curl -s -H 'Accept: application/vnd.github+json' \
  https://api.github.com/repos/pmorissette/bt \
  | python -c "import json,sys; d=json.load(sys.stdin); \
print(d['full_name'], d['stargazers_count'], d['pushed_at'], d['license']['spdx_id'])"
```

- 实测时间:2026-10-28(Asia/Shanghai)
- 能力描述来源:各仓库 README(默认分支);freqtrade 的 DCA/加仓回调来自官方文档 `strategy-callbacks`;Sentry 的 PII/采样参数来自 `sentry_sdk/consts.py`;pytest-benchmark 的 `--benchmark-json`/`--benchmark-compare-fail` 来自官方 `docs/usage.rst`。
