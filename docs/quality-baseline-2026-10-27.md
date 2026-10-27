# QuantFlow Pro 质量基线报告（第二轮 · 2026-10-27）

- 项目路径：`D:/xm/wz/Fund-Assessment`
- 采集时间：2026-10-27 21:23 – 21:42（本地时区 +0800）
- 当前 HEAD：`3943752`（`origin/main`）
- 用途：第二轮评估（增量复审 + 遗留收口）的唯一参照基准。**所有数字均来自本轮实际命令输出**；未实测项显式标注「待基线」。
- 对比基准：`docs/quality-baseline-2026-10-26.md`（第一轮阶段 0，1490 用例）

---

## 1. 环境与复现

| 项 | 值 |
|---|---|
| 解释器 | `D:\dev-tools\Python312\python.exe`（Python 3.12.9，`python312._pth` 模式） |
| 测试 | `python -m pytest tests/ -q` |
| 启动 | `python launch.py --host 127.0.0.1 --port 8055` |
| 诊断工具 | coverage 7.17.0 / pytest-cov 7.1.0 / Playwright（Edge `channel=msedge`） |
| 环境陷阱 | `PYTHONPATH` 失效、cwd 不入 `sys.path`；本机 curl 必须 `--noproxy '*'`；`pip install` 被代理拦截 |

新增可复跑脚本：
- `deliverables/gstack/_baseline_round2_services.py`：冷启动 3 次 + 首页/API/静态资源度量
- `deliverables/gstack/_perf_probe.py`：CWV 采集（沿用第一轮，`QF_URL` 指定端口）
- 机器可读产物：`_baseline_round2_services.json`、`_coverage-round2.json`、`_perf_round2.txt`

---

## 2. 测试基线

| 轮次 | 命令 | 结果 | 耗时 |
|---|---|---|---|
| Run 1 | `pytest tests/ -q` | **1758 passed / 0 failed / 1 warning** | 50.51 s |
| Run 2 | 同上 | **1758 passed / 0 failed / 1 warning** | 57.97 s |
| 覆盖率轮 | `pytest tests/ -q --cov=src --cov=web` | **1758 passed / 0 failed** | 86.28 s |

- 唯一 warning：`pkg_resources is deprecated`（`pylibs/py_mini_racer`），与第一轮相同。
- 与第一轮基线对比：**1490 → 1758（+268 例）**，0 failed。
- 覆盖率轮耗时包含插桩开销，不与普通回归耗时直接比较。

---

## 3. 覆盖率基线（本轮实测）

命令：
```bash
python -m pytest tests/ -q --cov=src --cov=web \
  --cov-report=json:deliverables/gstack/_coverage-round2.json \
  --cov-report=term-missing:skip-covered
```

| 范围 | 语句数 | 未覆盖 | 覆盖率 | 判定 |
|---|---:|---:|---:|---|
| **整体（src + web）** | 12 348 | 2 988 | **76%** | 未达 80% |
| `src/analysis/portfolio_attribution.py` | 94 | 11 | **88%** | ✅ |
| `src/core/portfolio_store.py` | 190 | 25 | **87%** | ✅ |
| `src/core/risk_manager.py` | 255 | 23 | **91%** | ✅ |
| `src/core/executor.py` | 346 | 32 | **91%** | ✅ |
| `src/core/llm_router.py` | 390 | 80 | **79%** | ⚠️ 差 1 个点 |
| `web/routes/portfolio.py` | 541 | 59 | **89%** | ✅ |
| `src/core/data_source_v2.py` | 1 295 | 862 | **33%** | ❌ 最大缺口 |
| `src/core/data_source.py` | 411 | 273 | **34%** | ❌ |
| `web/routes/market.py` | 719 | 319 | **56%** | ❌ |
| `web/routes/dashboard.py` | 316 | 131 | **59%** | ❌ |
| `web/routes/config.py` | 187 | 62 | **67%** | ❌ |
| `src/strategies/stock_monitor.py` | 216 | 107 | 50% | ❌ |
| `src/strategies/trading_quant.py` | 275 | 135 | 51% | ❌ |
| `src/strategies/limit_up.py` | 194 | 76 | 61% | ❌ |
| `src/strategies/a_stock_analyst.py` | 231 | 90 | 61% | ❌ |

**结论**：第一轮新增的组合/风控/执行链路覆盖率已达标；缺口集中在**行情数据源（data_source_v2 / data_source）与部分路由/策略**。阶段 2 的目标：
1. `llm_router` 补到 ≥80%（差距最小，优先）。
2. 新增/修改代码继续保持 ≥80%。
3. `data_source_v2` 制定分阶段提升计划（本轮先补关键分支，不承诺一步到 80%）；差距与原因写入最终报告。

---

## 4. 服务基线

### 4.1 冷启动（3 次，端口 8055）

| 采样 | 进程启动 → `/api/health` 200 |
|---|---|
| 第 1 次 | 3 381.8 ms |
| 第 2 次 | 3 369.3 ms |
| 第 3 次 | 3 356.0 ms |
| **中位数** | **3 369.3 ms** |

> 与第一轮 7 165 ms 不可直接比较：本轮为当日磁盘缓存已热状态；第一轮包含首次加载。该数字用于**同机同状态**的后续对比。

### 4.2 首页与静态资源

| 指标 | 实测 |
|---|---|
| `GET /` | **307** → `/static/index.html` |
| `HEAD /` | **405**（`Allow: GET`，A-17 仍存在） |
| `GET /static/index.html` 状态 | 200，`text/html; charset=utf-8`，ETag `"da995bc9…"` |
| 本地未压缩字节 | **314 326 B**（CRLF；生产为 LF 308 346 B） |
| gzip 传输字节 | **76 213 B**（压缩率 **75.75%**） |
| 本地 `index.html` sha256 | `4ffc7ae5…`（CRLF 原文） |
| **CRLF 归一化 sha256** | **`34fd6c21…`（与线上一致）** |
| 静态模块 | 5 个 JS（freshness 3 188 B / sectors 6 712 B / signal-progress 9 112 B / stage6-portfolio 47 470 B / wizard 26 528 B）+ 2 个 CSS（hud 5 306 B / stage6 9 857 B） |

### 4.3 API 规模（本地 `/openapi.json`）

| 指标 | 值 |
|---|---|
| 路径数 | **105** |
| 操作数（get/post/put/delete/patch） | **112** |

> 第一轮为 93 路径 / 98 操作；本轮新增 12 路径 / 14 操作（组合盯盘/风险/X-Ray/分组/归因/Webhook/导入导出等）。

---

## 5. 前端性能基线（同口径，localhost 无节流）

采集：`_perf_probe.py`，桌面 1440×900 / 移动 390×844，各 4 次；第 1 次为后端冷缓存，取第 2–4 次中位数。

| 指标 | 桌面（中位数） | 移动（中位数） | 第一轮桌面 | 备注 |
|---|---:|---:|---:|---|
| FCP | **100 ms** | **88 ms** | 96 ms | 持平 |
| LCP | **100 ms** | **144 ms** | 96 ms | 持平 |
| CLS | **0.02** | **0.00** | 0.23 | **显著改善** |
| TBT | **0 ms** | **0 ms** | 0 ms | 持平 |
| TTFB | 12.0 ms | 11.6 ms | 10.6 ms | — |
| DOM 节点 | **1 352** | 1 352 | 1 309 | +43 |
| 首屏请求数 | **13** | 13 | 6 | 新增 5 个静态模块 + 2 个 API |
| 首屏跨域请求 | **0** | 0 | 0 | ✅ 架构优点保持 |
| API 总耗时（热） | 105.7 ms | 97.5 ms | — | — |
| API 最慢（热） | 17.8 ms | 16.6 ms | — | — |
| JS 运行时错误 | 0 | 0 | 0 | — |
| 失败请求 | 0 | 0 | 0 | — |

冷缓存首轮（run1）关键值：桌面 API 总耗时 **6 887.8 ms**、最慢 **2 422 ms**、LCP 332 ms；移动 API 总耗时 380.9 ms、最慢 270.2 ms。说明冷态外部行情请求仍是首屏延迟主因，但 LCP 已不被其阻塞。

---

## 6. 线上基线（匿名，2026-10-27 21:28–21:33）

| 请求 | 状态 | 耗时 | 备注 |
|---|---|---:|---|
| `GET /api/health`（首次唤醒） | 200 | **53.597 s** | Render Free 休眠唤醒 |
| `GET /api/health`（热态 ×3） | 200 | **1.073 / 1.168 / 1.422 s** | 网络地板约 1.1 s |
| `GET /` | 307 | 1.787 s | — |
| `GET /static/index.html`（gzip） | 200 | 1.476 s | 75 895 B |
| `GET /api/fund/positions` | **401** | 2.267 s | 鉴权生效 |
| `GET /api/portfolio/groups` | **401** | 1.344 s | 鉴权生效 |
| `GET /api/portfolio/attribution` | **401** | 1.537 s | 鉴权生效 |
| `GET /api/health/llm` | 200 | 1.319 s | 3 Provider（`zhipu_glm` / `zhipu_glm_45` / `agnes`） |
| `GET /api/market/hot_stocks` | 200 | 3.086 s | 公开行情 |

资源指纹：
- `stage6-portfolio.js` 线上 sha256 = **`8d7b4909…`**，与本地一致。
- `index.html` 线上 LF 原文 sha256 = `34fd6c21…`；本地 CRLF 归一化后同值。

安全头（线上实测）：CSP 仍含 `'unsafe-inline'`（A-19 / B-04 遗留）；`strict-transport-security`、`referrer-policy`、`permissions-policy` 已存在；`x-content-type-options` 等 500 响应头未在本次冒烟中覆盖（B-14 待阶段 1 复核）。

---

## 7. 代码规模基线

| 范围 | 文件数 | 行数 |
|---|---:|---:|
| `src/` | 57 | **17 237** |
| `web/` | 20 | **6 157** |
| `tests/` | — | **21 502** |
| `scripts/` | — | 1 215 |
| 前端合计 | 8 | **8 332**（`index.html` 5 980 + JS 1 994 + CSS 358） |

> 第一轮：src 15 554 / web 4 576 / tests 17 650 / 前端 5 571。增长主要来自组合模块、LLM Provider、阶段 6 前端与测试。

---

## 8. 依赖漂移（本轮复核）

| 包 | 声明 | 实测 | 判定 |
|---|---|---|---|
| fastapi | `==0.115.12` | **0.136.1** | ⚠️ 跨大版本 |
| starlette | 未直接声明 | **1.0.0** | — |
| pandas | `==2.3.3` | **3.0.3** | ⚠️ 跨大版本 |
| numpy | `==2.5.1` | **2.4.6** | ⚠️ 低于声明 |
| requests | `==2.32.3` | 2.34.1 | ⚠️ 高于声明 |
| uvicorn | `==0.34.2` | **0.46.0** | ⚠️ 跨次版本 |
| akshare | `==1.16.94` | 1.18.64 | ⚠️ 高于声明 |
| httpx | `>=0.27.0` | **0.25.2** | ❌ 违反下限 |
| pydantic | `==2.13.4` | 2.13.4 | ✅ |
| pytest | 未声明 | 9.0.3 | 测试运行器 |
| pytest-asyncio / pytz | 未声明 | 已安装 | ❌ 开箱即用缺口 |

> 门禁 G-1 仍未通过：本机 `pip` 被代理拦截，无法用 `requirements.txt` 重建 pinned 环境；依赖 CVE 扫描（pip-audit）同样不可用，标「待基线」。

---

## 9. 「待基线」清单

| 项 | 原因 | 补测条件 |
|---|---|---|
| Lighthouse / PageSpeed Insights 正式分数 | 本机无 Lighthouse CLI；PSI 需外网配额 | 线上可用 + 允许调用 PSI |
| 生产环境 LCP/CLS（真实 4G 节流） | 本轮只采本地 CWV 与线上匿名耗时 | 阶段 4 用 Playwright 对线上采样或 PSI |
| 线上带凭据功能复验 | 本机无 `ADMIN_TOKEN` | 用户提供令牌后做只读复验 |
| Firefox / Safari 兼容 | 本机无对应运行时 | 安装 Playwright Firefox/WebKit 或真机 |
| 依赖 CVE（pip-audit / OSV） | pip 出网被拦 | 恢复 pip 或使用离线镜像 |
| 各端点 p50/p95 | 未压测 | k6/locust 可用时 |
| 内存长稳 | 未长时间运行 | 长稳环境 |

---

## 10. 与第一轮基线对比（结论）

| 指标 | 2026-10-26 | 2026-10-27 | 变化 |
|---|---:|---:|---|
| 测试用例 | 1 490 | **1 758** | +268，0 failed |
| 整体覆盖率 | 74%（第一轮阶段 1 实测） | **76%** | +2pp |
| 首页未压缩 | 290 009 B | **314 326 B** | +8.4% |
| 首页 gzip | 67 920 B | **76 213 B** | +12.2% |
| DOM 节点 | 1 309 | **1 352** | +43 |
| API 路径 / 操作 | 93 / 98 | **105 / 112** | +12 / +14 |
| 桌面 CLS | 0.23 | **0.02** | 显著改善 |
| 首屏跨域请求 | 0 | **0** | 保持 |
| 线上热态 health | — | **1.07–1.42 s** | 新基线 |

**基线结论**：第一轮的 P0/P1 修复与阶段 5 性能修复保持有效（CLS 0.02、0 跨域、0 JS 错误）。第二轮的主要质量债是**覆盖率缺口（data_source_v2 33%）**、**P3 遗留**、**依赖漂移**与**带凭据线上复验缺口**。

---

## 附录：本轮未修改任何业务代码

- 未修改 `src/`、`web/`、`tests/`、`requirements.txt`、`pyproject.toml`。
- 未调整任何测试断言或阈值。
- 新增文件仅限 `deliverables/gstack/` 下的基线脚本与产物（已 gitignore）。
- 服务进程全部终止，端口 8055 已释放。
