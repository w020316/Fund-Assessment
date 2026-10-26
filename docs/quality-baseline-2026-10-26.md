# QuantFlow Pro 质量基线报告（阶段 0）

- 项目路径：`D:/xm/wz/Fund-Assessment`
- 采集时间：2026-10-26 13:00 – 13:20（本地时区 +0800）
- 采集人：gstack-investigator（GStack 工程团队）
- 用途：阶段 1–5 的唯一参照基准。**本文件中每一个数字均来自实际命令输出**，未使用任何估算、经验值或文档抄录。凡未能实测的项，均显式标注「待基线」并写明补测所需条件。

---

## 1. 环境与复现方式

### 1.1 实际使用的解释器

| 项目 | 值 |
|---|---|
| 解释器绝对路径 | `D:\dev-tools\Python312\python.exe` |
| Python 版本 | `3.12.9 (tags/v3.12.9:fdb8142, Feb 4 2025) [MSC v.1942 64 bit (AMD64)]` |
| 依赖来源 | 系统级 `site-packages`（`D:\dev-tools\Python312\Lib\site-packages`）+ 项目内置 `pylibs/`（42 个包，由 `tests/conftest.py` 与 `launch.py` 注入 `sys.path`） |
| 项目 `.venv` | **未使用**。其 `Scripts\python.exe` 指向不存在的 `C:\...\Python310\python.exe`，已损坏 |

### 1.2 关键环境陷阱（后续所有人必读）

`D:\dev-tools\Python312\python312._pth` **存在**，使该解释器运行在 `._pth` 模式下：

- `PYTHONPATH` 环境变量被**完全忽略**（实测 `export PYTHONPATH=...` 后 `sys.path` 无变化）
- 当前工作目录**不会**被加入 `sys.path`（实测 `python -c "import web.api"` 报 `ModuleNotFoundError: No module named 'web'`）

因此**不能**用 `python -c` / `python -m` 直接 import 项目模块。可行的两条路径：

1. `python launch.py`（`launch.py` 内部把项目根与 `pylibs` 插入 `sys.path`，并同步写入 `PYTHONPATH` 供 reload 子进程继承）
2. `python -m pytest`（`tests/conftest.py` 第 8–17 行把项目根与 `pylibs` 插入 `sys.path`）

> **附带风险**：`python312._pth` 内硬编码了两个**属于其他项目**的路径：
> `d:\xm\mylibrary-rag\pylibs`、`d:\xm\mylibrary-rag`。
> 实测 `sys.path` 前 5 项为：
> `['D:\\dev-tools\\Python312\\python312.zip', 'D:\\dev-tools\\Python312', 'd:\\xm\\mylibrary-rag\\pylibs', 'd:\\xm\\mylibrary-rag', 'D:\\dev-tools\\Python312\\Lib\\site-packages']`
> 这意味着 `mylibrary-rag` 的同名包可能**遮蔽**本项目依赖。当前测试全绿说明未发生冲突，但这是一个跨项目环境污染隐患，建议后续阶段清理。

### 1.3 环境修复清单（本次为跑通测试而补装，非代码改动）

原始环境下测试无法全绿，根因是 4 个缺失依赖。补装记录：

| 包 | 补装版本 | 缺失导致的后果 | 是否在 `requirements.txt` 中 |
|---|---|---|---|
| `pytz` | 2026.5 | `pylibs/apscheduler` 导入失败 → `tests/test_scheduler.py` 整体 collection error（少收集 46 个用例） | 否（apscheduler 的传递依赖） |
| `slowapi` | 0.1.10 | `ModuleNotFoundError` → **213 个 error** | 是（`slowapi>=0.1.9`） |
| `json-repair` | 0.64.2 | 缺失（未直接触发失败，但属声明依赖） | 是（`json-repair>=0.25.0`） |
| `pytest-asyncio` | 1.4.1 | `async def functions are not natively supported` → **41 个 failed** | 否（测试依赖，未在任何清单中声明） |

传递依赖随之安装：`deprecated 3.0.0`、`limits 5.8.0`、`wrapt 2.5.1`。

未安装：`snownlp`（`requirements.txt` 注释明确为可选，代码有降级路径，不影响测试）。

### 1.4 已安装依赖与声明版本漂移（重要）

系统环境版本与 `requirements.txt` / `pyproject.toml` 声明**不一致**：

| 包 | 声明版本 | 实测版本 | 说明 |
|---|---|---|---|
| fastapi | 0.115.12 | **0.136.1** | 跨大版本 |
| starlette | （未直接声明） | **1.0.0** | 与 fastapi 0.136.1 配套 |
| pandas | 2.3.3 | **3.0.3** | 跨大版本（pandas 3.x 有破坏性变更） |
| numpy | 2.5.1 | **2.4.6** | 低于声明 |
| requests | 2.32.3 | 2.34.1 | 高于声明 |
| uvicorn | 0.34.2 | **0.46.0** | 跨次版本 |
| akshare | 1.16.94 | 1.18.64 | 高于声明 |
| httpx | **>=0.27.0** | **0.25.2** | **违反声明约束（低于下限）** |
| pydantic | 2.13.4 | 2.13.4 | 一致 |
| pytest | （未声明） | 9.0.3 | 测试运行器 |

> 结论：**当前 1490 全绿是在"漂移后的依赖组合"上达成的**，不代表在 `requirements.txt` 锁定版本上同样全绿。生产部署（Render/Fly）按 `requirements.txt` 安装，本地实测环境与之不同 —— 这是阶段 1 必须评估的风险项，本次不做修复。

### 1.5 复现命令（可直接复制）

```bash
# 测试（工作目录 = 项目根）
D:/dev-tools/Python312/python.exe -m pytest tests/ -q

# 服务启动（端口 8123 避开冲突）
D:/dev-tools/Python312/python.exe launch.py --host 127.0.0.1 --port 8123

# 健康检查（本机存在系统代理，必须 --noproxy，否则拿到代理的 502）
curl -s --noproxy '*' http://127.0.0.1:8123/api/health

# 前端性能采集（脚本已留存，见 4.1）
D:/dev-tools/Python312/python.exe deliverables/gstack/_perf_probe.py
```

> **代理陷阱**：本机 `http_proxy=http://127.0.0.1:56340` 已设置。不带 `--noproxy '*'` 的 curl 会走代理并返回 **502**（实测），会让人误判服务未启动。所有本机请求必须加 `--noproxy '*'`。

---

## 2. 测试基线

### 2.1 最终基线（可复现）

| 指标 | 实测值 |
|---|---|
| 收集用例总数（collected） | **1490** |
| 通过（passed） | **1490** |
| 失败（failed） | **0** |
| 错误（errors） | **0** |
| 跳过（skipped） | **0** |
| 警告（warnings） | 1（`pkg_resources is deprecated`，来自 `pylibs/py_mini_racer`） |
| 总耗时 | **30.78 s**（复跑 30.87 s，两次一致） |
| 退出码 | 0 |
| collection error | 0 |

复跑验证：同一命令连续执行 2 次，结果均为 `1490 passed, 1 warning`，耗时 30.78 s / 30.87 s。

### 2.2 基线演进过程（如实记录，供判断「文档声称 vs 真实」）

| # | 环境状态 | collected | passed | failed | errors | 耗时 | 退出码 |
|---|---|---|---|---|---|---|---|
| ① | 原始（缺 pytz） | 1444 + 1 collection error | — | — | — | 25.44 s | 中断 |
| ② | + `pytz` | 1490 | 1234 | 43 | 213 | 33.66 s | 1 |
| ③ | + `slowapi` `json-repair` | 1490 | 1449 | 41 | 0 | 37.90 s | 1 |
| ④ | + `pytest-asyncio`（最终基线） | 1490 | **1490** | **0** | **0** | 30.87 s | 0 |
| ⑤ | 标准命令复跑确认 | 1490 | **1490** | **0** | **0** | 30.78 s | 0 |

### 2.3 失败/错误根因（已全部闭环，均为环境缺失，非代码缺陷）

**状态 ②：213 个 error —— 单一根因**
- 缺失模块：`slowapi`，共 214 处 `No module named 'slowapi'` 报错（213 个 error + 1 处 import 期报错）
- 受影响用例集中在 `tests/test_trade_routes.py` 等路由测试（`web/rate_limiter.py` 导入 `slowapi`）

**状态 ③：41 个 failed —— 单一根因**
- 全部 41 条失败原因**完全相同**：`Failed: async def functions are not natively supported.`
- 根因：未安装 `pytest-asyncio`，`@pytest.mark.asyncio` 被当作未知 mark（收集期产生 42 条 `PytestUnknownMarkWarning`），async 用例被收集但无法执行
- 涉及 6 个文件、41 个 async 用例：

| 文件 | async 用例数 |
|---|---|
| `tests/test_dashboard_routes.py` | 7 |
| `tests/test_fund_advisor_v2.py` | 5 |
| `tests/test_fund_holdings.py` | 13 |
| `tests/test_market_assessment.py` | 3 |
| `tests/test_multi_agent_fund.py` | 3 |
| `tests/test_news_aggregator.py` | 10 |

**状态 ①：1 个 collection error —— 单一根因**
```
ERROR collecting tests/test_scheduler.py
tests\test_scheduler.py:22: from src.core.scheduler import Scheduler, _DEFAULT_JOB_TIMEOUT
src\core\scheduler.py:7: from apscheduler.schedulers.background import BackgroundScheduler
pylibs\apscheduler\executors\base.py:8: from pytz import utc
E   ModuleNotFoundError: No module named 'pytz'
```
`src/core` 的模块级 try/except 已捕获该异常并降级（日志：`import .scheduler failed: No module named 'pytz'`），但测试文件直接 import 具体符号，无法降级。

### 2.4 外部网络依赖失败

**未观测到**。最终基线 0 failed / 0 error，说明测试已对行情数据源（akshare 等）做了充分 mock，**不存在因外部网络导致的失败**。此结论仅针对本次实测（网络可用状态下），不代表离线环境表现。

### 2.5 README 声明与实测的差异（数据真实性核对）

| 项 | README 声明 | 实测 | 差异 |
|---|---|---|---|
| 测试文件数 | 56 | **59**（`tests/test_*.py`）+ `conftest.py` + `__init__.py` = 61 个 `.py` | +3 |
| 测试用例数 | 1394 | **1490** | **+96** |
| 通过率 | 「全部通过」 | 在**补装 4 个依赖后**才 100% 通过；原始环境为 43 failed + 213 errors | 声明不完整 |

> 结论：README 的测试数据**过期且未标注前提**。阶段 1 应将 README 数字同步列为文档缺陷。

---

## 3. 服务基线

### 3.1 启动与健康检查

| 指标 | 实测值 |
|---|---|
| 启动命令 | `D:/dev-tools/Python312/python.exe launch.py --host 127.0.0.1 --port 8123` |
| 启动结果 | **成功**。日志出现 `Application startup complete` / `Uvicorn running on http://127.0.0.1:8123` |
| `GET /api/health` HTTP 状态码 | **200** |
| `GET /api/health` Content-Length | 506 B（未压缩，低于 GZip 1000 B 阈值） |
| 响应体 | `{"status":"ok","akshare":true,"core_modules":true,"ai_ready":true,"ai_keys":{"ttapi":true,"tavily":true,"tinyfish":true,"agnes":true},"metrics":{...},"cache_stats":{...}}` |
| 启动期日志 | `akshare loaded successfully` / `core modules loaded successfully` |

### 3.2 冷启动耗时（3 次独立采样）

| 采样 | 从进程启动到首次 `/api/health` 返回 200 |
|---|---|
| 第 1 次 | 7 165 ms |
| 第 2 次 | 12 617 ms |
| 第 3 次 | 6 154 ms |
| **中位数** | **7 165 ms**（区间 6.2 – 12.6 s） |

> 说明：第 2 次采样偏慢（12.6 s）发生在全量测试刚结束、系统 IO 竞争时；应用自身 `uptime_seconds` 当时报 11.0 s，与外部计时一致。**冷启动基线取 7.2 s（中位数），并记录 12.6 s 为观测上限。**

### 3.3 首页响应

| 请求 | 状态码 | 说明 |
|---|---|---|
| `GET /` | **307** | `Location: http://127.0.0.1:8123/static/index.html`，body 0 B |
| `GET /static/index.html`（跟随重定向） | **200** | `Content-Length: 290 009`，`Content-Type: text/html; charset=utf-8`，`ETag: "2190ab7ff2fb354414b6db723da8a81a"`，耗时 76 ms |
| `GET /static/index.html` + `Accept-Encoding: gzip` | **200** | 传输 **67 920 B**（压缩率 **76.6%**） |
| `HEAD /` | **405** | `Allow: GET` —— 根路径仅注册 GET，HEAD 不被 FastAPI 自动放行 |

前端单文件大小与源码一致（290 009 B），静态服务正常。

### 3.4 API 规模（来自运行中服务的 `/openapi.json`）

| 指标 | 值 |
|---|---|
| 路径数（paths） | 93 |
| 操作数（get/post/put/delete/patch 合计） | 98 |

### 3.5 进程清理

服务已关闭：`netstat` 无 `LISTENING` 于 8123（仅残留 `TIME_WAIT`），`tasklist` 无 `python.exe`，探针请求返回 `000`（连接拒绝）。**无遗留后台进程占用端口。**

---

## 4. 性能基线

### 4.1 采集方式（已实测，非「待基线」）

| 项 | 值 |
|---|---|
| Lighthouse | **不可用**。`npx --no-install lighthouse` 报 `missing packages`；尝试下载（`npx lighthouse@13.5.0`）需联网拉取，未安装成功。**故未采 Lighthouse 分数** |
| Playwright 自带浏览器 | **不可用**。Python `playwright 1.60.0` 期望 `chromium-1223` / `firefox-1522`，本地 `D:\xm\dev-cache\ms-playwright` 仅有 `firefox-1549`、`webkit-2359`、`webkit-2365`、`ffmpeg-1011`、`winldd-1007` —— **无 chromium 任何版本**。`playwright install chromium` 执行 5 分 30 秒未产生下载目录，判定下载受阻并中止 |
| **实际采用方案** | **Playwright + 系统 Edge（Chromium 系，`channel="msedge"`，headless）**，注入 `PerformanceObserver` 采集 FCP / LCP / CLS / longtask |
| 采集脚本（已留存，可复跑） | `deliverables/gstack/_perf_probe.py` |
| 采样方式 | 桌面 1440×900、移动 390×844（DSF 3，iPhone UA）各 4 次；第 1 次为后端缓存冷态，取第 2–4 次中位数 |
| 引擎 | Edge（Chromium 内核），支持 longtask → TBT 可计算 |
| 被测地址 | `http://127.0.0.1:8123/`（**本机回环，无 WAN 延迟**） |

### 4.2 实测指标（中位数，第 2–4 次采样）

| 指标 | 桌面 1440×900 | 移动 390×844 | 评级参考（Core Web Vitals 阈值） |
|---|---|---|---|
| FCP | **96 ms** | **88 ms** | 良好 |
| LCP | **96 ms** | **136 ms** | 良好 |
| CLS | **0.23** | **0.00** | 桌面 **需改进**（>0.1） |
| TBT | **0 ms** | **0 ms** | 良好 |
| 最长长任务 | 0 ms | 0 ms | — |
| TTFB | 10.6 ms | 10.6 ms | 良好 |
| DOMContentLoaded | 32.6 ms | 30.4 ms | — |
| load 事件 | 72.9 ms | 64.1 ms | — |
| 文档传输字节（gzip） | 68 220 B | 68 220 B | — |
| 文档解码字节 | 290 009 B | 290 009 B | — |
| 首屏请求数 | 6 | 6 | — |
| DOM 节点数 | 1 309 | 1 309 | — |
| JS 运行时错误 | 0 | 0 | — |
| 失败请求 | 0 | 0 | — |

### 4.3 冷态（后端缓存为空）首次采样 —— 波动来源

| 指标 | 桌面首次 | 移动首次 |
|---|---|---|
| FCP | 288 ms | 164 ms |
| LCP | **288 ms**（另一会话测到 **1 324 ms**） | 180 ms |
| CLS | 0.03 | 0.00 |
| TBT | 12 ms（最长长任务 62 ms） | 0 ms |
| API 请求总耗时 | 520 ms（另一会话 1 706 ms 单请求） | 411 ms |

**关键发现：LCP 由后端接口延迟决定，而非前端渲染。** 首屏 6 个同源请求中，`/api/market/hot_stocks` 冷态单次可达 **1 706 ms**，直接把 LCP 从 ~100 ms 拉到 1.3 s。热态同接口仅 14–20 ms。

### 4.4 CLS 不稳定性（需阶段 5 关注）

桌面 4 次采样的 CLS 分别为 **0.03 / 0.37 / 0.07 / 0.23**，中位数 0.23；另一次会话中位数 0.12。**波动区间 0.03 – 0.37，多次超过 0.1 的「良好」阈值**，属数据异步填充引起的布局抖动。移动端 4 次均为 **0.00**（移动布局下数据区域不在视口内或尺寸已预留）。

> 该指标波动大，单次测量不可信。阶段 5 重构后复测须**同法多次采样取中位数**才能对比。

### 4.5 外部请求

| 项 | 实测 |
|---|---|
| 首屏外部（跨域）请求数 | **0** |
| CDN 依赖 | `https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js` |
| 加载策略 | **懒加载**（`index.html:761` 注释 + `index.html:3079-3087` 实现），仅在用户首次点击「走势」时注入 `<script>` |
| 预连接 | `<link rel="preconnect" href="https://unpkg.com" crossorigin>` + `dns-prefetch`（`index.html:762-763`） |

> 结论：CDN 依赖**不阻塞首屏**，首屏零外部请求是当前架构的既有优点，阶段 5 重构必须保留。

---

## 5. 代码规模基线

### 5.1 Git 快照

| 项 | 值 |
|---|---|
| 分支 | `main` |
| HEAD commit | `2dfe9e17775013b561c7ff78a7e6797b4e7d4f22` |
| 提交时间 | `2026-08-01 23:48:57 +0800` |
| 提交者 / 标题 | `wyw` / `fix: 修复33个测试失败和11个API端点问题` |
| 历史提交总数 | 104 |
| 工作区状态 | **不干净**：1 项未跟踪 `?? deliverables/`（本次工作产出目录，非源码改动） |

### 5.2 后端代码规模

| 目录 | `.py` 文件数 | 总行数 |
|---|---|---|
| `src/`（合计） | **51** | **15 554** |
| ├ `src/agents/` | 8 | 968 |
| ├ `src/analysis/` | 13 | 4 329 |
| ├ `src/core/` | 12 | 7 530 |
| ├ `src/monitor/` | 3 | 300 |
| ├ `src/strategies/` | 7 | 1 797 |
| └ `src/utils/` | 7 | 629 |
| `web/`（合计） | **19** | **4 576** |
| ├ `web/routes/` | 13（12 个路由模块 + `__init__.py`） | 4 120 |
| ├ `web/middleware/` | 3 | 187 |
| ├ `web/api.py` | — | 250 |
| └ `web/rate_limiter.py`（19 行）+ `web/__init__.py`（0 行） | — | 19 |
| 根目录脚本 | 2（`launch.py`、`install_deps.py`） | — |
| `scripts/` | 14 | 1 215 |
| **后端 Python 合计** | **86** | **21 345** |

> 注：`web/routes/` 实测为 **12 个路由模块**（agent / config / dashboard / fund / global_market / holdings / market / monitor / news / scripts / strategy / trade），非任务书所述的 14 个。

### 5.3 测试代码规模

| 项 | 值 |
|---|---|
| `tests/` 下 `.py` 文件数 | **61** |
| 其中 `test_*.py` | **59** |
| 其余 | `conftest.py`、`__init__.py` |
| 测试代码总行数 | **17 650** |
| 测试代码 / 后端代码比 | **0.83 : 1**（17 650 / 21 345） |

### 5.4 前端规模

| 项 | 值 |
|---|---|
| 文件 | `web/static/index.html`（**唯一**前端文件） |
| 行数 | **5 571** |
| 字节数 | **290 009 B**（283.2 KB） |
| `web/static/` 其他文件 | 无 |
| gzip 后传输 | 67 920 B（压缩率 76.6%） |

### 5.5 依赖数量

| 项 | 值 |
|---|---|
| `requirements.txt` 直接依赖数 | **21** |
| `pyproject.toml` `dependencies` | 12 |
| `pyproject.toml` `optional-dependencies.market-data` | 4 |
| 项目内置 `pylibs/` 包数 | 42 |

---

## 6. 前端技术债量化（供阶段 5 重构参考）

统计方法：`grep`/`sed` 按行号区间切片后计数；CSS 规则数用 `<style>` 块内 `{` 出现次数近似（含 `:root` 与 5 个 `@media` 包裹块）；函数数用行首 `function` / `const|let|var X = (` 声明计数。

### 6.1 文件结构分区

| 区块 | 行号区间 | 行数 | 占比 |
|---|---|---|---|
| `<head>` 元信息 | 1 – 9 | 9 | 0.2% |
| **`<style>` 样式块** | **10 – 760** | **751** | **13.5%** |
| 骨架（topbar / 侧边导航 / 布局容器） | 761 – 824 | 64 | 1.1% |
| **10 个页面 DOM** | **825 – 1689** | **865** | **15.5%** |
| **`<script>` 脚本块** | **1690 – 5569** | **3 880** | **69.6%** |
| `</body></html>` | 5570 – 5571 | 2 | 0.04% |
| **合计** | 1 – 5571 | **5 571** | 100% |

### 6.2 CSS 量化

| 指标 | 值 | 说明 |
|---|---|---|
| CSS 变量声明数 | **25** | 全部唯一（无重复定义） |
| CSS 规则数（近似） | **289** | `<style>` 块内 `{` 计数，含 `:root` 与媒体查询块 |
| `@media` 断点数 | **5** | — |
| `!important` 使用次数 | **15** | 优先级冲突信号 |
| CSS 平均每规则行数 | ~2.6 | 751 行 / 289 规则，样式密度低（多行格式化） |

### 6.3 JavaScript 量化

| 指标 | 值 | 说明 |
|---|---|---|
| 顶层函数声明（`function xxx`） | **108** | 与任务书所述「108 个函数」一致 |
| 箭头/表达式函数赋值（`const x = (`） | **50** | 合计 **158** 个可调用单元 |
| `fetch(` 直接调用 | **1** | 集中在单一 `api()` 封装内 |
| `api(` 调用点 | **75** | 业务侧全部经统一封装，**这是重构的有利条件** |
| `innerHTML` 赋值次数 | **158** | XSS 风险面 + 重构时 DOM 改造量 |
| `addEventListener` 次数 | **5** | 极低 —— 说明事件绑定几乎全靠内联属性 |
| `setInterval` 次数 | **4** | 轮询定时器，重构需接管生命周期 |
| DOM 节点总数（运行时实测） | **1 309** | — |
| `id="..."` 总数 | **157** | — |
| `<div>` 总数 | **694** | div 占比高，语义化标签少 |

### 6.4 10 个页面 DOM 分布

| # | 页面 id | 行号区间 | DOM 行数 | 占比 |
|---|---|---|---|---|
| 1 | `page-market`（行情，默认 active） | 825 – 865 | **41** | 4.7% |
| 2 | `page-ai`（AI 决策） | 866 – 1009 | **144** | 16.6% |
| 3 | `page-strategy`（策略） | 1010 – 1083 | **74** | 8.6% |
| 4 | `page-trade`（交易） | 1084 – 1164 | **81** | 9.4% |
| 5 | `page-position`（持仓） | 1165 – 1261 | **97** | 11.2% |
| 6 | `page-fund`（基金） | 1262 – 1457 | **196** | **22.7%（最大）** |
| 7 | `page-global`（全球市场） | 1458 – 1521 | **64** | 7.4% |
| 8 | `page-data`（数据） | 1522 – 1545 | **24** | **2.8%（最小）** |
| 9 | `page-monitor`（监控） | 1546 – 1588 | **43** | 5.0% |
| 10 | `page-config`（配置） | 1589 – 1689 | **101** | 11.7% |
| | **合计** | 825 – 1689 | **865** | 100% |

### 6.5 内联事件处理器（重构事件绑定改造量）

| 事件属性 | 出现次数 |
|---|---|
| `onclick=` | **82** |
| `onkeydown=` | **3** |
| `onchange=` | **3** |
| `oninput=` | **1** |
| **合计** | **89** |

> 对比：`addEventListener` 仅 **5** 次。即 **94.7% 的事件绑定是内联 HTML 属性**。
> 阶段 5 若改用框架/组件化，这 89 处内联处理器必须逐条迁移为事件委托或显式监听，且需注意它们引用的 108 个全局函数 —— **这是重构中工作量最大、最容易引入回归的一块**。建议阶段 5 立项时按「每页平均 9 处」估算改造量（`page-fund` 与 `page-ai` 因 DOM 最大，预计占比最高）。

---

## 7. 基线结论与对后续阶段的影响

### 7.1 可用于「优化前 / 优化后」对比的指标（已实测，可直接对比）

| 指标 | 基线值 | 对比有效性 |
|---|---|---|
| 测试用例数 / 通过率 | 1490 / 100% | ✅ 稳定可复现（两次复跑一致） |
| 测试耗时 | 30.78 s | ✅ 可用于检测测试膨胀或变慢 |
| 冷启动耗时 | 中位数 7 165 ms（区间 6.2–12.6 s） | ⚠️ 波动大，对比时须多次采样取中位数 |
| `/api/health` 状态 | 200，body 506 B | ✅ |
| 首页 HTML 字节数 | 290 009 B（gzip 67 920 B） | ✅ 硬指标，重构后必须对比 |
| 首屏请求数 | 6（全部同源，0 跨域） | ✅ |
| FCP | 桌面 96 ms / 移动 88 ms | ✅ |
| LCP | 桌面 96 ms / 移动 136 ms | ⚠️ 受后端接口延迟干扰，对比时须固定后端缓存状态 |
| CLS | 桌面中位数 0.23（0.03–0.37）/ 移动 0.00 | ⚠️ 方差大，须多次采样 |
| TBT | 0 ms | ✅ |
| DOM 节点数 | 1 309 | ✅ |
| 单文件行数 / CSS 规则数 / 函数数 / 内联处理器数 | 5 571 / 289 / 158 / 89 | ✅ 重构收益的量化依据 |
| API 路径数 | 93 路径 / 98 操作 | ✅ |
| 后端代码行数 | 21 345（src 15 554 + web 4 576 + scripts 1 215） | ✅ |

### 7.2 标注为「待基线」的项（未采集，需补测条件）

| 项 | 状态 | 补测所需条件 |
|---|---|---|
| Lighthouse 性能/可访问性/最佳实践/SEO 四项分数 | **待基线** | 安装 Lighthouse（`npm i -g lighthouse`，需联网）或用 `CHROME_PATH` 指向系统 Edge 执行；本机 Edge 已具备，仅缺 Lighthouse 本体 |
| Lighthouse 移动端模拟（4G 节流 + 4× CPU 降速） | **待基线** | 同上。当前数据为 localhost 无节流，**不能等同于线上体验** |
| 生产环境（Render Free）真实 LCP/TTFB | **待基线** | 需部署后从公网侧测量；Render 免费实例休眠唤醒会显著拉长 TTFB |
| 测试覆盖率（行/分支） | **待基线** | 需 `pytest --cov`（`coverage` 已有历史产物 `.coverage`、`coverage.json`，但为 2026-07-29 旧数据，**不可采信为当前覆盖率**） |
| 各 API 端点的 p50/p95 延迟 | **待基线** | 需压测（如 locust/k6），本次仅采到单次冷/热值 |
| 内存占用 / 长稳（内存泄漏） | **待基线** | 需长时间运行 + 采样 |
| 前端 bundle 分析（因是单文件，无构建产物） | 不适用 | — |

### 7.3 关键风险与阻塞问题（供阶段 1 优先处理）

1. **【阻塞级】依赖版本漂移 + 声明缺失**
   - `httpx` 实测 0.25.2 **低于** `requirements.txt` 声明的 `>=0.27.0`；
   - `fastapi` / `pandas` 实测跨大版本（0.136.1 / 3.0.3）远超声明（0.115.12 / 2.3.3）；
   - `pytest-asyncio`、`pytz` **未在任何依赖清单中声明**，导致开箱即用时 43 failed + 213 errors。
   - 影响：本地全绿 ≠ 线上可运行。Render/Fly 按 `requirements.txt` 构建，行为可能与本地不一致。

2. **【阻塞级】解释器环境污染**
   - `python312._pth` 使 `PYTHONPATH` 失效、cwd 不入 `sys.path`，且硬编码了**另一个项目** `d:\xm\mylibrary-rag` 的两个路径。
   - 影响：任何人首次接手都会踩「`import web` 找不到」的坑；且存在跨项目模块遮蔽风险。`launch.py` 的 docstring 已提到此问题但只做了规避，未根治。

3. **【高】CLS 不稳定（桌面 0.03–0.37）**
   - 数据异步填充导致布局抖动，多次超过 0.1 阈值。属真实体验缺陷，非测量噪声。

4. **【中】README 测试数据过期**
   - 声称 56 文件 / 1394 用例「全部通过」，实测 59 文件 / 1490 用例，且需补装 4 个依赖才全绿。

5. **【中】前端事件绑定高度内联**
   - 89 处内联处理器 vs 5 处 `addEventListener`，是阶段 5 重构的最大改造量来源。

6. **【低】`HEAD /` 返回 405**
   - 根路径仅注册 GET；对监控探针（部分工具默认发 HEAD）不友好。

### 7.4 对后续阶段的具体约束

- **阶段 1（代码审查）**：优先审 `web/rate_limiter.py`（slowapi 依赖）、`src/core/scheduler.py`（apscheduler/pytz 依赖）、`web/api.py` 的中间件顺序；依赖清单需补齐 `pytest-asyncio`、`pytz`，并对齐 `httpx` 下限。
- **阶段 3/4（多端与体验评估）**：可直接复用 `deliverables/gstack/_perf_probe.py` 与本次的桌面/移动双视口方法，保证前后可比。
- **阶段 5（UI 重构）**：硬性约束 —— ① 首屏外部请求必须保持 0（CDN 继续懒加载）；② gzip 后传输不超过 67 920 B 量级；③ DOM 节点数不超过 1 309 量级；④ 89 处内联处理器与 158 处 `innerHTML` 需在重构中一并治理；⑤ 必须修复 CLS。
- **阶段 7（重构后复测）**：CLS 与 LCP 必须**同法多次采样取中位数**，否则对比无效。

---

## 附录 A：本次产出的可复跑脚本

| 文件 | 用途 |
|---|---|
| `deliverables/gstack/_perf_probe.py` | Playwright + Edge 采集 FCP/LCP/CLS/TBT/请求明细，桌面+移动各 4 次采样，输出中位数 |

## 附录 B：本次未修改任何业务代码

- 未修改 `src/`、`web/`、`tests/` 下任何文件
- 未修改 `pytest.ini` / `pyproject.toml` / `requirements.txt`
- 未调整任何断言
- 唯一的环境变更是补装 4 个缺失依赖（见 1.3），以及新增本报告与附录 A 脚本
- 服务进程已全部关闭，端口 8123 已释放
