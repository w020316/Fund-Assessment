# 质量基线 · 第六轮（2026-11-17）

**HEAD**：`561583b`（main，工作树干净，与 origin/main 同步）
**性质**：增量轮——基线锚定第五轮（`quality-baseline-2026-11-12-r5.md`，HEAD=`0ae5b7a`）。本轮全部数字为**本机/线上实测**，未实测项显式标「待基线」，**不沿用任何历史值**。
**环境**：`D:/dev-tools/Python312/python.exe`；ruff（随该解释器）；pip-audit 不可用（见 §5，已定位根因）；本机 curl 一律 `--noproxy '*'`。

---

## 1. 核心基线（实测）

| 项 | 实测值 | 命令 |
|---|---|---|
| 测试（无插桩） | **2885 passed / 1 skipped / 0 failed / 1 warning / 86.94s** | `python -m pytest tests/ -q -p no:cacheprovider` |
| 测试（`--cov`） | 2885 passed / 1 skipped / 123.38s | 同上加 `--cov=src --cov=web` |
| **覆盖率** | **85%**（17,959 语句 / 2,639 未覆盖） | `--cov-report=term` |
| 测试增量 | **+154 例**（r5 2731 → 2885） | 对照 r5 基线 |
| Lint | **169 errors**（F401×124 / E402×23 / F841×18 / F811×2 / E731×1 / F403×1；65 可自动修） | `ruff check src web tests --statistics` |
| Lint 增量 | 175 → 169，**净 −6**（增量开发未恶化 lint 面） | 对照 r5 |
| Python 源文件 | 242（tests 123 个文件） | `git ls-files` |
| 增量规模 | **27 提交 / 56 文件 / +7,982 −467 / 17 新增文件**（`0ae5b7a..561583b`） | `git diff --stat` |
| 线上健康 | ✅ **HTTP 200**，`status=ok`、`degraded=false`、`data_source=true`、`ai=true`、**`version=561583b`（=HEAD，指纹一致）** | `curl /api/health` |
| 冷启动耗时 | **73.69 s**（Render Free 休眠唤醒，实测） | `curl -w %{time_total}` |
| 依赖 CVE | **待基线**（根因已定位，见 §5） | pip-audit / OSV 均不可用 |

> 冷启动 73.69 s 属 Render Free 平台特性（r4/r5 各遇一次），**不是代码性能回归**。阶段 3/4 的性能口径拆「冷启动」与「稳态」两档，稳态复测另记。

## 2. 前端体量（实测，含 CRLF）

| 资产 | 字节 | 行数 | 备注 |
|---|---|---|---|
| `web/static/index.html` | **507,698 B** | **9,272** | r5：471,499 B / 8,652 行 → **+36.2 KB / +620 行，反向恶化** |
| ├ 内联 `<script>` | 13 块 / **329,990 B（内容占比 66%）** | — | 归一化内容 498,426 B（与磁盘差 9,272 B = CRLF） |
| └ 内联 `<style>` | 1 块 / **41,594 B（8%）** | — | 无外链 CSS |
| **行尾口径校准** | 磁盘 **507,698 B（CRLF）** vs git blob **498,426 B（LF）** vs r5 LF **462,847 B** | — | 两个口径增长率一致（+36.2 KB / **+35,579 B = +7.7%**）；**后续体积对比统一用 LF 口径**（git blob），避免 CRLF 换算被误读成额外膨胀 |
| `web/static/css/workbench.css` | **1179 行（全新）** | — | 本轮新增 |
| `web/static/js/workbench.js` | **1,025 行（全新）**，含 **26 处 `innerHTML`** | — | 本轮新增，XSS 面重点 |
| `web/static/js/stock-compare.js` | 140 行（全新），0 处 `innerHTML` | — | 合规 |
| `web/static/js/sector-board.js` | 125 行，7 处 `innerHTML` | — | 含改动 |
| `web/static/index.html` 内 `innerHTML` | **253 处** | — | 存量欠账，本轮 +若干 |
| 页面数 | **4**（index / stock-compare / ma-optimizer / factor-research） | — | r5 为 3 |

> **首页瘦身 backlog 结论**：不仅未收口，反而扩大 36 KB。根因是本轮新增功能继续以「内联进 index.html」的方式落地（+726 行），而 `workbench.css/js` 已正确外置——两种模式并存。阶段 5 需给出可量化的外置迁移方案（按上表字节数估算收益）。

## 3. 覆盖率明细（实测，增量模块 + 洼地）

| 模块 | 覆盖率 | 语句/未覆盖 | 性质 |
|---|---|---|---|
| **`src/core/data_source_v2.py`** | **66%** | **1,807 / 620** | ⚠️ **本轮最大改动文件（+361 −172）同时是最大覆盖率洼地**，620 条未覆盖语句 ≈ 全项目未覆盖的 23% |
| `src/core/backtest.py` | 98% | 495 / 11 | 健康 |
| `src/analysis/portfolio_risk.py` | 96% | 114 / 5 | 健康（本轮新增 HHI） |
| `src/analysis/fund_holdings.py` | 97% | 272 / 7 | 健康（本轮改解析） |
| `src/analysis/ddx_streak.py` | 83% | 217 / 36 | 本轮新增，达标 |
| `src/analysis/market_assessment.py` | 85% | 127 / 19 | 达标 |
| `src/core/llm_router.py` | 88% | 487 / 59 | 达标 |
| `src/core/ai_service.py` | 88% | 608 / 76 | 达标 |
| `web/routes/market.py` | 87% | 1,011 / 134 | 达标 |
| `web/routes/strategy.py` | 90% | 393 / 41 | 达标 |
| `web/routes/portfolio.py` | 90% | 740 / 76 | 达标 |
| `web/routes/dashboard.py` | 89% | 331 / 38 | 达标 |
| `web/routes/holdings.py` | 83% | 143 / 25 | 达标 |
| `web/routes/config.py` | 76% | 193 / 46 | 存量洼地 |
| `web/routes/monitor.py` | 79% | 195 / 40 | 存量洼地 |
| `src/strategies/stock_monitor.py` | **50%** | 216 / 107 | **存量洼地（历轮遗留）** |
| `src/strategies/trading_quant.py` | **55%** | 298 / 135 | **存量洼地（历轮遗留）** |
| `src/strategies/a_stock_analyst.py` | 61% | 231 / 90 | 存量洼地 |
| `src/strategies/limit_up.py` | 61% | 194 / 76 | 存量洼地 |
| `src/strategies/cb_t0_sniper.py` | 72% | 152 / 42 | 存量洼地 |
| **TOTAL** | **85%** | 17,959 / 2,639 | 门禁 78%，**过线** |

> **门禁判定**：全局 85% ≥ 78%，CI 门禁通过。但阶段 2 的「核心模块 80%」硬标准下，`data_source_v2.py` 66% **不达标**，且它正是本轮改动最集中的文件 → 列为阶段 2 必办项（补多源回退 down 分支 / None 传播 / 降级路径的测试）。

## 4. 线上部署与鉴权指纹（实测）

| 探针 | 结果 | 结论 |
|---|---|---|
| `GET /api/health` | 200，`version=561583b` | 部署与 HEAD 一致 |
| `degraded` | `false`；`data_source=true`、`ai=true`、`akshare=true` | 无降级 |
| `ai_providers` | `["zhipu_glm","agnes","ly77625"]` | 与本轮 `3f16263`（ly77625 Gemini 协议开关）后配置一致 |
| `cache_stats.llm` | size=0 / maxsize=100 / ttl=300s（实例冷启后无流量） | 符合进程内缓存设计 |
| 鉴权面 | 本轮**新增端点**匿名可达性 → 交由阶段 1-C 逐端点核 | — |

## 5. 依赖 CVE：根因定位（替代历轮「pip 出网被拦」的模糊表述）

两条路径全部实测失败，**均为可复现的硬阻塞，非态度问题**：

1. **`pip-audit` 无法启动**：`ModuleNotFoundError: No module named 'venv'`
   本机 `D:/dev-tools/Python312` 为裁剪发行版（含 `python312._pth`），缺 `venv` 标准库模块；`pip_audit._virtual_env` 在**模块导入期**即引用 `venv`，无任何 CLI 参数可绕过。
2. **OSV 漏洞库不可达**：`POST https://api.osv.dev/v1/query` 返回 `HTTP 000`
   三个出口全试过：直连 / 系统代理 `127.0.0.1:7890`（`ProxyEnable=1`，实测为 Edge 在用）/ `127.0.0.1:65436`。
   故「用 OSV API 手工批量查 pinned 版本」这条替代路线也走不通。

**结论**：维持「待基线」，但不再归因为"pip 出网被拦"。解锁条件明确为二选一：① 在含 `venv` 的标准 CPython 环境执行 `pip-audit -r requirements.txt`；② 恢复 `api.osv.dev` 出网后跑 OSV 批量查询脚本。
**不做的事**：不对 `requirements.txt` 里 20 个 pinned 版本凭记忆断言 CVE 有无——版本号属 2026 时间线，无可靠公共源可核，编造即违规。

## 6. 待基线清单（诚实标注）

| 项 | 状态 | 阻塞原因 / 解锁条件 |
|---|---|---|
| 依赖 CVE 扫描 | **待基线** | §5（缺 venv 模块 + OSV 不可达） |
| Firefox / Safari 兼容性 | **待基线** | 本机仅有 Chromium 内核（Edge）；不伪造结论 |
| 线上冷启动稳态性能（LCP/CWV） | 并入阶段 3/4 | 需先唤醒实例再测，分「冷启动/稳态」两档 |
| 带凭据全链路（回测/网格/因子/扫描） | 并入阶段 4 | 用户本轮决策：**仅匿名面 + 本地受控数据**，该面维持待基线 |
| 依赖包干净环境重建（门禁 G-1） | **未通过** | r3 起遗留：无法用 requirements 重建 pinned 环境 |

---

> 本基线由 AI 协作生成，关键决策请由工程负责人复核。
