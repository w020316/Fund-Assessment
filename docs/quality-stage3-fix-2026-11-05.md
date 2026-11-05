# 阶段三收尾闭环：修复与复测报告

- 修复日期：2026-11-05
- 代码基线：`9d1464f`（阶段三报告提交后），本轮修复 commit 见文末
- 服务环境：`python launch.py --port 8055` 本地单进程，页面走 `http://127.0.0.1:8055/`
- 驱动：Playwright 1.60.0 + Microsoft Edge（`channel=msedge`）
- 复测脚本：`scripts/page_audit.py`（走查主体）、`scripts/a11y_probe.py`（本轮定向探针，均已入库）

> **执行模型声明：本阶段实际执行模型为 DeepSeek-V4.1-Flash，非用户指定的 kimi-K3.1pro。** 本报告全部数值与截图均来自本模型实际执行，未引用外部结论、未假设任何未实测项通过。

---

## 一、结论

1. **修复 7 项，P3 backlog 记录 1 项**：P1 对比度（含 hover/active/disabled 四态）、P1 三个 select 可访问名称、P2 触摸目标透明热区、P2 focus-visible 焦点环、P2 滚动区键盘可达、P2 基金名 title、P3 探针裁切误报口径；"空数组与请求失败共用文案"按要求只登记不修。
2. **关键前后对比**

| 指标 | 改前 | 改后 | 口径 |
|---|---|---|---|
| `.btn-danger` 对比度（默认 / hover / active / disabled） | 2.77 / 2.68 / 2.68 / 2.28 | **6.77 / 5.67 / 5.67 / 5.17** | 深色文字 `#0b1220`，底色 `#F87171` 保留 |
| `.btn-success` 对比度（默认 / hover / active / disabled） | 1.92 / 1.88 / 1.88 / 1.71 | **9.74 / 8.06 / 8.06 / 7.30** | 底色 `#34D399` 保留 |
| axe `select-name`（critical） | 6 处节点（3 控件 × 2 视口） | **0** | `axe.run` runOnly rule |
| axe `critical / serious` 合计 | 6 / 7 | **0 / 0** | 20 页 × 375/1440 |
| 触摸目标 <48px（360+375 双视口） | 76（原始矩形） | **有效命中区 28**（原始矩形 72） | `elementFromPoint` 4px 步长采样 |
| 焦点环实测 | `#cfg-admin-token` 无环；链接/状态点为 UA 深色环 | **三者均 `rgb(34,211,238)` 2px / offset 2px**，对 `#161b22` 9.57:1 | 等 transition 结束后读取 |
| 风险页基金名 title | 0/9 | **9/9（100%）** | 1200×800 与 1920×1080 |
| 走查"文本裁切"误报 | 23 格 / 57 处 | **0 格 / 0 处**（quick 60 格） | 修正后判据 |
3. **全量回归**：`python -m pytest tests/ -q` **2009 passed / 0 failed**；覆盖率 **81.55%**（门槛 78%）；`python -m compileall -q src web launch.py scripts` 通过；`ruff --select E9,F63,F7,F82 src web tests scripts` 通过，全量 ruff 无新增（新增 `scripts/` 两个文件 0 报错）。
4. **线上是否生效**：见第五节，push 后对 `https://fund-assessment.onrender.com` 实测。
5. **还剩什么**：7 项之外未修（见第四节），其中 44px 原生输入控件受"视觉尺寸不改"约束保留，风险页 1200/1920 的 title 已全量补齐。

---

## 二、逐项修复（根因 / 方案 / 验证）

### 2.1 P1-01 买卖按钮对比度（`7735948` + `e16adff`）

- **根因**：`body.hud-theme` 把 `--color-up → #F87171`、`--color-down → #34D399`（[hud.css#L44-L45](file:///d:/xm/wz/Fund-Assessment/web/static/css/hud.css#L44-L45)），而 `.btn-danger/.btn-success` 文字固定白色，实测 2.77 / 1.92。提交中 `withLoading()` 会写 inline `opacity:.6`，整元素与背景合成后降到 2.28 / 1.71；hover/active 因 `opacity:.9` 也只有 2.68 / 1.88。
- **方案**：底色按要求一律保留，文字改为 `#0b1220`；hover 维持 `.9`；新增 `.btn-danger:disabled, .btn-success:disabled { opacity:0.85 !important }` 覆盖 inline 加载态，其余按钮保持 `.6` 外观。
- **验证**：同一探针在 1440×900 上依次测默认 / 悬停 / 鼠标按下 / 模拟提交中四态，并逐层合成 `.card(#161b22)` 背景：danger 6.77 / 5.67 / 5.67 / 5.17，success 9.74 / 8.06 / 8.06 / 7.30，全部 ≥4.5:1。

### 2.2 P1-02 三个筛选下拉可访问名称（`159e247`）

- **根因**：axe `select-name` 命中 `#globalMarketSelect`、`#scriptCategorySelect`、`#scriptSceneSelect`，无 accessible name。
- **方案**：补 `aria-label`："国际个股所属市场 / 话术库分类筛选 / 话术库场景筛选"；结构与样式未动。
- **验证**：`axe.run` runOnly rule `select-name`，智能分析页（2 个控件）与国际市场页（1 个）在 1440×900 下违规 **0**（原 6 节点 / 4 条目）。

### 2.3 P2-01 触摸目标透明热区（`3deacb2`）

- **根因**：360/375 两个断点共 76 处可点元素原始矩形 <48×48：`#connStatus` 8×8、`#st6PushEnabled` 18×18、行内链接 72×16、其余 44px 高表单控件。
- **方案**（视觉尺寸一律不改、不改变布局占位）：
  - `#connStatus`：绝对定位 `::after` **56×56**。移动端顶栏仅 46px 高，48 的框以圆点为中心会被视口上沿裁掉约 2px，取 56 后采样区实测仍 ≥48×48。
  - `.notice-info a`：`padding-block:16px` + 等量负 `margin-block`，行盒高度不变，命中高 19.2 → 51.2。
  - `.st6-push-toggle` / `.st6-threshold-toggle`：`::after` 48×48，分别以复选框中心（label 左边界 +9px）与 label 全宽为中心。
  - 原生 `input/select` 是替换元素，伪元素不进入其命中区；44px 高按"视觉尺寸不改"保留（满足 WCAG 2.5.5 AAA 的 44×44）。
- **验证**：同一 360/375 双视口、同一采样脚本：
  - 改前（注入 `BASELINE_CSS` 关闭本轮热区）**76** 处有效命中区 <48；
  - 改后 **28** 处（14×2），全部为 44px 高原生输入/选择控件；
  - 本轮新增热区逐个复核 **44/44** 达到 ≥48×48，遮挡相邻可点元素 **0** 次。

### 2.4 P2-02 focus-visible 焦点环（`5ad2186`）

- **根因**：`.input,.select` 的 `outline:none` 抹掉了 `#cfg-admin-token` 的焦点环；position 页行内链接与 `#connStatus`（`span[role=button]`）沿用 UA 深色默认环，在深色背景上不可见；`outline` 参与 `.input` 的 `transition: all .2s`，聚焦瞬间读数会落在过渡中间值。
- **方案**：统一 `:focus-visible { outline:2px solid #22d3ee; outline-offset:2px }`，覆盖 input/select/textarea/a/button/[role=button]/[tabindex]/tab/二级 tab/移动底栏。
- **验证**：`#cfg-admin-token`、position 行内链接、`#connStatus`（Tab 聚焦）三者均为 `rgb(34,211,238)` 2px / offset 2px 且 `matches(':focus-visible')`；`#22d3ee` 对 `#161b22` 9.57:1、对 `#0d1117` 10.47:1（≥3:1）。

### 2.5 P2-03 滚动区键盘可达（`77fc2c6`）

- **根因**：axe `scrollable-region-focusable` 命中 `#scriptLibraryBody`(400px) 与 `#dispatchSkills`(520px)，键盘无法聚焦滚动。
- **方案**：补 `tabindex="0"` + `role="region"` + 可访问名称（"话术库列表(可滚动)"、"一句话调度技能列表(可滚动)"）。
- **验证**：同一走查中该规则 4 条节点全部消失（2 容器 × 375/1440）。

### 2.6 P2-04 风险页基金名 title（`02b316d`）

- **根因**：`.st6-rb-name` 单行 ellipsis，1200 视口 8 行、1920 视口 3 行显示不全。
- **方案**：渲染时加 `title="代码 基金名"`（与可见文本同源，均已 `esc()`），不新增 DOM、不改样式。
- **验证**：1200×800 与 1920×1080 各 9 个 `.st6-rb-name`，带 title 9 个；截断 8 / 3 处全部有 title 兜底。

### 2.7 P3 探针裁切误报口径（`f32927c`）

- **根因**：旧判据"`overflow-x:hidden/clip` 且 `scrollWidth > clientWidth + 2` 且宽 >40px"会把伪元素撑宽的动画容器算成文本裁切：`.news-ticker` 跑马灯（scrollWidth 17361 / clientWidth 314）、`.skeleton` 微光（515 / 316），占全量 23 格 / 57 处 ⚠ 的主要来源。
- **方案**：`scripts/page_audit.py` 改为"只判定含直接文本节点的元素 + `.news-ticker/.skeleton` 类白名单"；axe 在 `deliverables/`（gitignore）缺失时跳过并告警，CI 可运行。
- **验证**：quick 口径（375/768/1440，60 格）复测，裁切警告 **0**；剩余 ⚠ 23 格 = 20 格触摸（44px 原生控件）+ 3 格切页时 `Failed to fetch`（切页中止在途请求，端点独立请求正常）。

---

## 三、复测结果明细

### 3.1 按钮四态（`scripts/a11y_probe.py`）

| 按钮 | 状态 | 计算 opacity | 文字 / 底色 | 对比度 |
|---|---|---|---|---|
| `.btn-danger` | 默认 | 1 | `#0b1220` / `#F87171` | 6.77 |
| | hover | 0.9 | 同上 | 5.67 |
| | active | 0.9（无独立 `:active` 规则，按压时仍命中 hover） | 同上 | 5.67 |
| | disabled（提交中） | 0.85 | 同上 | 5.17 |
| `.btn-success` | 默认 | 1 | `#0b1220` / `#34D399` | 9.74 |
| | hover | 0.9 | 同上 | 8.06 |
| | active | 0.9 | 同上 | 8.06 |
| | disabled（提交中） | 0.85 | 同上 | 7.30 |

### 3.2 触摸目标（360/375 双视口，有效命中区）

| 类别 | 改前 | 改后 | 说明 |
|---|---|---|---|
| `#connStatus`（8×8，每页 1 个） | <48 | **48×48**（56×56 热区经视口裁切后） | 40/40 达标 |
| `#st6PushEnabled`（18×18） | <48 | **52×48** | 2/2 达标 |
| `#st6PushThreshold`（89×44） | <48 | **52×48** | 2/2 达标 |
| 行内链接（72×16） | <48 | **72×51.2** | 1/1 达标 |
| 原生输入/选择控件（44 高） | <48 | 44（保留） | 28 处，受约束不扩 |
| 合计 | 76 | **28** | 全部为原生表单控件 |

### 3.3 走查状态（quick 60 格）

| 状态 | 结果 |
|---|---|
| ✔ | 37 |
| ⚠ | 23（20 格触摸 + 3 格切页 `Failed to fetch`） |
| ✘ | **0** |

> 触摸 ⚠ 的判据仍是"原始矩形 <48"，与阶段三 76 处的口径一致；本轮的透明热区不改变原始矩形，故该警告会保留，其真实可点性见 3.2。

---

## 四、未修复 / 留观察

| 项 | 级别 | 状态 | 说明 |
|---|---|---|---|
| 空数组与请求失败共用文案 | P3 | **仅登记** | 接口返回 `[]` 与请求失败都渲染成"暂无数据"，用户无法区分；本轮按要求不改 |
| `global@1440` CLS 0.188 | 观察 | **未宣称修复** | 8 次复测（3 全量 + 5 分容器采样）均为 0，属偶发；不下"已修复"结论 |
| 44px 原生输入/选择控件 | P2 约束 | **保留** | 伪元素无法扩原生控件命中区，扩宽即改变视觉尺寸；已满足 WCAG 2.5.5 AAA 44×44，Apple HIG 48 未达 |
| `#gateSector` 在 360/375 被固定底栏遮挡 | P2（新发现） | **未修** | 测量时有效命中高仅 0/4px，`#buyStrategy`@360 亦部分遮挡；属既有内容区与固定底栏重叠，非本轮改动引入 |
| `.tab.active` 等其余配色 | — | 未动 | 本轮约束"配色体系不动"，且 axe 复测色对比为 0 违规 |

---

## 五、回归与线上验证

| 项目 | 结果 |
|---|---|
| `python -m pytest tests/ -q --cov=src --cov=web --cov-fail-under=78` | **2009 passed / 0 failed**；TOTAL 81.55% |
| `python -m compileall -q src web launch.py scripts` | 通过 |
| `ruff --select E9,F63,F7,F82 src web tests scripts` | 通过 |
| `ruff check src web tests`（全量，仅报告） | 182 条，均为存量；新增 `scripts/` 两文件 0 条 |
| 线上 `GET /api/health` | 见下方实测记录 |
| 线上静态页断言 | 见下方实测记录 |

### 5.1 线上验证记录

（push 后执行，结果回填于本节）

- 部署：Render Free，`https://fund-assessment.onrender.com`，push `main` 自动部署（`9d1464f..f32927c`）
- 冷唤醒耗时：首次 `GET /api/health` 返回 200 用时 **44 s**（响应体 `metrics.uptime_seconds=24.0`，说明 44 s 中含容器拉起 + Render 构建/传播）；随后轮询 `/static/index.html` **18 s** 后出现新版本标记（`color:#0b1220`），即静态资源已在线上生效
- `/api/health`：**200**，`{"status":"ok","akshare":true,"core_modules":true,"ai_ready":true, ...}`，`metrics.uptime_seconds: 24.0`
- 静态资源断言（线上 `/static/index.html` + `stage6.css` + `stage6-portfolio.js`）：**11/11 全部 True**
  - `aria-label="国际个股所属市场"` / `aria-label="话术库分类筛选"` / `aria-label="话术库场景筛选"` → True
  - `.btn-success/.btn-danger` 深色文字 `color:#0b1220` → True
  - `:disabled` 态 `opacity:0.85 !important` → True
  - `#connStatus::after` 56×56 热区 → True
  - `outline: 2px solid #22d3ee`（focus-visible）→ True
  - `scriptLibraryBody` / `dispatchSkills` 的 `tabindex="0"` → True
  - 风险页 `class="st6-rb-name" title=` 渲染 → True
  - `.st6-push-toggle::after` 热区（`stage6.css`）→ True
  - 资源长度：`index.html` 334,938 B / `stage6-portfolio.js` 52,521 B / `stage6.css` 13,872 B

---

## 六、复现

```bash
python launch.py --port 8055
python scripts/a11y_probe.py --base http://127.0.0.1:8055/ --out .screenshots/stage3-fix-2026-11-05
python scripts/page_audit.py --viewports quick --out .screenshots/stage3-fix-2026-11-05
```

产物：`.screenshots/stage3-fix-2026-11-05/`（`a11y.json`、`results.json` 与 16 张截图；该目录仍在 gitignore 内）。

## 七、commit 列表

| commit | 内容 |
|---|---|
| `7735948` | fix(ui): 交易页买卖按钮改用深色文字，对比度 2.76/1.92 → 6.77/9.74 |
| `e16adff` | fix(ui): 补交易按钮 disabled 态对比度（3.19:1 → 5.17:1） |
| `159e247` | fix(a11y): 三个筛选下拉补可访问名称 |
| `3deacb2` | fix(a11y): 触摸目标透明热区扩容（有效命中区 <48px 76→28） |
| `5ad2186` | fix(a11y): 统一 focus-visible 高对比焦点环 |
| `77fc2c6` | fix(a11y): 话术库与调度技能滚动区补键盘可达 |
| `02b316d` | fix(ui): 风险页基金名省略号处补全名 title |
| `f32927c` | chore(audit): 走查脚本入库并修正裁切误报口径 |
