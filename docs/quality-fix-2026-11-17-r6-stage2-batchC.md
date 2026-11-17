# 第六轮质量评估 · 阶段二批次 C 修复记录（P2 收尾 7 项 + 1 处机理更正）

**范围**：`91cb4db..1a41c40`｜**提交**：7 个 fix/test + 1 个 docs｜**回归**：**3071 passed / 1 skipped / 0 failed / 89.62s**
**P2 关闭率：26 / 27 = 96.3%**（门禁 ≥90%，**达标**）

---

## 一、逐项闭环

| # | 编号 | 提交 | 内容 |
|---|---|---|---|
| 1 | D-02 + F-02 零方差无下限 | `ccca803` | `MIN_CORR_STD = 1e-12`，浮点残差不再制造噪声相关 |
| 2 | D-06 + F-04 季度口径 + 猜列 | `f2ac5ef` | 两源统一「最近已披露季度」+ 按 `<th>` 列名定位 |
| 3 | A-03 冷却非对称 | `6367543` | 指数退避 600→1200→2400，sina 历史兜底纳入暂停 |
| 4 | E-02 超时日志语义 | `f394f8f` | **代码已在批次 A `57c2434` 修过**，本轮补契约测试 |
| 5 | M-04 + C-02(后端) 缺失伪造成 0 | `8c3d835` | 上游 `-` 占位 → `null`，前端渲染「—」 |
| 6 | C-06 相关性用「红=涨」语义色 | `0c8ca7c` | 换中性风险色阶 + 显式图例「不表示涨跌」 |
| 7 | F-03 compare 缺 3 个关键分支 | `0419101` | 补 3 例，且**实测能变红** |
| 8 | inf 机理更正（我本人补） | `1a41c40` | 仓库里流传的错误 docstring 改正 |

### 1. D-02 + F-02（`ccca803`）

- **根因**：`np.std(r, ddof=1) > 0` **没有下限**。而"常数收益"在浮点下不产生精确 0：`1.01**i` 序列的日收益 std 实测 **8.6e-17** —— 旧判据认为它有方差，于是货基/理财这种净值只到 4 位小数、四舍五入残留噪声的标的，会与其它标的算出**纯噪声的相关系数**并混进 `avg_correlation`。
- **更要命的是**：既有用例 `test_zero_variance_fund_excluded` **正是靠这 1e-17 才通过**（拿 `1.01**i` 当"零方差"、断言它"有相关"）—— 结论对、机制错，所以这个缺陷一直没被发现。
- **方案**：`MIN_CORR_STD = 1e-12`（远低于任何真实日收益波动，只吃浮点残差）；用例里 `FLAT` 改**精确常数** `1.0`，`TINY` 保留 `1.01**i` 并改标为"浮点残差"，另加两只真方差对照。
- **验证**：① `test_tiny_variance_is_treated_as_zero` 先断言 `0 < std < MIN_CORR_STD`（即"看起来有方差"）再断言不参与；② `test_real_variance_still_counts` 反向保护，证明阈值没定高。

### 2. D-06 + F-04（`f2ac5ef`）

- **根因（两条叠加）**：① `_fetch_fund_holdings_em` 固定取当年，**1–4 月时当年 Q1 未披露 → 空**，而 akshare 兜底却映射到 `{year-1}4`，两源年度口径不一致；且空结果**缓存 12 小时**，用户一上午看空重仓股。② 解析器用「第一个以 `%` 结尾的 cell 就是占净值比例」，这个启发式**只因为真实页面「涨跌幅」列为空才成立**——一旦上游服务端渲染 `1.77%`，`weight/hold_amount/hold_value` **三字段整体错位而数值都像真的**，而既有 fixture 恰好把涨跌幅写成不带 `%` 的 `1.77`，把这个脆弱性掩盖了。
- **方案**：抽 `_latest_disclosed_quarter()` 作唯一口径（两源共用，em 当年为空再退一年）；空结果 TTL 12h → 600s；解析器改按 `<th>` 表头文字定位列（表头不全才退回旧启发式，兼容老版 7 列）；「只保留最新一期」改按 `(年,季)` 数值比较取最大，不再依赖"分块由新到旧"。
- **验证**：fixture 增加「涨跌幅带 `%`」变体，断言三字段仍正确（**改前会红**）。

### 3. A-03（`6367543`）

- **方案**：`_backoff_seconds()` 600→1200→2400 封顶，`record_success` 清零回落；`_fetch_sina_history` 也走 `_paused("ddx_sina_hist")`；**三源全 paused 时返回 `source="paused"`、`history_available=False`，绝不退到当日快照**（否则"被跳过"会伪装成"今天没有连增"）；`build_ddx_streak` 新增 `skipped` 与 `failed` 区分。
- **验证**：`TestSourceBackoff` 6 条，含"第 2 档 1200s 内即使已过 600s 仍 paused"（证明用的是退避不是固定常量）。

### 4. E-02（`f394f8f`）— 只有测试，没有代码改动

工程师判定：`except HTTPException: raise` 三处**已在批次 A 的 `57c2434` 补上了**，本轮无需改代码，缺的是把语义钉成契约的测试。
**我核对该判定成立**，所以 E-02 记为「批次 A 已修 + 本轮补契约测试」，**不重复计为本次新关闭**。
- 新增 2 条：超时路径 `logger.exception` 调用 **0 次**；真解析异常仍恰好 **1 次**（防"因噎废食"把真异常也静音）。
- **有效性实测**：临时删掉 `scan_limit_up` 的 `except HTTPException: raise` → 用例①立刻变红（1 failed），还原后 14 passed。

### 5. M-04 + C-02 后端侧（`8c3d835`）

- **根因**：东财 clist 对退市/停牌股把数值字段直接返回 `-`，实测 100 行样本里 **21 行 `f2='-'`**（PT金田A / 国华退 / ST星源…）。`_safe_float(item.get("f2", 0))` 一律填 0 → 渲染成"价格 0.00 / 涨幅 0.00%"，把"没拿到"画成"今天不涨不跌"。前端第二层 `Number(null) === 0` 同样把缺失变成 `+0.00%`。
- **方案**：`get_stock_ranking_em` 九个数值字段改 `_opt_float`（缺失 → None），`code/name` 仍字符串；`HotStocksItem` 四个数值字段改 `float | None`；`scan/new_high` 的新高判定加类型守卫（`None >= 3` 会 TypeError，且它本就不该算"涨 3% 的新高"）；前端热门股/板块不再 `Number(x)` 归一，缺失渲染「—」，**板块排序把缺失项显式分离并 `concat` 到末尾**（否则"没拿到"会被排进"领跌"）。
- **明确没做的事**：**没有改 `_safe_float` 本身的语义**（大量调用点依赖 `default=0.0`）。
- **验证**：用实测 21 行 `f2='-'` 的样本做 fixture，断言 `price is None` / `change_pct is None`（不是 0.0）；前端机制测试断言无 `Number(x) || 0`；排序用例断言缺失排末尾。

### 6. C-06（`0c8ca7c`）

- **根因**：`corrColor` 用 `var(--color-up)`（红）表示"相关性高=风险高"，而全站 `--color-up` 语义是「涨」→ 用户按红涨绿跌直觉会把"高相关"读成"正向利好"；`#FBBF24` 还是硬编码 hex，违背 token 规范。
- **方案**：`--qf-sem-critical` / `--qf-sem-warn` / `--accent-cyan` 三档 + 卡片内加图例「相关性**只**表示分散度风险…**不表示涨跌**，不是红涨绿跌」。
- **前置校验（我做的）**：先确认 `--qf-sem-warn:#F5A524` / `--qf-sem-critical:#FF5A5F` 在 `tokens.css:38,40` 真实存在，`--accent-cyan` 在 `index.html:31` 有定义——**换成不存在的 token 会让颜色静默失效，比原来更难查**。并加了一条 `test_qf_sem_tokens_exist_in_tokens_css` 防复发。

### 7. F-03（`0419101`）

- **补 3 例**：全标的失败（200 + 每项 `best is None` + `grid==[]` + 有 error + **连调两次顺序一致**）；**真跑一次引擎**（只 patch `DataSourceManager.get_history_kline`，`BacktestEngine` 不整类替换，63 组合 / 160 根 K 线），证明消毒逻辑在真实路径上被走到；路由返回值层断言 `information_ratio is None`。
- **有效性实证（原文）**：注释掉 `_sanitize_floats` 两处调用后 ——

```
>       assert results[0]["best"]["information_ratio"] is None
E       assert inf is None
```

还原后 92 passed。

---

## 二、【重要】inf 逃逸机理的第 3 次更正

工程师在 C-7 里提出「本环境 `jsonable_encoder`/`JSONResponse` 会把 inf 悄悄转成 null，所以文本级断言测不出」。**这个诊断是错的**，我实测证伪，并顺带修正了仓库里流传的 docstring。

| 说法 | 出处 | 实测结论 |
|---|---|---|
| "裸 `Infinity` 让前端 `JSON.parse` 整体崩" | 第五轮 `test_dashboard_portfolio_risk.py` docstring | ❌ 错。前端根本拿不到响应体 |
| "服务端 500" | 本轮阶段一汇编 | ✅ 对 |
| "`jsonable_encoder`/`JSONResponse` 悄悄转 null" | C-7 提交 body | ❌ 错。`jsonable_encoder({"x": inf})` 原样返回 `inf`，`JSONResponse` 抛 `ValueError` |

**最小 FastAPI 复现**（`return dict` 与 `return response_model` 各一条路由，值 `inf`，`TestClient(raise_server_exceptions=False)`）：两条均 `status=500`、`body='Internal Server Error'`。

**那么文本级断言为什么真的"测不出"？** 不是因为它转 null，而是因为——**500 的 body 是 `Internal Server Error`，同样不含 `Infinity`/`NaN` 字符串**，所以「响应文本里没有 Infinity」这条断言在 500 时也成立，**是一条空心断言**。

**结论（已写进 `1a41c40` 的 docstring）**：这类用例**必须先断言 `status_code == 200`**，再断言文本不含 `Infinity`/`NaN`，最后断言值 `is None`。三层缺一不可。
仓库里既有的 `test_dashboard_portfolio_risk.py::test_all_positive_returns_sortino_none` **本来就断言了 200**，所以它不是空心的——只是 docstring 的机理写错了。本轮已改正。

---

## 三、P2 关闭总账（27 项）

| 批次 | 关闭项 | 数量 |
|---|---|---|
| 批次 A | A-16、A-04（limit 归一 + 限流） | 2 |
| 批次 B1 | C-03、D-01、D-05、A-07、A-08、A-09、M-03 | 7 |
| 批次 B2 | D-03、D-04、C-04、C-05、A-15、B-01、B-02、F-01 | 8 |
| 批次 C | D-02、D-06、A-03、E-02、M-04、C-06、F-02、F-03、F-04 | 9 |
| **合计关闭** | | **26** |
| **部分关闭** | **C-02**（后端链打通 + 前端热点/板块已改；另 4 处 `_safe_float(change_pct)` 未动） | **1** |

**关闭率 26/27 = 96.3%**，门禁 ≥90% **达标**。

## 四、断言台账（本批）

| 文件:用例 | 改前 | 改后 | 强度 |
|---|---|---|---|
| `tests/test_portfolio_risk.py::test_zero_variance_fund_excluded` | `matrix[A][C] is not None`（靠 8.6e-17 噪声通过） | `matrix[TINY][N1] is None`，并把 `1.01**i` 的角色从"零方差"改标为"浮点残差" | 断言条数 3→6，**只升不降** |
| 其余 6 项（C-2/C-3/C-4/C-5/C-6/C-7） | — | **0 条既有断言被改**（C-2 的"只保留最新季度"用实现改成 `(年,季)` 取最大，用例原文未动） | — |

## 五、线上指纹

| 指纹 | 修复前（实测） | 修复后 | 判定 |
|---|---|---|---|
| `GET /api/market/ddx_streak?limit=5` 的 `data` 键 | `[failed, items, min_days, sampled, sources, trade_date]`（**无 skipped**） | 新增 **`skipped`**（"被跳过"与"没失败"可区分） | ✅ |

> 批次 C 的其余 6 项**没有可靠的匿名行为指纹**：C-1/C-2 需出特定边界数据才可观察，C-5 需停牌股进 top-N，C-6/C-7 是前端与测试层。这些项以单元测试 + 全量回归为证，**不编造线上指纹**。

## 六、遗留（如实）

1. **C-02 只打通了"热点榜"这条链**：`stock_realtime` / `index_realtime` / `sector_flow` / `intraday` 四处 `_safe_float(change_pct)` 未改——上游是腾讯/新浪/交易所、未见 `-` 占位，且这 4 个模型被多处算术消费，改 nullable 要连带 8+ 处前端守卫，属另一条链。
2. **A-15 仍为 26s**（真实基线 32s），压到 15s 内需动兜底跳数/单跳超时，改数据源覆盖面 → 独立立项。
3. **`_build_meta` 仍有 53 处未显式传 `quality_score`**（B1 记录已载），需「逐端点质量契约」专项。
4. **`index.html` 997 处插值仅 218 处转义**（B2 记录已载），需「前端 innerHTML 转义专项」按页推进。

---

> 本记录由 AI 协作生成，关键决策请由工程负责人复核。
