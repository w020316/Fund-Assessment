# 增量 PRD：策略自进化（Strategy Evolution）（2026-11-07）

作者：许清楚（产品经理）· 团队 software-stage5-design
主理人裁定：Q1 = P0 净值轻量评估（9 只持仓基金、120 交易日），bspro 因子权重进化降 P1；Q3 = 写回 config/strategies.yaml；Q2/Q4/Q5/Q6 按推荐项。

## 调研发现（代码库现状）

### 1. 策略相关模块（src/）
- **src/strategies/trading_quant.py**：`TradingQuant` 多维评分策略。核心可调参数为模块内硬编码的 `WEIGHTS`（technical 0.25 / capital 0.30 / fundamental 0.10 / news 0.20 / sentiment 0.15）与信号阈值（STRONG_BUY 80 / BUY 65 / WATCH 50 / HOLD 35 / SELL 20）。
- **src/strategies/bspro_quant.py**：`BSProQuant` 因子策略。`FACTOR_LIBRARY` 22 个因子（value/growth/quality/momentum/volatility 五类）；`backtest_strategy(factor_combo, period)` 接受因子权重 dict，但当前权重 `{"pe": -0.2, "roe_change": 0.3, "return_3m": 0.3, "sharpe": 0.2}` **硬编码在 web/routes/strategy.py:411**。已有单因子预算 20s（FACTOR_BUDGET_SECONDS）与 akshare 8s 超时控制。
- **src/strategies/a_stock_analyst.py / limit_up.py / cb_t0_sniper.py / stock_monitor.py / three_question_gate.py**：评分/扫描/门控类，参数较少（如 min_quality_score=60）。
- **src/core/backtest.py**：`BacktestEngine` 单票事件驱动回测，`BacktestResult` 已含 total_return / annual_return / max_drawdown / sharpe / win_rate / profit_loss_ratio / sortino / calmar / volatility / equity_curve / trades。内置 4 个硬编码策略函数（new_high / limit_up / cb_t0 / long_value）。成本模型：佣金 0.03%（最低 ¥5）+ 印花税 0.1%。**但 run() 接口是"策略函数"而非"参数组"——参数化回测能力需要薄封装。**
- **src/core/executor.py**：`SimulatedBroker`（进程内、即时成交、无挂单）+ `TradeExecutor`（sqlite **data/trade.db**，`get_trade_history()`，T+1 语义在 `_roll_trading_day` / `_available_for_sell`）。**历史成交记录存在且带 strategy/reason 字段，可作反馈数据源**，但当前无"信号自动下单"循环，模拟盘成交都来自手动 /api/trade 下单。
- **src/core/risk_manager.py**：RiskManager（sqlite data/risk.db，日内熔断/回撤暂停/禁新仓）。
- **src/core/fund_nav_source.py**：基金净值数据源存在（可用于按持仓基金净值评估）。

### 2. 配置（config/）
- **config/strategies.yaml**（63 行）：trading_quant 权重与信号阈值、cb_t0_sniper（max_premium_rate/stop_loss/max_positions）、limit_up.min_quality_score、stock_monitor.rules、strategies 段（new_high / limit_up_board / cb_t0 / long_value 的 max_position / stop_loss / execute）。**这是进化的主要作用对象，结构现成。**
- **config/sector_map.yaml**、**config/settings.yaml**（通用配置）。

### 3. 策略相关 API（web/routes/）
- **web/routes/strategy.py**：`POST /analyze`（require_admin）、`GET /scan/new_high|limit_up|cb`、`POST /backtest`（require_admin + `_single_backtest_guard` 单飞锁 + 30s 硬超时，503 显式失败）、`POST /validate`（滚动样本外+蒙特卡洛，require_admin + 20/min 限流）、`GET /list`（匿名，硬编码目录）。
- **web/routes/config.py**：`GET/PUT /api/config/strategies`（require_admin，yaml 原子写 + .bak）——阶段二鉴权改造已在位。
- **web/routes/trade.py**：`GET /api/trade/history`（require_admin，读 trade.db）。

### 4. 回测/模拟评估能力结论
- 已支撑：单票回测指标全套、样本外验证、单飞防并发、超时硬预算。
- 缺口：① BacktestEngine 只吃"策略函数"不接"参数组"；② 回测依赖外部行情（akshare/mootdx），30s 内只能跑少量标的；③ 模拟盘历史记录可作反馈源但**样本稀疏**（手动下单），短期主要反馈源仍是回测。
- 进化算力结论：**每代 N 个候选 × 每候选一次回测，同步单请求内只能跑"小 N + 短窗口 + 缓存行情"的快速版**（复用 30s 预算与单飞锁），完整版只能分步。

### 5. 前端现状
- web/static/index.html（8183 行单文件 SPA）：已有「HUD-策略」导航组 → 策略中心（列表表格）、策略实验室（strategyLab：analyze/backtest/validate 结果区）、策略参数卡（strategyParamsForm，读写 /api/config/strategies）。**无代际/版本/对比 UI，需新增**。

---

## 产品定义

### 产品目标（≤3）
1. **G1 自动寻优**：用户一键触发，系统在既有策略参数空间内自动变异、回测评估、择优，产出新一代参数组——把"手动试参数"变成"看结果选代"。
2. **G2 可追溯可逆**：每代参数组持久化存档（谱系可查、指标可对比），任何一代可一键回滚/激活，且激活动作必须显式人工确认。
3. **G3 不破坏既有语义**：进化只改"参数"，不触碰下单链路——T+1、RiskManager 熔断/禁新仓、SimulatedBroker 语义零改动；进化计算不得阻塞主请求循环、不得绕过鉴权。

### 用户故事（7 条）
1. 作为**管理员**，我想在策略实验室点一次"开始进化"，系统在限定预算内对当前激活代的参数做变异并回测评估，最后存为新代，**以便**不用手动反复调参试错。
2. 作为**管理员**，我想查看代际谱系列表（代号、父代、参数摘要、评估指标、时间），**以便**了解参数是怎么一步步变优的。
3. 作为**管理员**，我想并排对比"当前激活代 vs 新一代"的收益率/最大回撤/夏普/胜率，**以便**判断是否值得采纳。
4. 作为**管理员**，我想把某一代"设为激活"，使 /api/config/strategies 中的对应参数被该代参数覆盖，**以便**新参数真正生效于分析/扫描链路。
5. 作为**管理员**，当新代实际表现不如预期时，我想一键回滚到上一代（或任意历史代），**以便**快速止损。
6. 作为**管理员**，当进化失败（数据源不可用/超时/预算耗尽）时，我想看到明确的失败原因与状态码（503 语义，与既有回测一致），且**当前激活代与 strategies.yaml 保持原样不动**，**以便**失败不影响任何现有功能。
7. 作为**管理员**，我想看到模拟盘（trade.db）成交记录按策略聚合的胜率/盈亏摘要并纳入评估参考，**以便**进化不只依赖假设性回测。

## 需求池

### P0（Must have）
| # | 需求 | 说明 |
|---|------|------|
| P0-1 | **手动触发进化** | `POST /api/strategy/evolution/run`，`Depends(require_admin)` 显式挂载 + 复用单飞锁模式（同时只允许一个进化任务，非阻塞快速拒绝 503）。首期不做定时触发。 |
| P0-2 | **进化作用域 = 参数组** | **（主理人裁定后修订）** P0 仅对 trading_quant 五维 weights（归一化和为 1）与信号阈值（限幅内变异）做变异，评估器 = 净值轻量评估（9 只持仓基金、120 交易日）；**bspro 因子权重进化降为 P1**（逐票行情在 Render Free 上抖动大）。**不**变异风控参数（stop_loss/max_position 等，避免绕过 RiskManager 语义）。 |
| P0-3 | **变异 + 选择** | 每代生成 N 个候选（默认 5）：父代参数 + 高斯扰动（限幅），评估后按适应度取 Top-1 存为新代；无更优者则不生成新代（防退化）。允许单代仅完成部分候选（预算内到点收割已完成者）。 |
| P0-4 | **适应度评估** | 净值轻量评估：`fund_nav_source.py` 取 9 只持仓基金近 120 交易日净值序列（走 DataCache 缓存），"参数→净值择时模拟"（按 trading_quant 权重合成分做阈值择时的净值模拟）。硬预算 **≤25s**，适应度 = 加权组合（默认：年化收益 0.4 / 最大回撤 0.25 / 夏普 0.25 / 胜率 0.1，回撤为惩罚项）。评估全程包 `asyncio.to_thread` + `asyncio.wait_for`。 |
| P0-5 | **代际存储（文件层）** | `data/strategy_evolution/`：`generations/gen_<n>_<ts>.json`（参数全量+指标+父代+生成方式）、`lineage.json`（索引）、`active.json`（当前激活代指针）。用既有 `atomic_write_json`（tmp+replace+.bak）。每代 ≤50KB，上限保留最近 50 代，超出滚动清理最旧。 |
| P0-6 | **对比评估接口** | `GET /api/strategy/evolution/compare?gen=<n>`：返回激活代与新代参数 diff + 指标对照表。 |
| P0-7 | **激活 / 回滚** | `POST /api/strategy/evolution/activate {generation_id}`：将指定代参数写入 config/strategies.yaml 对应段（复用 `_save_yaml` 原子写 + .bak）并更新 active.json。回滚 = 激活任意历史代，同一端点。激活前强制返回 diff 预览供前端二次确认。 |
| P0-8 | **查询接口** | `GET /api/strategy/evolution/lineage`（谱系列表）、`GET /api/strategy/evolution/generations/<id>`（单代详情）。 |
| P0-9 | **鉴权（铁律）** | 上述**全部**新端点逐一显式挂 `Depends(require_admin)`，写入代码注释与验收清单（历史教训：漏挂导致过 P0 事故）。 |
| P0-10 | **失败兜底** | 数据源不可用/超时/预算耗尽：503 + `code`（与既有回测 503 语义一致，不返回空结果冒充成功）；失败时**不写任何代文件、不改 strategies.yaml**。进化单飞锁与回测单飞锁互斥或复用同一把，防止叠加打满 512MB 实例。 |
| P0-11 | **T+1/风控不受影响** | 进化产物仅为参数文件；分析/扫描/交易链路读参逻辑不变；不生成任何 Signal → TradeExecutor 调用。写入验收用例。 |

### P1（Should have）
| # | 需求 | 说明 |
|---|------|------|
| P1-1 | **前端代际谱系页** | 在「HUD-策略」下新增"策略进化"子页：谱系表格（代/时间/指标/状态）、对比面板（新旧指标并排+参数 diff 高亮）、激活/回滚按钮（带二次确认）。 |
| P1-2 | **模拟盘反馈聚合** | 读取 trade.db 按 strategy 聚合胜率/盈亏比，展示在代际详情页并作为适应度的参考展示项（首期不直接计入适应度权重）。 |
| P1-3 | **bspro 因子权重进化** | 评估器 = `BSProQuant.backtest_strategy`（复用单飞锁与预算），作为第二参数组进入同一谱系。 |
| P1-4 | **进化历史摘要推送** | 进化完成后写入现有 webhook 通知，含新代关键指标与"是否优于父代"结论。 |
| P1-5 | **进化运行状态端点** | `GET /api/strategy/evolution/status`：是否运行中/最近一次结果，供前端轮询（require_admin）。 |

### P2（Nice to have）
| # | 需求 | 说明 |
|---|------|------|
| P2-1 | 定时进化（如每周收盘后），复用 `src/core/scheduler.py`；受 Render Free 重启影响需可自愈，默认关闭。 |
| P2-2 | 自动采纳（连续 K 代优于父代才自动激活），默认关闭、显式开关放 strategies.yaml。 |
| P2-3 | 分步进化（长预算多候选拆多个请求推进），仅在 P0 快速版实测无法产出有意义结果时升级。 |
| P2-4 | LLM 对每代变异生成"变异理由"文字注释。 |

### UI Design Draft（前端增量）
- 位置：`index.html`「HUD-策略」导航组新增子 tab「策略进化」（与"策略中心/策略实验室"平级）。
- 布局：顶部操作条（[开始进化] + 运行状态指示）→ 左侧代际谱系列表（表格式，激活代高亮标签）→ 右侧详情/对比面板（指标对照表：收益率/年化/最大回撤/夏普/胜率，红涨绿跌配色遵循既有主题；参数 diff 用行内高亮）→ 底部操作区（[设为激活] 需先展示 diff 预览 + 确认弹窗；[回滚到上一代] 快捷按钮）。
- 交互规则：进化进行中按钮置灰并轮询 status（10s 间隔）；失败展示 503 返回的 code+message，不弹通用"未知错误"。

### 非目标（明确不做）
1. ❌ 不做自动实盘/模拟盘下单——进化产物只是参数，绝不生成交易信号（T+1 与 RiskManager 语义零改动）。
2. ❌ 不做策略**代码/因子公式**进化——只调参数，不生成新逻辑。
3. ❌ 不做机器学习训练（神经网络/完整遗传算法/贝叶斯优化）——高斯变异+择优足够，且匹配 Render Free 算力。
4. ❌ 不做多用户/多策略实例并行进化——单用户单管理员，单飞锁串行。
5. ❌ 不做盘中实时进化——评估基于历史数据，触发粒度最低为"手动/每日一次"。
6. ❌ 不做跨代回测数据缓存复用框架——行情缓存沿用 DataCache 既有机制，不新增基础设施。
