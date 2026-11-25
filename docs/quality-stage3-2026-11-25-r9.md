# r9 阶段三 · 多端多场景页面检查（2026-11-25，只测不修）

**环境**：Playwright + Microsoft Edge（channel=msedge）本地服务 `127.0.0.1:8055`（`APP_ENV=dev`，代码 = 线上 `0ca118b`，指纹已核实一致）。
**矩阵**：7 视口（360/375/768/1024/1200/1440/1920）× 20 页面容器 = **140 单元格**；360/375 按 is_mobile+DPR2 模拟真机。
**驱动**：复用历轮沉淀的 [scripts/page_audit.py](file:///D:/xm/wz/Fund-Assessment/scripts/page_audit.py)（真实点击导航、横向滚动/溢出/裁切、axe、CWV、键盘焦点、200% 缩放），产出 `.screenshots/page-audit/`（results.json + 40 张截图）。
**执行模型**：GLM-5.3-Flash（如实标注）。

## 一、总体结论

| 检查项 | 结果 |
|---|---|
| 横向滚动 / 元素溢出 | **0 处**（140 单元格全过） |
| 导航（一级 tab → 二级 subtab 真实点击） | 全过，无路由跳转异常 |
| 200% 缩放 | market/position/config 三页实测无溢出无裁切 |
| 键盘焦点顺序 | 3 项检测全过 |
| 控制台 JS 错误 | 127 条，其中 **429 限流占绝对多数**（走查脚本 7 视口连打触发匿名桶 10/min，属限流器正确工作，非功能缺陷）；剔除 429 后仅剩 API Error 提示类 |
| CLS | 中位数 0.0；**review@1440 = 0.202 超门禁(≤0.05)**、risk@1440 = 0.053 边缘 |
| axe 违规 | serious `scrollable-region-focusable` ×20；**critical `aria-required-children` ×2**（详见下） |
| 触控目标 <48px | 4 个 selector 被标记（详见下，其中 2 个为探针口径限制） |
| **Firefox 兼容（历轮待基线，本轮首次建立）** | **通过**：Firefox 150.0.2，1440/375 两断点 × 5 关键页，0 JS 错误、0 横向滚动、shell 层正常（`isMobile` 参数 FF 不支持，以缩视口近似，口径差异如实记录） |

## 二、问题清单（供阶段五修复，本阶段零代码改动）

| # | 级别 | 位置 | 表现（含复现） | 修复建议 |
|---|---|---|---|---|
| 1 | **P2** | review 页 @1440 | **CLS 0.202（门禁 ≤0.05）**。复现：1440×900 打开 → switchPage('review') → 等待 3-5s，复盘数据异步插入导致整版下移（截图 `review__360x640__warn.png` 系列 + cwv 记录） | 阶段五：对复盘骨架区预留 min-height / 占位卡片，位移源用 LayoutShift sources 归因后精准预留；修后 CLS 实测 ≤0.05 |
| 2 | **P2** | risk 页 @1440 | CLS 0.053，边缘超标 | 同上，随 #1 一并治理 |
| 3 | **P2** | [index.html:3191](file:///D:/xm/wz/Fund-Assessment/web/static/index.html) `.aep-retry/.aep-close` | **桌面端触控/点击目标过小**：1440 下 retry 48×25、close **16×16**（S9-18 的 44px+热区规则在 `max-width:640` 内，桌面不生效）。实测 close 元素外上方 2px 可命中（移动端热区生效 ✔），桌面无热区 | 热区规则提升为全断点生效（左侧邻元素为非可点击文本 aep-body，扩展无重叠风险）；close 需 -16px 扩展或桌面最小尺寸 44×44 |
| 4 | **P2** | `.qf-disc-chips`（review 页） | **axe critical**：`role="list"` 的 5 个 chip 子元素（span）无 `role="listitem"`，违反 aria-required-children | chip span 补 `role="listitem"` |
| 5 | **P2** | [index.html:1081](file:///D:/xm/wz/Fund-Assessment/web/static/index.html) `#connStatus` | `role="button" tabindex="0"` 的 7×7px 状态圆点，**无任何点击行为**——假按钮：屏幕阅读器宣布为按钮、键盘可聚焦但 Enter 无效果，且是 40 个单元格的触控警告源 | 移除 `role="button" tabindex="0"`（保留 aria-label/title 的状态语义），或为其实现真实行为 |
| 6 | **P3** | hud-market @360/375/768 | 3 处文本裁切：`.wb-stat-s`（首板·连板统计串 152→134px）、`.wb-ph-sub`（北向说明 207→191px）、`.wb-pf-name`（长基金名 224→214px）——均为截断式收窄，移动端信息可读性受损 | 补 `title` 全文提示（已有约定的文本截断规范）或窄断点换行 |
| 7 | **P3** | `.tab-items`（20 处出现） | axe serious：可滚动区域不可键盘聚焦 | 补 `tabindex="0"` + 可访问名称（承项目既有无障碍约定） |

**触控探针口径说明（如实记录）**：page_audit 以元素 rect 判 <48px，无法感知 `::after` 透明热区——`#apiErrorRetry/#apiErrorClose` 的 31 次警告中，移动端实际命中区已 48×48（elementFromPoint 边缘外 2px 命中实测 ✔）；`#qfWbEditBtn`（86×44）在 ≤500px 断点热区同样生效。`#connStatus` 为真问题（见 #5）。

**429 限流说明**：127 条控制台错误中绝大多数为 `Failed to load resource: 429`，由走查脚本 20 页 × 7 视口高速连打触发匿名读桶（10/min）——限流器按设计工作；真实用户单页浏览不会遇到。无「绕过限流」处置，如实记录。

## 三、页面 × 分辨率矩阵（摘要）

- **全过（✔ 8 格）**：除下列 ⚠ 外的所有非 429 干扰单元格
- **⚠ 132 格**：其中 ~125 格唯一问题为 429 限流噪声；真实问题集中于：review/risk 的 CLS（1440）、hud-market 裁切（360/375/768）、触控目标标记（全断点）
- 无 FAIL 级单元格；无布局塌陷、无导航失效、无暗色/亮色不一致（全站为固定暗色终端主题，无模式切换开关——与终端化设计稿一致）

## 四、阶段小结

P0/P1 缺陷为零；2 个 P2 CLS 超标 + 3 个 P2 无障碍/触控问题 + 2 个 P3 记录在案，全部转阶段五迭代修复（阶段三零代码改动，符合只测不修约定）。Firefox 首次基线建立并通过。**判定：通过**，进入阶段四。
