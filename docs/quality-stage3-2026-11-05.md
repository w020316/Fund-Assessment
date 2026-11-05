# 阶段三：多端多场景页面检查报告

- 检查日期：2026-11-05
- 代码基线：`056bd09`（main，工作区干净；本阶段未改动任何 UI/业务代码）
- 服务环境：`python launch.py --port 8055` 本地单进程，页面走 `http://127.0.0.1:8055/`
- 驱动：Playwright 1.60.0 + Microsoft Edge（`channel=msedge`，主基线）、Firefox `firefox-1522`、WebKit `webkit-2287`
- 执行范围：**只检查、只报告**，未修复任何 UI 缺陷（本阶段无阻断型布局缺陷，未触发"记录后最小化处置"例外）
- 本阶段两个前置提交（与检查内容无关，均为独立 `chore`）：
  - `3881a7e chore(git): 忽略 .screenshots/ 走查截图目录`
  - `056bd09 chore(ci): 覆盖率门槛 75% → 78%`

> **执行模型声明：本阶段实际执行模型为 DeepSeek-V4.1-Flash，非用户指定的 kimi-K3.1pro。** 本报告全部结论、数值与截图均来自本模型实际执行的自动化走查，未引用外部结论、未假设任何未实测项通过。

---

## 一、结论

1. **最严重三类问题**
   1. **交易页"确认买入/卖出"按钮文字对比度不达标**：`.btn-danger` 白字 on `#F87171` 实测 **2.76:1**（375，12px）、`.btn-success` 白字 on `#34D399` 实测 **1.92:1**（1440，13px），WCAG AA 要求 4.5:1。根因是 HUD 主题把 `--color-up/--color-down` 重映射到浅色板，而按钮文字固定白色（[hud.css#L44-L45](file:///d:/xm/wz/Fund-Assessment/web/static/css/hud.css#L44-L45)、[index.html#L251-L254](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L251-L254)）。截图：`trade__360x640__warn.png`。
   2. **移动端触摸目标系统性偏小**：360/375 两个断点、20 个页面全部命中，合计 **76 处（去重 16 类选择器）**；最小为目标 8×8 的 `#connStatus`（`role="button" tabindex="0"`，每页一个）、18×18 的 `#st6PushEnabled` 复选框、72×16 的行内链接 `a '「基金管理」'`，其余为 314×44 / 325×44 的输入框（高度差 4px）。
   3. **无障碍语义缺失**：axe `critical` 6 处（`#globalMarketSelect`、`#scriptCategorySelect`、`#scriptSceneSelect` 三个 `<select>` 无 accessible name）+ `serious` 4 处（`#scriptLibraryBody`、`#dispatchSkills` 可滚动区域无键盘可达子元素），屏幕阅读器/键盘用户无法完成"国际行情切换"与"脚本库筛选"。截图：`global__360x640__warn.png`、`ai__1440x900__warn.png`。
2. **阻断项（✘）数量 = 0**。140 格中无横向滚动、无越界元素、无导航/底部 Tab 失效、无首屏关键内容缺失。
3. **覆盖**：20 页面 × 7 断点 = **140 格，覆盖 140/140（100%）**；状态 **82 ✔（58.6%）/ 58 ⚠（41.4%）/ 0 ✘**。
4. **是否具备进入阶段四**：**具备**（无阻断项，布局与导航在三引擎下均可正常使用）；建议阶段四开工前先闭环 P1 两条（对比度、select 命名），预计改动 ≤3 处样式/属性。

---

## 二、方法与判定口径

### 2.1 页面进入方式（真实点击，不改 hash）

每个单元格均从首页 `goto` 起，按真实交互路径进入：

1. 一级 Tab：优先 `.nav-item[data-page="<hud-* 聚合容器 id>"]`（桌面侧栏），不可见时退化为 `.bottom-tab-item[data-page="<group>"]`（移动底栏）；
2. 二级页：点击 `.hud-subtab[data-page="<二级页 id>"]`；
3. 等待 `networkidle`（≤6 s，超时忽略）后再静置 1.2 s 采样。

> 实测发现：侧栏 `.nav-item` 的 `data-page` 是聚合容器 id（`hud-market` / `hud-holdings` / `hud-strategy` / `hud-monitor` / `hud-settings`），移动底栏是 group id（`market` / `position` …）。两侧标识不一致，属于阶段二 R-03 引入的 `STAGE5_PAGE_MAP` 聚合改造遗留，未新增导航层级。

### 2.2 断点与设备模拟

| 断点 | 视口 | 模拟 |
|---|---|---|
| 手机竖屏窄 | 360×640 | `is_mobile=True`、`has_touch=True`、`device_scale_factor=2` |
| 手机竖屏基准 | 375×667 | 同上 |
| 平板竖屏 | 768×1024 | 普通视口 |
| 平板横屏/小笔记本 | 1024×768 | 普通视口 |
| 笔记本 | 1200×800 | 普通视口 |
| 桌面基准 | 1440×900 | 普通视口 |
| 大屏 | 1920×1080 | 普通视口 |

### 2.3 单格判定规则（每条都可复算）

| 判定 | 触发条件 | 级别 |
|---|---|---|
| 横向滚动 | `documentElement.scrollWidth > clientWidth + 1` | ✘ |
| 元素越界 | 可见元素 `rect.right > innerWidth + 1` 或 `rect.left < -1` | ✘ |
| 导航可用性 | `navVisible == 0 && bottomTabVisible == 0` | ✘ |
| 文本裁切 | 元素 `overflow-x: hidden/clip` 且 `scrollWidth > clientWidth + 2` 且宽度 >40px | ⚠ |
| 首屏标题 | 页面标题矩形不落在首屏内 | ⚠ |
| 触摸目标 | 交互元素 `width < 48 或 height < 48`（限移动断点） | ⚠ |
| 控制台错误 | `console.error` / `pageerror` 非空 | ⚠ |

单元格状态：出现任一 error → ✘；否则出现任一 warn → ⚠；否则 ✔。

### 2.4 采样与工具

- **axe-core**：vendored `deliverables/gstack/vendor/axe.min.js`（553,446 B），`axe.run(document, {runOnly:{type:'tag', values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})`，在 375 与 1440 执行（20 页 × 2 宽 = 40 次扫描）。
- **性能**：`add_init_script` 注入 `PerformanceObserver` 自采 `largest-contentful-paint` / `layout-shift` / `longtask`；**TBT 用 longtask 时长近似（非 Lighthouse 口径，仅供参考）**，采样窗口为进入页面后 1.2 s 静置期。
- **键盘**：`market` / `position` / `config` @1440 连续 `Tab` 12 次，记录每步 `activeElement` 选择器、`outline`、`boxShadow` 与尺寸。
- **200% 缩放**：以 `document.documentElement.style.zoom='2'` 近似（Chromium 无 `page.set_zoom`），观察是否产生横向滚动。
- **兼容性**：Firefox / WebKit 首次安装在默认路径 `D:\xm\dev-cache\ms-playwright` 失败（`EPERM: operation not permitted, mkdir ...\__dirlock`，沙箱不可写）；改用可写目录 `PLAYWRIGHT_BROWSERS_PATH=.tmp/ms-playwright` 后安装成功并完成实测（详见第十一节）。

---

## 三、页面清单与依赖接口（实测）

依赖接口为**运行时抓包实测**（每页真实点击后 2.5 s 内实际发出的 `/api` 请求，脚本 `.tmp/stage3_endpoints.py`，结果 `endpoints.json`）。

**6 个公共接口（每页都会请求）**：`/api/health`、`/api/market/index_realtime`、`/api/market/hot_stocks`、`/api/market/sector_ranking`、`/api/market/global_news`、`/api/global/indices`。

| # | 页面容器 id | 中文名 | 主 Tab | 页面专属依赖接口 |
|---|---|---|---|---|
| 1 | `hud-market` | 行情·聚合容器 | 行情（主） | 无（仅公共 6 个） |
| 2 | `market` | 行情总览 | 行情 | 无 |
| 3 | `review` | 短线复盘 | 行情 | `/api/review/board`、`/api/review/first_board?top=20`、`/api/review/journal?limit=100`、`/api/review/sentiment?days=5` |
| 4 | `global` | 国际市场 | 行情 | `/api/global/overview`、`/api/global/hk_hot`、`/api/global/us_hot` |
| 5 | `data` | 深度数据 | 行情 | `/api/market/research_reports?code=600519` |
| 6 | `hud-holdings` | 持仓·聚合容器 | 持仓（主） | `/api/holdings/sector-rotation/overview` |
| 7 | `position` | 持仓管理 | 持仓 | `/api/holdings/sector-rotation/overview` |
| 8 | `fund` | 基金管理 | 持仓 | `/api/fund/positions`、`/api/portfolio/groups` |
| 9 | `trade` | 交易执行 | 持仓 | 无（表单操作后才发请求） |
| 10 | `watchboard` | 盯盘 | 持仓 | `/api/portfolio/watchboard`、`/api/portfolio/webhook/config` |
| 11 | `risk` | 风险 | 持仓 | `/api/fund/positions`、`/api/portfolio/attribution?window=mtd`、`/api/portfolio/rebalance/config`、`/api/portfolio/risk?window=3m`、`/api/portfolio/webhook/config` |
| 12 | `hud-strategy` | 策略·聚合容器 | 策略（主） | `/api/strategy/list`、`/api/market/stock_kline?code=600519&count=120` |
| 13 | `strategy` | 策略中心 | 策略 | 同上 |
| 14 | `ai` | 智能分析 | 策略 | `/api/scripts/list`、`/api/strategy/list`、`/api/market/stock_kline?code=600519&count=120` |
| 15 | `gate` | 出手三问 | 策略 | `/api/strategy/list`、`/api/market/stock_kline?code=600519&count=120` |
| 16 | `dispatch` | 一句话调度 | 策略 | `/api/agent/skills`、`/api/strategy/list`、`/api/market/stock_kline?code=600519&count=120` |
| 17 | `hud-monitor` | 监控·聚合容器 | 监控（主） | `/api/market/hot_stocks_signal`、`/api/market/northbound` |
| 18 | `monitor` | 监控预警 | 监控 | 同上 |
| 19 | `hud-settings` | 设置·聚合容器 | 设置（主） | `/api/data/health` |
| 20 | `config` | 系统配置 | 设置 | `/api/data/health` |

主 Tab 共 5 个（行情/持仓/策略/监控/设置），二级页 15 个（#2–#5、#7–#11、#13–#16、#18、#20）。

---

## 四、页面 × 断点矩阵（20 × 7 = 140 格）

图例：✔ 无问题；⚠ 非阻断问题（明细见第五节）；✘ 阻断（本次 0 格）。

| 页面（容器 id） | 360×640 | 375×667 | 768×1024 | 1024×768 | 1200×800 | 1440×900 | 1920×1080 |
|---|---|---|---|---|---|---|---|
| 行情·聚合容器 (`hud-market`) | ⚠ T+C | ⚠ T+C | ⚠ C | ⚠ C | ⚠ C | ⚠ C | ⚠ C |
| 行情总览 (`market`) | ⚠ T+C | ⚠ T+C | ⚠ C | ⚠ C | ⚠ C | ⚠ C | ⚠ C |
| 短线复盘 (`review`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 国际市场 (`global`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 深度数据 (`data`) | ⚠ T | ⚠ T+J | ✔ | ✔ | ✔ | ✔ | ✔ |
| 持仓·聚合容器 (`hud-holdings`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 持仓管理 (`position`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 基金管理 (`fund`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 交易执行 (`trade`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 盯盘 (`watchboard`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 风险 (`risk`) | ⚠ T | ⚠ T | ✔ | ✔ | ⚠ N | ✔ | ⚠ N |
| 策略·聚合容器 (`hud-strategy`) | ⚠ T+J | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 策略中心 (`strategy`) | ⚠ T+J | ⚠ T | ✔ | ✔ | ✔ | ✔ | ⚠ J |
| 智能分析 (`ai`) | ⚠ T+C+J | ⚠ T+C | ⚠ C | ⚠ C | ⚠ C | ⚠ C | ⚠ C+J |
| 出手三问 (`gate`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 一句话调度 (`dispatch`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 监控·聚合容器 (`hud-monitor`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 监控预警 (`monitor`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 设置·聚合容器 (`hud-settings`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |
| 系统配置 (`config`) | ⚠ T | ⚠ T | ✔ | ✔ | ✔ | ✔ | ✔ |

标注含义：**T** 触摸目标 <48px；**C** 文本裁切判定命中（行情跑马灯 / AI 骨架屏，见 5.2、5.3）；**N** 基金名省略号截断（5.4）；**J** 控制台 `Failed to fetch`（切换时在途请求中止，5.5）。

各断点状态分布：

| 断点 | ✔ | ⚠ | ✘ |
|---|---:|---:|---:|
| 360×640 | 0 | 20 | 0 |
| 375×667 | 0 | 20 | 0 |
| 768×1024 | 17 | 3 | 0 |
| 1024×768 | 17 | 3 | 0 |
| 1200×800 | 15 | 5 | 0 |
| 1440×900 | 17 | 3 | 0 |
| 1920×1080 | 16 | 4 | 0 |
| **合计** | **82（58.6%）** | **58（41.4%）** | **0** |

---

## 五、非 ✔ 单元格明细（按问题类聚合）

### 5.1 触摸目标 <48px — 40 格（360 与 375，每页各 1 格）

- 覆盖：20 个页面 × 2 个移动断点，每页 1–5 处，单宽合计 38 处，两宽 **76 处**。
- 元素定位与实测尺寸：见第七节完整表。
- 表现：`#connStatus` 为 8×8 的 `role="button"` 状态点；表单输入框高度统一 314×44 / 325×44，均低于 48。
- 截图：每页 `{page}__360x640__warn.png`（`trade__360x640__warn.png` 为 5 处最密集的代表）。

### 5.2 行情跑马灯被判"裁切" — 14 格（`hud-market`、`market` 各 7 宽）

- 元素：`div.news-ticker`。

| 断点 | scrollWidth | clientWidth | 文本锚点 |
|---|---:|---:|---|
| 360 | 17,662 | 314 | 摩根大通上调麦当劳目标价至270美元，看好门店现代化改造 |
| 768 | 17,361 | 718 | 光大期货1105热点追踪：股指期货连续两日反弹，好日子来 |
| 1440 | 17,361 | 1,178 | 同上 |
| 1920 | 17,361 | 1,658 | 同上 |

- 判定为**口径误报**：`.news-ticker{overflow:hidden; white-space:nowrap}` + `.news-ticker-inner{animation:ticker-scroll 30s linear infinite}`（[index.html#L380-L389](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L380-L389)），溢出是滚动动画的设计意图，非裁切缺陷。
- 截图：`hud-market__360x640__warn.png`、`market__360x640__warn.png`、`market__1440x900__warn.png`。

### 5.3 AI 三张分析卡的骨架屏被判"裁切" — 7 格（`ai` 全部断点）

- 元素：`div.skeleton.skeleton-line`，示例 1440：`scrollWidth 515 / clientWidth 316`。
- 判定为**口径误报**：`.skeleton{position:relative; overflow:hidden}` + `.skeleton::after` 微光层从 `left:-100%` 平移，撑大了 `scrollWidth`（[index.html#L711-L716](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L711-L716)）。
- 但暴露一个真实体验问题：三张分析卡在未触发分析时**长期停留骨架屏**（1.2 s 静置后仍为骨架），无"请点击分析"类空态文案（见 P3-01）。
- 截图：`ai__360x640__warn.png`、`ai__1440x900__warn.png`。

### 5.4 风险页基金名被省略号截断 — 2 格（`risk` 1200 / 1920）

- 元素：`span.st6-rb-name`；1200 命中 5 处（`scrollWidth 169–177 / clientWidth 161`），1920 命中 3 处（`177–182 / 171`）。
- 示例文本：`002891 华夏移动互联混合(QDII)`、`006503 财通集成电路产业股票C`、`011611 华泰柏瑞科创50ETF联接C`。
- `.st6-rb-name{overflow:hidden; text-overflow:ellipsis; white-space:nowrap}`（[stage6.css#L114-L117](file:///d:/xm/wz/Fund-Assessment/web/static/css/stage6.css#L114-L117)）→ 属**有意省略**，但无 `title`/`aria-label` 承载全名，用户无法查看完整基金名称与代码。
- 截图：`risk__360x640__warn.png`（360 未截断，用于对照正常态）；1440 无截断，故 1200 无独立截图（缺陷由选择器与实测宽度可复现）。

### 5.5 控制台 `TypeError: Failed to fetch` — 6 格

`hud-strategy@360`、`strategy@360`、`ai@360`、`data@375`、`strategy@1920`、`ai@1920`。

- 表现：快速连续切换页面时，上一页在途请求被中止，`api()` 抛出 `TypeError: Failed to fetch`，例如 `/api/market/stock_kline?code=600519&count=120`、`/api/global/overview`。
- 复核：单独请求同一端点在 200 与 5.7 ms 正常返回（`curl` 实测），故判定为**切换时序造成的请求中止，非接口故障**；页面未出现错误 UI（对应区块显示空态）。
- 截图：`data__360x640__warn.png`、`strategy__360x640__warn.png`、`ai__360x640__warn.png`。

### 5.6 未命中项（0 例）

横向滚动 0、越界元素 0、导航/底栏不可用 0、首屏关键内容缺失 0、`titleVisible=false` 0。

### 5.7 加载 / 空 / 错误态（补充实测）

用浏览器侧 `route` 拦截（不改后端、不碰用户数据）对 `market` 页做两态取证：

| 态 | 制造方式 | 实测渲染 |
|---|---|---|
| 错误态 | `abort()` `/api/market/hot_stocks`、`/api/market/sector_ranking` | `#hotStockWrap` → "暂无数据 / 数据获取失败,请稍后重试"；`#sectorHeatmap` → "暂无板块数据 / 请稍后重试" |
| 空态 | `fulfill(200, "[]")` 同上两个接口 | 与错误态**文案相同**（"数据获取失败"），未区分"无数据"与"获取失败" |
| 加载态 | 正常进入（骨架屏 / `hud-status-loading`） | 存在且不引起布局抖动（CLS≈0） |

截图：`state-error__market__375x667.png`、`state-empty__market__375x667.png`、`state-error__market__1440x900.png`、`state-empty__market__1440x900.png`。

> 未覆盖：错误重试入口是否存在未验证（页面仅给文案，无重试按钮）；空态与错误态文案混用的影响面未逐页核查。

---

## 六、axe 可访问性汇总（40 次扫描）

### 6.1 按 impact 计数

| impact | 节点数 | 规则 |
|---|---:|---|
| critical | 6 | `select-name` ×6 |
| serious | 7 | `scrollable-region-focusable` ×4、`color-contrast` ×3 |
| moderate | 0 | — |
| minor | 0 | — |

### 6.2 规则与元素定位

| 规则 | impact | 节点 | 页面 / 断点 | 说明 |
|---|---|---|---|---|
| `select-name` | critical | 6 | `global` 375+1440（各 1）；`ai` 375+1440（各 2） | `#globalMarketSelect`、`#scriptCategorySelect`、`#scriptSceneSelect` 无 `<label>`/`aria-label` |
| `scrollable-region-focusable` | serious | 4 | `ai` 375+1440（各 1）；`dispatch` 375+1440（各 1） | `#scriptLibraryBody`、`#dispatchSkills` 可滚动但无可聚焦子元素 |
| `color-contrast` | serious | 3 | `trade` 375（1）；`trade` 1440（2） | 见 6.3 |

### 6.3 对比度实测数值

| 元素 | 断点 | 前景 | 背景 | 字号 | 实测比值 | AA 要求 | 缺口 |
|---|---|---|---:|---:|---:|---:|---:|
| `.btn-danger`（确认买入） | 375×667 | `#ffffff` | `#f87171` | 12 px | **2.76** | 4.5 | -1.74 |
| `.btn-danger`（确认买入） | 1440×900 | `#ffffff` | `#f87171` | 13 px | **2.76** | 4.5 | -1.74 |
| `.btn-success`（确认卖出） | 1440×900 | `#ffffff` | `#34d399` | 13 px | **1.92** | 4.5 | -2.58 |

根因：`body.hud-theme` 将 `--color-up → #F87171`、`--color-down → #34D399`（[hud.css#L23-L24](file:///d:/xm/wz/Fund-Assessment/web/static/css/hud.css#L23-L24)、[#L44-L45](file:///d:/xm/wz/Fund-Assessment/web/static/css/hud.css#L44-L45)），而 `.btn-danger/.btn-success` 使用白色文字 + 该变量背景（[index.html#L251-L254](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L251-L254)）。

### 6.4 主题与对比度背景

- 全站仅暗色单一主题（`:root` 深色变量，无 theme toggle，无 `prefers-color-scheme` 分支、无 `class=light`），因此**"亮色主题"不适用**，未做亮色对比度实测（不假设通过）。
- 暗色下正文 `#e6edf3` / 底 `#0d1117`、次文本 `#8b949e` / 底 `#0d1117` 未被 axe 判违规，即 ≥4.5:1（axe 未报即通过该项阈值）。

---

## 七、触摸目标 <48px 逐项清单（移动断点 360 / 375）

去重后 16 类，出现 76 次（每宽 38 次）。尺寸为 Playwright `getBoundingClientRect()` 实测。

| # | 选择器 | 实测宽×高 (px) | 出现页数 | 文本 / 说明 |
|---|---|---|---:|---|
| 1 | `#connStatus` | **8×8** | 20（每页） | 连接状态；`role="button" tabindex="0"` |
| 2 | `#dataCodeInput` | 314×44 | 1 | 深度数据 代码输入 |
| 3 | `#buyCode` | 314×44 | 1 | 交易 买入代码 |
| 4 | `#buyQty` | 314×44 | 1 | 交易 买入数量 |
| 5 | `#buyPrice` | 314×44 | 1 | 交易 买入价格 |
| 6 | `#buyStrategy` | 314×44 | 1 | 交易 策略选择 |
| 7 | `a`（行内链接） | **72×16** | 2 | 「基金管理」跳转链接 |
| 8 | `#st6PushEnabled` | **18×18** | 1 | 盯盘 推送开关（checkbox） |
| 9 | `#st6PushThreshold` | 89×44 | 1 | 盯盘 推送阈值 |
| 10 | `#klineCode` | 314×44 | 2 | 策略中心 / 策略聚合 代码输入 |
| 11 | `#aiStockInput` | 314×44 | 1 | 智能分析 股票代码 |
| 12 | `#gateCode` | 314×44 | 1 | 出手三问 代码 |
| 13 | `#gateMode` | 314×44 | 1 | 出手三问 取数模式（select） |
| 14 | `#gateSector` | 314×44 | 1 | 出手三问 板块 |
| 15 | `#dispatchQuery` | 314×44 | 1 | 一句话调度 输入 |
| 16 | `#watchlistInput` | 314×44 | 2 | 自选/监控 输入 |

375 断点尺寸整体 +11px 宽度（325×44），高度一致。逐页计数：`trade 5`、`gate 4`、`watchboard 3`、`data / hud-holdings / position / hud-strategy / strategy / ai / dispatch / hud-monitor / monitor 各 2`、其余 1。

---

## 八、Core Web Vitals 采样（375 / 1440）

方法：PerformanceObserver 自采（LCP 取末次 `largest-contentful-paint` startTime；CLS 累加非 `hadRecentInput` 的 `layout-shift`；**TBT 用 `longtask` 的 `(duration-50)` 求和近似**，非 Lighthouse 真 TBT）。单位 ms。

| 页面 | 375 LCP | 375 CLS | 375 TBT | 1440 LCP | 1440 CLS | 1440 TBT |
|---|---:|---:|---:|---:|---:|---:|
| hud-market | 160 | 0 | 0 | 108 | 0.000 | 0 |
| market | 52 | 0 | 0 | 64 | 0 | 0 |
| review | 64 | 0 | 0 | 68 | 0 | 0 |
| global | 60 | 0 | 0 | 60 | **0.188** | 0 |
| data | 76 | 0 | 0 | 52 | 0 | 0 |
| hud-holdings | 64 | 0 | 0 | 68 | 0 | 0 |
| position | 56 | 0 | 0 | 60 | 0 | 0 |
| fund | 64 | 0 | 0 | 68 | 0 | 0 |
| trade | 64 | 0 | 0 | 72 | 0 | 0 |
| watchboard | 64 | 0 | 0 | 60 | 0 | 0 |
| risk | 68 | 0 | 0 | 56 | 0 | 0 |
| hud-strategy | 60 | 0 | 0 | 68 | 0 | 0 |
| strategy | 64 | 0 | 0 | 64 | 0 | 0 |
| ai | 72 | 0.008 | 0 | 76 | 0.013 | 0 |
| gate | 52 | 0 | 0 | 64 | 0 | 0 |
| dispatch | 64 | 0 | 0 | 56 | 0 | 0 |
| hud-monitor | 64 | 0 | 0 | 56 | 0 | 0 |
| monitor | 64 | 0 | 0 | 64 | 0 | 0 |
| hud-settings | 60 | 0 | 0 | 52 | 0 | 0 |
| config | 60 | 0 | 0 | 64 | 0 | 0 |

- LCP 区间 52–160 ms（375）、52–108 ms（1440），远优于 2.5 s 阈值；TBT 全 0。
- 与上轮 CLS≈0.02 基线对比：本轮 40 个采样中 37 个为 0，`ai` 0.008/0.013 同量级，说明 `#indexCards`/`#sectorHeatmap` 定高策略（[index.html#L172-L200](file:///d:/xm/wz/Fund-Assessment/web/static/index.html#L172-L200)）持续有效。
- **`global@1440` 单次 0.188** 为唯一超 0.1 采样：位移发生在 t≈2.05 s，来源 `DIV.grid-2` 高度 **-155 px**（收缩）。复测 8 次（3 次全量 + 5 次分容器采样）均为 0，**未稳定复现**，按"偶发、待复现"记录（P2-05），不做"已修复"表述。

---

## 九、键盘可达性与 200% 缩放

### 9.1 Tab 顺序与焦点可见性（@1440，连续 12 次）

| 页面 | 观察 |
|---|---|
| `market` | 前 6 次落在 `.hud-subtab` / `.tab`，`outline: auto 3px rgb(175,194,212)`（可见）；第 7/8 次为 `#hotStockWrap`、`#sectorHeatmap`，`outline: solid 2px rgb(34,211,238)`（可见）。顺序 = 聚合子 Tab → 页内 Tab → 内容 → 操作，符合 DOM 顺序。 |
| `position` | 出现 `a` 元素焦点环 `outline: auto 1px rgb(16,16,16)`——与深色底 `#0d1117` 几乎同色，**焦点不可见**（P2-04）。 |
| `config` | `#cfg-admin-token` 聚焦时 `outline: none 3px`（无 outline），仅依赖边框变化，**焦点提示不足**（P2-04）。 |

其余 Tab 落点均为 `button.btn`（`auto 3px rgb(175,194,212)`，可见）。未发现 Tab 顺序错乱或焦点陷阱。

### 9.2 200% 缩放（`zoom=2` 近似）

| 页面 | 横向滚动 | docW / clientW | 越界元素 | 裁切 |
|---|---|---|---|---|
| `market` | 否 | 1440 / 1440 | 0 | 1（同 5.2 跑马灯） |
| `position` | 否 | 1440 / 1440 | 0 | 0 |
| `config` | 否 | 1440 / 1440 | 0 | 0 |

> 局限：`zoom` 只放大内容不影响布局视口，与浏览器"200% 缩放"（等效 720 CSS px 视口）不完全等价；该项仅作初筛，正式结论建议阶段四用 `--force-device-scale-factor` 或系统级缩放复核。

---

## 十、暗色 / 亮色

- 产品客观现状：单一暗色主题。`<meta name="theme-color" content="#0d1117">`，全部颜色为 `:root` 暗色变量；无切换入口、无 `prefers-color-scheme` 媒体查询、无亮色变量集。
- 结论：**亮色模式不适用**（非"通过"，而是"该形态不存在"）；如需在阶段四支持亮色，属新增需求，需重建一套亮色变量与对比度复核。

---

## 十一、浏览器兼容性（Edge / Firefox / WebKit）

| 浏览器 | 安装 | 375 结果 | 1440 结果 | 横向滚动 | 越界 | 页面级 JS 错误 |
|---|---|---|---|---|---:|---:|---:|
| Microsoft Edge（基线） | 系统已装，`channel=msedge` | 0 ✔ / 20 ⚠ | 17 ✔ / 3 ⚠ | 0 | 0 | 6 次 fetch 中止（5.5） |
| Firefox `firefox-1522` | 本轮安装成功 | 0 ✔ / 20 ⚠ | **17 ✔ / 3 ⚠** | 0 | 0 | 0 |
| WebKit `webkit-2287` | 本轮安装成功 | 0 ✔ / 20 ⚠ | **17 ✔ / 3 ⚠** | 0 | 0 | 0 |

- Firefox / WebKit 的 3 个 1440 ⚠ 与 Edge 完全一致：`hud-market`、`market`（跑马灯口径误报）、`ai`（骨架屏口径误报）；375 全部 ⚠ 亦全部为触摸目标（与 Edge 同源）。
- 差异说明：**Firefox 的 1440 触摸判定需排除**——Firefox 不支持 `isMobile` 模拟，本轮脚本在桌面宽度仍执行了触摸采样（`#connStatus` 等），已在统计中剔除；剔除后 1440 判定与 Edge 一致。
- **Firefox 不支持 `isMobile`**，故 375 采用"真实 375 视口 + CSS 媒体查询"，无移动 UA 与触摸标记；移动布局由媒体查询生效，底栏点击路径正常。
- **WebKit 为 Playwright 自带 Windows 版**，非 Safari/iOS 真机；结论不能等同 iOS Safari（尤其 `-webkit-` 前缀与触摸手势差异）。
- 首次安装失败原因（已解决，如实记录）：默认 `PLAYWRIGHT_BROWSERS_PATH=D:\xm\dev-cache\ms-playwright` 位于沙箱不可写目录，安装报 `EPERM: operation not permitted, mkdir 'D:\xm\dev-cache\ms-playwright\__dirlock'`；改到可写目录后安装成功。
- **Firefox / WebKit 均已实测，不存在"待基线"项**。未覆盖：iOS Safari 真机、Android Chrome 真机（无设备）。

---

## 十二、修复清单（P0–P3）

P0 = 阻断/不可用；P1 = 明确缺陷，影响核心操作或无障碍合规；P2 = 体验/一致性问题；P3 = 清理与口径修正。工作量：S ≤0.5 人日，M ≤2 人日。

### P0：0 条

140 格无阻断项，无横向滚动、无越界、无导航失效。**未触发"阻断型布局缺陷最小化处置"例外**。

### P1：2 条

| 编号 | 页面 / 断点 | 表现 | 根因（猜测/已定位） | 建议 | 工作量 |
|---|---|---|---|---|---|
| P1-01 | `trade` / 375、1440 | `.btn-danger` 白字 on `#f87171` = **2.76:1**（12–13px）；`.btn-success` 白字 on `#34d399` = **1.92:1**（13px），均低于 AA 4.5:1 | 已定位：`body.hud-theme` 重映射 `--color-up/--color-down` 为浅色板（hud.css L23-24、L44-45），按钮文字写死 `#fff`（index.html L251-254） | 二选一：①按钮文字改深色（`#0A0E14`）；②`.btn-danger/.btn-success` 背景改用深色语义色（`#ef4444`/`#22c55e`）。改后需复测 ≥4.5:1 | S |
| P1-02 | `global`、`ai` / 375、1440 | axe critical ×6：`#globalMarketSelect`、`#scriptCategorySelect`、`#scriptSceneSelect` 无 accessible name（屏幕阅读器读出"下拉列表"无上下文） | 未定位到具体行（三个 `<select>` 均无 `<label for>`/`aria-label`/`aria-labelledby`） | 三个 select 补 `aria-label`（或在旁加 `<label for>`）；建议同时补齐 `#scriptLibraryBody`、`#dispatchSkills` 的 `tabindex=0` + `aria-label`（与 P2-03 合并处理） | S |

截图与实测数值：P1-01 → `trade__360x640__warn.png` + 6.3 表；P1-02 → `global__360x640__warn.png`、`ai__1440x900__warn.png` + 6.2 表。

### P2：5 条

| 编号 | 页面 / 断点 | 表现 | 根因（猜测/已定位） | 建议 | 工作量 |
|---|---|---|---|---|---|
| P2-01 | 全部 20 页 / 360、375 | 触摸目标 <48px 共 **76 处 / 16 类**（最小 8×8） | `#connStatus` 视觉即状态点，未做命中区扩展；输入框统一 `44px` 高；checkbox 未放大；行内链接无 padding | 移动断点下给交互控件 `min-height:48px`；`#connStatus` 用伪元素/padding 扩命中区（保持视觉 8×8）；`#st6PushEnabled` 改为 24×24 且整行 label 可点 | M |
| P2-02 | `risk` / 1200、1920 | 基金名省略号截断（1200 五处、1920 三处），无全名入口 | `text-overflow:ellipsis` 有意省略，但缺 `title`/`aria-label` | 在 `.st6-rb-name` 上输出 `title="<code> <全名>"`，或允许两行展示 | S |
| P2-03 | `ai`、`dispatch` / 375、1440 | axe serious ×4：`#scriptLibraryBody`、`#dispatchSkills` 可滚动但键盘不可达 | 滚动容器无 `tabindex` 且子元素不可聚焦 | 补 `tabindex="0"` + `role`/`aria-label`（与 P1-02 同批） | S |
| P2-04 | `position`、`config` / 1440 | 焦点环不可见：`a` 为 `outline: auto 1px rgb(16,16,16)`；`#cfg-admin-token` 为 `outline: none` | 深色主题下未统一 `:focus-visible` 环（沿用默认 outline 色/样式） | 统一加 `:focus-visible{outline:2px solid var(--hud-accent,#22d3ee); outline-offset:2px}` | S |
| P2-05 | `global` / 1440 | CLS 单次 **0.188**（阈值 0.1），位移源 `#page-global .grid-2` 高度 -155px，t≈2.05s | 未定位：疑似列表回填后卡片高度收缩；**8 次复测均未复现** | 阶段四用 lab 多次采样 + 首屏容器定高（参照 `#indexCards` 做法）复现后再修 | M |

截图：P2-01 → 20 张 360 截图（第七节表）；P2-02 → `risk__360x640__warn.png`（正常态对照）；P2-03 → `ai__1440x900__warn.png`；P2-04/P2-05 无独立截图（焦点环与 CLS 为数值型证据，复现方式见第十四节）。

### P3：3 条

| 编号 | 页面 / 断点 | 表现 | 根因 | 建议 | 工作量 |
|---|---|---|---|---|---|
| P3-01 | `ai` / 全断点 | 三张分析卡在未触发分析时长期停留骨架屏，无"点击分析"空态提示 | 骨架为初始占位，未区分"未开始"与"加载中" | 未开始态用 `renderStatus('empty','尚未分析','输入代码后点击分析')` | S |
| P3-02 | `market` / 全断点 | 空数组（`200 []`）与请求失败显示同一文案"数据获取失败,请稍后重试" | `renderEmpty` 分支未区分空数据与异常（index.html L3308） | 空数据 → "暂无数据"；异常 → "获取失败，请重试" | S |
| P3-03 | 走查脚本 / — | 跑马灯、骨架屏微光被 "scrollWidth > clientWidth" 规则判为裁切（21 格误报） | 判定口径未排除"内部动画溢出" | 规则加白名单：`.news-ticker`、`.skeleton` 及其伪元素不参与裁切判定；脚本入库到 `scripts/` 便于复跑 | S |

---

## 十三、覆盖度与阶段四准入

| 指标 | 数值 |
|---|---|
| 覆盖单元格 | **140 / 140（100%）**（20 页面 × 7 断点，全部走真实点击） |
| 状态占比 | ✔ 82（58.6%）／⚠ 58（41.4%）／✘ **0** |
| 横向滚动 / 越界元素 | 0 / 0 |
| axe impact | critical 6、serious 7、moderate 0、minor 0 |
| 触摸目标超标 | 76 处（去重 16 类） |
| CWV 超标 | CLS 1 次（`global@1440` 0.188，未复现）；LCP/TBT 全部达标 |
| 兼容性 | Edge / Firefox / WebKit 三引擎实测一致；无"待基线"项 |
| 截图 | **36 张 / 3.71 MB**（20 张 360 全页 + 6 张 1440（3 张 ⚠ + 3 张 ✔ 代表图）+ 6 张 Firefox/WebKit + 4 张空/错误态） |

**阶段四准入判断：具备。** 依据：①阻断项 0，三引擎无布局破坏与导航失效；②所有页面在 7 个断点均能进入并呈现首屏内容；③性能 LCP/TBT 达标，CLS 仅 1 次偶发；④P1 两条集中在样式属性与无障碍属性，不影响页面可用性，可与阶段四并行或在阶段四首日闭环。

**建议阶段四前置条件**：先落地 P1-01、P1-02（同日可完成），避免阶段四真机验收时把对比度/无障碍问题与新功能缺陷混淆归因。

---

## 十四、证据索引与复现方式

### 14.1 证据文件

| 文件 | 内容 |
|---|---|
| `.screenshots/stage3-2026-11-05/results.json` | 140 格全量原始结果（状态、原因、裁切/越界元素、触摸项、截图名） |
| `.screenshots/stage3-2026-11-05/summary.md` | 自动聚合摘要（矩阵、58 行明细、axe、CWV、触摸清单、键盘、缩放） |
| `.screenshots/stage3-2026-11-05/endpoints.json` | 20 页实测依赖接口 |
| `.screenshots/stage3-2026-11-05/crossbrowser.json` | Firefox / WebKit 80 格原始结果 |
| `.screenshots/stage3-2026-11-05/*.png` | 36 张截图（命名 `{page}__{w}x{h}__{ok\|warn}.png`、`xb-{browser}__…`、`state-{mode}__…`） |

> `.screenshots/` 与 `.tmp/` 已被 `.gitignore` 忽略（截图可能含持仓画面且体积大），**不随本次提交入库**；如需入库请另行确认脱敏方案。

### 14.2 复现

```powershell
# 1. 起服务（本阶段结束已终止，端口 8055 已释放）
python launch.py --port 8055

# 2. 全量 140 格走查（Edge）
python .tmp/stage3_audit.py

# 3. 聚合摘要
python .tmp/stage3_summarize.py

# 4. 兼容性（须把浏览器装到可写目录）
$env:PLAYWRIGHT_BROWSERS_PATH="$PWD\.tmp\ms-playwright"
python -m playwright install firefox webkit
python .tmp/stage3_crossbrowser.py
```

单点复现入口：
- 对比度：`.tmp/contrast_probe.py`（对 `.btn-danger/.btn-success` 定向 `axe.run`）；
- 空/错误态：`.tmp/stage3_states.py`（`route` 拦截 `hot_stocks` / `sector_ranking`）；
- CLS：`.tmp/cls_probe.py`、`.tmp/cls_sources.py`（layout-shift 来源与容器高度时间序列）。

### 14.3 本阶段未覆盖项（如实登记）

1. 错误态"重试入口"是否存在未验证（仅确认文案）；
2. iOS Safari / Android Chrome 真机未测（无设备）；
3. 200% 缩放为 `zoom` 近似，非系统级缩放；
4. `global@1440` CLS 0.188 未稳定复现（8 次复测 0）；
5. 暗色以外的亮色主题产品不存在，未测。
