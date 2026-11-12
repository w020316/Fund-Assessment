# 阶段 1 · 增量代码审查报告（2026-11-12）

**范围**：第四轮基线（`8d7e7d4`，2026-11-08）之后 11 个 commit / 23 文件 / +2,876 −105。
**方法**：逐文件人工交叉审查（后端 5 个 + 前端 4 个新增文件全部精读）+ ruff 静态扫描 + 测试覆盖交叉验证 + 线上行为探针复核。
**审查面**：逻辑正确性、边界条件、异常分支、鉴权/限流、注入面、JSON 序列化安全、编码规范、功能完整性。

---

## 审查结论（总览）

本轮增量质量**整体为历轮最高**：

- **鉴权/限流/并发**：两个新重计算端点（`factor-research` / `optimize-ma-cross`）均正确挂载 `require_admin` + `_single_backtest_guard` 单飞锁 + `3/minute` 限流，且与历史回测共用同一把锁（复用而非另开）。线上匿名探针 401 已验证。
- **XSS 面**：两个新页面 JS 全程 `textContent` / `createElementNS`，0 处 `innerHTML`；请求模型约束完备（2MB / 2万行 / 200 股 / 面板 10 万格 DOS 上限）。
- **降级诚实化（R-08 B1-a）**：`opt_float` 语义清晰，None 全链路传递，前端 `null→—` 渲染与 `data_quality=degraded` ⚠ 标识闭环，未发现伪造 0 的回归。
- **量化逻辑**：purged time holdout、Alpha144 分组秩、IR 年化口径、基准对齐守卫（`valid_rows == len(df)`）实现正确且注释诚实（过拟合、幸存者偏差、IR 未年化等限制均写进 notes）。
- **测试**：增量模块覆盖率 97%/99%/88%，新增测试为行为级断言（含降级路径）。

## 问题清单

| # | 级别 | 位置 | 问题 | 修复建议 |
|---|---|---|---|---|
| F-1 | **P2** | `web/routes/dashboard.py:571` + `src/core/backtest.py:52,59-65` | **Sortino=inf 泄漏到 API 边界，产生非法 JSON**。`_calc_sortino_ratio` 在组合日收益无下跌日且均值为正时返回 `float('inf')`（commit `2dfe9e1` 于 2026-08-01 有意从 99.0 改回 inf 并由 `tests/test_backtest_analyzers.py:95` 锁定），但 `portfolio_risk` 端点直接 `round(sortino, 3)` 进响应——FastAPI 默认序列化为 `Infinity`，违反 JSON 规范，浏览器 `JSON.parse` 会整体失败，组合风险卡片白屏。同时 `backtest.py` docstring（L52「返回 99.0」）与 L59-61 注释（「改为有限大数 99.0」）与代码（返回 inf）矛盾，误导后续维护。 | 边界消毒：`dashboard.py` 在构造响应前 `sortino if math.isfinite(sortino) else None`，响应模型字段改 `float \| None`（前端 `fmtNum` 已有 null→—）；`backtest.py` 修正 docstring 与注释使其与 inf 行为一致。**不改测试断言**（inf 语义属既有设计决策）。 |
| F-2 | P3 | `web/routes/strategy.py:467-474` | `factor-research` 端点是全文件唯一没有 `asyncio.wait_for` 超时保护的重计算端点（backtest 45s / optimize 60s / validate 均有）。输入有界（2MB CSV / 面板 10 万格）故最坏耗时秒级、风险低，但单飞锁会被占住，且防护风格不一致。 | 补 `wait_for(..., timeout=BACKTEST_TIMEOUT_SECONDS * 2)` + TimeoutError→503，与兄弟端点对齐。 |
| F-3 | P3 | `web/static/ma-optimizer.html:31-32`、`factor-research.html` 同款 | 表单 input 垂直尺寸约 38px、按钮约 36px，低于移动端触摸目标 ≥44px 标准（阶段 3 矩阵待确认实际渲染值）。 | 阶段 3 实测后统一在媒体查询下补 `min-height:44px`。 |
| F-4 | P3 | `web/static/ma-optimizer.html:69` | 股票代码 pattern `[0-9A-Za-z]{1,12}` 前端允许字母，服务端不校验格式——输入非六位数字代码会走完上游取数才失败，返回"网格为空"的笼统提示，用户不知错在哪。 | 服务端补 `^[0-9]{6}$` 校验→422（与持仓/基金模块既有代码校验口径对齐）；前端 pattern 同步收紧。 |
| F-5 | P3 | `src/core/factor_research.py`、`web/routes/strategy.py`、`scripts/backtest.py` 等 | 新增量 Python 文件贡献 ruff **10 errors**（F401 未用 import×8、F841 未用变量×2），均可 `--fix`。 | `ruff check --fix` 增量文件，独立 commit。 |
| F-6 | P3 | `tests/`（conftest 相关） | 唯一 warning 来自 `test_verification_upsert_and_retry_overwrite` 硬编码未来交易日 2026-11-30，日期漂移后会从 warning 恶化为红测。 | 改用 `conftest.trading_days_ago()` 相对日期。 |
| F-7 | P3 | `web/static/index.html`（策略实验室卡片） | 回测结果标签「样本只数」实为 `data.trades`（交易次数），文案误导；存量问题非本轮引入，顺手修。 | 文案改「交易次数」。 |

## 功能缺口（对照视频链路 STEP 15 与同类产品）

| 缺口 | 优先级 | 用户价值 | 实现成本 | 建议 |
|---|---|---|---|---|
| 网格结果导出（CSV/JSON） | P3 | 研究留档、跨工具分析 | 低（前端 Blob，参照 factor-research.js 已有 download） | 入 backlog |
| IR 热力图二维色标图例（当前仅文字说明红绿含义） | P3 | 可读性 | 低 | 入 backlog |
| 存量洼地 stock_monitor 50% / trading_quant 55% 覆盖率 | P2 | 回归保护 | 中 | 已在历轮 backlog，维持 |

## 未发现问题的重点确认项

- 新端点无 SQL/命令注入面（无 SQL、无 eval、无路径拼接）。
- `optimize_ma_cross` 网格 63 组合 × 单次取数复用，`copy_slices=False` 仅对只读内置策略启用，注释已说明外部策略须保持默认 True——契约正确。
- 基准收益对齐：`equity_curve`（n+1 点）与 `benchmark_returns`（n 点）经 `padded` 错位对齐，且有 `valid_rows == len(df)` 守卫防日期解析失败错位。
- `factor_research` 的 segment 划分（train/purged/test/unlabelled）与标签规则注释一致，`cutoff` 越界不可能（train_ratio ≤ 0.9）。
- 前端 120s 超时 > 服务端 60s 上限，不会出现服务端已返回但前端已放弃的窗口。
