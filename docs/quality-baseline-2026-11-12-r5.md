# 质量基线 · 第五轮（2026-11-12）

**HEAD**：`0ae5b7a`（main，工作树干净，与 origin/main 同步）
**性质**：增量轮——基线锚定第四轮（`quality-baseline-2026-11-08-r4.md`，HEAD=`8d7e7d4`），本轮全部数字为本机/线上实测，未实测项显式标"待基线"，不沿用历史值。
**环境**：`D:/dev-tools/Python312/python.exe`；ruff 0.15.12；pip 出网仍被拦（pip-audit 维持待基线）；本机 curl 打 localhost/外网均 `--noproxy '*'`。

---

## 1. 核心基线（实测）

| 项 | 实测值 | 命令 |
|---|---|---|
| 测试（无插桩） | **2731 passed / 1 skipped / 0 failed / 2 warnings / 61.26s** | `python -m pytest tests/ -q -p no:cacheprovider` |
| 测试（`--cov`） | **2731 passed / 1 skipped / 0 failed / 84.98s** | 同上加 `--cov=src --cov=web` |
| 覆盖率 | **84%**（17,124 语句 / 2,696 未覆盖），与 r4 持平 | `--cov-report=term` |
| 测试增量 | **+216 例**（r4 2515 → 2731） | 对照 r4 基线 |
| Lint | **175 errors**（F401×126 / E402×23 / F841×22 / F811×2 / E731×1 / F403×1；69 可自动修） | `ruff check src web tests --statistics` |
| 增量 lint | 新增量 Python 文件贡献 **10 errors**（F401×8 / F841×2，均可自动修） | `ruff check` 增量文件清单 |
| 首页字节 / 行数 | **471,499 B / 8,652 行**（r4：470,589 / 8,644） | `wc -c/-l web/static/index.html` |
| 新增页面 | `ma-optimizer.html` 7,513 B；`factor-research.html` 13,042 B；配套 `js/ma-optimizer.js` 150 行、`js/factor-research.js` 240 行 | `wc -c` |
| 增量规模 | **11 提交 / 23 文件 / +2,876 −105**（`8d7e7d4..HEAD`） | `git diff --stat` |
| 线上健康 | ✅ **HTTP 200 / 0.82s**，`status=ok degraded=false`，`data_source=true ai=true`，`version=0ae5b7a`（=HEAD，部署指纹一致） | `curl /api/health` |

> 对照 r4 的"线上 000/超时"：本轮线上已恢复可达。r4 不可达系 Render Free 实例冷启动/休眠窗口，非代码缺陷。

## 2. 线上部署与鉴权指纹（实测）

| 探针 | 结果 | 结论 |
|---|---|---|
| `POST /api/strategy/factor-research` 匿名 | **401** | `require_admin` 已上线生效 |
| `POST /api/strategy/optimize-ma-cross` 匿名 | **401**（GET 405=路由存在） | 同上 |
| `GET /static/ma-optimizer.html` | **200** | 新页面已部署 |
| `/api/health.metrics` | `optimize-ma-cross` 出现在 top_slow_paths | 端点有真实流量 |

## 3. 覆盖率明细（增量模块实测）

| 模块 | 覆盖率 | 性质 |
|---|---|---|
| `src/core/backtest.py`（MA/IR/网格） | **97%** | 本轮增量，健康 |
| `src/core/factor_research.py` | **99%** | 本轮增量，健康 |
| `web/routes/strategy.py` | **88%** | 健康 |
| `web/routes/prediction.py` | **100%** | r4 洼地已由 `f39e8da` 补平 |
| `src/strategies/stock_monitor.py` | 50% | **存量洼地**（非本轮增量） |
| `src/strategies/trading_quant.py` | 55% | **存量洼地**（非本轮增量） |

## 4. 测试卫生观察

- 唯一 warning：`test_verification_upsert_and_retry_overwrite` 触发未来交易日（2026-11-30）守卫警告——测试日期硬编码将随时间推移变成稳定性隐患（P3，建议改用 `conftest.trading_days_ago()` 相对日期）。

## 5. 待基线 / 并入后续阶段

| 项 | 状态 | 说明 |
|---|---|---|
| 依赖 CVE | **待基线** | pip 出网被拦，pip-audit 不可用，不编造 |
| CWV / DOM 节点 / 冷启动 | 并入阶段 3/4 浏览器实测 | 沿用第四轮探针口径 |
| Firefox / Safari 兼容 | 待基线 | 仅 Chromium 内核可实测 |
| 线上带凭据全链路（回测/网格/因子） | 待阶段 4（需 ADMIN_TOKEN 登录态） | 匿名面已验证 401 |

---

> 本基线由 AI 协作生成，关键决策请由工程负责人复核。
