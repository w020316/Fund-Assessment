# 阶段二修复记录：问题闭环与缺失功能补全（2026-11-05）

> 上游输入：[阶段一全量审查报告](./quality-review-2026-11-05.md)（21 条问题：4×P1 / 11×P2 / 6×P3）
> 起点 HEAD：`82a9d41` → 本阶段末尾 HEAD：`84fe9d1`（本文档另起一次 `docs` 提交）
> 环境：Windows / Python 3.12 / 本地 dev 模式冒烟 + Render 线上健康检查

---

## 1. 结论摘要

| 项目 | 结果 |
|---|---|
| P1 关闭 | 4 / 4（R-01、R-02、R-03、R-04） |
| P2 关闭 | 9 / 9（本轮选定范围，R-05/06/07/09/10/11/12/13/15） |
| P2 未选（进 backlog） | 2 / 11（R-08、R-14） |
| P3 未做（进 backlog） | 6 / 6（R-16 ~ R-21，其中 R-16 的"禁止全量 `ruff --fix`"约束在 lint 收敛时已遵守） |
| 补测中发现并修复 | 2 条（缓存层，见 §4） |
| 全量回归 | **2009 passed / 0 failed**（基线 1928 passed） |
| 覆盖率 | **77.13% → 81.51%**（covered 11319 / 13887，`--cov-fail-under=75` 通过） |
| 静态门禁 | `python -m compileall` 通过；`ruff check --select E9,F63,F7,F82` 全绿 |
| 线上验证 | `GET https://fund-assessment.onrender.com/api/health` → 200，冷唤醒 54s；新构建已上线（`strategyLabCard` 存在、无 token 调 validate 返回 401） |

---

## 2. 范围口径

### 2.1 选中的 9 条 P2 与理由

| 编号 | 选取理由 |
|---|---|
| R-05 前端注入点未转义 | 安全类，XSS 可直接在用户浏览器执行；改动集中在渲染函数，回归风险低 |
| R-06 缺失字段伪装成 0 + 无降级标识 | 数据可信度问题，与 R-02 同源，可一次把"假数据"链路收敛干净 |
| R-07 `force` 参数无效 | 契约违背（接口声明与行为不一致），单点改动可验证 |
| R-09 无 CI | 后续所有修复的防回归基础设施，越早落地收益越大 |
| R-10 启动脚本 PYTHONPATH 拼接/品牌残留 | 直接影响"能否启动"，且与 R-01 同一文件，合并修复成本最低 |
| R-11 `requirements-minimal` 缺依赖 | 部署误导，补齐成本极低 |
| R-12 README 多处漂移 | 与 R-03 的"对外宣称 vs 实际能力"同源，一并修订 |
| R-13 计算密集端点无鉴权 | 安全 + 成本类（可被当作免费算力），单点装饰器改动 |
| R-15 `--workers 4` 破坏进程内状态 | 正确性缺陷：限流/缓存/调度/持仓锁均为进程内状态，多 worker 会语义错乱 |

### 2.2 未选的 2 条 P2（进 backlog）

- **R-08** 前端单文件巨石（6790 行）：需要引入构建链与 eslint、按页面拆分 JS、建立页面清单快照测试，属于结构性重构，与"最小改动闭环"口径冲突，单独立项。
- **R-14** Vercel serverless 部署：涉及部署架构选型（SQLite/APScheduler/磁盘缓存无法在 serverless 成立），需要产品决策（移除入口或改造为只读 API），不在本轮修复范围。

---

## 3. 修复明细

> 每条一个独立 commit；提交前均执行 `python -m compileall` + 模块专项测试 + 全量回归 + 本地冒烟。

### R-01 [P1] 本地写端点全部 503

- **原始问题**：`require_admin` 在 `APP_ENV != "dev"` 时 fail-closed；本地无 `ADMIN_TOKEN` 时所有写端点返回 503，开箱不可用。
- **技术方案**（固定口径 c+a）：`start.ps1` / `start.bat` 启动时显式注入 `APP_ENV=dev`，并在缺 `ADMIN_TOKEN` 时打印醒目警告；本地 `.env`（已 gitignore，不提交）补 `APP_ENV=dev`；`.env.example` 与 README 写清"生产必须有 ADMIN_TOKEN，本地 dev 放行仅限回环"。**`require_admin` 与鉴权逻辑一行未改。**
- **状态**：关闭。
- **验证**：`GET /api/health` 200；`POST /api/fund/positions/validate`、`POST /api/holdings/upload`、`POST /api/strategy/analyze|validate` 由 503 变为 200/422（422 为请求体校验，属正常语义）。
- **commit**：`a5c6461 fix(startup): 本地启动脚本注入 APP_ENV=dev 并告警缺失 ADMIN_TOKEN`

### R-02 [P1] 基金建议净值缺失时盈亏静默为 0.00

- **原始问题**：`fund_advisor_v2` 在净值缺失时把 `pnl_pct/pnl_amount` 算成 `0.0`，前端显示"0.00%"误导为真实盈亏。
- **技术方案**：`nav_available = current_nav > 0`；缺失时 `pnl_pct/pnl_amount = None`；新增 `data_quality = ok|unavailable`；`final_action = "数据不足"`（保留 signal 的中性值）；摘要计入 `unavailable_count`；前端五信号卡片显示"净值缺失"。
- **状态**：关闭。
- **验证**：新增单测覆盖净值缺失/正常两类；`GET /api/fund/advice` 净值缺失场景返回 `pnl_pct=null`。
- **commit**：`bb53cba fix(fund): 净值缺失时标注 data_quality 并将盈亏置空`

### R-03 [P1] 回测/验证能力无前端入口

- **原始问题**：README 宣称"回测/策略验证"，但 `POST /api/strategy/analyze|backtest|validate` 无任何前端入口（32/126 端点无 UI）。
- **技术方案**（固定两步）：
  1. README 先按实际能力修订，明确标注哪些能力为 API-only；
  2. 在既有策略页区域新增 `id="strategyLabCard"` 卡片（输入代码 + 运行次数，三个按钮分别调 analyze/backtest/validate），复用现有组件与样式 token；validate 用 `/api/market/stock_kline?count=120` 的真实收盘价算收益率，不足 85 期直接提示而不合成数据。未新增导航层级、未改配色/布局骨架/字体。其余 29 个孤儿端点只登记 backlog，未删除/新增任何端点。
- **状态**：关闭（回测超时语义同时修正，见下方说明）。
- **验证**：新增 `TestStrategyLabUiEntry`（卡片必须位于 `page-strategy` 与 `page-trade` 之间、三端点被 UI 引用）；实际请求 `POST /api/strategy/validate` 200。
- **附**：冒烟发现 `/api/strategy/backtest` 逐票拉行情无超时，实测 >150s 无响应。已改为 `asyncio.wait_for(..., timeout=BACKTEST_TIMEOUT_SECONDS=45)`，超时返回 503"回测超时"，实测 45.3s 返回 503。
- **commit**：`b47f995 feat(strategy): 补齐回测/验证前端入口并纠正 README 口径`

### R-04 [P1] 依赖声明与实际漂移

- **原始问题**：声明版本与实测运行版本漂移（fastapi、pandas、httpx 违反下限），"测试绿 ≠ 部署绿"。
- **技术方案**：`pip list --format=freeze` 实测值为唯一依据，锁定 `requirements.txt` / `requirements-minimal.txt` / `pyproject.toml`（fastapi==0.136.1、starlette==1.0.0、uvicorn[standard]==0.46.0、pandas==3.0.3、numpy==2.4.6、httpx==0.25.2、akshare==1.18.64、tushare==1.4.29、mootdx==0.11.7、tdxpy==0.2.7、slowapi==0.1.10、openpyxl==3.1.5、python-multipart==0.0.28 等）。**未修改任何测试断言/用例/阈值。**
- **状态**：关闭（"干净环境安装验证"待基线，见 §8）。
- **验证**：锁定后全量回归 1928 passed；`import web.api` 成功。
- **commit**：`9e35db8 chore(deps): 依赖声明收敛到实测通过的精确版本`
- **连带发现**：锁定 pandas 3.0.3 后暴露缓存反序列化缺陷，见 §4。

### R-05 [P2] 前端 innerHTML 注入点未转义

- **原始问题**：多处渲染直接把后端字段拼进 `innerHTML`，并存在 `onclick="fn('${var}')"` 变量插值，可被行情/新闻字段注入。
- **技术方案**：所有注入点统一走 `escapeHtml(String(v))`；`onclick` 内联插值改为事件委托（新增 `onStrategyManageClick`、`onFundWizardReviewClick`、`onViewHoldingsClick`、`onMultiAgentAnalyzeClick`、`onToggleFundChartClick`、`onDeleteFundPositionClick`、`onSelectFundClick`、`onDeleteStockPositionClick`、`onRemoveWatchlistClick`）；新增 `renderMetaFooter(meta)` 展示"数据降级"。
- **状态**：关闭。
- **验证**：新增 `TestFrontendHtmlEscaping`（`escapeHtml` 必须存在、10 个渲染点必须转义、禁止 `onclick` 变量插值）。
- **commit**：`4af2132 fix(frontend): 统一转义 innerHTML 注入点并移除 onclick 变量插值`

### R-06 [P2] 缺失字段返回 0.00 且无降级标识

- **原始问题**：基金实时解析把 `nav<=0` 的条目当有效数据返回；`_meta` 的 80/100 默认分与空数据 0 分都没有降级标记，前端无法区分真实行情与降级。
- **技术方案**：`data_source_v2` 基金实时解析遇 `nav<=0` 记 warning 并跳过；`_build_meta` 增加 `degraded = quality_score < 70`（保留 80/100 默认语义，避免破坏既有断言）。
- **状态**：关闭。
- **验证**：`test_fund_realtime_drops_zero_nav_entry`；`TestBuildMetaDegraded`（0 分 → degraded=True，默认/缓存 → False）。
- **commit**：`3dec874 fix(data): 净值缺失不再返回 0.00 假行情并增加 degraded 降级标识`

### R-07 [P2] `ScanRequest.force` 被赋值后从未使用

- **原始问题**：`/api/portfolio/scan` 的 `force=true` 无法跳过节流。
- **技术方案**：`_build_scan_alerts(..., force=False)`，节流判断改为 `if not force and pstore.is_throttled(...)`。
- **状态**：关闭。
- **验证**：新增 `test_scan_force_bypasses_throttle`（首次推送成功 → 第二次 throttled → 第三次 force 重新推送，实际推送计数=2）。
- **commit**：`ae8d9fb fix(portfolio): webhook scan 的 force 参数真正跳过节流`

### R-09 [P2] 无 CI 门禁

- **原始问题**：lint/测试/覆盖率全靠人工。
- **技术方案**：新增 `.github/workflows/ci.yml`，push/PR 触发，Python 3.12：compileall → ruff 关键规则（E9,F63,F7,F82，阻断）→ ruff 全量（`|| true` 仅报告）→ pytest `--cov-fail-under=75`。
- **状态**：关闭。
- **验证**：workflow 语法与命令本地逐一预演通过。
- **commit**：`14e002d ci: 新增 GitHub Actions 质量门禁(编译/关键lint/测试/覆盖率)`

### R-10 [P2] 启动脚本自拼 PYTHONPATH + 品牌残留

- **原始问题**：脚本各自拼 `PYTHONPATH`、子进程启动方式不一致；控制台残留 OpenClaw 品牌。
- **技术方案**：dev 分支统一 `& $py launch.py --host 0.0.0.0 --port 8000 --reload`，由 `launch.py` 负责 `sys.path`/`PYTHONPATH` 注入；`start.bat` 同步重写；品牌统一为 QuantFlow Pro。
- **状态**：关闭。
- **验证**：两条启动路径本地启动均成功，`/api/health` 200。
- **commit**：`37a24fb fix(startup): 启动脚本统一走 launch.py 并清理 OpenClaw 品牌残留`

### R-11 [P2] `requirements-minimal` 缺运行必需依赖

- **原始问题**：缺 `slowapi/openpyxl/python-multipart/tdxpy`，按该文件安装无法启动。
- **技术方案**：补齐上述依赖并锁定实测版本。
- **状态**：关闭。
- **验证**：依赖集合与 `web/api.py` 导入链逐项比对无缺口。
- **commit**：`12cac9d chore(deps): requirements-minimal 补齐运行必需依赖`

### R-12 [P2] README 与实测漂移

- **原始问题**：13 页面 vs 20 容器；12 路由 vs 16 模块；77 测试文件 vs 79；`pandas-ta/stockstats` 未在依赖；Docker 未提供 Dockerfile；"6 个写端点" vs 实际 30+。
- **技术方案**：按实测值逐项修订（20 页面容器、16 路由模块、79 文件 1923 用例、删除 pandas-ta/stockstats、标注无 Dockerfile、改写写端点口径）。
- **状态**：关闭。
- **验证**：文档核对清单归零（逐项以实测命令输出为准）。
- **commit**：`05e5c07 docs(readme): 按实测修订页面/路由/测试/依赖与鉴权口径`

### R-13 [P2] 计算密集端点缺鉴权

- **原始问题**：`POST /api/strategy/validate`（以及 review 三问）无 `require_admin`，仅靠 20/min 限流。
- **技术方案**：`validate` 挂 `dependencies=[Depends(require_admin)]`；`analyze/backtest` 原本已有鉴权，保持一致；保留限流。前端 `api()` 自动附带 `localStorage.admin_token`，卡片错误提示引导设置 token。
- **状态**：部分关闭——`/api/review/three_questions` 前端在用，加鉴权会导致现有 UI 失效，列为待评估项（§8）。
- **验证**：新增 validate 的 401/503/200 三例 + `test_backtest_times_out_with_503`。
- **commit**：`36407a7 fix(security): strategy/validate 补齐 require_admin 鉴权`

### R-15 [P2] `--workers 4` 破坏进程内状态

- **原始问题**：限流计数、缓存、APScheduler、持仓锁均为进程内状态，多 worker 语义错乱。
- **技术方案**：`start.ps1 --prod` 改为单进程 `launch.py --host 0.0.0.0 --port 8000`，与 Procfile 保持一致。
- **状态**：关闭。
- **验证**：生产分支启动后单进程运行，`/api/health` 200。
- **commit**：`03df2c1 fix(startup): 生产启动固定单进程, 移除 --workers 4`

---

## 4. 覆盖率补测中新发现并修复的 2 个缺陷

补测 `src/core/data_source.py` 时，两个用例在**未改断言**的前提下直接失败，定位为真实缺陷：

### N-01 K 线缓存读回在 pandas 3.x 下失效

- **原始问题**：`CacheSource._deserialize` 用 `pd.read_json(JSON 字符串)`；pandas 3.x 起该用法被当作文件路径，抛 `FileNotFoundError` 后被 `except` 吞掉返回 `None` —— 缓存里的历史行情永远读不回来，且无日志。
- **技术方案**：`import io` + `pd.read_json(io.StringIO(d["data"]), orient="records")`，不改缓存格式与写入侧。
- **验证**：`tests/test_cache_source_regressions.py::TestKlineCacheRoundtrip`（写入后读回断言 symbol/source/len(data)/latency_ms）；去掉 `StringIO` 时该用例失败。
- **commit**：`2f4bd64 fix(core): K线缓存读回适配 pandas 3.x`

### N-02 数据源缓存兜底键不一致

- **原始问题**：`DataSourceManager._fallback` 用方法名写缓存（`get_realtime_quote`），`CacheSource._retrieve` 用 `query_type` 读（`realtime_quote`），读写键永不匹配 → "刚成功取过、随后全部源故障"仍抛 `DataSourceError`，缓存兜底是死代码。
- **技术方案**：写入改为 `method_name.removeprefix("get_")`，与四个 getter 读取键对齐；无缓存时仍 fail-closed。
- **验证**：`TestManagerCacheFallback`（先成功写缓存 → 全源故障 → 断言返回缓存对象）；`test_no_cache_still_fails_closed` 断言未放松失败语义。
- **commit**：`5a931f8 fix(core): 修复数据源缓存兜底键不一致导致降级形同虚设`

---

## 5. 覆盖率

| 范围 | 修复前 | 修复后 | 说明 |
|---|---|---|---|
| 整体 | 77.13%（3175/13886 未覆盖） | **81.51%**（2568/13887） | 达到 ≥80% 目标 |
| `src/core/data_source.py` | 34% | 96.84%（13 未覆盖） | 三源 × 5 方法 × 成功/空/异常 |
| `web/routes/market.py` | 55% | 90.57%（68 未覆盖） | 22 个端点四类分支 |
| `web/routes/dashboard.py` | 59% | 87.34%（40 未覆盖） | 组合风险 7 条分支 |
| `src/core/data_source_v2.py` | 38% | 38.63%（858 未覆盖） | 未优先补，见 §8 |

新增测试文件：`tests/test_market_routes_coverage.py`、`tests/test_data_source_coverage.py`、`tests/test_dashboard_portfolio_risk.py`、`tests/test_cache_source_regressions.py`。

---

## 6. 验证证据

### 6.1 本地冒烟（`python launch.py --port 8055`，dev 模式）

| 请求 | 状态码 | 耗时 / 要点（取自服务端访问日志） |
|---|---|---|
| `GET /api/health` | 200 | 启动日志 `Application startup complete` 后即可用 |
| `GET /static/index.html` | 200 | 首屏 HTML 正常返回 |
| `GET /api/market/status` | 200 | 0ms，返回当前交易时段 |
| `GET /api/market/northbound` | 200 | 984ms；上游 `push2` 被代理阻断 → 走降级路径，仍返回结构完整响应 |
| `GET /api/market/heatmap` | 200 | 547ms；行业板块取数失败 → 降级，未返回 500 |
| `GET /api/market/search?q=600519` | 200 | 5328ms；上游连接被重置 → 返回空结果，未返回 500 |
| `GET /api/market/stock_realtime?codes=600519` | 200 | 671ms，真实行情返回 |
| `GET /api/dashboard/portfolio_risk` | 200 | 16ms，本地空持仓 → `insufficient` 分支 |
| `POST /api/strategy/validate`（缺字段） | 422 | 请求体校验语义正常 |
| `POST /api/strategy/validate`（合法收益序列） | 200 | 0ms，正常返回验证结果 |
| `POST /api/fund/positions/validate`（缺字段） | 422 | 请求体校验语义正常（原为 503，R-01 后不再被鉴权拦截） |

冒烟后进程已终止，8055 端口已释放（`Get-NetTCPConnection` 无占用，无残留 `python` 进程）。

### 6.2 线上 Render

| 项目 | 结果 |
|---|---|
| `GET /api/health` | **200** |
| 冷唤醒耗时 | **54s**（首次请求触发休眠实例唤醒，`uptime_seconds=47`） |
| 新构建上线复核 | `GET /static/index.html` 已包含 `strategyLabCard`（R-03 前端入口），`POST /api/strategy/validate` 无 token 返回 **401**（R-13 生效） |
| 新构建健康复核 | `GET /api/health` 200，热请求 1.5s，`uptime_seconds=118`（新进程刚启动） |

---

## 7. Backlog（未做，明确转出）

| 编号 | 级别 | 内容 | 转出原因 |
|---|---|---|---|
| R-08 | P2 | 前端单文件巨石（6790 行）拆分 + eslint + 页面快照测试 | 结构性重构，需单独立项 |
| R-14 | P2 | Vercel serverless 部署语义不成立 | 需产品决策（移除入口或改只读） |
| R-16 | P3 | ruff 172 条（F401×121 等） | 本轮仅保证新增/改代码不新增告警；`__init__.py` re-export 需补 `__all__`/per-file-ignores 后再清，禁止全量 `--fix` |
| R-17 | P3 | F841 赋值未使用（backtest/capital_flow/news/ai_service） | 同上 |
| R-18 | P3 | 品牌残留（start.bat/control.ps1 已随 R-10 一并处理，其余脚本待核） | 低风险清理 |
| R-19 | P3 | `fly.toml` 引用不存在的 Dockerfile | 部署选项决策 |
| R-20 | P3 | 魔法值散落（2.0 阈值、0.015/0.03 止盈止损） | 需配置化设计 |
| R-21 | P3 | CSP 含 `'unsafe-inline'` 与 unpkg 外部脚本无 SRI | 与 R-08 前端重构同批 |
| 孤儿端点 | - | 其余 29 个无前端入口的后端端点 | 按口径只登记，不删不增 |
| 部署配置 | - | `railway.json` 仍为 `--workers 2`，与 R-15 单进程口径不一致 | 需确认该平台是否仍在用 |
| 环境 | - | `.venv` 的 `python.exe` 指向不存在的 Python310、无脚本引用 | 仅记录，未删除（避免误删用户环境） |

---

## 8. 待基线 / 未达成项（如实记录）

1. **R-04 的"干净环境安装验证"待基线**：`pip download --no-deps slowapi` 多次超时（出网受阻），无法在隔离环境验证"声明版本可安装"。已锁定实测版本，但**未做干净环境验证**；未凭猜测改版本号。
2. **`/api/review/three_questions` 鉴权**：前端在用，加 `require_admin` 会使现有 UI 失效，本轮未改，待确认前端 token 注入方案后再评估。
3. **`src/core/data_source_v2.py` 覆盖率 38.63%**：其取数函数直接绑定外部协议（腾讯/东财/akshare/mootdx），稳定 mock 成本高。分阶段补测计划：
   - 阶段 A：抽离 `_parse_*` 纯解析函数后补测（预计 +8pp）；
   - 阶段 B：为 `requests` 加注入点，覆盖超时/重试/降级三分支（预计 +6pp）；
   - 阶段 C：端到端契约测试（可选，依赖录制回放）。
4. **CI 覆盖率门槛仍为 75%**：本轮实际 81.51%，未上调门槛（避免在未跑满 CI 环境前引入误伤），建议下一轮按实测基线提到 78%。
5. **线上冷唤醒基线含义**：本轮实测到的 54s 冷唤醒发生在**旧构建**实例上；54s 是首次请求的端到端等待时间（含实例唤醒 + 应用启动 + akshare 加载）。新构建上线后仅测得热请求 1.5s，**新构建的冷唤醒耗时未复测**（Render Free 实例需再次休眠才能测，建议下一轮以 `/api/health` 首次响应时间补齐）。
