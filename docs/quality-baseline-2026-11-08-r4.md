# 质量基线 · 第四轮（2026-11-08）

**HEAD**：`8d7e7d4`（main，工作树干净，与 origin/main 同步）
**性质**：本轮全部数字为本机实测，附命令；未实测项显式标"待基线"，不沿用历史值。
**环境**：`D:/dev-tools/Python312/python.exe`；ruff 0.15.12；`.venv` 损坏；pip 出网被拦；本机 curl 打 localhost 需 `--noproxy '*'`。

---

## 1. 核心基线（实测）

| 项 | 实测值 | 命令 |
|---|---|---|
| 测试（无插桩） | **2515 passed / 1 skipped / 0 failed / 29 warnings / 55.23s** | `python -m pytest tests/ -q -p no:cacheprovider` |
| 测试（`--cov`） | **2515 passed / 1 skipped / 0 failed / 71.41s** | `pytest --cov=src --cov=web --cov-report=json:...` |
| 非确定性准入 | 无插桩 + `--cov` **双模式连续 2 次失败集为空**（协议要求 3 次，Phase 2 每 commit 后再补 1 次） | 同上 |
| 覆盖率 | **84%**（16,736 语句 / 2,738 未覆盖） | `--cov-report=term-missing` |
| 测试文件数 | **115** | `find tests -name "test_*.py" \| wc -l` |
| Lint | **174 errors**（F401×125 / E402×23 / F841×22 / F811×2 / E731×1 / F403×1；68 可自动修） | `ruff check src web tests --statistics` |
| 首页字节 / 行数 | **470,589 B / 8,644 行** | `wc -c / wc -l web/static/index.html` |
| 增量（自 `75bbc59`） | **11 提交 / 18 文件 / +1,356 −75** | `git diff --stat 75bbc59..HEAD` |
| 线上健康 | 🔴 **status=000 / time=25.0s / size=0 / curl exit 28（超时）** | `curl -m25 https://fund-assessment.onrender.com/api/health` |

## 2. 覆盖率洼地（`--cov` 实测）

| 模块 | 覆盖率 | 性质 |
|---|---|---|
| `src/strategies/stock_monitor.py` | **50%** | 存量洼地（非本增量） |
| `web/routes/prediction.py` | **71%** | 第三轮遗留（<75% 目标） |
| `src/core/prediction_store.py` | 80% | 第三轮面 |
| `web/routes/market.py` | 91% | 含新增异动端点，健康 |
| `src/core/llm_task_tier.py` | 99% | 健康 |
| `src/strategies/evolution.py` | 95% | 健康 |

## 3. 待基线 / 并入后续阶段

| 项 | 状态 | 说明 |
|---|---|---|
| CWV / DOM 节点 / 冷启动 / 首屏请求数 | **并入阶段 3/4 一次浏览器测量** | 沿用第三轮效率教训：阶段 3 断点矩阵与阶段 4 旅程共用一套 Playwright 探针，避免重复启停 |
| 依赖 CVE | **待基线** | pip 出网被拦，pip-audit 不可用；不编造 CVE 编号 |
| 依赖版本漂移 / 跨项目加载 | F-11 本地-only | `._pth` 含 `d:\xm\mylibrary-rag`，8 核心包跨项目加载；生产用 requirements.txt 不受影响（上轮决策不处置） |
| API 面全量路径/操作数 | 待 Phase 2 复核 | 本轮确认新增 `GET /api/market/stock_anomaly`（有前端入口，非孤儿） |
| 线上带凭据功能 | 待基线 | 本地无有效 ADMIN_TOKEN；线上不可达 |
| Firefox / Safari 兼容 | 待基线 | 仅 Chromium 内核可实测 |

## 4. 方向参考（非本轮实测，仅对照）

- 8 维度设计：上轮终评 7.7/10（`docs/stage5-design-final-2026-11-07.md`）→ 阶段 5 重测。
- SUS：上轮 62.5（开发者自评代理值）→ 阶段 4 重测，目标 ≥80 作方向。
- 覆盖率上轮终值 83.9% → 本轮实测 84%（持平略升）。

---

> 本基线由 AI 协作生成，关键决策请由工程负责人复核。