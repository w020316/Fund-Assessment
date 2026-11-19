# 第七轮质量评估 · 阶段二：问题闭环修复与缺失功能补全

**执行日期**：2026-11-19｜**基线**：`docs/quality-baseline-2026-11-19-r7.md`（3125P+1F+1S / 86% / ruff 171）

## 修复记录

### Q1（P1）guard 行为级用例在 Windows 误报失败 → 已修复 ✅
- **原始描述**：`test_guard_script_behaviour_matrix` 必然失败——`shutil.which("bash")` 命中未安装发行版的 WSL 启动器 stub，脚本不执行，rc=1 撞上期望值后输出断言必败（最小探针复现）。
- **技术方案**：`skipif` 升级为行为级 bash 可用性探针（`bash -c "exit 7"` 校验 rc==7）；子进程捕获加 `encoding="utf-8", errors="replace"` 消除编码线程崩溃。**未削弱任何断言**，真 bash 环境（GitHub Actions）矩阵断言完整保留。
- **提交**：`3155849`
- **验证**：`pytest tests/test_prediction_workflow.py` → 5 passed + 1 skipped（诚实环境跳过）；全量回归 0 failed。

### Q2（P2）ruff 171 errors → 0 ✅
- **原始描述**：F401×126（含 `__init__` re-export 门面 55 条被误伤）/ E402×23（lazy-import 结构性必然）/ F841×18 / F811×2 / E731×1 / F403×1。
- **技术方案**：分层治理——① `pyproject.toml` 新增 `[tool.ruff]` per-file-ignores 豁免 3 类结构性误报（`src/core/__init__.py`、`src/agents/__init__.py` re-export 门面；`web/api.py`、`web/routes/dashboard.py` 路由注册晚于中间件；`ai_service.py` load_dotenv 顺序）；② 手工删除 11 处真实死变量（`capital_flow` col_map、`market_assessment` level、`news` mapped、`ai_service` company、`data_source_v2`×6、`web/api.py` akshare 延迟加载改 `import akshare  # noqa`）；③ 测试 mock 未用变量改下划线前缀 ×7；④ 去重 `dashboard_route` 双导入（真 F811）；⑤ lambda→def；⑥ 其余 65 处未用导入 `ruff --fix`（`__init__` 门面已被豁免保护，规避历轮误删教训）。
- **提交**：`9c9c205`
- **验证**：`ruff check src web tests` → **All checks passed (0 errors)**；全量回归 3226 passed / 0 failed。

### Q3（P2）策略层覆盖率洼地 50/55/61% → 98/99/98% ✅
- **原始描述**：`stock_monitor.py` 50%、`trading_quant.py` 55%、`limit_up.py` 61%——历轮遗留，7 个告警检查器与评分分支几乎无测试。
- **技术方案**：新增 **84 个行为级用例**（不改任何业务代码）：
  - `test_stock_monitor.py` +40：spot/kline 降级、周涨幅分支、EPS 超预期 8 分支、量价背离 3 分支、大宗交易折溢价 7 分支、北向 5日/1日档位、板块轮动 7 分支、多告警聚合、RSI 定向断言（含单调序列 RSI→NaN→50 的边界发现）
  - `test_trading_quant.py` +27：`_read_active_params` YAML 读取/不全回退/损坏回退、K线缓存 3 行为、技术/资金/基本面/新闻/情绪五维评分档位精确值、资金流明细、北向汇总、市场异动筛选
  - `test_limit_up.py` +17：涨停池 3 交易日加载/去重/单日失败容忍、归因 4 分支、质量评分全档位边界（含封顶 100）、晋级概率精确值（修正封单 2e8 恰等阈值走 >1e8 档的边界）
- **提交**：`97b312e`
- **验证**：三文件覆盖率 **98% / 99% / 98%**；全量回归通过。

## 收口指标（实测）

| 指标 | 基线（r7 起） | 阶段 2 收口 | 变化 |
|---|---|---|---|
| 全量测试 | 3125P + **1F** + 1S | **3226P + 0F** + 2S | +101 用例，红灯清零 |
| 覆盖率 | 86%（18,471/2,581 miss） | **88%**（18,438/2,280 miss） | +2pp，未覆盖 −301 |
| ruff | 171 errors | **0 errors** | −171 |
| P0/P1 修复率 | — | **100%**（0 个 P0，1 个 P1 已修） | 达标 |
| P2 修复率 | — | **100%**（2 个 P2 全修） | 达标 |
| P3 | 4 条 | 已列入 backlog（Q4-Q7） | 不阻塞 |

**多场景验证方法**：每项修复均执行「专项测试 + 全量回归 + ruff」三重验证；Q1 另有最小探针复现比对；Q3 三次迭代修正测试数据构造直至精确断言。

> 本报告由 AI 协作生成，关键决策请由工程负责人复核。
