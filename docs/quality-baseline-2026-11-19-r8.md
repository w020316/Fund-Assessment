# 质量基线 · 第八轮（2026-11-19）

**HEAD**：`6765107`（main，工作树干净；开工前遗留的 2 个 docs 变更已由 `c1f56bc` 收口）
**性质**：增量轮——锚定第七轮收口 `ab01cc9`，评估主体 = 之后 7 个 DataGuard 绞杀迁移提交
**环境**：`D:/dev-tools/Python312/python.exe`（3.12.9）；ruff/compileall 随该解释器；线上 `--noproxy '*'`
**原则**：全部数字为本轮实测；未测项显式标「待基线」，不沿用任何历史结论

---

## 1. 核心基线（实测）

| 项 | 实测值 | 命令 |
|---|---|---|
| 测试用例收集 | **3,339 collected**（19.44s） | `pytest tests/ --collect-only -q` |
| 全量测试（无插桩） | **3,337 passed / 0 failed / 2 skipped / 150.66s** | `pytest tests/ -q -p no:cacheprovider` |
| 覆盖率 | **89%**（18,797 语句 / 2,027 未覆盖 / 206.06s） | 同上加 `--cov=src --cov=web --cov-report=term` |
| Lint | **0 errors** | `ruff check src web tests --statistics` |
| 编译 | **0 errors** | `python -m compileall -q src web` |
| 增量规模 | **7 提交 / 13 文件 / +4,453 −1,605**（`ab01cc9..6765107`） | `git diff --stat` |
| 线上健康 | **HTTP 200 / 1.06s / `version=6765107`（=HEAD）/ `degraded=false` / data_source=true / ai=true** | `curl /api/health` |
| 线上 AI Provider | `["zhipu_glm","agnes","ly77625"]` | 同上 |

> 线上为已唤醒实例（1.06s），非冷启动；r7 记录的 Render Free 冷唤醒 ~58s 属平台特性，本轮不复测冷启动。

## 2. 前端体量（实测）

| 资产 | 实测 | 备注 |
|---|---|---|
| `web/static/index.html` | **507,061 B / LF 9,370 / 非空行 8,755** | 与 r7 基线**字节完全一致**；r7 的「8,755 行」为非空行口径，本轮同时记录两种口径，避免误判为变更 |
| 内联 `<script>` | **2 块** | 与 r7 持平 |
| 外置 JS | **16 个文件 / 265,905 B** | `web/static/js/` |
| `innerHTML` 出现次数 | **332 处 / 15 文件**（其中 index.html **254** 处） | r7：253（index）→ 254，+1 |
| 页面容器 | **24 个** | 与 r7 持平 |

> 本轮 `ab01cc9..HEAD` **无任何 `web/static/**` 改动**，前端风险面不变；增量集中在后端数据层。

## 3. 覆盖率明细（低洼模块，实测）

| 模块 | 覆盖率 | 语句/未覆盖 | 性质 |
|---|---|---|---|
| `src/core/__init__.py` | **52%** | 25 / 12 | 存量 |
| `src/strategies/a_stock_analyst.py` | **61%** | 230 / 90 | 存量洼地（r7 未覆盖该文件） |
| `src/analysis/market_review.py` | **67%** | 369 / 122 | 存量洼地 |
| `src/strategies/cb_t0_sniper.py` | **72%** | 151 / 42 | 存量 |
| `web/routes/config.py` | **76%** | 193 / 46 | 存量洼地 |
| `web/routes/monitor.py` | **79%** | 193 / 40 | 存量洼地 |
| `src/core/data_source_v2.py` | **82%** | **1,366 / 246** | r7 1,397 语句 → 本轮 1,366（−31） |
| `data_guard/contract.py` | 98% | 60 / 1 | 本轮新增层 |
| `data_guard/router.py` | 97% | 39 / 1 | 本轮新增层 |
| `data_guard/providers/*` | 85%–100% | 903 / 78 | 本轮新增层 |
| **TOTAL** | **89%** | 18,797 / 2,027 | 门禁 78%，过线 |

> r7 的两个策略洼地已收窄：`stock_monitor` 98%、`trading_quant` 99%、`limit_up` 98%。

## 4. 待基线清单（诚实标注）

| 项 | 状态 | 阻塞原因 / 解锁条件 |
|---|---|---|
| 依赖 CVE 扫描 | **待基线** | `python -m pip_audit` 模块不存在；本机 pip 出网受限，未安装成功，不凭记忆断言 CVE |
| Firefox / Safari 兼容性 | **待基线** | 本机仅 Chromium 内核（Edge），不伪造通过 |
| 原创性「非抄袭人工比对」 | 并入阶段 5 | 自动量化不能替代人工举证 |
| 线上写端点真实链路 | 并入阶段 4 | 本地 `ADMIN_TOKEN` 为空（长度 0），需用户提供线上令牌 |

---

> 本基线由 AI 协作生成，关键决策请由工程负责人复核。
