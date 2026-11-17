# 第六轮质量评估 · 阶段二批次 D 修复记录（移动端触摸目标 48px 收口）

**范围**：`77feafa..5968d2b`｜**提交**：1 个 fix｜**回归**：**3075 passed / 1 skipped / 0 failed / 90.50s**
**来源**：阶段三真机矩阵实测（不是评审推测）

---

## 一、问题与根因

**修复前**：4 页共 **58 个** <48px 可点目标，尺寸集中在 44~46px。

| 页面 | 唯一 <48px 目标 | 典型项 |
|---|---|---|
| `index` | 4 | `.hud-subtab`（`role=button`）44px × 3 |
| `stock-compare` | 10 | `#stockCodes`/`#adminToken` 44、`#startDate`/`#endDate` 46、`#runCompare` 44、`nav a` 44 |
| `ma-optimizer` | 20 | `#stockCode`、`#shortMin/Max`、`#longMin/Max/Step`、`#startDate`/`#endDate`、`#runOptimizer`、`nav a` |
| `factor-research` | 24 | `#csvFile`、`#groups`、`#horizon`、`#minAmount`、`#minListing`、`#window`、`#direction`、`#trainRatio`、`#runResearch`、`#downloadTemplate`、`#downloadReport`、`nav a` |

**根因两条**：

1. 三个辅助页各自内联 `<style>` 的 `@media (max-width:640px)` 里写着**上一轮 44px 标准**：
   ```css
   input, button { min-height: 44px; }
   nav a { display: inline-flex; align-items: center; min-height: 44px; }
   ```
   而且 **`select` 完全不在选择器里**（`#direction` / `#trainRatio` 是 select）。另外 `#startDate/#endDate` 的自然高度是 **46px**，不受 `min-height:44` 约束 —— 所以必须写 **48**，写 46/47 都不够。
2. 首页 `.hud-subtab` 在 `aurora-theme.css:352` 是**固定** `height:44px`。固定值会压住 `min-height`，且 aurora 在 `hud.css` **之后**加载。

---

## 二、修法

1. **三个辅助页**：`44 → 48`，并补 `select`；`nav a` 顺带覆盖 `footer a`。三页保持同一段规则。
2. **首页**：在 `hud.css` 新增移动端段（沿用 `hud.css:194` 既有的"必须压过 aurora 44px"手法）：
   - `body.hud-theme .hud-subtabs > .hud-subtab { height:auto; min-height:48px; min-width:48px; }`（特异性 0,3,1 > aurora 的 0,2,1）
   - `body.hud-theme .btn.btn-sm { height:auto; min-height:48px; }`
   - `.token-banner-close` 提到 48×48
3. **`.btn-sm` 确认是需要改的**：`aurora-theme.css:342` 是 `height:40px;min-height:44px`，375px 实测 **44px < 48**。

### 一个必须记住的坑：**触摸目标收口不能只写 `body.hud-theme` 前缀版**

工程师第一版只加带前缀的规则，结果：
- 深色档 `.hud-subtab` 变成 **44×48（宽度不足）** —— 只提了 `min-height`，宽度还是 44；
- **回退档（移除 `body.hud-theme`）残留 8 条**：`span.hud-subtab ×5`（44 高）、`button.btn-sm 59×32`、`button.token-banner-close 28×28`。

原因：`body.hud-theme …` 前缀规则在回退档**整体失效**，落到 `index.html` 的 `.tab/.btn-sm{min-height:44}` 与 `hud.css` 自己的 `.token-banner-close{28px}`。
**修法**：同时写**无前缀版**，并给子导航补 `min-width:48px`。
> 这条与阶段三 §六 的发现同源：**项目没有浅色主题，但有"回退档"**。任何只挂 `body.hud-theme` 的移动端规则都会在回退档漏掉。

---

## 三、验证（真机矩阵，非目测）

重跑 `deliverables/gstack/_stage6_s3.py`（4 页 × 6 断点 × 2 主题 = 48 组，本机 Edge）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| `records` | 48 | 48 |
| `defect_cells` | 22（含 12 个 touch 单元 + 探针假阳性） | **4** |
| `touch<48` 合计 | 58（唯一目标）/ 每页 10~24 | **4** |
| `hscroll>0` / `overflow>0` / `jsErrors>0` / `contrast>0` | 0 / 0 / 0 / 0 | **0 / 0 / 0 / 0**（无回归） |

**剩余 4 条的构成（逐条列明，不打包成"基本达标"）**：
```
index 375x667 dark  touch=['span#connStatus 7x7']
index 375x667 light touch=['span#connStatus 8x8']
index 414x896 dark  touch=['span#connStatus 7x7']
index 414x896 light touch=['span#connStatus 8x8']
```

`#connStatus` **是探针的已知假阳性**，不是缺陷：元素 border-box 只有 7×7，但 `index.html:127-132` 的 `#connStatus::after` 是 `position:absolute; width:56px; height:56px; translate(-50%,-50%)` —— **真实命中区 56×56，达标**。按要求**没有改页面去迎合探针**。

**因此：44/48 组完全干净，另 4 组的问题已被证明是测量口径问题。**

---

## 四、回归

| 项 | 结果 |
|---|---|
| 全量回归（主理人独立复跑） | **3075 passed / 1 skipped / 0 failed / 90.50s**（批次 C 3071 → +4） |
| 新增测试 | `tests/test_frontend_workbench.py` +4 条结构回归（锁定 48px 规则存在、且**同时存在带前缀与无前缀两版**） |
| 既有断言改动 | 0 |
| 布局/滚动/对比度 | 无回归 |

## 五、线上指纹

**CSS 是静态资源，指纹用"文件内容变了没有"**：部署后 `GET /static/css/hud.css` 应包含 `D(2026-11-17):移动端触摸目标 48px 收口` 这段注释（修复前不存在）。比版本号可靠。

## 六、遗留

1. **探针不支持伪元素命中区**：`::after/::before` 扩大的点击区量不到（本轮人工排除 1 例）。要自动化需读伪元素计算样式。
2. **未覆盖横屏（667×375）与大平板（820×1180）**；Safari/Firefox 引擎差异维持「待基线」。
3. **`.hud-subtab.active` 在回退档是橙底 + 深色字**（`rgba(244,158,12,.965)` / `rgb(5,16,23)`），与深色页面并存视觉突兀 —— 非本轮引入，P3，见阶段三 §六。

---

> 本记录由 AI 协作生成，关键决策请由工程负责人复核。
