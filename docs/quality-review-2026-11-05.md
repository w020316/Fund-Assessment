# 阶段一：全量代码审查与功能完整性排查报告

- 审查日期：2026-11-05
- 基线 HEAD：`82a9d41`（main，工作区干净，与 origin/main 同步）
- 审查性质：只读审查，未修改任何业务代码
- 结论有效期：以 `82a9d41` 为准；代码变动后需重新核对行号

## 一、结论

1. **P0 = 0 条**：未发现可导致资金/账户风险、交易价格错误或线上不可用的缺陷。交易链路对 0 价格、涨停价、超持仓均有硬拒绝。
2. **P1 = 4 条**：①本地所有写端点在默认配置下返回 503，核心录入/交易功能开箱不可用；②基金建议在净值缺失时把盈亏静默算成 0.00 且不标注数据缺失；③后端 32/126 个操作（25.4%）无前端入口，其中包含 README 宣称的回测/验证能力；④依赖声明与本机实测大面积漂移，本地测试全绿不能代表部署环境可用。
3. **P2 = 11 条、P3 = 6 条**：前端存在多处未转义 HTML 注入点；缺失字段被静默转成 0；若干工程一致性问题。
4. **数据真实性结论**：未发现"伪造行情/编造交易记录"的代码路径；降级逻辑以空值/中性值返回，并带 `[降级]` 说明与 `confidence=0`；三问闸门与鉴权均 fail-closed。但存在 2 处"缺失→0"的静默降级会以真实数值形态呈现（见 R-02、R-06）。
5. **阶段一是否具备进入阶段二的条件**：**具备**。4 条 P1 均可在阶段二闭环，无需要推翻架构的阻塞项。

---

## 二、审查范围与方法

### 2.1 范围与文件覆盖

| 范围 | 文件覆盖 | 说明 |
|---|---:|---|
| `src/` | **65/65（100%）** | 全量静态扫描；`data_source_v2.py`、`ai_service.py`、`fund_advisor_v2.py`、`llm_router.py` 逐段精读 |
| `web/` Python | **23/23（100%）** | 16 个路由模块全覆盖；`portfolio.py`、`market.py`、`dashboard.py`、`fund.py` 逐段精读 |
| 前端 | **8/8（100%）** | `index.html` 6790 行 + 5 个 JS + 2 个 CSS，合计 9460 行；XSS/DOM 关键段精读 |
| 部署与工程配置 | 8/8 | `render.yaml`、`Procfile`、`fly.toml`、`vercel.json`、`railway.json`、`start.ps1`、`start.bat`、`launch.py` |
| 文档 | 抽样 6/32 | README、两份旧基线与设计增量文档 |

精确规模（`(Get-Content <file>).Count` 求和，非 `Measure-Object -Line`）：

| 范围 | 文件数 | 行数 |
|---|---:|---:|
| `src/` | 65 | 20,005 |
| `web/`（Python） | 23 | 6,698 |
| `tests/` | 81 | 23,288 |
| 前端（html/css/js） | 8 | 9,460 |

> 更正：阶段零报告中的 `index.html 6303 行 / 前端 8771 行 / src 17558 行` 使用了 `Measure-Object -Line`（会漏计空行）。本报告数字为精确行数，后续以本报告为准。

### 2.2 方法

1. **契约机械比对**：用 TestClient 导出 OpenAPI 126 个操作到 `.tmp/endpoints.txt`，再对 `web/static/**` 全文做路径前缀匹配，得到孤儿/悬空清单。
2. **风险模式扫描**：针对 `random/mock/fake`、裸 `except`、`time.sleep`、`requests.*`、`innerHTML`、`onclick` 拼接、`require_admin`、`X-Forwarded-For`、文件上传后缀/大小做定向检索。
3. **关键路径精读**：数据源降级、基金建议、鉴权、限流、上传、模拟交易、前端渲染。
4. **动态探针**：本地服务启动后实测写端点鉴权行为（仅只读/幂等安全探测，未触发任何资金动作）。
5. **静态分析**：`ruff check`、`compileall`、`pytest`、`coverage`（阶段零已实测，本轮未改代码，结果沿用）。

---

## 三、质量基线（实测）

| 检查项 | 命令 | 结果 | 耗时 |
|---|---|---|---:|
| 用例 | `python -m pytest tests/ -q` | 1905 passed / 0 failed / 1 warning | 99.60 s |
| 覆盖率 | `--cov=src --cov=web` | 13,867 语句 / 3,178 未覆盖 / **77%** | 108.96 s |
| Lint | `python -m ruff check src web tests` | **172 errors**（F401×121、F841×23、E402×20、F541×6、F403×1、F811×1） | — |
| 语法 | `python -m compileall -q src web launch.py` | exit 0 | — |
| 前端 JS 语法 | `node --check` × 5 | 5/5 OK | — |
| 服务启动 | `python launch.py --port 8055` | `/api/health` 200，冷启动 14.64 s | — |
| API 面 | TestClient `/openapi.json` | **117 路径 / 126 操作** | — |

---

## 四、P0 / P1 逐条证据

> 级别定义：**P0** = 可导致数据错误、资金/账户风险或线上不可用；**P1** = 明确功能缺陷或安全弱点，有现实触发路径；**P2** = 体验与可维护性问题；**P3** = 风格与清理建议。

### R-01 [P1] 默认配置下本地全部写端点返回 503，核心功能开箱不可用

- 位置：[auth.py#L48-L53](file:///d:/xm/wz/Fund-Assessment/src/utils/auth.py#L48-L53)、[auth.py#L68-L85](file:///d:/xm/wz/Fund-Assessment/src/utils/auth.py#L68-L85)
- 证据：

```python
env = os.getenv("APP_ENV", "production").strip().lower()   # 默认生产
...
if not expected_token:
    if _is_production(env):
        raise HTTPException(503, "服务端鉴权未配置, 请设置 ADMIN_TOKEN")
```

- 本地 `.env` 实测：16 个变量中 8 个非空，**不含 `APP_ENV`，`ADMIN_TOKEN` 为空**。
- 动态实测：

```text
POST /api/fund/positions/validate -> 503
POST /api/holdings/upload         -> 503
```

- 触发条件：按仓库自带 `.env.example` 配置后直接启动，未额外设置 `APP_ENV=dev` 或 `ADMIN_TOKEN`。
- 影响：持仓录入、基金增删、交易、配置保存、上传全部不可用；阶段四的真实用户链路无法在本地跑通。线上因 fail-closed 反而是安全的。
- 修复建议：`.env.example` 显式增加 `APP_ENV=dev`（并在注释说明生产必须删除/改回）；或 `launch.py` 在 `--dev` 时注入；保持生产 fail-closed 不变。
- 验证方式：`POST /api/fund/positions/validate` 返回 4xx（参数校验）而非 503；`ADMIN_TOKEN` 为空时仍返回 503 的单测保持通过。

### R-02 [P1] 基金建议在净值缺失时把盈亏静默算成 0.00，且不标注数据缺失

- 位置：[fund_advisor_v2.py#L527-L529](file:///d:/xm/wz/Fund-Assessment/src/analysis/fund_advisor_v2.py#L527-L529)、[fund_advisor_v2.py#L556-L557](file:///d:/xm/wz/Fund-Assessment/src/analysis/fund_advisor_v2.py#L556-L557)、[fund_advisor_v2.py#L570-L586](file:///d:/xm/wz/Fund-Assessment/src/analysis/fund_advisor_v2.py#L570-L586)
- 证据：

```python
current_nav = 0.0
if quotes and isinstance(quotes, list) and len(quotes) > 0:
    current_nav = float(quotes[0].get("nav", 0) or 0)
...
pnl_pct = (...) if cost_nav > 0 and current_nav > 0 else 0.0
pnl_amount = (...) if current_nav > 0 and cost_nav > 0 else 0.0
```

返回体（L570-L586）只有 `current_nav / pnl_pct / advice`，**无 `data_quality` / `degraded` / `stale` 字段**。

- 触发条件：基金行情源失败、代码错误或返回空 `quotes`，接口仍 200 并给出 `advice.action`。
- 影响：前端展示 `0.00% / 0.00 元`，与"真实不赚不亏"无法区分；同时仍给出持有/加仓建议，属于金融场景的数据真实性问题。
- 修复建议：`current_nav <= 0` 时返回 `data_quality="unavailable"`，`advice.action="数据不足"`，盈亏字段置 `null` 而非 0；前端显示"—"。补充"行情为空"用例。
- 验证方式：mock `quotes=[]` 调用 `generate_fund_advice_v2`，断言 `data_quality=unavailable` 且 `pnl_pct is None`；前端渲染快照。

### R-03 [P1] 32/126 个后端操作无前端入口，含 README 宣称的回测/验证能力

- 机械比对结果：**126 操作中 32 个（25.4%）未被前端引用**；未发现实际 `api()` 调用指向不存在的端点（唯一悬空引用为 [index.html#L2313](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L2313) 的超时规则 `/api/market/outlook`，该端点不存在且从未被调用）。
- 孤儿清单（按模块）：

| 模块 | 孤儿端点 |
|---|---|
| strategy（3） | `/api/strategy/analyze`、`/api/strategy/backtest`、`/api/strategy/validate` |
| trade（1） | `/api/trade/history` |
| monitor（2） | `/api/monitor/capital_flow`、`/api/monitor/northbound` |
| config（2） | `GET/PUT /api/config/strategies` |
| market（12） | `fund_realtime`、`fund_history`、`sector_flow`、`news`、`heatmap`、`search`、`status`、`stock_detail`、`market_sentiment`、`market_wide_stats`、`capital-flow`、`assessment`、`data-quality/{stock_code}` |
| agent（3） | `/api/agent/multi_analyze`、`/api/agent/portfolio_advice`、`/api/agent/fund_analyze_steps` |
| fund（1） | `/api/fund/realtime` |
| scripts（3） | `/api/scripts/categories`、`/api/scripts/generate`、`/api/scripts/match/stock` |
| skills（1） | `/api/agent/skills/categories` |
| 运维/外部（3） | `/api/portfolio/webhook/scan`（GitHub Actions 调用，属设计内）、`/api/health/llm`、`/api/health/llm/probe` |

- 关键矛盾：[README.md#L18](file:///d:/xm/wz/Fund-Assessment/README.md#L18) 宣称"回测验证 Walk-Forward + 蒙特卡洛"，[README.md#L245](file:///d:/xm/wz/Fund-Assessment/README.md#L245) 文档化 `POST /api/strategy/validate`，但前端对 `backtest`/`validate` **0 次引用**。
- 影响：核心卖点"回测验证"在 UI 上不可达；用户无法使用已实现的策略分析能力。
- 修复建议（二选一）：阶段二接入回测/验证 UI（推荐）；或从 README 移除未接入口的宣称并标注"仅 API"。
- 验证方式：前端全局检索命中；E2E 从策略页发起回测并看到结果。

### R-04 [P1] 依赖声明与本机实测漂移，测试绿 ≠ 部署绿

| 依赖 | 声明 | 实测 |
|---|---|---|
| fastapi | `0.115.12` | **0.136.1** |
| pandas | `2.3.3` | **3.0.3** |
| numpy | `2.5.1` | **2.2.6** |
| httpx | `>=0.27.0` | **0.25.2（违反下限）** |
| uvicorn | `0.34.2` | **0.46.0** |
| starlette | 未声明 | **1.0.0** |

- 位置：[pyproject.toml](file:///d:/xm/wz/Fund-Assessment/pyproject.toml)、[requirements.txt](file:///d:/xm/wz/Fund-Assessment/requirements.txt)
- 影响：1905 全绿仅证明当前解释器组合可用；Render 按 pinned 版本安装，行为差异无法被测试发现（pandas 3.0 与 2.x 在数据类型/缺失值处理上存在差异）。
- 修复建议：`pip freeze` 生成 `constraints.txt` 并在 CI 中同时验证"声明版本"和"生产版本"；升级时用同一套 1905 用例做回归。
- 验证方式：干净虚拟环境按 `requirements.txt` 安装后跑全量测试与启动冒烟。

---

## 五、P2 问题清单

| 编号 | 位置 | 问题 | 证据 | 修复建议 | 验证方式 |
|---|---|---|---|---|---|
| R-05 | [index.html#L3519](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L3519)、[#L3525](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L3525)、[#L3858](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L3858)、[#L5464](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L5464)、[#L5652](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L5652) | 前端 HTML 注入点：LLM 摘要、指数名、策略名、自选名直接插入模板，未走 `escapeHtml` | `${oa.summary}`、`${idx.name}`、`onclick="showToast('策略操作: ${s.name}'...)"` | 统一用 `escapeHtml`；属性值用 `data-*` + 事件委托，禁止把变量拼进 `onclick` | 构造含 `<img onerror>` 的基金名/摘要，断言不执行 |
| R-06 | [data_source_v2.py#L137-L148](file:///d:/xm/wz/Fund-Assessment/src/core/data_source_v2.py#L137-L148)、[market.py#L44](file:///d:/xm/wz/Fund-Assessment/web/routes/market.py#L44) | 缺失字段被 `_safe_float` 默认成 `0.0`，行情以 0.00 呈现；`quality_score` 默认 100/80，弱化了降级信号 | `_safe_float` 返回 `default`；`quality_score=... if ... else (100.0 if cached else 80.0)` | 缺失字段返回 `None` 并由前端显示"—"；质量分低于阈值时前端显示降级标识 | mock 缺失字段，断言 JSON 为 `null` 且前端显示"—" |
| R-07 | [portfolio.py#L891-L901](file:///d:/xm/wz/Fund-Assessment/web/routes/portfolio.py#L891-L901) | `ScanRequest.force` 参数被赋值后从未使用，`force=true` 无法跳过节流 | `body = req or ScanRequest()` 之后无 `body.force` 引用 | 在节流判断处读取 `body.force` 或删除该字段与文档 | 单测：`force=true` 时跳过 throttle |
| R-08 | [index.html](file:///d:/xm/wz/Fund-Assessment/web/static/index.html)（6790 行） | 前端为单文件巨石，无构建、无 JS lint、无前端测试，20 个页面手工维护 | 无 `package.json`；`node --check` 仅覆盖 5 个外置 JS，内联脚本未覆盖 | 至少引入 eslint + 按页面拆分 JS；建立页面清单快照测试 | 引入后 lint 0 error、页面冒烟通过 |
| R-09 | 仓库根 | 无 CI：lint/测试/覆盖率全靠人工 | 无 `.github/` workflow | 增加 GitHub Actions：ruff + pytest + 覆盖率门槛 | PR 触发即运行 |
| R-10 | [start.ps1#L58-L59](file:///d:/xm/wz/Fund-Assessment/start.ps1#L58-L59)、[start.bat#L2](file:///d:/xm/wz/Fund-Assessment/start.bat#L2) | 启动脚本与运行约束不一致：`--prod` 使用 4 workers（与进程内状态/单 worker 约束冲突）；`start.bat` 未设 PYTHONPATH 且品牌文案为 OpenClaw；`.venv` 指向不存在的 Python310 | `--workers 4`；`echo Starting OpenClaw ...`；`.venv` 执行即退出 | 统一为 `launch.py` 单进程；生产用 Procfile；清理 OpenClaw 文案；重建 venv 或删除损坏 venv | `start.ps1` 启动后 `/api/health` 200 且进程数为 1 |
| R-11 | [requirements-minimal.txt](file:///d:/xm/wz/Fund-Assessment/requirements-minimal.txt) | 缺少应用实际依赖 `slowapi/openpyxl/python-multipart/tdxpy`（全文 0 命中） | `Select-String` 无匹配 | 补齐或删除该文件，避免误导部署 | 用 minimal 安装后 `import web.api` 成功 |
| R-12 | [README.md#L21-L72](file:///d:/xm/wz/Fund-Assessment/README.md#L21-L72) | 文档漂移：13 页面 vs 20 容器；12 路由 vs 16 模块；77 测试文件 vs 79；`pandas-ta/stockstats` 未在依赖；Docker 未提供 Dockerfile；"6 个写端点" vs 实际 30+ | 见对应行 | 按实测值修订；旧基线文档标注为历史版本 | 文档核对清单归零 |
| R-13 | [review.py#L105](file:///d:/xm/wz/Fund-Assessment/web/routes/review.py#L105)、[strategy.py#L394](file:///d:/xm/wz/Fund-Assessment/web/routes/strategy.py#L394) | 计算密集型 POST（三问闸门、回测验证）无 `require_admin`，仅靠 20/min 限流 | 装饰器无 `dependencies=[Depends(require_admin)]` | 加鉴权或改为只读 GET；保留限流 | 未带 token 返回 401 |
| R-14 | [vercel.json](file:///d:/xm/wz/Fund-Assessment/vercel.json) | 有状态应用（SQLite/APScheduler/磁盘缓存）部署到 serverless，运行语义不成立 | `@vercel/python` + `maxLambdaSize 50mb` | 从 README 移除 Vercel 一键部署，或改造为纯只读 API | 部署冒烟：写端点与定时任务 |
| R-15 | [start.ps1#L59](file:///d:/xm/wz/Fund-Assessment/start.ps1#L59) | 4 workers 会破坏进程内状态（限流、缓存、调度、持仓锁）一致性 | `--workers 4` | 与 Procfile 保持一致，固定 1 worker | 压测并发写入一致性 |

---

## 六、P3 问题清单

| 编号 | 位置 | 问题 | 证据 | 建议 |
|---|---|---|---|---|
| R-16 | `src/`、`web/`、`tests/` | ruff 172 条：F401×121、F841×23、E402×20、F541×6、F403×1、F811×1 | `ruff check --statistics` | F401 中 55 条来自 `src/core/__init__.py`(39) 与 `src/agents/__init__.py`(16) 的公共 re-export。**禁止直接 `ruff --fix`**（会删除导出破坏 `from src.core import X`）；应先补 `__all__` 或 per-file-ignores，再修剩余 66 条。 |
| R-17 | [backtest.py#L221-L222](file:///d:/xm/wz/Fund-Assessment/src/core/backtest.py#L221-L222)、[capital_flow.py#L30](file:///d:/xm/wz/Fund-Assessment/src/analysis/capital_flow.py#L30)、[news.py#L115](file:///d:/xm/wz/Fund-Assessment/src/analysis/news.py#L115)、[ai_service.py#L160](file:///d:/xm/wz/Fund-Assessment/src/core/ai_service.py#L160) | 赋值后未使用的变量（F841），多数为残留代码，非逻辑错误 | `ruff --select F841` | 清理；`data_source_v2.py:990-991/1081` 的 `volume/amount/prev_close` 需确认是否漏映射 |
| R-18 | [start.bat#L2](file:///d:/xm/wz/Fund-Assessment/start.bat#L2)、[control.ps1](file:///d:/xm/wz/Fund-Assessment/control.ps1#L1-L2) | 品牌残留 `OpenClaw`，与 QuantFlow Pro 命名约束冲突 | 控制台输出与脚本头部注释 | 全部替换为 QuantFlow Pro |
| R-19 | [fly.toml#L4-L6](file:///d:/xm/wz/Fund-Assessment/fly.toml#L4-L6) | Fly 配置引用不存在的 Dockerfile，部署必失败 | 文件自述注释 | 删除该部署选项或补 Dockerfile |
| R-20 | [portfolio.py#L912](file:///d:/xm/wz/Fund-Assessment/web/routes/portfolio.py#L912) 等 | 魔法值：`2.0` 阈值、`0.015/0.03` 止盈止损线散落 | 多处字面量 | 收敛到 `config/settings.yaml` 或模块常量 |
| R-21 | [api.py#L137-L155](file:///d:/xm/wz/Fund-Assessment/web/api.py#L137-L155) | CSP 仍含 `'unsafe-inline'` 与 `unpkg.com`，对外部脚本无 SRI | 响应头实测 | 逐步外移内联脚本；为 `lightweight-charts` 加 `integrity` 或本地 vendored |

---

## 七、数据真实性专项结论

### 7.1 结论：无系统性伪造，存在 2 处静默归零

| 检查点 | 结论 | 证据 |
|---|---|---|
| LLM 解析失败 | **fail-closed 且标注** | [ai_service.py#L796-L817](file:///d:/xm/wz/Fund-Assessment/src/core/ai_service.py#L796-L817)：降级结果 `action=HOLD`、`confidence=0.0`、reasoning 以 `[降级]` 开头，不透传伪造价格 |
| 三问闸门 | **fail-closed** | [three_question_gate.py#L73-L145](file:///d:/xm/wz/Fund-Assessment/src/strategies/three_question_gate.py#L73-L145)：热度/龙头/竞价数据缺失时 `passed=False` |
| 数据源失败 | 返回 `[]`/`None`，不返回假行情 | `data_source_v2.py` 全文 `return [] / return None` 共 60 处；`_kline_em_fallback` 失败返回 `[]` |
| 智能体降级 | 中性 50 分 + `confidence=0`，文案标注"降级" | [fundamental_agent.py#L24-L40](file:///d:/xm/wz/Fund-Assessment/src/agents/fundamental_agent.py#L24-L40) 等 `_mock_analysis` |
| 基金盈亏 | **有缺陷**：净值缺失→`0.00`，无标记 | 见 R-02 |
| 行情字段 | **有缺陷**：单字段缺失→`0.0`，质量分默认 80/100 | 见 R-06 |
| 鉴权 | **fail-closed** | 生产未配 token → 503；scan 端点未配 key → 503 |

**判定**：项目没有"随机数/写死价格"式造假；风险集中在"缺失值被当作合法数值"的呈现层，需在阶段二按 R-02/R-06 修复。

---

## 八、契约一致性

- API 面：**117 路径 / 126 操作**（TestClient `/openapi.json`）。
- 孤儿端点：**32 个（25.4%）**，清单见 R-03。
- 悬空调用：**0 个**；唯一悬空引用是 `index.html:2313` 的超时规则 `/api/market/outlook`，该端点不存在且从未被调用（死配置）。
- 额外发现：`/api/scripts/ai-generate` 仅出现在超时规则中，前端无实际调用，与孤儿等价。
- 修复方向：阶段二建立"端点 → 页面"映射表，消灭死配置与无入口功能。

---

## 九、安全专项

### 9.1 写端点鉴权覆盖

- 所有真正改变状态的写端点（trade、holdings、fund positions、portfolio、monitor watchlist、config、review journal、strategy scan、scripts generate）均带 `Depends(require_admin)`。
- 例外：`/api/review/three_question`、`/api/strategy/validate` 为只读计算但无鉴权（R-13）；`/api/portfolio/webhook/scan` 使用 `X-Api-Key` + `secrets.compare_digest`，未配置即 503（[portfolio.py#L774-L795](file:///d:/xm/wz/Fund-Assessment/web/routes/portfolio.py#L774-L795)）。
- `ADMIN_TOKEN` 为空时：生产 `APP_ENV` 默认 `production` → **503 fail-closed**，线上不可被绕过；本地因此不可用（R-01）。

### 9.2 其他

| 项 | 结论 | 证据 |
|---|---|---|
| SSRF | 未发现用户可控 URL 注入请求；Webhook 目标来自服务端配置而非请求体 | `webhook_push.get_channel` 读取配置；路由仅接受 `channel/enabled/threshold` |
| 文件上传 | 大小、扩展名、xlsx 解压体积均有校验，由管理员触发 | [holdings.py#L189-L245](file:///d:/xm/wz/Fund-Assessment/web/routes/holdings.py#L189-L245) |
| 限流绕过 | 取真实 IP，`X-Forwarded-For` 仅取右侧可信段；LLM 3/min、写 10/min、GET 60/min | [rate_limiter.py#L28-L64](file:///d:/xm/wz/Fund-Assessment/web/rate_limiter.py#L28-L64) |
| CORS | 禁止 `*` 与 credentials 同用 | [api.py#L109-L135](file:///d:/xm/wz/Fund-Assessment/web/api.py#L109-L135) |
| XSS | 部分转义，至少 6 处未转义（R-05） | `escapeHtml` 存在于 [index.html#L2255](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L2255)，但未全覆盖 |
| 敏感信息回显 | 配置接口对密钥做掩码；健康接口仅返回布尔 | [config.py](file:///d:/xm/wz/Fund-Assessment/web/routes/config.py) |
| 命令/路径注入 | 未发现 `eval/exec/os.system`；上传内容在内存解析 | 定向检索 |

---

## 十、功能缺口清单（对标公开功能维度）

> 对标维度参考东方财富、雪球、同花顺 iFinD、Yahoo Finance、Wind 移动端的公开功能类别；只比较"能力是否存在/是否有入口"，不复制具体实现。

| 编号 | 缺口 | 现状 | 优先级 | 用户价值 | 成本（轮次/小时） | 落地建议 |
|---|---|---|---|---|---|---|
| G-01 | 回测/验证 UI | 后端 `backtest.py` + `/api/strategy/backtest|validate` 已实现，前端 0 入口 | **P1** | 高 | 2–3 轮 / 6–10 h | 策略页增加参数表单 + 结果表（收益/回撤/Calmar/胜率） |
| G-02 | 策略分析与校验入口 | `/api/strategy/analyze` 无入口 | **P1** | 中高 | 1–2 轮 / 4–6 h | 与 G-01 合并为一个"策略实验室"页 |
| G-03 | 多智能体/组合建议入口 | `/api/agent/multi_analyze`、`portfolio_advice` 无入口 | P2 | 中 | 1–2 轮 / 4–6 h | AI 页增加"多标的对比/组合诊断" |
| G-04 | 市场宽度/情绪/热力图/资金流 | 后端 12 个 market 端点无入口 | P2 | 中 | 2 轮 / 6–8 h | 行情页增加二级 Tab，复用现有 endpoint |
| G-05 | 财报三表/公告正文 | 仅有 `research_reports/notice` 列表 | P2 | 高 | 3 轮 / 8–12 h | 新增数据源与个股详情页（需评估数据授权） |
| G-06 | 基金筛选器/经理/费率/规模 | 仅有单基金建议与持仓分析 | P2 | 高 | 3 轮 / 8–12 h | 新增筛选页，先只读展示 |
| G-07 | 自选分组与预警规则 UI | watchlist 支持阈值，但无分组/规则编辑 | P2 | 中 | 1–2 轮 / 3–5 h | 监控页增加规则编辑器 |
| G-08 | 模拟盘挂单/条件单/撤单 | 仅市价模拟；撤单能力已下线 | P2 | 中 | 2–3 轮 / 6–8 h | 增加 pending order 状态机与撤单 |
| G-09 | 定投/再平衡计划 | 仅有一次性再平衡预览 | P3 | 中 | 2 轮 / 4–6 h | 计划表 + 到期提醒 |
| G-10 | 报表导出（CSV/PDF） | 仅持仓 JSON 导出 | P3 | 中低 | 1 轮 / 2–4 h | 复用 openpyxl 生成交易/持仓报表 |
| G-11 | 实时推送（WebSocket） | 轮询 + 60s 缓存 | P3 | 低 | 3 轮+ / 大 | 暂缓，免费实例不适合长连接 |
| G-12 | 多用户/多账户 | 单 `ADMIN_TOKEN` | P3 | 低 | 大 | 明确为单用户产品，不立项 |

**半成品/边界未覆盖**：通用 Webhook 渠道仅预留；预警依赖外部 GitHub Actions cron，无内置调度；`/api/health/llm` 无 UI；基金累计净值 `acc_nav` 未参与计算（待确认）。

---

## 十一、待确认 / 待基线

| 编号 | 事项 | 原因 | 取证方式 |
|---|---|---|---|
| U-01 | 线上时间戳是否偏移 8 小时 | `datetime.now()`（[market.py#L46](file:///d:/xm/wz/Fund-Assessment/web/routes/market.py#L46)）在 Render 默认 UTC 容器中返回 UTC；render.yaml 未设 `TZ`。在线取证因实例冷启动超时失败 | 线上 `GET /api/market/index_realtime` 比对 `_meta.timestamp` 与北京时间 |
| U-02 | SQL 是否全部参数化 | 未逐条审查 SQLite 调用 | 检索 `execute(f"...")` 拼接 |
| U-03 | `acc_nav` 未使用是否影响收益口径 | [data_source_v2.py#L1618](file:///d:/xm/wz/Fund-Assessment/src/core/data_source_v2.py#L1618) 赋值后未使用 | 基金净值口径单测 |
| U-04 | `nav_history` 是否升序 | MA/信号计算依赖排序 | 数据源契约测试 |
| U-05 | `prev_close/volume/amount` 未使用是否漏映射 | `data_source_v2.py:990-991/1081` | 对照行情字段断言 |
| U-06 | 前端是否消费 `quality_score` | 未确认渲染位置 | 前端检索 + 页面实测 |
| U-07 | 生产 `ADMIN_TOKEN` 是否已配置 | 不能对线上交易端点做探测 | Render Dashboard 人工确认 |
| U-08 | 静态类型检查 | mypy/pyright 未安装 | 安装后跑 `src web` |
| U-09 | 依赖 CVE | pip-audit 未安装、pip 出网受限 | 恢复出网后扫描 |
| U-10 | Firefox/WebKit、真机、压测、Lighthouse | 工具/设备缺失 | 阶段三/四补测 |

---

## 十二、文档一致性处置建议

| 文档 | 偏差 | 建议 |
|---|---|---|
| [README.md#L28](file:///d:/xm/wz/Fund-Assessment/README.md#L28) | "13 页面" vs 实测 20 个页面容器 | 改为 20（列出 5 主 Tab + 15 二级页） |
| [README.md#L31](file:///d:/xm/wz/Fund-Assessment/README.md#L31) | "12 大路由模块" vs 16 | 改为 16 并补全模块名 |
| [README.md#L62](file:///d:/xm/wz/Fund-Assessment/README.md#L62) | 声明 `pandas-ta + stockstats`，两者均不在依赖且未使用 | 删除，改为"纯 pandas/numpy 实现" |
| [README.md#L72](file:///d:/xm/wz/Fund-Assessment/README.md#L72)、[#L119](file:///d:/xm/wz/Fund-Assessment/README.md#L119) | "77 文件" vs 79 个测试文件（含 conftest 共 81 个 .py） | 改为 79 |
| [README.md#L21](file:///d:/xm/wz/Fund-Assessment/README.md#L21) | "6 个写/交易端点保护" vs 实际 30+ | 改为"全部写端点" |
| [README.md#L22](file:///d:/xm/wz/Fund-Assessment/README.md#L22)、[#L53](file:///d:/xm/wz/Fund-Assessment/README.md#L53) | 宣称 Docker 一键部署，仓库无 Dockerfile | 删除 Docker 或补 Dockerfile |
| [quality-baseline-2026-10-27.md](file:///d:/xm/wz/Fund-Assessment/docs/quality-baseline-2026-10-27.md) | 基线 HEAD `3943752`，落后当前 55 个提交 | 文首标注"历史基线"；README 增加"最新基线/审查"链接指向本报告 |

---

## 十三、阶段小结

### 13.1 覆盖与产出

- 文件覆盖：`src/` 65/65（100%）、`web/` 23/23（路由 16/16）、前端 8/8、部署配置 8/8。
- 问题总数：**21 条**（P0 0、P1 4、P2 11、P3 6）+ 待确认 10 + 功能缺口 12。
- 未改动任何业务代码；未触碰 AI 模型配置；本地服务探测后已终止进程。

### 13.2 阶段二建议优先级（含估算）

| 顺序 | 事项 | 对应编号 | 估算 |
|---|---|---|---|
| 1 | 本地鉴权开关（`APP_ENV=dev`），恢复写功能可测 | R-01 | 0.5 轮 / 1–2 h |
| 2 | 基金建议数据缺失标记 + 前端"—" | R-02 | 1 轮 / 2–3 h |
| 3 | 缺失字段 `null` 化与降级标识 | R-06 | 1 轮 / 2–4 h |
| 4 | 前端统一转义与事件委托 | R-05 | 1 轮 / 2–4 h |
| 5 | 回测/验证入口（G-01/G-02）或文档降级 | R-03 | 2–3 轮 / 6–10 h |
| 6 | 依赖锁定 + 最小 CI | R-04/R-09 | 1 轮 / 2–4 h |
| 7 | 启动脚本/venv/requirements-minimal 归正 | R-10/R-11 | 0.5 轮 / 1–2 h |
| 8 | README 与旧基线处置 | R-12 | 0.5 轮 / 1 h |
| 9 | `force` 参数、计算端点鉴权、vercel 说明 | R-07/R-13/R-14 | 0.5 轮 / 1–2 h |
| 10 | ruff 清理（先 `__init__` 豁免，再 66 条） | R-16 | 1 轮 / 2–3 h |

合计约 **9–11 轮 / 20–35 h**。

### 13.3 进入阶段二判定

**可以进入。** 无 P0；P1 均为可局部修复的功能/工程问题，不涉及架构重构。建议阶段二先做第 1–4 项（半天内可完成并回归），再决定回测 UI 与文档降级的取舍。

---

## 附录 A：本轮实测命令

```powershell
# 契约
python -c "import sys; sys.path.insert(0,'.'); from fastapi.testclient import TestClient; from web.api import app; ..."

# 孤儿端点机械比对（临时脚本，内存执行，未落盘）
python -c "import glob; eps=[...]; txt=''.join(...); orph=[e for e in eps if e.split('{')[0] not in txt]"

# 静态分析
python -m pytest tests/ -q
python -m pytest tests/ -q --cov=src --cov=web --cov-report=term
python -m ruff check src web tests --statistics
python -m ruff check src web tests --select F841,F811,F403 --output-format concise
python -m compileall -q src web launch.py

# 动态探针（仅鉴权行为，无资金动作）
POST /api/fund/positions/validate -> 503
POST /api/holdings/upload         -> 503

# 进程清理
Stop-Process -Id <launch.py 进程>；端口 8055/8056/8057/8058 已释放
```

## 附录 B：关键文件索引

- 鉴权：[src/utils/auth.py](file:///d:/xm/wz/Fund-Assessment/src/utils/auth.py)
- 限流：[web/rate_limiter.py](file:///d:/xm/wz/Fund-Assessment/web/rate_limiter.py)
- 数据源：[src/core/data_source_v2.py](file:///d:/xm/wz/Fund-Assessment/src/core/data_source_v2.py)
- 基金建议：[src/analysis/fund_advisor_v2.py](file:///d:/xm/wz/Fund-Assessment/src/analysis/fund_advisor_v2.py)
- 三问闸门：[src/strategies/three_question_gate.py](file:///d:/xm/wz/Fund-Assessment/src/strategies/three_question_gate.py)
- 组合/Webhook：[web/routes/portfolio.py](file:///d:/xm/wz/Fund-Assessment/web/routes/portfolio.py)
- 行情：[web/routes/market.py](file:///d:/xm/wz/Fund-Assessment/web/routes/market.py)
- 前端：[web/static/index.html](file:///d:/xm/wz/Fund-Assessment/web/static/index.html)
- 部署：[render.yaml](file:///d:/xm/wz/Fund-Assessment/render.yaml)、[Procfile](file:///d:/xm/wz/Fund-Assessment/Procfile)、[launch.py](file:///d:/xm/wz/Fund-Assessment/launch.py)
