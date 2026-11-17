# 第六轮质量评估 · 阶段三：真机布局矩阵（48 组）

**执行日期**：2026-11-17｜**代码版本**：`77feafa`
**方法**：Playwright + 本机 Edge，4 页面 × 6 断点 × 2 主题 = **48 组**
**工具**：`deliverables/gstack/_stage6_s3.py`｜**产物**：`_r6_stage3_matrix.json` + 24 张截图

---

## 一、结论（TL;DR）

| 维度 | 结果 |
|---|---|
| **横向滚动** | **0 / 48 组**（`scrollingElement.scrollWidth - clientWidth` 全部为 0） |
| **父容器溢出** | **0 / 48 组**（口径见 §二，绝不用"超出视口"） |
| **JS 运行时错误** | **0 / 48 组**（`pageerror` 全部为空） |
| **WCAG AA 对比度** | **0 处不达标**；另有 120 条"无法用单色近似"如实标注为 unmeasured（§四） |
| **触摸目标 <48px** | 修复前 4 页共 **58** 个（44~46px）；修复后见 §五 |
| **三页功能渲染** | 全部真实渲染成功（§三） |

**布局层面本轮 0 缺陷。** 唯一实质问题是移动端触摸目标普遍停在 44px（上一轮门槛），未达本轮 48px 标准。

---

## 二、测量口径（决定结论真假的关键）

1. **溢出判据 = "超出父元素内容盒"（>1px 容差），不是"超出视口"**。
   原因：`.content` 有 `overflow-x:auto`，用视口判据会把所有元素豁免——r5 曾因此得出"全线 0 溢出"的假结论。本探针额外做两层豁免：元素在 `overflow:auto/scroll` 祖先内则不判。
2. **横向滚动单独量** `document.scrollingElement`，不与元素溢出混为一谈。
3. **触摸目标量 border-box 且要求宽高均 ≥48px**，只在 `<500px` 断点执行。
4. **主题维度**：见 §六——本轮实际测的是「HUD 深空主题 / 上一代深色主题」，**不是亮/暗**。

---

## 三、三页功能渲染（不是"页面能打开"，是"数据能出来"）

用 mock fetch 注入受控响应后驱动真实渲染：

| 页面 | 断言 | 结果 |
|---|---|---|
| `stock-compare` | `#rankTable` 行数 = 表头 1 + 3 只标的 = **4**；`#compareInfo` 含「3 只标的」 | ✅ 4 行，首行 `MA3/MA10 · 12.0% · 0.5000` |
| `ma-optimizer` | `#heatmap td.cell:not(.empty)` 计数 | ✅ **9** 格（3×3 网格，与 mock 一致） |
| `factor-research` | `#dailyRows tr` + `#notes li` | ✅ **6** 行 + **2** 条 note |

> ⚠️ **这里抓到过一个假通过**：第一版探针的 stock-compare mock 写成了 `data.entries[].code`，而 `stock-compare.js:52` 实际读 `data.results[].stock_code`。结果页面**只画了表头**（`rank_rows=1`）、`compareInfo` 显示「0 只标的」，但 **`jsErrors=0`、`hscroll=0`、`overflow=0`** —— 如果只看"无报错"就会得出"该页正常"的假结论。已修正 mock 并复验到 4 行。
> 教训：**功能验证必须断言"渲染出了预期条数的数据"，不能只断言"没有报错"。**

---

## 四、WCAG AA 对比度：0 缺陷，但 3 条假阳性值得记录

逐文本节点算对比度（正文 4.5:1 / 大字号 3:1；大字号 = ≥24px 或 ≥18.66px 且 700 字重）。

**最终 0 处不达标**。但第一版探针报了 2 条"缺陷"，全是**探针自身口径问题**，已修：

| 第一版报的 | 真相 | 探针缺陷 |
|---|---|---|
| `div.logo-icon "QF"` 对比度 **1.00**（前景=背景） | 该元素底色是 `linear-gradient(135deg, rgb(34,227,255…))`，深色字压在渐变上，视觉对比充足 | 背景采样遇到 gradient 就跳过、一路走到 `body` 的 `rgb(4,7,14)`，于是算出"文字与背景同色" |
| `span "工作台"` **1.87** | 是 `.hud-subtab.active` 主题切换**过渡中间态**的颜色 | 移除主题类后只等 120ms 就采样，CSS 过渡没结束 |

修正后：遇渐变/背景图**不再判失败**，而是记入 `contrast_unmeasured`（120 条）；主题切换等待提到 **800ms**。

> **不采信这 120 条为"通过"**。它们是无法用单色近似测量的元素（logo、渐变卡片等），口径上属"未测"，不是"合格"。

---

## 五、触摸目标：真实缺口与修复

### 修复前（实测）

| 页面 | 唯一 <48px 目标数 | 典型项 |
|---|---|---|
| `index` | 4 | `.hud-subtab`（role=button）44px × 3 |
| `stock-compare` | 10 | `#stockCodes`/`#adminToken` 44px、`#startDate`/`#endDate` 46px、`#runCompare` 44px、`nav a` 44px |
| `ma-optimizer` | 20 | `#stockCode`、`#shortMin/Max`、`#longMin/Max/Step`、`#startDate`/`#endDate`、`#runOptimizer`、`nav a` |
| `factor-research` | 24 | `#csvFile`、`#groups`、`#horizon`、`#minAmount`、`#minListing`、`#window`、`#direction`、`#trainRatio`、`#runResearch`、`#downloadTemplate`、`#downloadReport`、`nav a` |

### 根因（一处写死，四页受害）

三个辅助页**各自**内联 `<style>` 里都有同一段：

```css
@media (max-width: 640px) {
  input, button { min-height: 44px; }
  nav a { display: inline-flex; align-items: center; min-height: 44px; }
}
```

—— 是**上一轮 44px 标准**的遗留，且**没有覆盖 `select`**。

首页 `.hud-subtab` 的 44px 来自 `aurora-theme.css:352` 的移动端媒体查询（`height:44px` 固定值，会压住 `min-height`）。

### 假阳性（不计入缺陷）

- **`#connStatus` 7×7**：`index.html:127-132` 已有 `::after` 56×56 的透明命中区，**是达标的**。探针量的是 border-box，没计伪元素。
- 这条提示一个通用问题：**看 DOM 尺寸判触摸目标会漏掉"伪元素扩大命中区"的常见写法**，后续探针应支持 `::after/::before` 命中区检测。

### 修复后（批次 D，`5968d2b`）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| `touch<48` 合计（唯一目标） | **58** | **4** |
| `defect_cells`（48 组中含任一缺陷的组数） | 22 | **4** |
| `hscroll>0` / `overflow>0` / `jsErrors>0` / `contrast>0` | 0 / 0 / 0 / 0 | **0 / 0 / 0 / 0**（无回归） |

**剩余 4 条逐条列明**（不打包成"基本达标"）：

```
index 375x667 dark  touch=['span#connStatus 7x7']
index 375x667 light touch=['span#connStatus 8x8']
index 414x896 dark  touch=['span#connStatus 7x7']
index 414x896 light touch=['span#connStatus 8x8']
```

全部是同一个 `#connStatus`，**已证明是探针假阳性**（`::after` 真实命中区 56×56）。
**即：44/48 组完全干净。**

**一个必须记住的坑**：触摸目标收口**不能只写 `body.hud-theme …` 前缀版**。第一版只加前缀规则，结果深色档 `.hud-subtab` 变成 44×48（宽度没提），**回退档残留 8 条**（前缀规则整体失效，落到 `index.html` 的 `.tab/.btn-sm{44}` 与 `hud.css` 的 `.token-banner-close{28px}`）。必须**双档覆盖 + 补 `min-width`**。


---

## 六、【重要更正】项目没有"浅色主题"

`web/static/index.html:1004/1017` 的注释写「移除 `body` 的 `hud-theme` 类即**整体回退**」，我按这个做了"暗/亮双主题"矩阵，实测发现：

| 状态 | `body` 背景色 | 文字色（`wb-title`） |
|---|---|---|
| 默认（有 `hud-theme`） | `rgb(4, 7, 14)` 近黑 | `rgb(234,243,255)` 白 |
| 移除 `hud-theme` 后 | **`rgb(13, 17, 23)` 仍是深色** | `rgb(234,243,255)` 白 |

**结论：项目只有深色主题**，"移除即回退"是回退到**上一代深色主题**，不存在浅色态。
因此：
1. 本矩阵的第二个"主题"应更名为「旧深色主题」，**任何"暗色/亮色一致性"的结论都不成立**；
2. 但回退态本身有瑕疵：`.hud-subtab.active` 在回退态下变成**橙色底 + 深色字**（`rgba(244,158,12,.965)` / `rgb(5,16,23)`），与深色页面并存，视觉突兀。这条**不是本轮引入**（`aurora-theme.css` 原有），记为 P3 遗留。

这条更正同样适用于基线文档——r5/r6 基线里写过"暗色主题一致性"，措辞需要按此校正。

---

## 七、遗留

1. **触摸目标伪元素口径**：探针只量 border-box，会漏 `::after` 扩大的命中区（本轮已人工排除 1 例）。后续若要自动化，需读伪元素尺寸。
2. **120 条对比度未测**：渐变/背景图元素无法用单色近似，需人工目检或改用截图取色。
3. **回退态配色瑕疵**（`.hud-subtab.active` 橙底），P3，非本轮引入。
4. **未测断点之外的设备**：未覆盖横屏（667×375）、平板竖屏大尺寸（如 820×1180）、以及 Safari/Firefox 引擎差异——后者维持"待基线"。

---

> 本报告由 AI 协作生成，关键决策请由工程负责人复核。
