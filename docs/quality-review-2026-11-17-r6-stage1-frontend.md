# 第六轮质量评估 · 阶段一增量代码审查（前端面 / 数值算法面 / 测试质量面）

- 审查人：寇豆码（software-engineer）
- 审查日期：2026-11-17
- 项目：QuantFlow Pro（`D:/xm/wz/Fund-Assessment`）
- 基线锚定：第五轮 HEAD `0ae5b7a`（2026-11-12）
- 审查对象 HEAD：`561583b`（工作树干净，仅 `docs/quality-plan-2026-11-17-r6.md` 未跟踪）
- 审查范围：`git diff 0ae5b7a..HEAD`，27 个提交，C 组（前端工作台）、D 组（数值与算法）、F 组（测试增量质量）
- **本次审查未修改任何源码、未提交任何 commit**，只写本报告

---

## 1. 审查范围与方法

### 1.1 文件范围（实际打开阅读的文件）

| 组 | 文件 | 本轮变更 |
|---|---|---|
| C | `web/static/js/workbench.js` | 1025 行全新 |
| C | `web/static/css/workbench.css` | 1179 行全新 |
| C | `web/static/index.html` | 9272 行，**+726 −106** |
| C | `web/static/js/stock-compare.js` | 140 行全新 |
| C | `web/static/stock-compare.html` | 88 行全新 |
| C | `web/static/js/sector-board.js` | +12 −4 |
| C | `web/static/js/ma-optimizer.js` | +107 |
| D | `src/analysis/portfolio_risk.py` | +85（`compute_correlation` / `compute_concentration`） |
| D | `src/core/backtest.py` | +288（`_analyze_plateau` / tear sheet / 向量化快路径） |
| D | `src/analysis/fund_holdings.py` | +105（东财重仓股新表解析） |
| D | `src/analysis/ddx_streak.py` | 381 行全新 |
| D | `web/routes/strategy.py` | +141（对比端点 / 网格上限 / `_run_scan` 超时） |
| D | `web/routes/portfolio.py` | +120（`/diversification`） |
| F | `tests/test_frontend_workbench.py`、`test_ddx_streak.py`、`test_data_source_v2_eastmoney_reports.py`、`test_hot_stocks_ths.py` | 新增 |
| F | `tests/test_strategy_routes.py`、`test_portfolio_risk.py`、`test_backtest_ma_cross.py`、`test_backtest_analyzers.py`、`test_data_fabrication_fix.py`、`test_dashboard_portfolio_risk.py`、`test_fund_holdings.py` | 修改 |

### 1.2 方法

1. `git diff 0ae5b7a..HEAD -- <file>` 取增量，再读 HEAD 全文确认上下文与真实行号（本报告所有行号均为 HEAD 实测行号）。
2. **实测优先于推断**：
   - 用 `D:/dev-tools/Python312/python.exe` 直接调用 `_fetch_fund_holdings_em()`，**实连东财拿到了真实响应**（见 §6.3），据此判定解析器成立 / 不成立；
   - 用 numpy 复算测试 fixture，验证「零方差」用例是否靠浮点噪声通过（见 F-02）；
   - 用 `inspect.getsource(starlette.responses.JSONResponse.render)` 确认 `allow_nan=False`，据此确定 inf/NaN 逃逸的真实失败形态是 **500 而非非法 JSON**（见 §3 结论）。
3. 统计类结论（首页体积、内联 JS/CSS 占比、`innerHTML` 计数）由脚本对真实文件计算，不凭感觉：
   - `index.html` 工作树 CRLF 与 git blob LF 混用，已统一口径（见 C-11）。
4. 测试有效性按「断言是否可能因被测代码错误而失败」判定，不评价「能不能过」。抽样运行：`test_portfolio_risk.py`（35 passed）、`test_frontend_workbench.py` + `test_fund_holdings.py`（113 passed）。

---

## 2. 问题清单

> 级别：**P0 阻断 / P1 严重 / P2 一般 / P3 建议**
> 「本轮引入」列标注 `是`（`0ae5b7a..561583b` 新增或改写）/ `遗留`（基线已存在，本轮未修）。

| 编号 | 文件:行号 | 组 | 级别 | 本轮引入 | 问题描述（触发条件 + 可观察后果） | 修复建议 |
|---|---|---|---|---|---|---|
| C-01 | `web/static/js/workbench.js:93-107`（`fetch` 在 100 行） | C | **P1** | 是 | `fetchJSON` 裸 `fetch`，**无 AbortController、无超时**。`load()` 用 `await Promise.all([7 个请求])`，任一上游挂起（`/api/portfolio/watchboard` 打腾讯、`/api/review/board` 冷启动较慢）则整屏不渲染；`state.loading` 只在 `finally` 复位（894 行），挂起时永为 `true` → **「刷新」按钮永久 disabled（839 行置灰、895 行才恢复）**，60s 定时器（902 行）每次 `load()` 都在 836 行被静默 return，形成**不可自愈的假死**，只能刷新页面。全站已有 `api()`（`index.html:3207-3218`）按 URL 分档 15/25/30/40s 超时，工作台是唯一绕过它的新代码。 | 复用 `api()` 或给 `fetchJSON` 加 `AbortController` + `setTimeout`（行情类 15s、`/api/review/board` 30s），超时返回 `{ok:false,status:0}` 并让 `load()` 走到 catch 分支恢复按钮态。 |
| C-02 | `web/static/js/workbench.js:225`、`665`、`695`、`706`；`web/static/js/sector-board.js:34` | C | P2 | 是（workbench）/遗留（sector-board） | 前端用 `Number(x)` 归一化涨跌幅：`Number(null) === 0`、`Number("") === 0` → `clsOf(0)` 返回空类、`pctText(0)` 返回 `+0.00%`。即**上游下发 `null` 时会把「无数据」画成「+0.00%」**（历史红线原样复现）；`undefined` 会变 `NaN` 因而安全。当前后端 pydantic 声明 `change_pct: float` 非空，故**直接触发条件尚不成立**，但同一链路上 `web/routes/market.py` 多处 `_safe_float(item.get("change_pct"))`（如 253/421/448/546/565 行）**缺字段默认 0.0**，端到端仍会把「没拿到」变成「+0.00%」并参与排序（`renderSectors:652` 用 `Number(b.change_pct \|\| 0)` 排序）。 | 前端：改为 `isNum(it.change_pct) ? pctText(it.change_pct) : '—'`，排序时缺失值固定排末尾。后端（属 market.py/数据面，需另行派单）：区分 `None` 与 `0.0`，缺失下发 `null`。 |
| C-03 | `web/routes/portfolio.py:192` → `web/static/js/workbench.js:804,818` | C | P2 | 是 | 盘中估值全降级时后端把 `summary.total_est_day_pnl` 强制置 `0.0`（192 行），前端 `renderPortfolio` 不做判空，`fmtMoney(0)` → **`¥0.00`**、`clsOf(0)` → 无色。用户看到「组合估算当日盈亏 ¥0.00」，虽然旁边有「已降级」小字，但 0 是可被当作真实盈亏读走的数字。另 `portfolio.py:184` 单只降级项 `est_day_pnl: 0.0` 而同项 `est_pct: null`，前端列表里出现「— 涨跌幅 + ¥0.00 盈亏」的自相矛盾行。 | 后端降级时 `total_est_day_pnl` 下发 `null`；前端 `renderPortfolio` 对 `null` 显示「—」并在 `degraded` 时整块加「估算不可用」样式。单只项 `est_day_pnl` 同样置 `null`。 |
| C-04 | `web/static/css/workbench.css:942`；`.wb-hot-row`（683-702，`padding:6px 4px`）；`.wb-link`（333-341，`padding:4px 2px`） | C | P2 | 是 | 移动端可点击元素未达本轮 48px 复核标准：`.wb-btn` 仅在 `max-width:640px` 提到 `min-height:40px`（**低于 48，也低于旧 44px 门槛**）；`.wb-hot-row`（热门股 TOP5、资金连增全部行，`font-size:11.5px + padding 6px` ≈ 29px）与 `.wb-link`（复盘→/板块→/持仓管理→，≈19px）**没有任何移动端放大规则**。后果：粗手指误触，工作台是默认落地页，命中率最高。 | 在 `@media (max-width:640px)` 增加：`.wb-btn{min-height:48px}`、`.wb-hot-row{padding:14px 6px;min-height:48px}`、`.wb-link{min-height:48px;padding:0 8px;display:inline-flex;align-items:center}`。 |
| C-05 | `web/static/index.html:7783`（调用）+ `3207-3218`（分档表） | C | P2 | 是 | 新端点 `/api/portfolio/diversification` **未加入 `API_TIMEOUT_MS` 白名单**，落到默认 15s。该端点逐只基金拉历史净值（`portfolio.py:508-514` 信号量并发 `_fetch_fund_nav_series`），同类 `/api/portfolio/risk`（同源同量级）在 3215 行被显式给到 30s。后果：持仓 ≥3 只时前端先 abort，报「网络错误」而后端仍在跑，用户重试叠加上游压力。 | 在 `API_TIMEOUT_MS` 加 `if (url.startsWith('/api/portfolio/diversification')) return 30000;`，或与 risk/attribution 合并为一条。 |
| C-06 | `web/static/index.html:7790-7791` | C | P2 | 是 | `corrColor` 用 **`var(--color-up)`（红 `#F87171`）表示「相关性 >0.85 = 风险高」**，而全站 `--color-up` 的语义是「涨」。同一屏内「权重」列用青色、「平均相关」用红/黄/青，用户按红涨绿跌直觉会把「高相关」读成「正向利好」。另 `#FBBF24` 写死 hex，违反本仓库「色值一律引用 token」（`tokens.css` 已有 `--qf-sem-warn`）。 | 改用中性风险色阶（`--qf-sem-warn` → `--qf-sem-critical`）或加统一图例说明「红=风险高，非涨跌」；`#FBBF24` 换 `var(--qf-sem-warn)`。 |
| C-07 | `web/static/js/workbench.js:263` | C | P3 | 是 | 统计条「涨停 / 跌停」单元格硬编码 `cls: 'is-up'`，值为 `0 / 0` 时也标红，暗示「有涨停」。 | `cls: (isNum(lim.limit_up_count) && lim.limit_up_count > 0) ? 'is-up' : ''`。 |
| C-08 | `web/static/js/workbench.js:434,441`（`var v = isNum(d.value) ? ... : 50;`） | C | P3 | 是 | 雷达图对**缺失维度按 50 分绘制**，多边形看上去是「六维均衡的正常形态」；图例虽写「缺失」、脚注也说「仅作占位」，但图形本身在视觉上等价于「有值且中性」。数据诚实性上的次生风险。 | 缺失维度不画顶点（改用虚线连最近有效点）或整块置灰 + 图例前置「2 项缺失，图形不代表完整评分」。 |
| C-09 | `web/static/index.html:7283-7284` | C | P3 | **遗留** | `sentiment='偏多'` 用 `var(--color-down)`（绿）、`'偏空'` 用 `var(--color-up)`（红）——即**看多用绿、看空用红**，与全站红涨绿跌相反。`git diff 0ae5b7a..HEAD` 中无此段，_非本轮引入_，但属于「红涨绿跌写反」类的现存风险点，建议一并收口。 | 改为 `偏多→var(--color-up)`（红）、`偏空→var(--color-down)`（绿）；若坚持「红=危险」语义，需在文案处显式标注色义。 |
| C-10 | `web/static/js/sector-board.js:41` | C | P3 | 遗留 | `esc(sign + (isFinite(pct) ? pct.toFixed(2) : '—')) + '%'` → 非有限值渲染成 **`—%`**。 | 抽 `pctText()`：`isFinite ? x.toFixed(2)+'%' : '—'`。 |
| C-11 | `web/static/index.html`（整体） | C | P3 | 是 | 首页体积**反向恶化**：LF 口径 462,847 B → 498,426 B（**+35,579 B，+7.7%**）；行数 8,652 → 9,272（+620）。工作树为 CRLF，CRLF 口径 471,499 B → 507,698 B（+36,199 B）——两个口径增长率一致，报告以 LF 为准避免 CRLF 干扰。**本轮 +726 行中：内联 `<script>` 405 行 / 22,342 字符（占新增字符 56.3%）、内联 `<style>` 1 行 / 22 字符、HTML 标记 320 行 / 17,269 字符**。当前文件内联 JS 合计 **293,733 字符（单块，2877-9248 行）**、内联 CSS **36,701 字符**，二者占全文 **330,434 / 507,698 = 65.1%**。可量化瘦身：把主内联脚本外置为 `/static/js/app-core.js` 可直接把 `index.html` 从 507,698 B 降到约 **177,264 B（−65%）**；本轮新增的 22,342 字符同样可整块搬走。注：**总体字节不下降**（另存为独立文件），收益在「HTML 可缓存/可流式解析、重复访问不再传输 294KB 脚本、避免单文件 9272 行的合并冲突」。 | 优先级最高的是 `index.html:2877-9248` 单体外置；同时把本轮新增的 405 行按功能切到 `workbench-boot.js` / `portfolio-diversify.js` / `review-discipline.js` 三个文件（都是自包含函数，无跨段耦合）。 |
| C-12 | `web/static/js/workbench.js:22,880` vs `web/static/index.html:4716`（`apiCached(..., 'hot_stocks', 30)`） | C | P3 | 是 | 同一份 `/api/market/hot_stocks`，行情总览走 30s `localStorage` 缓存、工作台每 60s 直连无缓存；`/api/market/sector_flow` 同样。用户在两页之间来回切，会看到两套不一致的「热门股/板块」。 | 在工作台复用 `apiCached` 或在两处统一为 30s 缓存；至少保证同一会话内以「最近一次刷新时间」对齐。 |

### D 组

| 编号 | 文件:行号 | 组 | 级别 | 本轮引入 | 问题描述（触发条件 + 可观察后果） | 修复建议 |
|---|---|---|---|---|---|---|
| D-01 | `src/analysis/portfolio_risk.py:237-255` | D | P2 | 是 | `compute_concentration` 只在 `last_nav_map`（= 拉到 ≥2 点净值的基金）上算市值，**净值缺失的基金被静默剔出分母**，`weights` 仍归一化到 1。3 只持仓里 1 只净值拉不到 → HHI/有效持仓按 2 只算，「有效持仓 2.0 只」看起来像结论，而 `note` 只说「名义 3 只」。这是「名义分散/实际集中」诊断里最容易误导的一环：分母被缩小后集中度**必然偏高**。 | 返回 `weights_covered`（已计价市值 / 全部持仓成本市值估算）或在 `weights` 里显式带上 `null` 项；`note` 里写明「N 只中 M 只净值缺失，HHI 仅覆盖 M 只」。 |
| D-02 | `src/analysis/portfolio_risk.py:212` | D | P2 | 是 | 零方差判定为 `np.std(r, ddof=1) > 0`，**无下限**。实测：`nav=1.01**i`（数学上日收益恒等于 1%）的序列，`np.std` 返回 **8.6e-17**，被判为「有方差」→ 与其它标的算出相关系数并计入 `avg_correlation`。真实货基净值只有 4 位小数，四舍五入后残留 1e-17 级噪声时，**会输出纯噪声的相关系数并进入「平均相关」结论**。现有测试 `test_zero_variance_fund_excluded`（第 351 行） 正是靠这个浮点噪声才通过（见 F-02）。 | 改为 `np.std(r, ddof=1) > 1e-12`（或相对阈值 `> 1e-9 * abs(mean)`），并把阈值提为模块常量 `MIN_CORR_STD` 供测试锁定。 |
| D-03 | `src/core/backtest.py:203-239` | D | P2 | 是 | `_analyze_plateau` 以 `best = grid[0]`（205 行）取最优，**隐式依赖调用方已按 IR 降序排序**，函数内无排序也无校验；`component_ir_mean = round(sum(irs)/len(irs),4)`（233 行）无 `isfinite` 消毒，`cells` 的入选条件是 `ir > 0`（216 行），`inf > 0` 成立 → 一旦网格里出现 `inf`，`component_ir_mean` 直接是 `inf`，经 `/api/strategy/optimize-ma-cross` 返回时 Starlette `JSONResponse(allow_nan=False)` 抛 `ValueError` → **500**（实测见 §3 说明）。现有测试只喂已排序网格。 | 函数入口 `grid = sorted(grid, key=..., reverse=True)` 自保；`component_ir_mean` 计算前 `irs = [x for x in irs if math.isfinite(x)]`，空则返回 `None`；`cells` 条件改 `math.isfinite(ir) and ir > 0`。 |
| D-04 | `web/routes/strategy.py:638-640` | D | P2 | 是 | 多标的对比按 `best["information_ratio"]` 排序，**无 `isfinite` 消毒**；`NaN` 参与 `sort` 时比较恒为 False → 排序结果不稳定（同名数据两次请求可能排名不同），且 `NaN` 一旦进响应体 → 500（不是「非法 JSON」而是服务端 500，见 §3）。与第五轮 `dashboard.py` 已做的 `math.isfinite` 消毒策略**不一致**：同一类比率在两个出口两套规矩。此外 `optimize_ma_cross` 的 `grid`/`best`（`backtest.py:990-995`）同样无消毒。 | 在 `_run_all` 内对每个 `best` 做 `if not math.isfinite(ir): ir = None`；排序键改为 `(ir is None, -(ir or 0))` 让缺失稳定排在末尾；`grid` 各字段统一走一个 `_sanitize_floats(dict)`。 |
| D-05 | `src/analysis/fund_holdings.py:153-163` | D | P2 | 是 | 新表结构里「持仓变化」不是文本列，而是「相关资讯」单元格内的 **`变动详情` 链接**（实测 HTML：`<td class='xglj'>…变动详情股吧行情…</td>`，见 §6.3）。代码遍历所有 cell 找「新进/增持/减持」，一个都匹配不到 → **`change` 恒为 `"不变"`**。实测 `110022`、`161725` 各 10 只全部返回 `change:'不变'`。后果：把「无法判定」输出成「确认没变」，是最典型的伪造确定性。 | `change` 默认改为 `None`（前端显示「—」），只有真正匹配到关键词才赋值；或在表头/正则中识别 `变动详情` 并置 `"未披露"`。 |
| D-06 | `src/analysis/fund_holdings.py:85-93` + `188-206` | D | P2 | 遗留（本轮未修，但与「只取最新季度」直接相关） | `_fetch_fund_holdings_em` 固定 `year = 今年`、`month = ""`。**1–4 月**当年 Q4（上一年）报告是最新披露，但参数只取当年 → 上游返回空 → `content` 为空 → `[]`，失败原因文案是「基金可能无持仓披露」（把口径问题说成基金问题）。随后 `_fetch_fund_holdings_ak` 兜底把 `month<=3` 映射到 `{year-1}4`，**两条链路年度口径不一致**；且 `get_fund_holdings:243-249` 会把空结果 **缓存 12 小时**，用户一整个上午看到空重仓股。 | `_fetch_fund_holdings_em` 增加 `year-1` 回退：当年为空且月份 ≤4 时用 `year-1` 再请求一次；两源统一按「最近一个已披露季度」计算，抽公共函数 `_latest_disclosed_quarter()`；空结果缓存 TTL 降到 10 分钟。 |
| D-07 | `src/analysis/fund_holdings.py:151-152` | D | P3 | 是 | `float(cells[i].replace(",","").replace("--","0") or 0)` → 缺失（`--` / 空）时 **持股数与持仓市值输出 0.0**，不是 `None`。前端若直接显示「持股数 0 万股」即是伪造 0（红线）。 | 改 `_num_or_none()`：无法解析返回 `None`，API 层保持 `null`。 |
| D-08 | `src/analysis/fund_holdings.py:176-181` | D | P3 | 是 | 「只取最新季度」用 `latest = result[0]["quarter"]`，**依赖分块顺序由新到旧**。实测 2026 年响应确为 `3季度 → 2季度 → 1季度`（假设当前成立），但该顺序未写入契约也无断言来源；若上游改序，会**静默保留最旧季度**。另若首行 `quarter` 解析为空（`latest == ""`），`if latest:` 为假 → 不筛选 → 多季度混合后 `[:topline]` 截断，同一股票可能在不同季度重复出现。 | 改为 `latest = max(q for q in set(result_quarters))`（按 `(年,季)` 数值比较），与顺序解耦；`latest` 为空时按单季度分组最多取一组并加日志。 |
| D-09 | `src/analysis/ddx_streak.py:57-71` | D | P3 | 是 | `compute_streak` docstring 明确要求「升序」，但函数内**不排序**，完全信任上游。三个源里 `_fetch_sina_history` 自己排了（211 行），`_fetch_em_history` 与 `_fetch_akshare_history`（171-176）**均未排序**。akshare 各版本 `stock_individual_fund_flow` 的返回顺序不一致（CI 用 1.18.64、本地 vendored 1.14.53），一旦倒序 `reversed(values)` 就从最早一天开始数 → **连增天数算在错误的日期区间**且看起来合理。 | `compute_streak` 入口按 `date_col` `sort_values` 自保（无日期列时直接用原序并日志），使正确性不依赖上游顺序。 |
| D-10 | `src/core/backtest.py:45,79` vs `src/analysis/portfolio_risk.py:21-22` | D | P3 | 遗留 | 两套口径并存：`backtest` 用 `_TRADING_DAYS_PER_YEAR=252` + 3% 无风险利率；`portfolio_risk` 用 `244` + 2%。同一「夏普」在两个页面数值不可比，用户会以为其中一个是错的。本轮新增的 `_analyze_plateau`/IR 沿用 252。 | 在 `portfolio_risk` 模块 docstring 之外，于前端两处「夏普」旁增加口径脚注（「252 日/3%」「244 日/2%」），并在架构文档登记为已知差异。 |
| D-11 | `web/routes/strategy.py:588-650` | D | P3 | 是 | 对比端点只用**用户输入的统一起止日**，未回传各标的实际有效区间。若某标的在该区间内停牌/上市不足，引擎取到的 K 线更短，`annual_return` 的分母（年数）按传入日期算（`backtest.py:826-832`），**同一排名里各标的的年化收益率不是同一时间跨度**。 | `entries` 增加 `actual_start`/`actual_end`/`bars`，前端在「备注」列标注区间差异；差异超过 N 个交易日时给 warning。 |

### F 组

| 编号 | 文件:行号 | 组 | 级别 | 本轮引入 | 问题描述 | 修复建议 |
|---|---|---|---|---|---|---|
| F-01 | `tests/test_frontend_workbench.py`（全文 240 行，14 个用例） | F | P2 | 是 | **14 个用例全部是源码字符串 `assert "xxx" in text`**，无一条行为断言。它锁住的是「某段字面量还在文件里」，因此：① 抓不到 C-02 这类经 helper（`pctText`）产生的 `+0.00%`；② 反例检查只覆盖拼接式 `+ (it.name \|\| '—')`（56-62 行），**对模板字符串 `${it.name}` 完全免疫**——本轮 `index.html` 新增的 innerHTML 大量用模板字符串（如 7795 行 `corrColor` 段、8651-8677 行策略参数表单），这类漏网无人拦截；③ `assert "fund-head" not in text`（237 行）是与本次改动无关的全局子串检查，任何新类名含该词即误报。 | 保留少量「接线存在」断言，把转义检查改为真正的机制测试：`node --check` 之外，用 `pytest` + 正则扫描所有 `` `...${ `` 模板插值，禁止未包裹 `escapeHtml(` / `esc(` 的数据变量名白名单外出现；再补 1 条对着 `workbench.js` 的 JSDOM/无头契约测试（或退一步：把 `pctText/clsOf` 抽成可直接 eval 的纯函数并用 Node 断言）。 |
| F-02 | `tests/test_portfolio_risk.py`（`TestComputeCorrelation.test_zero_variance_fund_excluded`） | F | P2 | 是 | 用例意图是「零方差基金不参与相关对」，但 fixture 用 `1.0*(1.01**i)` 构造序列，**实测 `np.std` = 8.6e-17 > 0**，于是 `A` 被判为「有方差」并成功算出 A-C 相关——断言 `matrix[ia][ic] is not None` **正是靠这 1e-17 的浮点噪声才通过**。若换成真实数据（等值字符串解析出的精确常数）或 numpy 版本变化给出精确 0，用例立刻失败；反之它也无法发现 D-02 的真实缺陷（阈值缺失）。属于「结论对、机制不对」的伪覆盖。 | fixture 改为精确常数（`[1.0]*25`）并显式断言 `FLAT` 与**精确常数序列**都不参与；同时新增 `test_tiny_variance_is_treated_as_zero` 锁定修复后的 `MIN_CORR_STD` 阈值。 |
| F-03 | `tests/test_strategy_routes.py`（`TestCompareMaCross`，+203 行 / 14 例） | F | P2 | 是 | 路由行为覆盖很扎实（422 参数矩阵、鉴权、单飞锁、429、单只失败隔离），但**恰好缺最关键的 3 个分支**：① `best.information_ratio` 为 `inf`/`NaN` 时端点行为（D-04 的验证缺口）；② **全部标的失败**（`entries` 全 `best=None`）时排序与响应（当前靠 `-inf` 兜底，无断言）；③ 真实 `BacktestEngine` 一条都没跑（`patch("src.core.backtest.BacktestEngine")` 整类替换），因此 D-03/D-04 的消毒逻辑在 CI 里**永远不会被触发**。 | 补 3 例：`side_effect` 返回含 `float('inf')`/`float('nan')` 的 `best`，断言 `resp.status_code == 200` 且 `"Infinity" not in resp.text and "NaN" not in resp.text`（照抄 `test_dashboard_portfolio_risk.py:147-157` 的写法）；全失败用例断言 `results` 顺序稳定；用 `use_vectorized=False` 的小样本真跑一次引擎做一次冒烟。 |
| F-04 | `tests/test_fund_holdings.py:1033-1078`（`TestFetchFundHoldingsEmNewTableLayout`） | F | P2 | 是 | fixture `_row()` 把「涨跌幅」列写成 `'1.77'`（**不带 `%`**）。实测真实页面该列为空字符串（`<span data-id='zd00883'>--</span>` / `''`），**恰好让「第一个以 % 结尾的 cell 就是占净值比例」这条启发式成立**。用例因此把「上游恰好不给涨跌幅」当成不变量锁死：若东财改为服务端渲染 `1.77%`（完全可能，最新价/涨跌幅本来就是留给 JS 填的），`weight_idx` 会命中涨跌幅列，`weight=1.77 / hold_amount=9.55 / hold_value=36.76` ——**三个字段全部错位且数值都像真的**，本用例仍会通过。 | fixture 增加一条「涨跌幅带 % 后缀」的变体并断言解析结果（当前会失败 → 正好驱动改成按 `<thead>` 列名定位而不是按 `%` 猜）；解析器改为从 `<thead><th>` 读列序。</br>另：`test_keeps_only_latest_quarter` 把「分块由新到旧」写成了断言（D-08），但该顺序无外部依据，建议改为通过 `(年,季)` 比较取最大。 |
| F-05 | `tests/test_ddx_streak.py`（39 例） | F | P3 | 是 | 质量整体最好的一批（用 `date.today()` 偏移构造日期，不腐烂；`test_parses_newest_first_payload_into_streak` 验证了新浪侧的排序归一化）。盲区：**没有一例把乱序 DataFrame 直接喂给 `compute_streak`**（D-09 的路径），也没有 `_fetch_akshare_history` 返回倒序的用例。 | 补 `test_compute_streak_is_order_tolerant`：同一份数据正序/倒序各跑一次，断言 `streak` 相同（修复 D-09 后应通过）。 |
| F-06 | `tests/test_data_fabrication_fix.py`（+162 行 / 10 例） | F | P3 | 是 | `test_ensure_em_checked_triggers_probe_when_none`（第 372 行）mock 返回 `{"data": {"f12": "000001"}}`（**无 `f43`**）却断言探测成功；而同文件新增的 `test_probe_requests_fields_that_return_data`（第 329 行）明确写着「只带 f12 时上游返回 200 + `data:{}`，会误判」。**两条用例的 mock 前提互相矛盾**，前者是非忠实 mock（方向安全所以不假绿，但会把读者误导）。 | 统一 mock 载荷为 `{"data": {"f12": "000001", "f43": 363435}}`，或在前者用例 docstring 注明「该用例只验证调用链，不校验探测判据」。 |
| F-07 | 全量 | F | P3 | 是 | 本轮改动的**覆盖盲区汇总**（无任何测试触及）：① 多源回退 down 分支中 `ddx_streak._fetch_sina_snapshot` 未套 `_paused`（`ddx_streak.py:304-308` 只对 em/akshare 套了 `_paused`）；② `None` 传播到前端 `Number()` 的路径（C-02）；③ `inf/NaN` 消毒（仅 `dashboard.py` 一处有测试）；④ `_analyze_plateau` 引入的**未排序网格**与 `inf` 网格；⑤ `compute_concentration` 的重复 `fund_code`（同一基金两笔持仓 → `shares_map[code]` 被后者覆盖，`funds_out` 却出两行，见 D-01 附注）。 | 按编号逐条补测；重复 fund_code 建议在 `_load_positions` 后先做 `dict` 合并并保留 `lots`。 |

---

## 3. inf/NaN 取值域核查表（D 组专用）

**前置事实（实测）**：本仓库无自定义响应类，走 Starlette 默认 `JSONResponse`，其 `render()` 为 `json.dumps(..., allow_nan=False)`。因此 **inf/NaN 逃逸到响应的可观察后果是「500 Internal Server Error」，不是「裸 `Infinity` 让前端 `JSON.parse` 崩」**（后者只在 `allow_nan=True` 或手写 `json.dumps` 时出现）。修复策略不变，但排障时不要按「前端 JSON 解析错误」去找。

| 输出字段 | 表达式（文件:行号） | 可能产生的极端值 | 是否已消毒 | 消毒位置 |
|---|---|---|---|---|
| `hhi` | `sum(w*w for w in weights)`，`w = mv/total`（`portfolio_risk.py:249-250`，`total<=0` 已在 247 行拦截） | 值域 **(0, 1]**：`w_i ∈ (0,1]`、`Σw=1` ⇒ `hhi ≥ 1/n > 0`，不可能 inf/NaN。`total` 极大导致 `1e308` 级时 `w→0`、`hhi→0` 仍有限 | 结构上安全 | 无需（`hhi>0` 判断在 254 行挡住 `1/hhi`） |
| `effective_holdings` | `1.0/hhi if hhi > 0 else None`（`portfolio_risk.py:254`） | 值域 **[1, n]**；`hhi>0` 前置判断杜绝 `ZeroDivisionError`/`inf` | 是 | 同表达式内的 `if hhi > 0` |
| `weights[code]` | `round(mv/total, 6)`（`portfolio_risk.py:252`） | `[0,1]`，有限 | 结构上安全 | — |
| `correlation.matrix[i][j]` | `np.corrcoef(...)[0,1]` → `if np.isfinite(corr)`（`portfolio_risk.py:220-222`） | `np.corrcoef` 可返回 `nan`（零方差）或 `±inf`（极端输入） | **是** | `portfolio_risk.py:221` `np.isfinite` 过滤，不通过则保持 `None` |
| `correlation.avg_correlation` | `round(sum(pair_vals)/len(pair_vals),4)`（`225`） | `pair_vals` 全部经 `isfinite` 过滤 ⇒ 均值有限；空列表 → `None` | 是（间接） | `221` 行过滤 + `225` 行 `if pair_vals else None` |
| `correlation.overlap_points` | `int(next(iter(rets.values())).size)`（`214`） | `int`，0..n | 结构上安全 | — |
| `sortino`（`portfolio_risk` 出口） | `(annual_return - rf)/downside_vol`，`downside_vol > 0` 才计算（`portfolio_risk.py:102`） | `annual_return` 可为 `inf`（见下），`downside_vol>0` ⇒ 结果为 `inf` | **否 / 但出口为 None** | `102` 行 `if downside_vol > 0 else None` 只挡除零，**不挡 inf**；调用方 `web/routes/portfolio.py` 无 `isfinite`；`web/routes/dashboard.py:551` 对**另一条** sortino 路径做了 `math.isfinite → None`。两者不同源，勿混淆 |
| `annual_return`（`portfolio_risk`） | `(arr[-1]/arr[0])**(244/periods) - 1`（`portfolio_risk.py:84`） | `periods=1` 且净值跳变 R 倍 ⇒ `R**244`；`R ≥ 18` 即溢出为 `inf`（复权/拆分异常或坏数据可触发）。`round(inf,6)=inf`，**无 `isfinite` 消毒** | **否** | 无（`compute_risk_metrics` 内无消毒；`/api/portfolio/risk` 出口亦无） |
| `sharpe`（`portfolio_risk`） | `(annual_return - rf)/annual_vol`，`annual_vol > 0`（`97`） | `annual_return=inf` ⇒ `sharpe=inf` | **否** | 同上，无 |
| `calmar`（`portfolio_risk`） | `annual_return/abs(max_drawdown)`，`max_drawdown>0`（`104`） | 同上 | **否** | 同上 |
| `sortino_ratio`（`dashboard.py` 出口） | `_calc_sortino_ratio` → 可能 `inf` | `inf` | **是** | `web/routes/dashboard.py:551-552` `if not math.isfinite(sortino): sortino = None`（第五轮 P2 修复，本轮回归测试 `test_dashboard_portfolio_risk.py:147-157` 锁定 ✅） |
| `information_ratio`（网格 / best / 对比排名） | `mean(excess)*252/(te*sqrt(252))`（`backtest.py:148`）；`te<=0` 已返回 0.0（`146`） | `inf`：需 `te→0⁺` 且 `mean(excess)` 显著非零（`te<=0` 已挡，实测不可达）；**`NaN`：可达** —— 输入序列含 `NaN` 时 `np.std` 返回 `nan`，`nan <= 0` 求值为 `False`，**未被 146 行拦截**，最终返回 `nan` | **否** | 无。`backtest.py:995` 排序、`web/routes/strategy.py:638` 排序、`optimize_ma_cross` 返回体均无 `isfinite` |
| `information_ratio`（前端展示） | `number(best.information_ratio)`（`stock-compare.js:88`、`ma-optimizer.js`） | `finite()` 判定后显示 `—` | 是（仅展示层） | `stock-compare.js:8`、`ma-optimizer.js` 的 `finite()`；**不能替代后端消毒**（`NaN` 会让整个响应 500，前端拿不到数据） |
| `component_ir_mean`（高原识别） | `round(sum(irs)/len(irs),4)`（`backtest.py:233`） | `irs` 来自 `ir > 0` 的节点（`216`），`inf > 0` 成立 ⇒ `inf` | **否** | 无（见 D-03） |
| `grid_positive_count` / `component_size` | `sum(1 for ir in cells.values() if ir > 0)` / `len(irs)` | `int` | 结构上安全 | — |
| `skewness` / `kurtosis` | `pd.Series.skew()/kurt()` → `if pd.notna(...) else None`（`backtest.py:161-164`） | 退化序列返回 `NaN` | **是** | `backtest.py:163-164` `pd.notna` |
| `monthly_returns[i].return_pct` | `round((end/prev-1)*100,2) if prev>0 else 0.0`（`backtest.py:186`） | `prev>0` 保证有限 | 是 | 同表达式的 `if prev > 0` |
| `streak` / `net_today` / `net_sum` / `ratio_today`（ddx） | `round(float(v),2)`；`values[-1]` 已挡 `pd.isna`（`ddx_streak.py:75`）；`ratio` 带 `pd.isna` 判断（`99-103`） | 上游 `float("inf")` 字符串可转成 `inf` 并通过 `isna` | **否** | 无 `isfinite`；建议在 `compute_streak`/`_last_row_snapshot` 的 `round` 前加 `math.isfinite` 检查 |
| `win_rate` | `len(win)/len(sell)`，`if sell_trades else 0.0`（`backtest.py:854`） | `[0,1]` | 结构上安全 | — |
| `hhi` / `avg_correlation` 前端展示 | `d.effective_holdings == null ? '—'`、`fmtNum(d.hhi,3)`（`index.html:7816-7818`） | 依赖后端消毒 | 展示层有 `==null` 判断 | `index.html` 7816/7818 行 |

**结论**：新增的 HHI / 相关矩阵 / 连通域规模字段**在结构上安全**（值域有界或已 `isfinite` 过滤）；**真正未收口的是三类**：
1. `portfolio_risk.compute_risk_metrics` 的 `annual_return/sharpe/calmar/sortino`（无 `isfinite`，`annual_return` 可溢出为 `inf`）；
2. `backtest` 的 `information_ratio` 在输入含 `NaN` 时返回 `NaN`（`nan <= 0` 未拦截），并直接进入 `grid`/`best`/`component_ir_mean`；
3. `ddx_streak` 的数值出口无 `isfinite`。
第五轮修过的 `sortino=inf` 泄漏**只在 `dashboard.py` 一个出口被修复**，本轮新增的 2 个比率出口（`optimize-ma-cross`、`compare-ma-cross`）**没有继承同一条纪律**，这是「同一缺陷在不同出口复发」的结构性风险。

---

## 4. innerHTML 风险点位表（C 组专用）

### 4.1 新增 `innerHTML` / `insertAdjacentHTML` 点位全量核对

`index.html` 中 `innerHTML` 出现次数：基线 237 → HEAD **253**（+16）；本轮 diff 中新增的含 `innerHTML` 行 17 条。逐条核对结果：

| 文件:行号（HEAD 实测） | 拼接内容 | 注入源 | 是否转义 | 风险等级 |
|---|---|---|---|---|
| `index.html:4322`（`const paint = (html) => { … el.innerHTML = html }`） | 由调用方组装 | 见下 3 行 | 见下 | 低（通道本身） |
| `index.html:4342-4345`（`loadStockFlowStreak` 历史可用分支） | 连增天数、日期区间、累计净额、来源 | 后端 `/api/market/stock_flow_streak`（东财/新浪第三方） | **是**：`escapeHtml(range)`、`escapeHtml(_fmtFlowYuan(...))`、`escapeHtml(ratio)`、`escapeHtml(source)`；`d.streak` 为 `Number` 直插 | 低 ✅ |
| `index.html:4348-4350`（新浪快照分支） | 今日主力净额 | 同上 | **是**：`escapeHtml(_fmtFlowYuan(...))`，颜色走 `changeColor()` 白名单 | 低 ✅ |
| `index.html:4323`、`4353` | 固定文案 | 无 | 不涉及 | 无 |
| `index.html:4377-4390`（`renderDisciplineCompact`） | 纪律项名/值/提示、结论 | `Workbench.disciplineChecks()`（前端本地计算） | **是**：`escapeHtml(c.hint…)`、`escapeHtml(c.name)`、`escapeHtml(verdict.verdict)`、`escapeHtml(noteText)` | 低 ✅ |
| `index.html:4395`、`4407`、`4415`（复盘/三问页三态） | 固定文案 | 无 | 不涉及 | 无 |
| `index.html:4436-4444`（`searchStocks` 失败态） | 错误 message + 错误码 | `api()` 抛出的后端 `detail`（可能含上游文本） | **是**：`escapeHtml(msg)`、`escapeHtml(code)` | 低 ✅ |
| `index.html:4448`、`4451-4460`（搜索结果空态/列表） | 代码/名称/价格/涨跌幅 | 后端 `search`（股票名来自第三方） | **是**：`escapeHtml(it.code)`、`escapeHtml(it.name)`；`price`/`pct` 经 `_numOrDash`/`_pctOrDash` 返回白名单格式；颜色走 `changeColor()` | 低 ✅ |
| `index.html:4470`、`4475`、`4480`（个股详情加载/失败/无数据态） | 固定文案 | 无 | 不涉及 | 无 |
| `index.html:4508-4553`（`renderStockDetail` 主体） | 名称/代码/各指标/资金流/数据源/完整度 | 后端 `stock_detail`（第三方行情+财务） | **是**：`escapeHtml(name)`、`escapeHtml(d.code)`、`escapeHtml(missingText)`、`escapeHtml((meta && meta.data_source) \|\| '—')`；指标值经 `_numOrDash/_pctOrDash/_ratioOrDash/_pctAbsOrDash` 白名单 | 低 ✅ |
| `index.html:4557-4564`（`_stockRangeHTML`） | 最新收盘/30日高低/均量 | 后端 `kline_summary` | **是**：`_numOrDash()` 白名单 + `escapeHtml(k.latest_date)` | 低 ✅ |
| `index.html:4678-4700`（行情页资金连增 TOP5） | 名称/代码/连增/净额/来源统计 | 后端 `ddx_streak` | **是**：`escapeHtml(it.code)`、`escapeHtml(it.name)`、`escapeHtml((it.name…)+' · 连续 '+streak+' 日 · 截至 '+…)`（整串进 `title`）、`escapeHtml(_fmtFlowYuan(net))`、`escapeHtml(parts.join(' · '))` | 低 ✅ |
| `index.html:2918`（`tokenGateHTML` 定义）/ `7778`（分散度调用） | 固定文案 + `what` 参数 | 调用方传字面量 | 不涉及（无上游数据） | 无 |
| `index.html:7781`（分散度加载态） | 固定文案 | 无 | 不涉及 | 无 |
| **`index.html:7813-7830`（分散度诊断主体，含 7792 `fundsRow` / 7803 `corrTable` / 7790 `corrColor`）** | 基金代码/名称/权重/净值点数/相关矩阵 | 后端 `/api/portfolio/diversification`（基金名来自本地持仓文件的**用户输入**） | **是**：`escapeHtml(f.fund_code)`、`escapeHtml(f.fund_name \|\| '—')`、`escapeHtml(codes[i])`、`escapeHtml(d.note \|\| '')`；`corrColor(v)` 返回固定色值白名单；`fmtNum()` 输出数字 | 低 ✅（颜色语义问题见 C-06，非注入） |
| `index.html:7833`（分散度失败态） | 异常 message（**含后端异常原文**，`portfolio.py:589` `note=f"分散度计算异常: {e}"`） | Python 异常字符串，可能含上游 URL / 文件路径 / 数据源原文 | **是**：`escapeHtml(e.message \|\| e)` —— 已转义，不会 XSS；但**信息泄露面**（把内部异常原文展示给终端用户）建议收敛 | 低（XSS）/ 中（信息暴露） |
| **`index.html:8648-8677`（策略参数表单 `form.innerHTML = note + …`）** | 参数路径、标签、提示、取值 | `GET /api/config/strategies`（本地 YAML，管理员可编辑） | **是**：`escapeHtml(gmeta[0])`、`escapeHtml(group)`、`escapeHtml(path)`（含 `data-skey` 属性与 `title`）、`escapeHtml(label)`、`escapeHtml(hint)`、`escapeHtml(value)`（含 `value="…"` 属性）；`type` 来自 `typeof` 三分支白名单 | 低 ✅ |

`workbench.js`（1025 行全新，共 **26** 处 `innerHTML` 赋值）：`176`（数据源健康条）、`209`（指数空态）、`224`（指数卡）、`291`（统计条）、`312`（承接空态）、`359`（承接主体）、`456`（雷达+图例）、`490`（相位空态）、`502`（相位主体）、`575`（纪律空态）、`583`（纪律主体）、`608`/`617`（梯队空态）、`633`（梯队主体）、`649`（板块空态）、`657`（板块主体）、`681`/`686`（热门空态/主体）、`724`/`733`/`752`/`756`（资金连增四态）、`798`（持仓未授权）、`806`（无持仓）、`815`（持仓主体）。逐处核对结论：

- **所有来自上游的字符串都过 `esc()`**（`workbench.js:41`，内部委托给 `escapeHtml`，实测 `escapeHtml` 转义 `& < > " '` 五字符，见 `index.html:3124-3132`，属性插值安全）：`it.name`(228/698/699/709/710)、`s.sector`(669)、`s.name`(631)、`p.fund_name`/`p.fund_code`(824-826)、`x.k`(178-184)、`phase.label`(503)、`c.name`/`c.val`/`c.hint`/`aria`(594-597)、`d.ddx` 的 `it.name`/`it.code`/`tip`(766-771)、`d.degraded_reason`(819)。
- **纯数值插值**（`num/signed/pctText/fmtMoney/fmtYuanAmount/fmtNetYuan/fmtAmountWan`，返回值都是 `[+-]?digits[单位]` 白名单，或 `—`）：228-230、294、344、347、350、353、366-368、453、508-511、514-516、626-627、670-671、700-712、768-770、818/827-828。
- **样式类名插值**：`clsOf()`(55-58) 与三处三元表达式(120/127/152/363)只产出 `''/'is-up'/'is-down'/'is-warn'/'is-accent'` 固定集合，无注入面。
- **SVG 属性插值**（408-449）：全部经 `.toFixed(1)` 归一化，`anchor` 为三值白名单。

**结论**：本轮新增的 17 + 19 处 `innerHTML` **无一处存在 XSS 注入路径**，转义纪律执行到位（这是本轮的明确进步：历史欠账「152 处 innerHTML 仅 21 处转义」在本轮新增代码上没有继续恶化）。两点非注入类问题需处理：
1. `index.html:7833` / `portfolio.py:589` 把 Python 异常原文回吐给用户（转义了不炸，但暴露内部实现细节与上游 URL）→ 建议改为固定文案 + 日志留原文。
2. `innerHTML` 全量重建的性能面（见 4.2）。

### 4.2 性能 / 状态一致性 / 三态核查（非注入项）

| 检查项 | 结论 | 证据 |
|---|---|---|
| 全量重建 DOM | **每次刷新 12 个面板全部 `innerHTML=` 重建**，含 4 个 SVG 多边形/坐标重算（`workbench.js:401-459`）。60s 一次、单次 ≈1.2KB×12 的 DOM 解析，桌面端可接受；移动端（工作台是默认落地页）会有可感知的重排。无 diff-复用、无 `requestIdleCallback` 分片 | `workbench.js:868-878` |
| `setInterval` 未清理 | **无泄漏**。`startTimer()` 先 `stopTimer()`（899-903）；`onSwitch()` 在页面失活时 `stopTimer()`（972-974）；`wrapSwitchPage()` 用 `window.__workbenchWrapped` 防重复包装（978-979）；`tick()` 双保险（`page.active` + `visibilityState`，906-907）。脚本位于 `index.html:9270`（主内联脚本 2877-9248 之后），`window.switchPage` 此时已定义，包装不会静默失败 | 已核 |
| 事件监听重复绑定 | **无重复**。`bindRowClicks()` 用 `el.__wbBound` 幂等（936-938），且用事件委托（`closest('.wb-hot-row')`），容器重建不影响监听 | 已核 |
| 大列表虚拟滚动 / 分页上限 | 工作台自身上限明确（板块前 6/后 6、热门 TOP5、连增全量但候选池 10~12、持仓 TOP5）；`sector-board.js` 渲染全部板块（≈86 行，`max-height:520px` 内滚）；`stock-compare` 最多 5 行。**均无性能风险** | 已核 |
| 三态（加载/空/失败）齐备度 | 工作台 12 个面板**全部**有三态，且失败态文案逐条区分「取数失败 / 筛选无结果 / 未授权 / 冷却中」（`731-738`、`751-755`、`793-799`）。**唯一缺口**：`renderIndices`/`renderSectors`/`renderHot` 没有独立的「加载中」态复写（依赖 HTML 里的 `.wb-skel` 骨架，首次渲染后被覆盖，第二次刷新时旧数据保留 — 这是可接受的「保留上次数据」策略，但没有任何「数据已过期」标记，除 `wbLiveDot` 的 `is-stale`） | 已核 |
| `localStorage` 跨页一致性 | token 键统一为 `admin_token`（`workbench.js:96`、`index.html:3271`、其余 9 个 js 文件一致）；工作台自身偏好 `qf_workbench_auto_v1` 独立命名空间，无冲突。**不一致的是缓存策略**（见 C-12） | 已核 |
| 前端硬超时 90s / LLM deep ≤60s | 工作台**完全无超时**（C-01）；`api()` 分档最高 40s（`fund_analyze`），符合 90s 硬上限；`stock-compare.js:125` 与 `ma-optimizer.js` 的对比/优化请求也**未走 `api()`**（自建 `fetch`），同样无超时——但这两条链路后端有 `BACKTEST_TIMEOUT_SECONDS*2` 的 60s 上限兜底，风险低于 C-01 | 已核 |

### 4.3 红涨绿跌逐处取值逻辑（遍历结果）

色 token 定义：`tokens.css:36-37` `--qf-sem-up:#F87171`（红）、`--qf-sem-down:#34D399`（绿）；`aurora-theme.css:38-39` 把 `--color-up/--color-down` 别名到同一对。`workbench.css:23-24` 再别名到 `--wb-up/--wb-down`。⇒ **底层红=up 正确**。

| 位置 | 判定表达式 | 语义 | 是否正确 |
|---|---|---|---|
| `workbench.js:55-58` `clsOf(v)` | `!isNum(v)\|\|v===0 → ''`；`v>0 → 'is-up'` | 通用涨跌类 | ✅ 红涨绿跌 |
| `workbench.js:225-229` 指数卡 | `clsOf(Number(change_pct))` + `pctText` | 指数涨跌幅 | ✅（缺失值问题见 C-02） |
| `workbench.js:263` 涨停/跌停 | 硬编码 `'is-up'` | — | ⚠️ 见 C-07 |
| `workbench.js:287` `temp.score>=60?'is-up':<40?'is-down'` | 温度高=红 | 温度≠涨跌，用红表示「热」符合 A 股「热=红」习惯 | ✅ 合理 |
| `workbench.js:328-334` 承接状态 | 减弱 `is-down`(绿)、增强 `is-up`(红) | 好=红 | ✅ |
| `workbench.js:347` 封板率 | `seal<70?'is-warn':'is-up'` | 低封板率用 warn 而非「绿」 | ✅ 合理（避免把「差」画成「跌」） |
| `workbench.js:367` 封板率 mini | `seal<70?'is-down':'is-up'` | **同一指标在 347 行用 warn、367 行用 is-down(绿)**，同屏两处不一致 | ⚠️ P3 一致性（未单列，建议统一） |
| `workbench.js:390` 雷达·封板 | `100-broken_rate_pct` | 值域换算 | ✅ |
| `workbench.js:470-474` 相位 | 退潮 `is-down`(绿)、一致 `is-up`(红)、分歧 `is-warn` | 好=红 | ✅ |
| `workbench.js:514` 炸板率变化 | `delta<0 → 'is-up'`（炸板率下降=好=红） | ✅ 正确（**历史「领涨符号写反」同类点，本轮写对了**） | ✅ |
| `workbench.js:515` 涨停数变化 | `clsOf(limitDelta)` 正=红 | ✅ | ✅ |
| `workbench.js:537` 指数均值 | `clsOf(avg)` | ✅ | ✅ |
| `workbench.js:626-627` 连板梯队条形/数值 | 硬编码 `is-up` | 梯队高度非涨跌 | ⚠️ P3（同 C-07 类，建议中性色） |
| `workbench.js:665-671` 板块行 | `clsOf(Number(s.change_pct))` | ✅（缺失见 C-02） | ✅ |
| `workbench.js:695/706` 热门股 | `clsOf(Number(s.change_pct))` | ✅ | ✅ |
| `workbench.js:769` 连增·今日净流入 | `clsOf(Number(it.net_today))` 正=红 | 净流入为正=红，符合 A 股资金面习惯 | ✅ |
| `workbench.js:818/822-827` 持仓盈亏 | `clsOf(total)`、`clsOf(pct)` | ✅ | ✅ |
| `index.html:7320-7321` 涨/跌家数 | `▲gain` 用 `--color-up`(红)、`▼fall` 用 `--color-down`(绿) | ✅ | ✅ |
| `index.html:4342` 连增天数 | 硬编码 `--color-up` | 连增=好=红 | ✅ |
| `index.html:4348` 个股资金流 | `changeColor(d.net_today)` → `val>0 → var(--color-up)` | ✅ | ✅ |
| `index.html:4458` 搜索结果涨跌幅 | `changeColor(it.change_pct)` | ✅ | ✅ |
| `index.html:4519` 详情页涨跌幅 | `changeColor(q.change_pct)` | ✅ | ✅ |
| `index.html:4681` 资金连增 | `net>0 → --color-up`，`net<0 → --color-down`，否则 muted | ✅ | ✅ |
| `index.html:7790-7791` 相关矩阵 | `>0.85 → --color-up`(红) | **「高相关=风险」用红，与「红=涨」冲突** | ❌ 见 C-06 |
| `index.html:8654` 「生效中」徽标 | 硬编码 `#34D399`(绿) | 状态徽标用绿，非涨跌 | ⚠️ P3（写死 hex，应换 token） |
| `stock-compare.js:85-89` | `total_return/annual_return >= 0 → --qf-sem-up`(红) | ✅ 收益正=红 | ✅ |
| `stock-compare.js:88-89` | `information_ratio >= 0 → --qf-sem-up`(红) | IR 是风险调整后超额，正=好=红，可接受 | ✅（建议加「非收益率」说明） |
| `ma-optimizer.js` 月度收益 | `return_pct >= 0 → --qf-sem-up` | ✅ | ✅ |
| `index.html:7283-7284` 综合情绪 | 偏多=绿、偏空=红 | **反了** | ❌ 见 C-09（遗留） |

**小结**：本轮新写的工作台红涨绿跌**判定链全部正确**（含 `clsOf` 单一入口 + `changeColor` 白名单，未出现「领涨写反」），两处问题分别是**语义借用**（相关性用红=风险，C-06）与**遗留反向**（情绪用绿=多，C-09）。另有 3 处「非涨跌指标借用 is-up/is-down」的一致性问题（C-07、梯队、封板率两处不一致），建议统一引入 `is-pos/is-neg/is-hot/is-warn` 语义类，避免把「涨跌色」复用成「好坏色」。

---

## 5. 测试有效性评估（F 组）

> 判定口径：**有效断言** = 断言失败能真实反映被测行为回归；**复述实现型** = 用与被测代码相同的算法/同一份常量算出期望值；**mock 假绿** = patch 掉被测真实路径导致回退链/消毒/边界一条都没跑到。

| 测试文件 | 新增用例 | 有效断言数（估） | 复述实现型 | mock 假绿风险 | 覆盖盲区 |
|---|---|---|---|---|---|
| `test_data_source_v2_eastmoney_reports.py`（新，272 行） | 11 | **~40（最高质量）** | 低 | 低：`monkeypatch` 掉的是 `ds2.em_get`（HTTP 传输层），被测的报表名/筛选列/字段映射全在真实代码路径上跑，且**断言了请求参数**（`reportName`/`filter`/`sortColumns`）——这是防上游 schema 漂移的唯一有效手段 | 无 `akshare` 路径；`success:false` 分支仅 1 例 |
| `test_ddx_streak.py`（新，513 行） | 39 | ~95 | 低 | 中：`TestBuildDdxStreak` 直接 `patch.object(ddx, "fetch_streak")`，**四源回退链被整体短路**，回退只在 `TestFlowStreakFallback` 里单独验证（该组做得对，用 `monkeypatch` 卡 em/akshare/sina 三个取数函数，链路真跑）。日期用 `date.today()` 偏移，不腐烂 ✅ | `compute_streak` 的乱序输入（D-09）；`_fetch_sina_snapshot` 未套 `_paused` |
| `test_hot_stocks_ths.py`（新，84 行） | 3 | ~12 | 低 | 低：断言 `filter == "HS,GEM2STAR"`，是防上游参数变更的有效契约 | 响应缺 `info`/`page.total` 为 0 的分支 |
| `test_frontend_workbench.py`（新，240 行） | 14 | **~3（几乎全是源码 grep）** | 高（`assert "某字面量" in text` 本质是复述源码） | 无 mock，但也无被测行为 | 见 F-01：对模板字符串插值、经 helper 的数值格式、DOM 行为完全无感 |
| `test_strategy_routes.py`（+203 行） | 14 | ~35 | 中：`_canned_result()` 自己造 IR 再断言排名（排名逻辑本身是 1 行 `sort`，价值有限；422 矩阵/鉴权/单飞锁价值高） | **高**：`patch("src.core.backtest.BacktestEngine")` 整类替换，`optimize_ma_cross`/`_analyze_plateau`/`_calc_information_ratio` 一条没跑 | `inf`/`NaN` 消毒、全标的失败、真实引擎冒烟（F-03） |
| `test_portfolio_risk.py`（+179 行） | 14 | ~40 | 中：`test_two_funds_weights_hhi` 用 `(2/3)**2+(1/3)**2` 重算 HHI 再比 —— 是 HHI 的定义式，可接受但零增量信息 | 低：`patch("src.core.fund_nav_source.get_fund_nav_history")` 卡在数据层，路由/降级/聚合全部真跑 ✅ | 见 F-02 的零方差伪覆盖；`inf` 净值；重复 `fund_code` |
| `test_backtest_ma_cross.py`（+136 行） | 9 | **~30（本轮最有价值的一批）** | 低 | 无：`TestVectorizedEventEquivalence` 用**两套独立实现互校**（向量化 vs 事件引擎，3 个 seed × 6 字段），这是真正的差分测试；`TestPlateauDetection` 用手算连通域断言 | 未排序网格、`inf` IR（D-03） |
| `test_backtest_analyzers.py`（+133 行） | 7 | ~22 | 低：月度收益用 `(1.01**n-1)*100` 手工推导，可对拍 ✅ | 无：`_calc_monthly_returns`/`_calc_skew_kurtosis` 是纯函数直调 | `monthly_returns` 跨年边界、`prev<=0` 分支 |
| `test_data_fabrication_fix.py`（+162 行） | 10 | ~25 | 低 | 中：`patch.object(ds2._EM_SESSION, "get")` 卡在 HTTP 层（正确做法），但 mock 载荷前后矛盾（F-06） | 冷却窗口的并发行为（只测了单线程时序） |
| `test_dashboard_portfolio_risk.py`（+14 行） | 1 | **1（关键）** | 无 | 无 | 只覆盖 `dashboard` 出口；`strategy` 两个出口无同类用例（F-03） |
| `test_fund_holdings.py`（+43 行） | 2 | ~8 | 低 | 中：`patch.object(_EM_FUND_SESSION, "get", _mock_em_get(...))` 卡在 HTTP 层 ✅，但 fixture 列结构与真实页面不一致（F-04） | 涨跌幅带 `%`、多季度乱序、`--` 缺失值（D-07） |

### 5.1 「复述实现型」清单（对回归零价值）

1. `test_portfolio_risk.py::TestComputeConcentration::test_two_funds_weights_hhi` —— 期望值 `(2/3)**2+(1/3)**2` 即 HHI 定义式；应改为锁「已知手算案例」（如 `[0.5,0.3,0.2] → 0.38`）或锁「不变式」（`equal weights ⇒ eff == n`，该文件已有）。
2. `test_portfolio_risk.py::TestComputeConcentration::test_equal_weights_effective_equals_n` —— 与上条同源，可合并。
3. `test_strategy_routes.py::TestCompareMaCross::test_ranks_by_ir_desc_and_warns` —— 断言「按 IR 降序」= 断言 `sorted()` 会排序；真正的价值在 `warning` 文案与 `stock_code` 顺序，建议保留后者、删除对 `results[0]["best"]["information_ratio"] == 0.9` 的断言（那只是把 `_canned_result` 的入参抄回来）。
4. `test_frontend_workbench.py` 全 14 例 —— `assert "renderDiscipline(board, thermo)" in js` 之类，等价于「函数名没被改」，对行为零约束（详见 F-01）。
5. `test_ddx_streak.py::TestBuildDdxStreak::test_filters_by_min_days_and_sorts` —— 排序断言 `(-streak, -net_today)` 是从实现抄的；价值在于「min_days 过滤」部分，建议只留过滤断言 + 一条「相同 streak 时 net_today 大者在前」的手算案例。

### 5.2 测试卫生检查

| 检查项 | 结论 |
|---|---|
| 硬编码未来/绝对日期 | **良好**：`test_ddx_streak.py` 用 `date.today() - timedelta(days=n)`；`test_backtest_analyzers.py` 用 `date(2025,1,2)`（历史日期，不腐烂）；`test_fund_holdings.py` fixture 里 `2026年3季度` 是**匹配正则的固定输入**而非「当前时间」假设，安全。未见 `2027+` 之类会随真实时间腐烂的断言 |
| 依赖真实文件路径（`POSITIONS_FILE`） | **已有护栏**：`tests/conftest.py:22-49` 用 session 级 autouse fixture 对 `web/user_fund_positions.json`（真实持仓）做 mtime/size 快照，**任何测试改动它都会直接 fail**。新增的 `test_portfolio_risk.py` 路由用例全部用 `tmp_path` 注入。✅ 无「改写用户真实持仓」风险 |
| 依赖执行顺序 | 未发现：`test_data_fabrication_fix.py` 的 `setup_method/teardown_method` 双重清理 `_EM_AVAILABLE`/`_EM_LAST_CHECK`；`conftest` 每例清 `_llm_cache`/`_ai_cache`/限流存储/指标单例 |
| `sleep` 等待 | `test_strategy_routes.py::test_timeout_returns_503` 与 `TestCompareMaCross::test_timeout_is_503` 用 `time.sleep(1)` / `sleep(5)` 制造超时，但都配合 `monkeypatch.setattr(BACKTEST_TIMEOUT_SECONDS, 0.05)`，实际耗时 ≈0.05s，属**可接受**写法（若把 timeout 设大才是问题） |
| CI/本地 akshare 版本差异 | `test_data_source_v2_eastmoney_reports.py` / `test_hot_stocks_ths.py` **不依赖 akshare**，只依赖 `requests`（`monkeypatch` HTTP 层），CI 与本地一致 ✅；`test_ddx_streak.py` 的 `_fetch_akshare_history` 走 `monkeypatch`，未见 `create=True` 缺失问题；`fund_nav_source` 相关用例通过 `monkeypatch` 卡数据函数，未直接调 akshare 真实 sign |

### 5.3 已验证（抽样运行）

```
python -m pytest tests/test_portfolio_risk.py -q -p no:cacheprovider        → 35 passed
python -m pytest tests/test_frontend_workbench.py tests/test_fund_holdings.py -q → 113 passed
```
即：**上述 F 组问题全部是「通过但无效」，不是「失败」** —— 这正是本轮测试面的核心结论：2885 passed 不能证明本轮 27 个提交的行为正确性，尤其 `optimize/compare` 两条比率出口的消毒逻辑在 CI 里从未被执行。

---

## 6. 已验证合规项（本轮做对了什么）

### 6.1 数据诚实性（工作台是明显进步）
1. **缺失一律显示「—」并给原因**：`workbench.js:44-53` 的 `num/signed/pctText` 全部以 `isNum()` 为前置，`—` 是唯一缺失出口；12 个面板的空态/失败态文案逐条区分「上游未返回 / 取数失败 / 冷却中 / 筛选无结果 / 未授权」（`731-738`、`751-755`、`793-799`），明确写了「这是筛选结果，不是数据缺失」「不影响其他判读」。
2. **连增口径不当 DDX 冒充**：`workbench.js:773`「非通达信 DDX 大单净量」；`index.html` 详情页「新浪快照只有当日，不能据此计算连增」（`4365`）；后端 `ddx_streak.py:11-17` 把「失败 → None」写入模块契约，`_fetch_sina_snapshot` 用 `r0/r1/r2` 全 0 判「上游无数据」（`248-250`），**不把「没数据」当「净流入 0 元」**。
3. **交易日历/陈旧数据拒绝**：`ddx_streak.py:41,221-223` 对超过 15 天的历史直接判不可用（宁缺勿旧）。
4. **策略参数「生效中 / 仅记录」标注**：`index.html:8613-8619` 把 `trading_quant` 标「生效中」、其余 4 组标「仅记录」。**已实测核实该标注为真**：`grep -rn "max_position\|total_position" src/ web/ --include=*.py` 无命中，`src/strategies/stock_monitor.py` 无 yaml/config 读取，`trading_quant` 确为 `_read_active_params()` 真源。✅ 这是本轮最有价值的一处诚实性改进。
5. **`Sortino=inf` 消毒在 `dashboard.py` 落地并加回归测试**：`dashboard.py:551-552` + `test_dashboard_portfolio_risk.py:147-157`（断言 `"Infinity" not in resp.text`）✅（但仅此一个出口，见 §3）。

### 6.2 红涨绿跌
工作台全部 20+ 处涨跌判定经 `clsOf` / `changeColor` 单一入口，**未发现写反**；`100 - broken_rate_pct` 的封板率换算在统计条、相位、梯队、纪律四处一致，且 `test_frontend_workbench.py:65-69` 专门锁定「不得把炸板率当封板率」。

### 6.3 东财重仓股解析：**实测通过**（本轮修复有效）
实连 `fundf10.eastmoney.com` 后直接调用生产函数（只读 GET）：

```
_fetch_fund_holdings_em('110022') → 10 行
  {'code':'600519','name':'贵州茅台','weight':9.96,'hold_amount':80.0,
   'hold_value':100689.35,'quarter':'2026年3季度','change':'不变'}
_fetch_fund_holdings_em('161725') → 10 行
  {'code':'600519','name':'贵州茅台','weight':18.54,'hold_amount':492.84,
   'hold_value':620295.39,'quarter':'2026年3季度','change':'不变'}
```
真实表头实测为 `序号 | 股票代码 | 股票名称 | 最新价 | 涨跌幅 | 相关资讯 | 占净值比例 | 持股数（万股） | 持仓市值（万元）`，当前「最新价/涨跌幅」两列为空 → **`weight_idx` 命中 `占净值比例`，三字段全部正确**；同一响应的旧版季度块（7 列布局：`序号|代码|名称|股吧行情|比例|持股|市值`）也被 `weight_idx` 正确命中。⇒ 解析恢复**确认成立**，`test_fund_holdings.py` 两条新用例的数值与真实数据同构。

### 6.4 数值边界（本轮新写的部分）
- `compute_concentration`：0 持仓 / 权重全 0 / 负权重 / 非数份额 全部 fail-closed 返回 `None`（`_safe_shares` 164-170、`total<=0` 247），**无 `ZeroDivisionError`、无 `inf`**。
- `compute_correlation`：交集为空、样本 <21、零方差、`corrcoef` 返回非有限值 四条边界**逐条有防护且有测试**（`198-205`、`212`、`221`），对称性与对角恒 1 明确。
- `_analyze_plateau`：`[]`、全负 IR、NaN 最优（`not (nan>0)` 为真）、单格 全部返回 `None` 或 `size=1`，**不伪造高原**；`grid_positive_count` 与 `component_cells` 只含正 IR 节点。
- 网格上限：`_MAX_GRID_COMBOS=2000`（`strategy.py:511,548-552`）+ 前端 `customWindows()` 同口径预校验（`ma-optimizer.js:183-205`），前后端上限一致，不会出现「前端放行、后端 422」的体验断裂。

### 6.5 安全 / 无障碍 / 动效
- 新增 `innerHTML` 点位**零注入路径**（§4.1）。
- 工作台可点击元素全部是真实 `<button type="button">`（`1097-1218`、`workbench.js:698,709,766`），有 `:focus-visible` 焦点环（`workbench.css:702`），热门股行用**事件委托**而非内联 `onclick`（`935-943`），`aria-pressed`/`aria-live`/`role="list"`/`aria-label` 齐备（`wbHotBody` `aria-live="polite"`、纪律列表 `role="listitem"` + 完整 `aria-label`）。
- `prefers-reduced-motion` 双向定义（`workbench.css:900,908`），动效仅 `transform/opacity` ✅。
- `stock-compare.js` 全文件**零 `innerHTML`**（用 `createElement` + `textContent` + `replaceChildren`），是本仓库最干净的写法，`test_strategy_routes.py:803-812` 甚至用 `assert "innerHTML" not in js.text` 把它锁死了 ✅。

---

## 7. 待确认事项

| # | 事项 | 为什么无法在本轮定论 | 建议核实方式 |
|---|---|---|---|
| 1 | `web/routes/market.py` 多处 `_safe_float(item.get("change_pct"))`（253/421/448/546/565）把缺失默认成 `0.0`，是否已在别处转成 `None` | `market.py` 不在我的文件集内（+407 行，属数据面/架构面） | 由负责 `market.py` 的成员核对：`index_realtime`/`sector_flow`/`hot_stocks` 三条链路的 pydantic 模型（`change_pct: float` 非空）是否真的从未下发 `null`；若确认恒非空，C-02 降为 P3 防御性建议 |
| 2 | 东财「涨跌幅」列未来是否改为服务端渲染 `1.77%` | 当前实测为空（`<span data-id='zd00883'>--</span>`），但该列本就是留给 JS 填的占位，随时可能变 | 加监控：每日跑 1 只基金的 `_fetch_fund_holdings_em`，断言 `weight < 100 and weight + hold_amount + hold_value 三者量级关系合理`；或按 `<thead>` 列名定位（推荐，一劳永逸） |
| 3 | `fund_nav_source.get_fund_holdings`（akshare 版，被 `/api/portfolio/xray` 消费）是否同样受东财表格改版影响 | 走 akshare 内部解析，未实测；且 `latest_q = df["季度"].iloc[-1]` 同样依赖 ascending（`fund_nav_source.py:186-188`） | 实连 akshare `fund_portfolio_hold_em('110022', '2026')` 看返回顺序与列名；另确认 X-Ray 页当前是否显示正确的「最新季度」 |
| 4 | `test_fund_holdings.py` 的 `change:'不变'` 是否有别处消费（D-05 的真实影响面） | 静态前端 grep 无 `hold_amount`/`change` 消费点，但 `/api/fund/...` 的消费方可能在动态渲染 | 由负责基金页的成员确认；若无消费方，D-05 降为 P3 |
| 5 | 移动端 48px 标准是否已落文档 | 任务简报要求「复核标准提高到 48px」，仓库内未见对应规范文件 | 请主理人确认口径后，我把 C-04 的验收标准写死到报告（当前按 48px 判 P2） |
| 6 | `optimize-ma-cross` / `compare-ma-cross` 的 `information_ratio` 是否会实际出现 `NaN` | 需要上游 K 线含 `NaN` 收盘价才可达；`_sanitize_kline_rows`（`data_source_v2.py:422-442`）已过滤 `close<=0` 与 NaN，但 `get_history_kline`（`data_source.py`）的 tushare/akshare 分支**未见同样过滤** | 加一条防御性断言（`_sanitize_floats`）比追查可达性更经济，建议按 D-04 直接修 |
| 7 | `web/static/index.html` 是否有 `.gitattributes` 统一行尾 | 工作树 CRLF、git blob LF，导致体积统计出现 471,499 vs 462,847 两个口径 | 建议加 `.gitattributes`（`* text=auto`），否则每轮体积对比都要换算，容易误判「恶化幅度」 |

---

## 附：分级统计

| 级别 | 数量 | 编号 |
|---|---|---|
| P0 阻断 | **0** | — |
| P1 严重 | **1** | C-01 |
| P2 一般 | **15** | C-02, C-03, C-04, C-05, C-06, D-01, D-02, D-03, D-04, D-05, D-06, F-01, F-02, F-03, F-04 |
| P3 建议 | **14** | C-07, C-08, C-09, C-10, C-11, C-12, D-07, D-08, D-09, D-10, D-11, F-05, F-06, F-07 |
| **合计** | **30** | C 组 12 / D 组 11 / F 组 7 |

其中按「本轮引入」拆分：**本轮新引入 26 条；遗留（基线已存在、本轮未修）4 条** —— C-09（情绪看多用绿）、C-10（`—%`）、D-06（年初重仓股年度口径 + 空结果缓存 12h）、D-10（252/244 与 3%/2% 双口径）；另有 **C-02 为混合项**（`workbench.js` 侧为本轮新增，`sector-board.js:34` 侧为遗留）。

### 最严重 3 条（一句话）
1. **C-01（P1）**：`workbench.js` 自建 `fetchJSON` 无超时、无 AbortController，任一上游挂起就吃满 `Promise.all`，`state.loading` 永不复位 → **「刷新」按钮永久置灰 + 60s 自动刷新静默失效，工作台整体假死且不可自愈**（唯一绕过全站 `api()` 超时体系的模块）。
2. **D-05 + D-01（P2）**：东财改版后「持仓变化」已不在 HTML 文本里，解析器却默认填 `"不变"`（实测 110022/161725 各 10 只全为「不变」）——把「无法判定」输出成「确认没变」；同一类问题在组合分散度里表现为「净值缺失的基金被静默剔出 HHI 分母」。
3. **D-03/D-04 + F-03（P2）**：第五轮修过的「`inf` 逃逸到 API 边界」只在 `dashboard.py` 一个出口收口，本轮新增的 `optimize-ma-cross` / `compare-ma-cross` 两个比率出口**均无 `isfinite` 消毒**（`NaN` 可达：`_calc_information_ratio` 的 `nan <= 0` 未拦截），且 CI 里**没有任何测试会触发它**（`BacktestEngine` 被整类 patch）。

### 报告路径
`D:/xm/wz/Fund-Assessment/docs/quality-review-2026-11-17-r6-stage1-frontend.md`
