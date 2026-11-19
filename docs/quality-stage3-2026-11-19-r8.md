# 第八轮质量评估 · 阶段三：多端多场景页面检查

**执行日期**：2026-11-19｜**环境**：Playwright + Microsoft Edge（Chromium 内核），本地 `launch.py --port 8765`（`APP_ENV=dev`）
**工具**：`deliverables/gstack/_stage8_s3.py`（复用 r7 探针并补 1024×768；**56 组 = 4 页面 × 7 断点 × 2 主题**）
**矩阵明细**：`deliverables/gstack/_r8_stage3_matrix.json`；截图 `deliverables/gstack/_r8_s3_*.png`

## 断点与页面

| 类型 | 断点 |
|---|---|
| 桌面 | 1920×1080 / 1440×900 / 1366×768 |
| 平板 | **1024×768（本轮新增）** / 768×1024 |
| 移动 | 414×896 / 375×667（<500px 触控 48px 门槛生效） |

页面：index（24 个页面容器）/ stock-compare / ma-optimizer / factor-research；每页深色（出厂 `hud-theme`）与浅色回退双跑。

## 实测结果（阶段五 CSS 调整后复跑）

| 检查项 | 结果 |
|---|---|
| 横向滚动 | **56/56 hscroll=0** |
| 父盒溢出 | **56/56 全部 0**（含 mock 驱动渲染后复查） |
| WCAG 2.1 AA 对比度 | **56/56 全部 0 fail**（渐变/图片底按 unmeasured 记录，不误判） |
| JS 错误 | **56/56 全部 0** |
| 触控目标 <48px | 仅 375/414 移动断点 4 格命中 `span#connStatus`（7×7 / 8×8） |
| 功能渲染（mock 驱动） | stock-compare `rank_rows=4` ✓ / ma-optimizer `heatmap_cells=9` ✓ / factor-research `daily_rows=6, notes=2` ✓ |
| 新断点 1024×768 | index 渲染 101 张卡片，hscroll/溢出/对比度/JS 全 0 |

**defect_cells=4**，与 r6/r7 完全同源：`#connStatus` 是 8×8 连接状态圆点，`role="button" tabindex="0"` 但**无任何 click/keydown 处理器**，48px 触控规则不适用——记为**假阳性**；其语义误标作为 P3-1 继续挂在 backlog（与 r7 一致）。

## 阶段五前后一致性

| 指标 | 阶段五前 | 阶段五后（CSS 对齐 23 处） |
|---|---|---|
| defect_cells | 4（同源假阳性） | **4（同源假阳性）** |
| hscroll / 溢出 / JS / 对比度 | 全 0 | 全 0 |
| 功能渲染 | 全部正常 | 全部正常 |

> 阶段五的 6/10/14/18px → 8/12/16px 间距对齐未引入任何布局缺陷。
> 兼容性：Chromium 内核全量实测；Firefox / Safari 维持「待基线」（本机不可实测，不伪造通过）。
> 本报告由 AI 协作生成，关键决策请由工程负责人复核。
