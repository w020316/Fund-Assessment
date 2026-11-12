# 阶段 2 · 问题闭环修复记录（2026-11-12）

**输入**：阶段 1 问题清单（`quality-review-2026-11-12-stage1.md`）：0×P0 / 1×P2 / 6×P3。
**结果**：P2 1/1 已修复；P3 6 项中 5 项已修复、1 项（F-3 触摸目标）按计划移交阶段 3 实测后统一处理。
**回归**：**2736 passed / 1 skipped / 0 failed / 1 warning / 70.83s**（基线 2731 + 新增 5 测试；warning 2→1）。
**全局 ruff**：175 → **167 errors**（增量文件清单 All checks passed）。
**CI**：push `8df9ff9` 触发，run 43196343750；**已部署上线并复验**（见文末）。

---

## 修复记录

### F-1（P2）portfolio_risk 边界消毒 Sortino inf · commit `7784524`
- **原始描述**：`_calc_sortino_ratio` 无下跌日时返回 inf（`2dfe9e1` 有意设计，测试锁定），`web/routes/dashboard.py:571` 直接 `round(inf,3)` 进响应 → FastAPI 序列化为裸 `Infinity`，非法 JSON，浏览器 `JSON.parse` 整体失败，组合风险卡片白屏。`backtest.py` docstring/注释与代码矛盾（称"返回 99.0"）。
- **方案**：不动上游函数语义与测试断言；API 边界 `math.isfinite` 消毒 inf→None；响应模型 `sortino_ratio: float | None`；前端 `fmtNum` 既有 null→`—` 渲染承接；`backtest.py` 注释对齐现实并注明 Web 边界消毒契约。
- **验证**：新增 `test_all_positive_returns_sortino_none`——断言响应文本不含 `Infinity`/`NaN` 且 `sortino_ratio is None`；专项 122 passed，含锁定的 inf 语义测试（未改动断言）。

### F-2（P3）factor-research 端点补超时 · commit `5d50a82`
- **原始描述**：strategy.py 中唯一无 `wait_for` 的重计算端点，异常环境下挂起会无限占用单飞锁。
- **方案**：`wait_for(timeout=60s)` + TimeoutError→503（"请缩小 CSV 日期范围或股票池"）。
- **验证**：新增 `test_timeout_returns_503`（monkeypatch 超时至 0.05s + 计算函数 sleep 挂起）；test_strategy_routes 56→59 passed。

### F-4（P3）股票代码服务端校验 · commit `772cc88`
- **原始描述**：前端 pattern 允许字母、服务端零校验，非法代码走完上游取数才以"网格为空"失败。
- **方案**：路由层 strip 后校验 6 位纯数字 → 422 明确报错；前端 `pattern="[0-9]{6}"` maxlength=6 inputmode=numeric。
- **验证**：`test_rejects_invalid_request` 参数化新增 ABC123/60051/6005191，断言 422 且 BacktestEngine 未被调用。

### F-5（P3）增量文件 ruff 清零 · commit `5f3e436`
- **原始描述**：增量及关联文件 10 lint 错误（F401×8 / F841×2，后细化为 F401×4 / F841×4）。
- **方案**：F401 `--fix` 自动修；F841 手工删——`cb_t0_strategy` 死变量 `current_low/current_high` 连同仅为其服务的价格列提取一并删除（纯死代码）；`except ... as e` 未用改裸 except。
- **验证**：ruff 增量清单 All checks passed；关联 4 测试文件 273 passed。

### F-6（P3）测试未来日期改相对日期 · commit `5d7974c`
- **原始描述**：`test_verification_upsert_and_retry_overwrite` 硬编码 2026-11-30 → 每次运行 UserWarning，日期漂移后成红测。
- **方案**：改用 `conftest.trading_days_ago()` 相对日期（验证日=2 交易日前，窗口=[30 日前, 今天]），断言语义不变。
- **验证**：test_prediction_store 11 passed，warnings 2→1（仅剩 pylibs 跨项目加载的 pkg_resources 环境告警）。

### F-7（P3）「样本只数」→「交易次数」 · commit `8df9ff9`
- **原始描述**：策略实验室回测结果标签与取值（`data.trades`）语义不符。
- **方案**：纯文案修正。验证：阶段 3 浏览器实测该卡片渲染。

### F-3（P3）新页面触摸目标 <44px —— 移交阶段 3
- 按计划在阶段 3 断点矩阵中实测实际渲染尺寸后，于移动断点统一补 `min-height:44px`。

---

## 上线复验（部署指纹）

| 探针 | 预期 | 实测 |
|---|---|---|
| `/api/health.version` | `8df9ff9` | （复验时回填） |
| `/static/index.html` 含「交易次数」 | 是 | （复验时回填） |
| CI run 43196343750 | success | in_progress |
