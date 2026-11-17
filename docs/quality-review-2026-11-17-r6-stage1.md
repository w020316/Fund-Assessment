# 第六轮质量评估 · 阶段一汇编（增量代码审查 + 功能完整性排查）

**项目**：QuantFlow Pro（Fund-Assessment）｜**HEAD**：`561583b`｜**锚点**：第五轮 `0ae5b7a`
**审查范围**：`0ae5b7a..561583b` 的 27 个提交 / 56 文件 / +7,982 −467 / 17 新增文件
**编排**：主理人齐活林｜**执行**：架构师高见远（后端数据面 A/B/E + 功能缺口）、工程师寇豆码（前端 C / 数值 D / 测试 F）、主理人（安全矩阵 / 自动化链路 / 覆盖率盲区 / 真实上游字段核验）
**明细报告**：`quality-review-2026-11-17-r6-stage1-backend.md`（45.7 KB，行号级）、`quality-review-2026-11-17-r6-stage1-frontend.md`（63.0 KB，含 inf/NaN 取值域表与 innerHTML 点位表）

---

## 一、问题总览

| 级别 | 数量 | 定义 |
|---|---|---|
| P0 阻断 | **0** | 线上功能错误 / 数据错到误导决策 |
| P1 严重 | **8** | 明确可复现的错误或不可用风险 |
| P2 一般 | **27** | 契约 / 口径 / 性能偏差，触发条件明确 |
| P3 建议 | **25** | 坏味道、可维护性、体验细节 |
| **合计** | **60** | 其中「本轮引入」48 项，「存量未修」12 项 |

**与第五轮对比**：r5 为 0 P0 / 1 P2 / 6 P3 = 7 项。本轮增量规模是 r5 的 2.5 倍（27 vs 11 提交），问题数 8.6 倍——**不是质量退步，是审查深度提高了**：r5 只做了「读 diff」，本轮做了上游真实响应核验、inf/NaN 取值域逐字段推演、innerHTML 逐点位溯源、测试有效性反向验证。

---

## 二、P1 严重问题（8 项，阶段二必须 100% 关闭）

| # | 编号 | 文件:行号 | 一句话 |
|---|---|---|---|
| 1 | **C-01** | `web/static/js/workbench.js:93-107` | `fetchJSON` 裸 fetch **无超时无 AbortController**，`Promise.all` 任一挂起 → `state.loading` 永不复位 → **「刷新」按钮永久置灰 + 60s 自动刷新静默失效，工作台假死不可自愈**（全站唯一绕过 `api()` 超时体系的模块） |
| 2 | **E-01** | `web/routes/strategy.py:330-335` | `asyncio.wait_for(to_thread(fn), 15)` **只取消 await、不取消线程**；akshare 东财接口无 socket 超时 → 每次超时泄漏一个长期占线程，默认 executor 打满后**全站 `to_thread` 排队**（代码注释自承「实测 scan_limit_up 超 60s 未返回」） |
| 3 | **A-01** | `data_source_v2.py:2501-2502` + `market_assessment.py:258-265` | 北向沪深拆分位写死 `0.0`，且 `sh_sz_split_available` 这个新 flag **在聚合层被丢弃** → 上游恢复时前端显示「沪股通 0.00 / 深股通 0.00」，把「无拆分数据」读成「沪深各恰好 0」 |
| 4 | **A-02** | `data_health.py:102-109` ← `:112-116` | `is_down()` 复用 `_status()` 的「从未成功即 down」老规则 → **冷启动首败即判 down**，新源被整轮放弃 600s，与 `DOWN_THRESHOLD=3` 设计意图相反 |
| 5 | **A-05** | `strategy.py:338/392/411` + `market.py:629` | 三个 `scan/*` 与 `/ddx_streak` 重型端点无鉴权**且无限流** → 与 E-01 叠加可拖死单 worker |
| 6 | **A-06** | `data_source_v2.py:1541` | 两融「融券卖出」映射到 `RQMCL`（**股**），与同排 `RZYE/RQYE/RZRQYE`（元）**混排差 3~4 个数量级**（已实测确认，见 §三.4） |
| 7 | **M-01** | `.github/workflows/prediction.yml:44-45, 75-77` | **定时预测循环自 11-09 起 6 个交易日全部失败**：仓库 `gh secret list` 为空，`PREDICT_API_BASE`/`ADMIN_TOKEN` 从未配置；4 个 cron 中 3 个走 skip 分支返回绿色 → **18 绿 6 红造成假绿，连续 6 个交易日无人察觉** |
| 8 | **M-02** | `src/core/data_source_v2.py` | 覆盖率 **66%**（1,807 语句 / **620 未覆盖**，占全项目未覆盖的 23%），且未覆盖集合**恰好是本轮新增的多源回退函数**（`_get_sector_ranking_sina` 52 行、`_kline_sina_fallback` 36、`_get_capital_flow_fallback` 29…）——**本轮的核心改动基本没有测试** |

---

## 三、P2 一般问题（27 项）

| 编号 | 文件:行号 | 一句话 |
|---|---|---|
| C-02 | `workbench.js:225,665,695,706`；`sector-board.js:34` | `Number(null)===0` → 缺失被画成 `+0.00%`（历史红线原样复现），排序也把缺失当 0 |
| C-03 | `portfolio.py:192` → `workbench.js:804,818` | 盘中全降级时 `total_est_day_pnl` 强制 `0.0` → 前端显示「¥0.00」，可被读成真实盈亏；单只项出现「— 涨跌幅 + ¥0.00」自相矛盾行 |
| C-04 | `workbench.css:942`、`.wb-hot-row 683-702`、`.wb-link 333-341` | 移动端触摸目标未达 48px：`.wb-btn` 仅 40px，热门股行 ≈29px，页脚链接 ≈19px |
| C-05 | `index.html:7783` vs `3207-3218` | 新端点 `/api/portfolio/diversification` 未进 `API_TIMEOUT_MS` 白名单，落到 15s 默认（同类 `/risk` 给 30s）→ 前端先 abort，后端仍在跑 |
| C-06 | `index.html:7790-7791` | `corrColor` 用 `--color-up`（红=涨）表示「相关性高=风险高」，与红涨绿跌冲突；`#FBBF24` 硬编码违背 token 规范 |
| D-01 | `portfolio_risk.py:237-255` | 净值缺失的基金被**静默剔出 HHI 分母**→ 集中度必然偏高，而 `note` 只说「名义 N 只」 |
| D-02 | `portfolio_risk.py:212` | 零方差判定 `np.std > 0` 无下限；实测 `1.01**i` 序列 std=8.6e-17 被判「有方差」→ 输出纯噪声相关系数 |
| D-03 | `backtest.py:203-239` | `_analyze_plateau` 隐式依赖已排序网格；`component_ir_mean` 无 `isfinite`，`inf` 网格 → 500 |
| D-04 | `strategy.py:638-640` | 多标的排名按 IR 排序无 `isfinite`；`NaN` 比较恒 False → 排名不稳定且响应 500。与 r5 在 `dashboard.py` 建立的消毒纪律**不一致** |
| D-05 | `fund_holdings.py:153-163` | 东财改版后「持仓变化」不在 HTML 文本里，解析器默认填 `"不变"` → **实测 110022/161725 各 10 只全「不变」**，把「无法判定」输出成「确认没变」 |
| D-06 | `fund_holdings.py:85-93,188-206` | 1–4 月取当年 Q4 必然为空；两源年度口径不一致；**空结果缓存 12 小时**→ 一上午看不到重仓股 |
| A-03 | `ddx_streak.py:271-277,306` | 冷却非对称：可用源被过度惩罚（抖动一次再锁 600s），`_fetch_sina_history` 反而完全无 `_paused` 保护 |
| A-04 | `market.py:640` | `/ddx_streak` 缓存键含 `limit(30)×min_days(10)`=300 组合，无参数档位约束 → 参数遍历放大上游 36 倍 |
| A-07 | `market.py:481-482,1013,972,991` | `hot_stocks`/`hot_stocks_signal`/`news`/`global_news` 空列表仍报 quality=80、`degraded=false`（`b870f3e` 同类路径未覆盖完） |
| A-08 | `market.py:1514-1550` + `market_assessment.py:100-122` | 四维数据全缺时温度计仍输出「中性 50 分/观望」且 quality=80 → **用缺失数据生成投资结论**，比空态更有害 |
| A-09 | `market.py:890/910/930` + `data_source_v2.py:1532,1577,1629` | `code` 未校验即 f-string 拼进东财 filter（`(SECURITY_CODE="{code}")`）→ 可改写过滤条件；同文件 `:687` 已有正确写法 |
| A-15 | `data_source_v2.py:1268-1336` | 新增板块兜底把最坏路径 11s→**17s**，被 6 个端点共用且无 single-flight；工作台 `Promise.all` 无超时（与 C-01 同症） |
| A-16 | `data_source_v2.py:1592` | `premium_pct` 直接把 `PREMIUM_RATIO`（**小数** −0.1747）当百分数 → 前端显示「-0.17%」，真值 **-17.47%**，**差 100 倍**（实测见 §三.4） |
| B-01 | `llm_router.py:945-951` | Gemini 协议下 `system` 提示词退化为 `user` 段 → 人格/JSON 约束弱化，且**不报错**，表现为「偶尔解析失败」 |
| B-02 | `llm_router.py:568` | `LY77625_PROTOCOL` 拼错（`gemini-pro` 等）**静默走 OpenAI 兼容层**（即开关本来要绕开的那层），无任何日志 |
| E-02 | `strategy.py:386-389,405-408,426-429` | `HTTPException(503)` 被外层 `except Exception` 二次捕获并 `logger.exception` → 正常超时在日志里长得像代码缺陷 |
| F-01 | `tests/test_frontend_workbench.py`（240 行 / 14 例） | **14 例全是源码字符串 `assert "xxx" in text`**，零行为断言；对模板字符串插值 `${it.name}` 完全免疫 → 本轮新增 innerHTML 的漏网无人拦截 |
| F-02 | `test_portfolio_risk.py::test_zero_variance_fund_excluded` | fixture 的「零方差」实为 8.6e-17 噪声，断言**靠噪声才通过** → 「结论对、机制不对」的伪覆盖，也正是 D-02 抓不到的原因 |
| F-03 | `test_strategy_routes.py::TestCompareMaCross`（14 例） | 恰好缺最关键的 3 个分支：IR=inf/NaN、全标的失败、真实引擎（整类 patch 掉）→ D-03/D-04 在 CI 里**永远不会被触发** |
| F-04 | `test_fund_holdings.py:1030-1073` | fixture 把涨跌幅写成不带 `%`，恰好让「第一个含 `%` 的 cell 是权重」成立 → 上游一旦服务端渲染 `1.77%`，三字段**全部错位且数值都像真的**，用例仍会过 |
| M-03 | `web/routes/market.py:45,54` | `_build_meta` 默认乐观：`quality = 100 if cached else 80`，`degraded = quality<70`；**market.py 94 次调用中 44 次未传 `quality_score`**（global_market 6/25、holdings 5/9、news 6/12）→ 空数据一律报 80 分健康 |
| M-04 | `data_source_v2.py:626-637` | `get_stock_ranking_em` 把 `f2/f3/...` 一律 `_safe_float(...,0)`；实测 100 行原始响应中 **21 行 `f2='-'`**（退市/停牌）→ 口径与 `30abf5c`（宽表改 None）不一致，任何扩大 count 的路径立刻伪造 0 |

---

## 四、P3 建议（25 项，入 backlog）

A-10 龙虎榜 pageSize（实测 58~85 行，当前 100 够用但余量 15%）｜A-11 `result: null` 静默 AttributeError｜A-12 `_get_block_trades_fallback` 整段死逻辑（无条件 `return []`，白打一次腾讯）｜A-13 股东户数无上期时输出「环比 0.00%」｜A-14 同花顺版 `get_northbound_flow` 已成无调用方死代码且仍是伪造 0 口径｜B-03 Gemini 分支用 `provider.api_key` 而非 `current_api_key`｜B-04 安全拦截与真空响应无法区分 → 一个用户输入熔断整条 Provider｜B-05 Gemini 分支不带 `usage` → 成本统计恒 0｜E-03 `em_get` 硬编码 `timeout=5` 未参数化｜E-04 market（200+degraded）与 strategy（503）两套失败契约并用

C-07 涨停/跌停 `0/0` 也标红｜C-08 雷达图缺失维度按 50 分画（图形等价于「有值且中性」）｜C-09 `sentiment='偏多'` 用绿色（**存量红绿写反**）｜C-10 `—%` 渲染｜C-11 首页体积反向恶化（LF 口径 462,847→498,426 B，+7.7%；内联 JS 293,733 字符 + 内联 CSS 36,701 = **占 65.1%**，外置可降至约 177 KB）｜C-12 同一 `/hot_stocks` 两页两套缓存策略

D-07 持股数缺失输出 0.0｜D-08「只取最新季度」依赖上游分块顺序｜D-09 `compute_streak` 不排序，akshare 版本间顺序不一致｜D-10 `backtest` 252/3% 与 `portfolio_risk` 244/2% 两套夏普口径并存｜D-11 多标的对比未回传各标的实际区间

F-05 `test_ddx_streak.py` 无乱序 DataFrame 用例｜F-06 两条用例 mock 前提互相矛盾｜F-07 覆盖盲区汇总（5 处）

M-05 `stock-compare.html`/`ma-optimizer.html` 返回链接 `href="/"`，`factor-research.html` 用 `/static/index.html`，三页不一致

---

## 五、主理人补充实测（4 项前置事实，供阶段二直接引用）

### 5.1 鉴权矩阵（全量 151 端点，脚本实测）

- 151 个端点，**82 个挂 `require_admin`**，69 个匿名。
- 匿名写端点 3 个，逐个人工核过，**均另有保护，无越权缺口**：
  - `POST /webhook/scan` → `Depends(_require_scan_key)`，env 未配 **503 fail-closed**、`secrets.compare_digest` 防时序、日志只记长度（合规）
  - `POST /three_question`（`review.py:105`）、`POST /dispatch`（`skills.py:53`）→ 均为只读计算 + `@limiter.limit("20/minute")`，`/dispatch` 硬编码 plan_only
- 兜底限流存在：`limiter = Limiter(key_func=rate_limit_key, default_limits=[f"{_ANON_GLOBAL_RPM}/minute"])`，`_ANON_GLOBAL_RPM` 默认 300；另有 `anonymous_read_guard_middleware` 与写端点 `10/minute`。
- **本轮新增 4 个端点**：`GET /ddx_streak`（匿名，有缓存/SWR/冷却）、`GET /diversification`（**`require_admin` ✅**）、`GET /stock_flow_streak`（匿名，有缓存）、`POST /compare-ma-cross`（**`require_admin` ✅**）。**零删除端点**。
- 结论：A-05 的问题**不是越权**，而是「重成本端点没有成本边界」。

### 5.2 定时任务链路（真实 GitHub Actions 运行记录）

`gh secret list` / `gh variable list` **均为空** ⇒ `PREDICT_API_BASE`、`ADMIN_TOKEN` 从未配置。
`Prediction Cycle` 近 40 次运行结论分布：**success 18 / failure 6**，6 次失败全部落在 `08:2x–08:4x UTC`（= 16:2x CST，日内唯一的真正执行窗口），日期 11-09 / 11-10 / 11-11 / 11-12 / 11-13 / 11-16 —— **每个交易日都失败**。

根因是**双重的**：① 配置缺失（secrets 未配）；② 失败语义自相矛盾——guard 步（`prediction.yml:75-77`）`echo "::warning::"` + 写 `pred_failures` 让整个 run 变红，而各执行步（`:85, :103, :121, ...`）用 `[ -z "$PREDICT_API_BASE" ] && exit 0` **静默成功**。同一次运行里「有一步说失败、其余步说成功」，最终红 run 被 3:1 的绿 skip 淹没，形成可长期存活的假绿。

### 5.3 覆盖率盲区（`coverage report -m` + 函数级归属）

`data_source_v2.py` 620 行未覆盖，按函数归属 Top：`get_stock_detail` 95、`_get_sector_ranking_sina` 52、`search_stock` 43、`_get_stock_ranking_sina` 41、`get_market_sentiment` 41、`_get_stock_news_sina` 39、`_get_sector_ranking_sina_v2` 38、`_kline_sina_fallback` 36、`get_research_reports` 36、`_get_capital_flow_fallback` 29。
—— 除 `get_stock_detail` / `search_stock` 外，**其余全部是本轮「多源回退」改造引入或改写的兜底函数**。

### 5.4 真实上游字段核验（4 项待确认全部闭环，活数据）

| 报表 | 实测证据 | 结论 |
|---|---|---|
| `RPTA_WEB_RZRQ_GGMX`（600519，2026-11-16） | `RZYE`=14,788,586,340、`RQYE`=322,111,545、`RQYL`=221,550、`RQMCL`=2,900、`SPJ`=1453.9；且 **`RQYE/RQYL = 1453.9 = SPJ`** | RQYL/RQMCL 是**股**，RQYE/RZYE 是**元**；报表**无融券「额」字段** ⇒ A-06 确认 P1，只能走「`short_sell=None` + 新增 `short_sell_volume`」 |
| `RPT_DATA_BLOCKTRADE`（300003，2026-11-16） | `DEAL_PRICE`=9.92、`CLOSE_PRICE`=12.02、`PRE_CLOSE_PRICE`=12.09；`PREMIUM_RATIO`=**-0.174708818636** = 9.92/12.02−1；`DISCOUNT_RATIO`=**-17.948717948718** = 9.92/12.09−1 | `PREMIUM_RATIO` 是**小数**（基准收盘价）、`DISCOUNT_RATIO` 是**百分数**（基准昨收）⇒ A-16 确认 P2，二者不可互换 |
| 同上 | `DEAL_AMT` 13,137,100 ÷ `DEAL_PRICE` 9.92 = **1,324,300** = `DEAL_VOLUME` | `DEAL_VOLUME` 单位是**股**，不是手 |
| `RPT_BILLBOARD_DAILYDETAILS` 近 8 交易日 | 每日行数 70/69/69/85/58/67/79/3 | 最大 85 < 100 ⇒ A-10 当前**不截断**，降为「余量仅 15% 的防御性调整」 |
| `push2.eastmoney.com/api/qt/clist/get`（全市场 100 行样本） | **21 行 `f2='-'`**（PT金田A / 国华退 / ST星源…） | `_safe_float('-',0)` → 0.0 ⇒ M-04 确认；当前 top-10 路径不触发，属口径不一致的潜伏风险 |

---

## 六、功能缺口清单（G 组，来自架构师代码面，标杆对标留阶段四）

| # | 缺口 | 用户价值 | 成本 | 优先级 |
|---|---|---|---|---|
| G-1 | 温度计/研判缺「数据可用性契约」（无 `available: bool` / `available_dims`，前端靠 note 字符串匹配，一改文案即失效） | 高 | 低 | **P1** |
| G-2 | 数据源「暂停/冷却」状态不可见（运维看到 down 无法判断是否已停止重试、何时自愈） | 中高 | 低 | P2 |
| G-3 | 无上游源开关/强制指定源，故障隔离与 A/B 无法做 | 中 | 低 | P2 |
| G-4 | 龙虎榜/两融/大宗缺「数据时点距今几个交易日」提示（周末看到 3 天前数据无提示） | 中 | 低 | P2 |
| G-5 | 资金连增候选池覆盖度不透明（仅涨幅 Top10+成交额 Top10+涨停池，上限 12，用户以为是全市场） | 中 | 低 | P2 |
| G-6 | 无冷启动预热（Render Free 频繁休眠，首个工作台请求等 8~17s，`Promise.all` 无超时） | **高** | 中 | P2 |
| G-7 | `_meta` 契约无文档（本轮新增 6 个降级信号字段散落各处） | 中 | 低 | P3 |

---

## 七、阶段二修复排序建议（待确认后执行）

**批次 A · P1 全关闭（8 项，必修）**
`C-01` → `E-01` → `A-06` → `A-16` → `A-01` → `A-02` → `A-05` → `M-01`
排序理由：先断「假死不可自愈」（C-01）与「超时反而制造雪崩」（E-01），再修「数据错到误导」（A-06/A-16/A-01），最后修「源被误判 down」（A-02）、「成本无边界」（A-05）、「假绿」（M-01）。

**批次 B · P2 高价值（14 项）**
`D-05`（伪造「不变」）→ `D-01`（分母缩小）→ `A-07`/`M-03`（空数据报 80）→ `A-08`（缺失数据出结论）→ `A-09`（查询注入）→ `D-03`/`D-04`（inf/NaN 出口）→ `C-03`（¥0.00）→ `C-04`（48px）→ `C-05` → `A-15` → `B-01`/`B-02` → `F-01`
**批次 C · P2 剩余（≥11 项）+ P3 顺手**，含测试有效性补强（F-02/F-03/F-04）与 M-02 覆盖率补测。

**需先过技术方案评审的 2 项**（用户规则第 6 条）：
- **E-01**：引入模块级受限 `ThreadPoolExecutor(max_workers=2)` + 半开熔断（60s 冷却直接 503）——属并发架构调整，需评审后再动。
- **A-05**：扫描端点**不加 `require_admin`**（决策已定，见 §八.1），改为 `@limiter.limit("6/minute")` + 进程内单飞——属限流架构调整。

**门禁**：P0/P1 100%；P2 ≥90%（27×0.9 → 至少 25 项）；每 commit 后全量回归（当前基线 2885 passed）；禁止改断言/删用例/降阈值；每次 push 后用新代码确定性行为做线上指纹。

---

## 八、阶段一的两项决策（已定，供阶段二执行）

1. **A-05 扫描端点不加 `require_admin`**：前端 `runScan()`（`index.html:5428-5446`）匿名直调 `/api/strategy/scan/*`，是匿名面核心功能；82 个端点已鉴权，扫描是刻意留公开的。加鉴权会让匿名用户点扫描直接 401，属产品回归。⇒ **资源风险在资源层解决（限流 + 单飞 + 受限线程池），不用鉴权解决**。
2. **移动端触摸目标复核标准取 48px**（严于上一轮的 44px），据此 C-04 判 P2；`README`/规范文件缺该口径，阶段五补入设计规范文档。

---

## 九、诚实边界

- **0 个 P0**：本轮不存在「线上功能已错」的缺陷。A-01/A-06/A-16 均为「上游恢复/数据存在时才会暴露的显示错误」，当前处于潜伏或已暴露但影响限于可读性。
- **12 项为存量未修**（C-09、C-10、D-06、D-10、A-07 部分、E-04 等），已在表中标注，不计入「本轮回归」。
- **3 项 P2 的可达性仍待证**：C-02（前端触发需后端下发 null）、D-05（消费方待确认）、F-02（numpy 版本敏感）。若阶段二核实不可达，按规则降级并记录，不虚报修复数。
- **对第五轮的一处更正**（本轮实测修正）：r5 报告把 `Sortino=inf` 泄漏描述为「裸 `Infinity` 非法 JSON 让前端 `JSON.parse` 崩」。实测本仓库走的是 Starlette 默认 `JSONResponse`，其 `render()` 使用 `json.dumps(..., allow_nan=False)` ⇒ **inf/NaN 逃逸的真实后果是服务端 500，前端根本拿不到响应体**。修复策略不变，但阶段二排障时不要按「前端 JSON 解析错误」去找。
- 依赖 CVE、Firefox/Safari 兼容维持「待基线」，理由见 `quality-baseline-2026-11-17-r6.md` §5、§6。

---

> 本汇编由 AI 协作生成，关键决策请由工程负责人复核。
