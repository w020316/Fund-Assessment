# 质量基线 · 第七轮（2026-11-19）

**HEAD**：`c4b0f7d`（main，工作树干净）
**性质**：增量轮——锚定第六轮（`quality-baseline-2026-11-17-r6.md`，HEAD=`561583b`）。本轮全部数字为**本机/线上实测**，未实测项显式标「待基线」，**不沿用任何历史值**。
**环境**：`D:/dev-tools/Python312/python.exe`（3.12.9）；ruff 随该解释器；本机 curl 一律 `--noproxy '*'`。

---

## 1. 核心基线（实测）

| 项 | 实测值 | 命令 |
|---|---|---|
| 测试（无插桩） | **3125 passed / 1 failed / 1 skipped / 2 warnings / 155.97s** | `python -m pytest tests/ -q -p no:cacheprovider` |
| 测试（`--cov`） | 同上 1 failed / 3125 passed，190.69s | 同上加 `--cov=src --cov=web` |
| **唯一失败用例** | `tests/test_prediction_workflow.py::test_guard_script_behaviour_matrix` —— 期望输出含 `secrets_ready=false`，实际空文件；伴随子进程读取线程 `UnicodeDecodeError('utf-8' codec can't decode byte 0xc0)`。**单测隔离复测仍失败，判定真实回归（P1）**，并发跑两套 pytest 时另有 `data/risk.db` 时间戳竞争假失败（共享目录干扰，非用例缺陷） | 隔离重跑确认 |
| **覆盖率** | **86%**（18,471 语句 / 2,581 未覆盖） | `--cov-report=term` |
| 测试增量 | **+29 例**（r6 收官 3096 → 3125 passed+1 failed） | 对照 r6 阶段五报告 |
| Lint | **171 errors**（F401×126 / E402×23 / F841×18 / F811×2 / E731×1 / F403×1；67 可自动修） | `ruff check src web tests --statistics` |
| Lint 增量 | 169 → 171，**+2**（增量开发轻微恶化） | 对照 r6 基线 |
| 增量规模 | **57 提交 / 74 文件 / +9,021 −622**（`561583b..c4b0f7d`） | `git diff --stat` |
| 线上健康 | ✅ **HTTP 200**，`status=ok`、`degraded=false`、`data_source=true`、`ai=true`、**`version=c4b0f7d`（=HEAD，指纹一致）**、`ai_providers=["zhipu_glm","agnes","ly77625"]` | `curl /api/health` |
| 冷唤醒耗时 | **58.04 s**（Render Free 休眠唤醒，`time_total` 实测） | `curl -w %{time_total}` |

> 冷唤醒 58s 属 Render Free 平台特性，非代码回归。阶段 3/4 性能口径继续拆「冷启动/稳态」两档。

## 2. 前端体量（实测，含 CRLF）

| 资产 | 磁盘 | 行数 | git blob LF | 备注 |
|---|---|---|---|---|
| `web/static/index.html` | **507,061 B** | **8,755** | **443,294 B** | r6：LF 498,426 B / 9,272 行 → **LF −55,132B（−11.1%）/ −517 行，首次反转** |
| ├ 内联 `<script>` | **2 块**（r6：13 块） | — | — | 外置化推进显著 |
| └ 内联 `<style>` | 1 块 | — | — | 不变 |
| 外置 JS | **16 个文件**（web/static/js/） | — | — | workbench 1052 行 / wizard / prediction / global-market / version-modal 等新增外置 |
| `web/static/css/workbench.css` | 35,234 B | 1,067 | 32,669 B | r6 1179 行 → 1067 行 |
| `web/static/js/workbench.js` | 56,807 B | 1,052 | 48,077 B | r6 1025 行 → 1052 行 |
| `web/static/css/tokens.css` | 7,265 B | 155 | 5,724 B | 设计 token 层 |
| `index.html` 内 `innerHTML` | **254 处**（r6：253） | — | — | 存量欠账基本持平 |
| 页面容器 | **24 个**（r6：20） | — | — | 新增 prediction / global 等页面 |

> **首页瘦身 backlog 结论**：r6 的「反向恶化」已修复——本轮新增功能（预测/全球面板）均以外置 JS 落地，index.html 净缩 55KB（LF 口径）。

## 3. 覆盖率明细（低洼模块，实测）

| 模块 | 覆盖率 | 语句/未覆盖 | 性质 |
|---|---|---|---|
| `src/strategies/stock_monitor.py` | **50%** | 216 / 107 | 存量洼地（历轮遗留） |
| `src/core/__init__.py` | 52% | 25 / 12 | 存量 |
| `src/strategies/trading_quant.py` | **55%** | 298 / 135 | 存量洼地（历轮遗留） |
| `src/strategies/limit_up.py` | 61% | 194 / 76 | 存量洼地 |
| `src/strategies/a_stock_analyst.py` | 61% | 231 / 90 | 存量洼地 |
| `src/analysis/market_review.py` | 67% | 369 / 122 | 存量洼地 |
| `src/core/data_source_v2.py` | **72%**（r6 66%） | **2,030 / 565** | 洼地收窄但仍是最大单点（未覆盖 ≈ 全项目 22%） |
| `web/routes/config.py` | 76% | 193 / 46 | 存量洼地 |
| `web/routes/monitor.py` | 79% | 195 / 40 | 存量洼地 |
| `src/core/prediction_store.py` | 80% | 335 / 68 | 本轮新增模块，达标边缘 |
| `src/core/prediction_service.py` | 82% | 537 / 98 | 本轮新增模块，达标 |
| **TOTAL** | **86%** | 18,471 / 2,581 | 门禁 78%，**过线** |

## 4. 待基线清单（诚实标注）

| 项 | 状态 | 阻塞原因 / 解锁条件 |
|---|---|---|
| 依赖 CVE 扫描 | **待基线** | 承 r6 §5：本机 Python 缺 `venv` 模块 + `api.osv.dev` 不可达；不对 2026 时间线版本凭记忆断言 CVE |
| Firefox / Safari 兼容性 | **待基线** | 本机仅有 Chromium 内核（Edge）；不伪造结论 |
| 线上稳态性能（LCP/CWV） | 并入阶段 3/4 | 分「冷启动/稳态」两档 |
| 原创性「非抄袭人工比对」 | 并入阶段 5 | 自动量化不能替代人工举证 |

---

> 本基线由 AI 协作生成，关键决策请由工程负责人复核。
