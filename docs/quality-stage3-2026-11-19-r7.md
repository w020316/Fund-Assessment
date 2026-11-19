# 第七轮质量评估 · 阶段三：多端多场景页面检查

**执行日期**：2026-11-19｜**环境**：Playwright + Microsoft Edge（Chromium 内核），本地 `launch.py --port 8765`（`APP_ENV=dev`）
**工具**：`deliverables/gstack/_stage7_s3.py`（复用 r6 `_stage6_s3` 探针，口径逐字不变；48 组 = 4 页面 × 6 断点 × 2 主题，可重跑）
**矩阵明细**：`deliverables/gstack/_r7_stage3_matrix.json`；截图 `deliverables/gstack/_r7_s3_*.png`

## 断点与页面

| 类型 | 断点 |
|---|---|
| 桌面 | 1920×1080 / 1440×900 / 1366×768 |
| 平板 | 768×1024 |
| 移动 | 375×667 / 414×896（<500px 触控 48px 门槛生效） |

页面：index（20+ 容器 SPA）/ stock-compare / ma-optimizer / factor-research；每页深色（出厂 `hud-theme`）与浅色回退双跑。

## 实测结果

| 检查项 | 结果 |
|---|---|
| 横向滚动 | **48/48 全部 hscroll=0** |
| 父盒溢出 | **48/48 全部 0**（含 mock 渲染后复查） |
| WCAG 2.1 AA 对比度 | **48/48 全部 0 fail**（渐变底文案按 unmeasured 记录，不误判） |
| JS 错误 | **48/48 全部 0** |
| 触控目标 <48px | 仅移动断点 4 格命中 `span#connStatus`（8×8）——见下方判定 |
| 功能渲染（mock 驱动） | stock-compare `rank_rows=4` ✓ / ma-optimizer `heatmap_cells=9` ✓ / factor-research `daily_rows=6, notes=2` ✓ |

**defect_cells=4**，与 r6 完全一致，全部来自同一元素。

## `#connStatus` 判定（承 r6「假阳性」结论，本轮补充根因）

`index.html:1044` 的 `span.status-dot` 为 8×8 连接状态圆点，标注了 `role="button" tabindex="0"`。复核全站 JS：**该元素无任何 click/keydown 处理器**——它不是可操作控件，48px 触控规则不适用，故 4 格记假阳性。但其语义标注本身构成一个真实的无障碍瑕疵：对屏幕阅读器宣告了一个「按下无反应」的按钮。

**处置**：登记为 P3-1「非交互元素误标 role=button」（修复建议：移除 `role="button" tabindex="0"` 或补真实行为），列入 backlog，不在检查阶段修改业务代码。

## 与 r6 阶段三对比

| 指标 | r6 | r7 | 变化 |
|---|---|---|---|
| defect_cells | 4（同源假阳性） | 4（同源假阳性） | 0 新增缺陷 |
| 页面容器 | 20 | 24（+prediction/global 等） | 新增页面全绿 |

> 结论：r7 新增的预测/全球市场面板在全部 6 断点 × 2 主题下未引入任何布局/对比度/JS 缺陷。
> 本报告由 AI 协作生成，关键决策请由工程负责人复核。
